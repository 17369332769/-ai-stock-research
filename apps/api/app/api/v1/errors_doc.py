"""OpenAPI 里的错误响应声明（spec §7 的错误表）。

集中在这里，避免每个路由各写一份、最后契约漂移。
"""

from __future__ import annotations

from typing import Any

from apps.api.app.schemas.common import ErrorResponse

_DESCRIPTIONS: dict[int, str] = {
    400: "INVALID_ARGUMENT：代码、分页或时间参数无效",
    404: "INSTRUMENT_NOT_FOUND：证券不存在或不在 MVP 市场范围",
    409: "NOT_CURRENT_UNIVERSE_MEMBER / DUPLICATE_WATCHLIST_ITEM",
    422: "INSUFFICIENT_DATA：历史样本不足，无法生成结果",
    424: "PROVIDER_UNAVAILABLE：上游数据源失败且无可用结果",
    503: "MODEL_UNAVAILABLE：没有可用模型版本",
}


def error_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    return {
        code: {"model": ErrorResponse, "description": _DESCRIPTIONS[code]} for code in codes
    }
