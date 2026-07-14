"""文档与解释编排（spec §7.3 / §11.3）。

证据展开的铁律：**整条成功或整条失败，绝不返回部分证据。**

``analyses.evidence`` 里存的是已展开的证据数组（document_id / title / source_url /
published_at / quote）。读取时在**同一事务快照**内再校验一次这些 document_id 是否仍然存在：
任一 id 不存在 ⇒ 整条分析校验失败。文档表是只追加的，出现这种情况属于数据完整性事故，
因此 fail closed（500），而不是悄悄丢掉几条证据把剩下的展示给用户。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.clock import to_shanghai
from apps.api.app.core.enums import AnalysisType, Direction, DocumentType, EventHorizon
from apps.api.app.core.errors import InstrumentNotFound
from apps.api.app.core.logging import get_logger
from apps.api.app.core.pagination import Cursor
from apps.api.app.models.tables import Analysis
from apps.api.app.repositories import analyses as analyses_repo
from apps.api.app.repositories import documents as documents_repo
from apps.api.app.repositories import instruments as instruments_repo
from apps.api.app.repositories import jobs as jobs_repo
from apps.api.app.schemas.analyses import AnalysisDTO, EvidenceDTO
from apps.api.app.schemas.documents import DocumentDTO
from apps.api.app.schemas.jobs import JobDTO

logger = get_logger(__name__)


class EvidenceIntegrityError(RuntimeError):
    """证据无法完整展开。不可恢复的数据完整性错误 ⇒ 500，不返回部分证据。"""


async def _require_instrument(session: AsyncSession, symbol: str) -> None:
    if await instruments_repo.get(session, symbol) is None:
        raise InstrumentNotFound(symbol)


async def list_documents(
    session: AsyncSession,
    symbol: str,
    *,
    document_type: DocumentType | None,
    limit: int,
    cursor: Cursor | None,
) -> tuple[list[DocumentDTO], Cursor | None, bool]:
    await _require_instrument(session, symbol)
    rows, has_more = await documents_repo.list_by_symbol(
        session,
        symbol,
        document_type=document_type.value if document_type else None,
        limit=limit,
        cursor=cursor,
    )
    next_cursor = documents_repo.build_cursor(rows[-1]) if rows and has_more else None
    return [DocumentDTO.from_row(row) for row in rows], next_cursor, has_more


async def list_analyses(
    session: AsyncSession,
    symbol: str,
    *,
    analysis_type: AnalysisType | None,
    limit: int,
    cursor: Cursor | None,
) -> tuple[list[AnalysisDTO], Cursor | None, bool]:
    await _require_instrument(session, symbol)
    rows, has_more = await analyses_repo.list_by_symbol(
        session,
        symbol,
        analysis_type=analysis_type.value if analysis_type else None,
        limit=limit,
        cursor=cursor,
    )

    # 同一事务快照内一次性校验整页引用到的所有 document_id（spec §11.3）
    referenced = _referenced_document_ids(rows)
    existing = await analyses_repo.existing_document_ids(session, sorted(referenced))

    dtos = [_to_analysis_dto(row, existing) for row in rows]
    next_cursor = analyses_repo.build_cursor(rows[-1]) if rows and has_more else None
    return dtos, next_cursor, has_more


async def refresh_analysis(session: AsyncSession, symbol: str) -> JobDTO:
    """``POST /stocks/{symbol}/analyses/refresh``。

    只登记 ``analysis_refresh`` 作业（jobs 表即队列），由 worker 里的证据约束 Agent 执行；
    Agent 不在 API 进程内跑 —— apps/api 不得包含分析算法（spec §5.1 / §14.1）。
    """
    await _require_instrument(session, symbol)
    job = await jobs_repo.enqueue_analysis_refresh(session, symbol)
    return JobDTO.from_row(job)


def _referenced_document_ids(rows: list[Analysis]) -> set[uuid.UUID]:
    ids: set[uuid.UUID] = set()
    for row in rows:
        for item in row.evidence or []:
            raw = item.get("document_id") if isinstance(item, dict) else None
            if raw is None:
                raise EvidenceIntegrityError(f"分析 {row.id} 的证据缺少 document_id")
            try:
                ids.add(uuid.UUID(str(raw)))
            except ValueError as exc:
                raise EvidenceIntegrityError(f"分析 {row.id} 的 document_id 非法：{raw!r}") from exc
    return ids


def _to_analysis_dto(row: Analysis, existing_document_ids: set[uuid.UUID]) -> AnalysisDTO:
    evidence = _expand_evidence(row, existing_document_ids)
    return AnalysisDTO(
        id=row.id,
        symbol=row.symbol,
        analysis_type=AnalysisType(row.analysis_type),
        # 无证据必须 unknown（spec §11.3）；direction 为空按 unknown 处理
        direction=Direction(row.direction) if row.direction else Direction.UNKNOWN,
        horizon=EventHorizon(row.horizon) if row.horizon else EventHorizon.UNKNOWN,
        confidence=float(row.confidence) if row.confidence is not None else None,
        summary=row.summary,
        evidence=evidence,
        model_provider=row.model_provider,
        model_name=row.model_name,
        data_cutoff=to_shanghai(row.data_cutoff),
        created_at=to_shanghai(row.created_at),
    )


def _expand_evidence(row: Analysis, existing_document_ids: set[uuid.UUID]) -> list[EvidenceDTO]:
    """整条成功或整条失败（spec §11.3）。"""
    expanded: list[EvidenceDTO] = []
    for item in row.evidence or []:
        if not isinstance(item, dict):
            raise EvidenceIntegrityError(f"分析 {row.id} 的证据项不是对象")

        document_id = uuid.UUID(str(item["document_id"]))
        if document_id not in existing_document_ids:
            # 任一 ID 不存在 ⇒ 整条分析校验失败，不返回部分证据
            logger.error(
                "证据引用的文档不存在，整条分析校验失败",
                extra={"analysis_id": str(row.id), "document_id": str(document_id)},
            )
            raise EvidenceIntegrityError(
                f"分析 {row.id} 引用的文档 {document_id} 不存在，整条证据校验失败"
            )
        try:
            expanded.append(
                EvidenceDTO(
                    document_id=document_id,
                    title=str(item["title"]),
                    source_url=str(item["source_url"]),
                    published_at=_parse_dt(item["published_at"]),
                    quote=str(item["quote"]),
                )
            )
        except (KeyError, ValidationError, ValueError) as exc:
            raise EvidenceIntegrityError(f"分析 {row.id} 的证据项非法：{exc}") from exc
    return expanded


def _parse_dt(value: object) -> datetime:
    if isinstance(value, datetime):
        return to_shanghai(value)
    moment = datetime.fromisoformat(str(value))
    if moment.tzinfo is None:
        raise ValueError("证据 published_at 必须带时区")
    return to_shanghai(moment)
