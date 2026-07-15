"""OpenBB 标准模型 ``IndexConstituents`` 的中证指数实现。

REST 路由：``GET /api/v1/index/constituents?provider=csi300&symbol=000300&as_of=2026-07-14``

``as_of`` 是 provider 特有参数（标准模型没有）：

- ``as_of`` 缺省或 >= 今天 → 官方当期成分（选股用）
- ``as_of`` < 今天         → 官方历史快照（训练用）；快照缺失直接报错
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.index_constituents import (
    IndexConstituentsData,
    IndexConstituentsQueryParams,
)
from pydantic import Field

from ..client import get_current_constituents, get_snapshot_constituents
from ..constants import INDEX_CODE, SHANGHAI, SOURCE_NAME, UNIVERSE_CODE, ProviderConfigError


class Csi300IndexConstituentsQueryParams(IndexConstituentsQueryParams):
    as_of: date | None = Field(
        default=None,
        description="成分生效日期。缺省=当期成分；历史日期读官方历史快照（无快照则报错）",
    )


class Csi300IndexConstituentsData(IndexConstituentsData):
    name: str | None = Field(default=None, description="证券简称")
    exchange: str = Field(description="SSE / SZSE")
    index_code: str = Field(default=INDEX_CODE, description="指数代码")
    universe: str = Field(default=UNIVERSE_CODE, description="股票池代码")
    as_of: date = Field(description="请求的成分生效日期")
    snapshot_date: date = Field(description="该成分表的官方生效日期（可能早于 as_of）")
    source: str = Field(default=SOURCE_NAME, description="权威来源：中证指数")
    source_url: str = Field(description="官方成分文件地址")
    observed_at: datetime = Field(description="采集时刻（spec §4.2 必填）")


class Csi300IndexConstituentsFetcher(
    Fetcher[Csi300IndexConstituentsQueryParams, list[Csi300IndexConstituentsData]]
):
    require_credentials = False

    @staticmethod
    def transform_query(params: dict[str, Any]) -> Csi300IndexConstituentsQueryParams:
        query = Csi300IndexConstituentsQueryParams(**params)
        symbol = (query.symbol or "").strip().upper().removeprefix("SH").split(".")[0]
        if symbol and symbol != INDEX_CODE:
            raise ProviderConfigError(
                f"csi300 Provider 只提供沪深300（{INDEX_CODE}）成分，收到 symbol={query.symbol!r}"
            )
        return query

    @staticmethod
    async def aextract_data(
        query: Csi300IndexConstituentsQueryParams,
        credentials: dict[str, str] | None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        now = datetime.now(tz=SHANGHAI)
        today = now.date()
        as_of = query.as_of or today
        if as_of >= today:
            return await get_current_constituents(as_of, observed_at=now)
        return get_snapshot_constituents(as_of, observed_at=now)

    @staticmethod
    def transform_data(
        query: Csi300IndexConstituentsQueryParams,
        data: list[dict[str, Any]],
        **kwargs: Any,
    ) -> list[Csi300IndexConstituentsData]:
        return [Csi300IndexConstituentsData.model_validate(item) for item in data]
