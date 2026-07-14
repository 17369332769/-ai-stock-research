"""调度时刻表断言：逐行对照 spec §8。

每个用例把 spec §8 表里的一行文字翻译成"这一天到底在哪些时刻触发"，不给实现留解释空间。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from itertools import pairwise

import pytest

from apps.api.app.core.clock import SHANGHAI
from services.worker.scheduler import (
    JobSpec,
    build_schedule,
    every,
    every_hours_outside,
    fire_times,
)
from services.worker.tests.conftest import TRADING_DAY

SCHEDULE: tuple[JobSpec, ...] = build_schedule()
BY_ID: dict[str, JobSpec] = {spec.job_id: spec for spec in SCHEDULE}


def times_of(job_id: str, day: date = TRADING_DAY) -> list[str]:
    """该作业在 day 当天的全部触发时刻，格式 HH:MM:SS。"""
    return [moment.strftime("%H:%M:%S") for moment in fire_times(BY_ID[job_id].triggers, day)]


def at(hour: int, minute: int, second: int = 0, day: date = TRADING_DAY) -> datetime:
    return datetime.combine(day, time(hour, minute, second), tzinfo=SHANGHAI)


# ── 沪深300成分同步：每日 07:30、18:30 ───────────────────────────────────────────────────
def test_csi300_universe_sync_twice_a_day() -> None:
    assert times_of("csi300_universe_sync") == ["07:30:00", "18:30:00"]


# ── 自选股报价：09:25-11:30、13:00-15:00 每 15 秒 ─────────────────────────────────────────
def test_watchlist_quotes_every_15_seconds_in_two_windows() -> None:
    fires = times_of("watchlist_quotes")

    assert fires[0] == "09:25:00"  # 集合竞价开始
    assert fires[-1] == "15:00:00"  # 收盘
    # 上午 09:25:00→11:30:00 共 501 次，下午 13:00:00→15:00:00 共 481 次
    assert len(fires) == 501 + 481

    assert "09:24:45" not in fires  # 窗口前不采
    assert "09:25:15" in fires
    assert "11:30:00" in fires  # 右端点包含
    assert "11:30:15" not in fires  # 午盘不采
    assert "12:00:00" not in fires
    assert "13:00:00" in fires
    assert "15:00:15" not in fires  # 收盘后不采


def test_watchlist_quotes_step_is_exactly_15_seconds() -> None:
    fires = fire_times(BY_ID["watchlist_quotes"].triggers, TRADING_DAY)
    morning = [f for f in fires if f < at(12, 0)]
    deltas = {(b - a).total_seconds() for a, b in pairwise(morning)}
    assert deltas == {15.0}


# ── 5 分钟K线：09:35-11:30、13:05-15:05 每 60 秒 ──────────────────────────────────────────
def test_minute_bars_every_60_seconds() -> None:
    fires = times_of("minute_bars")

    assert fires[0] == "09:35:00"
    assert fires[-1] == "15:05:00"  # 收盘后 5 分钟补最后一根
    assert len(fires) == 116 + 121
    assert "09:34:00" not in fires
    assert "11:30:00" in fires
    assert "11:31:00" not in fires
    assert "13:04:00" not in fires
    assert "13:05:00" in fires
    assert "15:06:00" not in fires


# ── 日线：15:10、18:00 各一次 ────────────────────────────────────────────────────────────
def test_daily_bars_twice() -> None:
    assert times_of("daily_bars") == ["15:10:00", "18:00:00"]


# ── 公告：交易时段每 5 分钟，其他时段每小时 ──────────────────────────────────────────────
def test_announcements_session_5min_and_hourly_outside() -> None:
    fires = times_of("announcements")

    # 交易时段（09:30-11:30、13:00-15:00）每 5 分钟：25 + 25
    assert "09:30:00" in fires
    assert "09:35:00" in fires
    assert "11:30:00" in fires
    assert "13:00:00" in fires
    assert "15:00:00" in fires
    # 其他时段每小时整点（09:00 在开盘前 → 属于"其他时段"）
    assert "00:00:00" in fires
    assert "09:00:00" in fires
    assert "12:00:00" in fires
    assert "16:00:00" in fires
    assert "23:00:00" in fires
    # 交易时段内的整点不重复触发（10:00 已由 5 分钟档覆盖）
    assert fires.count("10:00:00") == 1
    assert fires.count("14:00:00") == 1
    # 非交易时段不得出现 5 分钟粒度
    assert "12:05:00" not in fires
    assert "16:30:00" not in fires
    assert len(fires) == 50 + 19


# ── 新闻：交易时段每 10 分钟，其他时段每 2 小时 ──────────────────────────────────────────
def test_news_session_10min_and_two_hourly_outside() -> None:
    fires = times_of("news")

    assert "09:30:00" in fires
    assert "09:40:00" in fires
    assert "09:35:00" not in fires  # 10 分钟档，不是 5 分钟
    assert "11:30:00" in fires
    assert "15:00:00" in fires
    # 其他时段每 2 小时：偶数整点
    assert "00:00:00" in fires
    assert "08:00:00" in fires
    assert "12:00:00" in fires
    assert "16:00:00" in fires
    assert "22:00:00" in fires
    assert "17:00:00" not in fires  # 奇数点不触发
    assert fires.count("10:00:00") == 1  # 落在交易时段，只由 10 分钟档触发
    assert len(fires) == 26 + 10


# ── 今日预测：09:45 起每 15 分钟，最后一次 14:45 ─────────────────────────────────────────
def test_today_predictions_from_0945_every_15min_until_1445() -> None:
    fires = times_of("today_predictions")

    assert fires[0] == "09:45:00"
    assert fires[-1] == "14:45:00"
    assert len(fires) == 21
    assert "09:44:00" not in fires  # spec §3.3 / 验收 §15.6：09:45 前不可用
    assert "09:30:00" not in fires
    assert "15:00:00" not in fires  # 最后一次就是 14:45
    moments = fire_times(BY_ID["today_predictions"].triggers, TRADING_DAY)
    assert {(b - a).total_seconds() for a, b in pairwise(moments)} == {900.0}


# ── 一周预测：09:45、11:30、15:20 ────────────────────────────────────────────────────────
def test_next5d_predictions_three_times() -> None:
    assert times_of("next5d_predictions") == ["09:45:00", "11:30:00", "15:20:00"]


# ── 预测结算：15:20，及次日 08:30 补偿 ───────────────────────────────────────────────────
def test_settlement_at_1520_and_next_day_0830() -> None:
    assert times_of("settle_predictions") == ["08:30:00", "15:20:00"]


# ── spec §8 表未给时刻、由功能章节推断的作业 ─────────────────────────────────────────────
def test_inferred_jobs_have_sane_slots() -> None:
    assert times_of("feature_drift") == ["18:30:00"]  # §9.3.1 每日 PSI，日线 18:00 之后
    anomalies = times_of("detect_anomalies")  # §12，交易时段每 5 分钟
    assert anomalies[0] == "09:40:00"
    assert anomalies[-1] == "15:05:00"
    analyses = times_of("refresh_analyses")  # §11，交易时段每 30 分钟 + 收盘后一次
    assert analyses[0] == "09:30:00"
    assert analyses[-1] == "15:30:00"


# ── 交易日守卫覆盖面 ─────────────────────────────────────────────────────────────────────
def test_all_data_jobs_are_trading_day_only_except_backfill_dispatcher() -> None:
    non_gated = {spec.job_id for spec in SCHEDULE if not spec.trading_day_only}
    # 回补由用户添加自选股触发，周末也必须能跑完（spec §3.1）
    assert non_gated == {"backfill_dispatcher"}


def test_every_job_has_triggers_and_unique_id() -> None:
    ids = [spec.job_id for spec in SCHEDULE]
    assert len(ids) == len(set(ids))
    assert all(spec.triggers for spec in SCHEDULE)


# ── 触发器构造器本身的边界 ───────────────────────────────────────────────────────────────
def test_every_rejects_misaligned_step() -> None:
    with pytest.raises(ValueError):
        every(((time(9, 30), time(11, 30)),), timedelta(seconds=7))  # 7 不整除 60
    with pytest.raises(ValueError):
        every(((time(9, 30), time(11, 30)),), timedelta(minutes=7))  # 7 分钟不整除小时
    with pytest.raises(ValueError):
        every(((time(9, 31), time(11, 30)),), timedelta(minutes=5))  # 起点未对齐 5 分钟栅格


def test_every_hours_outside_excludes_session_hours() -> None:
    triggers = every_hours_outside(((time(9, 30), time(11, 30)),), hours=1)
    hours = {moment.hour for moment in fire_times(triggers, TRADING_DAY)}
    assert 10 not in hours  # 10:00 落在交易时段
    assert 11 not in hours
    assert 9 in hours  # 09:00 在开盘前
    assert 12 in hours
