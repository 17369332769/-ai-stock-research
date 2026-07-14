"""可注入时钟。

spec §16.1 要求测试用固定时间覆盖 09:44 / 09:45 / 11:30 / 13:00 / 15:00 / 节假日 /
跨年第 5 个交易日，因此生产代码一律不得直接调用 ``datetime.now()``；
所有"现在"都必须经过 ``Clock``。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


class Clock(Protocol):
    def now(self) -> datetime:
        """返回带时区的当前时间（Asia/Shanghai）。"""
        ...


class SystemClock:
    """生产时钟。"""

    def now(self) -> datetime:
        return datetime.now(tz=SHANGHAI)


class FixedClock:
    """测试时钟：时间固定，可手动推进。"""

    def __init__(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            raise ValueError("FixedClock 需要带时区的 datetime")
        self._moment = moment.astimezone(SHANGHAI)

    def now(self) -> datetime:
        return self._moment

    def set(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            raise ValueError("FixedClock 需要带时区的 datetime")
        self._moment = moment.astimezone(SHANGHAI)

    def advance(self, delta: timedelta) -> None:
        self._moment += delta


def to_shanghai(moment: datetime) -> datetime:
    """把任意带时区时间转换到上海时区；拒绝 naive datetime（spec §8：所有时间按 Asia/Shanghai 处理）。"""
    if moment.tzinfo is None:
        raise ValueError("拒绝 naive datetime：所有时间必须带时区")
    return moment.astimezone(SHANGHAI)


def trading_day_of(moment: datetime) -> date:
    """返回该时刻所属的自然日（上海时区）。是否为交易日由 TradingCalendar 判定。"""
    return to_shanghai(moment).date()
