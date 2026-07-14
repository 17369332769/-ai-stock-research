"""worker 测试夹具。

spec §16.1：使用可注入 Clock 固定时间 + 测试交易日历夹具，不依赖运行机器当前日期；
测试禁止访问公网（本目录下的测试不发起任何网络或数据库调用）。
"""

from __future__ import annotations

import calendar
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from apps.api.app.core.clock import SHANGHAI, FixedClock
from apps.api.app.core.runtime import reset_runtime, set_clock, set_trading_calendar
from apps.api.app.core.trading_calendar import StaticTradingCalendar
from services.worker.runner import HealthRegistry, JobRunner

# ── 固定日期（不随运行机器变化）──────────────────────────────────────────────────────────
TRADING_DAY: date = date(2026, 7, 14)  # 周二，交易日
WEEKEND: date = date(2026, 7, 18)  # 周六，非交易日
HOLIDAY: date = date(2026, 10, 1)  # 国庆，工作日但非交易日

# 2026 年法定休市日（够测试用：国庆 10/01-10/07、元旦 01/01）
_HOLIDAYS: frozenset[date] = frozenset(
    [date(2026, 1, 1), *[date(2026, 10, day) for day in range(1, 8)]]
)


def _sessions_2026() -> list[date]:
    """测试交易日历：2026 年全部工作日减去 _HOLIDAYS。"""
    days: list[date] = []
    for month in range(1, 13):
        for day in range(1, calendar.monthrange(2026, month)[1] + 1):
            current = date(2026, month, day)
            if current.weekday() >= 5 or current in _HOLIDAYS:
                continue
            days.append(current)
    return days


@pytest.fixture
def trading_calendar() -> StaticTradingCalendar:
    return StaticTradingCalendar(_sessions_2026())


@pytest.fixture
def clock() -> FixedClock:
    """默认停在交易日 2026-07-14 09:45:00（今日预测的第一次触发时刻）。"""
    return FixedClock(datetime.combine(TRADING_DAY, time(9, 45), tzinfo=SHANGHAI))


@pytest.fixture(autouse=True)
def runtime(clock: FixedClock, trading_calendar: StaticTradingCalendar) -> Iterator[None]:
    set_clock(clock)
    set_trading_calendar(trading_calendar)
    yield
    reset_runtime()


class FakeSleep:
    """记录退避时长但不真的等待，让重试测试保持毫秒级（spec §16.1 确定性）。"""

    def __init__(self, clock: FixedClock) -> None:
        self.delays: list[float] = []
        self._clock = clock

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)
        self._clock.advance(timedelta(seconds=seconds))


@pytest.fixture
def fake_sleep(clock: FixedClock) -> FakeSleep:
    return FakeSleep(clock)


@pytest.fixture
def registry(tmp_path: Path) -> HealthRegistry:
    return HealthRegistry(tmp_path / "worker_health.json")


@pytest.fixture
def runner(registry: HealthRegistry, fake_sleep: FakeSleep) -> JobRunner:
    return JobRunner(registry=registry, sleep=fake_sleep)
