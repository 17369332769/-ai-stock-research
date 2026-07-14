"""FastAPI 应用入口。

- MVP **无登录**：只允许通过绑定在 127.0.0.1 的本地服务访问（spec §7 / §14.3）；
  绑定地址由 uvicorn/Docker 层强制，这里再声明一次默认值，避免有人顺手改成 0.0.0.0。
- 挂载路由 + 中间件 + 异常处理器；
- OpenAPI 是 DTO 的唯一真相，CI 比对 ``openapi.json`` 快照（spec §7）。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from apps.api.app.api.v1 import api_router
from apps.api.app.core.db import dispose_engine
from apps.api.app.core.errors import ERROR_STATUS, ErrorCode
from apps.api.app.core.logging import configure_logging, get_logger
from apps.api.app.core.middleware import install_middleware
from apps.api.app.core.settings import get_settings
from apps.api.app.schemas.common import ErrorResponse

# 只绑定回环地址（spec §4.1 / §14.3：Docker 端口默认只绑定 127.0.0.1）
LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

API_TITLE = "A股 AI 研究助手 API"
API_VERSION = "1.1"
API_DESCRIPTION = (
    "个人使用、仅沪深300成分股、OpenBB 统一数据网关、免费近实时数据、无自动交易。\n\n"
    "所有响应都带 request_id；列表使用游标分页（limit 默认 20、最大 100）。\n"
    "预测仅供研究，不构成投资建议。"
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    get_logger(__name__).info("api 启动", extra={"app_env": settings.app_env})
    yield
    await dispose_engine()


_VALIDATION_ERROR_REF = "#/components/schemas/HTTPValidationError"
_ERROR_RESPONSE_REF = "#/components/schemas/ErrorResponse"
_BAD_REQUEST = str(ERROR_STATUS[ErrorCode.INVALID_ARGUMENT])  # "400"
_UNPROCESSABLE = "422"


def _error_content() -> dict[str, Any]:
    return {"application/json": {"schema": {"$ref": _ERROR_RESPONSE_REF}}}


def _rewrite_validation_responses(schema: dict[str, Any]) -> dict[str, Any]:
    """把 FastAPI 自动生成的 422 HTTPValidationError 改写成本项目真正会返回的 400。

    入参校验失败经 ``validation_error_handler`` 统一变成 **400 INVALID_ARGUMENT**
    （spec 把 422 留给 INSUFFICIENT_DATA）。如果不改写，openapi.json —— 也就是
    spec §7 认定的"DTO 唯一真相" —— 会向客户端**撒谎**：宣称一个永远不会发生的
    422 ``{"detail": [...]}``，却不提真正会发生的 400 错误信封。
    """
    for operations in schema.get("paths", {}).values():
        for operation in operations.values():
            responses = operation.get("responses", {})
            phantom = responses.get(_UNPROCESSABLE, {})
            is_phantom = (
                phantom.get("content", {})
                .get("application/json", {})
                .get("schema", {})
                .get("$ref")
                == _VALIDATION_ERROR_REF
            )
            if not is_phantom:
                continue  # 路由自己声明的真 422（INSUFFICIENT_DATA）不能动

            del responses[_UNPROCESSABLE]
            responses.setdefault(
                _BAD_REQUEST,
                {
                    "description": "INVALID_ARGUMENT：代码、分页或时间参数无效",
                    "content": _error_content(),
                },
            )

    # 改写后 HTTPValidationError / ValidationError 可能已无人引用，移除以免误导客户端
    rendered = json.dumps(schema)
    for orphan in ("HTTPValidationError", "ValidationError"):
        if f"#/components/schemas/{orphan}" not in rendered:
            schema.get("components", {}).get("schemas", {}).pop(orphan, None)
    return schema


def build_openapi(app: FastAPI) -> dict[str, Any]:
    schema = get_openapi(
        title=API_TITLE,
        version=API_VERSION,
        description=API_DESCRIPTION,
        routes=app.routes,
    )
    # 确保错误信封始终在 components 里（即使某天所有路由都没显式声明错误响应）
    schema.setdefault("components", {}).setdefault("schemas", {}).setdefault(
        "ErrorResponse", ErrorResponse.model_json_schema(ref_template=_ERROR_RESPONSE_REF)
    )
    return _rewrite_validation_responses(schema)


def create_app() -> FastAPI:
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description=API_DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    install_middleware(app)
    app.include_router(api_router)

    def custom_openapi() -> dict[str, Any]:
        if not app.openapi_schema:
            app.openapi_schema = build_openapi(app)
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "apps.api.app.main:app",
        host=LOOPBACK_HOST,  # 绝不监听 0.0.0.0
        port=DEFAULT_PORT,
        log_config=None,  # 日志由 core/logging.py 统一接管（含脱敏）
    )
