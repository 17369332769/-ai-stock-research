"""预测读取编排（spec §7.4）。

状态机（验收标准直接考）：
- 预测存在 ⇒ 200；
- 预测不存在 **且回补进行中** ⇒ 202 + 作业状态；
- 预测不存在 **且没有可用（active）模型** ⇒ 503 MODEL_UNAVAILABLE；
- 预测不存在 **且确认样本不足** ⇒ 422 INSUFFICIENT_DATA；
- 今日预测在交易日 09:45 前不可用（``today_prediction_allowed``）⇒ 422。

API 只**读账本**，不生成预测：推理由 worker 按 §8 的调度产生并落库。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.clock import SHANGHAI, to_shanghai
from apps.api.app.core.enums import ConfidenceLabel, PredictionHorizon
from apps.api.app.core.errors import InstrumentNotFound, InsufficientData, ModelUnavailable
from apps.api.app.core.pagination import Cursor
from apps.api.app.core.runtime import get_trading_calendar
from apps.api.app.core.trading_calendar import TODAY_PREDICTION_EARLIEST, today_prediction_allowed
from apps.api.app.models.tables import ModelVersion, Prediction
from apps.api.app.repositories import instruments as instruments_repo
from apps.api.app.repositories import jobs as jobs_repo
from apps.api.app.repositories import model_versions as model_versions_repo
from apps.api.app.repositories import predictions as predictions_repo
from apps.api.app.schemas.jobs import JobDTO
from apps.api.app.schemas.predictions import (
    PredictionDTO,
    PredictionModelRefDTO,
    ReturnIntervalDTO,
)

# 训练侧必须写进 model_versions.validation_metrics 的键（spec §9.3.1 / §9.4）
BETTER_THAN_BASELINE_KEY = "better_than_baseline"


@dataclass(frozen=True, slots=True)
class LatestPrediction:
    """要么有预测（200），要么有正在跑的回补作业（202）。两者不会同时为空。"""

    prediction: PredictionDTO | None
    pending_backfill: JobDTO | None


async def get_latest(
    session: AsyncSession, symbol: str, horizon: PredictionHorizon, now: datetime
) -> LatestPrediction:
    if await instruments_repo.get(session, symbol) is None:
        raise InstrumentNotFound(symbol)

    as_of_floor: datetime | None = None
    if horizon is PredictionHorizon.TODAY_CLOSE:
        _assert_today_prediction_window(now)
        # 今日预测只认**当前交易日**产生的那几条，否则会把昨天已结算的预测当成"今天的"
        local = to_shanghai(now)
        as_of_floor = datetime.combine(local.date(), TODAY_PREDICTION_EARLIEST, tzinfo=SHANGHAI)

    row = await predictions_repo.latest(session, symbol, horizon, as_of_not_before=as_of_floor)
    if row is not None:
        model = await model_versions_repo.get(session, row.model_version_id)
        if model is None:  # 外键保证不会发生；真发生就是完整性事故
            raise ModelUnavailable(f"预测 {row.id} 引用的模型版本不存在")
        return LatestPrediction(prediction=to_prediction_dto(row, model), pending_backfill=None)

    # 没有预测 —— 按 spec §7 的优先级判定原因，绝不返回空壳预测
    active_job = await jobs_repo.active_backfill(session, symbol)
    if active_job is not None:
        return LatestPrediction(prediction=None, pending_backfill=JobDTO.from_row(active_job))

    active_model = await model_versions_repo.active_for_horizon(session, horizon.value)
    if active_model is None:
        raise ModelUnavailable(f"没有可用的 {horizon.value} 模型版本（candidate 不对 API 提供预测）")

    raise InsufficientData(
        f"{symbol} 的 {horizon.value} 预测尚不可用：历史样本不足，无法生成结果"
    )


def _assert_today_prediction_window(now: datetime) -> None:
    """今日预测最早在交易日 09:45 生成（spec §3.3 / 验收 §15.6）。"""
    calendar = get_trading_calendar()
    if today_prediction_allowed(now, calendar):
        return

    local = to_shanghai(now)
    if not calendar.is_trading_day(local.date()):
        raise InsufficientData(f"{local.date()} 休市，今日预测不可用")
    raise InsufficientData(
        f"今日预测最早在交易日 {TODAY_PREDICTION_EARLIEST.strftime('%H:%M')} 生成，"
        f"当前 {local.strftime('%H:%M')} 尚不可用"
    )


async def get_history(
    session: AsyncSession,
    symbol: str,
    horizon: PredictionHorizon,
    *,
    limit: int,
    cursor: Cursor | None,
) -> tuple[list[PredictionDTO], Cursor | None, bool]:
    if await instruments_repo.get(session, symbol) is None:
        raise InstrumentNotFound(symbol)

    rows, has_more = await predictions_repo.history(
        session, symbol, horizon, limit=limit, cursor=cursor
    )
    models = await model_versions_repo.get_many(session, [row.model_version_id for row in rows])

    dtos: list[PredictionDTO] = []
    for row in rows:
        model = models.get(row.model_version_id)
        if model is None:
            raise ModelUnavailable(f"预测 {row.id} 引用的模型版本不存在")
        dtos.append(to_prediction_dto(row, model))

    next_cursor = predictions_repo.build_cursor(rows[-1]) if rows and has_more else None
    return dtos, next_cursor, has_more


def read_better_than_baseline(model: ModelVersion) -> bool:
    """从 ``validation_metrics`` 读取优势判定（spec §9.3.1）。

    API **不重算** better_than_baseline；缺这个键说明模型没走过 §9.4 的发布门槛，
    fail closed（503），而不是默认 False 蒙混过关。
    """
    metrics: dict[str, Any] = model.validation_metrics or {}
    value = metrics.get(BETTER_THAN_BASELINE_KEY)
    if not isinstance(value, bool):
        raise ModelUnavailable(
            f"模型 {model.model_key}/{model.version} 的 validation_metrics 缺少布尔字段 "
            f"{BETTER_THAN_BASELINE_KEY}，未通过发布门槛（spec §9.4）"
        )
    return value


def to_prediction_dto(row: Prediction, model: ModelVersion) -> PredictionDTO:
    return PredictionDTO(
        id=row.id,
        symbol=row.symbol,
        horizon=PredictionHorizon(row.horizon),
        as_of=to_shanghai(row.as_of),
        target_at=to_shanghai(row.target_at),
        data_cutoff=to_shanghai(row.data_cutoff),
        reference_price=float(row.reference_price),
        probability_up=float(row.probability_up),
        expected_return=float(row.expected_return),
        return_interval=ReturnIntervalDTO(
            p20=float(row.lower_return), p80=float(row.upper_return)
        ),
        confidence=ConfidenceLabel(row.confidence_label),
        model=PredictionModelRefDTO(
            key=model.model_key,
            version=model.version,
            better_than_baseline=read_better_than_baseline(model),
        ),
    )
