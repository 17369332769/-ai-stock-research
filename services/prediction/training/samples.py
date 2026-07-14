"""训练样本回放：把历史快照重放成一条条 point-in-time 样本。

关键：**训练与线上共用同一份特征代码**（``build_feature_snapshot``）。
这里不存在"训练版特征"和"线上版特征"两套实现 —— 那是 train/serve skew 的头号来源。

回放的 PIT 语义（每个样本都独立成立）：
- ``next_5d``：cutoff = 交易日 t 的收盘 15:00 → t 的日线可见；标签 = close(t+5)/close(t) - 1。
- ``today_close``：cutoff = 交易日 t 的 09:45 → **t 的日线不可见**（尚未收盘）；
  只有 t 的开盘价（09:30 就公开了）和 09:45 之前已完成的分钟线可见；
  标签 = close(t)/close(t-1) - 1。

成分股按 **当时有效** 的成分名单取样（spec §9.3：禁止用当前 300 只回填历史）。
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from apps.api.app.core.enums import PredictionHorizon
from apps.api.app.core.errors import InsufficientData
from apps.api.app.core.trading_calendar import TradingCalendar
from services.prediction.features.builder import FeatureSnapshot, build_feature_snapshot
from services.prediction.features.config import FeatureSetConfig
from services.prediction.features.panel import (
    BENCH_CSI300,
    BENCH_SSE,
    DailyBar,
    DocumentRef,
    MinuteBar,
    PitPanel,
)
from services.prediction.features.pit import PitViolation
from services.prediction.training.labels import (
    Label,
    compute_label,
    target_session_for,
    training_cutoff_for,
)

__all__ = [
    "InstrumentSeries",
    "MembershipIndex",
    "Sample",
    "SampleBuildStats",
    "build_samples",
    "feature_window_sessions",
]


def feature_window_sessions(config: FeatureSetConfig) -> int:
    """回放时每个样本只切出这么多根日线。

    最长的特征窗口是 ret_60 / ma_dist_60（需要 61 根），min_completed_sessions 就是它。
    多留 5 根余量。不切窗口的话回放是 O(样本数 × 全历史)，一次训练要跑几十分钟。
    """
    return config.history.min_completed_sessions + 5


@dataclass(slots=True)
class InstrumentSeries:
    """单个标的的完整历史（已按时间升序）。来自 Parquet 快照，不再回查数据库。"""

    symbol: str
    daily: list[DailyBar]
    minute_by_session: dict[date, list[MinuteBar]] = field(default_factory=dict)
    documents: list[DocumentRef] = field(default_factory=list)
    adjustment: str = "qfq"
    _session_index: dict[date, int] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.daily.sort(key=lambda bar: bar.bar_time)
        self.documents.sort(key=lambda doc: doc.published_at)
        self._session_index = {bar.session: i for i, bar in enumerate(self.daily)}

    def index_of(self, session: date) -> int | None:
        return self._session_index.get(session)

    @property
    def sessions(self) -> list[date]:
        return [bar.session for bar in self.daily]


@dataclass(frozen=True, slots=True)
class MembershipIndex:
    """指数成分的历史有效期。``members_at`` 返回**当时**有效的成分（spec §9.3）。"""

    periods: Mapping[str, tuple[tuple[date, date | None], ...]]

    def is_member(self, symbol: str, day: date) -> bool:
        for start, end in self.periods.get(symbol, ()):
            if start <= day and (end is None or day <= end):
                return True
        return False

    def members_at(self, day: date) -> list[str]:
        return sorted(symbol for symbol in self.periods if self.is_member(symbol, day))


@dataclass(frozen=True, slots=True)
class Sample:
    symbol: str
    session: date
    horizon: str
    snapshot: FeatureSnapshot
    label: Label
    benchmark_return: float | None  # 同期沪深300收益（用于 CSI300 方向参照基准）

    @property
    def target_return(self) -> float:
        return self.label.target_return

    @property
    def up(self) -> bool:
        return self.label.up


@dataclass(slots=True)
class SampleBuildStats:
    """回放过程中每一类跳过原因的计数 —— 训练前必须看这张表，否则不知道样本是怎么没的。"""

    considered: int = 0
    built: int = 0
    skipped_not_member: int = 0
    skipped_short_history: int = 0
    skipped_no_target: int = 0
    skipped_bad_price: int = 0
    skipped_insufficient_data: int = 0

    def to_json(self) -> dict[str, int]:
        return {
            "considered": self.considered,
            "built": self.built,
            "skipped_not_member": self.skipped_not_member,
            "skipped_short_history": self.skipped_short_history,
            "skipped_no_target": self.skipped_no_target,
            "skipped_bad_price": self.skipped_bad_price,
            "skipped_insufficient_data": self.skipped_insufficient_data,
        }


def _benchmark_slice(
    bars: Sequence[DailyBar], sessions: Sequence[date], cutoff_session: date, window: int
) -> list[DailyBar]:
    """取基准在 cutoff_session（含）之前的最后 ``window`` 根日线。"""
    position = bisect_right(sessions, cutoff_session)  # 严格大于 cutoff 的第一个
    lo = max(0, position - window)
    return list(bars[lo:position])


def build_samples(
    *,
    horizon: str,
    universe: MembershipIndex,
    series: Mapping[str, InstrumentSeries],
    benchmarks: Mapping[str, InstrumentSeries],
    calendar: TradingCalendar,
    config: FeatureSetConfig,
    start: date | None = None,
    end: date | None = None,
) -> tuple[list[Sample], SampleBuildStats]:
    """回放全部标的、全部交易日，产出 PIT 样本。"""
    window = feature_window_sessions(config)
    stats = SampleBuildStats()
    samples: list[Sample] = []

    bench_series = {
        BENCH_CSI300: benchmarks.get(BENCH_CSI300),
        BENCH_SSE: benchmarks.get(BENCH_SSE),
    }
    bench_sessions = {
        key: (value.sessions if value is not None else [])
        for key, value in bench_series.items()
    }
    csi300 = bench_series[BENCH_CSI300]
    csi300_index = {bar.session: i for i, bar in enumerate(csi300.daily)} if csi300 else {}

    for symbol, instrument in sorted(series.items()):
        for sample in _build_for_instrument(
            symbol=symbol,
            instrument=instrument,
            horizon=horizon,
            universe=universe,
            calendar=calendar,
            config=config,
            window=window,
            bench_series=bench_series,
            bench_sessions=bench_sessions,
            csi300=csi300,
            csi300_index=csi300_index,
            start=start,
            end=end,
            stats=stats,
        ):
            samples.append(sample)

    samples.sort(key=lambda item: (item.session, item.symbol))
    stats.built = len(samples)
    return samples, stats


def _build_for_instrument(
    *,
    symbol: str,
    instrument: InstrumentSeries,
    horizon: str,
    universe: MembershipIndex,
    calendar: TradingCalendar,
    config: FeatureSetConfig,
    window: int,
    bench_series: Mapping[str, InstrumentSeries | None],
    bench_sessions: Mapping[str, list[date]],
    csi300: InstrumentSeries | None,
    csi300_index: Mapping[date, int],
    start: date | None,
    end: date | None,
    stats: SampleBuildStats,
) -> Iterator[Sample]:
    daily = instrument.daily
    is_today = horizon == PredictionHorizon.TODAY_CLOSE
    min_index = config.history.min_completed_sessions  # 之前至少要有这么多根已收盘日线

    for i, bar in enumerate(daily):
        session = bar.session
        if start is not None and session < start:
            continue
        if end is not None and session > end:
            continue
        stats.considered += 1

        # 成分股必须按**当时**有效的名单取样（禁止用今天的 300 只回填历史）
        if not universe.is_member(symbol, session):
            stats.skipped_not_member += 1
            continue

        # 可见日线的右边界：今日模型看不到当天（未收盘），一周模型看得到当天收盘
        visible_end = i if is_today else i + 1
        if visible_end < min_index:
            stats.skipped_short_history += 1
            continue

        target_session = target_session_for(session, horizon, calendar)
        target_index = instrument.index_of(target_session)
        if target_index is None:
            stats.skipped_no_target += 1  # 标签还没实现（或该日停牌），不能造样本
            continue

        # today 的参考价固定是昨收；next_5d 是当日收盘（spec §7.4）
        reference_price = daily[i - 1].close if is_today else bar.close
        target_price = daily[target_index].close
        if reference_price <= 0 or target_price <= 0:
            stats.skipped_bad_price += 1
            continue

        cutoff = training_cutoff_for(session, horizon)
        visible_daily = daily[max(0, visible_end - window) : visible_end]
        minute = instrument.minute_by_session.get(session, []) if is_today else []
        documents = _documents_before(instrument.documents, cutoff)

        # 今日模型在 09:45 也看不到当天的指数收盘 —— 基准的右边界必须跟着退一天
        bench_cutoff = daily[i - 1].session if is_today else session
        bench_panel: dict[str, list[DailyBar]] = {}
        for key in (BENCH_CSI300, BENCH_SSE):
            bench = bench_series[key]
            bench_panel[key] = _benchmark_slice(
                bench.daily if bench is not None else [],
                bench_sessions[key],
                bench_cutoff,
                window,
            )

        try:
            panel = PitPanel.build(
                symbol=symbol,
                data_cutoff=cutoff,
                daily=visible_daily,
                minute=minute,
                documents=documents,
                benchmark_daily=bench_panel,
                # 当日开盘价 09:30 就公开了，09:45 拿它不是泄漏；
                # 但当日的 high/low/close/volume 绝不可见，所以只传一个 float，不传整根 bar。
                session_open=bar.open if is_today else None,
                session_open_source="daily_open_field" if is_today else None,
                adjustment=instrument.adjustment,
                history_sessions=visible_end,
            )
            snapshot = build_feature_snapshot(
                panel, horizon=horizon, feature_set_version=config.version
            )
        except InsufficientData:
            stats.skipped_insufficient_data += 1
            continue
        except PitViolation:  # 回放逻辑写错了 —— 必须炸，不能跳过
            raise

        label = compute_label(
            session=session,
            horizon=horizon,
            reference_price=reference_price,
            target_price=target_price,
            calendar=calendar,
        )
        yield Sample(
            symbol=symbol,
            session=session,
            horizon=horizon,
            snapshot=snapshot,
            label=label,
            benchmark_return=_benchmark_return(
                csi300, csi300_index, session, target_session, is_today
            ),
        )


def _documents_before(documents: Sequence[DocumentRef], cutoff: datetime) -> list[DocumentRef]:
    """按 published_at 切片（documents 已升序，二分）。

    只往前带 200 条：事件特征最长窗口 5 天，hours_since_last_document 也只看最近一条，
    带太多纯属浪费。切片右边界严格 <= cutoff —— 这是事件特征的 PIT 死线。
    """
    lo, hi = 0, len(documents)
    while lo < hi:
        mid = (lo + hi) // 2
        if documents[mid].published_at <= cutoff:
            lo = mid + 1
        else:
            hi = mid
    return list(documents[max(0, lo - 200) : lo])


def _benchmark_return(
    csi300: InstrumentSeries | None,
    index: Mapping[date, int],
    session: date,
    target_session: date,
    is_today: bool,
) -> float | None:
    """沪深300 在同一目标区间上的**已实现**收益（只用于 CSI300 方向参照基准）。"""
    if csi300 is None:
        return None
    anchor_session = session
    if is_today:
        # today 的参考点是昨收，基准也必须用昨收，否则区间对不齐
        position = index.get(session)
        if position is None or position == 0:
            return None
        anchor_session = csi300.daily[position - 1].session
    start_index = index.get(anchor_session)
    end_index = index.get(target_session)
    if start_index is None or end_index is None:
        return None
    start_close = csi300.daily[start_index].close
    end_close = csi300.daily[end_index].close
    if start_close <= 0:
        return None
    return end_close / start_close - 1
