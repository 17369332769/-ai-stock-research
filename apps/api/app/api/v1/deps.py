"""路由公共依赖。

- ``now`` 一律经 ``get_clock()``，禁止 ``datetime.now()``（测试用 FixedClock 注入）；
- 分页参数解码集中在这里，非法游标一律 400 INVALID_ARGUMENT；
- 外部算法能力（相似行情）通过端口注入，测试可覆盖依赖。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import Depends, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.clock import to_shanghai
from apps.api.app.core.db import get_session
from apps.api.app.core.pagination import MAX_LIMIT, Cursor, decode_cursor, normalize_limit
from apps.api.app.core.runtime import get_clock
from apps.api.app.schemas.watchlist import SYMBOL_PATTERN  # 6 位 A 股代码，单一真相
from apps.api.app.services.ports import AnalogFinder

SessionDep = Annotated[AsyncSession, Depends(get_session)]

SymbolPath = Annotated[
    str, Path(pattern=SYMBOL_PATTERN, description="6 位 A 股代码，例如 600519", examples=["600519"])
]

LimitQuery = Annotated[
    int | None,
    Query(ge=1, le=MAX_LIMIT, description=f"默认 20，最大 {MAX_LIMIT}"),
]

CursorQuery = Annotated[str | None, Query(description="无填充 Base64URL 游标")]


def get_now() -> datetime:
    """当前时间（Asia/Shanghai）。所有"现在"的唯一来源。"""
    return to_shanghai(get_clock().now())


NowDep = Annotated[datetime, Depends(get_now)]


def get_request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", "")
    return str(value)


RequestIdDep = Annotated[str, Depends(get_request_id)]


def resolve_limit(limit: int | None) -> int:
    return normalize_limit(limit)


def resolve_cursor(token: str | None, *, expected_sort: str) -> Cursor | None:
    if token is None:
        return None
    return decode_cursor(token, expected_sort=expected_sort)


def resolve_as_of(as_of: date | None, now: datetime) -> date:
    """``as_of`` 缺省 = 查询日（上海时区），不依赖运行机器时区。"""
    return as_of if as_of is not None else to_shanghai(now).date()


def get_analog_finder() -> AnalogFinder:
    """相似行情端口的生产实现（``services/prediction/analogs``）。

    缺件属于**部署错误**（⇒ 500），不在这里降级成假数据。
    测试通过 ``app.dependency_overrides`` 注入确定性 stub。
    """
    from services.prediction.analogs.finder import find_analogs

    return find_analogs


AnalogFinderDep = Annotated[AnalogFinder, Depends(get_analog_finder)]
