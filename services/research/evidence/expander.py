"""证据展开（spec §11.3）——供 **API 层** 调用。

约定：``evidence_ids`` 是 Agent 的内部输出；对外的 ``analyses.evidence`` 必须由 API 层
**在同一事务快照中**查询 ``documents`` 展开成 ``EvidenceDTO``。
任一 ID 不存在 → 抛 ``EvidenceValidationError``，整条分析校验失败，**不返回部分证据**。

用法（API / 作业层）::

    async with session_scope() as session:
        evidence = await expand_evidence(
            session, output.evidence_ids, symbol="600519", as_of=analysis.data_cutoff
        )

同一个 ``session`` 既读 ``analyses`` 又读 ``documents``，因此两者天然处于同一事务快照。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.enums import Direction
from apps.api.app.models.tables import Document
from services.research.evidence.dto import DocumentLike, EvidenceDTO
from services.research.evidence.validator import (
    build_evidence,
    coerce_uuid,
    dedupe_ids,
    enforce_direction_consistency,
)


async def expand_evidence(
    session: AsyncSession,
    evidence_ids: Sequence[uuid.UUID | str],
    *,
    quotes: Mapping[uuid.UUID, str] | None = None,
    symbol: str | None = None,
    as_of: datetime | None = None,
) -> list[EvidenceDTO]:
    """在同一事务快照中把 ``evidence_ids`` 展开为 ``EvidenceDTO`` 列表。

    :param quotes: 可选的引文覆盖（Agent 固定 Schema 不含 quote，默认由原文确定性截取）。
                   给定时必须逐字命中原文，否则整条失败。
    :param symbol: 给定时校验文档归属该证券。
    :param as_of:  给定时校验文档在该时点之前已可见（PIT）。
    :raises EvidenceValidationError: 任一 ID 不存在 / 引文非原文子串 / 归属或 PIT 不符。
    """
    ordered = dedupe_ids([coerce_uuid(value) for value in evidence_ids])
    if not ordered:
        return []

    rows = (await session.execute(select(Document).where(Document.id.in_(ordered)))).scalars().all()
    documents: dict[uuid.UUID, DocumentLike] = {row.id: row for row in rows}
    return build_evidence(
        documents,
        ordered,
        quotes=dict(quotes) if quotes else None,
        symbol=symbol,
        as_of=as_of,
    )


async def expand_and_validate_analysis(
    session: AsyncSession,
    *,
    evidence_ids: Sequence[uuid.UUID | str],
    direction: Direction | str,
    summary: str,
    quotes: Mapping[uuid.UUID, str] | None = None,
    symbol: str | None = None,
    as_of: datetime | None = None,
) -> list[EvidenceDTO]:
    """展开证据 **并** 校验"无证据 → unknown + 固定文案"。整条通过才返回。

    这是分析落库前与 API 出参前的同一个闸门：两条路径都必须过它，否则一条分析可能
    "写入时合法、读出时非法"。
    """
    evidence = await expand_evidence(
        session, evidence_ids, quotes=quotes, symbol=symbol, as_of=as_of
    )
    enforce_direction_consistency(direction=direction, summary=summary, evidence=evidence)
    return evidence
