"""作业路由（spec §7.1）。状态只允许 queued|running|succeeded|failed。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from apps.api.app.api.v1.deps import RequestIdDep, SessionDep
from apps.api.app.api.v1.errors_doc import error_responses
from apps.api.app.core.errors import AppError, ErrorCode
from apps.api.app.repositories import jobs as jobs_repo
from apps.api.app.schemas.common import ItemResponse
from apps.api.app.schemas.jobs import JobDTO

router = APIRouter(tags=["jobs"])


@router.get(
    "/jobs/{job_id}",
    response_model=ItemResponse[JobDTO],
    responses=error_responses(400, 404),
    summary="查询作业进度",
)
async def get_job(job_id: uuid.UUID, session: SessionDep, request_id: RequestIdDep) -> ItemResponse[JobDTO]:
    job = await jobs_repo.get(session, job_id)
    if job is None:
        # spec §7 的错误表没有 JOB_NOT_FOUND；404 只有 INSTRUMENT_NOT_FOUND 一个码。
        # 这里复用 404 而不是自造错误码（见交付说明中的 spec 缺口）。
        raise AppError(ErrorCode.INSTRUMENT_NOT_FOUND, f"作业 {job_id} 不存在")
    return ItemResponse[JobDTO](data=JobDTO.from_row(job), request_id=request_id)
