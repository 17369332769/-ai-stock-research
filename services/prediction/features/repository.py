"""从 PostgreSQL 读取 PIT 面板（spec §9.2 的取数侧）。

三道防线的第一道在这里：SQL 的 WHERE 条件本身就不取 cutoff 之后的行。
第二、三道（过滤 + 断言）在 ``PitPanel.build`` 里。

**这是 services/prediction 里唯一允许写 SQL 的地方**（模块边界 spec §5.1：
prediction 不直接调外部 URL，只读已入库数据）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.clock import SHANGHAI, to_shanghai, trading_day_of
from apps.api.app.core.enums import CSI300_CODE, Timeframe
from apps.api.app.core.errors import InsufficientData
from apps.api.app.models.tables import Bar, Document, Quote, UniverseMembership
from services.prediction.features.config import FeatureSetConfig
from services.prediction.features.panel import (
    BENCH_CSI300,
    BENCH_SSE,
    DailyBar,
    DocumentRef,
    MinuteBar,
    PitPanel,
)

__all__ = [
    "QuoteSnapshot",
    "is_universe_member_at",
    "load_daily_bars",
    "load_latest_quote",
    "load_pit_panel",
    "load_session_close",
    "universe_members_at",
]

# 日线回看窗口：必须覆盖一周模型的 3 年门槛，否则 completed_sessions 会被人为截断，
# 把"数据够"误判成"数据不够"。+60 是特征窗口（ret_60）的余量。
_LOOKBACK_MARGIN = 60


def _f(value: Decimal | float | None) -> float | None:
    return None if value is None else float(value)


def _fr(value: Decimal | float) -> float:
    return float(value)


class QuoteSnapshot:
    """cutoff 之前可见的最新报价快照。"""

    __slots__ = ("observed_at", "open", "previous_close", "price")

    def __init__(
        self,
        observed_at: datetime,
        price: float,
        previous_close: float,
        open_: float | None,
    ) -> None:
        self.observed_at = observed_at
        self.price = price
        self.previous_close = previous_close
        self.open = open_


def _daily_lookback(config: FeatureSetConfig) -> int:
    return (
        max(
            config.history.next_5d_min_sessions,
            config.history.today_close_min_sessions,
            config.history.min_completed_sessions,
        )
        + _LOOKBACK_MARGIN
    )


def _recent_daily_stmt(symbol: str, cutoff: datetime, limit: int) -> Select[tuple[Bar]]:
    return (
        select(Bar)
        .where(
            Bar.symbol == symbol,
            Bar.timeframe == Timeframe.DAY1.value,
            Bar.bar_time <= cutoff,  # 第一道防线：SQL 层就不取未来
        )
        .order_by(Bar.bar_time.desc())
        .limit(limit)
    )


async def load_daily_bars(
    session: AsyncSession, symbol: str, cutoff: datetime, limit: int
) -> tuple[list[DailyBar], set[str]]:
    """返回（升序日线, 出现过的复权基准集合）。"""
    rows = (await session.execute(_recent_daily_stmt(symbol, cutoff, limit))).scalars().all()
    adjustments = {row.adjustment for row in rows}
    bars = [
        DailyBar(
            bar_time=row.bar_time,
            open=_fr(row.open),
            high=_fr(row.high),
            low=_fr(row.low),
            close=_fr(row.close),
            volume=_fr(row.volume),
            amount=_f(row.amount),
        )
        for row in reversed(rows)
    ]
    return bars, adjustments


async def _load_minute_bars(
    session: AsyncSession, symbol: str, cutoff: datetime, session_day: date
) -> tuple[list[MinuteBar], set[str]]:
    day_start = datetime.combine(session_day, datetime.min.time(), tzinfo=SHANGHAI)
    stmt = (
        select(Bar)
        .where(
            Bar.symbol == symbol,
            Bar.timeframe == Timeframe.MIN5.value,
            Bar.bar_time > day_start,
            Bar.bar_time <= cutoff,
        )
        .order_by(Bar.bar_time.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    adjustments = {row.adjustment for row in rows}
    bars = [
        MinuteBar(
            bar_time=row.bar_time,
            open=_fr(row.open),
            high=_fr(row.high),
            low=_fr(row.low),
            close=_fr(row.close),
            volume=_fr(row.volume),
            amount=_f(row.amount),
        )
        for row in rows
    ]
    return bars, adjustments


async def _load_documents(
    session: AsyncSession, symbol: str, cutoff: datetime, lookback_days: int
) -> list[DocumentRef]:
    since = cutoff - timedelta(days=lookback_days)
    stmt = (
        select(Document.published_at, Document.document_type)
        .where(
            Document.symbol == symbol,
            Document.published_at <= cutoff,  # 死线：published_at，不是 observed_at
            Document.published_at > since,
        )
        .order_by(Document.published_at.asc())
    )
    rows = (await session.execute(stmt)).all()
    return [DocumentRef(published_at=row[0], document_type=row[1]) for row in rows]


async def load_latest_quote(
    session: AsyncSession, symbol: str, cutoff: datetime
) -> QuoteSnapshot | None:
    stmt = (
        select(Quote.observed_at, Quote.price, Quote.previous_close, Quote.open)
        .where(Quote.symbol == symbol, Quote.observed_at <= cutoff)
        .order_by(Quote.observed_at.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    return QuoteSnapshot(
        observed_at=row[0],
        price=_fr(row[1]),
        previous_close=_fr(row[2]),
        open_=_f(row[3]),
    )


async def load_session_close(session: AsyncSession, symbol: str, day: date) -> float | None:
    """某个交易日的（当前复权基准下的）收盘价。结算与相似行情用。"""
    day_start = datetime.combine(day, datetime.min.time(), tzinfo=SHANGHAI)
    day_end = day_start + timedelta(days=1)
    stmt = select(Bar.close).where(
        Bar.symbol == symbol,
        Bar.timeframe == Timeframe.DAY1.value,
        Bar.bar_time >= day_start,
        Bar.bar_time < day_end,
    )
    row = (await session.execute(stmt)).first()
    return None if row is None else _fr(row[0])


async def universe_members_at(
    session: AsyncSession, as_of: date, universe_code: str = CSI300_CODE
) -> list[str]:
    """as_of 当天**当时有效**的成分股（spec §9.3：禁止用当前 300 只回填历史）。"""
    stmt = (
        select(UniverseMembership.symbol)
        .where(
            UniverseMembership.universe_code == universe_code,
            UniverseMembership.effective_from <= as_of,
            _still_effective(as_of),
        )
        .distinct()
        .order_by(UniverseMembership.symbol)
    )
    return [row[0] for row in (await session.execute(stmt)).all()]


async def is_universe_member_at(
    session: AsyncSession, symbol: str, as_of: date, universe_code: str = CSI300_CODE
) -> bool:
    stmt = select(func.count()).select_from(UniverseMembership).where(
        UniverseMembership.universe_code == universe_code,
        UniverseMembership.symbol == symbol,
        UniverseMembership.effective_from <= as_of,
        _still_effective(as_of),
    )
    count = (await session.execute(stmt)).scalar_one()
    return count > 0


def _still_effective(as_of: date):  # type: ignore[no-untyped-def]  # SQLAlchemy 布尔表达式
    return and_(
        (UniverseMembership.effective_to.is_(None))
        | (UniverseMembership.effective_to >= as_of)
    )


async def load_pit_panel(
    session: AsyncSession,
    *,
    symbol: str,
    data_cutoff: datetime,
    config: FeatureSetConfig,
    include_minute: bool,
) -> PitPanel:
    """构建 ``symbol`` 在 ``data_cutoff`` 时刻的 PIT 面板。

    ``include_minute=True`` 只对今日模型有意义（一周模型不用盘中特征）。
    """
    cutoff = to_shanghai(data_cutoff)
    session_day = trading_day_of(cutoff)
    lookback = _daily_lookback(config)

    daily, daily_adjustments = await load_daily_bars(session, symbol, cutoff, lookback)
    if not daily:
        raise InsufficientData(f"{symbol} 在 {cutoff.isoformat()} 之前没有任何已收盘日线")

    minute: list[MinuteBar] = []
    minute_adjustments: set[str] = set()
    if include_minute:
        minute, minute_adjustments = await _load_minute_bars(session, symbol, cutoff, session_day)

    _assert_single_adjustment(symbol, daily_adjustments, minute_adjustments)
    adjustment = next(iter(daily_adjustments))

    documents = await _load_documents(session, symbol, cutoff, config.event.document_lookback_days)

    benchmarks: dict[str, list[DailyBar]] = {}
    for key, bench_symbol in (
        (BENCH_CSI300, config.benchmarks["csi300"]),
        (BENCH_SSE, config.benchmarks["sse"]),
    ):
        bench_bars, _ = await load_daily_bars(session, bench_symbol, cutoff, lookback)
        benchmarks[key] = bench_bars

    session_open: float | None = None
    session_open_source: str | None = None
    if minute:
        session_open = minute[0].open
        session_open_source = "minute_bar"
    elif include_minute:
        quote = await load_latest_quote(session, symbol, cutoff)
        if quote is not None and quote.open is not None and quote.observed_at.date() == session_day:
            session_open = quote.open
            # 报价是未复权价，日线是 qfq —— 除权日 open_gap 会有偏差，这里如实标注来源。
            session_open_source = "quote_raw"

    return PitPanel.build(
        symbol=symbol,
        data_cutoff=cutoff,
        daily=daily,
        minute=minute,
        documents=documents,
        benchmark_daily=benchmarks,
        session_open=session_open,
        session_open_source=session_open_source,
        adjustment=adjustment,
    )


def _assert_single_adjustment(
    symbol: str, daily: Sequence[str] | set[str], minute: Sequence[str] | set[str]
) -> None:
    """日线与分钟线必须同一复权基准，否则 open_gap / morning_range 在除权日会算错。

    绝不静默换算：口径不一致就是数据问题，fail closed。
    """
    daily_set = set(daily)
    minute_set = set(minute)
    if len(daily_set) > 1:
        raise InsufficientData(f"{symbol} 的日线出现多种复权基准 {sorted(daily_set)}，拒绝构建特征")
    if minute_set and minute_set != daily_set:
        raise InsufficientData(
            f"{symbol} 的分钟线复权基准 {sorted(minute_set)} 与日线 {sorted(daily_set)} 不一致，"
            f"拒绝构建特征（除权日会算出错误的开盘缺口）"
        )
