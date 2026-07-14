"""唯一触达 akshare 的模块。

spec §5.2 硬约束：**只允许调用 4 个 akshare 函数**。这里用运行时白名单
``ALLOWED_AKSHARE_FUNCTIONS`` 强制 —— 任何越权调用直接 ``ProviderConfigError``，
而不是靠 code review 或注释约束（见 tests/test_akshare_client_allowlist.py）。

akshare 是同步阻塞库；OpenBB Fetcher 是 async，因此对外暴露的都是
``async def``，内部用 ``asyncio.to_thread`` 把阻塞调用挪出事件循环。
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from types import ModuleType
from typing import Any, cast

from .constants import (
    ALLOWED_ADJUSTMENTS,
    DEFAULT_ADJUSTMENT,
    MINUTE_PERIOD,
    ProviderConfigError,
    ProviderUpstreamError,
)
from .transform import normalize_symbol

# ── spec §5.2：AKShare 扩展只允许调用这 4 个函数 ───────────────────────────────
ALLOWED_AKSHARE_FUNCTIONS: frozenset[str] = frozenset(
    {
        "stock_zh_a_spot_em",  # 实时行情快照
        "stock_zh_a_hist",  # 日线
        "stock_zh_a_hist_min_em",  # 分钟线
        "stock_news_em",  # 个股新闻
    }
)

# akshare 固定版本（spec §5.2）
PINNED_AKSHARE_VERSION = "1.18.64"


def _import_akshare() -> ModuleType:
    try:
        import akshare  # 延迟导入：未装 akshare 时仍可跑纯 transform 契约测试
    except ImportError as exc:  # pragma: no cover - 依赖缺失是部署问题
        raise ProviderUpstreamError(
            "akshare 未安装：AKShare Provider 不可用（fail closed，不使用任何备用源）"
        ) from exc
    # akshare 无 type stub，import 结果在 mypy 眼里是 Any；显式收窄成 ModuleType，
    # 使 Any 不会从这里泄漏到调用方。
    return cast(ModuleType, akshare)


def _records(payload: Any) -> list[dict[str, Any]]:
    """akshare 返回 pandas.DataFrame → list[dict]。空表返回 []（不是错误）。"""
    if payload is None:
        return []
    to_dict = getattr(payload, "to_dict", None)
    if to_dict is None:
        raise ProviderUpstreamError(f"akshare 返回了非 DataFrame 对象：{type(payload).__name__}")
    if bool(getattr(payload, "empty", False)):
        return []
    rows: Any = to_dict("records")
    if not isinstance(rows, list):  # pragma: no cover - pandas 契约
        raise ProviderUpstreamError("akshare DataFrame.to_dict('records') 未返回 list")
    return [dict(row) for row in rows]


def call_akshare(function_name: str, /, **kwargs: Any) -> list[dict[str, Any]]:
    """**同步**调用 akshare 白名单函数。越权函数名直接抛错。"""
    if function_name not in ALLOWED_AKSHARE_FUNCTIONS:
        raise ProviderConfigError(
            f"akshare 函数 {function_name!r} 不在 spec §5.2 白名单内："
            f"{sorted(ALLOWED_AKSHARE_FUNCTIONS)}"
        )
    akshare = _import_akshare()
    fn = getattr(akshare, function_name, None)
    if fn is None:
        raise ProviderUpstreamError(
            f"akshare {PINNED_AKSHARE_VERSION} 中不存在函数 {function_name!r}（上游 API 变更）"
        )
    try:
        payload = fn(**kwargs)
    except ProviderConfigError:
        raise
    except Exception as exc:  # akshare 把网络错误/限流/解析错误全抛成各种异常
        raise ProviderUpstreamError(f"akshare.{function_name} 调用失败：{exc}") from exc
    return _records(payload)


async def acall_akshare(function_name: str, /, **kwargs: Any) -> list[dict[str, Any]]:
    return await asyncio.to_thread(lambda: call_akshare(function_name, **kwargs))


def _check_adjustment(adjustment: str) -> str:
    if adjustment not in ALLOWED_ADJUSTMENTS:
        raise ProviderConfigError(f"非法复权方式 {adjustment!r}，允许：{sorted(ALLOWED_ADJUSTMENTS)}")
    return adjustment


def _yyyymmdd(value: date) -> str:
    return value.strftime("%Y%m%d")


def _yyyy_mm_dd_hhmmss(value: datetime) -> str:
    if value.tzinfo is None:
        raise ProviderConfigError("时间参数必须带时区")
    return value.strftime("%Y-%m-%d %H:%M:%S")


# ── 4 个白名单函数的类型化封装 ─────────────────────────────────────────────
async def fetch_spot() -> list[dict[str, Any]]:
    """沪深京 A 股全市场实时快照。上游没有单标的接口，只能整表拉取后本地过滤。"""
    return await acall_akshare("stock_zh_a_spot_em")


async def fetch_daily(
    symbol: str, start: date, end: date, adjustment: str = DEFAULT_ADJUSTMENT
) -> list[dict[str, Any]]:
    return await acall_akshare(
        "stock_zh_a_hist",
        symbol=normalize_symbol(symbol),
        period="daily",
        start_date=_yyyymmdd(start),
        end_date=_yyyymmdd(end),
        adjust=_check_adjustment(adjustment),
    )


async def fetch_minute(
    symbol: str, start: datetime, end: datetime, adjustment: str = DEFAULT_ADJUSTMENT
) -> list[dict[str, Any]]:
    return await acall_akshare(
        "stock_zh_a_hist_min_em",
        symbol=normalize_symbol(symbol),
        start_date=_yyyy_mm_dd_hhmmss(start),
        end_date=_yyyy_mm_dd_hhmmss(end),
        period=MINUTE_PERIOD,
        adjust=_check_adjustment(adjustment),
    )


async def fetch_news(symbol: str) -> list[dict[str, Any]]:
    """个股新闻。上游**不接受时间窗**，只返回最近约 100 条；时间窗过滤在 Fetcher 内完成。"""
    return await acall_akshare("stock_news_em", symbol=normalize_symbol(symbol))
