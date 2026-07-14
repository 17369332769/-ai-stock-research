"""特征计算与特征集契约（spec §9.2 / §9.3.1）。"""

from __future__ import annotations

from datetime import date

import pytest

from apps.api.app.core.errors import InsufficientData
from services.prediction.features.builder import build_feature_snapshot, ensure_horizon_enabled
from services.prediction.features.computers import implemented_feature_names
from services.prediction.features.config import FeatureSetConfig, FeatureSetError, load_feature_set
from services.prediction.features.panel import BENCH_CSI300, BENCH_SSE, PitPanel
from services.prediction.tests.conftest import (
    TEST_SESSIONS,
    at,
    close_time,
    daily_bar,
    minute_bar,
    price_series,
    sessions_upto,
)

TODAY = date(2026, 7, 14)


def _panel(day: date = TODAY, count: int = 80, **kwargs) -> PitPanel:  # type: ignore[no-untyped-def]
    sessions = sessions_upto(day, count)
    return PitPanel.build(
        symbol="600519",
        data_cutoff=kwargs.pop("cutoff", close_time(day)),
        daily=kwargs.pop("daily", price_series(sessions)),
        benchmark_daily={
            BENCH_CSI300: price_series(sessions, start_price=3500.0, step=8.0),
            BENCH_SSE: price_series(sessions, start_price=3000.0, step=6.0),
        },
        **kwargs,
    )


# ── 契约 ────────────────────────────────────────────────────────────────────


def test_yaml_and_code_are_in_sync(feature_config: FeatureSetConfig) -> None:
    """yaml 声明的特征与代码实现的特征必须**双向**一一对应。"""
    feature_config.validate_against_registry(implemented_feature_names())
    assert set(feature_config.names) == set(implemented_feature_names())


def test_registry_drift_is_detected(feature_config: FeatureSetConfig) -> None:
    """代码里多出一个 yaml 没声明的特征 → 报错（禁止静默漂移）。"""
    with pytest.raises(FeatureSetError, match="代码实现但 yaml 未声明"):
        feature_config.validate_against_registry([*implemented_feature_names(), "secret_alpha"])

    with pytest.raises(FeatureSetError, match="yaml 声明但代码未实现"):
        feature_config.validate_against_registry(
            [n for n in implemented_feature_names() if n != "ret_1"]
        )


def test_horizon_scopes(feature_config: FeatureSetConfig) -> None:
    """今日模型 = base + today；一周模型只有 base（spec §9.2「今日模型专用」）。"""
    today = set(feature_config.names_for_horizon("today_close"))
    weekly = set(feature_config.names_for_horizon("next_5d"))

    assert weekly < today
    assert today - weekly == {
        "open_gap",
        "ret_since_0945",
        "morning_range",
        "morning_volume_share",
    }
    assert "ret_60" in weekly and "ret_60" in today


def test_feature_set_sha_is_stable(feature_config: FeatureSetConfig) -> None:
    load_feature_set.cache_clear()
    again = load_feature_set("v1")
    assert again.sha256 == feature_config.sha256
    assert len(feature_config.sha256) == 64


# ── 数值 ────────────────────────────────────────────────────────────────────


def test_momentum_and_trend_values(feature_config: FeatureSetConfig) -> None:
    """用一条手写序列核对动量与均线距离的定义。"""
    sessions = sessions_upto(TODAY, 70)
    closes = [100.0] * 69 + [110.0]
    bars = [daily_bar(day, close) for day, close in zip(sessions, closes, strict=True)]

    panel = _panel(daily=bars)
    snapshot = build_feature_snapshot(panel, horizon="next_5d", feature_set_version="v1")

    # ret_1 = 110/100 - 1
    assert snapshot.values["ret_1"] == pytest.approx(0.1)
    assert snapshot.values["ret_5"] == pytest.approx(0.1)
    # ma_dist_5：5 期均线 = (100*4 + 110)/5 = 102
    assert snapshot.values["ma_dist_5"] == pytest.approx(110 / 102 - 1)


def test_volume_features(feature_config: FeatureSetConfig) -> None:
    sessions = sessions_upto(TODAY, 70)
    bars = [daily_bar(day, 100.0, volume=1_000.0) for day in sessions[:-1]]
    bars.append(daily_bar(sessions[-1], 100.0, volume=3_000.0))

    snapshot = build_feature_snapshot(
        _panel(daily=bars), horizon="next_5d", feature_set_version="v1"
    )

    # volume_rel_ma20：最新 3000 / 最近 20 期均值 (19*1000 + 3000)/20 = 1100
    assert snapshot.values["volume_rel_ma20"] == pytest.approx(3000 / 1100)
    # volume_ratio：最新 3000 / 之前 5 期均值 1000
    assert snapshot.values["volume_ratio"] == pytest.approx(3.0)


