"""证据校验错误。

证据校验是**全有或全无**的（spec §11.3）：任一条证据不合法，整条分析校验失败，
不允许"返回一部分证据"。因此这里只有一个异常类型，携带失败原因与失败的证据 ID。

本异常刻意不继承 ``AppError``：证据校验既发生在写入路径（Agent 产出落库前），
也发生在读取路径（API 展开 ``evidence_ids``）。两条路径对外映射成什么 HTTP 状态，
由 API 层决定，不在这里预先绑定错误码。
"""

from __future__ import annotations

import uuid
from enum import StrEnum


class EvidenceFailure(StrEnum):
    """证据校验失败的原因分类（用于日志与测试断言，不直接对外展示）。"""

    MALFORMED_ID = "malformed_id"  # evidence_ids 中出现非 UUID
    DOCUMENT_NOT_FOUND = "document_not_found"  # 引用的文档在同一事务快照中不存在
    SYMBOL_MISMATCH = "symbol_mismatch"  # 文档不属于被分析的证券
    NOT_VISIBLE_AT_CUTOFF = "not_visible_at_cutoff"  # 文档在 data_cutoff 之后才可见（PIT 违规）
    QUOTE_NOT_VERBATIM = "quote_not_verbatim"  # quote 不是原文中连续存在的子串
    QUOTE_LENGTH = "quote_length"  # quote 长度不在 1..300
    DIRECTION_WITHOUT_EVIDENCE = "direction_without_evidence"  # 无证据却给出了非 unknown 方向
    MISSING_UNKNOWN_CAUSE_TEXT = "missing_unknown_cause_text"  # 无证据却没有固定文案


class EvidenceValidationError(Exception):
    """整条分析的证据校验失败。调用方必须丢弃整条分析，不得返回部分证据。"""

    def __init__(
        self,
        failure: EvidenceFailure,
        message: str,
        *,
        document_id: uuid.UUID | str | None = None,
    ) -> None:
        super().__init__(message)
        self.failure = failure
        self.message = message
        self.document_id = document_id

    def __str__(self) -> str:
        if self.document_id is None:
            return f"[{self.failure}] {self.message}"
        return f"[{self.failure}] {self.message}（document_id={self.document_id}）"
