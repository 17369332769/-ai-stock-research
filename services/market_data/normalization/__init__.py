"""规范化与数据质量：字段范围、时间、OHLC 一致性、去重。

外部数据必须先经这里校验、去重，才能落库进入模型与 Agent（spec §4.2）。
"""

from __future__ import annotations

from services.market_data.normalization.dedup import content_hash, dedup_documents
from services.market_data.normalization.symbols import exchange_of, is_valid_symbol, normalize_symbol
from services.market_data.normalization.validators import (
    CLOCK_SKEW,
    MAX_PRICE,
    Rejection,
    RejectReason,
    age_seconds,
    freshness_of,
    validate_bar,
    validate_document,
    validate_quote,
)

__all__ = [
    "CLOCK_SKEW",
    "MAX_PRICE",
    "RejectReason",
    "Rejection",
    "age_seconds",
    "content_hash",
    "dedup_documents",
    "exchange_of",
    "freshness_of",
    "is_valid_symbol",
    "normalize_symbol",
    "validate_bar",
    "validate_document",
    "validate_quote",
]
