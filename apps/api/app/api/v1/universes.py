"""股票池路由（spec §7.1）。"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from apps.api.app.api.v1.deps import (
    CursorQuery,
    LimitQuery,
    NowDep,
    RequestIdDep,
    SessionDep,
    resolve_as_of,
    resolve_cursor,
    resolve_limit,
)
from apps.api.app.api.v1.errors_doc import error_responses
from apps.api.app.core.enums import CSI300_CODE
from apps.api.app.repositories.instruments import MEMBER_SORT_KEY
from apps.api.app.schemas.common import ListResponse, PageInfo
from apps.api.app.schemas.instruments import InstrumentDTO
from apps.api.app.services import universe_service

router = APIRouter(tags=["universes"])


@router.get(
    "/universes/CSI300/instruments",
    response_model=ListResponse[InstrumentDTO],
    responses=error_responses(400, 424),
    summary="指定日期有效的沪深300成分",
)
async def list_csi300_instruments(
    session: SessionDep,
    now: NowDep,
    request_id: RequestIdDep,
    as_of: Annotated[date | None, Query(description="缺省为查询日；返回该日期真实有效的成分")] = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = None,
) -> ListResponse[InstrumentDTO]:
    items, next_cursor, has_more = await universe_service.list_universe_members(
        session,
        CSI300_CODE,
        resolve_as_of(as_of, now),
        limit=resolve_limit(limit),
        cursor=resolve_cursor(cursor, expected_sort=MEMBER_SORT_KEY),
    )
    return ListResponse[InstrumentDTO](
        data=items,
        page=PageInfo(
            next_cursor=next_cursor.encode() if next_cursor else None, has_more=has_more
        ),
        request_id=request_id,
    )
