"""法定披露公告，复用 OpenBB 标准模型 ``CompanyNews``。

REST 路由：``GET /api/v1/news/company?provider=cn_disclosure&symbol=600519&start_date=&end_date=``

为什么复用 ``CompanyNews`` 而不是自造一个 router：OpenBB 的自定义路由需要另一组
``openbb_core_extension`` entry point 并重建路由表；公告的字段形态（标题、正文、时间、
原文链接、关联证券）与 ``CompanyNews`` 完全同构。**"公告 vs 新闻"由 provider 区分**：
``provider=cn_disclosure`` → ``document_type=announcement``（法定披露原文）
``provider=akshare``       → ``document_type=news``（媒体报道）
网关据此写入 ``documents.document_type``，两种口径不会混淆（spec §6）。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.company_news import (
    CompanyNewsData,
    CompanyNewsQueryParams,
)
from pydantic import Field

from ..client import CninfoClient
from ..constants import PRIMARY_SOURCE, SHANGHAI, ProviderConfigError
from ..transform import transform_announcements

# 公告默认回溯窗口
DEFAULT_LOOKBACK_DAYS = 30


class CnDisclosureCompanyNewsQueryParams(CompanyNewsQueryParams):
    """``symbol`` 必填，单标的。"""


class CnDisclosureCompanyNewsData(CompanyNewsData):
    source: str = Field(default=PRIMARY_SOURCE, description="法定披露来源：cninfo / sse / szse")
    document_type: str = Field(default="announcement", description="固定 announcement")
    announcement_id: str | None = Field(default=None, description="巨潮公告 ID")
    org_id: str | None = Field(default=None, description="巨潮机构 ID")
    announcement_type: str | None = Field(default=None, description="巨潮公告分类")
    sec_name: str | None = Field(default=None, description="证券简称")
    detail_url: str | None = Field(default=None, description="巨潮网页版详情页")


def _as_date(value: date | datetime | None, default_value: date) -> date:
    if value is None:
        return default_value
    if isinstance(value, datetime):
        return value.astimezone(SHANGHAI).date() if value.tzinfo else value.date()
    return value


class CnDisclosureCompanyNewsFetcher(
    Fetcher[CnDisclosureCompanyNewsQueryParams, list[CompanyNewsData]]
):
    require_credentials = False

    @staticmethod
    def transform_query(params: dict[str, Any]) -> CnDisclosureCompanyNewsQueryParams:
        return CnDisclosureCompanyNewsQueryParams(**params)

    @staticmethod
    async def aextract_data(
        query: CnDisclosureCompanyNewsQueryParams,
        credentials: dict[str, str] | None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        symbol = (query.symbol or "").split(",")[0].strip()
        if not symbol:
            raise ProviderConfigError("公告查询必须指定 symbol")
        today = datetime.now(tz=SHANGHAI).date()
        end = _as_date(query.end_date, today)
        start = _as_date(query.start_date, end - timedelta(days=DEFAULT_LOOKBACK_DAYS))

        async with CninfoClient() as client:
            records = await client.fetch_announcements(symbol, start, end)
        items = transform_announcements(records, symbol, source=PRIMARY_SOURCE)

        # 巨潮按自然日过滤，这里再按精确时间边界收口
        lower = datetime.combine(start, time.min, tzinfo=SHANGHAI)
        upper = datetime.combine(end, time.max, tzinfo=SHANGHAI)
        filtered = [item for item in items if lower <= item["date"] <= upper]
        if query.limit:
            filtered = filtered[: query.limit]
        return filtered

    @staticmethod
    def transform_data(
        query: CnDisclosureCompanyNewsQueryParams,
        data: list[dict[str, Any]],
        **kwargs: Any,
    ) -> list[CompanyNewsData]:
        return [CnDisclosureCompanyNewsData.model_validate(item) for item in data]
