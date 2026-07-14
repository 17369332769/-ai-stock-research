"""EvidenceDTO / AnalysisDTO（spec §7.3 / §11）。

铁律（验收 §15.5）：
- ``evidence`` 每项必须含 ``document_id``、``title``、``source_url``、``published_at``、``quote``；
- ``quote`` 是原文中**连续存在**的 1..300 个字符（连续性由写入侧对照 body_text 校验）；
- 没有证据时 ``direction=unknown``，且 summary 必须包含固定文本「未找到可验证事件原因」。

**证据展开是整条成功或整条失败**：任一 document_id 不存在 ⇒ 整条分析校验失败，
绝不返回部分证据（spec §11.3）。展开逻辑见 ``apps/api/app/services/evidence_service.py``。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from apps.api.app.core.enums import (
    NO_VERIFIABLE_CAUSE_TEXT,
    AnalysisType,
    Direction,
    EventHorizon,
)
from apps.api.app.schemas.common import BaseDTO

QUOTE_MIN_LEN = 1
QUOTE_MAX_LEN = 300


class EvidenceDTO(BaseDTO):
    document_id: uuid.UUID
    title: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    published_at: datetime
    quote: str = Field(
        min_length=QUOTE_MIN_LEN,
        max_length=QUOTE_MAX_LEN,
        description="原文中连续存在的 1..300 个字符（spec §7.3）",
    )


class AnalysisDTO(BaseDTO):
    id: uuid.UUID
    symbol: str
    analysis_type: AnalysisType
    direction: Direction
    horizon: EventHorizon
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    summary: str
    evidence: list[EvidenceDTO] = Field(default_factory=list)
    model_provider: str | None = None
    model_name: str | None = None
    data_cutoff: datetime
    created_at: datetime

    @model_validator(mode="after")
    def _no_evidence_implies_unknown(self) -> Self:
        """验收 §15.5 / spec §7.3 / §11.3 / §12：没有证据时

        1. ``direction`` 必须是 ``unknown``；
        2. ``summary`` 必须包含固定文本「未找到可验证事件原因」。

        两条都 fail closed —— 一条「无证据却声称利好」或「无证据又不承认原因未知」的分析
        属于数据完整性事故，宁可让请求失败，也不把它当成事实展示给用户。
        """
        if self.evidence:
            return self

        if self.direction is not Direction.UNKNOWN:
            raise ValueError(
                f"分析 {self.id} 无证据但 direction={self.direction}，违反 spec §11.3（无证据必须 unknown）"
            )
        if NO_VERIFIABLE_CAUSE_TEXT not in self.summary:
            raise ValueError(
                f"分析 {self.id} 无证据，summary 必须包含固定文本"
                f"「{NO_VERIFIABLE_CAUSE_TEXT}」（spec §7.3 / §12 / 验收 §15.5）"
            )
        return self
