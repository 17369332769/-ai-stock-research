"""股票池与搜索编排（spec §7.1）。

- ``GET /universes/CSI300/instruments?as_of=`` 返回**指定日期真实有效**的成分，
  不用当前 300 只回填历史（验收 §15.16）；
- ``GET /instruments/search`` 只搜索查询日当前成分，精确代码匹配优先；
- 成分从未同步成功（库里一条有效期都没有）⇒ 424 PROVIDER_UNAVAILABLE，
  而不是返回空列表假装"沪深300是空的"。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.errors import ProviderUnavailable
from apps.api.app.core.pagination import Cursor
from apps.api.app.repositories import instruments as instruments_repo
from apps.api.app.repositories.instruments import MEMBER_SORT_KEY
from apps.api.app.schemas.instruments import InstrumentDTO


async def list_universe_members(
    session: AsyncSession,
    universe_code: str,
    as_of: date,
    *,
    limit: int,
    cursor: Cursor | None,
) -> tuple[list[InstrumentDTO], Cursor | None, bool]:
    if not await instruments_repo.universe_has_snapshot(session, universe_code, as_of):
        raise ProviderUnavailable(
            f"{universe_code} 在 {as_of} 没有任何成分记录：成分数据尚未同步成功"
        )

    rows, has_more = await instruments_repo.list_members(
        session, universe_code, as_of, limit=limit, cursor=cursor
    )
    dtos = [InstrumentDTO.from_row(row, is_current_universe_member=True) for row in rows]
    next_cursor = (
        Cursor(sort=MEMBER_SORT_KEY, value=rows[-1].symbol, id=rows[-1].symbol)
        if rows and has_more
        else None
    )
    return dtos, next_cursor, has_more


async def search_instruments(
    session: AsyncSession,
    universe_code: str,
    query: str,
    as_of: date,
    limit: int,
) -> list[InstrumentDTO]:
    if not await instruments_repo.universe_has_snapshot(session, universe_code, as_of):
        raise ProviderUnavailable(
            f"{universe_code} 在 {as_of} 没有任何成分记录：成分数据尚未同步成功"
        )

    rows = await instruments_repo.search_current_members(
        session, universe_code, query, as_of, limit
    )
    # 命中即为当日成分（查询已按有效期过滤）
    return [InstrumentDTO.from_row(row, is_current_universe_member=True) for row in rows]
