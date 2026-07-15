"""自选股仓储。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, literal, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.errors import InvalidArgument
from apps.api.app.core.pagination import Cursor
from apps.api.app.models.tables import Instrument, WatchlistItem

WATCHLIST_SORT_KEY = "display_order"


async def list_items(session: AsyncSession) -> list[WatchlistItem]:
    result = await session.execute(
        select(WatchlistItem).order_by(WatchlistItem.display_order.asc(), WatchlistItem.id.asc())
    )
    return list(result.scalars().all())


def _parse_page_cursor(cursor: Cursor) -> tuple[int, int]:
    try:
        display_order = int(cursor.value)
        row_id = int(cursor.id)
    except ValueError as exc:
        raise InvalidArgument("自选股游标字段无效") from exc
    return display_order, row_id


async def list_page(
    session: AsyncSession,
    *,
    limit: int,
    cursor: Cursor | None,
    query: str | None,
) -> tuple[list[WatchlistItem], bool]:
    """按固定展示顺序读取一页；搜索在数据库中覆盖全部自选股。"""
    stmt = (
        select(WatchlistItem)
        .join(Instrument, Instrument.symbol == WatchlistItem.symbol)
        .order_by(WatchlistItem.display_order.asc(), WatchlistItem.id.asc())
        .limit(limit + 1)
    )
    normalized = (query or "").strip()
    if normalized:
        stmt = stmt.where(
            or_(
                Instrument.symbol.contains(normalized, autoescape=True),
                Instrument.name.contains(normalized, autoescape=True),
            )
        )
    if cursor is not None:
        display_order, row_id = _parse_page_cursor(cursor)
        stmt = stmt.where(
            tuple_(WatchlistItem.display_order, WatchlistItem.id)
            > tuple_(literal(display_order), literal(row_id))
        )

    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    return rows[:limit], has_more


def build_page_cursor(row: WatchlistItem) -> Cursor:
    return Cursor(sort=WATCHLIST_SORT_KEY, value=str(row.display_order), id=str(row.id))


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
