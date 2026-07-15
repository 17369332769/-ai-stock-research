"""``OpenBBGateway`` 的 HTTP 实现 —— 业务代码访问外部数据的**唯一**出口（spec §4.2 / §5.2）。

只走 OpenBB 内部 REST（``settings.openbb_base_url``）。这里**不 import akshare、不 import
httpx 去打第三方 URL**；第三方 URL 只能出现在 ``services/openbb_extensions``。

────────────────────────────────────────────────────────────────────────────
OpenBB REST 路由表（与 docs/data-sources.md 一致，改这里必须同步改文档与契约测试）

  get_universe_members / search_instruments
      GET /api/v1/index/constituents?provider=csi300&symbol=000300&as_of=YYYY-MM-DD
  get_quotes
      GET /api/v1/equity/price/quote?provider=akshare&symbol=600519,000001
  get_bars
      GET /api/v1/equity/price/historical?provider=akshare&symbol=600519
              &interval=1d|5m&start_date=&end_date=&adjustment=qfq
  get_announcements
      GET /api/v1/news/company?provider=cn_disclosure&symbol=600519&start_date=&end_date=
  get_news
      GET /api/v1/news/company?provider=akshare&symbol=600519&start_date=&end_date=

"公告 vs 新闻"由 provider 区分，两种口径不混（spec §5.2）。
────────────────────────────────────────────────────────────────────────────

失败语义（spec §5.2 / §7）：上游任何失败（超时/限流/5xx/JSON 破损/字段缺失/类型改变）
→ ``ProviderUnavailable``（HTTP 424）。**不重试到别的源、不返回缓存、不用默认值填洞。**
调用方参数错误 → ``InvalidArgument``（HTTP 400）。
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal

import httpx

from apps.api.app.core.clock import SHANGHAI, Clock, to_shanghai
from apps.api.app.core.enums import CSI300_BENCHMARK_SYMBOL, DocumentType, Timeframe
from apps.api.app.core.errors import InvalidArgument, ProviderUnavailable
from apps.api.app.core.runtime import get_clock
from apps.api.app.core.settings import get_settings
from services.market_data.contracts import (
    BarRecord,
    DocumentRecord,
    InstrumentRecord,
    OpenBBGateway,
    QuoteRecord,
    Universe,
    UniverseMemberRecord,
)

API_PREFIX = "/api/v1"
ROUTE_CONSTITUENTS = f"{API_PREFIX}/index/constituents"
ROUTE_QUOTE = f"{API_PREFIX}/equity/price/quote"
ROUTE_HISTORICAL = f"{API_PREFIX}/equity/price/historical"
ROUTE_COMPANY_NEWS = f"{API_PREFIX}/news/company"

PROVIDER_AKSHARE = "akshare"
PROVIDER_CN_DISCLOSURE = "cn_disclosure"
PROVIDER_CSI300 = "csi300"

# 沪深 300 默认全部进入自选股；单次请求必须覆盖完整成分集合。
MAX_QUOTE_SYMBOLS = 300
MAX_SEARCH_LIMIT = 100

_INTERVAL: dict[str, str] = {"1d": "1d", "5m": "5m"}


class _Missing:
    """哨兵：区分"字段不存在"与"字段存在但为 null"。"""


_MISSING = _Missing()


# ── 取值原语：缺字段 / 类型改变 / 非有限数值一律 ProviderUnavailable ─────────────
def _fail(message: str) -> ProviderUnavailable:
    return ProviderUnavailable(f"OpenBB 上游数据不符合契约：{message}")


def _get(item: dict[str, Any], key: str) -> Any:
    return item.get(key, _MISSING)


def _is_null(value: Any) -> bool:
    if isinstance(value, _Missing) or value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and value.strip() in {"", "None", "nan", "NaN"}


def _req_str(item: dict[str, Any], key: str, ctx: str) -> str:
    value = _get(item, key)
    if isinstance(value, _Missing):
        raise _fail(f"{ctx} 缺少字段 {key!r}（实际字段：{sorted(item)}）")
    if _is_null(value):
        raise _fail(f"{ctx} 字段 {key!r} 为空")
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise _fail(f"{ctx} 字段 {key!r} 类型异常（{type(value).__name__}）")
    return str(value).strip()


def _to_decimal(value: Any, key: str, ctx: str) -> Decimal:
    if isinstance(value, bool):
        raise _fail(f"{ctx} 字段 {key!r} 类型异常（bool）")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise _fail(f"{ctx} 字段 {key!r} 是非有限数值 {value!r}")
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            parsed = Decimal(value.strip())
        except InvalidOperation as exc:
            raise _fail(f"{ctx} 字段 {key!r} 无法解析为数值：{value!r}") from exc
        if not parsed.is_finite():
            raise _fail(f"{ctx} 字段 {key!r} 是非有限数值 {value!r}")
        return parsed
    raise _fail(f"{ctx} 字段 {key!r} 类型异常（{type(value).__name__}）")


def _req_decimal(item: dict[str, Any], key: str, ctx: str) -> Decimal:
    value = _get(item, key)
    if isinstance(value, _Missing):
        raise _fail(f"{ctx} 缺少字段 {key!r}（实际字段：{sorted(item)}）")
    if _is_null(value):
        raise _fail(f"{ctx} 必填字段 {key!r} 为空")
    return _to_decimal(value, key, ctx)


def _opt_decimal(item: dict[str, Any], key: str, ctx: str) -> Decimal | None:
    value = _get(item, key)
    if _is_null(value):
        return None
    return _to_decimal(value, key, ctx)


def _opt_str(item: dict[str, Any], key: str) -> str | None:
    value = _get(item, key)
    if _is_null(value):
        return None
    return str(value).strip()


def _to_datetime(value: Any, key: str, ctx: str, *, date_at: time) -> datetime:
    """ISO 字符串 / date / datetime → 带时区 datetime。

    naive 值按 **Asia/Shanghai** 解释：本项目所有上游都是中国境内数据源，
    OpenBB 序列化时可能丢时区。这个假设是显式的、写进 docs 的 —— 不是猜。
    纯日期（``2026-07-14``）补 ``date_at`` 时刻（日线补 15:00 收盘）。
    """
    if isinstance(value, datetime):
        return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else to_shanghai(value)
    if isinstance(value, date):
        return datetime.combine(value, date_at, tzinfo=SHANGHAI)
    if not isinstance(value, str):
        raise _fail(f"{ctx} 字段 {key!r} 类型异常（{type(value).__name__}）")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _fail(f"{ctx} 字段 {key!r} 时间格式无法识别：{value!r}") from exc
    if len(text) == 10:  # 纯日期
        return datetime.combine(parsed.date(), date_at, tzinfo=SHANGHAI)
    return parsed.replace(tzinfo=SHANGHAI) if parsed.tzinfo is None else to_shanghai(parsed)


def _req_datetime(item: dict[str, Any], key: str, ctx: str, *, date_at: time = time(15, 0)) -> datetime:
    value = _get(item, key)
    if isinstance(value, _Missing):
        raise _fail(f"{ctx} 缺少字段 {key!r}（实际字段：{sorted(item)}）")
    if _is_null(value):
        raise _fail(f"{ctx} 必填字段 {key!r} 为空")
    return _to_datetime(value, key, ctx, date_at=date_at)


def _exchange_of(symbol: str) -> Literal["SSE", "SZSE"]:
    if symbol.startswith(("6", "9")):
        return "SSE"
    if symbol.startswith(("0", "2", "3")):
        return "SZSE"
    raise _fail(f"无法判定交易所：{symbol!r} 不是沪深 A 股代码")


def _normalize_symbol(raw: str) -> str:
    text = str(raw).strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    text = text.split(".")[0]
    if not (len(text) == 6 and text.isdigit()):
        raise InvalidArgument(f"非法 A 股代码：{raw!r}")
    return text


class OpenBBHttpGateway:
    """``OpenBBGateway`` Protocol 的 HTTP 实现。

    ``OpenBBHttpGateway`` 是无状态的（除了 httpx 连接池）：不缓存、不落库、不替换数据来源。
    """

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        clock: Clock,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._clock = clock
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )

    @classmethod
    def create(cls, client: httpx.AsyncClient | None = None) -> OpenBBHttpGateway:
        settings = get_settings()
        return cls(
            base_url=settings.openbb_base_url,
            timeout_seconds=settings.openbb_timeout_seconds,
            clock=get_clock(),
            client=client,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> OpenBBHttpGateway:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    # ── HTTP ────────────────────────────────────────────────────────────
    async def _results(self, route: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        url = f"{self._base_url}{route}"
        try:
            response = await self._client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise ProviderUnavailable(
                f"OpenBB 请求超时（>{self._timeout:g}s）：{route} provider={params.get('provider')}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"OpenBB 网络错误：{route}：{exc}") from exc

        status = response.status_code
        if status == 429:
            raise ProviderUnavailable(f"OpenBB 上游限流（HTTP 429）：{route}")
        if status in (400, 422):
            # 参数错误是调用方的问题，不是上游故障 —— 不要伪装成 PROVIDER_UNAVAILABLE
            raise InvalidArgument(f"OpenBB 拒绝请求参数（HTTP {status}）：{route}：{_body_hint(response)}")
        if status >= 500:
            # OpenBB 的 5xx 往往只是 Provider 异常的外壳；响应体中的 detail 才包含
            # 真正的上游原因（连接被断开、TLS、限流等）。只保留短摘要，既便于排障，
            # 又避免把整段堆栈或超长 URL 透传到作业表和前端。
            raise ProviderUnavailable(
                f"OpenBB 服务端错误（HTTP {status}）：{route}：{_body_hint(response)}"
            )
        if status >= 400:
            raise ProviderUnavailable(f"OpenBB 请求失败（HTTP {status}）：{route}：{_body_hint(response)}")

        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise ProviderUnavailable(f"OpenBB 返回非 JSON 响应：{route}") from exc
        if not isinstance(payload, dict):
            raise _fail(f"{route} 响应不是对象（{type(payload).__name__}）")
        if "results" not in payload:
            raise _fail(f"{route} 响应缺少 results 字段（实际字段：{sorted(payload)}）")
        results = payload["results"]
        if results is None:
            return []
        if not isinstance(results, list):
            raise _fail(f"{route} 的 results 不是数组（{type(results).__name__}）")
        for item in results:
            if not isinstance(item, dict):
                raise _fail(f"{route} 的 results 元素不是对象（{type(item).__name__}）")
        return [dict(item) for item in results]

    # ── 成分（CSI300）─────────────────────────────────────────────────────
    async def _constituents(self, universe: Universe, as_of: date) -> list[dict[str, Any]]:
        if universe != "CSI300":
            raise InvalidArgument(f"MVP 只支持 CSI300 股票池，收到 {universe!r}")
        results = await self._results(
            ROUTE_CONSTITUENTS,
            {"provider": PROVIDER_CSI300, "symbol": CSI300_BENCHMARK_SYMBOL, "as_of": as_of.isoformat()},
        )
        if not results:
            # 沪深300 成分不可能为空。若把空列表当成"今天一只都没有"，
            # sync 作业会把 300 只全部标记为已调出 —— 必须 fail closed。
            raise ProviderUnavailable(
                f"CSI300 成分为空（as_of={as_of}）：拒绝用空成分覆盖历史有效期（spec §8）"
            )
        return results

    async def get_universe_members(
        self, universe: Universe, as_of: date
    ) -> list[UniverseMemberRecord]:
        results = await self._constituents(universe, as_of)
        ctx = "index/constituents"
        out: list[UniverseMemberRecord] = []
        for item in results:
            symbol = _normalize_symbol(_req_str(item, "symbol", ctx))
            # snapshot_date = 该成分表的官方生效日期；缺省退到 as_of
            snapshot_raw = _opt_str(item, "snapshot_date")
            effective_from = (
                _to_datetime(snapshot_raw, "snapshot_date", ctx, date_at=time(0, 0)).date()
                if snapshot_raw
                else as_of
            )
            out.append(
                UniverseMemberRecord(
                    universe=universe,
                    symbol=symbol,
                    effective_from=min(effective_from, as_of),
                    effective_to=None,  # 有效期闭合由 ingest 按快照差分计算，Provider 不知道历史
                    source=_req_str(item, "source", ctx),
                    source_url=_req_str(item, "source_url", ctx),
                    observed_at=_req_datetime(item, "observed_at", ctx),
                )
            )
        return out

    async def list_instruments(self, universe: Universe, as_of: date) -> list[InstrumentRecord]:
        """指定日期的全部成分股（Protocol 之外的补充方法，供成分同步作业写 instruments 表）。

        行业与上市日期上游不提供（spec §5.2 只允许 4 个 akshare 函数，均无这两列）——
        留 ``None``，**不编造**。
        """
        results = await self._constituents(universe, as_of)
        ctx = "index/constituents"
        out: list[InstrumentRecord] = []
        for item in results:
            symbol = _normalize_symbol(_req_str(item, "symbol", ctx))
            out.append(
                InstrumentRecord(
                    symbol=symbol,
                    name=_req_str(item, "name", ctx),
                    exchange=_exchange_of(symbol),
                    industry=None,
                    listed_at=None,
                )
            )
        return out

    async def search_instruments(
        self, universe: Universe, query: str, as_of: date, limit: int
    ) -> list[InstrumentRecord]:
        """只在查询日的当前成分内搜索；精确代码匹配优先（spec §7.1）。"""
        if limit < 1 or limit > MAX_SEARCH_LIMIT:
            raise InvalidArgument(f"limit 必须在 1..{MAX_SEARCH_LIMIT} 之间，收到 {limit}")
        text = query.strip()
        if not text:
            raise InvalidArgument("搜索关键字不能为空")

        instruments = await self.list_instruments(universe, as_of)
        needle = text.upper()

        def rank(instrument: InstrumentRecord) -> tuple[int, str]:
            if instrument.symbol == needle:
                return (0, instrument.symbol)  # 精确代码匹配优先
            if instrument.symbol.startswith(needle):
                return (1, instrument.symbol)
            if needle in instrument.name.upper():
                return (2, instrument.symbol)
            return (9, instrument.symbol)

        matched = [item for item in instruments if rank(item)[0] < 9]
        matched.sort(key=rank)
        return matched[:limit]

    # ── 行情 ────────────────────────────────────────────────────────────
    async def get_quotes(self, symbols: list[str], as_of: datetime) -> list[QuoteRecord]:
        if not symbols:
            raise InvalidArgument("symbols 不能为空")
        if len(symbols) > MAX_QUOTE_SYMBOLS:
            raise InvalidArgument(f"一次最多请求 {MAX_QUOTE_SYMBOLS} 个 symbol，收到 {len(symbols)}")
        if as_of.tzinfo is None:
            raise InvalidArgument("as_of 必须带时区")
        wanted = [_normalize_symbol(s) for s in symbols]

        results = await self._results(
            ROUTE_QUOTE, {"provider": PROVIDER_AKSHARE, "symbol": ",".join(wanted)}
        )
        ctx = "equity/price/quote"
        allowed = set(wanted)
        out: list[QuoteRecord] = []
        for item in results:
            symbol = _normalize_symbol(_req_str(item, "symbol", ctx))
            if symbol not in allowed:
                continue
            observed_raw = _get(item, "last_timestamp")
            observed_at = (
                _to_datetime(observed_raw, "last_timestamp", ctx, date_at=time(15, 0))
                if not _is_null(observed_raw)
                else to_shanghai(as_of)
            )
            out.append(
                QuoteRecord(
                    symbol=symbol,
                    price=_req_decimal(item, "last_price", ctx),
                    previous_close=_req_decimal(item, "prev_close", ctx),
                    open=_opt_decimal(item, "open", ctx),
                    high=_opt_decimal(item, "high", ctx),
                    low=_opt_decimal(item, "low", ctx),
                    volume=_opt_decimal(item, "volume", ctx),
                    amount=_opt_decimal(item, "turnover", ctx),
                    volume_ratio=_opt_decimal(item, "volume_ratio", ctx),
                    source=_req_str(item, "source", ctx),
                    source_url=_opt_str(item, "source_url"),
                    observed_at=observed_at,
                    raw_payload=item,  # 原始上游口径整包留存（quotes.raw_payload NOT NULL）
                )
            )
        return out

    async def get_bars(
        self, symbol: str, timeframe: Timeframe | str, start: datetime, end: datetime
    ) -> list[BarRecord]:
        code = _normalize_symbol(symbol)
        frame = str(timeframe)
        if frame not in _INTERVAL:
            raise InvalidArgument(f"timeframe 只支持 {sorted(_INTERVAL)}，收到 {timeframe!r}")
        if start.tzinfo is None or end.tzinfo is None:
            raise InvalidArgument("start/end 必须带时区")
        local_start, local_end = to_shanghai(start), to_shanghai(end)
        if local_start > local_end:
            raise InvalidArgument(f"start {local_start} 晚于 end {local_end}")

        results = await self._results(
            ROUTE_HISTORICAL,
            {
                "provider": PROVIDER_AKSHARE,
                "symbol": code,
                "interval": _INTERVAL[frame],
                "start_date": local_start.date().isoformat(),
                "end_date": local_end.date().isoformat(),
                "adjustment": "qfq",
            },
        )
        ctx = "equity/price/historical"
        out: list[BarRecord] = []
        for item in results:
            bar_time = _req_datetime(item, "date", ctx, date_at=time(15, 0))
            # 上游按自然日返回，这里按调用方给的精确时间窗收口（5m 尤其重要）
            if not (local_start <= bar_time <= local_end):
                continue
            out.append(
                BarRecord(
                    symbol=code,
                    timeframe=frame,  # type: ignore[arg-type]
                    bar_time=bar_time,
                    open=_req_decimal(item, "open", ctx),
                    high=_req_decimal(item, "high", ctx),
                    low=_req_decimal(item, "low", ctx),
                    close=_req_decimal(item, "close", ctx),
                    volume=_req_decimal(item, "volume", ctx),
                    amount=_opt_decimal(item, "turnover", ctx),
                    adjustment=_opt_str(item, "adjustment") or "qfq",
                    source=_req_str(item, "source", ctx),
                    source_url=_opt_str(item, "source_url"),
                    observed_at=self._clock.now(),
                )
            )
        out.sort(key=lambda bar: bar.bar_time)
        return out

    # ── 文档 ────────────────────────────────────────────────────────────
    async def _documents(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        *,
        provider: str,
        document_type: DocumentType,
    ) -> list[DocumentRecord]:
        code = _normalize_symbol(symbol)
        if start.tzinfo is None or end.tzinfo is None:
            raise InvalidArgument("start/end 必须带时区")
        local_start, local_end = to_shanghai(start), to_shanghai(end)
        if local_start > local_end:
            raise InvalidArgument(f"start {local_start} 晚于 end {local_end}")

        results = await self._results(
            ROUTE_COMPANY_NEWS,
            {
                "provider": provider,
                "symbol": code,
                "start_date": local_start.date().isoformat(),
                "end_date": local_end.date().isoformat(),
            },
        )
        ctx = f"news/company（provider={provider}）"
        observed_at = self._clock.now()
        out: list[DocumentRecord] = []
        for item in results:
            published_at = _req_datetime(item, "date", ctx, date_at=time(0, 0))
            if not (local_start <= published_at <= local_end):
                continue
            out.append(
                DocumentRecord(
                    symbol=code,
                    document_type=document_type.value,
                    title=_req_str(item, "title", ctx),
                    body_text=_opt_str(item, "text"),
                    source=_req_str(item, "source", ctx),
                    source_url=_req_str(item, "url", ctx),
                    published_at=published_at,
                    observed_at=observed_at,
                )
            )
        out.sort(key=lambda doc: doc.published_at, reverse=True)
        return out

    async def get_announcements(
        self, symbol: str, start: datetime, end: datetime
    ) -> list[DocumentRecord]:
        """法定披露公告（巨潮/沪深交易所原文）。"""
        return await self._documents(
            symbol,
            start,
            end,
            provider=PROVIDER_CN_DISCLOSURE,
            document_type=DocumentType.ANNOUNCEMENT,
        )

    async def get_news(self, symbol: str, start: datetime, end: datetime) -> list[DocumentRecord]:
        """媒体新闻（东方财富，经 akshare）。与公告口径分开，绝不互相顶替。"""
        return await self._documents(
            symbol, start, end, provider=PROVIDER_AKSHARE, document_type=DocumentType.NEWS
        )


def _body_hint(response: httpx.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
            text = payload["detail"]
        else:
            text = response.text
        return " ".join(text.split())[:300]
    except Exception:  # pragma: no cover
        try:
            return " ".join(response.text.split())[:300]
        except Exception:
            return "<无法读取响应体>"


def create_gateway(client: httpx.AsyncClient | None = None) -> OpenBBHttpGateway:
    """工厂：作业与 API 层都用它拿网关（依赖注入点）。"""
    return OpenBBHttpGateway.create(client=client)


if TYPE_CHECKING:
    # 静态断言：OpenBBHttpGateway 必须满足 spec §5.2 的 OpenBBGateway Protocol。
    # 签名一旦漂移，mypy 直接报错（不需要运行时构造对象）。
    def _protocol_check(gateway: OpenBBHttpGateway) -> OpenBBGateway:
        return gateway
