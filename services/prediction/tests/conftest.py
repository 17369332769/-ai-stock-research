"""测试夹具（spec §16.1）。

铁律：
- **不依赖运行机器的当前日期** → 一律用 ``StaticTradingCalendar`` 夹具 + ``FixedClock``。
- **不访问公网** → 全部数据在内存里构造，没有任何 HTTP / DB 调用。
- 交易日历刻意包含真实的中国节假日（春节、国庆、跨年），
  这样"第 5 个后续交易日"的测试才是真的在考节假日，而不是在考算术。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from apps.api.app.core.clock import SHANGHAI, FixedClock
from apps.api.app.core.runtime import reset_runtime, set_clock, set_trading_calendar
from apps.api.app.core.trading_calendar import StaticTradingCalendar
from services.prediction.features.config import load_feature_set
from services.prediction.features.panel import DailyBar, DocumentRef, MinuteBar

# ── 交易日历：2024-2026，去掉周末与主要法定节假日 ──────────────────────────

# 只列出会被测试踩到的休市日（春节 / 国庆 / 元旦 / 五一 / 清明 / 端午 / 中秋）
HOLIDAYS: frozenset[date] = frozenset(
    {
        # 2025 元旦
        date(2025, 1, 1),
        # 2025 春节（1/28 - 2/4）
        *(date(2025, 1, 28) + timedelta(days=i) for i in range(8)),
        # 2025 清明
        date(2025, 4, 4),
        # 2025 五一
        *(date(2025, 5, 1) + timedelta(days=i) for i in range(5)),
        # 2025 端午
        date(2025, 6, 2),
        # 2025 国庆 + 中秋（10/1 - 10/8）
        *(date(2025, 10, 1) + timedelta(days=i) for i in range(8)),
        # 2026 元旦
        date(2026, 1, 1),
        date(2026, 1, 2),
        # 2026 春节（2/16 - 2/22）
        *(date(2026, 2, 16) + timedelta(days=i) for i in range(7)),
        # 2026 清明
        date(2026, 4, 6),
        # 2026 五一
        *(date(2026, 5, 1) + timedelta(days=i) for i in range(5)),
        # 2026 国庆
        *(date(2026, 10, 1) + timedelta(days=i) for i in range(8)),
    }
)


def _sessions(start: date, end: date) -> list[date]:
    days: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5 and cursor not in HOLIDAYS:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


TEST_SESSIONS: list[date] = _sessions(date(2023, 1, 3), date(2026, 12, 31))


@pytest.fixture
def calendar() -> StaticTradingCalendar:
    return StaticTradingCalendar(TEST_SESSIONS)


@pytest.fixture(autouse=True)
def _runtime(calendar: StaticTradingCalendar):  # type: ignore[no-untyped-def]
    """每个测试都注入固定时钟与测试日历；结束后复位。"""
    set_trading_calendar(calendar)
    set_clock(FixedClock(datetime(2026, 7, 14, 9, 45, tzinfo=SHANGHAI)))
    yield
    reset_runtime()


@pytest.fixture
def feature_config():  # type: ignore[no-untyped-def]
    return load_feature_set("v1")


# ── 造数工具 ────────────────────────────────────────────────────────────────


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=SHANGHAI)


def close_time(day: date) -> datetime:
    """交易日收盘 15:00 —— 日线的可见时刻。"""
    return at(day, 15, 0)


def daily_bar(
    day: date,
    close: float,
    *,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: float = 1_000_000.0,
    bar_time: datetime | None = None,
) -> DailyBar:
    """日线。默认 ``bar_time`` = 收盘时刻。

    ``bar_time`` 可以显式传 00:00 —— 用来复现"上游把日线时间戳写成当日零点"的泄漏陷阱：
    可见性必须由**交易日的收盘时刻**决定，而不是由 bar_time 决定。
    """
    return DailyBar(
        bar_time=bar_time or close_time(day),
        open=open_ if open_ is not None else close,
        high=high if high is not None else close * 1.01,
        low=low if low is not None else close * 0.99,
        close=close,
        volume=volume,
        amount=close * volume,
    )


def minute_bar(day: date, hour: int, minute: int, close: float, *, volume: float = 10_000.0) -> MinuteBar:
    """5 分钟线。``bar_time`` 是该 bar 的**结束**时刻。"""
    return MinuteBar(
        bar_time=at(day, hour, minute),
        open=close,
        high=close * 1.002,
        low=close * 0.998,
        close=close,
        volume=volume,
        amount=close * volume,
    )


def document(published_at: datetime, kind: str = "announcement") -> DocumentRef:
    return DocumentRef(published_at=published_at, document_type=kind)


def price_series(
    sessions: list[date], *, start_price: float = 100.0, step: float = 0.5
) -> list[DailyBar]:
    """一条确定性的价格序列（不随机 —— 测试必须可复算）。"""
    bars: list[DailyBar] = []
    price = start_price
    for i, day in enumerate(sessions):
        # 温和的锯齿走势，保证收益率有正有负、波动不为 0
        price = start_price + step * ((i % 7) - 3) + i * 0.05
        bars.append(daily_bar(day, round(price, 2), volume=1_000_000.0 + (i % 5) * 50_000))
    return bars


def sessions_upto(day: date, count: int) -> list[date]:
    """截止 ``day``（含）的最后 ``count`` 个交易日。"""
    index = TEST_SESSIONS.index(day)
    return TEST_SESSIONS[max(0, index - count + 1) : index + 1]
