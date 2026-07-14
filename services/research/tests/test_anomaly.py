"""异动检测测试（spec §12）—— 四条规则各自的边界。

每条规则都测三点：**刚好等于阈值不触发**（严格大于）、**超过阈值触发**、**样本不足不评估**。
所有用例都构造成"只让被测规则有触发机会"，避免规则之间互相污染。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from apps.api.app.core.clock import SHANGHAI
from apps.api.app.core.trading_calendar import StaticTradingCalendar
from services.research.anomaly import (
    BENCHMARK_DIVERGENCE_ABS,
    MIN_GAP_SAMPLES,
    MIN_RETURN_SAMPLES,
    MIN_VOLUME_SAMPLES,
    VOLUME_PACE_MULTIPLE,
    AnomalyInput,
    AnomalyRule,
    detect,
    detect_for_symbol,
    five_minute_returns,
    historical_gaps,
    load_anomaly_input,
    percentile,
    session_bounds,
    uncovered_signals,
)
from services.research.tests.conftest import (
    AS_OF,
    SYMBOL,
    FakeRepository,
    bar,
    daily_bar,
    trading_days,
)

TODAY = AS_OF.date()
SLOT_OPEN = time(9, 35)
SLOT_NOW = time(10, 5)  # 与 AS_OF 一致


def _history_days(count: int) -> list[date]:
    days = trading_days(TODAY, count + 1)
    return [day for day in days if day < TODAY][-count:]


def make_input(
    *,
    history_days: int = 25,
    history_return: float = 0.002,
    history_volume: float = 500.0,
    today_open: float = 100.0,
    today_close: float = 100.2,
    today_volumes: tuple[float, float] = (500.0, 500.0),
    previous_close: float = 100.0,
    benchmark_return: float | None = None,
    daily_days: int = 25,
    daily_open: float = 101.0,
    daily_close: float = 100.0,
) -> AnomalyInput:
    """构造一份可控的异动输入。默认参数下**四条规则都不触发**。"""
    history_bars = []
    for day in _history_days(history_days):
        history_bars.append(bar(day, SLOT_OPEN, close=100.0, volume=history_volume))
        history_bars.append(
            bar(day, SLOT_NOW, close=100.0 * (1 + history_return), volume=history_volume)
        )

    intraday = [
        bar(TODAY, SLOT_OPEN, open_=today_open, close=today_open, volume=today_volumes[0]),
        bar(TODAY, SLOT_NOW, close=today_close, volume=today_volumes[1]),
    ]
    history_daily = [
        daily_bar(day, open_=daily_open, close=daily_close) for day in _history_days(daily_days)
    ]

    if benchmark_return is None:
        benchmark_return = today_close / previous_close - 1.0  # 默认与个股同步 → 规则 3 不触发

    return AnomalyInput(
        symbol=SYMBOL,
        as_of=AS_OF,
        trading_day=TODAY,
        intraday_bars=tuple(intraday),
        history_bars=tuple(history_bars),
        history_daily=tuple(history_daily),
        previous_close=Decimal(str(previous_close)),
        benchmark_return=benchmark_return,
    )


def _rules(data: AnomalyInput) -> set[AnomalyRule]:
    event = detect(data)
    return set(event.rules) if event else set()


# ── 基线：默认输入不产生任何异动 ────────────────────────────────────────────


def test_baseline_produces_no_event() -> None:
    assert detect(make_input()) is None


# ── 规则 1：5 分钟收益 > 过去 60 日同时间段的 99 百分位 ─────────────────────


def test_rule1_at_threshold_does_not_trigger() -> None:
    """今日 5 分钟收益与历史同时间段完全一致 → 恰好等于 99 百分位 → 不触发（严格大于）。"""
    data = make_input(history_return=0.002, today_close=100.0 * 1.002)
    assert AnomalyRule.INTRADAY_RETURN_SPIKE not in _rules(data)


def test_rule1_above_threshold_triggers() -> None:
    data = make_input(history_return=0.002, today_close=100.5, benchmark_return=0.005)
    event = detect(data)
    assert event is not None
    assert AnomalyRule.INTRADAY_RETURN_SPIKE in event.rules
    signal = next(s for s in event.signals if s.rule is AnomalyRule.INTRADAY_RETURN_SPIKE)
    assert signal.observed > signal.threshold
    assert "10:05" in signal.fact  # 事实句必须写明是哪一个时间段


def test_rule1_negative_return_uses_absolute_value() -> None:
    data = make_input(history_return=0.002, today_close=99.5, benchmark_return=-0.005)
    assert AnomalyRule.INTRADAY_RETURN_SPIKE in _rules(data)


def test_rule1_insufficient_samples_is_skipped_not_triggered() -> None:
    """同时间段样本 < 20 → 不评估。样本不足绝不能当成异动信号。"""
    data = make_input(
        history_days=MIN_RETURN_SAMPLES - 1,
        today_close=130.0,  # 巨幅上涨
        benchmark_return=0.30,
        daily_days=MIN_GAP_SAMPLES,  # 让缺口规则也不足样本，隔离干扰
    )
    event = detect(data)
    assert event is None or AnomalyRule.INTRADAY_RETURN_SPIKE not in event.rules


def test_rule1_first_bar_of_session_excludes_overnight_gap() -> None:
    """当日首根 K 线用 close/open-1，隔夜跳空交给规则 4，避免同一现象被算两次。"""
    day = TODAY
    bars = [bar(day, SLOT_OPEN, open_=100.0, close=101.0)]
    returns = five_minute_returns(bars)
    assert returns[0][0] == SLOT_OPEN
    assert returns[0][1] == pytest.approx(0.01)


# ── 规则 2：当日成交量进度 > 过去 20 日同时间进度均值的 2 倍 ────────────────


def test_rule2_at_threshold_does_not_trigger() -> None:
    """今日进度 = 均值 × 2.0 → 不触发（必须严格超过）。"""
    data = make_input(
        history_days=15,  # < 20 → 规则 1 不评估，隔离干扰
        history_volume=500.0,  # 历史同时间进度 = 1000
        today_volumes=(1000.0, 1000.0),  # 今日进度 = 2000 = 2 × 1000
    )
    assert AnomalyRule.VOLUME_PACE not in _rules(data)


def test_rule2_above_threshold_triggers() -> None:
    data = make_input(
        history_days=15,
        history_volume=500.0,
        today_volumes=(1000.0, 1001.0),  # 2001 > 2000
    )
    event = detect(data)
    assert event is not None
    assert AnomalyRule.VOLUME_PACE in event.rules
    signal = next(s for s in event.signals if s.rule is AnomalyRule.VOLUME_PACE)
    assert signal.observed > signal.threshold
    assert f"{VOLUME_PACE_MULTIPLE:g} 倍" in signal.fact


def test_rule2_insufficient_samples_is_skipped() -> None:
    data = make_input(
        history_days=MIN_VOLUME_SAMPLES - 1,
        history_volume=500.0,
        today_volumes=(100_000.0, 100_000.0),  # 天量
        daily_days=MIN_GAP_SAMPLES,
    )
    event = detect(data)
    assert event is None or AnomalyRule.VOLUME_PACE not in event.rules


def test_rule2_uses_same_time_progress_not_full_day() -> None:
    """历史进度只累计到"同一时刻"为止：14:55 的历史成交量不得进入 10:05 的对照。"""
    history_bars = []
    for day in _history_days(15):
        history_bars.append(bar(day, SLOT_OPEN, close=100.0, volume=500.0))
        history_bars.append(bar(day, SLOT_NOW, close=100.2, volume=500.0))
        history_bars.append(bar(day, time(14, 55), close=100.2, volume=100_000.0))  # 尾盘天量
    data = AnomalyInput(
        symbol=SYMBOL,
        as_of=AS_OF,
        trading_day=TODAY,
        intraday_bars=(
            bar(TODAY, SLOT_OPEN, close=100.0, volume=1000.0),
            bar(TODAY, SLOT_NOW, close=100.2, volume=1001.0),
        ),
        history_bars=tuple(history_bars),
        history_daily=(),
        previous_close=Decimal("100.0"),
        benchmark_return=0.002,
    )
    # 若把尾盘天量算进均值，2001 远低于阈值；只算到 10:05 才会触发
    assert AnomalyRule.VOLUME_PACE in _rules(data)


# ── 规则 3：当日收益与沪深300收益之差 > 2 个百分点 ──────────────────────────


def test_rule3_above_threshold_triggers() -> None:
    data = make_input(history_days=5, daily_days=5, today_close=103.0, benchmark_return=0.005)
    event = detect(data)
    assert event is not None
    assert AnomalyRule.BENCHMARK_DIVERGENCE in event.rules
    signal = next(s for s in event.signals if s.rule is AnomalyRule.BENCHMARK_DIVERGENCE)
    assert signal.observed > BENCHMARK_DIVERGENCE_ABS
    assert "沪深300" in signal.fact


def test_rule3_just_below_threshold_does_not_trigger() -> None:
    # 个股 +3.0%，基准 +1.01% → 相差 1.99% < 2 个百分点
    data = make_input(history_days=5, daily_days=5, today_close=103.0, benchmark_return=0.0101)
    assert detect(data) is None


def test_rule3_negative_divergence_triggers() -> None:
    data = make_input(history_days=5, daily_days=5, today_close=97.0, benchmark_return=0.005)
    assert AnomalyRule.BENCHMARK_DIVERGENCE in _rules(data)


def test_rule3_missing_benchmark_is_skipped() -> None:
    data = make_input(history_days=5, daily_days=5, today_close=103.0, benchmark_return=None)
    data = AnomalyInput(
        symbol=data.symbol,
        as_of=data.as_of,
        trading_day=data.trading_day,
        intraday_bars=data.intraday_bars,
        history_bars=data.history_bars,
        history_daily=data.history_daily,
        previous_close=data.previous_close,
        benchmark_return=None,  # 沪深300 数据缺失
    )
    event = detect(data)
    assert event is None  # 缺基准 → 不评估该规则，而不是"当成异动"


# ── 规则 4：开盘缺口 > 过去 60 日的 95 百分位 ───────────────────────────────


def test_rule4_at_threshold_does_not_trigger() -> None:
    """今日缺口与历史缺口完全一致 → 恰好等于 95 百分位 → 不触发。"""
    data = make_input(
        history_days=5,  # 隔离规则 1
        today_open=101.0,  # 缺口 = 101/100 - 1，与历史构造完全相同
        today_close=101.0,
        previous_close=100.0,
        benchmark_return=0.01,
        daily_days=25,
        daily_open=101.0,
        daily_close=100.0,
    )
    assert AnomalyRule.OPENING_GAP not in _rules(data)


def test_rule4_above_threshold_triggers() -> None:
    data = make_input(
        history_days=5,
        today_open=103.0,
        today_close=103.0,
        previous_close=100.0,
        benchmark_return=0.03,  # 让规则 3 不触发
        daily_days=25,
        daily_open=101.0,
        daily_close=100.0,
    )
    event = detect(data)
    assert event is not None
    assert AnomalyRule.OPENING_GAP in event.rules
    signal = next(s for s in event.signals if s.rule is AnomalyRule.OPENING_GAP)
    assert signal.observed > signal.threshold
    assert "开盘缺口" in signal.fact


def test_rule4_insufficient_samples_is_skipped() -> None:
    data = make_input(
        history_days=5,
        today_open=130.0,
        today_close=130.0,
        previous_close=100.0,
        benchmark_return=0.30,
        daily_days=MIN_GAP_SAMPLES - 5,  # 缺口样本不足
    )
    event = detect(data)
    assert event is None or AnomalyRule.OPENING_GAP not in event.rules


def test_historical_gaps_uses_open_over_previous_close() -> None:
    bars = [
        daily_bar(date(2026, 7, 10), open_=100.0, close=100.0),
        daily_bar(date(2026, 7, 13), open_=102.0, close=101.0),
    ]
    gaps = historical_gaps(bars)
    assert gaps == [pytest.approx(0.02)]


# ── 百分位与事实块 ──────────────────────────────────────────────────────────


def test_percentile_linear_interpolation() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0], 50.0) == pytest.approx(2.5)
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.0) == pytest.approx(1.0)
    assert percentile([1.0, 2.0, 3.0, 4.0], 100.0) == pytest.approx(4.0)
    assert percentile([5.0], 99.0) == pytest.approx(5.0)


def test_percentile_rejects_empty() -> None:
    with pytest.raises(ValueError):
        percentile([], 99.0)


def test_facts_block_lists_every_triggered_rule() -> None:
    data = make_input(
        history_return=0.002,
        today_close=105.0,
        today_volumes=(5000.0, 5000.0),
        benchmark_return=0.0,
    )
    event = detect(data)
    assert event is not None
    block = event.facts_block
    for signal in event.signals:
        assert signal.fact in block
    assert block.startswith(SYMBOL)


def test_multiple_rules_can_trigger_together() -> None:
    data = make_input(
        history_return=0.002,
        today_close=105.0,
        today_volumes=(5000.0, 5000.0),
        benchmark_return=0.0,
    )
    rules = _rules(data)
    assert AnomalyRule.INTRADAY_RETURN_SPIKE in rules
    assert AnomalyRule.VOLUME_PACE in rules
    assert AnomalyRule.BENCHMARK_DIVERGENCE in rules


# ── 幂等去重 ────────────────────────────────────────────────────────────────


def test_uncovered_signals_filters_already_recorded_rules() -> None:
    data = make_input(history_return=0.002, today_close=105.0, benchmark_return=0.0)
    event = detect(data)
    assert event is not None
    first = next(s for s in event.signals)
    remaining = uncovered_signals(event, [f"……{first.label}……"])
    assert first.rule not in {s.rule for s in remaining}
    assert len(remaining) == len(event.signals) - 1


def test_session_bounds_covers_whole_trading_day() -> None:
    start, end = session_bounds(TODAY)
    assert start == datetime(2026, 7, 14, 0, 0, tzinfo=SHANGHAI)
    assert end - start == timedelta(days=1)


# ── 装载：PIT + 非交易日 ────────────────────────────────────────────────────


async def test_load_anomaly_input_excludes_future_bars(calendar: StaticTradingCalendar) -> None:
    repo = FakeRepository()
    repo.bars[(SYMBOL, "5m")] = [
        bar(TODAY, SLOT_OPEN, close=100.0),
        bar(TODAY, SLOT_NOW, close=100.2),
        bar(TODAY, time(10, 10), close=120.0),  # as_of 之后的未来 K 线
    ]
    repo.bars[(SYMBOL, "1d")] = [
        daily_bar(day, open_=100.0, close=100.0) for day in _history_days(25)
    ]
    data = await load_anomaly_input(repo, symbol=SYMBOL, as_of=AS_OF, calendar=calendar)
    assert data is not None
    assert [b.bar_time.time() for b in data.intraday_bars] == [SLOT_OPEN, SLOT_NOW]
    assert data.previous_close == Decimal("100.0")


async def test_detect_for_symbol_returns_none_on_non_trading_day() -> None:
    calendar = StaticTradingCalendar([date(2026, 7, 13)])  # 只有 7/13 是交易日
    repo = FakeRepository()
    event = await detect_for_symbol(repo, symbol=SYMBOL, as_of=AS_OF, calendar=calendar)
    assert event is None


async def test_detect_for_symbol_returns_none_without_today_bars(
    calendar: StaticTradingCalendar,
) -> None:
    repo = FakeRepository()
    repo.bars[(SYMBOL, "5m")] = [bar(day, SLOT_NOW, close=100.0) for day in _history_days(25)]
    event = await detect_for_symbol(repo, symbol=SYMBOL, as_of=AS_OF, calendar=calendar)
    assert event is None
