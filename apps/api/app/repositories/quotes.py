"""行情仓储。只读最后一条快照；是否 stale 由 services/freshness 判定，不在 SQL 里写死。"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.models.tables import LatestQuote, Quote

QuoteRow = LatestQuote | Quote


async def latest(session: AsyncSession, symbol: str) -> QuoteRow | None:
    """最后一条实时行情；从未取得时返回 None。"""
    current = await session.get(LatestQuote, symbol)
    if current is not None:
        return current
    result = await session.execute(
        select(Quote).where(Quote.symbol == symbol).order_by(Quote.observed_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def latest_many(session: AsyncSession, symbols: Sequence[str]) -> dict[str, QuoteRow]:
    """批量取每只股票的最后一条行情（自选股表格一次查完）。"""
    if not symbols:
        return {}

    latest_result = await session.execute(
        select(LatestQuote).where(LatestQuote.symbol.in_(list(symbols)))
    )
    rows: dict[str, QuoteRow] = {row.symbol: row for row in latest_result.scalars().all()}
    missing = sorted(set(symbols) - set(rows))
    if not missing:
        return rows

    # 兼容迁移前数据和测试夹具：latest_quotes 尚无行时才回退到历史快照。
    stmt = (
        select(Quote)
        .where(Quote.symbol.in_(missing))
        .order_by(Quote.symbol.asc(), Quote.observed_at.desc())
        .distinct(Quote.symbol)
    )
    result = await session.execute(stmt)
    rows.update({row.symbol: row for row in result.scalars().all()})
    return rows
