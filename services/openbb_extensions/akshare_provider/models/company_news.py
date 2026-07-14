"""OpenBB 标准模型 ``CompanyNews`` 的 AKShare 实现（个股新闻）。

REST 路由：``GET /api/v1/news/company?provider=akshare&symbol=600519&start_date=&end_date=``
上游函数：``stock_news_em``

上游**不接受时间窗参数**（只返回最近约 100 条），因此 ``start_date`` / ``end_date``
在本 Fetcher 内做过滤。这一点必须写进 docs：新闻回补深度受上游限制，不是本系统能力。
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.company_news import (
    CompanyNewsData,
    CompanyNewsQueryParams,
)
from pydantic import Field

from ..client import fetch_news
from ..constants import SHANGHAI, SOURCE_NAME
from ..transform import transform_news


class AKShareCompanyNewsQueryParams(CompanyNewsQueryParams):
    """``symbol`` 必填（``stock_news_em`` 只支持单标的）。"""


class AKShareCompanyNewsData(CompanyNewsData):
    source: str = Field(default=SOURCE_NAME, description="文章来源（上游「文章来源」列）")
    provider_source: str = Field(default=SOURCE_NAME, description="采集来源标识（spec §4.2 必填）")
    keyword: str | None = Field(default=None, description="上游关键词列")


def _bound(value: date | datetime | None, at: time, default: datetime) -> datetime:
    if value is None:
        return default
    if isinstance(value, datetime):
        return value.astimezone(SHANGHAI) if value.tzinfo else value.replace(tzinfo=SHANGHAI)
    return datetime.combine(value, at, tzinfo=SHANGHAI)


class AKShareCompanyNewsFetcher(
    Fetcher[AKShareCompanyNewsQueryParams, list[CompanyNewsData]]
):
    require_credentials = False

    @staticmethod
    def transform_query(params: dict[str, Any]) -> AKShareCompanyNewsQueryParams:
        return AKShareCompanyNewsQueryParams(**params)

    @staticmethod
    async def aextract_data(
        query: AKShareCompanyNewsQueryParams,
        credentials: dict[str, str] | None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        symbol = (query.symbol or "").split(",")[0].strip()
        records = await fetch_news(symbol)
        items = transform_news(records, symbol)

        lower = _bound(query.start_date, time.min, datetime.min.replace(tzinfo=SHANGHAI))
        upper = _bound(query.end_date, time.max, datetime.max.replace(tzinfo=SHANGHAI))
        filtered = [item for item in items if lower <= item["date"] <= upper]
        if query.limit:
            filtered = filtered[: query.limit]
        return filtered

    @staticmethod
    def transform_data(
        query: AKShareCompanyNewsQueryParams,
        data: list[dict[str, Any]],
        **kwargs: Any,
    ) -> list[CompanyNewsData]:
        return [AKShareCompanyNewsData.model_validate(item) for item in data]
