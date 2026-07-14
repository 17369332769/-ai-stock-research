"""证据约束测试（spec §7.3 / §11.3）。

覆盖三条硬约束：
* 空证据 → 方向必须 unknown + 固定文案；
* 引用的 document_id 不存在 → **整条**分析失败，不返回部分证据；
* quote 不是原文连续子串（或长度越界）→ 整条失败。
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.enums import NO_VERIFIABLE_CAUSE_TEXT, Direction, EventHorizon
from services.research.agents.schema import AgentOutput, AgentSchemaError, parse_agent_output
from services.research.evidence import (
    MAX_QUOTE_CHARS,
    EvidenceFailure,
    EvidenceValidationError,
    build_evidence,
    derive_quote,
    enforce_direction_consistency,
    expand_and_validate_analysis,
    expand_evidence,
    validate_quote,
)
from services.research.tests.conftest import AS_OF, SYMBOL, FakeSession, make_document

# ── quote 必须是原文中连续存在的 1–300 字符 ────────────────────────────────


def test_quote_must_be_verbatim_substring() -> None:
    document = make_document(body_text="公司与甲方签署了重大合同，合同金额 12 亿元。")
    validate_quote("签署了重大合同", title=document.title, body_text=document.body_text, document_id=document.id)


def test_quote_not_in_source_fails_whole_analysis() -> None:
    document = make_document(body_text="公司与甲方签署了重大合同，合同金额 12 亿元。")
    with pytest.raises(EvidenceValidationError) as exc:
        validate_quote(
            "公司预计明年净利润翻倍",  # 原文里没有这句 —— 典型的"改写/编造引文"
            title=document.title,
            body_text=document.body_text,
            document_id=document.id,
        )
    assert exc.value.failure is EvidenceFailure.QUOTE_NOT_VERBATIM


def test_quote_paraphrase_with_whitespace_change_fails() -> None:
    """不做任何归一化：改一个空格也不算"原文中连续存在"。"""
    document = make_document(body_text="合同金额 12 亿元。")
    with pytest.raises(EvidenceValidationError):
        validate_quote(
            "合同金额12亿元。", title=document.title, body_text=document.body_text, document_id=document.id
        )


@pytest.mark.parametrize("length", [0, MAX_QUOTE_CHARS + 1])
def test_quote_length_out_of_range_fails(length: int) -> None:
    body = "甲" * (MAX_QUOTE_CHARS + 10)
    document = make_document(body_text=body)
    with pytest.raises(EvidenceValidationError) as exc:
        validate_quote("甲" * length, title=document.title, body_text=document.body_text, document_id=document.id)
    assert exc.value.failure is EvidenceFailure.QUOTE_LENGTH


def test_quote_matched_in_title_when_body_missing() -> None:
    document = make_document(title="关于签署重大合同的公告", body_text=None)
    validate_quote("签署重大合同", title=document.title, body_text=None, document_id=document.id)


# ── derive_quote：确定性截取，且产物永远是原文子串 ─────────────────────────


def test_derive_quote_is_always_a_substring() -> None:
    body = "  首句是导语。第二句给出了合同金额 12 亿元。" + "补充说明。" * 100
    document = make_document(body_text=body)
    quote = derive_quote(title=document.title, body_text=body)
    assert 1 <= len(quote) <= MAX_QUOTE_CHARS
    assert quote in body  # 逐字子串
    validate_quote(quote, title=document.title, body_text=body, document_id=document.id)


def test_derive_quote_falls_back_to_title() -> None:
    quote = derive_quote(title="关于签署重大合同的公告", body_text="   ")
    assert quote == "关于签署重大合同的公告"


def test_derive_quote_truncates_long_body_at_sentence_end() -> None:
    body = "第一句。" + "长正文" * 500
    quote = derive_quote(title="标题", body_text=body)
    assert quote in body
    assert len(quote) <= MAX_QUOTE_CHARS


# ── 空证据 → direction 必须 unknown + 固定文案 ──────────────────────────────


def test_empty_evidence_requires_unknown_direction() -> None:
    with pytest.raises(EvidenceValidationError) as exc:
        enforce_direction_consistency(
            direction=Direction.POSITIVE, summary="利好公告", evidence=[]
        )
    assert exc.value.failure is EvidenceFailure.DIRECTION_WITHOUT_EVIDENCE


def test_empty_evidence_requires_fixed_text() -> None:
    with pytest.raises(EvidenceValidationError) as exc:
        enforce_direction_consistency(direction=Direction.UNKNOWN, summary="暂无解读", evidence=[])
    assert exc.value.failure is EvidenceFailure.MISSING_UNKNOWN_CAUSE_TEXT


def test_empty_evidence_unknown_with_fixed_text_passes() -> None:
    enforce_direction_consistency(
        direction=Direction.UNKNOWN,
        summary=f"当日涨幅 5%。{NO_VERIFIABLE_CAUSE_TEXT}",
        evidence=[],
    )


def test_agent_schema_rejects_direction_without_evidence() -> None:
    """同一约束在 Agent 输出层就拦住（触发一次重试，而不是落库后才发现）。"""
    payload = (
        '{"summary":"利好","direction":"positive","horizon":"short","confidence":0.8,'
        '"evidence_ids":[],"unknowns":[],"risk_flags":[]}'
    )
    with pytest.raises(AgentSchemaError):
        parse_agent_output(payload)


def test_agent_schema_forbids_extra_fields() -> None:
    """模型不得自造字段（尤其不得自造概率）—— 定量概率只来自量化模型。"""
    payload = (
        '{"summary":"x","direction":"unknown","horizon":"unknown","confidence":0.1,'
        '"evidence_ids":[],"unknowns":[],"risk_flags":[],"probability_up":0.9}'
    )
    with pytest.raises(AgentSchemaError):
        parse_agent_output(payload)


# ── ID 不存在 → 整条失败，不返回部分证据 ───────────────────────────────────


def test_missing_document_id_fails_entire_analysis() -> None:
    present = make_document()
    missing_id = uuid.uuid4()
    with pytest.raises(EvidenceValidationError) as exc:
        build_evidence({present.id: present}, [present.id, missing_id])
    assert exc.value.failure is EvidenceFailure.DOCUMENT_NOT_FOUND
    assert exc.value.document_id == missing_id


async def test_expand_evidence_partial_hit_returns_nothing() -> None:
    """一半 ID 命中、一半不存在 → 抛错，**不返回**那半条命中的证据。"""
    present = make_document()
    session = cast(AsyncSession, FakeSession([present]))
    with pytest.raises(EvidenceValidationError):
        await expand_evidence(session, [present.id, uuid.uuid4()])


async def test_expand_evidence_happy_path() -> None:
    document = make_document()
    session = cast(AsyncSession, FakeSession([document]))
    evidence = await expand_evidence(session, [str(document.id)], symbol=SYMBOL, as_of=AS_OF)
    assert len(evidence) == 1
    item = evidence[0]
    assert item.document_id == document.id
    assert item.title == document.title
    assert item.source_url == document.source_url
    assert item.published_at == document.published_at
    assert item.quote and item.quote in (document.body_text or "")


async def test_expand_evidence_rejects_symbol_mismatch() -> None:
    document = make_document(symbol="000001")
    session = cast(AsyncSession, FakeSession([document]))
    with pytest.raises(EvidenceValidationError) as exc:
        await expand_evidence(session, [document.id], symbol=SYMBOL)
    assert exc.value.failure is EvidenceFailure.SYMBOL_MISMATCH


async def test_expand_evidence_rejects_future_document() -> None:
    """PIT：published_at 晚于 data_cutoff 的文档不得成为证据（spec §16 数据泄漏）。"""
    document = make_document(published_at=AS_OF + timedelta(hours=1))
    session = cast(AsyncSession, FakeSession([document]))
    with pytest.raises(EvidenceValidationError) as exc:
        await expand_evidence(session, [document.id], as_of=AS_OF)
    assert exc.value.failure is EvidenceFailure.NOT_VISIBLE_AT_CUTOFF


async def test_expand_evidence_rejects_forged_quote() -> None:
    document = make_document(body_text="公司与甲方签署了重大合同。")
    session = cast(AsyncSession, FakeSession([document]))
    with pytest.raises(EvidenceValidationError) as exc:
        await expand_evidence(session, [document.id], quotes={document.id: "公司预计净利润翻倍"})
    assert exc.value.failure is EvidenceFailure.QUOTE_NOT_VERBATIM


async def test_expand_evidence_dedupes_ids() -> None:
    document = make_document()
    session = cast(AsyncSession, FakeSession([document]))
    evidence = await expand_evidence(session, [document.id, document.id])
    assert len(evidence) == 1  # 同一文档不重复展示（验收 §15.4）


async def test_expand_evidence_rejects_malformed_id() -> None:
    session = cast(AsyncSession, FakeSession([]))
    with pytest.raises(EvidenceValidationError) as exc:
        await expand_evidence(session, ["not-a-uuid"])
    assert exc.value.failure is EvidenceFailure.MALFORMED_ID


async def test_expand_and_validate_analysis_combines_both_gates() -> None:
    document = make_document()
    session = cast(AsyncSession, FakeSession([document]))
    output = AgentOutput(
        summary="公司签署重大合同，短期偏正面。",
        direction=Direction.POSITIVE,
        horizon=EventHorizon.SHORT,
        confidence=0.6,
        evidence_ids=[document.id],
        unknowns=[],
        risk_flags=[],
    )
    evidence = await expand_and_validate_analysis(
        session,
        evidence_ids=list(output.evidence_ids),
        direction=output.direction,
        summary=output.summary,
        symbol=SYMBOL,
        as_of=AS_OF,
    )
    assert len(evidence) == 1
    assert evidence[0].to_json_dict()["document_id"] == str(document.id)