def test_turnover_rate_is_missing_without_share_count(feature_config: FeatureSetConfig) -> None:
    """当前数据口径没有股本字段 → 换手率恒缺失，如实记录为 optional_missing（不是 0）。"""
    snapshot = build_feature_snapshot(_panel(), horizon="next_5d", feature_set_version="v1")

    assert snapshot.values["turnover_rate"] is None
    assert "turnover_rate" in snapshot.meta["optional_missing"]
    # required=false → 不算"必填缺失"，因此不触发降级
    assert not any(d.reason == "required_features_missing" for d in snapshot.degradations)


def test_no_documents_is_not_a_degradation(feature_config: FeatureSetConfig) -> None:
    """一只票 90 天没有公告/新闻是**正常状态**，不是数据降级。

    若把它当"必填特征缺失"，几乎每只票都会被压到 low 置信度 —— 置信度就失去了区分度。
    """
    snapshot = build_feature_snapshot(_panel(), horizon="next_5d", feature_set_version="v1")

    assert snapshot.meta["visible_documents"] == 0
    assert snapshot.values["hours_since_last_document"] is None
    assert snapshot.values["doc_count_5d"] == 0.0  # 条数是真的 0
    assert "hours_since_last_document" in snapshot.meta["optional_missing"]
    assert not snapshot.degraded
    assert not snapshot.forces_low_confidence


def test_turnover_rate_computed_when_shares_known(feature_config: FeatureSetConfig) -> None:
    """未来数据口径补上 free_float_shares 后，同一个特征定义直接生效。"""
    sessions = sessions_upto(TODAY, 70)
    bars = [daily_bar(day, 100.0, volume=2_000.0) for day in sessions]
    panel = _panel(daily=bars, free_float_shares=100_000.0)

    snapshot = build_feature_snapshot(panel, horizon="next_5d", feature_set_version="v1")
    assert snapshot.values["turnover_rate"] == pytest.approx(0.02)


def test_relative_strength_is_own_minus_benchmark(feature_config: FeatureSetConfig) -> None:
    snapshot = build_feature_snapshot(_panel(), horizon="next_5d", feature_set_version="v1")
    for window in (1, 5, 20):
        own = snapshot.values[f"ret_{window}"]
        bench = snapshot.values[f"bench_csi300_ret_{window}"]
        rel = snapshot.values[f"rel_strength_csi300_{window}"]
        assert own is not None and bench is not None and rel is not None
        assert rel == pytest.approx(own - bench)


def test_missing_benchmark_records_degradation(feature_config: FeatureSetConfig) -> None:
    """基准缺失 → 市场类特征全缺 + 记录降级（且强制 low 置信度）。"""
    sessions = sessions_upto(TODAY, 70)
    panel = PitPanel.build(
        symbol="600519",
        data_cutoff=close_time(TODAY),
        daily=price_series(sessions),
        benchmark_daily={},  # 没有基准
    )
    snapshot = build_feature_snapshot(panel, horizon="next_5d", feature_set_version="v1")

    assert snapshot.values["bench_csi300_ret_5"] is None
    assert snapshot.values["rel_strength_csi300_5"] is None
    reasons = {d.reason for d in snapshot.degradations}
    assert "benchmark_unavailable" in reasons
    assert snapshot.forces_low_confidence


# ── 盘中特征与开盘模型降级 ──────────────────────────────────────────────────


def test_intraday_features_at_0945(feature_config: FeatureSetConfig) -> None:
    previous = TEST_SESSIONS[TEST_SESSIONS.index(TODAY) - 1]
    sessions = sessions_upto(previous, 70)
    bars = [daily_bar(day, 100.0, volume=1_000.0) for day in sessions]

    panel = PitPanel.build(
        symbol="600519",
        data_cutoff=at(TODAY, 9, 45),
        daily=bars,
        minute=[
            minute_bar(TODAY, 9, 35, 102.0, volume=100.0),
            minute_bar(TODAY, 9, 40, 103.0, volume=100.0),
            minute_bar(TODAY, 9, 45, 104.0, volume=100.0),
        ],
        benchmark_daily={
            BENCH_CSI300: price_series(sessions, start_price=3500.0, step=8.0),
            BENCH_SSE: price_series(sessions, start_price=3000.0, step=6.0),
        },
        session_open=101.0,
        session_open_source="quote_raw",
    )
    snapshot = build_feature_snapshot(panel, horizon="today_close", feature_set_version="v1")

    # 开盘缺口 = 101 / 100（昨收） - 1
    assert snapshot.values["open_gap"] == pytest.approx(0.01)
    # 09:45 之后的收益：cutoff 恰好是 09:45，锚点就是最新价 → 0
    assert snapshot.values["ret_since_0945"] == pytest.approx(0.0)
    # 早盘区间 = (最高 - 最低) / 昨收
    high = max(b.high for b in panel.minute)
    low = min(b.low for b in panel.minute)
    assert snapshot.values["morning_range"] == pytest.approx((high - low) / 100.0)
    # 早盘成交占比 = 300 / 之前 5 日全日均量 1000
    assert snapshot.values["morning_volume_share"] == pytest.approx(0.3)
    assert not snapshot.degraded


