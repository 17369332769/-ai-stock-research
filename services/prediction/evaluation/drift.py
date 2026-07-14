"""特征漂移 PSI（spec §9.3.1）。

    PSI = Σ_bin (actual% - expected%) × ln(actual% / expected%)

- **参考分布来自训练窗口**，在训练时算好写进产物（``psi_reference.json``），
  所以线上算 PSI 不需要回头读训练数据。
- 分箱用训练窗口的分位数；额外有一个 **missing 桶** ——
  某个特征突然全变 NaN 是最需要报警的一类漂移，如果只对非空值分箱就会完全看不见。
- 阈值（spec §9.3.1 / §9.5）：
    > 0.20 → 标记漂移，置信度只能 low
    > 0.30 → **停止生成新预测**，返回 MODEL_UNAVAILABLE
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from services.prediction.features.config import FeatureSetConfig

__all__ = [
    "MISSING_BUCKET",
    "DriftReport",
    "FeatureReference",
    "PsiReference",
    "build_psi_reference",
    "compute_drift",
    "compute_psi",
]

MISSING_BUCKET = "__missing__"
_EPSILON = 1e-6  # 防止 0 除与 log(0)；空桶按极小占比处理


@dataclass(frozen=True, slots=True)
class FeatureReference:
    """一个特征在训练窗口上的参考分布。"""

    name: str
    edges: tuple[float, ...]  # 长度 = bins + 1（分位点，首尾为 -inf / +inf）
    expected: tuple[float, ...]  # 长度 = bins，各桶占比（只统计非缺失值）
    missing_rate: float  # 训练窗口里该特征的缺失率
    samples: int

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "edges": list(self.edges),
            "expected": list(self.expected),
            "missing_rate": self.missing_rate,
            "samples": self.samples,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FeatureReference:
        return cls(
            name=data["name"],
            edges=tuple(float(x) for x in data["edges"]),
            expected=tuple(float(x) for x in data["expected"]),
            missing_rate=float(data["missing_rate"]),
            samples=int(data["samples"]),
        )


@dataclass(frozen=True, slots=True)
class PsiReference:
    feature_set_version: str
    bins: int
    drift_threshold: float
    block_threshold: float
    features: tuple[FeatureReference, ...]

    def feature(self, name: str) -> FeatureReference | None:
        for item in self.features:
            if item.name == name:
                return item
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "feature_set_version": self.feature_set_version,
            "bins": self.bins,
            "drift_threshold": self.drift_threshold,
            "block_threshold": self.block_threshold,
            "features": [item.to_json() for item in self.features],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PsiReference:
        return cls(
            feature_set_version=data["feature_set_version"],
            bins=int(data["bins"]),
            drift_threshold=float(data["drift_threshold"]),
            block_threshold=float(data["block_threshold"]),
            features=tuple(FeatureReference.from_json(item) for item in data["features"]),
        )


@dataclass(frozen=True, slots=True)
class DriftReport:
    model_key: str
    computed_at: str
    lookback_sessions: int
    samples: int
    feature_psi: dict[str, float]
    drift_threshold: float
    block_threshold: float

    @property
    def max_psi(self) -> float | None:
        return max(self.feature_psi.values()) if self.feature_psi else None

    @property
    def drifted(self) -> bool:
        value = self.max_psi
        return value is not None and value > self.drift_threshold

    @property
    def blocked(self) -> bool:
        """> 0.30：停止生成新预测（spec §9.3.1）。"""
        value = self.max_psi
        return value is not None and value > self.block_threshold

    def blocking_features(self) -> list[str]:
        return sorted(
            name for name, value in self.feature_psi.items() if value > self.block_threshold
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "model_key": self.model_key,
            "computed_at": self.computed_at,
            "lookback_sessions": self.lookback_sessions,
            "samples": self.samples,
            "feature_psi": dict(self.feature_psi),
            "max_psi": self.max_psi,
            "drifted": self.drifted,
            "blocked": self.blocked,
            "drift_threshold": self.drift_threshold,
            "block_threshold": self.block_threshold,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DriftReport:
        return cls(
            model_key=data["model_key"],
            computed_at=data["computed_at"],
            lookback_sessions=int(data["lookback_sessions"]),
            samples=int(data["samples"]),
            feature_psi={k: float(v) for k, v in data["feature_psi"].items()},
            drift_threshold=float(data["drift_threshold"]),
            block_threshold=float(data["block_threshold"]),
        )


# ── 参考分布（训练时算）────────────────────────────────────────────────────


def _quantile_edges(values: Sequence[float], bins: int) -> tuple[float, ...]:
    """按分位数切边界。重复值会让某些桶塌缩 —— 去重后桶数变少是正常且正确的。"""
    ordered = sorted(values)
    n = len(ordered)
    inner: list[float] = []
    for i in range(1, bins):
        position = i * n / bins
        index = min(int(position), n - 1)
        inner.append(ordered[index])
    unique: list[float] = []
    for edge in inner:
        if not unique or edge > unique[-1]:
            unique.append(edge)
    return (-math.inf, *unique, math.inf)


def _bucket_index(value: float, edges: tuple[float, ...]) -> int:
    for i in range(len(edges) - 1):
        if edges[i] <= value < edges[i + 1]:
            return i
    return len(edges) - 2


def _distribution(values: Sequence[float], edges: tuple[float, ...]) -> tuple[float, ...]:
    count = len(edges) - 1
    counts = [0] * count
    for value in values:
        counts[_bucket_index(value, edges)] += 1
    total = len(values)
    if total == 0:
        return tuple(0.0 for _ in range(count))
    return tuple(item / total for item in counts)


def build_psi_reference(
    *,
    snapshots: Sequence[Mapping[str, float | None]],
    config: FeatureSetConfig,
) -> PsiReference:
    """从训练窗口的特征值构建参考分布。``snapshots`` 是每个样本的 values 字典。"""
    features: list[FeatureReference] = []
    for name in config.psi.key_features:
        raw = [snapshot.get(name) for snapshot in snapshots]
        present = [float(value) for value in raw if value is not None]
        missing_rate = 1.0 - (len(present) / len(raw)) if raw else 1.0
        if len(present) < config.psi.bins:
            # 训练窗口里该特征几乎没有值：给一个退化参考（单桶），
            # 它会让线上任何非缺失值都落进同一个桶，PSI 主要由 missing 桶驱动 —— 这是对的。
            features.append(
                FeatureReference(
                    name=name,
                    edges=(-math.inf, math.inf),
                    expected=(1.0,) if present else (0.0,),
                    missing_rate=missing_rate,
                    samples=len(present),
                )
            )
            continue
        edges = _quantile_edges(present, config.psi.bins)
        features.append(
            FeatureReference(
                name=name,
                edges=edges,
                expected=_distribution(present, edges),
                missing_rate=missing_rate,
                samples=len(present),
            )
        )
    return PsiReference(
        feature_set_version=config.version,
        bins=config.psi.bins,
        drift_threshold=config.psi.drift_threshold,
        block_threshold=config.psi.block_threshold,
        features=tuple(features),
    )


# ── PSI（线上算）──────────────────────────────────────────────────────────


def compute_psi(reference: FeatureReference, values: Sequence[float | None]) -> float:
    """带 missing 桶的 PSI。样本为空时返回 ``inf`` 是错的 —— 调用方必须先保证有样本。"""
    if not values:
        raise ValueError(f"特征 {reference.name} 没有任何线上样本，无法计算 PSI")

    present = [float(value) for value in values if value is not None]
    actual_missing = 1.0 - (len(present) / len(values))

    actual = _distribution(present, reference.edges)
    expected = reference.expected

    # 非缺失部分的占比要乘以 (1 - missing_rate)，才能和 missing 桶凑成一个完整分布
    expected_full = [
        max(p * (1.0 - reference.missing_rate), 0.0) for p in expected
    ] + [reference.missing_rate]
    actual_full = [max(p * (1.0 - actual_missing), 0.0) for p in actual] + [actual_missing]

    if len(expected_full) != len(actual_full):
        raise ValueError(f"特征 {reference.name} 的分桶数不一致（参考分布与线上分布对不上）")

    total = 0.0
    for expected_pct, actual_pct in zip(expected_full, actual_full, strict=True):
        e = max(expected_pct, _EPSILON)
        a = max(actual_pct, _EPSILON)
        total += (a - e) * math.log(a / e)
    return total


def compute_drift(
    *,
    model_key: str,
    reference: PsiReference,
    snapshots: Sequence[Mapping[str, float | None]],
    computed_at: str,
    lookback_sessions: int,
) -> DriftReport:
    """对每个关键特征算 PSI。没有线上样本时**不产出报告**（调用方应跳过，而不是记 0）。"""
    if not snapshots:
        raise ValueError(f"{model_key} 最近 {lookback_sessions} 个交易日没有特征样本，无法计算 PSI")

    psi: dict[str, float] = {}
    for item in reference.features:
        values = [snapshot.get(item.name) for snapshot in snapshots]
        psi[item.name] = compute_psi(item, values)

    return DriftReport(
        model_key=model_key,
        computed_at=computed_at,
        lookback_sessions=lookback_sessions,
        samples=len(snapshots),
        feature_psi=psi,
        drift_threshold=reference.drift_threshold,
        block_threshold=reference.block_threshold,
    )
