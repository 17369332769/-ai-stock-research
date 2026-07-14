"""预测与成绩单路由（spec §7.4）。"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse

from apps.api.app.api.v1.deps import (
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
from apps.api.app.core.enums import PredictionHorizon
from apps.api.app.core.middleware import REQUEST_ID_HEADER
from apps.api.app.repositories.predictions import PREDICTION_SORT_KEY
from apps.api.app.schemas.common import ItemResponse, ListResponse, PageInfo
from apps.api.app.schemas.jobs import PendingBackfillDTO
from apps.api.app.schemas.predictions import PredictionDTO, PredictionResponse, ScorecardDTO
from apps.api.app.services import prediction_service, scorecard_service

router = APIRouter(tags=["predictions"])


@router.get(
    "/stocks/{symbol}/predictions/latest",
    response_model=None,
    responses={
        200: {"model": PredictionResponse, "description": "最新预测"},
        202: {
            "model": ItemResponse[PendingBackfillDTO],
            "description": "预测不存在但回补仍在进行中",
        },
        **error_responses(400, 404, 422, 503),
    },
    summary="最新预测（今日预测 09:45 前不可用）",
)
async def get_latest_prediction(
    symbol: SymbolPath,
    session: SessionDep,
    now: NowDep,
    request_id: RequestIdDep,
    horizon: Annotated[PredictionHorizon, Query()] = PredictionHorizon.NEXT_5D,
) -> PredictionResponse | JSONResponse:
    result = await prediction_service.get_latest(session, symbol, horizon, now)

    if result.pending_backfill is not None:
        body = ItemResponse[PendingBackfillDTO](
            data=PendingBackfillDTO(backfill_job=result.pending_backfill),
            request_id=request_id,
        )
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=body.model_dump(mode="json"),
            headers={REQUEST_ID_HEADER: request_id},
        )

    assert result.prediction is not None
    return PredictionResponse(**result.prediction.model_dump(), request_id=request_id)


@router.get(
    "/stocks/{symbol}/predictions/history",
    response_model=ListResponse[PredictionDTO],
    responses=error_responses(400, 404, 503),
    summary="历史预测（只追加，永不覆盖）",
)
async def get_prediction_history(
    symbol: SymbolPath,
    session: SessionDep,
    request_id: RequestIdDep,
    horizon: Annotated[PredictionHorizon, Query()] = PredictionHorizon.NEXT_5D,
    cursor: CursorQuery = None,
    limit: LimitQuery = None,
) -> ListResponse[PredictionDTO]:
    items, next_cursor, has_more = await prediction_service.get_history(
        session,
        symbol,
        horizon,
        limit=resolve_limit(limit),
        cursor=resolve_cursor(cursor, expected_sort=PREDICTION_SORT_KEY),
    )
    return ListResponse[PredictionDTO](
        data=items,
        page=PageInfo(next_cursor=next_cursor.encode() if next_cursor else None, has_more=has_more),
        request_id=request_id,
    )


@router.get(
    "/models/{model_key}/scorecard",
    response_model=ItemResponse[ScorecardDTO],
    responses=error_responses(400, 422, 503),
    summary="模型成绩单（settled + pending = eligible；未到目标时间的预测不进分母）",
)
async def get_scorecard(
    model_key: str,
    session: SessionDep,
    now: NowDep,
    request_id: RequestIdDep,
    window: Annotated[Literal["20", "100", "all"], Query()] = "all",
) -> ItemResponse[ScorecardDTO]:
    scorecard = await scorecard_service.get_scorecard(session, model_key, window, now)
    return ItemResponse[ScorecardDTO](data=scorecard, request_id=request_id)
