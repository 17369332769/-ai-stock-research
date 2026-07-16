"""证券与指数成分仓储。

成员资格永远按**当时有效期**判定，禁止用当前 300 只回填历史（spec §9.3 / 验收 §15.16）：

    effective_from <= as_of AND (effective_to IS NULL OR effective_to >= as_of)
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.pagination import Cursor
from apps.api.app.models.tables import Instrument, UniverseMembership

MEMBER_SORT_KEY = "symbol"


def _membership_active(as_of: date) -> Select[tuple[str]]:
    """给定日期有效的成分 symbol 子查询。"""
    return select(UniverseMembership.symbol).where(
        UniverseMembership.effective_from <= as_of,
        or_(UniverseMembership.effective_to.is_(None), UniverseMembership.effective_to >= as_of),
    )


async def get(session: AsyncSession, symbol: str) -> Instrument | None:
    result = await session.execute(select(Instrument).where(Instrument.symbol == symbol))
    return result.scalar_one_or_none()


async def get_many(session: AsyncSession, symbols: Sequence[str]) -> dict[str, Instrument]:
    """批量取证券（自选股表格一次查完，避免 N+1）。"""
    if not symbols:
        return {}
    result = await session.execute(select(Instrument).where(Instrument.symbol.in_(list(symbols))))
    return {row.symbol: row for row in result.scalars().all()}


async def is_current_member(
    session: AsyncSession, symbol: str, universe_code: str, as_of: date
) -> bool:
    """POST /watchlist 的成员资格校验必须与插入在**同一事务**中执行（spec §7.1）。"""
    stmt = _membership_active(as_of).where(
        UniverseMembership.universe_code == universe_code,
        UniverseMembership.symbol == symbol,
    )
    result = await session.execute(select(func.count()).select_from(stmt.subquery()))
    return (result.scalar_one() or 0) > 0


async def was_member(session: AsyncSession, symbol: str, universe_code: str) -> bool:
    """是否曾经属于指定指数；用于区分“真正调出”和普通范围外股票。"""
    result = await session.execute(
        select(func.count()).where(
            UniverseMembership.universe_code == universe_code,
            UniverseMembership.symbol == symbol,
        )
    )
    return (result.scalar_one() or 0) > 0


async def current_member_symbols(
    session: AsyncSession, universe_code: str, as_of: date, symbols: Sequence[str]
) -> set[str]:
    """批量判定成员资格（自选股列表一次查完，避免 N+1）。"""
    if not symbols:
        return set()
    stmt = _membership_active(as_of).where(
        UniverseMembership.universe_code == universe_code,
        UniverseMembership.symbol.in_(list(symbols)),
    )
    result = await session.execute(stmt)
    return set(result.scalars().all())


async def universe_has_snapshot(session: AsyncSession, universe_code: str, as_of: date) -> bool:
    """该日期是否存在任何成分记录。

    全无记录 ⇒ 成分从未同步成功 ⇒ 上游不可用（424），而不是"沪深300是空的"。
    """
    stmt = _membership_active(as_of).where(UniverseMembership.universe_code == universe_code).limit(1)
    result = await session.execute(stmt)
    return result.first() is not None


async def list_members(
    session: AsyncSession,
    universe_code: str,
    as_of: date,
    *,
    limit: int,
    cursor: Cursor | None = None,
) -> tuple[list[Instrument], bool]:
    """按 symbol 升序的键集分页。返回 (页内数据, has_more)。"""
    stmt = (
        select(Instrument)
        .where(Instrument.symbol.in_(_membership_active(as_of).where(
            UniverseMembership.universe_code == universe_code
        )))
        .order_by(Instrument.symbol.asc())
        .limit(limit + 1)  # 多取一条用于判定 has_more
    )
    if cursor is not None:
        stmt = stmt.where(Instrument.symbol > cursor.value)

    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    return rows[:limit], has_more


async def search_current_members(
    session: AsyncSession, universe_code: str, query: str, as_of: date, limit: int
) -> list[Instrument]:
    """只搜索查询日当前成分股，**精确代码匹配优先**（spec §7.1）。"""
    normalized = query.strip()
    if not normalized:
        return []

    rank = case(
        (Instrument.symbol == normalized, 0),  # 精确代码
        (Instrument.symbol.startswith(normalized), 1),  # 代码前缀
        (Instrument.name == normalized, 2),  # 精确名称
        else_=3,  # 模糊名称/代码
    )
    stmt = (
        select(Instrument)
        .where(
            Instrument.symbol.in_(
                _membership_active(as_of).where(UniverseMembership.universe_code == universe_code)
            ),
            or_(
                Instrument.symbol.contains(normalized, autoescape=True),
                Instrument.name.contains(normalized, autoescape=True),
            ),
        )
        .order_by(rank.asc(), Instrument.symbol.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def search_all(session: AsyncSession, query: str, limit: int) -> list[Instrument]:
    """搜索本地已知 A 股；用于添加沪深300之外的额外自选。"""
    normalized = query.strip()
    if not normalized:
        return []
    rank = case(
        (Instrument.symbol == normalized, 0),
        (Instrument.symbol.startswith(normalized), 1),
        (Instrument.name == normalized, 2),
        else_=3,
    )
    stmt = (
        select(Instrument)
        .where(
            Instrument.active.is_(True),
            or_(
                Instrument.symbol.contains(normalized, autoescape=True),
                Instrument.name.contains(normalized, autoescape=True),
            ),
        )
        .order_by(rank.asc(), Instrument.symbol.asc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())
