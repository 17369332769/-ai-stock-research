"""Worker 统一跟踪范围：当前沪深300 ∪ 额外自选，按代码去重。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import or_, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.enums import CSI300_CODE
from apps.api.app.models.tables import UniverseMembership, WatchlistItem


async def tracking_symbols(session: AsyncSession, as_of: date) -> list[str]:
    members = select(UniverseMembership.symbol).where(
        UniverseMembership.universe_code == CSI300_CODE,
        UniverseMembership.effective_from <= as_of,
        or_(
            UniverseMembership.effective_to.is_(None),
            UniverseMembership.effective_to >= as_of,
        ),
    )
    extras = select(WatchlistItem.symbol)
    stmt = union(members, extras).order_by("symbol")
    return list((await session.execute(stmt)).scalars().all())
