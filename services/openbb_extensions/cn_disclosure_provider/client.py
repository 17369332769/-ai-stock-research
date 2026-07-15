"""唯一触达巨潮资讯（cninfo）的模块。

只有 ``services/openbb_extensions`` 允许直连第三方 URL（spec §4.2）。

失败一律抛 ``ProviderUpstreamError`` —— 不重试到别的来源、不返回缓存、不返回空列表冒充
"今天没有公告"（spec §8：不得静默使用缓存冒充新数据）。
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import httpx

from .constants import (
    CNINFO_LIST_REFERER,
    CNINFO_ORG_LOOKUP_URL,
    CNINFO_QUERY_URL,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_PAGES,
    PAGE_SIZE,
    REQUEST_INTERVAL_SECONDS,
    USER_AGENT,
    ProviderDataError,
    ProviderUpstreamError,
    column_for,
)
from .transform import extract_org_id, normalize_symbol

_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "http://www.cninfo.com.cn",
    "Referer": CNINFO_LIST_REFERER,
    "User-Agent": USER_AGENT,
    "X-Requested-With": "XMLHttpRequest",
}


def _json(response: httpx.Response, what: str) -> Any:
    if response.status_code == 429:
        raise ProviderUpstreamError(f"巨潮限流（HTTP 429）：{what}")
    if response.status_code >= 500:
        raise ProviderUpstreamError(f"巨潮服务端错误（HTTP {response.status_code}）：{what}")
    if response.status_code >= 400:
        raise ProviderUpstreamError(f"巨潮拒绝请求（HTTP {response.status_code}）：{what}")
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderUpstreamError(f"巨潮返回非 JSON 响应：{what}") from exc


class CninfoClient:
    """巨潮 HTTP 客户端。可注入 ``httpx.AsyncClient`` 以便契约测试用 respx mock。"""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        interval: float = REQUEST_INTERVAL_SECONDS,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout, headers=_HEADERS)
        self._interval = interval

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> CninfoClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def _post(self, url: str, data: dict[str, Any], what: str) -> Any:
        try:
            response = await self._client.post(url, data=data, headers=_HEADERS)
        except httpx.TimeoutException as exc:
            raise ProviderUpstreamError(f"巨潮请求超时：{what}") from exc
        except httpx.HTTPError as exc:
            raise ProviderUpstreamError(f"巨潮网络错误：{what}：{exc}") from exc
        return _json(response, what)

    async def fetch_org_id(self, symbol: str) -> str:
        code = normalize_symbol(symbol)
        payload = await self._post(
            CNINFO_ORG_LOOKUP_URL,
            {"keyWord": code, "maxNum": "10"},
            what=f"orgId lookup {code}",
        )
        if not isinstance(payload, list):
            raise ProviderDataError(f"巨潮 topSearch 返回形态异常：{type(payload).__name__}")
        return extract_org_id(payload, code)

    async def fetch_announcements(
        self, symbol: str, start: date, end: date, org_id: str | None = None
    ) -> list[dict[str, Any]]:
        """拉取 [start, end]（含端点）内的全部公告，自动翻页。"""
        code = normalize_symbol(symbol)
        if start > end:
            raise ProviderDataError(f"start {start} 晚于 end {end}")
        resolved_org = org_id or await self.fetch_org_id(code)
        column = column_for(code)
        se_date = f"{start.isoformat()}~{end.isoformat()}"

        items: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            payload = await self._post(
                CNINFO_QUERY_URL,
                {
                    "pageNum": str(page),
                    "pageSize": str(PAGE_SIZE),
                    "column": column,
                    "tabName": "fulltext",
                    "plate": "",
                    "stock": f"{code},{resolved_org}",
                    "searchkey": "",
                    "secid": "",
                    "category": "",
                    "trade": "",
                    "seDate": se_date,
                    "sortName": "",
                    "sortType": "",
                    "isHLtitle": "true",
                },
                what=f"announcements {code} {se_date} page={page}",
            )
            if not isinstance(payload, dict):
                raise ProviderDataError(f"巨潮公告查询返回形态异常：{type(payload).__name__}")
            announcements = payload.get("announcements")
            if announcements is None:
                # 该窗口无公告时上游返回 announcements: null —— 是合法的"空",不是错误
                break
            if not isinstance(announcements, list):
                raise ProviderDataError(
                    f"巨潮 announcements 字段类型异常：{type(announcements).__name__}"
                )
            items.extend(dict(item) for item in announcements)
            if not payload.get("hasMore") or len(announcements) < PAGE_SIZE:
                break
            if self._interval > 0:
                await asyncio.sleep(self._interval)
        return items
