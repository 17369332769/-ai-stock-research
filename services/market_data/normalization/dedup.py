"""文档去重（spec §8）。

两条独立规则，缺一不可：

- **公告**：按 ``content_hash`` 去重（同一份公告可能在不同板块页重复挂出，URL 不同）。
- **新闻**：按 **URL** 和 ``content_hash`` 双规则去重（同一篇稿子被多家转载 → 内容同、URL 异；
  同一 URL 被重复采集 → URL 同）。

``content_hash`` 的输入**刻意不含 source_url 与 observed_at**：
它描述"这份内容是什么"，不描述"我们从哪儿、什么时候拿到的"。
含 URL 会让转载稿哈希各不相同，去重失效；含 observed_at 会让每次采集都产生新行。
``documents.content_hash`` 上有 UNIQUE 约束，因此这是 DB 幂等的唯一依据。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable

from apps.api.app.core.enums import DocumentType
from services.market_data.contracts import DocumentRecord
from services.market_data.normalization.validators import Rejection, RejectReason

_WHITESPACE = re.compile(r"\s+")


def _canonical(text: str | None) -> str:
    """归一化：NFKC + 折叠空白 + 去首尾。

    全角/半角、多余空格、换行差异不应该产生不同的哈希 —— 否则同一份公告
    在上游换一次排版就会被当成新公告重复入库。
    """
    if text is None:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    return _WHITESPACE.sub(" ", normalized).strip()


def content_hash(document: DocumentRecord) -> str:
    """sha256(document_type | symbol | title | body_text) → 64 位十六进制。"""
    parts = [
        document.document_type,
        document.symbol or "",
        _canonical(document.title),
        _canonical(document.body_text),
    ]
    payload = "".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def dedup_documents(
    documents: Iterable[DocumentRecord],
) -> tuple[list[tuple[str, DocumentRecord]], list[Rejection]]:
    """批内去重 → (保留的 (content_hash, record)，被判重的拒收记录)。

    只做**批内**去重；与库内已有记录的去重由 ``ingest`` 的 ``ON CONFLICT DO NOTHING``
    （content_hash UNIQUE）+ 新闻 URL 预查询完成。
    """
    kept: list[tuple[str, DocumentRecord]] = []
    dropped: list[Rejection] = []
    seen_hash: set[str] = set()
    seen_url: set[str] = set()

    for document in documents:
        digest = content_hash(document)
        if digest in seen_hash:
            dropped.append(
                Rejection(
                    f"doc:{document.source_url}",
                    RejectReason.DUPLICATE,
                    f"content_hash 重复：{digest}",
                )
            )
            continue
        if document.document_type == DocumentType.NEWS.value and document.source_url in seen_url:
            dropped.append(
                Rejection(
                    f"doc:{document.source_url}",
                    RejectReason.DUPLICATE,
                    f"新闻 URL 重复：{document.source_url}",
                )
            )
            continue
        seen_hash.add(digest)
        if document.document_type == DocumentType.NEWS.value:
            seen_url.add(document.source_url)
        kept.append((digest, document))

    return kept, dropped
