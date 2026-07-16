"""证券搜索路由（spec §7.1）。

只搜索**查询日**沪深300当前成分，精确代码匹配优先。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from apps.api.app.api.v1.deps import LimitQuery, NowDep, RequestIdDep, SessionDep, resolve_limit
from apps.api.app.api.v1.errors_doc import error_responses
from apps.api.app.core.clock import to_shanghai
from apps.api.app.schemas.common import ListResponse, PageInfo
from apps.api.app.schemas.instruments import InstrumentDTO
from apps.api.app.services import universe_service

router = APIRouter(tags=["instruments"])


@router.get(
    "/instruments/search",
    response_model=ListResponse[InstrumentDTO],
    responses=error_responses(400, 424),
    summary="在当前沪深300成分中搜索（代码或名称）",
)
async def search_instruments(
    session: SessionDep,
    now: NowDep,
    request_id: RequestIdDep,
    q: Annotated[str, Query(min_length=1, max_length=80, description="代码或公司名称")],
    universe: Annotated[Literal["CSI300"], Query(description="MVP 只支持 CSI300")] = "CSI300",
    scope: Annotated[
        Literal["csi300", "all"],
        Query(description="csi300=当前成分；all=本地已知A股（用于额外自选）"),
    ] = "csi300",
    limit: LimitQuery = 20,
) -> ListResponse[InstrumentDTO]:
    as_of = to_shanghai(now).date()
    if scope == "all":
        items = await universe_service.search_known_instruments(
            session, q, as_of, resolve_limit(limit)
        )
    else:
        items = await universe_service.search_instruments(
            session, universe, q, as_of, resolve_limit(limit)
        )
    # 搜索是"取前 N 条最相关"，不做游标分页
    return ListResponse[InstrumentDTO](
        data=items,
        page=PageInfo(next_cursor=None, has_more=False),
        request_id=request_id,
    )
