"""分析（AI 结论）仓储。键集分页，游标 sort=created_at。"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.enums import AnalysisType
from apps.api.app.core.pagination import Cursor
from apps.api.app.models.tables import Analysis, Document
from apps.api.app.repositories.documents import parse_cursor_datetime

ANALYSIS_SORT_KEY = "created_at"


async def list_by_symbol(
    session: AsyncSession,
    symbol: str,
    *,
    analysis_type: str | None,
    limit: int,
    cursor: Cursor | None = None,
) -> tuple[list[Analysis], bool]:
    stmt = (
        select(Analysis)
        .where(Analysis.symbol == symbol)
        .order_by(Analysis.created_at.desc(), Analysis.id.desc())
        .limit(limit + 1)
    )
    if analysis_type is not None:
        stmt = stmt.where(Analysis.analysis_type == analysis_type)
    if cursor is not None:
        moment, row_id = parse_cursor_datetime(cursor)
        stmt = stmt.where(tuple_(Analysis.created_at, Analysis.id) < tuple_(literal(moment), literal(row_id)))

    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    return rows[:limit], has_more


async def latest_anomaly_id(session: AsyncSession, symbol: str) -> uuid.UUID | None:
    result = await session.execute(
        select(Analysis.id)
        .where(Analysis.symbol == symbol, Analysis.analysis_type == AnalysisType.ANOMALY.value)
        .order_by(Analysis.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def existing_document_ids(
    session: AsyncSession, document_ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    """同一事务快照内校验证据引用的文档是否存在（spec §11.3）。

    调用方据此实现「整条成功或整条失败」：任一 id 不存在 ⇒ 整条分析校验失败，
    **绝不返回部分证据**。
    """
    if not document_ids:
        return set()
    result = await session.execute(select(Document.id).where(Document.id.in_(list(document_ids))))
    return set(result.scalars().all())


def build_cursor(row: Analysis) -> Cursor:
    return Cursor(sort=ANALYSIS_SORT_KEY, value=row.created_at.isoformat(), id=str(row.id))
