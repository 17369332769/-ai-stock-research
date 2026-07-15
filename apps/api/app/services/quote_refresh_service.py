"""单只股票最新行情刷新编排。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.errors import InstrumentNotFound
from apps.api.app.repositories import instruments as instruments_repo
from apps.api.app.repositories import jobs as jobs_repo
from apps.api.app.schemas.jobs import JobDTO, QuoteRefreshDTO

QUOTE_REFRESH_SOURCE = "eastmoney_via_akshare"
QUOTE_REFRESH_ESTIMATED_SECONDS = 10
QUOTE_REFRESH_COOLDOWN_SECONDS = 30


async def request_quote_refresh(
    session: AsyncSession,
    symbol: str,
    *,
    now: datetime,
) -> QuoteRefreshDTO:
    if await instruments_repo.get(session, symbol) is None:
        raise InstrumentNotFound(symbol)

    job, retry_after = await jobs_repo.enqueue_quote_refresh(
        session,
        symbol,
        now=now,
        cooldown_seconds=QUOTE_REFRESH_COOLDOWN_SECONDS,
    )
    return QuoteRefreshDTO(
        job=JobDTO.from_row(job),
        source=QUOTE_REFRESH_SOURCE,
        estimated_seconds=QUOTE_REFRESH_ESTIMATED_SECONDS,
        retry_after_seconds=retry_after,
        requested_at=job.updated_at,
    )
