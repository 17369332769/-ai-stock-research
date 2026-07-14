"""PIT 数据面板：特征计算的**唯一**输入。

设计要点：
- ``PitPanel`` 一旦构造成功，就保证里面**没有任何** data_cutoff 之后的数据。
  特征函数因此不需要（也没有能力）再去判断时间 —— 它们拿不到未来数据。
- 构造只走 ``PitPanel.build()``：先过滤、再断言。直接 ``PitPanel(...)`` 也会在
  ``__post_init__`` 里跑断言，所以绕不过去。
- 面板是纯 Python（不依赖 pandas），确定性强、易于在测试里手工构造。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

from apps.api.app.core.clock import to_shanghai, trading_day_of
from services.prediction.features.pit import (
    PitViolation,
    assert_no_future_daily_bars,
    assert_no_future_documents,
    assert_no_future_minute_bars,
    require_aware,
    visible_daily_bars,
    visible_documents,
    visible_minute_bars,
)

__all__ = [
    "BENCH_CSI300",
    "BENCH_SSE",
    "DailyBar",
    "DocumentRef",
    "MinuteBar",
    "PitPanel",
]

BENCH_CSI300 = "csi300"
BENCH_SSE = "sse"


@dataclass(frozen=True, slots=True)
class DailyBar:
    """已复权（qfq）日线。``bar_time`` 的日期部分即交易日。"""

    bar_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float | None = None

    @property
    def session(self) -> date:
        return to_shanghai(self.bar_time).date()


@dataclass(frozen=True, slots=True)
class MinuteBar:
    """5 分钟线。``bar_time`` 是该 bar 的**结束**时刻。"""

    bar_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float | None = None

    @property
    def session(self) -> date:
        return to_shanghai(self.bar_time).date()


@dataclass(frozen=True, slots=True)
class DocumentRef:
    """事件特征只需要时间与类型；正文不进特征（正文归 services/research）。"""

    published_at: datetime
    document_type: str  # 'announcement' | 'news'


@dataclass(frozen=True, slots=True)
class PitPanel:
    symbol: str
    data_cutoff: datetime
    daily: tuple[DailyBar, ...]
    minute: tuple[MinuteBar, ...]
    documents: tuple[DocumentRef, ...]
    benchmark_daily: Mapping[str, tuple[DailyBar, ...]] = field(default_factory=dict)
    # 当日开盘价（来自当日首根分钟 bar，或 cutoff 之前可见的最新 quote）；开盘缺口特征要用
    session_open: float | None = None
    session_open_source: str | None = None
    # 复权基准。日线与分钟线必须同基准，否则 open_gap / morning_range 会在除权日算错。
    # 由 repository 校验后填入；不一致直接 InsufficientData，不做静默换算。
    adjustment: str = "qfq"
    # 换手率需要自由流通股本。spec §6 的表里没有这个字段 —— 缺失即 None，绝不猜。
    free_float_shares: float | None = None
    # 该标的在 cutoff 前的**已完成交易日总数**。可以大于 len(daily)：
    # 训练回放时只切出特征窗口（~66 根）以免 O(n²)，但"这只股票有没有 3 年日线"
    # 这个启用门槛仍要看真实总数。None 表示 daily 就是全部历史。
    history_sessions: int | None = None

    def __post_init__(self) -> None:
        cutoff = require_aware(self.data_cutoff, "data_cutoff")
        assert_no_future_daily_bars(self.daily, cutoff)
        assert_no_future_minute_bars(self.minute, cutoff)
        assert_no_future_documents(self.documents, cutoff)
        for name, bars in self.benchmark_daily.items():
            try:
                assert_no_future_daily_bars(bars, cutoff)
            except PitViolation as exc:  # 补上基准名，便于定位
                raise PitViolation(f"基准 {name}：{exc}") from exc
        if any(a.bar_time > b.bar_time for a, b in zip(self.daily, self.daily[1:], strict=False)):
            raise PitViolation(f"{self.symbol} 日线未按时间升序")
        if any(a.bar_time > b.bar_time for a, b in zip(self.minute, self.minute[1:], strict=False)):
            raise PitViolation(f"{self.symbol} 分钟线未按时间升序")

    # ── 构造 ────────────────────────────────────────────────────────────
    @classmethod
    def build(
        cls,
        *,
        symbol: str,
        data_cutoff: datetime,
        daily: Iterable[DailyBar],
        minute: Iterable[MinuteBar] = (),
        documents: Iterable[DocumentRef] = (),
        benchmark_daily: Mapping[str, Iterable[DailyBar]] | None = None,
        session_open: float | None = None,
        session_open_source: str | None = None,
        adjustment: str = "qfq",
        free_float_shares: float | None = None,
        history_sessions: int | None = None,
    ) -> PitPanel:
        """过滤掉 cutoff 之后的一切，再交给 ``__post_init__`` 断言。

        分钟线只保留**当前交易日**的：早盘特征讲的是"今天"，历史分钟线没有意义，
        留着反而会让 morning_* 特征在跨日时算错。
        """
        cutoff = require_aware(data_cutoff, "data_cutoff")
        session = trading_day_of(cutoff)
        visible_minutes = tuple(
            bar for bar in visible_minute_bars(minute, cutoff) if bar.session == session
        )
        benchmarks: dict[str, tuple[DailyBar, ...]] = {}
        for name, bars in (benchmark_daily or {}).items():
            benchmarks[name] = visible_daily_bars(bars, cutoff)

        resolved_open = session_open
        resolved_source = session_open_source
        if resolved_open is None and visible_minutes:
            resolved_open = visible_minutes[0].open
            resolved_source = "minute_bar"

        return cls(
            symbol=symbol,
            data_cutoff=cutoff,
            daily=visible_daily_bars(daily, cutoff),
            minute=visible_minutes,
            documents=visible_documents(documents, cutoff),
            benchmark_daily=benchmarks,
            session_open=resolved_open,
            session_open_source=resolved_source,
            adjustment=adjustment,
            free_float_shares=free_float_shares,
            history_sessions=history_sessions,
        )

    # ── 查询 ────────────────────────────────────────────────────────────
    @property
    def session(self) -> date:
        """cutoff 所在的自然日（是否交易日由 TradingCalendar 判定）。"""
        return trading_day_of(self.data_cutoff)

    @property
    def loaded_sessions(self) -> int:
        """面板里实际装了多少根日线 —— 决定特征算不算得出来。"""
        return len(self.daily)

    @property
    def completed_sessions(self) -> int:
        """标的在 cutoff 前的已完成交易日总数 —— 决定模型启不启用（3 年 / 120 日门槛）。"""
        return self.history_sessions if self.history_sessions is not None else len(self.daily)

    @property
    def last_close(self) -> float | None:
        """最近一个**已收盘**交易日的收盘价。今日盘中预测时，它就是昨收。"""
        return self.daily[-1].close if self.daily else None

    @property
    def last_session(self) -> date | None:
        return self.daily[-1].session if self.daily else None

    def closes(self) -> list[float]:
        return [bar.close for bar in self.daily]

    def volumes(self) -> list[float]:
        return [bar.volume for bar in self.daily]

    def benchmark_closes(self, name: str) -> list[float]:
        return [bar.close for bar in self.benchmark_daily.get(name, ())]

    @property
    def session_minute_volume(self) -> float:
        return sum(bar.volume for bar in self.minute)
