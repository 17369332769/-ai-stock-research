"""JobDTO（spec §7.1）。

状态只允许 ``queued|running|succeeded|failed``。
回补的三个步骤固定为 ``daily_bars``、``minute_bars``、``documents``；
分钟数据不可获得时该步骤记录 warning，**不使整项回补失败**（spec §7.1）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from apps.api.app.core.enums import JobStatus, JobType
from apps.api.app.models.tables import Job
from apps.api.app.schemas.common import BaseDTO


class JobDTO(BaseDTO):
    id: uuid.UUID
    job_type: JobType
    symbol: str | None = None
    status: JobStatus
    completed_steps: int
    total_steps: int
    current_step: str | None = None
    warnings: list[Any] = Field(
        default_factory=list, description="非致命告警，例如分钟线不可得（spec §7.1）"
    )
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Job) -> JobDTO:
        return cls(
            id=row.id,
            job_type=JobType(row.job_type),
            symbol=row.symbol,
            status=JobStatus(row.status),
            completed_steps=row.completed_steps,
            total_steps=row.total_steps,
            current_step=row.current_step,
            warnings=list(row.warnings or []),
            error_code=row.error_code,
            error_message=row.error_message,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            updated_at=row.updated_at,
        )


class PendingBackfillDTO(BaseDTO):
    """预测不存在但回补仍在进行时的 202 响应体（spec §7）。

    键名与 ``POST /watchlist`` 保持一致，前端一套渲染逻辑即可。
    """

    backfill_job: JobDTO
