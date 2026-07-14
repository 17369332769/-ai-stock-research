"""行情仓储。只读最后一条快照；是否 stale 由 services/freshness 判定，不在 SQL 里写死。"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.models.tables import Quote


async def latest(session: AsyncSession, symbol: str) -> Quote | None:
    """最后一条行情。返回 None ⇒ **从未取得行情** ⇒ 调用方必须 424，不得编造价格。"""
    result = await session.execute(
        select(Quote).where(Quote.symbol == symbol).order_by(Quote.observed_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def latest_many(session: AsyncSession, symbols: Sequence[str]) -> dict[str, Quote]:
    """批量取每只股票的最后一条行情（自选股表格一次查完）。"""
    if not symbols:
        return {}

    # DISTINCT ON 是 PostgreSQL 原生写法：按 symbol 分组取 observed_at 最大的一行
    stmt = (
        select(Quote)
        .where(Quote.symbol.in_(list(symbols)))
        .order_by(Quote.symbol.asc(), Quote.observed_at.desc())
        .distinct(Quote.symbol)
    )
    result = await session.execute(stmt)
    return {row.symbol: row for row in result.scalars().all()}
