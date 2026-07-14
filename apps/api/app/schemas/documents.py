"""DocumentDTO（spec §7.3）。公告与新闻按内容哈希去重（验收 §15.4，由 UNIQUE(content_hash) 保证）。"""

from __future__ import annotations

import uuid
from datetime import datetime

from apps.api.app.core.enums import DocumentType
from apps.api.app.models.tables import Document
from apps.api.app.schemas.common import BaseDTO


class DocumentDTO(BaseDTO):
    id: uuid.UUID
    symbol: str | None = None
    document_type: DocumentType
    title: str
    source: str
    source_url: str
    published_at: datetime
    observed_at: datetime

    @classmethod
    def from_row(cls, row: Document) -> DocumentDTO:
        return cls(
            id=row.id,
            symbol=row.symbol,
            document_type=DocumentType(row.document_type),
            title=row.title,
            source=row.source,
            source_url=row.source_url,
            published_at=row.published_at,
            observed_at=row.observed_at,
        )
