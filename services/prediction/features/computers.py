"""特征计算函数（spec §9.2）。

每个函数只接收一个 ``PitPanel``。面板在构造时已经把 data_cutoff 之后的数据全部剔除并断言过，
因此这里**根本拿不到未来数据** —— PIT 不是靠这些函数的自觉，而是靠类型边界。

返回 ``None`` 表示"数据不足以计算"，绝不返回 0 或任何编造的默认值：
0 在收益率/相对强弱里是一个有意义的取值，用它冒充缺失会污染模型。
缺失如何进入模型由 yaml 的 ``missing`` 策略决定（nan / zero）。
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from datetime import time, timedelta
from itertools import pairwise

from apps.api.app.core.clock import to_shanghai
from services.prediction.features.config import FeatureSetConfig, FeatureSpec
from services.prediction.features.panel import BENCH_CSI300, BENCH_SSE, DailyBar, PitPanel

__all__ = ["FEATURE_COMPUTERS", "Computer", "implemented_feature_names"]

Computer = Callable[[PitPanel, FeatureSpec, FeatureSetConfig], float | None]

# 今日模型的 "09:45 后当日收益" 基准时刻（spec §9.2）
ANCHOR_0945 = time(9, 45)


# ── 通用数值工具 ────────────────────────────────────────────────────────────


def _pct_change(newer: float, older: float) -> float | None:
    if older == 0:
        return None
    return newer / older - 1


def _returns(closes: Sequence[float]) -> list[float]:
    """相邻收盘价的简单收益序列。"""
    out: list[float] = []
    for prev, cur in pairwise(closes):
        change = _pct_change(cur, prev)
        if change is None:
            return []  # 出现 0 价格：整段不可信，直接判缺失
        out.append(change)
    return out


def _trailing_return(closes: Sequence[float], window: int) -> float | None:
    if len(closes) < window + 1:
        return None
    return _pct_change(closes[-1], closes[-1 - window])


def _ma_distance(closes: Sequence[float], window: int) -> float | None:
    if len(closes) < window:
        return None
    average = statistics.fmean(closes[-window:])
    if average == 0:
        return None
    return closes[-1] / average - 1


def _realized_vol(closes: Sequence[float], window: int) -> float | None:
    if len(closes) < window + 1:
        return None
    rets = _returns(closes[-(window + 1) :])
    if len(rets) < 2:
        return None
    return statistics.stdev(rets)


def _true_range(bar: DailyBar, previous_close: float) -> float:
    return max(
        bar.high - bar.low,
        abs(bar.high - previous_close),
        abs(bar.low - previous_close),
    )


# ── 价格动量 ────────────────────────────────────────────────────────────────


def _momentum(panel: PitPanel, spec: FeatureSpec, _cfg: FeatureSetConfig) -> float | None:
    return _trailing_return(panel.closes(), spec.window)


# ── 趋势 ────────────────────────────────────────────────────────────────────


def _trend(panel: PitPanel, spec: FeatureSpec, _cfg: FeatureSetConfig) -> float | None:
    return _ma_distance(panel.closes(), spec.window)


# ── 波动 ────────────────────────────────────────────────────────────────────


def _volatility(panel: PitPanel, spec: FeatureSpec, _cfg: FeatureSetConfig) -> float | None:
    return _realized_vol(panel.closes(), spec.window)


def _atr(panel: PitPanel, spec: FeatureSpec, _cfg: FeatureSetConfig) -> float | None:
    window = spec.window
    bars = panel.daily
    if len(bars) < window + 1:
        return None
    ranges = [
        _true_range(bars[i], bars[i - 1].close) for i in range(len(bars) - window, len(bars))
    ]
    last_close = bars[-1].close
    if last_close == 0:
        return None
    return statistics.fmean(ranges) / last_close


def _amplitude(panel: PitPanel, _spec: FeatureSpec, _cfg: FeatureSetConfig) -> float | None:
    bars = panel.daily
    if len(bars) < 2:
        return None
    previous_close = bars[-2].close
    if previous_close == 0:
        return None
    return (bars[-1].high - bars[-1].low) / previous_close


# ── 成交 ────────────────────────────────────────────────────────────────────


def _volume_rel_ma20(panel: PitPanel, spec: FeatureSpec, _cfg: FeatureSetConfig) -> float | None:
    volumes = panel.volumes()
    if len(volumes) < spec.window:
        return None
    average = statistics.fmean(volumes[-spec.window :])
    if average == 0:
        return None
    return volumes[-1] / average


def _turnover_rate(panel: PitPanel, _spec: FeatureSpec, _cfg: FeatureSetConfig) -> float | None:
    """换手率 = 成交量 / 自由流通股本。

    spec §6 的 instruments/bars 表都没有股本字段，因此当前数据口径下**恒为缺失**。
    这是有意为之：宁可让模型少一个特征，也绝不用"成交额/收盘价"之类的东西冒充换手率。
    训练层会把全缺失的列标为 unavailable 并写进模型卡。
    """
    shares = panel.free_float_shares
    if shares is None or shares <= 0 or not panel.daily:
        return None
    return panel.daily[-1].volume / shares


def _volume_ratio(panel: PitPanel, spec: FeatureSpec, _cfg: FeatureSetConfig) -> float | None:
    """量比（日频形式）：最新成交量 / 之前 N 个交易日成交量均值。"""
    volumes = panel.volumes()
    window = spec.window
    if len(volumes) < window + 1:
        return None
    baseline = statistics.fmean(volumes[-(window + 1) : -1])
    if baseline == 0:
        return None
    return volumes[-1] / baseline


# ── 市场（基准与相对强弱）──────────────────────────────────────────────────


def _benchmark_return(panel: PitPanel, name: str, window: int) -> float | None:
    return _trailing_return(panel.benchmark_closes(name), window)


def _bench_csi300(panel: PitPanel, spec: FeatureSpec, _cfg: FeatureSetConfig) -> float | None:
    return _benchmark_return(panel, BENCH_CSI300, spec.window)


def _bench_sse(panel: PitPanel, spec: FeatureSpec, _cfg: FeatureSetConfig) -> float | None:
    return _benchmark_return(panel, BENCH_SSE, spec.window)


def _relative_strength(panel: PitPanel, name: str, window: int) -> float | None:
    own = _trailing_return(panel.closes(), window)
    bench = _benchmark_return(panel, name, window)
    if own is None or bench is None:
        return None
    return own - bench


def _rel_csi300(panel: PitPanel, spec: FeatureSpec, _cfg: FeatureSetConfig) -> float | None:
    return _relative_strength(panel, BENCH_CSI300, spec.window)


def _rel_sse(panel: PitPanel, spec: FeatureSpec, _cfg: FeatureSetConfig) -> float | None:
    return _relative_strength(panel, BENCH_SSE, spec.window)


# ── 事件（只用 published_at <= data_cutoff 的文档）──────────────────────────


def _count_documents(panel: PitPanel, days: int, kind: str | None = None) -> float:
    since = panel.data_cutoff - timedelta(days=days)
    return float(
        sum(
            1
            for doc in panel.documents
            if doc.published_at > since and (kind is None or doc.document_type == kind)
        )
    )


def _doc_count_1d(panel: PitPanel, _spec: FeatureSpec, _cfg: FeatureSetConfig) -> float | None:
    return _count_documents(panel, 1)


def _doc_count_5d(panel: PitPanel, _spec: FeatureSpec, _cfg: FeatureSetConfig) -> float | None:
    return _count_documents(panel, 5)


def _announcement_count_5d(panel: PitPanel, _spec: FeatureSpec, _cfg: FeatureSetConfig) -> float | None:
    return _count_documents(panel, 5, "announcement")


def _news_count_5d(panel: PitPanel, _spec: FeatureSpec, _cfg: FeatureSetConfig) -> float | None:
    return _count_documents(panel, 5, "news")


def _hours_since_last_document(
    panel: PitPanel, _spec: FeatureSpec, _cfg: FeatureSetConfig
) -> float | None:
    if not panel.documents:
        return None  # 没有可见文档 —— 这是"未知"，不是"0 小时前刚发过公告"
    latest = panel.documents[-1].published_at
    return (panel.data_cutoff - latest).total_seconds() / 3600.0


# ── 今日模型专用（盘中）────────────────────────────────────────────────────


def _open_gap(panel: PitPanel, _spec: FeatureSpec, _cfg: FeatureSetConfig) -> float | None:
    previous_close = panel.last_close
    if panel.session_open is None or previous_close is None or previous_close == 0:
        return None
    return panel.session_open / previous_close - 1


def _ret_since_0945(panel: PitPanel, _spec: FeatureSpec, _cfg: FeatureSetConfig) -> float | None:
    """09:45 之后的当日收益。09:45 之前没有这个特征（今日预测本来也不允许在 09:45 前生成）。"""
    if not panel.minute:
        return None
    anchor: float | None = None
    for bar in panel.minute:
        if to_shanghai(bar.bar_time).time() == ANCHOR_0945:
            anchor = bar.close
            break
    if anchor is None or anchor == 0:
        return None
    return panel.minute[-1].close / anchor - 1


def _morning_range(panel: PitPanel, _spec: FeatureSpec, _cfg: FeatureSetConfig) -> float | None:
    previous_close = panel.last_close
    if not panel.minute or previous_close is None or previous_close == 0:
        return None
    high = max(bar.high for bar in panel.minute)
    low = min(bar.low for bar in panel.minute)
    return (high - low) / previous_close


def _morning_volume_share(panel: PitPanel, spec: FeatureSpec, _cfg: FeatureSetConfig) -> float | None:
    if not panel.minute:
        return None
    volumes = panel.volumes()
    window = spec.window
    if len(volumes) < window:
        return None
    baseline = statistics.fmean(volumes[-window:])
    if baseline == 0:
        return None
    return panel.session_minute_volume / baseline


# ── 注册表：yaml 里的每个 name 必须在这里有实现，反之亦然 ──────────────────

FEATURE_COMPUTERS: dict[str, Computer] = {
    "ret_1": _momentum,
    "ret_2": _momentum,
    "ret_5": _momentum,
    "ret_10": _momentum,
    "ret_20": _momentum,
    "ret_60": _momentum,
    "ma_dist_5": _trend,
    "ma_dist_10": _trend,
    "ma_dist_20": _trend,
    "ma_dist_60": _trend,
    "vol_5": _volatility,
    "vol_20": _volatility,
    "atr_14": _atr,
    "amplitude_1": _amplitude,
    "volume_rel_ma20": _volume_rel_ma20,
    "turnover_rate": _turnover_rate,
    "volume_ratio": _volume_ratio,
    "bench_csi300_ret_1": _bench_csi300,
    "bench_csi300_ret_5": _bench_csi300,
    "bench_csi300_ret_20": _bench_csi300,
    "bench_sse_ret_1": _bench_sse,
    "bench_sse_ret_5": _bench_sse,
    "bench_sse_ret_20": _bench_sse,
    "rel_strength_csi300_1": _rel_csi300,
    "rel_strength_csi300_5": _rel_csi300,
    "rel_strength_csi300_20": _rel_csi300,
    "rel_strength_sse_1": _rel_sse,
    "rel_strength_sse_5": _rel_sse,
    "rel_strength_sse_20": _rel_sse,
    "doc_count_1d": _doc_count_1d,
    "doc_count_5d": _doc_count_5d,
    "announcement_count_5d": _announcement_count_5d,
    "news_count_5d": _news_count_5d,
    "hours_since_last_document": _hours_since_last_document,
    "open_gap": _open_gap,
    "ret_since_0945": _ret_since_0945,
    "morning_range": _morning_range,
    "morning_volume_share": _morning_volume_share,
}


def implemented_feature_names() -> tuple[str, ...]:
    return tuple(FEATURE_COMPUTERS)
