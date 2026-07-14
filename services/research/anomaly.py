"""异动检测（spec §12）。

**纯确定性规则，不调用任何 LLM。** 满足任一条件即产生一个异动事件：

1. ``INTRADAY_RETURN_SPIKE``：5 分钟收益绝对值 > 过去 60 日**同时间段**的 99 百分位。
2. ``VOLUME_PACE``：当日成交量进度 > 过去 20 日**同时间进度**均值的 2 倍。
3. ``BENCHMARK_DIVERGENCE``：当日收益与沪深300收益之差的绝对值 > 2 个百分点。
4. ``OPENING_GAP``：开盘缺口绝对值 > 过去 60 日的 95 百分位。

异动分析先给确定性量价事实（``AnomalyEvent.facts_block``），再去检索事件证据；
没有匹配公告/新闻时写固定文案 ``NO_VERIFIABLE_CAUSE_TEXT``（由 ``agents.analyst`` 负责）。

口径（实现里唯一的一处定义，测试逐条覆盖）：

* **5 分钟收益**：同一交易时段内 ``close_t / close_{t-1} - 1``；当日**首根** K 线没有前一根，
  用 ``close / open - 1``（即剔除隔夜跳空，跳空由规则 4 单独负责，避免同一现象被算两次）。
* **同时间段**：按 K 线的"时刻"（``bar_time.time()``，如 10:05）对齐历史交易日的同一根 K 线。
* **百分位**：线性插值（与 numpy 默认的 ``linear`` 一致），对**绝对值**序列取分位。
* **成交量进度**：当日截至 ``as_of`` 的累计成交量；历史同时间进度 = 该日截至**同一时刻**的累计量。
* **当日收益**：最新价（最后一根 5m K 线收盘，无则当日日线收盘）/ 昨收 - 1；沪深300 同法。
* **开盘缺口**：当日开盘价 / 昨收 - 1；历史缺口由相邻两个交易日的日线 ``open_t / close_{t-1} - 1`` 得到。

历史样本不足时该规则**不触发**（记入 ``skipped``，进入分析的 ``unknowns``），
绝不用"样本不足"当成异动信号。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from math import ceil, floor

from apps.api.app.core.clock import SHANGHAI, to_shanghai
from apps.api.app.core.enums import CSI300_BENCHMARK_SYMBOL, Timeframe
from apps.api.app.core.trading_calendar import TradingCalendar
from services.research.agents.repository import BarPoint, ResearchReadRepository

logger = logging.getLogger(__name__)

# ── 阈值常量（spec §12 的四条规则，只在这里定义一次）─────────────────────────
RETURN_LOOKBACK_DAYS = 60
RETURN_PERCENTILE = 99.0
MIN_RETURN_SAMPLES = 20  # 同时间段的历史样本少于此数 → 不评估该规则

VOLUME_LOOKBACK_DAYS = 20
VOLUME_PACE_MULTIPLE = 2.0
MIN_VOLUME_SAMPLES = 10

BENCHMARK_DIVERGENCE_ABS = 0.02  # 2 个百分点

GAP_LOOKBACK_DAYS = 60
GAP_PERCENTILE = 95.0
MIN_GAP_SAMPLES = 20

# 检索事件证据的回溯窗口（异动分析先事实、后证据）
EVIDENCE_LOOKBACK = timedelta(hours=48)


class AnomalyRule(StrEnum):
    INTRADAY_RETURN_SPIKE = "intraday_return_spike"
    VOLUME_PACE = "volume_pace"
    BENCHMARK_DIVERGENCE = "benchmark_divergence"
    OPENING_GAP = "opening_gap"


# 规则的固定中文标签。它同时是**去重键**：同一交易日已存在含该标签的异动分析 → 不重复建。
RULE_LABELS: dict[AnomalyRule, str] = {
    AnomalyRule.INTRADAY_RETURN_SPIKE: "5分钟收益异常",
    AnomalyRule.VOLUME_PACE: "成交量进度异常",
    AnomalyRule.BENCHMARK_DIVERGENCE: "相对沪深300异常",
    AnomalyRule.OPENING_GAP: "开盘缺口异常",
}


def percentile(values: Sequence[float], q: float) -> float:
    """线性插值百分位（与 numpy 默认 ``method="linear"`` 一致）。空序列非法。"""
    if not values:
        raise ValueError("百分位需要非空样本")
    if not 0.0 <= q <= 100.0:
        raise ValueError("百分位 q 必须在 0..100")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (q / 100.0) * (len(ordered) - 1)
    low = floor(pos)
    high = ceil(pos)
    if low == high:
        return ordered[int(pos)]
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def _ret(current: Decimal, base: Decimal) -> float | None:
    """收益率；基数为 0 时返回 None（不制造 inf/NaN 信号）。"""
    if base == 0:
        return None
    return float(current) / float(base) - 1.0


@dataclass(frozen=True, slots=True)
class AnomalySignal:
    """一条触发的规则 + 它的确定性量价事实。"""

    rule: AnomalyRule
    label: str
    observed: float
    threshold: float
    fact: str  # 中文事实句，含数值；直接进 summary 的事实块


@dataclass(frozen=True, slots=True)
class AnomalyEvent:
    symbol: str
    as_of: datetime
    trading_day: date
    signals: tuple[AnomalySignal, ...]
    skipped: tuple[str, ...] = ()  # 因样本不足未评估的规则 → 写入分析的 unknowns

    @property
    def rules(self) -> tuple[AnomalyRule, ...]:
        return tuple(signal.rule for signal in self.signals)

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(signal.label for signal in self.signals)

    @property
    def facts_block(self) -> str:
        """确定性量价事实块。异动分析的 summary **必须以它开头**（spec §12）。"""
        header = f"{self.symbol} 于 {to_shanghai(self.as_of):%Y-%m-%d %H:%M} 触发异动："
        lines = [f"- {signal.fact}" for signal in self.signals]
        return "\n".join([header, *lines])


@dataclass(frozen=True, slots=True)
class AnomalyInput:
    """异动检测的**全部**输入。检测本身是纯函数：同样的输入永远得到同样的事件。"""

    symbol: str
    as_of: datetime
    trading_day: date
    intraday_bars: tuple[BarPoint, ...]  # 当日 5m K线，升序，bar_time <= as_of
    history_bars: tuple[BarPoint, ...]  # 过去 N 个交易日 5m K线，升序，不含当日
    history_daily: tuple[BarPoint, ...]  # 过去 N 个交易日 1d K线，升序，不含当日
    previous_close: Decimal | None  # 昨收（上一交易日日线收盘）
    benchmark_return: float | None  # 沪深300 当日收益
    today_daily: BarPoint | None = None  # 当日日线（收盘后才有）


# ── 规则实现 ────────────────────────────────────────────────────────────────


def _session_bars_by_day(bars: Sequence[BarPoint]) -> dict[date, list[BarPoint]]:
    grouped: dict[date, list[BarPoint]] = {}
    for bar in bars:
        grouped.setdefault(to_shanghai(bar.bar_time).date(), []).append(bar)
    for day_bars in grouped.values():
        day_bars.sort(key=lambda b: b.bar_time)
    return grouped


def five_minute_returns(day_bars: Sequence[BarPoint]) -> list[tuple[time, float]]:
    """一个交易日内每根 5m K线的收益。首根用 ``close/open - 1``（剔除隔夜跳空）。"""
    out: list[tuple[time, float]] = []
    previous_close: Decimal | None = None
    for bar in day_bars:
        base = previous_close if previous_close is not None else bar.open
        value = _ret(bar.close, base)
        if value is not None:
            out.append((to_shanghai(bar.bar_time).time(), value))
        previous_close = bar.close
    return out


def _check_intraday_return_spike(data: AnomalyInput) -> tuple[AnomalySignal | None, str | None]:
    if not data.intraday_bars:
        return None, None
    today_returns = five_minute_returns(data.intraday_bars)
    if not today_returns:
        return None, None
    slot, current = today_returns[-1]

    history = _session_bars_by_day(data.history_bars)
    samples: list[float] = []
    for day_bars in history.values():
        for bar_slot, value in five_minute_returns(day_bars):
            if bar_slot == slot:
                samples.append(abs(value))
    if len(samples) < MIN_RETURN_SAMPLES:
        return None, (
            f"{RULE_LABELS[AnomalyRule.INTRADAY_RETURN_SPIKE]}：{slot:%H:%M} 同时间段历史样本 "
            f"{len(samples)} 个，少于 {MIN_RETURN_SAMPLES} 个，本次未评估"
        )

    threshold = percentile(samples, RETURN_PERCENTILE)
    if abs(current) <= threshold:
        return None, None
    fact = (
        f"{RULE_LABELS[AnomalyRule.INTRADAY_RETURN_SPIKE]}：{slot:%H:%M} 这 5 分钟收益 "
        f"{current:+.2%}，绝对值超过过去 {RETURN_LOOKBACK_DAYS} 个交易日同时间段的 "
        f"{RETURN_PERCENTILE:g} 百分位（{threshold:.2%}，样本 {len(samples)} 个）"
    )
    return (
        AnomalySignal(
            rule=AnomalyRule.INTRADAY_RETURN_SPIKE,
            label=RULE_LABELS[AnomalyRule.INTRADAY_RETURN_SPIKE],
            observed=abs(current),
            threshold=threshold,
            fact=fact,
        ),
        None,
    )


def _check_volume_pace(data: AnomalyInput) -> tuple[AnomalySignal | None, str | None]:
    if not data.intraday_bars:
        return None, None
    slot = to_shanghai(data.intraday_bars[-1].bar_time).time()
    today_pace = float(sum(bar.volume for bar in data.intraday_bars))

    history = _session_bars_by_day(data.history_bars)
    # 只取最近 VOLUME_LOOKBACK_DAYS 个交易日
    recent_days = sorted(history)[-VOLUME_LOOKBACK_DAYS:]
    samples: list[float] = []
    for day in recent_days:
        pace = float(
            sum(bar.volume for bar in history[day] if to_shanghai(bar.bar_time).time() <= slot)
        )
        if pace > 0:
            samples.append(pace)
    if len(samples) < MIN_VOLUME_SAMPLES:
        return None, (
            f"{RULE_LABELS[AnomalyRule.VOLUME_PACE]}：截至 {slot:%H:%M} 的历史同时间进度样本 "
            f"{len(samples)} 个，少于 {MIN_VOLUME_SAMPLES} 个，本次未评估"
        )

    mean_pace = sum(samples) / len(samples)
    threshold = VOLUME_PACE_MULTIPLE * mean_pace
    if today_pace <= threshold:
        return None, None
    ratio = today_pace / mean_pace
    fact = (
        f"{RULE_LABELS[AnomalyRule.VOLUME_PACE]}：截至 {slot:%H:%M} 累计成交量 {today_pace:,.0f}，"
        f"为过去 {len(samples)} 个交易日同时间进度均值（{mean_pace:,.0f}）的 {ratio:.2f} 倍，"
        f"超过 {VOLUME_PACE_MULTIPLE:g} 倍阈值"
    )
    return (
        AnomalySignal(
            rule=AnomalyRule.VOLUME_PACE,
            label=RULE_LABELS[AnomalyRule.VOLUME_PACE],
            observed=today_pace,
            threshold=threshold,
            fact=fact,
        ),
        None,
    )


def day_return(data: AnomalyInput) -> float | None:
    """当日收益：最新 5m 收盘（无则当日日线收盘）/ 昨收 - 1。"""
    if data.previous_close is None:
        return None
    if data.intraday_bars:
        latest = data.intraday_bars[-1].close
    elif data.today_daily is not None:
        latest = data.today_daily.close
    else:
        return None
    return _ret(latest, data.previous_close)


def _check_benchmark_divergence(data: AnomalyInput) -> tuple[AnomalySignal | None, str | None]:
    stock_return = day_return(data)
    if stock_return is None or data.benchmark_return is None:
        return None, (
            f"{RULE_LABELS[AnomalyRule.BENCHMARK_DIVERGENCE]}："
            f"缺少昨收或沪深300（{CSI300_BENCHMARK_SYMBOL}）当日收益，本次未评估"
        )
    diff = stock_return - data.benchmark_return
    if abs(diff) <= BENCHMARK_DIVERGENCE_ABS:
        return None, None
    fact = (
        f"{RULE_LABELS[AnomalyRule.BENCHMARK_DIVERGENCE]}：当日收益 {stock_return:+.2%}，"
        f"沪深300 {data.benchmark_return:+.2%}，相差 {diff:+.2%}，"
        f"绝对值超过 {BENCHMARK_DIVERGENCE_ABS:.0%} 阈值"
    )
    return (
        AnomalySignal(
            rule=AnomalyRule.BENCHMARK_DIVERGENCE,
            label=RULE_LABELS[AnomalyRule.BENCHMARK_DIVERGENCE],
            observed=abs(diff),
            threshold=BENCHMARK_DIVERGENCE_ABS,
            fact=fact,
        ),
        None,
    )


def historical_gaps(daily_bars: Sequence[BarPoint]) -> list[float]:
    """相邻交易日的开盘缺口：``open_t / close_{t-1} - 1``。"""
    gaps: list[float] = []
    for previous, current in pairwise(daily_bars):
        value = _ret(current.open, previous.close)
        if value is not None:
            gaps.append(value)
    return gaps


def _today_open(data: AnomalyInput) -> Decimal | None:
    if data.intraday_bars:
        return data.intraday_bars[0].open
    if data.today_daily is not None:
        return data.today_daily.open
    return None


def _check_opening_gap(data: AnomalyInput) -> tuple[AnomalySignal | None, str | None]:
    today_open = _today_open(data)
    if today_open is None or data.previous_close is None:
        return None, None
    gap = _ret(today_open, data.previous_close)
    if gap is None:
        return None, None

    samples = [abs(value) for value in historical_gaps(data.history_daily)]
    if len(samples) < MIN_GAP_SAMPLES:
        return None, (
            f"{RULE_LABELS[AnomalyRule.OPENING_GAP]}：历史缺口样本 {len(samples)} 个，"
            f"少于 {MIN_GAP_SAMPLES} 个，本次未评估"
        )

    threshold = percentile(samples, GAP_PERCENTILE)
    if abs(gap) <= threshold:
        return None, None
    fact = (
        f"{RULE_LABELS[AnomalyRule.OPENING_GAP]}：开盘缺口 {gap:+.2%}，"
        f"绝对值超过过去 {GAP_LOOKBACK_DAYS} 个交易日的 {GAP_PERCENTILE:g} 百分位"
        f"（{threshold:.2%}，样本 {len(samples)} 个）"
    )
    return (
        AnomalySignal(
            rule=AnomalyRule.OPENING_GAP,
            label=RULE_LABELS[AnomalyRule.OPENING_GAP],
            observed=abs(gap),
            threshold=threshold,
            fact=fact,
        ),
        None,
    )


def detect(data: AnomalyInput) -> AnomalyEvent | None:
    """四条规则逐条判定。任一触发即返回事件；全不触发返回 ``None``。"""
    signals: list[AnomalySignal] = []
    skipped: list[str] = []
    for check in (
        _check_intraday_return_spike,
        _check_volume_pace,
        _check_benchmark_divergence,
        _check_opening_gap,
    ):
        signal, skip_reason = check(data)
        if signal is not None:
            signals.append(signal)
        if skip_reason is not None:
            skipped.append(skip_reason)
    if not signals:
        return None
    return AnomalyEvent(
        symbol=data.symbol,
        as_of=data.as_of,
        trading_day=data.trading_day,
        signals=tuple(signals),
        skipped=tuple(skipped),
    )


# ── 数据装载（唯一数据源：产品数据库；全程 PIT）───────────────────────────────


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=SHANGHAI)


def _split_by_day(bars: Sequence[BarPoint], day: date) -> tuple[list[BarPoint], list[BarPoint]]:
    """按交易日切成（当日, 历史）两段。"""
    today: list[BarPoint] = []
    history: list[BarPoint] = []
    for bar in bars:
        bar_day = to_shanghai(bar.bar_time).date()
        if bar_day == day:
            today.append(bar)
        elif bar_day < day:
            history.append(bar)
    return today, history


async def _load_benchmark_return(
    repo: ResearchReadRepository,
    as_of: datetime,
    trading_day: date,
    start: datetime,
) -> float | None:
    """沪深300 当日收益：最新 5m 收盘（无则日线收盘）/ 昨收 - 1。"""
    minute = await repo.get_bars_in_range(
        CSI300_BENCHMARK_SYMBOL, Timeframe.MIN5, start, as_of, as_of
    )
    daily = await repo.get_bars_in_range(CSI300_BENCHMARK_SYMBOL, Timeframe.DAY1, start, as_of, as_of)
    today_minute, _ = _split_by_day(minute, trading_day)
    today_daily, history_daily = _split_by_day(daily, trading_day)
    if not history_daily:
        return None
    previous_close = history_daily[-1].close
    if today_minute:
        latest = today_minute[-1].close
    elif today_daily:
        latest = today_daily[-1].close
    else:
        return None
    return _ret(latest, previous_close)


async def load_anomaly_input(
    repo: ResearchReadRepository,
    *,
    symbol: str,
    as_of: datetime,
    calendar: TradingCalendar,
) -> AnomalyInput | None:
    """从产品数据库装载检测输入（只读、PIT）。非交易日或当日无数据 → ``None``。"""
    local = to_shanghai(as_of)
    trading_day = local.date()
    if not calendar.is_trading_day(trading_day):
        return None

    lookback = max(RETURN_LOOKBACK_DAYS, VOLUME_LOOKBACK_DAYS, GAP_LOOKBACK_DAYS)
    try:
        first_day = calendar.previous_trading_day(trading_day, lookback)
    except LookupError:
        # 日历覆盖不足：退到日历首日，样本不足的规则会自行跳过
        sessions = calendar.sessions_between(date(1990, 1, 1), trading_day)
        if not sessions:
            return None
        first_day = sessions[0]
    start = _day_start(first_day)

    minute_bars = await repo.get_bars_in_range(symbol, Timeframe.MIN5, start, as_of, as_of)
    daily_bars = await repo.get_bars_in_range(symbol, Timeframe.DAY1, start, as_of, as_of)
    intraday, history_minute = _split_by_day(minute_bars, trading_day)
    today_daily_list, history_daily = _split_by_day(daily_bars, trading_day)

    if not intraday and not today_daily_list:
        return None  # 当日还没有任何行情 → 无从判定异动

    previous_close = history_daily[-1].close if history_daily else None
    benchmark_return = await _load_benchmark_return(repo, as_of, trading_day, start)

    return AnomalyInput(
        symbol=symbol,
        as_of=local,
        trading_day=trading_day,
        intraday_bars=tuple(intraday),
        history_bars=tuple(history_minute),
        history_daily=tuple(history_daily),
        previous_close=previous_close,
        benchmark_return=benchmark_return,
        today_daily=today_daily_list[-1] if today_daily_list else None,
    )


async def detect_for_symbol(
    repo: ResearchReadRepository,
    *,
    symbol: str,
    as_of: datetime,
    calendar: TradingCalendar,
) -> AnomalyEvent | None:
    """装载 + 检测。异动检测全程无 LLM。"""
    data = await load_anomaly_input(repo, symbol=symbol, as_of=as_of, calendar=calendar)
    if data is None:
        return None
    return detect(data)


def uncovered_signals(
    event: AnomalyEvent, existing_summaries: Sequence[str]
) -> tuple[AnomalySignal, ...]:
    """同一交易日已记录过的规则不重复建分析（作业幂等，spec §14.2）。

    去重键是规则的固定中文标签：异动分析的事实块由本模块确定性生成，标签必然出现在 summary 中。
    """
    covered = {
        signal.label
        for signal in event.signals
        if any(signal.label in summary for summary in existing_summaries)
    }
    return tuple(signal for signal in event.signals if signal.label not in covered)


def session_bounds(day: date) -> tuple[datetime, datetime]:
    """该交易日的 [00:00, 次日00:00) 边界，用于按日去重查询。"""
    start = _day_start(day)
    return start, start + timedelta(days=1)
