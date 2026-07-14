"""证据校验器（spec §7.3 / §11.3）。

三条硬约束，任一不满足即**整条分析失败**，不返回部分证据：

1. ``quote`` 必须是文档原文（``body_text``，其次 ``title``）中**逐字连续存在**的子串，长度 1–300。
   这里刻意**不做任何归一化**（不折叠空白、不去标点、不做大小写转换）：
   "原文中连续存在" 只有逐字比较这一种可验证的含义，任何归一化都会让"引文"变成"改写"。
2. ``evidence_ids`` 为空 → ``direction`` 必须是 ``unknown``，且 ``summary`` 必须包含固定文案
   ``NO_VERIFIABLE_CAUSE_TEXT``（"未找到可验证事件原因"）。
3. 证据文档必须属于被分析的证券，且在 ``data_cutoff`` 之前已可见（PIT）。

``derive_quote`` 用于在 Agent 只回传 ``evidence_ids``（spec §11.2 的固定 Schema 不含 quote）时，
从文档原文里**确定性地**截出引文；它的产物同样要过 ``validate_quote``，因此引文永远可验证。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from apps.api.app.core.enums import NO_VERIFIABLE_CAUSE_TEXT, Direction
from services.research.evidence.dto import MAX_QUOTE_CHARS, MIN_QUOTE_CHARS, DocumentLike, EvidenceDTO
from services.research.evidence.errors import EvidenceFailure, EvidenceValidationError

# 截取引文时优先在这些句读处收尾，保证引文读起来是完整一句
_SENTENCE_ENDS = ("。", "！", "？", "；", "\n", ".", ";", "!", "?")


def validate_quote(quote: str, *, title: str, body_text: str | None, document_id: uuid.UUID) -> None:
    """逐字校验 quote 是原文的连续子串且长度合法；失败即抛错（整条分析失败）。"""
    if not (MIN_QUOTE_CHARS <= len(quote) <= MAX_QUOTE_CHARS):
        raise EvidenceValidationError(
            EvidenceFailure.QUOTE_LENGTH,
            f"引文长度 {len(quote)} 不在 {MIN_QUOTE_CHARS}–{MAX_QUOTE_CHARS} 之间",
            document_id=document_id,
        )
    haystacks = [h for h in (body_text, title) if h]
    if not any(quote in haystack for haystack in haystacks):
        raise EvidenceValidationError(
            EvidenceFailure.QUOTE_NOT_VERBATIM,
            "引文不是文档原文中连续存在的子串",
            document_id=document_id,
        )


def derive_quote(*, title: str, body_text: str | None) -> str:
    """从原文确定性地截出 1–300 字引文：正文优先，正文为空时退回标题。

    截取方式保证结果一定是原文的连续子串：
    先跳过开头空白，再取不超过 300 字的切片，然后只在**右侧**做收敛（句读收尾 / 去尾部空白），
    从不拼接、不改写、不插入省略号。
    """
    source = body_text if body_text and body_text.strip() else title
    if not source or not source.strip():
        # 标题在 ORM 中 NOT NULL 且业务上非空；真出现空文档时属于数据缺陷，直接失败
        raise EvidenceValidationError(
            EvidenceFailure.QUOTE_LENGTH, "文档正文与标题均为空，无法产生引文"
        )

    start = 0
    while start < len(source) and source[start].isspace():
        start += 1
    window = source[start : start + MAX_QUOTE_CHARS]

    # 若窗口内有句读且不是整段被截断（即原文还有后续），在最后一个句读处收尾
    if start + MAX_QUOTE_CHARS < len(source):
        cut = max((window.rfind(end) for end in _SENTENCE_ENDS), default=-1)
        if cut >= MIN_QUOTE_CHARS:  # 至少留一个字符
            window = window[: cut + 1]

    quote = window.rstrip()  # rstrip 后仍是原文切片的前缀 → 仍是原文的连续子串
    if not quote:
        quote = window[:MIN_QUOTE_CHARS] or source[:MIN_QUOTE_CHARS]
    return quote


def build_evidence(
    documents: dict[uuid.UUID, DocumentLike],
    evidence_ids: Sequence[uuid.UUID],
    *,
    quotes: dict[uuid.UUID, str] | None = None,
    symbol: str | None = None,
    as_of: datetime | None = None,
) -> list[EvidenceDTO]:
    """把 ``evidence_ids`` 展开成 ``EvidenceDTO``（纯函数核心，不碰数据库）。

    任一 ID 在 ``documents`` 中不存在 → 整条失败。
    ``symbol`` / ``as_of`` 给出时，额外校验归属与 PIT 可见性。
    """
    quotes = quotes or {}
    evidence: list[EvidenceDTO] = []
    for document_id in evidence_ids:
        document = documents.get(document_id)
        if document is None:
            raise EvidenceValidationError(
                EvidenceFailure.DOCUMENT_NOT_FOUND,
                "分析引用的文档不存在于同一事务快照中，整条分析校验失败",
                document_id=document_id,
            )
        if symbol is not None and document.symbol != symbol:
            raise EvidenceValidationError(
                EvidenceFailure.SYMBOL_MISMATCH,
                f"文档不属于 {symbol}（实际 {document.symbol}）",
                document_id=document_id,
            )
        if as_of is not None and (document.published_at > as_of or document.observed_at > as_of):
            raise EvidenceValidationError(
                EvidenceFailure.NOT_VISIBLE_AT_CUTOFF,
                f"文档在数据截止时间 {as_of.isoformat()} 之后才可见（PIT 违规）",
                document_id=document_id,
            )

        quote = quotes.get(document_id)
        if quote is None:
            quote = derive_quote(title=document.title, body_text=document.body_text)
        validate_quote(
            quote,
            title=document.title,
            body_text=document.body_text,
            document_id=document.id,
        )
        evidence.append(
            EvidenceDTO(
                document_id=document.id,
                title=document.title,
                source_url=document.source_url,
                published_at=document.published_at,
                quote=quote,
            )
        )
    return evidence


def enforce_direction_consistency(
    *,
    direction: Direction | str,
    summary: str,
    evidence: Sequence[EvidenceDTO],
) -> None:
    """无证据 → 方向必须 unknown，且 summary 必须含固定文案（spec §7.3 / §12 / 验收 §15.5）。"""
    if evidence:
        return
    if Direction(direction) is not Direction.UNKNOWN:
        raise EvidenceValidationError(
            EvidenceFailure.DIRECTION_WITHOUT_EVIDENCE,
            f"无证据时方向必须为 unknown，实际为 {direction}",
        )
    if NO_VERIFIABLE_CAUSE_TEXT not in summary:
        raise EvidenceValidationError(
            EvidenceFailure.MISSING_UNKNOWN_CAUSE_TEXT,
            f"无证据时 summary 必须包含固定文案「{NO_VERIFIABLE_CAUSE_TEXT}」",
        )


def dedupe_ids(evidence_ids: Sequence[uuid.UUID]) -> list[uuid.UUID]:
    """按首次出现顺序去重（同一文档不重复展示，验收 §15.4）。"""
    seen: set[uuid.UUID] = set()
    ordered: list[uuid.UUID] = []
    for document_id in evidence_ids:
        if document_id not in seen:
            seen.add(document_id)
            ordered.append(document_id)
    return ordered


def coerce_uuid(value: uuid.UUID | str) -> uuid.UUID:
    """把 Agent 输出里的字符串 ID 转成 UUID；非法即整条失败。"""
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise EvidenceValidationError(
            EvidenceFailure.MALFORMED_ID,
            f"evidence_ids 中出现非法 UUID：{value!r}",
            document_id=str(value),
        ) from exc
