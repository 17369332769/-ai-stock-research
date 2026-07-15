"""请求中间件与异常处理器（spec §7 / §14.3 / §14.4）。

- 每请求生成 ``request_id``，写入日志上下文与 ``X-Request-Id`` 响应头；
- ``AppError`` → 统一错误信封，HTTP 状态由 ``AppError.status_code`` 唯一决定；
- 请求参数校验失败 → **400 INVALID_ARGUMENT**（而不是 FastAPI 默认的 422 ——
  在本 spec 里 422 专属 INSUFFICIENT_DATA）；
- 未捕获异常 → 500，响应体里**不泄漏堆栈**，堆栈只进本地日志。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response

from apps.api.app.core.errors import ERROR_STATUS, AppError, ErrorCode
from apps.api.app.core.logging import METRICS, get_logger, get_request_id, set_request_id
from apps.api.app.schemas.common import ErrorBody, ErrorResponse

REQUEST_ID_HEADER = "X-Request-Id"

# 浏览器中的 Next.js 前端与 API 分别监听 3000/8000，属于不同 origin。
# 仅放行本机前端，避免把无登录的研究 API 暴露给任意网页脚本。
LOCAL_WEB_ORIGINS = ["http://127.0.0.1:3000", "http://localhost:3000"]

logger = get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """生成 request_id、记录访问日志与延迟指标。"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        set_request_id(request_id)
        request.state.request_id = request_id

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # 让异常处理器负责响应体；这里只补上访问日志与指标
            latency_ms = (time.perf_counter() - started) * 1000
            METRICS.record_request(path=request.url.path, status=500, latency_ms=latency_ms)
            logger.exception(
                "请求处理失败",
                extra={"method": request.method, "path": request.url.path, "latency_ms": latency_ms},
            )
            raise

        latency_ms = (time.perf_counter() - started) * 1000
        response.headers[REQUEST_ID_HEADER] = request_id
        METRICS.record_request(path=request.url.path, status=response.status_code, latency_ms=latency_ms)
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "latency_ms": round(latency_ms, 2),
            },
        )
        return response


def _error_response(code: ErrorCode, message: str, status_code: int) -> JSONResponse:
    request_id = get_request_id()
    body = ErrorResponse(error=ErrorBody(code=code.value, message=message, request_id=request_id))
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers={REQUEST_ID_HEADER: request_id} if request_id else None,
    )


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    logger.warning(
        "业务错误",
        extra={"code": exc.code.value, "path": request.url.path, "status": exc.status_code},
    )
    return _error_response(exc.code, exc.message, exc.status_code)


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI/Pydantic 入参校验失败 ⇒ 400 INVALID_ARGUMENT（spec §7）。

    默认的 422 在本 spec 里被 INSUFFICIENT_DATA 占用，必须改写，否则错误码语义会串。
    """
    assert isinstance(exc, RequestValidationError)
    details = "; ".join(
        f"{'.'.join(str(p) for p in err.get('loc', ())[1:])}: {err.get('msg', '')}".strip(": ")
        for err in exc.errors()
    )
    message = f"请求参数无效：{details}" if details else "请求参数无效"
    logger.warning("入参校验失败", extra={"path": request.url.path, "detail": details})
    return _error_response(
        ErrorCode.INVALID_ARGUMENT, message, ERROR_STATUS[ErrorCode.INVALID_ARGUMENT]
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """把 Starlette 的 HTTPException（404 路由不存在、405 方法不允许等）套进统一错误信封。

    spec §7 的错误码表里没有 ROUTE_NOT_FOUND / METHOD_NOT_ALLOWED，
    这里退而求其次复用最接近的码；HTTP 状态仍然如实透传（405 就是 405）。
    """
    assert isinstance(exc, StarletteHTTPException)
    code = ErrorCode.INSTRUMENT_NOT_FOUND if exc.status_code == 404 else ErrorCode.INVALID_ARGUMENT
    message = str(exc.detail) if exc.detail else "请求无法处理"
    logger.warning(
        "HTTP 异常",
        extra={"path": request.url.path, "status": exc.status_code, "code": code.value},
    )
    return _error_response(code, message, exc.status_code)


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """未捕获异常 ⇒ 500，且**不把堆栈泄漏给客户端**（spec §14.3）。"""
    logger.exception("未处理异常", extra={"path": request.url.path})
    METRICS.increment("unhandled_errors")
    request_id = get_request_id()
    body = ErrorResponse(
        error=ErrorBody(
            code="INTERNAL_ERROR",
            message="服务内部错误",  # 不含类型、堆栈或文件路径
            request_id=request_id,
        )
    )
    return JSONResponse(
        status_code=500,
        content=body.model_dump(mode="json"),
        headers={REQUEST_ID_HEADER: request_id} if request_id else None,
    )


def install_middleware(app: FastAPI) -> None:
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=LOCAL_WEB_ORIGINS,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
