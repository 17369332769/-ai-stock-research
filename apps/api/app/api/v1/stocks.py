"""股票研究页路由：快照、文档、解释、历史相似行情（spec §7.2 / §7.3 / §7.5）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from apps.api.app.api.v1.deps import (
    AnalogFinderDep,
    CursorQuery,
    LimitQuery,
    NowDep,
    RequestIdDep,
    SessionDep,
    SymbolPath,
    resolve_cursor,
    resolve_limit,
)
from apps.api.app.api.v1.errors_doc import error_responses
from apps.api.app.core.enums import AnalysisType, DocumentType, PredictionHorizon, Timeframe
from apps.api.app.repositories.analyses import ANALYSIS_SORT_KEY
from apps.api.app.repositories.documents import DOCUMENT_SORT_KEY
from apps.api.app.schemas.analogs import AnalogDTO
from apps.api.app.schemas.analyses import AnalysisDTO
from apps.api.app.schemas.bars import BarsResponse
from apps.api.app.schemas.common import ItemResponse, ListResponse, PageInfo
from apps.api.app.schemas.documents import DocumentDTO
from apps.api.app.schemas.jobs import JobDTO, QuoteRefreshDTO
from apps.api.app.schemas.quotes import SnapshotResponse
from apps.api.app.services import (
    analog_service,
    history_service,
    quote_refresh_service,
    research_service,
    snapshot_service,
)

router = APIRouter(prefix="/stocks/{symbol}", tags=["stocks"])


@router.get(
    "/snapshot",
    response_model=SnapshotResponse,
    responses=error_responses(400, 404),
    summary="股票快照（行情、新鲜度、相对强弱、最新异动与预测）",
)
async def get_snapshot(
    symbol: SymbolPath, session: SessionDep, now: NowDep, request_id: RequestIdDep
) -> SnapshotResponse:
    """行情过期时返回 stale；尚无实时行情时返回 200 + quote=null。"""
    snapshot = await snapshot_service.get_snapshot(session, symbol, now)
    return SnapshotResponse(**snapshot.model_dump(), request_id=request_id)


@router.post(
    "/quote-refresh",
    response_model=ItemResponse[QuoteRefreshDTO],
    status_code=status.HTTP_202_ACCEPTED,
    responses=error_responses(400, 404),
    summary="为当前股票登记一次最新行情刷新任务",
)
async def refresh_quote(
    symbol: SymbolPath,
    session: SessionDep,
    now: NowDep,
    request_id: RequestIdDep,
) -> ItemResponse[QuoteRefreshDTO]:
    result = await quote_refresh_service.request_quote_refresh(session, symbol, now=now)
    await session.commit()
    return ItemResponse[QuoteRefreshDTO](data=result, request_id=request_id)


@router.get(
    "/bars",
    response_model=BarsResponse,
    responses=error_responses(400, 404),
    summary="历史 K 线（与实时行情独立）",
)
async def list_bars(
    symbol: SymbolPath,
    session: SessionDep,
    now: NowDep,
    request_id: RequestIdDep,
    timeframe: Annotated[Timeframe, Query()] = Timeframe.DAY1,
    limit: Annotated[int, Query(ge=1, le=1000)] = 240,
) -> BarsResponse:
    rows, meta = await history_service.list_bars(
        session,
        symbol,
        timeframe=timeframe,
        limit=limit,
        now=now,
    )
    return BarsResponse(
        data=rows,
        page=PageInfo(next_cursor=None, has_more=False),
        meta=meta,
        request_id=request_id,
    )


@router.get(
    "/documents",
    response_model=ListResponse[DocumentDTO],
    responses=error_responses(400, 404),
    summary="公告与新闻（按内容哈希去重）",
)
async def list_documents(
    symbol: SymbolPath,
    session: SessionDep,
    request_id: RequestIdDep,
    type: Annotated[DocumentType | None, Query(description="announcement | news")] = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = None,
) -> ListResponse[DocumentDTO]:
    items, next_cursor, has_more = await research_service.list_documents(
        session,
        symbol,
        document_type=type,
        limit=resolve_limit(limit),
        cursor=resolve_cursor(cursor, expected_sort=DOCUMENT_SORT_KEY),
    )
    return ListResponse[DocumentDTO](
        data=items,
        page=PageInfo(next_cursor=next_cursor.encode() if next_cursor else None, has_more=has_more),
        request_id=request_id,
    )


@router.get(
    "/analyses",
    response_model=ListResponse[AnalysisDTO],
    responses=error_responses(400, 404),
    summary="AI 解释（证据整条展开，绝不返回部分证据）",
)
async def list_analyses(
    symbol: SymbolPath,
    session: SessionDep,
    request_id: RequestIdDep,
    type: Annotated[AnalysisType | None, Query(description="document | anomaly | daily_brief")] = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = None,
) -> ListResponse[AnalysisDTO]:
    items, next_cursor, has_more = await research_service.list_analyses(
        session,
        symbol,
        analysis_type=type,
        limit=resolve_limit(limit),
        cursor=resolve_cursor(cursor, expected_sort=ANALYSIS_SORT_KEY),
    )
    return ListResponse[AnalysisDTO](
        data=items,
        page=PageInfo(next_cursor=next_cursor.encode() if next_cursor else None, has_more=has_more),
        request_id=request_id,
    )


@router.post(
    "/analyses/refresh",
    response_model=ItemResponse[JobDTO],
    status_code=status.HTTP_202_ACCEPTED,
    responses=error_responses(400, 404),
    summary="触发解释刷新（登记 analysis_refresh 作业，由 worker 执行）",
)
async def refresh_analyses(
    symbol: SymbolPath, session: SessionDep, request_id: RequestIdDep
) -> ItemResponse[JobDTO]:
    job = await research_service.refresh_analysis(session, symbol)
    await session.commit()
    return ItemResponse[JobDTO](data=job, request_id=request_id)


@router.get(
    "/analogs",
    response_model=ListResponse[AnalogDTO],
    responses=error_responses(400, 404, 422),
    summary="历史相似行情（有效候选 < 30 时关闭该功能 ⇒ 422）",
)
async def list_analogs(
    symbol: SymbolPath,
    session: SessionDep,
    now: NowDep,
    request_id: RequestIdDep,
    finder: AnalogFinderDep,
    horizon: Annotated[PredictionHorizon, Query()] = PredictionHorizon.NEXT_5D,
    limit: LimitQuery = 10,
) -> ListResponse[AnalogDTO]:
    items = await analog_service.get_analogs(
        session, finder, symbol, horizon, limit=resolve_limit(limit), now=now
    )
    return ListResponse[AnalogDTO](
        data=items,
        page=PageInfo(next_cursor=None, has_more=False),
        request_id=request_id,
    )
