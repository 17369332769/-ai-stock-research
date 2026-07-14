"""预测成绩单（spec §7.4 / 验收 §15.10）。

口径（一个字都不能松）：
- **eligible = 目标时间已到（target_at <= now）的预测**；尚未到目标时间的预测**不进入分母**；
- ``settled_count + pending_count == eligible_count``；
- 成绩单同时提供全部历史 / 最近 20 次 / 最近 100 次三个窗口。

分工：**每条预测的对错与误差由 services/prediction 在结算时算好并落 prediction_outcomes**
（direction_correct / absolute_error）。API 只对已结算行做聚合，不重算收益、不重算概率。
基准指标来自训练侧写入的 ``model_versions.validation_metrics``（spec §9.3.1），API 不重算基准。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.errors import InsufficientData, InvalidArgument, ModelUnavailable
from apps.api.app.repositories import model_versions as model_versions_repo
from apps.api.app.repositories import predictions as predictions_repo
from apps.api.app.schemas.predictions import ScorecardDTO
from apps.api.app.services.prediction_service import read_better_than_baseline

# 训练侧必须提供的基准键（spec §9.3.1：基准包括恒定上涨概率、历史均值收益和沪深300方向）
BASELINE_KEYS = (
    "baseline_direction_accuracy",
    "baseline_mae",
    "baseline_brier_score",
)

WINDOW_ALL: Literal["all"] = "all"
ALLOWED_WINDOWS = ("20", "100", WINDOW_ALL)


@dataclass(frozen=True, slots=True)
class _Window:
    label: int | Literal["all"]
    size: int | None  # None ⇒ all


def parse_window(raw: str) -> _Window:
    if raw not in ALLOWED_WINDOWS:
        # 由路由的 Literal 类型先兜一层；这里是二次防线
        raise InvalidArgument(f"window 只允许 {'|'.join(ALLOWED_WINDOWS)}")
    if raw == WINDOW_ALL:
        return _Window(label=WINDOW_ALL, size=None)
    return _Window(label=int(raw), size=int(raw))


async def get_scorecard(
    session: AsyncSession, model_key: str, window_raw: str, now: datetime
) -> ScorecardDTO:
    window = parse_window(window_raw)

    model = await model_versions_repo.latest_by_key(session, model_key)
    if model is None:
        raise ModelUnavailable(f"没有 model_key={model_key} 的模型版本")

    rows = await predictions_repo.eligible_for_scorecard(
        session, model_key, now=now, window=window.size
    )
    eligible_count = len(rows)
    settled = [(prediction, outcome) for prediction, outcome in rows if outcome is not None]
    settled_count = len(settled)
    pending_count = eligible_count - settled_count

    if settled_count == 0:
        # 没有任何已结算样本 ⇒ 指标无定义。绝不返回 0.0 冒充"命中率为零"
        raise InsufficientData(
            f"model_key={model_key} 在窗口 {window.label} 内没有已结算的预测，无法计算成绩单"
            f"（eligible={eligible_count}，pending={pending_count}）"
        )

    direction_hits = sum(1 for _, outcome in settled if outcome.direction_correct)
    direction_accuracy = direction_hits / settled_count
    mae = sum(float(outcome.absolute_error) for _, outcome in settled) / settled_count
    brier_score = (
        sum(
            (float(prediction.probability_up) - _actual_up_label(float(outcome.actual_return))) ** 2
            for prediction, outcome in settled
        )
        / settled_count
    )

    baselines = _read_baselines(model.validation_metrics or {}, model_key, model.version)

    return ScorecardDTO(
        model_key=model_key,
        window=window.label,
        eligible_count=eligible_count,
        settled_count=settled_count,
        pending_count=pending_count,
        direction_accuracy=direction_accuracy,
        mae=mae,
        brier_score=brier_score,
        baseline_direction_accuracy=baselines["baseline_direction_accuracy"],
        baseline_mae=baselines["baseline_mae"],
        baseline_brier_score=baselines["baseline_brier_score"],
        better_than_baseline=read_better_than_baseline(model),
        calculated_at=now,
    )


def _actual_up_label(actual_return: float) -> float:
    """spec §9.1：目标收益率大于 0 记为上涨，否则记为非上涨。"""
    return 1.0 if actual_return > 0 else 0.0


def _read_baselines(
    metrics: dict[str, Any], model_key: str, version: str
) -> dict[str, float]:
    """基准指标必须存在且为有限数值（spec §9.4 的发布门槛），缺失即 fail closed。"""
    values: dict[str, float] = {}
    for key in BASELINE_KEYS:
        raw = metrics.get(key)
        # bool 是 int 的子类：True 会被当成 1.0 蒙混过关，必须单独挡掉
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raise ModelUnavailable(
                f"模型 {model_key}/{version} 的 validation_metrics 缺少数值字段 {key}，"
                f"未通过发布门槛（spec §9.4）"
            )
        value = float(raw)
        if not math.isfinite(value):  # NaN / ±inf 都不是"已计算的有限数值"
            raise ModelUnavailable(f"模型 {model_key}/{version} 的 {key} 不是有限数值")
        values[key] = value
    return values
