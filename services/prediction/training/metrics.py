"""指标（spec §7.4 / §9.4）。纯 Python，推理与结算路径都能用，不拖科学栈。

所有指标在样本为空时返回 ``None``，**绝不返回 0**：
"0 个样本的 Brier Score = 0" 会让一个什么都没做的模型看起来完美。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "MetricSet",
    "brier_score",
    "direction_accuracy",
    "expected_calibration_error",
    "interval_coverage",
    "is_finite",
    "mean_absolute_error",
]


def is_finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def brier_score(probabilities: Sequence[float], outcomes: Sequence[bool]) -> float | None:
    """Brier = mean((p - y)^2)，越小越好。"""
    if len(probabilities) != len(outcomes):
        raise ValueError("概率与结果长度不一致")
    if not probabilities:
        return None
    total = sum((p - (1.0 if y else 0.0)) ** 2 for p, y in zip(probabilities, outcomes, strict=True))
    return total / len(probabilities)


def direction_accuracy(probabilities: Sequence[float], outcomes: Sequence[bool]) -> float | None:
    """预测方向 = probability_up >= 0.5；标签 = 实际收益 > 0（spec §9.1）。"""
    if len(probabilities) != len(outcomes):
        raise ValueError("概率与结果长度不一致")
    if not probabilities:
        return None
    hits = sum(1 for p, y in zip(probabilities, outcomes, strict=True) if (p >= 0.5) == y)
    return hits / len(probabilities)


def mean_absolute_error(predicted: Sequence[float], actual: Sequence[float]) -> float | None:
    if len(predicted) != len(actual):
        raise ValueError("预测与实际长度不一致")
    if not predicted:
        return None
    return sum(abs(p - a) for p, a in zip(predicted, actual, strict=True)) / len(predicted)


def interval_coverage(
    lower: Sequence[float], upper: Sequence[float], actual: Sequence[float]
) -> float | None:
    """区间覆盖率（spec §9.4 要求可见）。p20/p80 的名义覆盖率是 60%。"""
    if not (len(lower) == len(upper) == len(actual)):
        raise ValueError("区间与实际长度不一致")
    if not actual:
        return None
    inside = sum(
        1 for lo, hi, a in zip(lower, upper, actual, strict=True) if lo <= a <= hi
    )
    return inside / len(actual)


def expected_calibration_error(
    probabilities: Sequence[float], outcomes: Sequence[bool], bins: int = 10
) -> float | None:
    """ECE：把概率分桶，比较每桶的平均预测概率与实际频率，按样本数加权。

    spec §9.5 说置信度要求"校准合格"但没给定义 —— 本项目把它定义为
    ``ECE <= 0.10 且校准后 Brier 不劣于校准前``（见 training/calibration.py）。
    """
    if len(probabilities) != len(outcomes):
        raise ValueError("概率与结果长度不一致")
    if not probabilities or bins < 1:
        return None
    total = len(probabilities)
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for p, y in zip(probabilities, outcomes, strict=True):
        clamped = min(max(p, 0.0), 1.0)
        index = min(int(clamped * bins), bins - 1)
        buckets[index].append((clamped, y))
    error = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        avg_p = sum(p for p, _ in bucket) / len(bucket)
        freq = sum(1 for _, y in bucket if y) / len(bucket)
        error += (len(bucket) / total) * abs(avg_p - freq)
    return error


@dataclass(frozen=True, slots=True)
class MetricSet:
    """一个窗口上的完整指标。缺样本时全为 None —— 上层必须显式处理，不能当 0 用。"""

    count: int
    direction_accuracy: float | None
    mae: float | None
    brier_score: float | None
    interval_coverage: float | None = None
    expected_calibration_error: float | None = None

    @classmethod
    def compute(
        cls,
        *,
        probabilities: Sequence[float],
        expected_returns: Sequence[float],
        actual_returns: Sequence[float],
        lower: Sequence[float] | None = None,
        upper: Sequence[float] | None = None,
    ) -> MetricSet:
        outcomes = [value > 0 for value in actual_returns]
        coverage = (
            interval_coverage(lower, upper, actual_returns)
            if lower is not None and upper is not None
            else None
        )
        return cls(
            count=len(actual_returns),
            direction_accuracy=direction_accuracy(probabilities, outcomes),
            mae=mean_absolute_error(expected_returns, actual_returns),
            brier_score=brier_score(probabilities, outcomes),
            interval_coverage=coverage,
            expected_calibration_error=expected_calibration_error(probabilities, outcomes),
        )

    @property
    def all_finite(self) -> bool:
        """spec §9.4 发布门槛：Brier / MAE / 方向准确率必须都是有限数值。"""
        return all(
            is_finite(value)
            for value in (self.direction_accuracy, self.mae, self.brier_score)
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "direction_accuracy": self.direction_accuracy,
            "mae": self.mae,
            "brier_score": self.brier_score,
            "interval_coverage": self.interval_coverage,
            "expected_calibration_error": self.expected_calibration_error,
        }
