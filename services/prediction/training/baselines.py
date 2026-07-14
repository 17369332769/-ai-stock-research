"""基准（spec §9.3 / §9.4）。

三个基准：
1. **恒定上涨概率** —— 用**训练窗口**的上涨频率作为常数概率。
   注意是训练窗口，不是测试窗口：拿测试窗口的频率当基准等于让基准偷看答案。
2. **历史均值收益** —— 用训练窗口的平均收益作为常数预测。
3. **沪深300 方向** —— 参照物，不进发布门槛（见下）。

发布门槛（spec §9.3.1）：
    better_than_baseline = (Brier < 恒定概率基准) AND (MAE < 历史均值基准)
两个都必须**严格**优于，且在**同一个测试窗口**上比。

沪深300 方向基准刻意不进门槛：它用的是同期**已实现**的大盘方向，
事前根本拿不到，把它当作可击败的对手会误导人。它只作为"跟着大盘猜能对多少"的参照量出现。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from services.prediction.training.metrics import (
    brier_score,
    direction_accuracy,
    is_finite,
    mean_absolute_error,
)

__all__ = ["BaselineParams", "BaselineSet", "evaluate_baselines", "fit_baseline_params"]


@dataclass(frozen=True, slots=True)
class BaselineParams:
    """基准的常数参数，**只在训练窗口拟合**，随模型版本一起冻结。"""

    constant_probability: float
    historical_mean_return: float
    fitted_on_samples: int

    def to_json(self) -> dict[str, Any]:
        return {
            "constant_probability": self.constant_probability,
            "historical_mean_return": self.historical_mean_return,
            "fitted_on_samples": self.fitted_on_samples,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> BaselineParams:
        return cls(
            constant_probability=float(data["constant_probability"]),
            historical_mean_return=float(data["historical_mean_return"]),
            fitted_on_samples=int(data["fitted_on_samples"]),
        )


def fit_baseline_params(train_returns: Sequence[float]) -> BaselineParams:
    if not train_returns:
        raise ValueError("训练窗口为空，无法拟合基准")
    up_rate = sum(1 for value in train_returns if value > 0) / len(train_returns)
    mean_return = sum(train_returns) / len(train_returns)
    return BaselineParams(
        constant_probability=up_rate,
        historical_mean_return=mean_return,
        fitted_on_samples=len(train_returns),
    )


@dataclass(frozen=True, slots=True)
class BaselineSet:
    """在某个评估窗口上，三个基准各自的表现。"""

    count: int
    # 恒定上涨概率基准
    constant_probability: float
    baseline_brier_score: float | None
    baseline_direction_accuracy: float | None
    # 历史均值收益基准
    historical_mean_return: float
    baseline_mae: float | None
    # 沪深300 方向参照（同期已实现方向；不进发布门槛）
    baseline_csi300_direction_accuracy: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "constant_probability": self.constant_probability,
            "baseline_brier_score": self.baseline_brier_score,
            "baseline_direction_accuracy": self.baseline_direction_accuracy,
            "historical_mean_return": self.historical_mean_return,
            "baseline_mae": self.baseline_mae,
            "baseline_csi300_direction_accuracy": self.baseline_csi300_direction_accuracy,
        }


def evaluate_baselines(
    params: BaselineParams,
    *,
    actual_returns: Sequence[float],
    benchmark_returns: Sequence[float] | None = None,
) -> BaselineSet:
    """在评估窗口上算出三个基准的指标。``params`` 必须来自训练窗口。"""
    outcomes = [value > 0 for value in actual_returns]
    n = len(actual_returns)
    constant_probs = [params.constant_probability] * n
    mean_preds = [params.historical_mean_return] * n

    csi300_accuracy: float | None = None
    if benchmark_returns is not None:
        if len(benchmark_returns) != n:
            raise ValueError("基准收益与实际收益长度不一致")
        if n:
            hits = sum(
                1
                for market, actual in zip(benchmark_returns, outcomes, strict=True)
                if (market > 0) == actual
            )
            csi300_accuracy = hits / n

    return BaselineSet(
        count=n,
        constant_probability=params.constant_probability,
        baseline_brier_score=brier_score(constant_probs, outcomes),
        baseline_direction_accuracy=direction_accuracy(constant_probs, outcomes),
        historical_mean_return=params.historical_mean_return,
        baseline_mae=mean_absolute_error(mean_preds, list(actual_returns)),
        baseline_csi300_direction_accuracy=csi300_accuracy,
    )


def better_than_baseline(
    *,
    model_brier: float | None,
    model_mae: float | None,
    baseline_brier: float | None,
    baseline_mae: float | None,
) -> bool:
    """spec §9.3.1：**当且仅当** Brier 和 MAE 在同一测试窗口上**均严格**优于两个基准。

    任何一个指标缺失或非有限 → False（fail closed，绝不给"疑似更好"放行）。
    """
    values = (model_brier, model_mae, baseline_brier, baseline_mae)
    if not all(is_finite(value) for value in values):
        return False
    assert model_brier is not None and model_mae is not None
    assert baseline_brier is not None and baseline_mae is not None
    return model_brier < baseline_brier and model_mae < baseline_mae
