"""自选股路由（spec §7.1）。

``POST /watchlist``：首次添加返回 **202** + ``backfill_job``；
成员资格校验与插入在**同一事务**内完成（已调出 ⇒ 409 NOT_CURRENT_UNIVERSE_MEMBER）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from apps.api.app.api.v1.deps import CursorQuery, NowDep, RequestIdDep, SessionDep, SymbolPath, resolve_cursor
from apps.api.app.api.v1.errors_doc import error_responses
from apps.api.app.repositories.watchlist import WATCHLIST_SORT_KEY
from apps.api.app.schemas.common import ItemResponse, ListResponse, PageInfo
from apps.api.app.schemas.watchlist import (
    AddWatchlistRequest,
    ReorderWatchlistRequest,
    WatchlistAddedDTO,
    WatchlistItemDTO,
)
from apps.api.app.services import watchlist_service

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get(
    "",
    response_model=ListResponse[WatchlistItemDTO],
    summary="自选股列表（含最新行情与新鲜度）",
)
async def get_watchlist(
    session: SessionDep,
    now: NowDep,
    request_id: RequestIdDep,
    cursor: CursorQuery = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    q: Annotated[str | None, Query(max_length=80)] = None,
) -> ListResponse[WatchlistItemDTO]:
    items, next_cursor, has_more = await watchlist_service.list_watchlist_page(
        session,
        now,
        limit=limit,
        cursor=resolve_cursor(cursor, expected_sort=WATCHLIST_SORT_KEY),
        query=q,
    )
    return ListResponse[WatchlistItemDTO](
        data=items,
        page=PageInfo(
            next_cursor=next_cursor.encode() if next_cursor else None,
            has_more=has_more,
        ),
        request_id=request_id,
    )


@router.post(
    "",
    response_model=ItemResponse[WatchlistAddedDTO],
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        201: {
            "model": ItemResponse[WatchlistAddedDTO],
            "description": "此前已成功回补过该股票，无需新作业（backfill_job=null）",
        },
        **error_responses(400, 404, 409),
    },
    summary="添加自选股（首次添加返回 202 + 回补任务）",
)
async def add_watchlist_item(
    payload: AddWatchlistRequest,
    session: SessionDep,
    now: NowDep,
    request_id: RequestIdDep,
    response: Response,
) -> ItemResponse[WatchlistAddedDTO]:
    result = await watchlist_service.add_to_watchlist(session, payload.symbol, now)
    await session.commit()

    # 首次添加（回补作业刚入队）⇒ 202；此前已成功回补过 ⇒ 201（无新作业）
    response.status_code = result.status_code
    return ItemResponse[WatchlistAddedDTO](data=result.payload, request_id=request_id)


@router.delete(
    "/{symbol}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(400, 404),
    summary="删除自选股",
)
async def delete_watchlist_item(symbol: SymbolPath, session: SessionDep) -> Response:
    await watchlist_service.remove_from_watchlist(session, symbol)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/order",
    response_model=ListResponse[WatchlistItemDTO],
    responses=error_responses(400),
    summary="重排自选股（symbols 必须是当前自选股的全排列）",
)
async def reorder_watchlist(
    payload: ReorderWatchlistRequest,
    session: SessionDep,
    now: NowDep,
    request_id: RequestIdDep,
) -> ListResponse[WatchlistItemDTO]:
    items = await watchlist_service.reorder_watchlist(session, payload.symbols, now)
    await session.commit()
    return ListResponse[WatchlistItemDTO](
        data=items,
        page=PageInfo(next_cursor=None, has_more=False),
        request_id=request_id,
    )
