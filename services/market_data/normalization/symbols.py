"""A 股代码规范化。所有 symbol 进入领域层之前必须过这里。"""

from __future__ import annotations

from typing import Literal

from apps.api.app.core.enums import Exchange
from apps.api.app.core.errors import InvalidArgument

_PREFIXES = ("SH", "SZ", "BJ")


def normalize_symbol(raw: str) -> str:
    """``sh600519`` / ``600519.SH`` / `` 600519 `` → ``600519``。

    非沪深 A 股（含北交所 8/4 开头）一律 ``InvalidArgument`` —— MVP 只做沪深300（spec §2）。
    """
    text = str(raw).strip().upper()
    for prefix in _PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    text = text.split(".")[0].strip()
    if not (len(text) == 6 and text.isdigit()):
        raise InvalidArgument(f"非法证券代码：{raw!r}（应为 6 位数字的沪深 A 股代码）")
    if not text.startswith(("6", "9", "0", "2", "3")):
        raise InvalidArgument(f"{text} 不是沪深 A 股代码（MVP 只支持沪深两市）")
    return text


def exchange_of(symbol: str) -> Literal["SSE", "SZSE"]:
    """代码前缀 → 交易所。与 ``instruments.exchange`` 的 CHECK 约束一致。"""
    code = normalize_symbol(symbol)
    if code.startswith(("6", "9")):
        return Exchange.SSE.value
    return Exchange.SZSE.value


def is_valid_symbol(raw: str) -> bool:
    try:
        normalize_symbol(raw)
    except InvalidArgument:
        return False
    return True
