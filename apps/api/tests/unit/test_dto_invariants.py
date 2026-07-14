"""DTO 不变量（spec §7 / §9.4 / §11.3 / §13.2）。

这些是 Pydantic 层的 fail-closed 护栏：坏数据到不了客户端。
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from apps.api.app.core.enums import (
    NO_VERIFIABLE_CAUSE_TEXT,
    RESEARCH_ONLY_DISCLAIMER,
    AnalysisType,
    ConfidenceLabel,
    Direction,
    EventHorizon,
    PredictionHorizon,
)
from apps.api.app.schemas.analyses import AnalysisDTO, EvidenceDTO
from apps.api.app.schemas.predictions import (
    PredictionDTO,
    PredictionModelRefDTO,
    ReturnIntervalDTO,
    ScorecardDTO,
)
from apps.api.tests.conftest import AT_0950, SYMBOL


def make_evidence() -> EvidenceDTO:
    return EvidenceDTO(
        document_id=uuid.uuid4(),
        title="关于回购股份的公告",
        source_url="http://www.cninfo.com.cn/x",
        published_at=AT_0950,
        quote="公司拟以自有资金回购股份",
    )


def make_prediction(
    *, better_than_baseline: bool, confidence: ConfidenceLabel
) -> PredictionDTO:
    return PredictionDTO(
        id=uuid.uuid4(),
        symbol=SYMBOL,
        horizon=PredictionHorizon.NEXT_5D,
        as_of=AT_0950,
        target_at=AT_0950,
        data_cutoff=AT_0950,
        reference_price=1215.04,
        probability_up=0.38,
        expected_return=-0.011,
        return_interval=ReturnIntervalDTO(p20=-0.041, p80=0.019),
        confidence=confidence,
        model=PredictionModelRefDTO(
            key="a_share_5d_lightgbm",
            version="2026.07.14.1",
            better_than_baseline=better_than_baseline,
        ),
    )


# ── EvidenceDTO ──────────────────────────────────────────────────────────────
def test_evidence_quote_max_300_chars() -> None:
    with pytest.raises(ValidationError):
        EvidenceDTO(
            document_id=uuid.uuid4(),
            title="t",
            source_url="u",
            published_at=AT_0950,
            quote="字" * 301,
        )


def test_evidence_quote_min_1_char() -> None:
    with pytest.raises(ValidationError):
        EvidenceDTO(
            document_id=uuid.uuid4(),
            title="t",
            source_url="u",
            published_at=AT_0950,
            quote="",
        )


def test_evidence_requires_source_url() -> None:
    with pytest.raises(ValidationError):
        EvidenceDTO(
            document_id=uuid.uuid4(),
            title="t",
            source_url="",
            published_at=AT_0950,
            quote="q",
        )


# ── AnalysisDTO ──────────────────────────────────────────────────────────────
def test_analysis_without_evidence_must_be_unknown() -> None:
    """验收 §15.5：无证据时方向必须是 unknown。"""
    with pytest.raises(ValidationError):
        AnalysisDTO(
            id=uuid.uuid4(),
            symbol=SYMBOL,
            analysis_type=AnalysisType.ANOMALY,
            direction=Direction.POSITIVE,  # 无证据却声称利好 ⇒ 拒绝
            horizon=EventHorizon.SHORT,
            confidence=0.8,
            summary="放量大涨",
            evidence=[],
            data_cutoff=AT_0950,
            created_at=AT_0950,
        )


def test_analysis_without_evidence_unknown_is_accepted() -> None:
    dto = AnalysisDTO(
        id=uuid.uuid4(),
        symbol=SYMBOL,
        analysis_type=AnalysisType.ANOMALY,
        direction=Direction.UNKNOWN,
        horizon=EventHorizon.UNKNOWN,
        confidence=None,
        summary=f"当日放量上涨 5.2%，{NO_VERIFIABLE_CAUSE_TEXT}",
        evidence=[],
        data_cutoff=AT_0950,
        created_at=AT_0950,
    )
    assert dto.direction is Direction.UNKNOWN
    assert NO_VERIFIABLE_CAUSE_TEXT in dto.summary


def test_analysis_without_evidence_requires_fixed_unknown_cause_text() -> None:
    """验收 §15.5 / spec §7.3 §12：无证据时 summary 必须包含「未找到可验证事件原因」。

    只写 direction=unknown 是不够的 —— 用户必须看到那句固定文案。
    """
    with pytest.raises(ValidationError):
        AnalysisDTO(
            id=uuid.uuid4(),
            symbol=SYMBOL,
            analysis_type=AnalysisType.ANOMALY,
            direction=Direction.UNKNOWN,
            horizon=EventHorizon.UNKNOWN,
            confidence=None,
            summary="当日放量上涨 5.2%，可能与市场情绪有关",  # 没有固定文案 ⇒ 拒绝
            evidence=[],
            data_cutoff=AT_0950,
            created_at=AT_0950,
        )


def test_analysis_with_evidence_may_have_direction() -> None:
    dto = AnalysisDTO(
        id=uuid.uuid4(),
        symbol=SYMBOL,
        analysis_type=AnalysisType.DOCUMENT,
        direction=Direction.POSITIVE,
        horizon=EventHorizon.SHORT,
        confidence=0.6,
        summary="公司公告回购",
        evidence=[make_evidence()],
        data_cutoff=AT_0950,
        created_at=AT_0950,
    )
    assert len(dto.evidence) == 1


def test_analysis_confidence_bounded() -> None:
    with pytest.raises(ValidationError):
        AnalysisDTO(
            id=uuid.uuid4(),
            symbol=SYMBOL,
            analysis_type=AnalysisType.DOCUMENT,
            direction=Direction.POSITIVE,
            horizon=EventHorizon.SHORT,
            confidence=1.5,
            summary="x",
            evidence=[make_evidence()],
            data_cutoff=AT_0950,
            created_at=AT_0950,
        )


# ── PredictionDTO ────────────────────────────────────────────────────────────
def test_prediction_carries_disclaimer() -> None:
    """spec §13.2：任何预测区域都显示"仅供研究，不构成投资建议"。"""
    dto = make_prediction(better_than_baseline=False, confidence=ConfidenceLabel.LOW)
    assert dto.disclaimer == RESEARCH_ONLY_DISCLAIMER


def test_prediction_has_probability_and_interval_together() -> None:
    """spec §13.2：概率和区间必须同时出现，不允许只显示看涨/看跌。"""
    dto = make_prediction(better_than_baseline=False, confidence=ConfidenceLabel.LOW)
    payload = dto.model_dump(mode="json")
    assert "probability_up" in payload
    assert payload["return_interval"]["p20"] < payload["return_interval"]["p80"]


def test_prediction_not_better_than_baseline_forces_low_confidence() -> None:
    """spec §9.4：未优于基准的模型置信度只能为 low。"""
    with pytest.raises(ValidationError):
        make_prediction(better_than_baseline=False, confidence=ConfidenceLabel.HIGH)


def test_prediction_better_than_baseline_allows_medium() -> None:
    dto = make_prediction(better_than_baseline=True, confidence=ConfidenceLabel.MEDIUM)
    assert dto.confidence is ConfidenceLabel.MEDIUM


def test_prediction_probability_bounded() -> None:
    with pytest.raises(ValidationError):
        PredictionDTO(
            id=uuid.uuid4(),
            symbol=SYMBOL,
            horizon=PredictionHorizon.NEXT_5D,
            as_of=AT_0950,
            target_at=AT_0950,
            data_cutoff=AT_0950,
            reference_price=1.0,
            probability_up=1.4,  # 越界
            expected_return=0.0,
            return_interval=ReturnIntervalDTO(p20=-0.1, p80=0.1),
            confidence=ConfidenceLabel.LOW,
            model=PredictionModelRefDTO(key="k", version="v", better_than_baseline=False),
        )


def test_return_interval_rejects_inverted_bounds() -> None:
    with pytest.raises(ValidationError):
        ReturnIntervalDTO(p20=0.05, p80=-0.05)


# ── ScorecardDTO ─────────────────────────────────────────────────────────────
def test_scorecard_counts_must_add_up() -> None:
    """spec §7.4：settled_count + pending_count = eligible_count。"""
    with pytest.raises(ValidationError):
        ScorecardDTO(
            model_key="k",
            window=100,
            eligible_count=100,
            settled_count=98,
            pending_count=5,  # 98 + 5 != 100
            direction_accuracy=0.54,
            mae=0.018,
            brier_score=0.247,
            baseline_direction_accuracy=0.52,
            baseline_mae=0.019,
            baseline_brier_score=0.250,
            better_than_baseline=False,
            calculated_at=AT_0950,
        )


def test_scorecard_accepts_consistent_counts() -> None:
    dto = ScorecardDTO(
        model_key="a_share_5d_lightgbm",
        window=100,
        eligible_count=100,
        settled_count=98,
        pending_count=2,
        direction_accuracy=0.54,
        mae=0.018,
        brier_score=0.247,
        baseline_direction_accuracy=0.52,
        baseline_mae=0.019,
        baseline_brier_score=0.250,
        better_than_baseline=False,
        calculated_at=AT_0950,
    )
    assert dto.settled_count + dto.pending_count == dto.eligible_count


def test_dto_rejects_unknown_fields() -> None:
    """DTO 是契约：多出来的字段必须炸，而不是悄悄吞掉。"""
    with pytest.raises(ValidationError):
        ReturnIntervalDTO(p20=0.0, p80=0.1, p50=0.05)  # type: ignore[call-arg]
