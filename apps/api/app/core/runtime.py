"""运行时单例：时钟与交易日历。

生产代码一律经此获取"现在"和"交易日"，测试通过 ``set_clock`` / ``set_trading_calendar``
注入夹具，从而不依赖运行机器的当前日期（spec §16.1）。
"""

from __future__ import annotations

from datetime import date

from apps.api.app.core.clock import Clock, SystemClock
from apps.api.app.core.trading_calendar import StaticTradingCalendar, TradingCalendar, load_exchange_calendar

# 交易日历加载范围：覆盖 3 年以上历史回补与未来结算窗口（spec §9.3.1）
CALENDAR_START = date(2015, 1, 1)
CALENDAR_END = date(2030, 12, 31)

_clock: Clock = SystemClock()
_calendar: TradingCalendar | None = None


def get_clock() -> Clock:
    return _clock


def set_clock(clock: Clock) -> None:
    global _clock
    _clock = clock


def get_trading_calendar() -> TradingCalendar:
    global _calendar
    if _calendar is None:
        _calendar = load_exchange_calendar(CALENDAR_START, CALENDAR_END)
    return _calendar


def set_trading_calendar(calendar: TradingCalendar) -> None:
    global _calendar
    _calendar = calendar


def reset_runtime() -> None:
    """测试清理钩子。"""
    global _clock, _calendar
    _clock = SystemClock()
    _calendar = None


__all__ = [
    "CALENDAR_END",
    "CALENDAR_START",
    "Clock",
    "StaticTradingCalendar",
    "TradingCalendar",
    "get_clock",
    "get_trading_calendar",
    "reset_runtime",
    "set_clock",
    "set_trading_calendar",
]
