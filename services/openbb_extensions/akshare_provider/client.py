"""唯一触达 akshare 的模块。

生产数据调用由运行时白名单约束。每类数据只调用一个明确的上游函数；异常保持
原始语义并直接向上传递。
``ALLOWED_AKSHARE_FUNCTIONS`` 强制 —— 任何越权调用直接 ``ProviderConfigError``，
而不是靠 code review 或注释约束（见 tests/test_akshare_client_allowlist.py）。

akshare 是同步阻塞库；OpenBB Fetcher 是 async，因此对外暴露的都是
``async def``，内部用 ``asyncio.to_thread`` 把阻塞调用挪出事件循环。
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime
from types import ModuleType
from typing import Any, cast

import httpx

from .constants import (
    ALLOWED_ADJUSTMENTS,
    DEFAULT_ADJUSTMENT,
    MINUTE_PERIOD,
    ProviderConfigError,
    ProviderUpstreamError,
)
from .transform import normalize_symbol

# ── AKShare 生产调用白名单 ───────────────────────────────────────────────────
ALLOWED_AKSHARE_FUNCTIONS: frozenset[str] = frozenset(
    {
        "stock_zh_a_spot_em",  # 实时行情快照
        "stock_bid_ask_em",  # 指定代码的行情报价（002 主链路）
        "stock_zh_a_hist",  # 日线
        "stock_zh_a_hist_min_em",  # 分钟线
        "stock_news_em",  # 个股新闻
    }
)

# akshare 固定版本（spec §5.2）
PINNED_AKSHARE_VERSION = "1.18.64"

# stock_zh_a_spot_em 会分页拉取整个 A 股市场。定时刷新与多个手动刷新可能在同一时刻
# 到达 OpenBB；用很短的进程内快照合并并发请求，避免为每个 symbol 重复抓整市场。
# 这不是持久缓存：进程重启即丢失，失败也绝不缓存。
SPOT_CACHE_TTL_SECONDS = 12.0
_spot_cache: tuple[dict[str, Any], ...] | None = None
_spot_cache_expires_at = 0.0
_spot_fetch_lock = asyncio.Lock()
_spot_inflight: asyncio.Task[tuple[dict[str, Any], ...]] | None = None

# 002：主链路按代码获取，不下载全市场。缓存与并发合并按 symbol 隔离，
# 某一只失败不会污染其他股票的成功结果。
QUOTE_CACHE_TTL_SECONDS = 12.0
QUOTE_MAX_CONCURRENCY = 8
AKSHARE_QUOTE_TIMEOUT_SECONDS = 15.0
EASTMONEY_DELAY_QUOTE_URL = "https://push2delay.eastmoney.com/api/qt/stock/get"
EASTMONEY_QUOTE_TIMEOUT_SECONDS = 15.0
_EASTMONEY_QUOTE_FIELDS: dict[str, str] = {
    "f43": "最新",
    "f44": "最高",
    "f45": "最低",
    "f46": "今开",
    "f47": "总手",
    "f48": "金额",
    "f50": "量比",
    "f60": "昨收",
    "f71": "均价",
    "f161": "内盘",
    "f168": "换手",
    "f169": "涨跌",
    "f170": "涨幅",
    "f19": "buy_1",
    "f39": "sell_1",
}
_quote_cache: dict[str, tuple[float, tuple[dict[str, Any], ...]]] = {}
_quote_inflight: dict[str, asyncio.Task[tuple[dict[str, Any], ...]]] = {}
_quote_lock = asyncio.Lock()


def _copy_records(records: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    return [dict(row) for row in records]


def reset_spot_cache() -> None:
    """清空进程内报价快照；供测试和运维探针使用。"""
    global _spot_cache, _spot_cache_expires_at, _spot_inflight
    _spot_cache = None
    _spot_cache_expires_at = 0.0
    _spot_inflight = None
    _quote_cache.clear()
    _quote_inflight.clear()


async def _load_spot_snapshot() -> tuple[dict[str, Any], ...]:
    records = await acall_akshare("stock_zh_a_spot_em")
    return tuple(dict(row) for row in records)


def _import_akshare() -> ModuleType:
    try:
        import akshare  # 延迟导入：未装 akshare 时仍可跑纯 transform 契约测试
    except ImportError as exc:  # pragma: no cover - 依赖缺失是部署问题
        raise ProviderUpstreamError(
            "akshare 未安装：AKShare Provider 不可用"
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


# ── 白名单函数的类型化封装 ─────────────────────────────────────────────────
async def fetch_spot() -> list[dict[str, Any]]:
    """沪深京 A 股全市场实时快照；短时缓存并合并并发的整市场抓取。"""
    global _spot_cache, _spot_cache_expires_at, _spot_inflight

    now = time.monotonic()
    if _spot_cache is not None and now < _spot_cache_expires_at:
        return _copy_records(_spot_cache)

    async with _spot_fetch_lock:
        now = time.monotonic()
        if _spot_cache is not None and now < _spot_cache_expires_at:
            return _copy_records(_spot_cache)
        if _spot_inflight is None:
            _spot_inflight = asyncio.create_task(_load_spot_snapshot())
        task = _spot_inflight

    try:
        # 一个 HTTP 请求被取消时不能连带取消其他正在等待同一整市场快照的请求。
        snapshot = await asyncio.shield(task)
    except BaseException:
        async with _spot_fetch_lock:
            if _spot_inflight is task and task.done():
                _spot_inflight = None
        raise

    async with _spot_fetch_lock:
        _spot_cache = snapshot
        _spot_cache_expires_at = time.monotonic() + SPOT_CACHE_TTL_SECONDS
        if _spot_inflight is task:
            _spot_inflight = None
    return _copy_records(snapshot)


async def fetch_bid_ask(symbol: str) -> list[dict[str, Any]]:
    """指定一只股票的报价；12 秒内同代码请求合并。"""
    code = normalize_symbol(symbol)
    now = time.monotonic()
    cached = _quote_cache.get(code)
    if cached is not None and now < cached[0]:
        return _copy_records(cached[1])

    async with _quote_lock:
        now = time.monotonic()
        cached = _quote_cache.get(code)
        if cached is not None and now < cached[0]:
            return _copy_records(cached[1])
        task = _quote_inflight.get(code)
        if task is None:
            task = asyncio.create_task(_load_bid_ask(code))
            _quote_inflight[code] = task

    try:
        rows = await asyncio.shield(task)
    except BaseException:
        async with _quote_lock:
            if _quote_inflight.get(code) is task and task.done():
                _quote_inflight.pop(code, None)
        raise

    async with _quote_lock:
        _quote_cache[code] = (time.monotonic() + QUOTE_CACHE_TTL_SECONDS, rows)
        if _quote_inflight.get(code) is task:
            _quote_inflight.pop(code, None)
    return _copy_records(rows)


async def _load_bid_ask(symbol: str) -> tuple[dict[str, Any], ...]:
    try:
        rows = await asyncio.wait_for(
            acall_akshare("stock_bid_ask_em", symbol=symbol),
            timeout=AKSHARE_QUOTE_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        primary_error = ProviderUpstreamError(
            f"akshare.stock_bid_ask_em 超过 {AKSHARE_QUOTE_TIMEOUT_SECONDS:g}s 未返回"
        )
        rows = await _fallback_delay_quote(symbol, primary_error)
    except ProviderUpstreamError as primary_error:
        rows = await _fallback_delay_quote(symbol, primary_error)
    return tuple(dict(row) for row in rows)


async def _fallback_delay_quote(
    symbol: str, primary_error: ProviderUpstreamError
) -> list[dict[str, Any]]:
    # akshare 1.18.64 固定访问 push2.eastmoney.com；该主机偶尔会直接断开连接，
    # 但东方财富的同源延迟行情主机仍可用。只在主调用失败时按代码请求同一组字段，
    # 不切换数据供应商、不下载全市场，也不把失败静默伪装成空结果。
    try:
        return await _fetch_eastmoney_delay_quote(symbol)
    except ProviderUpstreamError as fallback_error:
        raise ProviderUpstreamError(
            f"akshare.stock_bid_ask_em 与东方财富延迟行情均失败："
            f"primary={primary_error}; fallback={fallback_error}"
        ) from fallback_error


async def _fetch_eastmoney_delay_quote(symbol: str) -> list[dict[str, Any]]:
    """按单股读取东方财富延迟行情，返回 ``stock_bid_ask_em`` 兼容的 item/value 行。"""
    code = normalize_symbol(symbol)
    market_code = 1 if code.startswith("6") else 0
    params = {
        "fltt": "2",
        "invt": "2",
        "fields": ",".join(_EASTMONEY_QUOTE_FIELDS),
        "secid": f"{market_code}.{code}",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ai-stock-research/0.1)",
        "Referer": "https://quote.eastmoney.com/",
    }
    try:
        async with httpx.AsyncClient(
            timeout=EASTMONEY_QUOTE_TIMEOUT_SECONDS,
            headers=headers,
            follow_redirects=True,
        ) as http:
            response = await http.get(EASTMONEY_DELAY_QUOTE_URL, params=params)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderUpstreamError(f"东方财富延迟行情请求失败：{exc}") from exc

    if not isinstance(payload, dict) or payload.get("rc") != 0:
        raise ProviderUpstreamError("东方财富延迟行情返回了异常响应")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ProviderUpstreamError(f"东方财富延迟行情未返回股票 {code} 的数据")
    missing = [field for field in ("f43", "f60") if data.get(field) is None]
    if missing:
        raise ProviderUpstreamError(f"东方财富延迟行情缺少必填字段：{missing}")
    return [
        {"item": item, "value": data.get(field)}
        for field, item in _EASTMONEY_QUOTE_FIELDS.items()
    ]


async def fetch_quotes(symbols: list[str]) -> dict[str, list[dict[str, Any]]]:
    """并发获取指定代码；结果仍按代码分组，禁止整批失败时丢弃已成功股票。"""
    codes = list(dict.fromkeys(normalize_symbol(symbol) for symbol in symbols))
    semaphore = asyncio.Semaphore(QUOTE_MAX_CONCURRENCY)

    async def one(code: str) -> tuple[str, list[dict[str, Any]]]:
        async with semaphore:
            return code, await fetch_bid_ask(code)

    results = await asyncio.gather(*(one(code) for code in codes), return_exceptions=True)
    out: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    for code, result in zip(codes, results, strict=True):
        if isinstance(result, BaseException):
            errors.append(f"{code}: {type(result).__name__}: {result}")
            continue
        returned_code, rows = result
        out[returned_code] = rows
    if not out and errors:
        raise ProviderUpstreamError("指定代码行情全部失败：" + "; ".join(errors[:5]))
    return out


async def fetch_daily(
    symbol: str, start: date, end: date, adjustment: str = DEFAULT_ADJUSTMENT
) -> list[dict[str, Any]]:
    code = normalize_symbol(symbol)
    adjust = _check_adjustment(adjustment)
    return await acall_akshare(
        "stock_zh_a_hist",
        symbol=code,
        period="daily",
        start_date=_yyyymmdd(start),
        end_date=_yyyymmdd(end),
        adjust=adjust,
    )


async def fetch_minute(
    symbol: str, start: datetime, end: datetime, adjustment: str = DEFAULT_ADJUSTMENT
) -> list[dict[str, Any]]:
    code = normalize_symbol(symbol)
    adjust = _check_adjustment(adjustment)
    return await acall_akshare(
        "stock_zh_a_hist_min_em",
        symbol=code,
        start_date=_yyyy_mm_dd_hhmmss(start),
        end_date=_yyyy_mm_dd_hhmmss(end),
        period=MINUTE_PERIOD,
        adjust=adjust,
    )


async def fetch_news(symbol: str) -> list[dict[str, Any]]:
    """个股新闻。上游**不接受时间窗**，只返回最近约 100 条；时间窗过滤在 Fetcher 内完成。"""
    return await acall_akshare("stock_news_em", symbol=normalize_symbol(symbol))
