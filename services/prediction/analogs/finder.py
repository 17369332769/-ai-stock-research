"""历史相似行情（spec §10）。

规矩逐条对应：
- 使用与当前模型**一致**的 point-in-time 特征 → 复用同一个 ``build_feature_snapshot``。
- 用**训练集**的均值/标准差标准化 → 复用产物里的 ``normalizer.json``（训练窗口拟合，spec §9.3）。
- 默认返回距离最近的 **10** 个历史状态。
- 展示后续 **1 日与 5 日**真实收益分布。
- 有效候选 **< 30** 时**关闭功能**并说明样本不足 → ``InsufficientData``（不是返回一个空列表假装正常）。
- **不得描述为因果**：返回结构里只有距离与收益分布，没有任何"因为…所以…"的字段；
  文案由前端渲染，本模块提供 ``DISCLAIMER`` 供其原样引用。

候选的 PIT 约束（很容易被忽略）：
    一个候选日 t 只有在它的**后续 5 日收益已经实现**时才可用，
    也就是 t + 5 个交易日 <= 最后一个已收盘交易日。否则它的"后续收益"是未来数据。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.clock import to_shanghai
from apps.api.app.core.errors import InsufficientData
from apps.api.app.core.runtime import get_trading_calendar
from apps.api.app.core.trading_calendar import TradingCalendar, session_close_at
from services.prediction.features.builder import build_feature_snapshot
from services.prediction.features.config import FeatureSetConfig, load_feature_set
from services.prediction.features.panel import BENCH_CSI300, BENCH_SSE, DailyBar, PitPanel
from services.prediction.features.repository import load_daily_bars
from services.prediction.inference.loader import load_model_bundle
from services.prediction.training.registry import active_model
from services.prediction.training.samples import feature_window_sessions
from services.prediction.training.trainer import Normalizer

__all__ = [
    "DEFAULT_LIMIT",
    "DISCLAIMER",
    "MIN_VALID_CANDIDATES",
    "Analog",
    "AnalogResult",
    "find_analogs",
]

# spec §10：有效候选少于 30 个时关闭该功能
MIN_VALID_CANDIDATES = 30
DEFAULT_LIMIT = 10
# 后续收益的观察期
FORWARD_1D = 1
FORWARD_5D = 5
# 一个候选至少要有这么多比例的特征可比，否则距离没有意义
MIN_FEATURE_OVERLAP = 0.8
# 排除紧邻当前时点的若干交易日：它们与当前状态高度重叠，算不上"历史相似"
RECENT_EXCLUSION_SESSIONS = 5

DISCLAIMER = "历史相似状态仅供参考，不代表因果关系，也不预示未来表现。"


@dataclass(frozen=True, slots=True)
class Analog:
    session: date
    distance: float
    features: dict[str, float | None]  # 当时可见的特征（PIT）
    forward_return_1d: float | None
    forward_return_5d: float | None

    def to_json(self) -> dict[str, Any]:
        return {
            "date": self.session.isoformat(),
            "distance": self.distance,
            "features": self.features,
            "forward_return_1d": self.forward_return_1d,
            "forward_return_5d": self.forward_return_5d,
        }


@dataclass(frozen=True, slots=True)
class AnalogResult:
    symbol: str
    horizon: str
    as_of: datetime
    feature_set_version: str
    model_key: str
    model_version: str
    candidates_considered: int
    candidates_valid: int
    analogs: tuple[Analog, ...]

    def forward_distribution(self, days: int) -> dict[str, float | None]:
        values = [
            item.forward_return_1d if days == FORWARD_1D else item.forward_return_5d
            for item in self.analogs
        ]
        clean = sorted(value for value in values if value is not None)
        if not clean:
            return {"count": 0, "min": None, "p25": None, "median": None, "p75": None, "max": None}
        return {
            "count": len(clean),
            "min": clean[0],
            "p25": _percentile(clean, 0.25),
            "median": _percentile(clean, 0.5),
            "p75": _percentile(clean, 0.75),
            "max": clean[-1],
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "horizon": self.horizon,
            "as_of": self.as_of.isoformat(),
            "feature_set_version": self.feature_set_version,
            "model": {"key": self.model_key, "version": self.model_version},
            "candidates_considered": self.candidates_considered,
            "candidates_valid": self.candidates_valid,
            "analogs": [item.to_json() for item in self.analogs],
            "forward_return_distribution": {
                "1d": self.forward_distribution(FORWARD_1D),
                "5d": self.forward_distribution(FORWARD_5D),
            },
            "disclaimer": DISCLAIMER,
        }


def _percentile(ordered: Sequence[float], fraction: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = math.floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _distance(
    current: Sequence[float | None], candidate: Sequence[float | None]
) -> tuple[float, int]:
    """在两边**都有值**的特征上算欧氏距离，并按可比特征数归一。

    缺失的维度直接跳过（而不是当 0）：把缺失当 0 会让"这个特征恰好等于训练均值"，
    从而人为拉近距离 —— 这正是相似度里最常见的一种自欺。
    """
    total = 0.0
    used = 0
    for a, b in zip(current, candidate, strict=True):
        if a is None or b is None:
            continue
        total += (a - b) ** 2
        used += 1
    if used == 0:
        return math.inf, 0
    return math.sqrt(total / used), used


async def find_analogs(
    db: AsyncSession,
    *,
    symbol: str,
    horizon: str,
    as_of: datetime,
    limit: int = DEFAULT_LIMIT,
    calendar: TradingCalendar | None = None,
) -> AnalogResult:
    """在该标的**自身历史**中找与当前状态最像的若干天。"""
    trading_calendar = calendar or get_trading_calendar()
    moment = to_shanghai(as_of)

    model = await active_model(db, horizon=horizon)
    loaded = load_model_bundle(
        model.artifact_uri, model.model_key, model.version, model.target_horizon
    )
    config: FeatureSetConfig = load_feature_set(loaded.feature_set_version)
    normalizer: Normalizer = loaded.normalizer

    window = feature_window_sessions(config)
    # 取够长的历史：候选覆盖 + 每个候选自己的特征窗口
    lookback = window + config.history.next_5d_min_sessions
    daily, adjustments = await load_daily_bars(db, symbol, moment, lookback)
    if len(adjustments) > 1:
        raise InsufficientData(f"{symbol} 的日线出现多种复权基准 {sorted(adjustments)}")
    if not daily:
        raise InsufficientData(f"{symbol} 没有可用日线")

    benchmarks: dict[str, list[DailyBar]] = {}
    for key, bench_symbol in (
        (BENCH_CSI300, config.benchmarks["csi300"]),
        (BENCH_SSE, config.benchmarks["sse"]),
    ):
        bars, _ = await load_daily_bars(db, bench_symbol, moment, lookback)
        benchmarks[key] = bars
    bench_sessions = {key: [bar.session for bar in bars] for key, bars in benchmarks.items()}

    adjustment = next(iter(adjustments))

    # ── 当前状态（与线上推理同一份特征代码）────────────────────────────
    current_panel = PitPanel.build(
        symbol=symbol,
        data_cutoff=moment,
        daily=daily[-window:],
        benchmark_daily={
            key: _slice_to(bars, bench_sessions[key], daily[-1].session, window)
            for key, bars in benchmarks.items()
        },
        adjustment=adjustment,
        history_sessions=len(daily),
    )
    current_snapshot = build_feature_snapshot(
        current_panel, horizon=horizon, feature_set_version=config.version
    )
    current_vector = normalizer.standardize(
        [current_snapshot.values.get(name) for name in normalizer.names]
    )

    # ── 候选：后续 5 日收益必须已经实现 ────────────────────────────────
    last_index = len(daily) - 1
    latest_candidate_index = last_index - FORWARD_5D - RECENT_EXCLUSION_SESSIONS
    considered = 0
    scored: list[tuple[float, Analog]] = []

    for i in range(config.history.min_completed_sessions, latest_candidate_index + 1):
        candidate_session = daily[i].session
        considered += 1
        cutoff = session_close_at(candidate_session)

        panel = PitPanel.build(
            symbol=symbol,
            data_cutoff=cutoff,
            daily=daily[max(0, i + 1 - window) : i + 1],
            benchmark_daily={
                key: _slice_to(bars, bench_sessions[key], candidate_session, window)
                for key, bars in benchmarks.items()
            },
            adjustment=adjustment,
            history_sessions=i + 1,
        )
        try:
            snapshot = build_feature_snapshot(
                panel, horizon=horizon, feature_set_version=config.version
            )
        except InsufficientData:
            continue

        vector = normalizer.standardize(
            [snapshot.values.get(name) for name in normalizer.names]
        )
        distance, used = _distance(current_vector, vector)
        if used < MIN_FEATURE_OVERLAP * len(normalizer.names):
            continue
        if not math.isfinite(distance):
            continue

        scored.append(
            (
                distance,
                Analog(
                    session=candidate_session,
                    distance=distance,
                    features=dict(snapshot.values),
                    forward_return_1d=_forward_return(daily, i, FORWARD_1D),
                    forward_return_5d=_forward_return(daily, i, FORWARD_5D),
                ),
            )
        )

    valid = len(scored)
    if valid < MIN_VALID_CANDIDATES:
        # spec §10：有效候选 < 30 → 关闭功能并说明样本不足。绝不"凑够 10 个"返回。
        raise InsufficientData(
            f"{symbol} 的历史相似行情样本不足：有效候选 {valid} 个 < {MIN_VALID_CANDIDATES} 个，"
            f"该功能已关闭"
        )

    scored.sort(key=lambda item: (item[0], item[1].session))
    analogs = tuple(item[1] for item in scored[:limit])

    _ = trading_calendar  # 候选与后续收益都直接按已收盘日线索引推进，无需再查日历
    return AnalogResult(
        symbol=symbol,
        horizon=horizon,
        as_of=moment,
        feature_set_version=config.version,
        model_key=model.model_key,
        model_version=model.version,
        candidates_considered=considered,
        candidates_valid=valid,
        analogs=analogs,
    )


def _slice_to(
    bars: Sequence[DailyBar], sessions: Sequence[date], upto: date, window: int
) -> list[DailyBar]:
    from bisect import bisect_right

    position = bisect_right(list(sessions), upto)
    return list(bars[max(0, position - window) : position])


def _forward_return(daily: Sequence[DailyBar], index: int, days: int) -> float | None:
    target = index + days
    if target >= len(daily):
        return None
    base = daily[index].close
    if base <= 0:
        return None
    return daily[target].close / base - 1
