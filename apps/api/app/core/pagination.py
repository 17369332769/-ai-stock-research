"""游标分页（spec §7）。

游标 = 以下 JSON 的**无填充 Base64URL** 编码::

    {"v":1,"sort":"published_at","value":"2026-07-14T00:00:00Z","id":"uuid"}

无效版本或字段一律 ``400 INVALID_ARGUMENT``。limit 默认 20、最大 100。
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any

from apps.api.app.core.errors import InvalidArgument

CURSOR_VERSION = 1
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


@dataclass(frozen=True, slots=True)
class Cursor:
    sort: str
    value: str
    id: str

    def encode(self) -> str:
        payload = {"v": CURSOR_VERSION, "sort": self.sort, "value": self.value, "id": self.id}
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token: str, *, expected_sort: str) -> Cursor:
    """解码游标；任何异常都归一化为 400 INVALID_ARGUMENT，不泄漏内部细节。"""
    padding = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + padding)
        payload: Any = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise InvalidArgument("游标格式无效") from exc

    if not isinstance(payload, dict):
        raise InvalidArgument("游标格式无效")
    if payload.get("v") != CURSOR_VERSION:
        raise InvalidArgument(f"游标版本无效，期望 v={CURSOR_VERSION}")

    sort = payload.get("sort")
    value = payload.get("value")
    cursor_id = payload.get("id")
    if not isinstance(sort, str) or not isinstance(value, str) or not isinstance(cursor_id, str):
        raise InvalidArgument("游标字段无效")
    if sort != expected_sort:
        raise InvalidArgument(f"游标排序字段无效，期望 {expected_sort}")

    return Cursor(sort=sort, value=value, id=cursor_id)


def normalize_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    if limit < 1 or limit > MAX_LIMIT:
        raise InvalidArgument(f"limit 必须在 1..{MAX_LIMIT} 之间")
    return limit
