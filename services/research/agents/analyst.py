"""把 Agent 输出装配成一条**可落库的分析**（spec §7.3 / §11 / §12）。

装配顺序（不可颠倒）：

1. **确定性事实先行**：异动分析的 summary 必须以异动检测算出的量价事实块开头。
2. **再检索事件证据**：Agent 只能引用它通过工具真正检索到的文档。
3. **证据展开与校验**：在**同一事务快照**里把 ``evidence_ids`` 展开成 ``EvidenceDTO``；
   任一 ID 不存在 / 引文非原文子串 / 归属或 PIT 不符 → **整条分析校验失败**，
   不落"部分证据"，而是降级为模板摘要（direction=unknown + 固定文案）。
4. **无证据 → unknown + 固定文案**，绝不编造公司事件。

Agent 不可用、两次 Schema 失败、证据校验失败，都走同一条降级路径，
且 ``model_provider`` / ``model_name`` 留空。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.enums import AnalysisType, Direction, EventHorizon
from apps.api.app.models.tables import Analysis
from services.research.agents.client import ChatClient
from services.research.agents.prompts import (
    anomaly_task_prompt,
    document_task_prompt,
    sanitize_untrusted_text,
)
from services.research.agents.repository import DocumentSnapshot, ResearchReadRepository
from services.research.agents.runner import AgentRunResult, DegradeReason, run_agent
from services.research.agents.schema import template_output
from services.research.agents.tools import DEFAULT_DOCUMENT_LOOKBACK, AgentToolbox
from services.research.anomaly import EVIDENCE_LOOKBACK, AnomalyEvent
from services.research.evidence.dto import EvidenceDTO
from services.research.evidence.errors import EvidenceValidationError
from services.research.evidence.expander import expand_and_validate_analysis

logger = logging.getLogger(__name__)

MAX_STORED_SUMMARY_CHARS = 4000


@dataclass(frozen=True, slots=True)
class AnalysisDraft:
    """一条待落库的分析。已通过证据校验，可直接写 ``analyses``。"""

    symbol: str
    analysis_type: AnalysisType
    direction: Direction
    horizon: EventHorizon
    confidence: float
    summary: str
    evidence: tuple[EvidenceDTO, ...]
    data_cutoff: datetime
    model_provider: str | None
    model_name: str | None
    degraded: bool
    degrade_reason: str | None = None

    def to_orm(self, analysis_id: uuid.UUID | None = None) -> Analysis:
        return Analysis(
            id=analysis_id or uuid.uuid4(),
            symbol=self.symbol,
            analysis_type=self.analysis_type.value,
            direction=self.direction.value,
            horizon=self.horizon.value,
            confidence=Decimal(str(round(self.confidence, 4))),
            summary=self.summary,
            evidence=[item.to_json_dict() for item in self.evidence],
            model_provider=self.model_provider,
            model_name=self.model_name,
            data_cutoff=self.data_cutoff,
        )


def _compose_summary(*parts: str) -> str:
    body = "\n\n".join(part.strip() for part in parts if part and part.strip())
    return body[:MAX_STORED_SUMMARY_CHARS]


def _annotations(result: AgentRunResult) -> str:
    """把 unknowns / risk_flags 落到 summary 里 —— ``analyses`` 没有对应列，不能静默丢弃。"""
    lines: list[str] = []
    if result.output.unknowns:
        lines.append("未知项：" + "；".join(result.output.unknowns))
    if result.output.risk_flags:
        lines.append("风险提示：" + "；".join(result.output.risk_flags))
    return "\n".join(lines)


async def _finalize(
    session: AsyncSession,
    *,
    result: AgentRunResult,
    symbol: str,
    analysis_type: AnalysisType,
    as_of: datetime,
    facts_prefix: str,
    degrade_prefix: str,
    served_document_ids: frozenset[uuid.UUID],
) -> AnalysisDraft:
    """证据展开 + 校验 + 汇总。任一环节失败 → 整条降级，不返回部分证据。

    ``degrade_prefix`` 是降级时必须保留的确定性事实（异动的量价事实块）：
    即便模型这条作废，事实也不能跟着丢。
    """
    output = result.output
    summary = _compose_summary(facts_prefix, output.summary, _annotations(result))

    # 反幻觉：只能引用**本次真正检索过**的文档
    unserved = [doc_id for doc_id in output.evidence_ids if doc_id not in served_document_ids]
    if unserved:
        logger.warning(
            "Agent 引用了未检索过的文档，整条分析降级 symbol=%s ids=%s", symbol, unserved
        )
        return _degraded_draft(
            symbol=symbol,
            analysis_type=analysis_type,
            as_of=as_of,
            facts_prefix=degrade_prefix,
            reason=DegradeReason.EVIDENCE_INVALID,
        )

    try:
        evidence = await expand_and_validate_analysis(
            session,
            evidence_ids=list(output.evidence_ids),
            direction=output.direction,
            summary=summary,
            symbol=symbol,
            as_of=as_of,
        )
    except EvidenceValidationError as exc:
        logger.warning("证据校验失败，整条分析降级 symbol=%s failure=%s", symbol, exc)
        return _degraded_draft(
            symbol=symbol,
            analysis_type=analysis_type,
            as_of=as_of,
            facts_prefix=degrade_prefix,
            reason=DegradeReason.EVIDENCE_INVALID,
        )

    return AnalysisDraft(
        symbol=symbol,
        analysis_type=analysis_type,
        direction=output.direction,
        horizon=output.horizon,
        confidence=output.confidence,
        summary=summary,
        evidence=tuple(evidence),
        data_cutoff=as_of,
        model_provider=result.model_provider,
        model_name=result.model_name,
        degraded=result.degraded,
        degrade_reason=result.degrade_reason,
    )


def _degraded_draft(
    *,
    symbol: str,
    analysis_type: AnalysisType,
    as_of: datetime,
    facts_prefix: str,
    reason: str,
    unknowns: list[str] | None = None,
) -> AnalysisDraft:
    """模板摘要：确定性事实（若有）+ 固定文案；无证据、方向 unknown、不留模型身份。"""
    output = template_output(summary_prefix=facts_prefix, unknowns=unknowns)
    note = "未知项：" + "；".join(output.unknowns) if output.unknowns else ""
    return AnalysisDraft(
        symbol=symbol,
        analysis_type=analysis_type,
        direction=Direction.UNKNOWN,
        horizon=EventHorizon.UNKNOWN,
        confidence=0.0,
        summary=_compose_summary(output.summary, note),
        evidence=(),
        data_cutoff=as_of,
        model_provider=None,
        model_name=None,
        degraded=True,
        degrade_reason=reason,
    )


async def analyze_document(
    session: AsyncSession,
    *,
    repository: ResearchReadRepository,
    client: ChatClient | None,
    symbol: str,
    document: DocumentSnapshot,
    as_of: datetime,
) -> AnalysisDraft:
    """对一篇新文档做解读。Agent 不可用时返回模板摘要（不阻断其他功能）。"""
    toolbox = AgentToolbox(
        repository,
        symbol=symbol,
        as_of=as_of,
        document_lookback=DEFAULT_DOCUMENT_LOOKBACK,
    )
    # 降级时的确定性前缀：只陈述文档元数据，不做任何解读
    safe_title = sanitize_untrusted_text(document.title, max_chars=200)
    facts_prefix = (
        f"文档「{safe_title}」（{document.source}，发布于 "
        f"{document.published_at:%Y-%m-%d %H:%M}）。"
    )

    result = await run_agent(
        client=client,
        toolbox=toolbox,
        task_prompt=document_task_prompt(symbol=symbol, as_of=as_of, document_id=document.id),
        summary_prefix=facts_prefix,
    )
    if result.degraded:
        return _degraded_draft(
            symbol=symbol,
            analysis_type=AnalysisType.DOCUMENT,
            as_of=as_of,
            facts_prefix=facts_prefix,
            reason=result.degrade_reason or DegradeReason.AGENT_UNAVAILABLE,
        )
    return await _finalize(
        session,
        result=result,
        symbol=symbol,
        analysis_type=AnalysisType.DOCUMENT,
        as_of=as_of,
        facts_prefix="",  # 非降级时事实由 Agent 的 summary 承载（每句都要能落到字段/证据）
        degrade_prefix=facts_prefix,  # 一旦作废，仍保留文档元数据这条确定性事实
        served_document_ids=toolbox.served_document_ids,
    )


async def analyze_anomaly(
    session: AsyncSession,
    *,
    repository: ResearchReadRepository,
    client: ChatClient | None,
    event: AnomalyEvent,
    as_of: datetime,
) -> AnalysisDraft:
    """异动分析：**先确定性量价事实，再检索事件证据**；没有匹配文档 → 固定文案（spec §12）。"""
    toolbox = AgentToolbox(
        repository,
        symbol=event.symbol,
        as_of=as_of,
        document_lookback=EVIDENCE_LOOKBACK,
    )
    facts_block = event.facts_block

    result = await run_agent(
        client=client,
        toolbox=toolbox,
        task_prompt=anomaly_task_prompt(
            symbol=event.symbol,
            as_of=as_of,
            facts_block=facts_block,
            evidence_window_hours=int(EVIDENCE_LOOKBACK.total_seconds() // 3600),
        ),
        summary_prefix=facts_block,
        unknowns=list(event.skipped),
    )
    if result.degraded:
        return _degraded_draft(
            symbol=event.symbol,
            analysis_type=AnalysisType.ANOMALY,
            as_of=as_of,
            facts_prefix=facts_block,
            reason=result.degrade_reason or DegradeReason.AGENT_UNAVAILABLE,
            unknowns=list(event.skipped),  # 样本不足未评估的规则不能静默消失
        )

    # 事实块永远在最前：模型可以复述，但不能替代
    prefix = "" if facts_block in result.output.summary else facts_block
    return await _finalize(
        session,
        result=result,
        symbol=event.symbol,
        analysis_type=AnalysisType.ANOMALY,
        as_of=as_of,
        facts_prefix=prefix,
        degrade_prefix=facts_block,  # 模型这条作废，量价事实也必须留在 summary 里
        served_document_ids=toolbox.served_document_ids,
    )


def draft_to_json(draft: AnalysisDraft) -> dict[str, Any]:
    """给日志/调试用的可序列化形态（不含 prompt、不含密钥）。"""
    return {
        "symbol": draft.symbol,
        "analysis_type": draft.analysis_type.value,
        "direction": draft.direction.value,
        "horizon": draft.horizon.value,
        "confidence": draft.confidence,
        "evidence_count": len(draft.evidence),
        "degraded": draft.degraded,
        "degrade_reason": draft.degrade_reason,
        "data_cutoff": draft.data_cutoff.isoformat(),
    }
