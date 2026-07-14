"""游标分页（spec §7）。游标 = 无填充 Base64URL JSON；无效版本或字段 ⇒ 400 INVALID_ARGUMENT。"""

from __future__ import annotations

import base64
import json

import pytest

from apps.api.app.core.errors import ErrorCode, InvalidArgument
from apps.api.app.core.pagination import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    Cursor,
    decode_cursor,
    normalize_limit,
)


def encode_payload(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_cursor_roundtrip() -> None:
    cursor = Cursor(sort="published_at", value="2026-07-14T00:00:00+08:00", id="abc")
    decoded = decode_cursor(cursor.encode(), expected_sort="published_at")
    assert decoded == cursor


def test_cursor_encoding_has_no_padding() -> None:
    """spec 明确要求**无填充** Base64URL。"""
    token = Cursor(sort="published_at", value="2026-07-14T00:00:00Z", id="x").encode()
    assert "=" not in token


def test_cursor_payload_matches_spec_shape() -> None:
    token = Cursor(sort="published_at", value="2026-07-14T00:00:00Z", id="uuid").encode()
    padded = token + "=" * (-len(token) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded))
    assert payload == {
        "v": 1,
        "sort": "published_at",
        "value": "2026-07-14T00:00:00Z",
        "id": "uuid",
    }


def test_decode_rejects_wrong_version() -> None:
    token = encode_payload({"v": 2, "sort": "published_at", "value": "x", "id": "y"})
    with pytest.raises(InvalidArgument) as exc:
        decode_cursor(token, expected_sort="published_at")
    assert exc.value.code is ErrorCode.INVALID_ARGUMENT


def test_decode_rejects_wrong_sort_field() -> None:
    token = Cursor(sort="created_at", value="x", id="y").encode()
    with pytest.raises(InvalidArgument):
        decode_cursor(token, expected_sort="published_at")


def test_decode_rejects_missing_field() -> None:
    token = encode_payload({"v": 1, "sort": "published_at", "value": "x"})
    with pytest.raises(InvalidArgument):
        decode_cursor(token, expected_sort="published_at")


def test_decode_rejects_non_base64() -> None:
    with pytest.raises(InvalidArgument):
        decode_cursor("!!!not-base64!!!", expected_sort="published_at")


def test_decode_rejects_non_object_payload() -> None:
    token = base64.urlsafe_b64encode(b"[1,2,3]").decode().rstrip("=")
    with pytest.raises(InvalidArgument):
        decode_cursor(token, expected_sort="published_at")


def test_normalize_limit_default() -> None:
    assert normalize_limit(None) == DEFAULT_LIMIT == 20


def test_normalize_limit_accepts_max() -> None:
    assert normalize_limit(MAX_LIMIT) == 100


@pytest.mark.parametrize("bad", [0, -1, MAX_LIMIT + 1])
def test_normalize_limit_rejects_out_of_range(bad: int) -> None:
    with pytest.raises(InvalidArgument):
        normalize_limit(bad)
