"""作业仓储。

**jobs 表就是队列**：API 只负责登记 ``status='queued'`` 的作业行，
由独立的 worker 进程（APScheduler）领取执行 —— 后台采集不得阻塞 API 进程（spec §14.1 / §4.1）。
worker 侧消费者为 ``services/worker/jobs/market_data_jobs.py::run_instrument_backfill``。

幂等：``idempotency_key`` 唯一。同一 symbol 的回补作业复用同一把钥匙，
失败后重新入队（重置进度），而不是堆积一堆孤儿作业（spec §14.2：所有作业幂等）。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.enums import BACKFILL_STEPS, JobStatus, JobType
from apps.api.app.core.runtime import get_clock
from apps.api.app.models.tables import Job

ACTIVE_STATUSES = (JobStatus.QUEUED.value, JobStatus.RUNNING.value)


def backfill_key(symbol: str) -> str:
    return f"{JobType.INSTRUMENT_BACKFILL.value}:{symbol}"


def analysis_refresh_key(symbol: str) -> str:
    return f"{JobType.ANALYSIS_REFRESH.value}:{symbol}"


async def get(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    result = await session.execute(select(Job).where(Job.id == job_id))
    return result.scalar_one_or_none()


async def get_by_key(session: AsyncSession, idempotency_key: str) -> Job | None:
    result = await session.execute(select(Job).where(Job.idempotency_key == idempotency_key))
    return result.scalar_one_or_none()


async def active_backfill(session: AsyncSession, symbol: str) -> Job | None:
    """queued/running 的回补作业。预测缺失时用它决定是否返回 202（spec §7）。"""
    result = await session.execute(
        select(Job)
        .where(
            Job.symbol == symbol,
            Job.job_type == JobType.INSTRUMENT_BACKFILL.value,
            Job.status.in_(ACTIVE_STATUSES),
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def succeeded_backfill(session: AsyncSession, symbol: str) -> Job | None:
    result = await session.execute(
        select(Job)
        .where(
            Job.symbol == symbol,
            Job.job_type == JobType.INSTRUMENT_BACKFILL.value,
            Job.status == JobStatus.SUCCEEDED.value,
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def enqueue(
    session: AsyncSession,
    *,
    job_type: JobType,
    symbol: str | None,
    total_steps: int,
    first_step: str | None,
    idempotency_key: str,
) -> Job:
    """登记或重新入队一个作业（幂等）。

    - 不存在 ⇒ 新建 queued；
    - 已存在且 queued/running ⇒ 原样返回（不重复排队）；
    - 已存在但 succeeded/failed ⇒ 重置进度并重新入队。
    """
    existing = await get_by_key(session, idempotency_key)
    if existing is not None:
        if existing.status in ACTIVE_STATUSES:
            return existing
        existing.status = JobStatus.QUEUED.value
        existing.completed_steps = 0
        existing.current_step = first_step
        existing.warnings = []
        existing.error_code = None
        existing.error_message = None
        existing.started_at = None
        existing.finished_at = None
        # jobs.updated_at 只有 server_default（INSERT 时生效），没有 onupdate；
        # 不显式赋值的话，重新入队后 GET /jobs/{id} 会一直显示旧的创建时间。
        existing.updated_at = get_clock().now()
        await session.flush()
        return existing

    job = Job(
        id=uuid.uuid4(),
        job_type=job_type.value,
        symbol=symbol,
        status=JobStatus.QUEUED.value,
        completed_steps=0,
        total_steps=total_steps,
        current_step=first_step,
        warnings=[],
        idempotency_key=idempotency_key,
    )
    session.add(job)
    await session.flush()
    await session.refresh(job)  # 取回 created_at/updated_at 的服务器默认值
    return job


async def enqueue_backfill(session: AsyncSession, symbol: str) -> Job:
    """三步固定：daily_bars → minute_bars → documents（spec §7.1）。"""
    return await enqueue(
        session,
        job_type=JobType.INSTRUMENT_BACKFILL,
        symbol=symbol,
        total_steps=len(BACKFILL_STEPS),
        first_step=BACKFILL_STEPS[0],
        idempotency_key=backfill_key(symbol),
    )


async def enqueue_analysis_refresh(session: AsyncSession, symbol: str) -> Job:
    return await enqueue(
        session,
        job_type=JobType.ANALYSIS_REFRESH,
        symbol=symbol,
        total_steps=1,
        first_step="analyze",
        idempotency_key=analysis_refresh_key(symbol),
    )
