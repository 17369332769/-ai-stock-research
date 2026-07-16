"""002：自动沪深300研究池、额外自选与合并报价。"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response, status

from apps.api.app.api.v1.deps import NowDep, RequestIdDep, SessionDep, SymbolPath
from apps.api.app.api.v1.errors_doc import error_responses
from apps.api.app.schemas.common import ItemResponse, ListResponse, PageInfo
from apps.api.app.schemas.quotes import QuoteDTO
from apps.api.app.schemas.watchlist import AddWatchlistRequest, WatchlistAddedDTO, WatchlistItemDTO
from apps.api.app.services import research_pool_service, watchlist_service

router = APIRouter(tags=["research-pool"])


@router.get(
    "/research-pool",
    response_model=ListResponse[WatchlistItemDTO],
    responses=error_responses(424),
    summary="沪深300自动研究池、额外自选或合并范围",
)
async def get_research_pool(
    session: SessionDep,
    now: NowDep,
    request_id: RequestIdDep,
    scope: Annotated[Literal["csi300", "extra", "all"], Query()] = "all",
    q: Annotated[str | None, Query(max_length=80)] = None,
) -> ListResponse[WatchlistItemDTO]:
    rows = await research_pool_service.list_research_pool(session, now, scope=scope, query=q)
    return ListResponse[WatchlistItemDTO](
        data=rows,
        page=PageInfo(next_cursor=None, has_more=False),
        request_id=request_id,
    )


@router.get(
    "/quotes/latest",
    response_model=ListResponse[QuoteDTO],
    responses=error_responses(424),
    summary="批量读取研究范围的最后成功报价",
)
async def get_latest_quotes(
    session: SessionDep,
    now: NowDep,
    request_id: RequestIdDep,
    scope: Annotated[Literal["csi300", "extra", "all"], Query()] = "all",
) -> ListResponse[QuoteDTO]:
    rows = await research_pool_service.latest_quotes_for_scope(session, now, scope=scope)
    return ListResponse[QuoteDTO](
        data=rows,
        page=PageInfo(next_cursor=None, has_more=False),
        request_id=request_id,
    )


@router.post(
    "/extra-watchlist",
    response_model=ItemResponse[WatchlistAddedDTO],
    status_code=status.HTTP_202_ACCEPTED,
    responses={201: {"model": ItemResponse[WatchlistAddedDTO]}, **error_responses(400, 404, 409)},
    summary="添加非沪深300股票到额外自选",
)
async def add_extra_watchlist(
    payload: AddWatchlistRequest,
    session: SessionDep,
    now: NowDep,
    request_id: RequestIdDep,
    response: Response,
) -> ItemResponse[WatchlistAddedDTO]:
    result = await research_pool_service.add_extra_watchlist(session, payload.symbol, now)
    await session.commit()
    response.status_code = result.status_code
    return ItemResponse[WatchlistAddedDTO](data=result.payload, request_id=request_id)


@router.delete(
    "/extra-watchlist/{symbol}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(404),
    summary="移除额外自选（不删除任何历史研究数据）",
)
async def delete_extra_watchlist(symbol: SymbolPath, session: SessionDep) -> Response:
    await watchlist_service.remove_from_watchlist(session, symbol)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
