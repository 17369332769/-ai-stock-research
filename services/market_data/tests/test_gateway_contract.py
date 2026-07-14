"""OpenBB 网关契约测试（spec §16：Provider 契约 ≥24 条）。

覆盖 spec §16.1 点名的每一类：
    正常响应 / 空列表 / 字段缺失 / 字段类型改变 / HTTP 429 / HTTP 5xx / 30 秒超时 / 脏数据

失败语义（spec §5.2 / §7）：
    上游任何故障 → ProviderUnavailable（424），**不静默切源、不返回缓存**
    调用方参数错误 → InvalidArgument（400）

全部用 respx mock httpx —— **一条公网请求都不发**。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import respx

from apps.api.app.core.clock import SHANGHAI
from apps.api.app.core.errors import InvalidArgument, ProviderUnavailable
from services.market_data.openbb_gateway import (
    ROUTE_COMPANY_NEWS,
    ROUTE_CONSTITUENTS,
    ROUTE_HISTORICAL,
    ROUTE_QUOTE,
    OpenBBHttpGateway,
)
from services.market_data.tests.conftest import BASE_URL, load

pytestmark = pytest.mark.contract

QUOTE_URL = f"{BASE_URL}{ROUTE_QUOTE}"
BARS_URL = f"{BASE_URL}{ROUTE_HISTORICAL}"
CONS_URL = f"{BASE_URL}{ROUTE_CONSTITUENTS}"
NEWS_URL = f"{BASE_URL}{ROUTE_COMPANY_NEWS}"

AS_OF = date(2026, 7, 14)
DAY_START = datetime(2026, 7, 1, 0, 0, tzinfo=SHANGHAI)
DAY_END = datetime(2026, 7, 14, 15, 30, tzinfo=SHANGHAI)


# ════════════════════════════════════════════════════════════════════════
# 行情（equity/price/quote，provider=akshare）
# ════════════════════════════════════════════════════════════════════════
@respx.mock
async def test_quotes_normal_response(gateway: OpenBBHttpGateway, now: datetime) -> None:
    route = respx.get(QUOTE_URL).mock(return_value=httpx.Response(200, json=load("openbb_quote_ok.json")))
    quotes = await gateway.get_quotes(["600519", "000001"], now)

    assert len(quotes) == 2
    maotai = next(q for q in quotes if q.symbol == "600519")
    assert maotai.price == Decimal("1215.04")
    assert maotai.previous_close == Decimal("1211.0")
    assert maotai.volume_ratio == Decimal("0.98")
    # 溯源三件套（spec §4.2）
    assert maotai.source == "eastmoney_via_akshare"
    assert maotai.source_url == "https://quote.eastmoney.com/sh600519.html"
    assert maotai.observed_at.tzinfo is not None
    # 原始上游口径整包留存（quotes.raw_payload NOT NULL）
    assert maotai.raw_payload["change_percent"] == 0.33

    # 路由与 provider 参数正确
    request = route.calls[0].request
    assert request.url.params["provider"] == "akshare"
    assert request.url.params["symbol"] == "600519,000001"


@respx.mock
async def test_quotes_empty_results(gateway: OpenBBHttpGateway, now: datetime) -> None:
    respx.get(QUOTE_URL).mock(return_value=httpx.Response(200, json=load("openbb_quote_empty.json")))
    assert await gateway.get_quotes(["600519"], now) == []


@respx.mock
async def test_quotes_missing_field_fails_closed(gateway: OpenBBHttpGateway, now: datetime) -> None:
    """上游删掉 last_price → ProviderUnavailable，绝不用 0 或上一条填。"""
    respx.get(QUOTE_URL).mock(
        return_value=httpx.Response(200, json=load("openbb_quote_missing_field.json"))
    )
    with pytest.raises(ProviderUnavailable, match="last_price"):
        await gateway.get_quotes(["600519", "000001"], now)


@respx.mock
async def test_quotes_type_change_fails_closed(gateway: OpenBBHttpGateway, now: datetime) -> None:
    """last_price 从数字变成 "N/A" → ProviderUnavailable。"""
    respx.get(QUOTE_URL).mock(
        return_value=httpx.Response(200, json=load("openbb_quote_type_changed.json"))
    )
    with pytest.raises(ProviderUnavailable, match="last_price"):
        await gateway.get_quotes(["600519", "000001"], now)


@respx.mock
async def test_quotes_rate_limited(gateway: OpenBBHttpGateway, now: datetime) -> None:
    respx.get(QUOTE_URL).mock(return_value=httpx.Response(429, json=load("openbb_error_429.json")))
    with pytest.raises(ProviderUnavailable, match="429"):
        await gateway.get_quotes(["600519"], now)


@respx.mock
async def test_quotes_server_error(gateway: OpenBBHttpGateway, now: datetime) -> None:
    respx.get(QUOTE_URL).mock(return_value=httpx.Response(500, json=load("openbb_error_500.json")))
    with pytest.raises(ProviderUnavailable, match="500"):
        await gateway.get_quotes(["600519"], now)


@respx.mock
async def test_quotes_timeout_30s(gateway: OpenBBHttpGateway, now: datetime) -> None:
    """30 秒超时 → ProviderUnavailable（进入 stale/unavailable，不混口径）。"""
    respx.get(QUOTE_URL).mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(ProviderUnavailable, match="超时"):
        await gateway.get_quotes(["600519"], now)


@respx.mock
async def test_quotes_dirty_data_reaches_normalization_layer(
    gateway: OpenBBHttpGateway, now: datetime
) -> None:
    """脏值（prev_close=0、负价）在网关层能解析出来 —— 语义拒收是 normalization 的职责。

    这条测试锁死分层：网关不该悄悄"修正"脏值，也不该把语义脏值当 schema 错误吞掉。
    """
    respx.get(QUOTE_URL).mock(return_value=httpx.Response(200, json=load("openbb_quote_dirty.json")))
    quotes = await gateway.get_quotes(["600519", "000001"], now)

    maotai = next(q for q in quotes if q.symbol == "600519")
    assert maotai.previous_close == 0  # 原样透出
    pingan = next(q for q in quotes if q.symbol == "000001")
    assert pingan.price < 0  # 原样透出，交给 validate_quote 拒收


@respx.mock
async def test_quotes_ignores_unrequested_symbols(gateway: OpenBBHttpGateway, now: datetime) -> None:
    respx.get(QUOTE_URL).mock(return_value=httpx.Response(200, json=load("openbb_quote_ok.json")))
    quotes = await gateway.get_quotes(["600519"], now)
    assert [q.symbol for q in quotes] == ["600519"]


async def test_quotes_rejects_empty_symbols(gateway: OpenBBHttpGateway, now: datetime) -> None:
    with pytest.raises(InvalidArgument):
        await gateway.get_quotes([], now)


async def test_quotes_rejects_bad_symbol(gateway: OpenBBHttpGateway, now: datetime) -> None:
    with pytest.raises(InvalidArgument):
        await gateway.get_quotes(["AAPL"], now)


async def test_quotes_rejects_naive_datetime(gateway: OpenBBHttpGateway) -> None:
    with pytest.raises(InvalidArgument, match="时区"):
        await gateway.get_quotes(["600519"], datetime(2026, 7, 14, 9, 50))  # noqa: DTZ001


# ════════════════════════════════════════════════════════════════════════
# K 线（equity/price/historical，provider=akshare）
# ════════════════════════════════════════════════════════════════════════
@respx.mock
async def test_bars_daily_normal_response(gateway: OpenBBHttpGateway) -> None:
    route = respx.get(BARS_URL).mock(return_value=httpx.Response(200, json=load("openbb_bars_1d_ok.json")))
    bars = await gateway.get_bars("600519", "1d", DAY_START, DAY_END)

    assert len(bars) == 3
    assert [bar.bar_time.date().isoformat() for bar in bars] == [
        "2026-07-10",
        "2026-07-13",
        "2026-07-14",
    ]
    first = bars[0]
    assert first.open == Decimal("1190.0")
    assert first.close == Decimal("1201.0")
    assert first.high == Decimal("1205.5")
    assert first.low == Decimal("1188.0")
    assert first.adjustment == "qfq"
    assert first.timeframe == "1d"
    assert first.observed_at.tzinfo is not None

    params = route.calls[0].request.url.params
    assert params["provider"] == "akshare"
    assert params["interval"] == "1d"
    assert params["adjustment"] == "qfq"
    assert params["start_date"] == "2026-07-01"
    assert params["end_date"] == "2026-07-14"


@respx.mock
async def test_bars_minute_window_is_clipped(gateway: OpenBBHttpGateway) -> None:
    """上游按自然日返回，网关按调用方给的**精确时间窗**收口（5m 尤其重要）。"""
    respx.get(BARS_URL).mock(return_value=httpx.Response(200, json=load("openbb_bars_5m_ok.json")))
    start = datetime(2026, 7, 14, 9, 30, tzinfo=SHANGHAI)
    end = datetime(2026, 7, 14, 9, 45, tzinfo=SHANGHAI)

    bars = await gateway.get_bars("600519", "5m", start, end)
    assert [bar.bar_time.strftime("%H:%M") for bar in bars] == ["09:35", "09:40"]  # 14:55 被裁掉


@respx.mock
async def test_bars_empty_results(gateway: OpenBBHttpGateway) -> None:
    respx.get(BARS_URL).mock(return_value=httpx.Response(200, json=load("openbb_bars_empty.json")))
    assert await gateway.get_bars("600519", "1d", DAY_START, DAY_END) == []


@respx.mock
async def test_bars_missing_field_fails_closed(gateway: OpenBBHttpGateway) -> None:
    respx.get(BARS_URL).mock(
        return_value=httpx.Response(200, json=load("openbb_bars_missing_field.json"))
    )
    with pytest.raises(ProviderUnavailable, match="close"):
        await gateway.get_bars("600519", "1d", DAY_START, DAY_END)


@respx.mock
async def test_bars_type_change_fails_closed(gateway: OpenBBHttpGateway) -> None:
    """volume 变成中文字符串 → ProviderUnavailable。"""
    respx.get(BARS_URL).mock(
        return_value=httpx.Response(200, json=load("openbb_bars_type_changed.json"))
    )
    with pytest.raises(ProviderUnavailable):
        await gateway.get_bars("600519", "1d", DAY_START, DAY_END)


@respx.mock
async def test_bars_rate_limited(gateway: OpenBBHttpGateway) -> None:
    respx.get(BARS_URL).mock(return_value=httpx.Response(429, json=load("openbb_error_429.json")))
    with pytest.raises(ProviderUnavailable, match="429"):
        await gateway.get_bars("600519", "1d", DAY_START, DAY_END)


@respx.mock
async def test_bars_server_error(gateway: OpenBBHttpGateway) -> None:
    respx.get(BARS_URL).mock(return_value=httpx.Response(502))
    with pytest.raises(ProviderUnavailable, match="502"):
        await gateway.get_bars("600519", "1d", DAY_START, DAY_END)


@respx.mock
async def test_bars_timeout_30s(gateway: OpenBBHttpGateway) -> None:
    respx.get(BARS_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
    with pytest.raises(ProviderUnavailable, match="超时"):
        await gateway.get_bars("600519", "1d", DAY_START, DAY_END)


@respx.mock
async def test_bars_dirty_ohlc_reaches_normalization_layer(gateway: OpenBBHttpGateway) -> None:
    respx.get(BARS_URL).mock(
        return_value=httpx.Response(200, json=load("openbb_bars_dirty_ohlc.json"))
    )
    bars = await gateway.get_bars("600519", "1d", DAY_START, DAY_END)
    assert bars[0].high < bars[0].open  # 原样透出，交给 validate_bar 拒收


async def test_bars_rejects_unknown_timeframe(gateway: OpenBBHttpGateway) -> None:
    with pytest.raises(InvalidArgument, match="timeframe"):
        await gateway.get_bars("600519", "1h", DAY_START, DAY_END)


async def test_bars_rejects_inverted_window(gateway: OpenBBHttpGateway) -> None:
    with pytest.raises(InvalidArgument, match="晚于"):
        await gateway.get_bars("600519", "1d", DAY_END, DAY_START)


# ════════════════════════════════════════════════════════════════════════
# 成分（index/constituents，provider=csi300）
# ════════════════════════════════════════════════════════════════════════
@respx.mock
async def test_constituents_normal_response(gateway: OpenBBHttpGateway) -> None:
    route = respx.get(CONS_URL).mock(
        return_value=httpx.Response(200, json=load("openbb_constituents_ok.json"))
    )
    members = await gateway.get_universe_members("CSI300", AS_OF)

    assert len(members) == 4
    maotai = next(m for m in members if m.symbol == "600519")
    assert maotai.universe == "CSI300"
    assert maotai.source == "csindex"
    assert maotai.effective_to is None  # 有效期闭合由 ingest 差分计算
    assert maotai.effective_from == date(2026, 6, 15)  # 成分表官方生效日
    assert maotai.observed_at.tzinfo is not None

    params = route.calls[0].request.url.params
    assert params["provider"] == "csi300"
    assert params["symbol"] == "000300"
    assert params["as_of"] == "2026-07-14"


@respx.mock
async def test_constituents_empty_fails_closed(gateway: OpenBBHttpGateway) -> None:
    """**关键**：空成分不是"今天没有成分股"，是上游坏了。

    若接受空列表，成分同步会把 300 只全部标记为"已调出"，历史有效期被摧毁（spec §8）。
    """
    respx.get(CONS_URL).mock(
        return_value=httpx.Response(200, json=load("openbb_constituents_empty.json"))
    )
    with pytest.raises(ProviderUnavailable, match="成分为空"):
        await gateway.get_universe_members("CSI300", AS_OF)


@respx.mock
async def test_constituents_missing_field_fails_closed(gateway: OpenBBHttpGateway) -> None:
    respx.get(CONS_URL).mock(
        return_value=httpx.Response(200, json=load("openbb_constituents_missing_field.json"))
    )
    with pytest.raises(ProviderUnavailable, match="name"):
        await gateway.list_instruments("CSI300", AS_OF)


@respx.mock
async def test_constituents_rate_limited(gateway: OpenBBHttpGateway) -> None:
    respx.get(CONS_URL).mock(return_value=httpx.Response(429))
    with pytest.raises(ProviderUnavailable, match="429"):
        await gateway.get_universe_members("CSI300", AS_OF)


@respx.mock
async def test_constituents_server_error(gateway: OpenBBHttpGateway) -> None:
    respx.get(CONS_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(ProviderUnavailable, match="500"):
        await gateway.get_universe_members("CSI300", AS_OF)


@respx.mock
async def test_constituents_timeout_30s(gateway: OpenBBHttpGateway) -> None:
    respx.get(CONS_URL).mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(ProviderUnavailable, match="超时"):
        await gateway.get_universe_members("CSI300", AS_OF)


@respx.mock
async def test_search_prefers_exact_code_match(gateway: OpenBBHttpGateway) -> None:
    """spec §7.1：只搜当前成分，精确代码匹配优先。"""
    respx.get(CONS_URL).mock(
        return_value=httpx.Response(200, json=load("openbb_constituents_ok.json"))
    )
    hits = await gateway.search_instruments("CSI300", "600519", AS_OF, 20)
    assert [h.symbol for h in hits] == ["600519"]
    assert hits[0].name == "贵州茅台"
    assert hits[0].exchange == "SSE"
    # 上游 4 个 akshare 函数都不给行业/上市日 —— 留空不编造
    assert hits[0].industry is None
    assert hits[0].listed_at is None


@respx.mock
async def test_search_by_name_and_limit(gateway: OpenBBHttpGateway) -> None:
    respx.get(CONS_URL).mock(
        return_value=httpx.Response(200, json=load("openbb_constituents_ok.json"))
    )
    hits = await gateway.search_instruments("CSI300", "中国平安", AS_OF, 1)
    assert [h.symbol for h in hits] == ["601318"]


@respx.mock
async def test_search_non_member_returns_empty(gateway: OpenBBHttpGateway) -> None:
    """非成分股搜不到 —— 上游 API 层据此返回 409 NOT_CURRENT_UNIVERSE_MEMBER。"""
    respx.get(CONS_URL).mock(
        return_value=httpx.Response(200, json=load("openbb_constituents_ok.json"))
    )
    assert await gateway.search_instruments("CSI300", "000002", AS_OF, 20) == []


async def test_search_rejects_bad_limit(gateway: OpenBBHttpGateway) -> None:
    with pytest.raises(InvalidArgument, match="limit"):
        await gateway.search_instruments("CSI300", "600519", AS_OF, 0)
    with pytest.raises(InvalidArgument, match="limit"):
        await gateway.search_instruments("CSI300", "600519", AS_OF, 101)


async def test_search_rejects_empty_query(gateway: OpenBBHttpGateway) -> None:
    with pytest.raises(InvalidArgument, match="关键字"):
        await gateway.search_instruments("CSI300", "  ", AS_OF, 20)


# ════════════════════════════════════════════════════════════════════════
# 公告（news/company，provider=cn_disclosure）
# ════════════════════════════════════════════════════════════════════════
@respx.mock
async def test_announcements_normal_response(gateway: OpenBBHttpGateway) -> None:
    route = respx.get(NEWS_URL).mock(
        return_value=httpx.Response(200, json=load("openbb_announcements_ok.json"))
    )
    docs = await gateway.get_announcements("600519", DAY_START, DAY_END)

    assert len(docs) == 2
    assert all(doc.document_type == "announcement" for doc in docs)
    assert docs[0].source == "cninfo"
    assert docs[0].source_url.endswith(".PDF")  # 法定披露原文
    assert docs[0].published_at.tzinfo is not None
    assert docs[0].observed_at.tzinfo is not None
    assert docs[0].published_at > docs[1].published_at  # 倒序

    assert route.calls[0].request.url.params["provider"] == "cn_disclosure"


@respx.mock
async def test_announcements_empty_results(gateway: OpenBBHttpGateway) -> None:
    respx.get(NEWS_URL).mock(
        return_value=httpx.Response(200, json=load("openbb_announcements_empty.json"))
    )
    assert await gateway.get_announcements("600519", DAY_START, DAY_END) == []


@respx.mock
async def test_announcements_missing_url_fails_closed(gateway: OpenBBHttpGateway) -> None:
    """没有原文链接的公告不可引用（Agent 证据必须能回溯到原文）→ 拒。"""
    respx.get(NEWS_URL).mock(
        return_value=httpx.Response(200, json=load("openbb_announcements_missing_url.json"))
    )
    with pytest.raises(ProviderUnavailable, match="url"):
        await gateway.get_announcements("600519", DAY_START, DAY_END)


@respx.mock
async def test_announcements_rate_limited(gateway: OpenBBHttpGateway) -> None:
    respx.get(NEWS_URL).mock(return_value=httpx.Response(429))
    with pytest.raises(ProviderUnavailable, match="429"):
        await gateway.get_announcements("600519", DAY_START, DAY_END)


@respx.mock
async def test_announcements_server_error(gateway: OpenBBHttpGateway) -> None:
    respx.get(NEWS_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(ProviderUnavailable, match="503"):
        await gateway.get_announcements("600519", DAY_START, DAY_END)


@respx.mock
async def test_announcements_timeout_30s(gateway: OpenBBHttpGateway) -> None:
    respx.get(NEWS_URL).mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(ProviderUnavailable, match="超时"):
        await gateway.get_announcements("600519", DAY_START, DAY_END)


# ════════════════════════════════════════════════════════════════════════
# 新闻（news/company，provider=akshare）
# ════════════════════════════════════════════════════════════════════════
@respx.mock
async def test_news_normal_response(gateway: OpenBBHttpGateway) -> None:
    route = respx.get(NEWS_URL).mock(return_value=httpx.Response(200, json=load("openbb_news_ok.json")))
    docs = await gateway.get_news("600519", DAY_START, DAY_END)

    assert len(docs) == 2
    assert all(doc.document_type == "news" for doc in docs)
    assert docs[0].body_text is not None  # 新闻有正文，公告没有
    assert docs[0].source == "东方财富"

    # 新闻与公告走同一路由、不同 provider —— 口径不混
    assert route.calls[0].request.url.params["provider"] == "akshare"


@respx.mock
async def test_news_empty_results(gateway: OpenBBHttpGateway) -> None:
    respx.get(NEWS_URL).mock(return_value=httpx.Response(200, json=load("openbb_news_empty.json")))
    assert await gateway.get_news("600519", DAY_START, DAY_END) == []


@respx.mock
async def test_news_type_change_fails_closed(gateway: OpenBBHttpGateway) -> None:
    """published_at 变成"昨天 09:12"这种人类可读串 → ProviderUnavailable。"""
    respx.get(NEWS_URL).mock(
        return_value=httpx.Response(200, json=load("openbb_news_type_changed.json"))
    )
    with pytest.raises(ProviderUnavailable, match="时间格式"):
        await gateway.get_news("600519", DAY_START, DAY_END)


@respx.mock
async def test_news_out_of_window_documents_are_clipped(gateway: OpenBBHttpGateway) -> None:
    respx.get(NEWS_URL).mock(return_value=httpx.Response(200, json=load("openbb_news_ok.json")))
    start = datetime(2026, 7, 14, 0, 0, tzinfo=SHANGHAI)
    docs = await gateway.get_news("600519", start, DAY_END)
    assert len(docs) == 1  # 07-13 的那条被裁掉


@respx.mock
async def test_news_rate_limited(gateway: OpenBBHttpGateway) -> None:
    respx.get(NEWS_URL).mock(return_value=httpx.Response(429))
    with pytest.raises(ProviderUnavailable, match="429"):
        await gateway.get_news("600519", DAY_START, DAY_END)


@respx.mock
async def test_news_timeout_30s(gateway: OpenBBHttpGateway) -> None:
    respx.get(NEWS_URL).mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(ProviderUnavailable, match="超时"):
        await gateway.get_news("600519", DAY_START, DAY_END)


# ════════════════════════════════════════════════════════════════════════
# 传输层通用
# ════════════════════════════════════════════════════════════════════════
@respx.mock
async def test_non_json_response_fails_closed(gateway: OpenBBHttpGateway, now: datetime) -> None:
    respx.get(QUOTE_URL).mock(return_value=httpx.Response(200, text="<html>502 Bad Gateway</html>"))
    with pytest.raises(ProviderUnavailable, match="非 JSON"):
        await gateway.get_quotes(["600519"], now)


@respx.mock
async def test_missing_results_key_fails_closed(gateway: OpenBBHttpGateway, now: datetime) -> None:
    respx.get(QUOTE_URL).mock(return_value=httpx.Response(200, json={"data": []}))
    with pytest.raises(ProviderUnavailable, match="results"):
        await gateway.get_quotes(["600519"], now)


@respx.mock
async def test_results_wrong_shape_fails_closed(gateway: OpenBBHttpGateway, now: datetime) -> None:
    respx.get(QUOTE_URL).mock(return_value=httpx.Response(200, json={"results": {"symbol": "600519"}}))
    with pytest.raises(ProviderUnavailable, match="不是数组"):
        await gateway.get_quotes(["600519"], now)


@respx.mock
async def test_bad_request_is_invalid_argument_not_provider_failure(
    gateway: OpenBBHttpGateway, now: datetime
) -> None:
    """OpenBB 返回 400/422 是**我们传错参数**，不是上游故障 —— 不能伪装成 424。"""
    respx.get(QUOTE_URL).mock(return_value=httpx.Response(422, json=load("openbb_error_400.json")))
    with pytest.raises(InvalidArgument):
        await gateway.get_quotes(["600519"], now)


@respx.mock
async def test_connection_error_fails_closed(gateway: OpenBBHttpGateway, now: datetime) -> None:
    respx.get(QUOTE_URL).mock(side_effect=httpx.ConnectError("connection refused"))
    with pytest.raises(ProviderUnavailable, match="网络错误"):
        await gateway.get_quotes(["600519"], now)


@respx.mock
async def test_no_silent_fallback_between_providers(gateway: OpenBBHttpGateway) -> None:
    """spec §5.2：不做静默备用源。

    公告源挂了 → 报错，**绝不**偷偷改用新闻源填充公告（那会混淆口径且骗过 UI）。
    """
    respx.get(NEWS_URL).mock(
        side_effect=lambda request: httpx.Response(503)
        if request.url.params.get("provider") == "cn_disclosure"
        else httpx.Response(200, json=load("openbb_news_ok.json"))
    )
    with pytest.raises(ProviderUnavailable):
        await gateway.get_announcements("600519", DAY_START, DAY_END)
    # 新闻源仍可用 —— 证明上一条失败不是因为整体网络问题，而是没有跨源顶替
    assert len(await gateway.get_news("600519", DAY_START, DAY_END)) == 2


@respx.mock
async def test_future_bars_are_returned_by_gateway_and_rejected_by_validator(
    gateway: OpenBBHttpGateway, now: datetime
) -> None:
    """未来 K 线：网关不判断时间语义（它只管 schema），validate_bar 负责拒收 —— 分层清晰。"""
    from services.market_data.normalization import validate_bar

    respx.get(BARS_URL).mock(return_value=httpx.Response(200, json=load("openbb_bars_future.json")))
    end = now + timedelta(days=30)
    bars = await gateway.get_bars("600519", "1d", DAY_START, end)

    assert len(bars) == 1
    issues = validate_bar(bars[0], now)
    assert any(issue.reason.value == "future_timestamp" for issue in issues)
