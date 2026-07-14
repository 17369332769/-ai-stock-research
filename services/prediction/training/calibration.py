"""概率校准（spec §9.3.1）。

- 时间验证集上做 **isotonic regression**。
- 验证样本 **< 200** 时降级为 **Platt scaling**，并在模型卡里记录降级（spec 明文要求）。
- 校准器序列化成 **JSON**（isotonic 存断点，Platt 存系数），不是 pickle：
  推理侧因此只需要 math，不需要 sklearn，也不怕 sklearn 版本变了产物读不出来。

"校准合格"（spec §9.5 用到但没定义）在本项目的定义：
    ECE <= 0.10 且 校准后 Brier <= 校准前 Brier
定义写在 ``CalibrationReport.is_acceptable``，并原样写进模型卡。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from services.prediction.training.metrics import brier_score, expected_calibration_error

__all__ = [
    "ECE_ACCEPTABLE",
    "MIN_ISOTONIC_SAMPLES",
    "CalibrationReport",
    "Calibrator",
    "fit_calibrator",
]

# spec §9.3.1：验证样本少于 200 时用 Platt scaling
MIN_ISOTONIC_SAMPLES = 200
# 本项目对"校准合格"的定义
ECE_ACCEPTABLE = 0.10

Method = Literal["isotonic", "platt", "identity"]


@dataclass(frozen=True, slots=True)
class Calibrator:
    """把模型原始概率映射到校准后概率。纯数值，无第三方依赖。"""

    method: Method
    # isotonic：分段常数/线性插值的断点
    thresholds_x: tuple[float, ...] = ()
    thresholds_y: tuple[float, ...] = ()
    # platt：sigmoid(a * p + b)
    coef: float = 1.0
    intercept: float = 0.0

    def apply(self, probability: float) -> float:
        value = min(max(float(probability), 0.0), 1.0)
        if self.method == "identity":
            return value
        if self.method == "platt":
            return _sigmoid(self.coef * value + self.intercept)
        if not self.thresholds_x:
            return value
        return _clamp01(_interpolate(value, self.thresholds_x, self.thresholds_y))

    def apply_many(self, probabilities: Sequence[float]) -> list[float]:
        return [self.apply(p) for p in probabilities]

    def to_json(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "thresholds_x": list(self.thresholds_x),
            "thresholds_y": list(self.thresholds_y),
            "coef": self.coef,
            "intercept": self.intercept,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Calibrator:
        method = data["method"]
        if method not in ("isotonic", "platt", "identity"):
            raise ValueError(f"未知校准方法：{method!r}")
        return cls(
            method=method,
            thresholds_x=tuple(float(x) for x in data.get("thresholds_x", ())),
            thresholds_y=tuple(float(y) for y in data.get("thresholds_y", ())),
            coef=float(data.get("coef", 1.0)),
            intercept=float(data.get("intercept", 0.0)),
        )


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    method: Method
    degraded: bool
    degraded_reason: str | None
    validation_samples: int
    brier_before: float | None
    brier_after: float | None
    ece_before: float | None
    ece_after: float | None

    @property
    def is_acceptable(self) -> bool:
        """校准合格 = ECE <= 0.10 且校准没把 Brier 弄得更差。"""
        if self.ece_after is None or self.brier_after is None or self.brier_before is None:
            return False
        if not math.isfinite(self.ece_after) or not math.isfinite(self.brier_after):
            return False
        return self.ece_after <= ECE_ACCEPTABLE and self.brier_after <= self.brier_before

    def to_json(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
            "validation_samples": self.validation_samples,
            "brier_before": self.brier_before,
            "brier_after": self.brier_after,
            "ece_before": self.ece_before,
            "ece_after": self.ece_after,
            "acceptable": self.is_acceptable,
            "criterion": f"ECE <= {ECE_ACCEPTABLE} 且校准后 Brier <= 校准前 Brier",
        }


def fit_calibrator(
    probabilities: Sequence[float], outcomes: Sequence[bool]
) -> tuple[Calibrator, CalibrationReport]:
    """在**时间验证集**上拟合校准器。

    调用方必须保证传进来的是验证段的样本 —— 用训练段拟合校准器等于自欺欺人。
    """
    if len(probabilities) != len(outcomes):
        raise ValueError("概率与结果长度不一致")
    n = len(probabilities)
    if n == 0:
        raise ValueError("验证集为空，无法校准（数据不足必须 fail closed，不得返回未校准概率）")

    brier_before = brier_score(probabilities, outcomes)
    ece_before = expected_calibration_error(probabilities, outcomes)

    unique_labels = {bool(y) for y in outcomes}
    if len(unique_labels) < 2:
        # 验证段只有单一类别：任何校准都是拟合噪声，保持恒等并如实标注降级。
        calibrator = Calibrator(method="identity")
        report = CalibrationReport(
            method="identity",
            degraded=True,
            degraded_reason="验证集只有单一方向标签，无法拟合校准器，保持未校准概率",
            validation_samples=n,
            brier_before=brier_before,
            brier_after=brier_before,
            ece_before=ece_before,
            ece_after=ece_before,
        )
        return calibrator, report

    if n < MIN_ISOTONIC_SAMPLES:
        calibrator = _fit_platt(probabilities, outcomes)
        method: Method = "platt"
        degraded = True
        reason = (
            f"验证样本 {n} < {MIN_ISOTONIC_SAMPLES}，isotonic 会过拟合，"
            f"按 spec §9.3.1 降级为 Platt scaling"
        )
    else:
        calibrator = _fit_isotonic(probabilities, outcomes)
        method = "isotonic"
        degraded = False
        reason = None

    calibrated = calibrator.apply_many(probabilities)
    report = CalibrationReport(
        method=method,
        degraded=degraded,
        degraded_reason=reason,
        validation_samples=n,
        brier_before=brier_before,
        brier_after=brier_score(calibrated, outcomes),
        ece_before=ece_before,
        ece_after=expected_calibration_error(calibrated, outcomes),
    )
    return calibrator, report


# ── 实现 ────────────────────────────────────────────────────────────────────


def _fit_isotonic(probabilities: Sequence[float], outcomes: Sequence[bool]) -> Calibrator:
    from sklearn.isotonic import IsotonicRegression

    model = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip", increasing=True)
    x = [float(p) for p in probabilities]
    y = [1.0 if value else 0.0 for value in outcomes]
    model.fit(x, y)
    xs = [float(v) for v in model.X_thresholds_]
    ys = [float(v) for v in model.y_thresholds_]
    if not xs:
        return Calibrator(method="identity")
    return Calibrator(method="isotonic", thresholds_x=tuple(xs), thresholds_y=tuple(ys))


def _fit_platt(probabilities: Sequence[float], outcomes: Sequence[bool]) -> Calibrator:
    from sklearn.linear_model import LogisticRegression

    model = LogisticRegression(solver="lbfgs", max_iter=1000)
    x = [[float(p)] for p in probabilities]
    y = [1 if value else 0 for value in outcomes]
    model.fit(x, y)
    return Calibrator(
        method="platt",
        coef=float(model.coef_[0][0]),
        intercept=float(model.intercept_[0]),
    )


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def _clamp01(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def _interpolate(value: float, xs: tuple[float, ...], ys: tuple[float, ...]) -> float:
    """线性插值（等价 numpy.interp，但不引依赖）。xs 已升序。"""
    if value <= xs[0]:
        return ys[0]
    if value >= xs[-1]:
        return ys[-1]
    lo, hi = 0, len(xs) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if xs[mid] <= value:
            lo = mid
        else:
            hi = mid
    span = xs[hi] - xs[lo]
    if span == 0:
        return ys[hi]
    weight = (value - xs[lo]) / span
    return ys[lo] + weight * (ys[hi] - ys[lo])
