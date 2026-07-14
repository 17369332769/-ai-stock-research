"""交易日与市场阶段（spec §8 / §9.1 / 验收 §15.6 §15.7）。

覆盖 spec §16.1 点名的时间：09:44 / 09:45 / 11:30 / 13:00 / 15:00 / 节假日 / 跨年第 5 个交易日。
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from apps.api.app.core.clock import SHANGHAI
from apps.api.app.core.trading_calendar import (
    MarketPhase,
    StaticTradingCalendar,
    is_market_open,
    market_phase,
    nth_trading_day_after,
    session_close_at,
    today_prediction_allowed,
)
from apps.api.tests.conftest import HOLIDAY, TRADING_DAY


def at(hour: int, minute: int, day: date = TRADING_DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SHANGHAI)


def test_trading_day_is_recognized(calendar: StaticTradingCalendar) -> None:
    assert calendar.is_trading_day(TRADING_DAY)


def test_holiday_is_not_a_trading_day(calendar: StaticTradingCalendar) -> None:
    assert not calendar.is_trading_day(HOLIDAY)


def test_weekend_is_not_a_trading_day(calendar: StaticTradingCalendar) -> None:
    assert not calendar.is_trading_day(date(2026, 7, 18))  # 周六


def test_next_trading_day_skips_holiday(calendar: StaticTradingCalendar) -> None:
    # 07-15 是夹具里的节假日 ⇒ 07-14 的下一个交易日是 07-16
    assert calendar.next_trading_day(TRADING_DAY) == date(2026, 7, 16)


def test_next_trading_day_skips_weekend(calendar: StaticTradingCalendar) -> None:
    assert calendar.next_trading_day(date(2026, 7, 17)) == date(2026, 7, 20)


def test_previous_trading_day(calendar: StaticTradingCalendar) -> None:
    assert calendar.previous_trading_day(TRADING_DAY) == date(2026, 7, 13)


def test_fifth_trading_day_after_skips_holiday(calendar: StaticTradingCalendar) -> None:
    """next_5d 的目标日必须是第 5 个**交易日**，不是第 5 个自然日（验收 §15.7）。"""
    target = nth_trading_day_after(TRADING_DAY, 5, calendar)
    # 07-15 节假日、07-18/19 周末 ⇒ 16, 17, 20, 21, 22
    assert target == date(2026, 7, 22)
    assert (target - TRADING_DAY).days == 8  # 自然日相差 8 天，证明没按自然日算


def test_fifth_trading_day_across_year_boundary(calendar: StaticTradingCalendar) -> None:
    """跨年第 5 个交易日：2026-12-28 起，跳过元旦与周末。"""
    target = nth_trading_day_after(date(2026, 12, 28), 5, calendar)
    # 12-29, 12-30, 12-31, (01-01 节假日, 01-02/03 周末), 01-04, 01-05
    assert target == date(2027, 1, 5)
    assert target.year == 2027


def test_sessions_between_is_inclusive(calendar: StaticTradingCalendar) -> None:
    sessions = calendar.sessions_between(date(2026, 7, 13), date(2026, 7, 17))
    assert sessions == [date(2026, 7, 13), date(2026, 7, 14), date(2026, 7, 16), date(2026, 7, 17)]


def test_calendar_coverage_exhausted_raises(calendar: StaticTradingCalendar) -> None:
    with pytest.raises(LookupError):
        calendar.next_trading_day(calendar.last_session, 1)


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (9, 0, MarketPhase.CLOSED),
        (9, 20, MarketPhase.PRE_OPEN),
        (9, 26, MarketPhase.CALL_AUCTION),
        (9, 44, MarketPhase.MORNING),
        (9, 45, MarketPhase.MORNING),
        (11, 29, MarketPhase.MORNING),
        (11, 30, MarketPhase.LUNCH_BREAK),
        (13, 0, MarketPhase.AFTERNOON),
        (14, 59, MarketPhase.AFTERNOON),
        (15, 0, MarketPhase.CLOSED),
        (16, 0, MarketPhase.CLOSED),
    ],
)
def test_market_phase(
    calendar: StaticTradingCalendar, hour: int, minute: int, expected: MarketPhase
) -> None:
    assert market_phase(at(hour, minute), calendar) is expected


def test_market_phase_on_holiday_is_closed(calendar: StaticTradingCalendar) -> None:
    assert market_phase(at(10, 0, HOLIDAY), calendar) is MarketPhase.CLOSED


def test_is_market_open(calendar: StaticTradingCalendar) -> None:
    assert is_market_open(at(10, 0), calendar)
    assert not is_market_open(at(12, 0), calendar)  # 午休
    assert not is_market_open(at(10, 0, HOLIDAY), calendar)


def test_today_prediction_not_allowed_before_0945(calendar: StaticTradingCalendar) -> None:
    """验收 §15.6：今日预测在 09:45 前不可用。"""
    assert not today_prediction_allowed(at(9, 44), calendar)


def test_today_prediction_allowed_at_0945(calendar: StaticTradingCalendar) -> None:
    assert today_prediction_allowed(at(9, 45), calendar)


def test_today_prediction_allowed_after_0945(calendar: StaticTradingCalendar) -> None:
    assert today_prediction_allowed(at(14, 45), calendar)


def test_today_prediction_never_allowed_on_holiday(calendar: StaticTradingCalendar) -> None:
    assert not today_prediction_allowed(at(10, 0, HOLIDAY), calendar)


def test_session_close_at_is_1500_shanghai() -> None:
    close = session_close_at(TRADING_DAY)
    assert close.hour == 15
    assert close.minute == 0
    assert close.utcoffset() is not None
    assert close.tzinfo is SHANGHAI
