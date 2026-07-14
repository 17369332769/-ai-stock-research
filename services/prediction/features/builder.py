"""从 PIT 面板构建特征快照。

``FeatureSnapshot`` 是训练、推理、相似行情三条路径**共用**的同一个对象 ——
这是"训练/线上同源"的实现方式：不存在第二套特征代码。

缺失的表示：
- 内部与 JSON 快照里用 ``None``（JSONB 不接受 NaN，写库会炸）。
- 喂给 LightGBM 时按 yaml 的 ``missing`` 策略转成 ``nan`` 或 ``0.0``。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any

from apps.api.app.core.errors import InsufficientData
from services.prediction.features.computers import FEATURE_COMPUTERS, implemented_feature_names
from services.prediction.features.config import FeatureSetConfig, load_feature_set
from services.prediction.features.panel import BENCH_CSI300, BENCH_SSE, PitPanel

__all__ = [
    "CORE_FEATURES",
    "Degradation",
    "FeatureSnapshot",
    "build_feature_snapshot",
    "ensure_horizon_enabled",
]

# 历史长度达标时这些特征不可能缺失。一旦缺失说明价格里有 0 或数据断裂 —— fail closed。
CORE_FEATURES: tuple[str, ...] = ("ret_1", "ret_5", "ret_20", "ma_dist_20", "vol_20")


@dataclass(frozen=True, slots=True)
class Degradation:
    """一次有记录的能力降级。``forces_low_confidence`` 的项会把置信度钉死在 low（spec §9.5）。"""

    reason: str
    detail: str
    forces_low_confidence: bool = True

    def to_json(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "detail": self.detail,
            "forces_low_confidence": self.forces_low_confidence,
        }


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    symbol: str
    horizon: str
    data_cutoff: datetime
    feature_set_version: str
    feature_set_sha256: str
    names: tuple[str, ...]
    values: dict[str, float | None]
    degradations: tuple[Degradation, ...]
    meta: dict[str, Any]

    @property
    def degraded(self) -> bool:
        return bool(self.degradations)

    @property
    def forces_low_confidence(self) -> bool:
        return any(item.forces_low_confidence for item in self.degradations)

    def to_model_row(self, config: FeatureSetConfig) -> list[float]:
        """按 names 顺序生成模型输入行；缺失按 yaml 的 missing 策略落位。"""
        row: list[float] = []
        for name in self.names:
            value = self.values.get(name)
            if value is None:
                policy = config.spec(name).missing
                row.append(0.0 if policy == "zero" else math.nan)
            else:
                row.append(float(value))
        return row

    def to_json(self) -> dict[str, Any]:
        """写入 predictions.features_snapshot（JSONB）。必须是 JSON-safe：没有 NaN。"""
        return {
            "feature_set_version": self.feature_set_version,
            "feature_set_sha256": self.feature_set_sha256,
            "horizon": self.horizon,
            "data_cutoff": self.data_cutoff.isoformat(),
            "values": {name: self.values.get(name) for name in self.names},
            "degradations": [item.to_json() for item in self.degradations],
            "meta": self.meta,
        }


@lru_cache(maxsize=8)
def _validated(version: str) -> FeatureSetConfig:
    """加载特征集并校验 yaml 与代码双向一致（漂移即报错）。"""
    config = load_feature_set(version)
    config.validate_against_registry(implemented_feature_names())
    return config


def build_feature_snapshot(
    panel: PitPanel,
    *,
    horizon: str,
    feature_set_version: str,
) -> FeatureSnapshot:
    """计算 ``horizon`` 所需的全部特征。

    ``panel`` 已经是 PIT 视图（构造时断言过），所以这里不再做时间判断 —— 也做不到。
    """
    config = _validated(feature_set_version)
    names = config.names_for_horizon(horizon)

    # 这里看的是**装进面板的**日线根数（特征算不算得出来），
    # 不是标的的历史总长度（那是 ensure_horizon_enabled 的事）。
    if panel.loaded_sessions < config.history.min_completed_sessions:
        raise InsufficientData(
            f"{panel.symbol} 在 {panel.data_cutoff.isoformat()} 只有 "
            f"{panel.loaded_sessions} 根可用日线，"
            f"少于计算特征所需的 {config.history.min_completed_sessions} 根"
        )

    degradations: list[Degradation] = []
    dropped: set[str] = set()

    # 早盘分钟特征不足 → 退化为开盘模型并标记原因（spec §9.3）
    if horizon == "today_close":
        rule = config.today_degradation
        minute_bars = len(panel.minute)
        if minute_bars < config.history.today_close_min_minute_bars:
            dropped = {
                spec.name for spec in config.features if spec.requires == rule.drop_requires
            }
            degradations.append(
                Degradation(
                    reason=rule.reason,
                    detail=(
                        f"当日只有 {minute_bars} 根分钟线（需要 "
                        f"{config.history.today_close_min_minute_bars} 根），"
                        f"退化为 {rule.mode}，已丢弃特征：{sorted(dropped)}"
                    ),
                    forces_low_confidence=rule.force_confidence == "low",
                )
            )

    # 基准缺失 → 市场类特征整体不可算
    for bench_name, bench_label in ((BENCH_CSI300, "沪深300"), (BENCH_SSE, "上证指数")):
        if not panel.benchmark_daily.get(bench_name):
            degradations.append(
                Degradation(
                    reason="benchmark_unavailable",
                    detail=f"{bench_label}（{bench_name}）在 cutoff 前没有可见日线，市场类特征缺失",
                )
            )

    values: dict[str, float | None] = {}
    for name in names:
        if name in dropped:
            values[name] = None
            continue
        spec = config.spec(name)
        values[name] = FEATURE_COMPUTERS[name](panel, spec, config)

    missing_core = [name for name in CORE_FEATURES if name in names and values.get(name) is None]
    if missing_core:
        raise InsufficientData(
            f"{panel.symbol} 在 {panel.data_cutoff.isoformat()} 的核心特征无法计算："
            f"{missing_core}（历史长度足够但价格数据异常，拒绝生成预测）"
        )

    # 必填但缺失的特征 → 记为降级（强制 low 置信度）。
    # required=false 的特征缺失是**正常状态**，不算降级：
    #   - turnover_rate：当前数据口径没有股本字段
    #   - hours_since_last_document：一只票 90 天没公告是正常的
    # 把这类"正常缺失"当降级，会让几乎每只票都掉到 low —— 那样置信度就失去了区分度。
    missing_required = sorted(
        name
        for name in names
        if values.get(name) is None and config.spec(name).required and name not in dropped
    )
    if missing_required:
        degradations.append(
            Degradation(
                reason="required_features_missing",
                detail=f"必填特征缺失：{missing_required}",
            )
        )

    meta: dict[str, Any] = {
        "completed_sessions": panel.completed_sessions,
        "loaded_sessions": panel.loaded_sessions,
        "last_session": panel.last_session.isoformat() if panel.last_session else None,
        "minute_bars": len(panel.minute),
        "visible_documents": len(panel.documents),
        "adjustment": panel.adjustment,
        "session_open_source": panel.session_open_source,
        # 可选特征的缺失（正常状态，不是降级）—— 如实记录，便于事后核对
        "optional_missing": sorted(
            name for name in names if values.get(name) is None and not config.spec(name).required
        ),
    }

    return FeatureSnapshot(
        symbol=panel.symbol,
        horizon=horizon,
        data_cutoff=panel.data_cutoff,
        feature_set_version=config.version,
        feature_set_sha256=config.sha256,
        names=names,
        values=values,
        degradations=tuple(degradations),
        meta=meta,
    )


def ensure_horizon_enabled(panel: PitPanel, *, horizon: str, feature_set_version: str) -> None:
    """模型启用门槛（spec §9.3）：一周模型 <3 年日线不启用；今日模型 <120 个交易日不启用。

    只在**推理**路径调用：训练早期的样本天然不满足 720 根，那是正常的。
    """
    config = _validated(feature_set_version)
    required = config.min_sessions_for_horizon(horizon)
    if panel.completed_sessions < required:
        raise InsufficientData(
            f"{panel.symbol} 只有 {panel.completed_sessions} 个已完成交易日，"
            f"少于 {horizon} 模型要求的 {required} 个，不启用该模型"
        )
