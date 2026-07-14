"""统一响应信封（spec §7）。

列表响应::

    {"data": [], "page": {"next_cursor": null, "has_more": false}, "request_id": "uuid"}

错误响应::

    {"error": {"code": "DATA_STALE", "message": "...", "request_id": "uuid"}}

所有响应都带 ``request_id``（spec §7 / §14.4）。

**ORM 模型不得直接序列化给客户端**（spec §7）：所有 DTO 都从 ORM 行显式映射，
禁止 ``from_attributes=True`` 之类的隐式转换，防止新增数据库列意外泄漏到 API 契约。
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class BaseDTO(BaseModel):
    """所有对外 DTO 的基类。

    - ``extra="forbid"``：契约漂移在构造期就炸掉，而不是悄悄多出一个字段；
    - ``protected_namespaces=()``：本领域里 ``model_key`` / ``model_provider`` / ``model_name``
      是**业务字段**（模型版本账本），不是 Pydantic 内部命名空间。不关掉这个保护，
      每次进程启动都会刷一串无意义的 UserWarning。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())


def to_float(value: Decimal | float | None) -> float | None:
    """Decimal → JSON number。

    spec 的 JSON 示例里价格与收益率都是**数字**（``"price": 1215.04`），
    而 Pydantic v2 默认把 Decimal 序列化成字符串，因此 DTO 边界一律转 float。
    数据库账本仍然保存 Decimal，精度不受影响。
    """
    if value is None:
        return None
    return float(value)


def require_float(value: Decimal | float) -> float:
    return float(value)


class PageInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    next_cursor: str | None = None
    has_more: bool = False


class ListResponse[T](BaseModel):
    """列表信封。"""

    model_config = ConfigDict(extra="forbid")

    data: list[T]
    page: PageInfo
    request_id: str


class ItemResponse[T](BaseModel):
    """单对象信封（``POST /watchlist``、成绩单等 spec 明示带 ``data`` 包裹的响应）。"""

    model_config = ConfigDict(extra="forbid")

    data: T
    request_id: str


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    """错误信封。HTTP 状态由 ``AppError.status_code`` 唯一决定（不在路由里硬编码）。"""

    model_config = ConfigDict(extra="forbid")

    error: ErrorBody
