"""AKShare Provider 契约测试：上游列名/类型/脏值（spec §16 Provider 契约 ≥24）。

夹具是脱敏后的 akshare 原始记录（``DataFrame.to_dict("records")`` 形态）。
**不联网、不装 akshare 也能跑** —— transform 是纯函数。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from apps.api.app.core.clock import SHANGHAI
from services.openbb_extensions.akshare_provider.constants import ProviderDataError
from services.openbb_extensions.akshare_provider.transform import (
    normalize_symbol,
    transform_daily,
    transform_minute,
    transform_news,
    transform_spot,
)
from services.openbb_extensions.tests.conftest import load_json

pytestmark = pytest.mark.contract


# ── 行情快照 ────────────────────────────────────────────────────────────────
def test_spot_maps_all_contract_columns(now: datetime) -> None:
    records = load_json("akshare_spot_em_raw.json")
    out = transform_spot(records, ["600519"], now)

    assert len(out) == 1
    item = out[0]
    assert item["symbol"] == "600519"
    assert item["last_price"] == 1215.04
    assert item["prev_close"] == 1211.0  # 昨收 —— today_close 目标的参考价
    assert item["open"] == 1212.0
    assert item["high"] == 1220.0
    assert item["low"] == 1208.0
    assert item["volume"] == 31234.0
    assert item["turnover"] == 3790000000.0
    assert item["volume_ratio"] == 0.98
    # 溯源三件套必须齐（spec §4.2）
    assert item["source"] == "eastmoney_via_akshare"
    assert item["source_url"] == "https://quote.eastmoney.com/sh600519.html"
    assert item["last_timestamp"] == now
    assert item["last_timestamp"].tzinfo is not None


def test_spot_filters_to_requested_symbols(now: datetime) -> None:
    """全市场快照 5000+ 只 —— 只留请求的标的，不把整个市场塞进库。"""
    records = load_json("akshare_spot_em_raw.json")
    out = transform_spot(records, ["600519", "000001"], now)
    assert sorted(item["symbol"] for item in out) == ["000001", "600519"]


def test_spot_empty_upstream_returns_empty_list(now: datetime) -> None:
    assert transform_spot([], ["600519"], now) == []


def test_spot_missing_required_column_fails_closed(now: datetime) -> None:
    """上游删列（这里是「最新价」）→ 必须炸，不能用 0 或 None 填。"""
    records = load_json("akshare_spot_em_missing_column.json")
    with pytest.raises(ProviderDataError, match="最新价"):
        transform_spot(records, ["600519"], now)


def test_spot_type_change_fails_closed(now: datetime) -> None:
    """数值列变成「停牌」这类字符串 → 必须炸。"""
    records = load_json("akshare_spot_em_type_changed.json")
    with pytest.raises(ProviderDataError, match="最新价"):
        transform_spot(records, ["600519"], now)


def test_spot_rejects_naive_observed_at() -> None:
    records = load_json("akshare_spot_em_raw.json")
    with pytest.raises(ProviderDataError, match="时区"):
        transform_spot(records, ["600519"], datetime(2026, 7, 14, 9, 50))  # noqa: DTZ001


# ── 日线 ───────────────────────────────────────────────────────────────────
def test_daily_bar_time_is_session_close() -> None:
    """日线 bar_time 必须落在 15:00：落 00:00 会让当日 09:45 的特征取到当日日线（数据泄漏）。"""
    out = transform_daily(load_json("akshare_hist_daily_raw.json"), "600519")

    assert len(out) == 3
    assert [item["date"].date().isoformat() for item in out] == [
        "2026-07-10",
        "2026-07-13",
        "2026-07-14",
    ]
    for item in out:
        assert item["date"].hour == 15
        assert item["date"].minute == 0
        assert item["date"].tzinfo is not None
        assert item["timeframe"] == "1d"
        assert item["adjustment"] == "qfq"
        assert item["source"] == "eastmoney_via_akshare"


def test_daily_ohlc_column_order_is_not_confused() -> None:
    """上游列序是 开盘/收盘/最高/最低（不是 OHLC！）—— 映射错会静默毁掉所有特征。"""
    out = transform_daily(load_json("akshare_hist_daily_raw.json"), "600519")
    first = out[0]
    assert first["open"] == 1190.0
    assert first["close"] == 1201.0
    assert first["high"] == 1205.5
    assert first["low"] == 1188.0


def test_daily_empty_returns_empty() -> None:
    assert transform_daily([], "600519") == []


def test_daily_missing_column_fails_closed() -> None:
    records = [{"日期": "2026-07-14", "开盘": 1.0, "最高": 2.0, "最低": 0.5, "成交量": 1}]
    with pytest.raises(ProviderDataError, match="收盘"):
        transform_daily(records, "600519")


def test_daily_dirty_ohlc_passes_transform_and_is_caught_downstream() -> None:
    """transform 只管形状；high<open 这类语义脏值由 normalization 层拒收（分层明确）。"""
    out = transform_daily(load_json("akshare_hist_daily_dirty.json"), "600519")
    assert out[0]["high"] < out[0]["open"]  # 形状合法，但语义脏 —— 下游 validate_bar 会拒


# ── 5 分钟线 ───────────────────────────────────────────────────────────────
def test_minute_bar_time_is_bar_end_and_session_bounded() -> None:
    """09:25 集合竞价行与 15:05 越界行必须被丢弃；bar_time 是 K 线结束时刻。"""
    out = transform_minute(load_json("akshare_hist_min_raw.json"), "600519")

    times = [item["date"].strftime("%H:%M") for item in out]
    assert times == ["09:35", "09:40"]  # 09:25 与 15:05 被剔除
    assert out[0]["timeframe"] == "5m"
    assert out[0]["close"] == 1213.5


def test_minute_empty_returns_empty() -> None:
    assert transform_minute([], "600519") == []


def test_minute_missing_time_column_fails_closed() -> None:
    with pytest.raises(ProviderDataError, match="时间"):
        transform_minute([{"开盘": 1.0, "收盘": 1.0, "最高": 1.0, "最低": 1.0, "成交量": 1}], "600519")


# ── 新闻 ───────────────────────────────────────────────────────────────────
def test_news_maps_published_at_and_url() -> None:
    out = transform_news(load_json("akshare_news_em_raw.json"), "600519")

    assert len(out) == 2
    assert out[0]["date"] > out[1]["date"]  # 按发布时间倒序
    assert out[0]["title"] == "白酒板块早盘走强，贵州茅台涨逾1%"
    assert out[0]["url"].startswith("https://finance.eastmoney.com/")
    assert out[0]["date"].tzinfo is not None
    assert out[0]["symbols"] == "600519"


def test_news_empty_returns_empty() -> None:
    assert transform_news([], "600519") == []


def test_news_missing_url_fails_closed() -> None:
    records = [{"新闻标题": "x", "新闻内容": "y", "发布时间": "2026-07-14 09:00:00", "文章来源": "z"}]
    with pytest.raises(ProviderDataError, match="新闻链接"):
        transform_news(records, "600519")


def test_news_bad_datetime_fails_closed() -> None:
    records = [
        {
            "新闻标题": "x",
            "新闻内容": "y",
            "发布时间": "昨天 09:12",
            "文章来源": "z",
            "新闻链接": "https://a",
        }
    ]
    with pytest.raises(ProviderDataError, match="时间格式"):
        transform_news(records, "600519")


# ── 代码规范化 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("600519", "600519"), ("sh600519", "600519"), ("600519.SH", "600519"), (" sz000001 ", "000001")],
)
def test_normalize_symbol(raw: str, expected: str) -> None:
    assert normalize_symbol(raw) == expected


@pytest.mark.parametrize("raw", ["60051", "abcdef", "", "6005190"])
def test_normalize_symbol_rejects_garbage(raw: str) -> None:
    with pytest.raises(ProviderDataError):
        normalize_symbol(raw)


def test_spot_datetime_is_shanghai_aware(now: datetime) -> None:
    out = transform_spot(load_json("akshare_spot_em_raw.json"), ["600519"], now)
    assert out[0]["last_timestamp"].utcoffset() is not None
    assert out[0]["last_timestamp"].astimezone(SHANGHAI).hour == 9
