"""文档（公告/新闻）仓储。键集分页，游标 sort=published_at。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.errors import InvalidArgument
from apps.api.app.core.pagination import Cursor
from apps.api.app.models.tables import Document

DOCUMENT_SORT_KEY = "published_at"


def parse_cursor_datetime(cursor: Cursor) -> tuple[datetime, uuid.UUID]:
    """游标里的 value/id 必须能解析；否则 400 INVALID_ARGUMENT（spec §7）。"""
    try:
        moment = datetime.fromisoformat(cursor.value)
        row_id = uuid.UUID(cursor.id)
    except ValueError as exc:
        raise InvalidArgument("游标字段无效") from exc
    if moment.tzinfo is None:
        raise InvalidArgument("游标时间必须带时区")
    return moment, row_id


async def list_by_symbol(
    session: AsyncSession,
    symbol: str,
    *,
    document_type: str | None,
    limit: int,
    cursor: Cursor | None = None,
) -> tuple[list[Document], bool]:
    """按 published_at 倒序。返回 (页内数据, has_more)。"""
    stmt = (
        select(Document)
        .where(Document.symbol == symbol)
        .order_by(Document.published_at.desc(), Document.id.desc())
        .limit(limit + 1)
    )
    if document_type is not None:
        stmt = stmt.where(Document.document_type == document_type)
    if cursor is not None:
        moment, row_id = parse_cursor_datetime(cursor)
        stmt = stmt.where(
            tuple_(Document.published_at, Document.id) < tuple_(literal(moment), literal(row_id))
        )

    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    return rows[:limit], has_more


def build_cursor(row: Document) -> Cursor:
    return Cursor(sort=DOCUMENT_SORT_KEY, value=row.published_at.isoformat(), id=str(row.id))
