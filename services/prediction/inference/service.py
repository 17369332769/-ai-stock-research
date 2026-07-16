"""推理编排（spec §3.3 / §7.4 / §9.5）。

一次预测的完整闸门顺序（**任何一道不过就 fail closed，绝不返回默认值或假概率**）：

    1. active 模型存在？        否 → ModelUnavailable（candidate 永不服务 API）
    2. 漂移超阈值？             PSI > 0.30 → ModelUnavailable（spec §9.3.1）
    3. today_close 是否已到 09:45？ 否 → InsufficientData（spec §3.3）
    4. 是否 as_of 当日的沪深300成分股？ 否 → 不生成新预测（spec §9.3）
    5. 历史长度够不够？         一周模型 <3 年 / 今日模型 <120 个交易日 → InsufficientData
    6. 核心特征算得出来吗？     否 → InsufficientData
    7. 特征集哈希与产物一致？   否 → ModelUnavailable（改特征必须升版本）

写入 ``predictions`` 是**追加**：账本不可覆盖（DB 有触发器挡 UPDATE）。
同一 (symbol, model_version, horizon, as_of) 重复生成 → 幂等跳过，不报错、不覆盖。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.clock import to_shanghai, trading_day_of
from apps.api.app.core.enums import ConfidenceLabel, PredictionHorizon
from apps.api.app.core.errors import InsufficientData, ModelUnavailable
from apps.api.app.core.runtime import get_trading_calendar
from apps.api.app.core.trading_calendar import TradingCalendar, today_prediction_allowed
from apps.api.app.models.tables import Prediction
from services.prediction.evaluation.drift_store import get_drift_store
from services.prediction.features.builder import build_feature_snapshot, ensure_horizon_enabled
from services.prediction.features.config import load_feature_set
from services.prediction.features.repository import (
    is_universe_member_at,
    load_latest_quote,
    load_pit_panel,
)
from services.prediction.inference.confidence import ConfidenceInputs, decide_confidence
from services.prediction.inference.loader import LoadedModel, load_model_bundle
from services.prediction.inference.reference_price import resolve_reference_price
from services.prediction.training.labels import target_time_for
from services.prediction.training.registry import ActiveModel, active_model

__all__ = ["NotUniverseMember", "PredictionResult", "generate_prediction"]


class NotUniverseMember(InsufficientData):
    """调出沪深300 的股票停止生成新预测，但既有预测继续结算（spec §3.1 / §9.3）。"""


@dataclass(frozen=True, slots=True)
class PredictionResult:
    prediction_id: uuid.UUID
    symbol: str
    horizon: str
    as_of: datetime
    target_at: datetime
    data_cutoff: datetime
    reference_price: float
    probability_up: float
    expected_return: float
    lower_return: float
    upper_return: float
    confidence: ConfidenceLabel
    model_key: str
    model_version: str
    better_than_baseline: bool
    created: bool  # False = 幂等命中，账本里已有同一条

    def to_json(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "horizon": self.horizon,
            "as_of": self.as_of.isoformat(),
            "data_cutoff": self.data_cutoff.isoformat(),
            "reference_price": self.reference_price,
            "probability_up": self.probability_up,
            "expected_return": self.expected_return,
            "return_interval": {"p20": self.lower_return, "p80": self.upper_return},
            "confidence": self.confidence.value,
            "model": {
                "key": self.model_key,
                "version": self.model_version,
                "better_than_baseline": self.better_than_baseline,
            },
        }


def _check_drift(model: ActiveModel, loaded: LoadedModel) -> tuple[dict[str, float], list[str]]:
    """返回 (关键特征 PSI, 阻断原因)。PSI > 0.30 → 阻断（spec §9.3.1）。"""
    report = get_drift_store().latest(model.model_key)
    if report is None:
        # 还没有漂移报告：没有证据说明漂移了 → 不阻断；
        # 但也没有证据说明没漂移 → 置信度升不到 high（confidence.py 里靠空 PSI 实现）。
        return {}, []
    if report.blocked:
        return report.feature_psi, [
            f"关键特征 PSI 超过阻断阈值 {loaded.psi_reference.block_threshold}："
            f"{report.blocking_features()}（PSI={report.max_psi:.4f}）"
        ]
    return report.feature_psi, []


async def generate_prediction(
    session: AsyncSession,
    *,
    symbol: str,
    horizon: str,
    as_of: datetime,
    calendar: TradingCalendar | None = None,
    allow_out_of_universe: bool = False,
) -> PredictionResult:
    """为单只股票生成一条预测并写入账本。"""
    trading_calendar = calendar or get_trading_calendar()
    moment = to_shanghai(as_of)
    session_day = trading_day_of(moment)

    # ── 闸门 1：active 模型 ─────────────────────────────────────────────
    model = await active_model(session, horizon=horizon)
    loaded = load_model_bundle(
        model.artifact_uri, model.model_key, model.version, model.target_horizon
    )

    # ── 闸门 2：漂移 ────────────────────────────────────────────────────
    key_feature_psi, blocking = _check_drift(model, loaded)
    if blocking:
        raise ModelUnavailable(
            f"{model.model_key}/{model.version} 因特征漂移停止生成新预测：{blocking[0]}"
        )

    # ── 闸门 3：今日预测最早 09:45（spec §3.3）──────────────────────────
    if horizon == PredictionHorizon.TODAY_CLOSE and not today_prediction_allowed(
        moment, trading_calendar
    ):
        raise InsufficientData(
            f"今日预测最早在交易日 09:45 生成；当前 {moment.isoformat()} 不满足"
        )
    if not trading_calendar.is_trading_day(session_day):
        raise InsufficientData(f"{session_day} 不是交易日，不生成预测")

    # ── 闸门 4：as_of 当日的成分股（调出后停止生成新预测）───────────────
    if not allow_out_of_universe and not await is_universe_member_at(session, symbol, session_day):
        raise NotUniverseMember(
            f"{symbol} 在 {session_day} 不是沪深300成分股，停止生成新预测"
            f"（既有预测继续结算）"
        )

    # ── 特征（data_cutoff = as_of）──────────────────────────────────────
    config = load_feature_set(loaded.feature_set_version)
    include_minute = horizon == PredictionHorizon.TODAY_CLOSE
    panel = await load_pit_panel(
        session,
        symbol=symbol,
        data_cutoff=moment,
        config=config,
        include_minute=include_minute,
    )
    # ── 闸门 5：历史长度（3 年 / 120 个交易日）──────────────────────────
    ensure_horizon_enabled(panel, horizon=horizon, feature_set_version=config.version)
    # ── 闸门 6：核心特征（算不出来就 InsufficientData）──────────────────
    snapshot = build_feature_snapshot(
        panel, horizon=horizon, feature_set_version=config.version
    )
    if tuple(snapshot.names) != loaded.feature_names:
        raise ModelUnavailable(
            f"特征顺序与模型产物不一致：模型={loaded.feature_names[:3]}…，"
            f"当前={tuple(snapshot.names)[:3]}…"
        )

    quote = await load_latest_quote(session, symbol, moment)
    reference = resolve_reference_price(
        horizon=horizon, panel=panel, quote=quote, as_of=moment
    )

    probability, expected, lower, upper = loaded.predict_one(snapshot.to_model_row(config))

    # ── 置信度（spec §9.5）──────────────────────────────────────────────
    degradation_reasons = tuple(item.reason for item in snapshot.degradations)
    if allow_out_of_universe:
        degradation_reasons += ("非沪深300训练股票池，模型适用范围外推",)
    decision = decide_confidence(
        ConfidenceInputs(
            better_than_baseline=model.better_than_baseline,
            validation_predictions=model.validation_predictions,
            required_validation_predictions=model.required_validation_predictions,
            calibration_acceptable=model.calibration_acceptable,
            key_feature_psi=key_feature_psi,
            degraded=snapshot.forces_low_confidence or allow_out_of_universe,
            degradation_reasons=degradation_reasons,
        )
    )

    target_at = target_time_for(session_day, horizon, trading_calendar)

    features_payload = snapshot.to_json()
    features_payload["reference"] = reference.to_json()
    features_payload["confidence"] = decision.to_json()
    features_payload["model"] = {
        "key": model.model_key,
        "version": model.version,
        "better_than_baseline": model.better_than_baseline,
    }

    prediction_id = uuid.uuid4()
    statement = (
        pg_insert(Prediction)
        .values(
            id=prediction_id,
            symbol=symbol,
            model_version_id=model.id,
            horizon=horizon,
            as_of=moment,
            target_at=target_at,
            reference_price=Decimal(str(round(reference.price, 4))),
            probability_up=Decimal(str(round(probability, 4))),
            expected_return=Decimal(str(round(expected, 8))),
            lower_return=Decimal(str(round(lower, 8))),
            upper_return=Decimal(str(round(upper, 8))),
            confidence_label=decision.label.value,
            data_cutoff=moment,
            features_snapshot=features_payload,
        )
        # 账本不可覆盖：重复生成同一条 → 什么都不做（幂等），绝不 UPDATE
        .on_conflict_do_nothing(
            index_elements=["symbol", "model_version_id", "horizon", "as_of"]
        )
        .returning(Prediction.id)
    )
    inserted = (await session.execute(statement)).scalar_one_or_none()
    created = inserted is not None

    return PredictionResult(
        prediction_id=inserted or prediction_id,
        symbol=symbol,
        horizon=horizon,
        as_of=moment,
        target_at=target_at,
        data_cutoff=moment,
        reference_price=reference.price,
        probability_up=probability,
        expected_return=expected,
        lower_return=lower,
        upper_return=upper,
        confidence=decision.label,
        model_key=model.model_key,
        model_version=model.version,
        better_than_baseline=model.better_than_baseline,
        created=created,
    )
