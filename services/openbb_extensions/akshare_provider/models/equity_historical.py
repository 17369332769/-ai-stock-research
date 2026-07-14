"""OpenBB 标准模型 ``EquityHistorical`` 的 AKShare 实现（日线 + 5 分钟线）。

REST 路由：``GET /api/v1/equity/price/historical?provider=akshare&symbol=600519&interval=1d``
上游函数：``interval=1d`` → ``stock_zh_a_hist``；``interval=5m`` → ``stock_zh_a_hist_min_em``
复权：固定 ``qfq``（前复权）。同一张 bars 表禁止混复权口径。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.equity_historical import (
    EquityHistoricalData,
    EquityHistoricalQueryParams,
)
from pydantic import Field

from ..client import fetch_daily, fetch_minute
from ..constants import SHANGHAI, SOURCE_NAME, ProviderConfigError
from ..transform import transform_daily, transform_minute

# 5 分钟线上游只保留最近约 5 个交易日；日线可回溯多年（spec §9.3：日线至少回补 3 年）
SESSION_START = time(9, 15)
SESSION_END = time(15, 5)


class AKShareEquityHistoricalQueryParams(EquityHistoricalQueryParams):
    interval: Literal["1d", "5m"] = Field(default="1d", description="K 线周期：1d 日线 / 5m 五分钟线")
    # DEFAULT_ADJUSTMENT 在 constants 里是裸 str，这里必须窄化到 Literal，
    # 否则默认值的类型与字段声明不一致（mypy arg-type）。
    adjustment: Literal["qfq", "hfq"] = Field(default="qfq", description="复权方式，固定前复权 qfq")


class AKShareEquityHistoricalData(EquityHistoricalData):
    symbol: str = Field(description="证券代码")
    turnover: float | None = Field(default=None, description="成交额（元）")
    turnover_rate: float | None = Field(default=None, description="换手率（%）")
    timeframe: str = Field(description="1d / 5m")
    adjustment: str = Field(description="复权方式")
    source: str = Field(default=SOURCE_NAME, description="数据来源标识（spec §4.2 必填）")
    source_url: str | None = Field(default=None, description="上游原文页面")
    volume_unit: str | None = Field(default=None, description="成交量单位：hand=手=100 股")
    amount_unit: str | None = Field(default=None, description="成交额单位：CNY")


def _as_date(value: date | datetime | None, fallback: date) -> date:
    if value is None:
        return fallback
    if isinstance(value, datetime):
        return value.astimezone(SHANGHAI).date() if value.tzinfo else value.date()
    return value


class AKShareEquityHistoricalFetcher(
    Fetcher[AKShareEquityHistoricalQueryParams, list[AKShareEquityHistoricalData]]
):
    require_credentials = False

    @staticmethod
    def transform_query(params: dict[str, Any]) -> AKShareEquityHistoricalQueryParams:
        return AKShareEquityHistoricalQueryParams(**params)

    @staticmethod
    async def aextract_data(
        query: AKShareEquityHistoricalQueryParams,
        credentials: dict[str, str] | None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        if "," in query.symbol:
            raise ProviderConfigError("K 线接口一次只支持单个 symbol")
        today = datetime.now(tz=SHANGHAI).date()
        start = _as_date(query.start_date, today - timedelta(days=365 * 3))
        end = _as_date(query.end_date, today)
        if start > end:
            raise ProviderConfigError(f"start_date {start} 晚于 end_date {end}")

        if query.interval == "1d":
            records = await fetch_daily(query.symbol, start, end, query.adjustment)
            return transform_daily(records, query.symbol, query.adjustment)

        start_dt = datetime.combine(start, SESSION_START, tzinfo=SHANGHAI)
        end_dt = datetime.combine(end, SESSION_END, tzinfo=SHANGHAI)
        records = await fetch_minute(query.symbol, start_dt, end_dt, query.adjustment)
        return transform_minute(records, query.symbol, query.adjustment)

    @staticmethod
    def transform_data(
        query: AKShareEquityHistoricalQueryParams,
        data: list[dict[str, Any]],
        **kwargs: Any,
    ) -> list[AKShareEquityHistoricalData]:
        return [AKShareEquityHistoricalData.model_validate(item) for item in data]
