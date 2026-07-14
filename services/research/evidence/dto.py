"""证据 DTO（spec §7.3）。

``analyses.evidence`` 是 JSONB 数组，每项必须包含 ``document_id`` / ``title`` /
``source_url`` / ``published_at`` / ``quote``；``quote`` 必须是原文中连续存在的 1–300 字符。

这里同时定义 Agent 内部的"文档只读视图"协议 ``DocumentLike``：ORM ``Document`` 天然满足它，
测试可以用轻量对象满足它，从而证据校验的核心逻辑不依赖数据库。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

# spec §7.3：quote 必须是原文中连续存在的 1 至 300 个字符
MIN_QUOTE_CHARS = 1
MAX_QUOTE_CHARS = 300


@runtime_checkable
class DocumentLike(Protocol):
    """证据校验只需要文档的这几个字段。ORM ``Document`` 结构上满足本协议。"""

    @property
    def id(self) -> uuid.UUID: ...

    @property
    def symbol(self) -> str | None: ...

    @property
    def title(self) -> str: ...

    @property
    def body_text(self) -> str | None: ...

    @property
    def source_url(self) -> str: ...

    @property
    def published_at(self) -> datetime: ...

    @property
    def observed_at(self) -> datetime: ...


class EvidenceDTO(BaseModel):
    """一条证据。任一字段缺失或 quote 非原文子串 → 整条分析校验失败（spec §11.3）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: uuid.UUID
    title: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    published_at: datetime
    quote: str = Field(min_length=MIN_QUOTE_CHARS, max_length=MAX_QUOTE_CHARS)

    @field_validator("published_at")
    @classmethod
    def _require_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("拒绝 naive datetime：所有时间必须带时区")
        return v

    def to_json_dict(self) -> dict[str, Any]:
        """落 JSONB / 出 API 用的可序列化形态。"""
        return self.model_dump(mode="json")
