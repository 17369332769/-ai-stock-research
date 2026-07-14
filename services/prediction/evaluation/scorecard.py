"""成绩单（spec §3.4 / §7.4）。

计数口径是这里最容易做错、也最容易骗人的地方，spec 写得很死：

    eligible_count = **目标时间已到**的预测数
    settled_count + pending_count = eligible_count
    尚未到目标时间的预测**不进入分母**

也就是说：一条今天 09:45 生成、目标是今天收盘的预测，在 14:00 时既不是 settled 也不是
pending —— 它**根本不该出现在成绩单里**。把它算进分母会让准确率被稀释，
算进 pending 又会让"待结算"永远清不掉。

窗口 20 / 100 / all 作用在**按 as_of 倒序的 eligible 预测**上。

指标只在 settled 上计算；settled 为 0 时全部返回 None（**不是 0**）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.clock import to_shanghai
from apps.api.app.models.tables import ModelVersion, Prediction, PredictionOutcome
from services.prediction.training.baselines import (
    BaselineParams,
    BaselineSet,
    better_than_baseline,
)
from services.prediction.training.metrics import (
    MetricSet,
    brier_score,
    direction_accuracy,
    mean_absolute_error,
)

__all__ = ["Scorecard", "ScorecardWindow", "compute_scorecard"]

ScorecardWindow = Literal[20, 100, "all"]


@dataclass(frozen=True, slots=True)
class Scorecard:
    model_key: str
    window: ScorecardWindow
    eligible_count: int
    settled_count: int
    pending_count: int
    metrics: MetricSet
    baselines: BaselineSet | None
    better_than_baseline: bool
    calculated_at: datetime

    def to_json(self) -> dict[str, Any]:
        baselines = self.baselines
        return {
            "model_key": self.model_key,
            "window": self.window,
            "eligible_count": self.eligible_count,
            "settled_count": self.settled_count,
            "pending_count": self.pending_count,
            "direction_accuracy": self.metrics.direction_accuracy,
            "mae": self.metrics.mae,
            "brier_score": self.metrics.brier_score,
            "baseline_direction_accuracy": (
                baselines.baseline_direction_accuracy if baselines else None
            ),
            "baseline_mae": baselines.baseline_mae if baselines else None,
            "baseline_brier_score": baselines.baseline_brier_score if baselines else None,
            "baseline_csi300_direction_accuracy": (
                baselines.baseline_csi300_direction_accuracy if baselines else None
            ),
            "interval_coverage": self.metrics.interval_coverage,
            "better_than_baseline": self.better_than_baseline,
            "calculated_at": self.calculated_at.isoformat(),
        }


async def compute_scorecard(
    session: AsyncSession,
    *,
    model_key: str,
    window: ScorecardWindow,
    now: datetime,
) -> Scorecard:
    moment = to_shanghai(now)

    # eligible = 目标时间已到。**没到目标时间的一律不取** —— 这是分母的定义。
    stmt = (
        select(Prediction, PredictionOutcome, ModelVersion)
        .join(ModelVersion, ModelVersion.id == Prediction.model_version_id)
        .outerjoin(PredictionOutcome, PredictionOutcome.prediction_id == Prediction.id)
        .where(ModelVersion.model_key == model_key, Prediction.target_at <= moment)
        .order_by(Prediction.as_of.desc(), Prediction.id.desc())
    )
    if window != "all":
        stmt = stmt.limit(int(window))

    rows = (await session.execute(stmt)).all()

    eligible_count = len(rows)
    settled_rows = [(p, o, m) for p, o, m in rows if o is not None]
    settled_count = len(settled_rows)
    pending_count = eligible_count - settled_count

    probabilities: list[float] = []
    expected_returns: list[float] = []
    actual_returns: list[float] = []
    lower: list[float] = []
    upper: list[float] = []
    constant_probabilities: list[float] = []
    mean_returns: list[float] = []
    benchmark_returns: list[float | None] = []

    for prediction, outcome, model_version in settled_rows:
        assert outcome is not None
        probabilities.append(float(prediction.probability_up))
        expected_returns.append(float(prediction.expected_return))
        actual_returns.append(float(outcome.actual_return))
        lower.append(float(prediction.lower_return))
        upper.append(float(prediction.upper_return))

        # 基准参数随**每条预测所用的模型版本**走：窗口里混着多个版本时，
        # 每条都跟自己那一版的训练窗口基准比，才是公平的。
        params = _baseline_params(model_version)
        constant_probabilities.append(params.constant_probability)
        mean_returns.append(params.historical_mean_return)
        benchmark_returns.append(_benchmark_return(prediction))

    metrics = MetricSet.compute(
        probabilities=probabilities,
        expected_returns=expected_returns,
        actual_returns=actual_returns,
        lower=lower,
        upper=upper,
    )

    baselines: BaselineSet | None = None
    beats = False
    if settled_count > 0:
        baselines = _evaluate_mixed_baselines(
            constant_probabilities=constant_probabilities,
            mean_returns=mean_returns,
            actual_returns=actual_returns,
            benchmark_returns=benchmark_returns,
        )
        beats = better_than_baseline(
            model_brier=metrics.brier_score,
            model_mae=metrics.mae,
            baseline_brier=baselines.baseline_brier_score,
            baseline_mae=baselines.baseline_mae,
        )

    return Scorecard(
        model_key=model_key,
        window=window,
        eligible_count=eligible_count,
        settled_count=settled_count,
        pending_count=pending_count,
        metrics=metrics,
        baselines=baselines,
        better_than_baseline=beats,
        calculated_at=moment,
    )


def _baseline_params(model_version: ModelVersion) -> BaselineParams:
    metrics: dict[str, Any] = dict(model_version.validation_metrics or {})
    raw = metrics.get("baseline_params")
    if isinstance(raw, dict):
        try:
            return BaselineParams.from_json(raw)
        except (KeyError, ValueError, TypeError):
            pass
    # 模型版本没记基准参数：用中性值（恒定概率 0.5、均值收益 0）。
    # 这不是"假装有基准"，而是最保守的参照 —— 模型必须打赢抛硬币才能算更好。
    return BaselineParams(constant_probability=0.5, historical_mean_return=0.0, fitted_on_samples=0)


def _benchmark_return(prediction: Prediction) -> float | None:
    snapshot: dict[str, Any] = dict(prediction.features_snapshot or {})
    values = snapshot.get("values")
    if not isinstance(values, dict):
        return None
    # 用生成预测时可见的沪深300近期收益作为"跟着大盘猜"的方向参照。
    # 注意：这**不是**同期已实现的大盘收益（那要事后才知道），
    # 所以它是一个 ex-ante 参照，不进 better_than_baseline 判定。
    raw = values.get("bench_csi300_ret_5")
    return float(raw) if isinstance(raw, int | float) else None


def _evaluate_mixed_baselines(
    *,
    constant_probabilities: list[float],
    mean_returns: list[float],
    actual_returns: list[float],
    benchmark_returns: list[float | None],
) -> BaselineSet:
    """窗口里可能混着多个模型版本，每条预测有各自的基准参数，所以逐条比。"""
    outcomes = [value > 0 for value in actual_returns]
    n = len(actual_returns)

    usable_benchmarks = [value for value in benchmark_returns if value is not None]
    csi300_accuracy: float | None = None
    if len(usable_benchmarks) == n and n > 0:
        hits = sum(
            1
            for market, actual in zip(usable_benchmarks, outcomes, strict=True)
            if (market > 0) == actual
        )
        csi300_accuracy = hits / n

    average_probability = sum(constant_probabilities) / n if n else 0.5
    average_mean_return = sum(mean_returns) / n if n else 0.0

    if n == 0:
        return BaselineSet(
            count=0,
            constant_probability=average_probability,
            baseline_brier_score=None,
            baseline_direction_accuracy=None,
            historical_mean_return=average_mean_return,
            baseline_mae=None,
            baseline_csi300_direction_accuracy=None,
        )

    return BaselineSet(
        count=n,
        constant_probability=average_probability,
        baseline_brier_score=brier_score(constant_probabilities, outcomes),
        baseline_direction_accuracy=direction_accuracy(constant_probabilities, outcomes),
        historical_mean_return=average_mean_return,
        baseline_mae=mean_absolute_error(mean_returns, actual_returns),
        baseline_csi300_direction_accuracy=csi300_accuracy,
    )
