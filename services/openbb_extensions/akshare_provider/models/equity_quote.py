"""OpenBB 标准模型 ``EquityQuote`` 的 AKShare 实现。

REST 路由：``GET /api/v1/equity/price/quote?provider=akshare&symbol=600519,000001``
上游函数：``stock_zh_a_spot_em``（全市场快照，本地按 symbol 过滤）
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.equity_quote import (
    EquityQuoteData,
    EquityQuoteQueryParams,
)
from pydantic import Field

from ..client import fetch_spot
from ..constants import SHANGHAI, SOURCE_NAME
from ..transform import transform_spot


class AKShareEquityQuoteQueryParams(EquityQuoteQueryParams):
    """``symbol`` 支持逗号分隔的多个 A 股代码。"""


class AKShareEquityQuoteData(EquityQuoteData):
    """标准字段之外补上 A 股特有列（量比、换手率、成交额）与溯源字段。"""

    name: str | None = Field(default=None, description="证券简称")
    turnover: float | None = Field(default=None, description="成交额（元）")
    turnover_rate: float | None = Field(default=None, description="换手率（%）")
    volume_ratio: float | None = Field(default=None, description="量比")
    source: str = Field(default=SOURCE_NAME, description="数据来源标识（spec §4.2 必填）")
    source_url: str | None = Field(default=None, description="上游原文页面")
    volume_unit: str | None = Field(default=None, description="成交量单位：hand=手=100 股")
    amount_unit: str | None = Field(default=None, description="成交额单位：CNY")


class AKShareEquityQuoteFetcher(
    Fetcher[AKShareEquityQuoteQueryParams, list[AKShareEquityQuoteData]]
):
    require_credentials = False

    @staticmethod
    def transform_query(params: dict[str, Any]) -> AKShareEquityQuoteQueryParams:
        return AKShareEquityQuoteQueryParams(**params)

    @staticmethod
    async def aextract_data(
        query: AKShareEquityQuoteQueryParams,
        credentials: dict[str, str] | None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        records = await fetch_spot()
        symbols = [s.strip() for s in query.symbol.split(",") if s.strip()]
        # 快照没有上游时间戳：observed_at = 取数时刻（写进 docs，不冒充撮合时间）
        observed_at = datetime.now(tz=SHANGHAI)
        return transform_spot(records, symbols, observed_at)

    @staticmethod
    def transform_data(
        query: AKShareEquityQuoteQueryParams,
        data: list[dict[str, Any]],
        **kwargs: Any,
    ) -> list[AKShareEquityQuoteData]:
        return [AKShareEquityQuoteData.model_validate(item) for item in data]
