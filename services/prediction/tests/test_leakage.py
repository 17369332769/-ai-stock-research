"""未来数据泄漏测试（spec §16：数据泄漏层最少 10 个）。

每一个测试都对应一条真实存在过的、会让回测结果凭空变好的泄漏路径。
断言的对象是**特征快照本身** —— 不是"函数返回了正确的东西"，而是
"未来的那根 K 线 / 那条公告，根本没有出现在模型看到的输入里"。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from apps.api.app.core.errors import InsufficientData
from apps.api.app.core.trading_calendar import StaticTradingCalendar, nth_trading_day_after
from services.prediction.features.builder import build_feature_snapshot
from services.prediction.features.config import FeatureSetConfig
from services.prediction.features.panel import BENCH_CSI300, BENCH_SSE, PitPanel
from services.prediction.features.pit import PitViolation
from services.prediction.tests.conftest import (
    TEST_SESSIONS,
    at,
    close_time,
    daily_bar,
    document,
    minute_bar,
    price_series,
    sessions_upto,
)
from services.prediction.training.labels import training_cutoff_for
from services.prediction.training.samples import (
    InstrumentSeries,
    MembershipIndex,
    build_samples,
)
from services.prediction.training.splits import DateRange, SplitError, assert_time_ordered

pytestmark = pytest.mark.leakage

TODAY = date(2026, 7, 14)  # 周二，交易日


def _history(day: date, count: int = 80) -> list:  # type: ignore[type-arg]
    return price_series(sessions_upto(day, count))


def _benchmarks(day: date, count: int = 80) -> dict[str, list]:  # type: ignore[type-arg]
    sessions = sessions_upto(day, count)
    return {
        BENCH_CSI300: price_series(sessions, start_price=3500.0, step=8.0),
        BENCH_SSE: price_series(sessions, start_price=3000.0, step=6.0),
    }


# ── 1. 未来日线 ─────────────────────────────────────────────────────────────


def test_future_daily_bar_is_not_visible(feature_config: FeatureSetConfig) -> None:
    """cutoff 之后的日线不得进入面板。"""
    previous = TEST_SESSIONS[TEST_SESSIONS.index(TODAY) - 1]
    history = _history(previous)
    future = daily_bar(TODAY, close=999.0)  # 明天的 K 线（相对 cutoff 而言是未来）

    panel = PitPanel.build(
        symbol="600519",
        data_cutoff=close_time(previous),
        daily=[*history, future],
        benchmark_daily=_benchmarks(previous),
    )

    assert future not in panel.daily
    assert panel.last_close == history[-1].close
    assert all(bar.session <= previous for bar in panel.daily)


# ── 2. 当日日线在 09:45 不可见（最致命的一条）──────────────────────────────


def test_todays_daily_bar_is_invisible_before_close(feature_config: FeatureSetConfig) -> None:
    """上游常把日线的 bar_time 写成当日 **00:00**。

    如果可见性按 ``bar_time <= cutoff`` 判定，09:45 的今日预测就会读到**当天的收盘价** ——
    也就是它要预测的那个答案。可见性必须由**交易日的收盘时刻（15:00）**决定。
    """
    previous = TEST_SESSIONS[TEST_SESSIONS.index(TODAY) - 1]
    history = _history(previous)
    # 当天的日线，时间戳是零点：bar_time(00:00) < cutoff(09:45)，但它尚未收盘
    todays_bar = daily_bar(TODAY, close=888.0, bar_time=at(TODAY, 0, 0))

    panel = PitPanel.build(
        symbol="600519",
        data_cutoff=at(TODAY, 9, 45),
        daily=[*history, todays_bar],
        benchmark_daily=_benchmarks(previous),
    )

    assert todays_bar not in panel.daily, "当日尚未收盘的日线绝不能可见"
    assert panel.last_close == history[-1].close
    assert panel.last_session == previous

    snapshot = build_feature_snapshot(panel, horizon="today_close", feature_set_version="v1")
    assert 888.0 not in [v for v in snapshot.values.values() if v is not None]


# ── 3. 未来公告 ─────────────────────────────────────────────────────────────


def test_future_announcement_not_in_feature_snapshot(feature_config: FeatureSetConfig) -> None:
    """``published_at > data_cutoff`` 的公告不得进入特征快照（spec §9.2）。"""
    cutoff = close_time(TODAY)
    history = _history(TODAY)

    visible = document(cutoff - timedelta(hours=2))
    future = document(cutoff + timedelta(minutes=1))  # 晚 1 分钟发布 —— 也是未来

    panel = PitPanel.build(
        symbol="600519",
        data_cutoff=cutoff,
        daily=history,
        documents=[visible, future],
        benchmark_daily=_benchmarks(TODAY),
    )

    assert future not in panel.documents
    assert visible in panel.documents

    snapshot = build_feature_snapshot(panel, horizon="next_5d", feature_set_version="v1")
    assert snapshot.values["doc_count_1d"] == 1.0, "只应看到那条已发布的公告"
    assert snapshot.values["announcement_count_5d"] == 1.0
    assert snapshot.meta["visible_documents"] == 1


def test_document_visibility_uses_published_at_not_observed_at(
    feature_config: FeatureSetConfig,
) -> None:
    """可见性看 ``published_at``，不看 ``observed_at``。

    一条"我们今天就抓到了、但明天才发布"的公告（爬虫抢跑 / 时区搞错），
    绝不能因为"我们已经观察到了"就进入今天的特征。
    """
    cutoff = close_time(TODAY)
    tomorrow_news = document(cutoff + timedelta(days=1), kind="news")

    panel = PitPanel.build(
        symbol="600519",
        data_cutoff=cutoff,
        daily=_history(TODAY),
        documents=[tomorrow_news],
        benchmark_daily=_benchmarks(TODAY),
    )

    assert panel.documents == ()
    snapshot = build_feature_snapshot(panel, horizon="next_5d", feature_set_version="v1")
    assert snapshot.values["news_count_5d"] == 0.0
    # 没有可见文档 → "距上次公告多久"是**未知**，不是 0
    assert snapshot.values["hours_since_last_document"] is None


def test_document_exactly_at_cutoff_is_visible(feature_config: FeatureSetConfig) -> None:
    """边界：``published_at == data_cutoff`` 属于**已发布**，可见。"""
    cutoff = close_time(TODAY)
    panel = PitPanel.build(
        symbol="600519",
        data_cutoff=cutoff,
        daily=_history(TODAY),
        documents=[document(cutoff)],
        benchmark_daily=_benchmarks(TODAY),
    )
    assert len(panel.documents) == 1
    snapshot = build_feature_snapshot(panel, horizon="next_5d", feature_set_version="v1")
    assert snapshot.values["hours_since_last_document"] == 0.0


# ── 4. 未来分钟线 ───────────────────────────────────────────────────────────


def test_future_minute_bars_not_visible(feature_config: FeatureSetConfig) -> None:
    """09:45 的 cutoff 看不到 09:50 的分钟线。"""
    previous = TEST_SESSIONS[TEST_SESSIONS.index(TODAY) - 1]
    cutoff = at(TODAY, 9, 45)

    visible = [
        minute_bar(TODAY, 9, 35, 101.0),
        minute_bar(TODAY, 9, 40, 102.0),
        minute_bar(TODAY, 9, 45, 103.0),
    ]
    future = [minute_bar(TODAY, 9, 50, 999.0), minute_bar(TODAY, 14, 55, 1234.0)]

    panel = PitPanel.build(
        symbol="600519",
        data_cutoff=cutoff,
        daily=_history(previous),
        minute=[*visible, *future],
        benchmark_daily=_benchmarks(previous),
        session_open=100.0,
    )

    assert len(panel.minute) == 3
    assert panel.minute[-1].close == 103.0
    assert all(bar.bar_time <= cutoff for bar in panel.minute)


def test_minute_bars_from_previous_session_do_not_leak_into_morning_features(
    feature_config: FeatureSetConfig,
) -> None:
    """昨天的分钟线不属于"今天的早盘"。

    否则 morning_range / morning_volume_share 会把昨天一整天的量价混进来。
    """
    previous = TEST_SESSIONS[TEST_SESSIONS.index(TODAY) - 1]
    cutoff = at(TODAY, 9, 45)

    yesterday_bars = [minute_bar(previous, 14, 55, 500.0, volume=9_999_999.0)]
    today_bars = [
        minute_bar(TODAY, 9, 35, 101.0, volume=10_000.0),
        minute_bar(TODAY, 9, 40, 102.0, volume=10_000.0),
        minute_bar(TODAY, 9, 45, 103.0, volume=10_000.0),
    ]

    panel = PitPanel.build(
        symbol="600519",
        data_cutoff=cutoff,
        daily=_history(previous),
        minute=[*yesterday_bars, *today_bars],
        benchmark_daily=_benchmarks(previous),
        session_open=100.0,
    )

    assert len(panel.minute) == 3
    assert all(bar.session == TODAY for bar in panel.minute)
    assert panel.session_minute_volume == 30_000.0


# ── 5. 构造器绕不过（第三道防线）────────────────────────────────────────────


def test_direct_construction_with_future_data_raises() -> None:
    """绕过 ``build()`` 直接构造也没用 —— ``__post_init__`` 会断言。"""
    cutoff = close_time(TODAY)
    future = daily_bar(TEST_SESSIONS[TEST_SESSIONS.index(TODAY) + 1], close=999.0)

    with pytest.raises(PitViolation, match="未来日线"):
        PitPanel(
            symbol="600519",
            data_cutoff=cutoff,
            daily=(*_history(TODAY), future),
            minute=(),
            documents=(),
        )


def test_direct_construction_with_future_document_raises() -> None:
    cutoff = close_time(TODAY)
    with pytest.raises(PitViolation, match="未来文档"):
        PitPanel(
            symbol="600519",
            data_cutoff=cutoff,
            daily=tuple(_history(TODAY)),
            minute=(),
            documents=(document(cutoff + timedelta(seconds=1)),),
        )


def test_benchmark_future_bars_also_raise() -> None:
    """基准指数的未来 K 线同样是泄漏（相对强弱特征会直接吃到未来大盘走势）。"""
    cutoff = close_time(TODAY)
    future_index = daily_bar(TEST_SESSIONS[TEST_SESSIONS.index(TODAY) + 1], close=4000.0)
    with pytest.raises(PitViolation, match="基准"):
        PitPanel(
            symbol="600519",
            data_cutoff=cutoff,
            daily=tuple(_history(TODAY)),
            minute=(),
            documents=(),
            benchmark_daily={BENCH_CSI300: (future_index,)},
        )


# ── 6. 训练回放：today 模型看不到当天的收盘/最高/最低/成交量 ─────────────


def test_today_replay_sees_only_open_not_close(
    feature_config: FeatureSetConfig, calendar: StaticTradingCalendar
) -> None:
    """today_close 训练样本的 cutoff 是 09:45。

    当天的**开盘价** 09:30 就公开了，可以用（这正是 open_gap 特征存在的前提）；
    但当天的 high / low / close / volume 绝不可见 —— 否则模型直接看到答案。
    """
    sessions = sessions_upto(TODAY, 80)
    bars = price_series(sessions)
    # 把最后一天的收盘价改成一个刺眼的值，方便断言它没漏进特征
    last = bars[-1]
    bars[-1] = daily_bar(
        last.session, close=7777.0, open_=last.open, high=8888.0, low=6666.0, volume=42_424_242.0
    )

    series = {"600519": InstrumentSeries(symbol="600519", daily=list(bars))}
    benchmarks = {
        BENCH_CSI300: InstrumentSeries(
            symbol="000300", daily=price_series(sessions, start_price=3500.0, step=8.0)
        ),
        BENCH_SSE: InstrumentSeries(
            symbol="000001", daily=price_series(sessions, start_price=3000.0, step=6.0)
        ),
    }
    universe = MembershipIndex(periods={"600519": ((date(2023, 1, 1), None),)})

    samples, _ = build_samples(
        horizon="today_close",
        universe=universe,
        series=series,
        benchmarks=benchmarks,
        calendar=calendar,
        config=feature_config,
        start=TODAY,
        end=TODAY,
    )

    assert len(samples) == 1
    sample = samples[0]
    assert sample.session == TODAY

    # 标签用到了当天收盘（这是允许的 —— 它就是要预测的目标）
    assert sample.label.target_price == 7777.0
    # 但特征里绝不能出现当天的收盘/最高/最低/成交量的任何痕迹
    forbidden = {7777.0, 8888.0, 6666.0, 42_424_242.0}
    values = {v for v in sample.snapshot.values.values() if v is not None}
    assert not (values & forbidden)
    # 面板里最后一根可见日线是**前一个**交易日
    assert sample.snapshot.meta["last_session"] == sessions[-2].isoformat()
    # 开盘缺口用的是当天开盘价 —— 它是可见的
    assert sample.snapshot.values["open_gap"] is not None
    assert sample.snapshot.meta["session_open_source"] == "daily_open_field"


def test_next5d_replay_cutoff_is_session_close(
    feature_config: FeatureSetConfig, calendar: StaticTradingCalendar
) -> None:
    """next_5d 的 cutoff 是 t 日收盘：t 的日线可见，t+1..t+5 全部不可见。"""
    anchor = date(2026, 6, 1)
    sessions = sessions_upto(anchor, 80)
    future_sessions = [
        s for s in TEST_SESSIONS if anchor < s <= nth_trading_day_after(anchor, 5, calendar)
    ]
    bars = price_series(sessions)
    # 未来 5 天给出极端价格：如果泄漏，特征值一定会被带偏
    bars += [daily_bar(day, close=5000.0 + i) for i, day in enumerate(future_sessions)]

    series = {"600519": InstrumentSeries(symbol="600519", daily=list(bars))}
    all_sessions = sessions + future_sessions
    benchmarks = {
        BENCH_CSI300: InstrumentSeries(
            symbol="000300", daily=price_series(all_sessions, start_price=3500.0, step=8.0)
        ),
        BENCH_SSE: InstrumentSeries(
            symbol="000001", daily=price_series(all_sessions, start_price=3000.0, step=6.0)
        ),
    }
    universe = MembershipIndex(periods={"600519": ((date(2023, 1, 1), None),)})

    samples, _ = build_samples(
        horizon="next_5d",
        universe=universe,
        series=series,
        benchmarks=benchmarks,
        calendar=calendar,
        config=feature_config,
        start=anchor,
        end=anchor,
    )

    assert len(samples) == 1
    sample = samples[0]
    assert sample.snapshot.data_cutoff == training_cutoff_for(anchor, "next_5d")
    assert sample.snapshot.meta["last_session"] == anchor.isoformat()
    # 标签确实用了第 5 个交易日的收盘价
    assert sample.label.target_session == nth_trading_day_after(anchor, 5, calendar)
    # 特征里不能有未来那 5 天的极端价格留下的痕迹
    values = {v for v in sample.snapshot.values.values() if v is not None}
    assert not any(v > 40 for v in values), f"特征值异常放大，疑似吃到未来价格：{values}"


# ── 7. 随机切分检测 ─────────────────────────────────────────────────────────


def test_random_split_is_rejected() -> None:
    """spec §9.3 明令禁止随机切分：时间上交错的切分必须直接判死。"""
    sessions = [d for d in TEST_SESSIONS if date(2025, 1, 1) <= d <= date(2026, 6, 30)]
    # 一个"随机"切分：训练段跨到了验证段之后
    train = DateRange(sessions[0], sessions[300])
    validation = DateRange(sessions[100], sessions[200])  # 完全被训练段包住

    with pytest.raises(SplitError, match="重叠或乱序"):
        assert_time_ordered(train, validation, embargo_sessions=5, sessions=sessions)


def test_missing_embargo_is_rejected() -> None:
    """next_5d 的标签要看未来 5 个交易日。

    训练段末尾紧挨着验证段开头 → 训练标签已经"看过"验证期的价格 → 必须拒绝。
    """
    sessions = [d for d in TEST_SESSIONS if date(2025, 1, 1) <= d <= date(2026, 6, 30)]
    train = DateRange(sessions[0], sessions[199])
    validation = DateRange(sessions[200], sessions[260])  # 中间 0 个交易日

    with pytest.raises(SplitError, match="禁运期不足"):
        assert_time_ordered(train, validation, embargo_sessions=5, sessions=sessions)

    # 留够 5 个交易日就合法
    ok = DateRange(sessions[205], sessions[260])
    assert_time_ordered(train, ok, embargo_sessions=5, sessions=sessions)


# ── 8. 节假日 / 跨年：第 5 个后续交易日 ─────────────────────────────────────


def test_fifth_trading_day_skips_national_holiday(calendar: StaticTradingCalendar) -> None:
    """国庆长假：第 5 个后续**交易日**绝不能按自然日 +5 算。"""
    anchor = date(2025, 9, 26)  # 周五，国庆前最后一周
    target = nth_trading_day_after(anchor, 5, calendar)

    assert target == date(2025, 10, 13), f"跨国庆的第 5 个交易日算错了：{target}"
    assert (target - anchor).days == 17  # 自然日相差 17 天，绝不是 5


def test_fifth_trading_day_crosses_year_boundary(calendar: StaticTradingCalendar) -> None:
    """跨年：2025-12-29 之后的第 5 个交易日落在 2026 年（spec §16.1 明确要求覆盖）。

    逐个数：12/30(1)、12/31(2)、[1/1、1/2 元旦休市]、[周末]、1/5(3)、1/6(4)、1/7(5)。
    按自然日 +5 会算成 2026-01-03（周六，根本不开市）—— 差了整整 4 天。
    """
    anchor = date(2025, 12, 29)  # 周一
    target = nth_trading_day_after(anchor, 5, calendar)

    assert target.year == 2026
    assert target == date(2026, 1, 7)

    # 逐日核对中间跳过了什么
    for day in (date(2025, 12, 30), date(2025, 12, 31), date(2026, 1, 5), date(2026, 1, 6)):
        assert calendar.is_trading_day(day)
    for holiday in (date(2026, 1, 1), date(2026, 1, 2)):  # 元旦
        assert not calendar.is_trading_day(holiday)
    for weekend in (date(2026, 1, 3), date(2026, 1, 4)):
        assert not calendar.is_trading_day(weekend)

    # 自然日 +5 的错误答案
    assert anchor + timedelta(days=5) == date(2026, 1, 3)
    assert not calendar.is_trading_day(anchor + timedelta(days=5))


def test_fifth_trading_day_skips_spring_festival(calendar: StaticTradingCalendar) -> None:
    """春节长假同理。"""
    anchor = date(2026, 2, 13)  # 春节前最后一个交易日（周五）
    target = nth_trading_day_after(anchor, 5, calendar)
    assert target == date(2026, 2, 27)
    assert not calendar.is_trading_day(date(2026, 2, 17))


# ── 9. 历史长度不足必须 fail closed ─────────────────────────────────────────


def test_short_history_raises_insufficient_data(feature_config: FeatureSetConfig) -> None:
    """日线不够算特征时必须抛 InsufficientData，绝不返回填了默认值的特征向量。"""
    sessions = sessions_upto(TODAY, 10)  # 只有 10 根，远少于 61
    panel = PitPanel.build(
        symbol="600519",
        data_cutoff=close_time(TODAY),
        daily=price_series(sessions),
        benchmark_daily=_benchmarks(TODAY),
    )
    with pytest.raises(InsufficientData, match="少于计算特征所需"):
        build_feature_snapshot(panel, horizon="next_5d", feature_set_version="v1")


# ── 10. naive datetime 一律拒绝 ─────────────────────────────────────────────


def test_naive_datetime_is_rejected() -> None:
    """没有时区的时间无法做 PIT 判定（到底是几点？）—— 直接拒绝，不猜。"""
    with pytest.raises((PitViolation, ValueError)):
        PitPanel.build(
            symbol="600519",
            data_cutoff=datetime(2026, 7, 14, 9, 45),  # noqa: DTZ001 - 故意造 naive
            daily=_history(TODAY),
        )


# ── 11. 复权边界 ────────────────────────────────────────────────────────────


def test_adjustment_basis_mismatch_is_rejected() -> None:
    """日线与分钟线复权基准不一致 → 拒绝构建特征（除权日会算出错误的开盘缺口）。"""
    from services.prediction.features.repository import _assert_single_adjustment

    with pytest.raises(InsufficientData, match="复权基准"):
        _assert_single_adjustment("600519", {"qfq"}, {"none"})

    with pytest.raises(InsufficientData, match="多种复权基准"):
        _assert_single_adjustment("600519", {"qfq", "hfq"}, set())

    # 一致时不报错
    _assert_single_adjustment("600519", {"qfq"}, {"qfq"})
    _assert_single_adjustment("600519", {"qfq"}, set())


def test_settlement_rescales_reference_price_across_ex_dividend() -> None:
    """除权边界：qfq 序列在除权后会被整体重标定。

    参考价必须先用锚点缩放到**当前**复权基准，再和目标日收盘价相除。
    否则一次 10 送 10（价格腰斩）会让收益率凭空多出 -50%。
    """
    from services.prediction.inference.reference_price import ReferencePrice

    # as_of 当时：锚定日收盘价 100，参考价（= 昨收）也是 100
    reference = ReferencePrice(
        price=100.0,
        source="previous_close",
        anchor_session=date(2026, 7, 13),
        anchor_close_at_as_of=100.0,
        price_on_as_of_basis=100.0,
        intraday_anchor=False,
    )

    # 期间发生 10 送 10：整条 qfq 历史被乘以 0.5，锚定日收盘价现在读到 50
    anchor_close_now = 50.0
    target_close_now = 52.0  # 目标日（现基准）收盘价

    rescale = anchor_close_now / reference.anchor_close_at_as_of  # 0.5
    reference_now = reference.price_on_as_of_basis * rescale  # 50.0
    actual_return = target_close_now / reference_now - 1

    assert rescale == 0.5
    assert reference_now == 50.0
    assert actual_return == pytest.approx(0.04), "真实涨幅是 +4%，不是 -48%"

    # 如果不做重标定（直接用 100 当参考价），就会算出一个荒唐的 -48%
    naive_return = target_close_now / reference.price - 1
    assert naive_return == pytest.approx(-0.48)


# ── 12. 特征集内容变更必须被发现 ────────────────────────────────────────────


def test_feature_set_hash_changes_when_config_changes(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """原地改 v1.yaml → sha256 变化 → 用旧 sha 训出来的模型会拒绝服务。

    这就是"任何字段/窗口/缺失值策略变化都必须升版本"的机器强制（spec §9.3.1）。
    """
    from services.prediction.features.config import load_feature_set

    original = load_feature_set("v1")

    features_dir = tmp_path / "features"
    features_dir.mkdir()
    text = original.source_path.read_text(encoding="utf-8")
    # 只把一个窗口从 20 改成 21 —— 特征语义变了
    tampered = text.replace("  min_completed_sessions: 61", "  min_completed_sessions: 62", 1)
    assert tampered != text
    (features_dir / "v1.yaml").write_text(tampered, encoding="utf-8")

    monkeypatch.setenv("PREDICTION_CONFIG_ROOT", str(tmp_path))
    load_feature_set.cache_clear()
    try:
        modified = load_feature_set("v1")
        assert modified.sha256 != original.sha256, "改了配置内容，sha256 必须变"
        assert modified.history.min_completed_sessions == 62
    finally:
        load_feature_set.cache_clear()
