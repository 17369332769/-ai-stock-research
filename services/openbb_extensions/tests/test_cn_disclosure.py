"""中国法定披露 Provider 契约测试（巨潮资讯）。

覆盖：正常响应、空列表、字段缺失、字段类型改变、HTTP 429、HTTP 5xx、30 秒超时、脏数据。
用 respx mock httpx —— **不访问公网**（spec §16.1）。
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from services.openbb_extensions.cn_disclosure_provider.client import CninfoClient
from services.openbb_extensions.cn_disclosure_provider.constants import (
    ALLOWED_SOURCES,
    CNINFO_ORG_LOOKUP_URL,
    CNINFO_QUERY_URL,
    ProviderDataError,
    ProviderUpstreamError,
    column_for,
)
from services.openbb_extensions.cn_disclosure_provider.transform import (
    announcement_pdf_url,
    extract_org_id,
    transform_announcements,
)
from services.openbb_extensions.tests.conftest import load_json

pytestmark = pytest.mark.contract

START = date(2026, 7, 10)
END = date(2026, 7, 14)


# ── 纯 transform ────────────────────────────────────────────────────────────
def test_transform_maps_announcement_to_original_pdf() -> None:
    payload = load_json("cninfo_announcements_raw.json")
    out = transform_announcements(payload["announcements"], "600519")

    assert len(out) == 2
    first = out[0]
    assert first["title"].startswith("贵州茅台酒股份有限公司2026年半年度业绩预告")
    # source_url 必须指向**原文 PDF**（法定披露原文），不是转载页
    assert first["url"] == "http://static.cninfo.com.cn/finalpage/2026-07-14/1220001111.PDF"
    assert first["source"] == "cninfo"
    assert first["document_type"] == "announcement"
    assert first["date"].tzinfo is not None
    assert first["date"].strftime("%Y-%m-%d") == "2026-07-14"
    # 公告原文是 PDF，MVP 不解析 → text 恒为 None（明确的能力边界，不是静默丢数据）
    assert first["text"] is None
    assert out[0]["date"] > out[1]["date"]  # 倒序


def test_transform_empty_returns_empty() -> None:
    assert transform_announcements([], "600519") == []


def test_transform_missing_adjunct_url_fails_closed() -> None:
    payload = load_json("cninfo_announcements_missing_field.json")
    with pytest.raises(ProviderDataError, match="adjunctUrl"):
        transform_announcements(payload["announcements"], "600519")


def test_transform_bad_timestamp_type_fails_closed() -> None:
    records = [
        {
            "announcementId": "1",
            "announcementTitle": "t",
            "announcementTime": "not-a-timestamp",
            "adjunctUrl": "finalpage/x.PDF",
            "secCode": "600519",
        }
    ]
    with pytest.raises(ProviderDataError, match="announcementTime"):
        transform_announcements(records, "600519")


def test_transform_drops_other_securities_in_same_response() -> None:
    """同一 orgId 下可能混入 B 股/债券公告 —— 不属于本次请求的标的，剔除。"""
    payload = load_json("cninfo_announcements_raw.json")
    records = [*payload["announcements"], {**payload["announcements"][0], "secCode": "900519"}]
    out = transform_announcements(records, "600519")
    assert all(item["symbols"] == "600519" for item in out)
    assert len(out) == 2


def test_source_allowlist_rejects_non_statutory_source() -> None:
    """spec §5.2：只返回巨潮/上交所/深交所原文。媒体转载不得从这里出去。"""
    payload = load_json("cninfo_announcements_raw.json")
    with pytest.raises(ProviderDataError, match="白名单"):
        transform_announcements(payload["announcements"], "600519", source="eastmoney")
    assert {"cninfo", "sse", "szse"} == ALLOWED_SOURCES


def test_pdf_url_join() -> None:
    assert announcement_pdf_url("finalpage/2026-07-14/x.PDF") == (
        "http://static.cninfo.com.cn/finalpage/2026-07-14/x.PDF"
    )
    with pytest.raises(ProviderDataError):
        announcement_pdf_url("   ")


def test_org_id_extraction() -> None:
    assert extract_org_id(load_json("cninfo_org_lookup.json"), "600519") == "gssh0600519"
    with pytest.raises(ProviderDataError, match="orgId"):
        extract_org_id([], "600519")


@pytest.mark.parametrize(("symbol", "column"), [("600519", "sse"), ("000001", "szse"), ("300750", "szse")])
def test_column_routing(symbol: str, column: str) -> None:
    assert column_for(symbol) == column


# ── HTTP 客户端（respx，不联网）──────────────────────────────────────────────
def _mock_org_lookup() -> None:
    respx.post(CNINFO_ORG_LOOKUP_URL).mock(
        return_value=httpx.Response(200, json=load_json("cninfo_org_lookup.json"))
    )


@respx.mock
async def test_client_fetches_announcements() -> None:
    _mock_org_lookup()
    respx.post(CNINFO_QUERY_URL).mock(
        return_value=httpx.Response(200, json=load_json("cninfo_announcements_raw.json"))
    )
    async with CninfoClient(interval=0) as client:
        records = await client.fetch_announcements("600519", START, END)
    assert len(records) == 2


@respx.mock
async def test_client_empty_window_returns_empty() -> None:
    """上游对"该窗口无公告"返回 announcements: null —— 是合法空值，不是错误。"""
    _mock_org_lookup()
    respx.post(CNINFO_QUERY_URL).mock(
        return_value=httpx.Response(200, json=load_json("cninfo_announcements_empty.json"))
    )
    async with CninfoClient(interval=0) as client:
        assert await client.fetch_announcements("600519", START, END) == []


@respx.mock
async def test_client_rate_limited_fails_closed() -> None:
    _mock_org_lookup()
    respx.post(CNINFO_QUERY_URL).mock(return_value=httpx.Response(429, text="too many requests"))
    async with CninfoClient(interval=0) as client:
        with pytest.raises(ProviderUpstreamError, match="429"):
            await client.fetch_announcements("600519", START, END)


@respx.mock
async def test_client_server_error_fails_closed() -> None:
    _mock_org_lookup()
    respx.post(CNINFO_QUERY_URL).mock(return_value=httpx.Response(503, text="unavailable"))
    async with CninfoClient(interval=0) as client:
        with pytest.raises(ProviderUpstreamError, match="503"):
            await client.fetch_announcements("600519", START, END)


@respx.mock
async def test_client_timeout_fails_closed() -> None:
    """30 秒超时 → ProviderUpstreamError，不返回缓存、不切换到别的来源。"""
    _mock_org_lookup()
    respx.post(CNINFO_QUERY_URL).mock(side_effect=httpx.ReadTimeout("timed out after 30s"))
    async with CninfoClient(interval=0) as client:
        with pytest.raises(ProviderUpstreamError, match="超时"):
            await client.fetch_announcements("600519", START, END)


@respx.mock
async def test_client_non_json_fails_closed() -> None:
    _mock_org_lookup()
    respx.post(CNINFO_QUERY_URL).mock(return_value=httpx.Response(200, text="<html>验证码</html>"))
    async with CninfoClient(interval=0) as client:
        with pytest.raises(ProviderUpstreamError, match="非 JSON"):
            await client.fetch_announcements("600519", START, END)


@respx.mock
async def test_client_type_change_fails_closed() -> None:
    """announcements 从数组变成对象（上游改版）→ 必须炸。"""
    _mock_org_lookup()
    respx.post(CNINFO_QUERY_URL).mock(
        return_value=httpx.Response(200, json={"announcements": {"unexpected": "object"}})
    )
    async with CninfoClient(interval=0) as client:
        with pytest.raises(ProviderDataError, match="类型异常"):
            await client.fetch_announcements("600519", START, END)


@respx.mock
async def test_client_paginates_until_no_more() -> None:
    _mock_org_lookup()
    page = load_json("cninfo_announcements_raw.json")
    full = {**page, "announcements": page["announcements"] * 15, "hasMore": True}  # 30 条 = PAGE_SIZE
    last = {**page, "hasMore": False}
    route = respx.post(CNINFO_QUERY_URL)
    route.side_effect = [httpx.Response(200, json=full), httpx.Response(200, json=last)]

    async with CninfoClient(interval=0) as client:
        records = await client.fetch_announcements("600519", START, END)
    assert len(records) == 32
    assert route.call_count == 2
