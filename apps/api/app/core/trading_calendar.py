"""交易日历。

spec §8：所有时间按 Asia/Shanghai 处理，交易日历以交易所日历为准。
spec §16.1：测试必须使用交易日历夹具，不依赖运行机器当前日期 —— 因此这里定义 Protocol，
生产用交易所日历（XSHG），测试注入 ``StaticTradingCalendar``。

"第 5 个后续交易日" 的定义在这里唯一实现（spec §9.1 / 验收 §15.7：节假日不得按自然日错误结算）。
"""

from __future__ import annotations

import bisect
from datetime import date, datetime, time
from enum import StrEnum
from typing import Protocol

from apps.api.app.core.clock import SHANGHAI, to_shanghai


class MarketPhase(StrEnum):
    CLOSED = "closed"  # 非交易日，或交易日的收盘之后
    PRE_OPEN = "pre_open"  # 09:15-09:25 集合竞价前
    CALL_AUCTION = "call_auction"  # 09:25-09:30
    MORNING = "morning"  # 09:30-11:30
    LUNCH_BREAK = "lunch_break"  # 11:30-13:00
    AFTERNOON = "afternoon"  # 13:00-15:00


# 沪深两市连续竞价时段
PRE_OPEN_START = time(9, 15)
CALL_AUCTION_START = time(9, 25)
MORNING_OPEN = time(9, 30)
MORNING_CLOSE = time(11, 30)
AFTERNOON_OPEN = time(13, 0)
AFTERNOON_CLOSE = time(15, 0)

# 今日预测最早生成时间（spec §3.3）
TODAY_PREDICTION_EARLIEST = time(9, 45)


class TradingCalendar(Protocol):
    def is_trading_day(self, day: date) -> bool: ...

    def next_trading_day(self, day: date, n: int = 1) -> date:
        """返回 ``day`` 之后的第 n 个交易日（严格大于 day，不含 day 本身）。"""
        ...

    def previous_trading_day(self, day: date, n: int = 1) -> date:
        """返回 ``day`` 之前的第 n 个交易日（严格小于 day）。"""
        ...

    def sessions_between(self, start: date, end: date) -> list[date]:
        """闭区间 [start, end] 内的全部交易日。"""
        ...


class StaticTradingCalendar:
    """由显式交易日列表构成的日历。

    生产实现（交易所日历）与测试夹具共用同一套语义：给定一个排序好的交易日序列。
    """

    def __init__(self, sessions: list[date]) -> None:
        if not sessions:
            raise ValueError("交易日历不能为空")
        self._sessions = sorted(set(sessions))
        self._index = {day: i for i, day in enumerate(self._sessions)}

    @property
    def first_session(self) -> date:
        return self._sessions[0]

    @property
    def last_session(self) -> date:
        return self._sessions[-1]

    def is_trading_day(self, day: date) -> bool:
        return day in self._index

    def _position_at_or_before(self, day: date) -> int:
        """返回 <= day 的最后一个交易日下标；若 day 早于全部交易日则返回 -1。"""
        return bisect.bisect_right(self._sessions, day) - 1

    def _position_at_or_after(self, day: date) -> int:
        """返回 >= day 的第一个交易日下标；若越界则返回 len。"""
        return bisect.bisect_left(self._sessions, day)

    def next_trading_day(self, day: date, n: int = 1) -> date:
        if n < 1:
            raise ValueError("n 必须 >= 1")
        # 严格大于 day 的第一个交易日
        start = bisect.bisect_right(self._sessions, day)
        target = start + n - 1
        if target >= len(self._sessions):
            raise LookupError(
                f"交易日历覆盖不足：{day} 之后没有第 {n} 个交易日（最后交易日 {self.last_session}）"
            )
        return self._sessions[target]

    def previous_trading_day(self, day: date, n: int = 1) -> date:
        if n < 1:
            raise ValueError("n 必须 >= 1")
        start = bisect.bisect_left(self._sessions, day)  # 严格小于 day 的最后一个 = start-1
        target = start - n
        if target < 0:
            raise LookupError(
                f"交易日历覆盖不足：{day} 之前没有第 {n} 个交易日（首个交易日 {self.first_session}）"
            )
        return self._sessions[target]

    def sessions_between(self, start: date, end: date) -> list[date]:
        if start > end:
            return []
        lo = self._position_at_or_after(start)
        hi = self._position_at_or_before(end)
        return self._sessions[lo : hi + 1]


def load_exchange_calendar(start: date, end: date) -> StaticTradingCalendar:
    """从交易所日历（XSHG，上交所）加载真实交易日。

    只在生产路径调用；测试一律使用 StaticTradingCalendar 夹具（spec §16.1）。

    ``exchange_calendars`` 的节假日数据有版本边界；直接传入超出边界的未来日期会让
    整个 worker 启动失败。先加载库实际提供的区间，再裁剪到应用请求范围。
    """
    import exchange_calendars as xcals

    xshg = xcals.get_calendar("XSHG")
    available_sessions = [ts.date() for ts in xshg.sessions]
    sessions = [session for session in available_sessions if start <= session <= end]
    return StaticTradingCalendar(sessions)


def market_phase(moment: datetime, calendar: TradingCalendar) -> MarketPhase:
    """判定该时刻的市场阶段。用于前端"休市"状态与采集作业调度（spec §8 / §13.2）。"""
    local = to_shanghai(moment)
    if not calendar.is_trading_day(local.date()):
        return MarketPhase.CLOSED
    t = local.time()
    if t < PRE_OPEN_START:
        return MarketPhase.CLOSED
    if t < CALL_AUCTION_START:
        return MarketPhase.PRE_OPEN
    if t < MORNING_OPEN:
        return MarketPhase.CALL_AUCTION
    if t < MORNING_CLOSE:
        return MarketPhase.MORNING
    if t < AFTERNOON_OPEN:
        return MarketPhase.LUNCH_BREAK
    if t < AFTERNOON_CLOSE:
        return MarketPhase.AFTERNOON
    return MarketPhase.CLOSED


def is_market_open(moment: datetime, calendar: TradingCalendar) -> bool:
    return market_phase(moment, calendar) in (MarketPhase.MORNING, MarketPhase.AFTERNOON)


def today_prediction_allowed(moment: datetime, calendar: TradingCalendar) -> bool:
    """今日预测最早在交易日 09:45 生成（spec §3.3，验收 §15.6：09:45 前不可用）。"""
    local = to_shanghai(moment)
    if not calendar.is_trading_day(local.date()):
        return False
    return local.time() >= TODAY_PREDICTION_EARLIEST


def nth_trading_day_after(day: date, n: int, calendar: TradingCalendar) -> date:
    """第 n 个后续交易日。next_5d 的目标日 = nth_trading_day_after(as_of_day, 5)。"""
    return calendar.next_trading_day(day, n)


def session_close_at(day: date) -> datetime:
    """该交易日的收盘时刻（15:00，Asia/Shanghai）。用于 predictions.target_at。"""
    return datetime.combine(day, AFTERNOON_CLOSE, tzinfo=SHANGHAI)
