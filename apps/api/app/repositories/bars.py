"""历史行情仓储。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.models.tables import Bar


async def recent(
    session: AsyncSession,
    symbol: str,
    *,
    timeframe: str,
    cutoff: datetime,
    limit: int,
) -> list[Bar]:
    """读取截止时刻之前最近的 K 线，并按时间正序返回。"""
    rows = list(
        (
            await session.execute(
                select(Bar)
                .where(
                    Bar.symbol == symbol,
                    Bar.timeframe == timeframe,
                    Bar.bar_time <= cutoff,
                )
                .order_by(Bar.bar_time.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    rows.reverse()
    return rows
