"""统一错误契约（spec §7）。

错误响应恒为::

    {"error": {"code": "...", "message": "...", "request_id": "..."}}

错误码与 HTTP 状态一一对应，禁止在别处新造错误码。
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"  # 400
    INSTRUMENT_NOT_FOUND = "INSTRUMENT_NOT_FOUND"  # 404
    NOT_CURRENT_UNIVERSE_MEMBER = "NOT_CURRENT_UNIVERSE_MEMBER"  # 409
    DUPLICATE_WATCHLIST_ITEM = "DUPLICATE_WATCHLIST_ITEM"  # 409
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"  # 422
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"  # 424
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"  # 503


ERROR_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_ARGUMENT: 400,
    ErrorCode.INSTRUMENT_NOT_FOUND: 404,
    ErrorCode.NOT_CURRENT_UNIVERSE_MEMBER: 409,
    ErrorCode.DUPLICATE_WATCHLIST_ITEM: 409,
    ErrorCode.INSUFFICIENT_DATA: 422,
    ErrorCode.PROVIDER_UNAVAILABLE: 424,
    ErrorCode.MODEL_UNAVAILABLE: 503,
}


class AppError(Exception):
    """所有对外错误的基类。HTTP 状态由 ``code`` 唯一决定。"""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def status_code(self) -> int:
        return ERROR_STATUS[self.code]


class InvalidArgument(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.INVALID_ARGUMENT, message)


class InstrumentNotFound(AppError):
    def __init__(self, symbol: str) -> None:
        super().__init__(ErrorCode.INSTRUMENT_NOT_FOUND, f"证券 {symbol} 不存在或不在 MVP 市场范围内")


class NotCurrentUniverseMember(AppError):
    def __init__(self, symbol: str, universe: str = "CSI300") -> None:
        super().__init__(
            ErrorCode.NOT_CURRENT_UNIVERSE_MEMBER,
            f"{symbol} 不是查询日 {universe} 当前成分股",
        )


class DuplicateWatchlistItem(AppError):
    def __init__(self, symbol: str) -> None:
        super().__init__(ErrorCode.DUPLICATE_WATCHLIST_ITEM, f"{symbol} 已在自选股中")


class InsufficientData(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.INSUFFICIENT_DATA, message)


class ProviderUnavailable(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.PROVIDER_UNAVAILABLE, message)


class ModelUnavailable(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.MODEL_UNAVAILABLE, message)
