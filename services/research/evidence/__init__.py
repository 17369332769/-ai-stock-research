"""证据约束层：Agent 只能引用检索到的文档，引文必须逐字可验证（spec §4.2 / §7.3 / §11.3）。"""

from __future__ import annotations

from services.research.evidence.dto import (
    MAX_QUOTE_CHARS,
    MIN_QUOTE_CHARS,
    DocumentLike,
    EvidenceDTO,
)
from services.research.evidence.errors import EvidenceFailure, EvidenceValidationError
from services.research.evidence.expander import expand_and_validate_analysis, expand_evidence
from services.research.evidence.validator import (
    build_evidence,
    coerce_uuid,
    dedupe_ids,
    derive_quote,
    enforce_direction_consistency,
    validate_quote,
)

__all__ = [
    "MAX_QUOTE_CHARS",
    "MIN_QUOTE_CHARS",
    "DocumentLike",
    "EvidenceDTO",
    "EvidenceFailure",
    "EvidenceValidationError",
    "build_evidence",
    "coerce_uuid",
    "dedupe_ids",
    "derive_quote",
    "enforce_direction_consistency",
    "expand_and_validate_analysis",
    "expand_evidence",
    "validate_quote",
]
