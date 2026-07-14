"""自选股仓储。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.models.tables import WatchlistItem


async def list_items(session: AsyncSession) -> list[WatchlistItem]:
    result = await session.execute(
        select(WatchlistItem).order_by(WatchlistItem.display_order.asc(), WatchlistItem.id.asc())
    )
    return list(result.scalars().all())


async def get(session: AsyncSession, symbol: str) -> WatchlistItem | None:
    result = await session.execute(select(WatchlistItem).where(WatchlistItem.symbol == symbol))
    return result.scalar_one_or_none()


async def next_display_order(session: AsyncSession) -> int:
    result = await session.execute(select(func.max(WatchlistItem.display_order)))
    current_max = result.scalar_one_or_none()
    return 0 if current_max is None else int(current_max) + 1


async def add(session: AsyncSession, symbol: str, universe_code: str, display_order: int) -> WatchlistItem:
    item = WatchlistItem(symbol=symbol, universe_code=universe_code, display_order=display_order)
    session.add(item)
    await session.flush()  # 触发 UNIQUE(symbol) —— 并发重复添加在这里被拦下
    await session.refresh(item)  # 取回 created_at 的服务器默认值
    return item


async def remove(session: AsyncSession, symbol: str) -> int:
    result = await session.execute(delete(WatchlistItem).where(WatchlistItem.symbol == symbol))
    return cast("CursorResult[Any]", result).rowcount


async def reorder(session: AsyncSession, symbols: Sequence[str]) -> list[WatchlistItem]:
    """按给定顺序重排。调用方必须先校验 symbols 是当前自选股的全排列。"""
    items = await list_items(session)
    by_symbol = {item.symbol: item for item in items}
    for order, symbol in enumerate(symbols):
        by_symbol[symbol].display_order = order
    await session.flush()
    return await list_items(session)