def test_open_model_degradation_when_minute_bars_missing(feature_config: FeatureSetConfig) -> None:
    """分钟线不足 → 退化为开盘模型、标记原因、强制 low 置信度（spec §9.3）。"""
    previous = TEST_SESSIONS[TEST_SESSIONS.index(TODAY) - 1]
    sessions = sessions_upto(previous, 70)

    panel = PitPanel.build(
        symbol="600519",
        data_cutoff=at(TODAY, 9, 45),
        daily=[daily_bar(day, 100.0) for day in sessions],
        minute=[minute_bar(TODAY, 9, 35, 102.0)],  # 只有 1 根，少于要求的 3 根
        benchmark_daily={
            BENCH_CSI300: price_series(sessions, start_price=3500.0, step=8.0),
            BENCH_SSE: price_series(sessions, start_price=3000.0, step=6.0),
        },
        session_open=101.0,
    )
    snapshot = build_feature_snapshot(panel, horizon="today_close", feature_set_version="v1")

    assert snapshot.degraded
    assert snapshot.forces_low_confidence
    reasons = {d.reason for d in snapshot.degradations}
    assert "minute_bars_insufficient" in reasons

    # requires=minute_bars 的特征被丢弃
    assert snapshot.values["ret_since_0945"] is None
    assert snapshot.values["morning_range"] is None
    assert snapshot.values["morning_volume_share"] is None
    # 但开盘缺口仍然可用 —— 这正是"开盘模型"的意义
    assert snapshot.values["open_gap"] == pytest.approx(0.01)


# ── 启用门槛 ────────────────────────────────────────────────────────────────


def test_weekly_model_requires_three_years(feature_config: FeatureSetConfig) -> None:
    """一周模型：日线不足 3 年不启用（spec §9.3）。"""
    panel = _panel(count=200)  # 只有 200 个交易日
    # 特征算得出来（>= 61 根）
    build_feature_snapshot(panel, horizon="next_5d", feature_set_version="v1")
    # 但模型不启用
    with pytest.raises(InsufficientData, match="next_5d 模型要求的 720"):
        ensure_horizon_enabled(panel, horizon="next_5d", feature_set_version="v1")


def test_today_model_requires_120_sessions(feature_config: FeatureSetConfig) -> None:
    panel = _panel(count=100)
    with pytest.raises(InsufficientData, match="today_close 模型要求的 120"):
        ensure_horizon_enabled(panel, horizon="today_close", feature_set_version="v1")

    enough = _panel(count=130)
    ensure_horizon_enabled(enough, horizon="today_close", feature_set_version="v1")


def test_history_sessions_counts_beyond_loaded_window(feature_config: FeatureSetConfig) -> None:
    """训练回放只装 66 根日线，但启用门槛看的是真实历史总数。"""
    sessions = sessions_upto(TODAY, 66)
    panel = PitPanel.build(
        symbol="600519",
        data_cutoff=close_time(TODAY),
        daily=price_series(sessions),
        history_sessions=800,  # 真实历史有 800 个交易日
    )
    assert panel.loaded_sessions == 66
    assert panel.completed_sessions == 800
    ensure_horizon_enabled(panel, horizon="next_5d", feature_set_version="v1")


# ── 快照序列化 ──────────────────────────────────────────────────────────────


def test_snapshot_json_is_json_safe(feature_config: FeatureSetConfig) -> None:
    """features_snapshot 要写进 JSONB —— 里面绝不能有 NaN（asyncpg 会拒绝）。"""
    import json
    import math

    snapshot = build_feature_snapshot(_panel(), horizon="next_5d", feature_set_version="v1")
    payload = snapshot.to_json()

    text = json.dumps(payload, ensure_ascii=False, allow_nan=False)  # allow_nan=False 是关键
    assert "NaN" not in text
    assert payload["values"]["turnover_rate"] is None

    # 但喂给模型时，缺失按策略变成 nan / 0.0
    row = snapshot.to_model_row(feature_config)
    turnover_index = snapshot.names.index("turnover_rate")
    assert math.isnan(row[turnover_index])
    doc_index = snapshot.names.index("doc_count_1d")
    assert row[doc_index] == 0.0  # missing: zero
