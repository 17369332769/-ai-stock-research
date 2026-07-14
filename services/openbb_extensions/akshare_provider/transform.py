"""akshare 原始记录 → OpenBB Data 字段的**纯函数**映射。

刻意不依赖 akshare / openbb_core / pandas：输入是 ``list[dict[str, Any]]``
（即 ``DataFrame.to_dict("records")`` 的结果），输出是 OpenBB Data 模型可直接吃的 dict。

这样上游列名/类型的每一次变化都能被 ``services/openbb_extensions/tests/`` 里的
确定性夹具测试锁死，而无需装 akshare、无需联网（spec §16.1）。

**列名即契约**：下面每张表的中文列名都逐列写进 docs/data-sources.md，改列名必须同时改文档与测试。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time
from typing import Any

from .constants import (
    AMOUNT_UNIT,
    DAILY_BAR_CLOSE_HHMM,
    DEFAULT_ADJUSTMENT,
    SESSION_CLOSE_HHMM,
    SESSION_OPEN_HHMM,
    SHANGHAI,
    SOURCE_NAME,
    VOLUME_UNIT,
    ProviderDataError,
    news_url,
    quote_url,
)

# ── 上游列名（akshare 1.18.64）──────────────────────────────────────────────
# stock_zh_a_spot_em
COL_SPOT_SYMBOL = "代码"
COL_SPOT_NAME = "名称"
COL_SPOT_LAST = "最新价"
COL_SPOT_PREV_CLOSE = "昨收"
COL_SPOT_OPEN = "今开"
COL_SPOT_HIGH = "最高"
COL_SPOT_LOW = "最低"
COL_SPOT_VOLUME = "成交量"
COL_SPOT_AMOUNT = "成交额"
COL_SPOT_VOLUME_RATIO = "量比"
COL_SPOT_CHANGE_PCT = "涨跌幅"
COL_SPOT_TURNOVER_RATE = "换手率"

# stock_zh_a_hist（日线）
COL_HIST_DATE = "日期"
COL_HIST_OPEN = "开盘"
COL_HIST_CLOSE = "收盘"
COL_HIST_HIGH = "最高"
COL_HIST_LOW = "最低"
COL_HIST_VOLUME = "成交量"
COL_HIST_AMOUNT = "成交额"
COL_HIST_TURNOVER_RATE = "换手率"

# stock_zh_a_hist_min_em（分钟线）
COL_MIN_TIME = "时间"

# stock_news_em
COL_NEWS_TITLE = "新闻标题"
COL_NEWS_BODY = "新闻内容"
COL_NEWS_PUBLISHED = "发布时间"
COL_NEWS_SOURCE = "文章来源"
COL_NEWS_URL = "新闻链接"
COL_NEWS_KEYWORD = "关键词"

_SESSION_OPEN = time(*SESSION_OPEN_HHMM)
_SESSION_CLOSE = time(*SESSION_CLOSE_HHMM)
_DAILY_CLOSE = time(*DAILY_BAR_CLOSE_HHMM)

Record = Mapping[str, Any]


# ── 取值原语：缺列 / 类型改变 / 脏值一律抛 ProviderDataError，绝不静默补默认值 ──────
def _require(record: Record, column: str, ctx: str) -> Any:
    if column not in record:
        raise ProviderDataError(f"{ctx}：上游缺少列 {column!r}（可用列：{sorted(record)}）")
    return record[column]


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):  # pandas NaN
        return True
    return isinstance(value, str) and value.strip() in {"", "-", "--", "None", "nan", "NaN"}


def _to_float(value: Any, column: str, ctx: str) -> float:
    """数值列 → float。字符串数字接受（上游偶尔返回 str），非数值一律抛错。"""
    if isinstance(value, bool):  # bool 是 int 的子类，必须先挡掉
        raise ProviderDataError(f"{ctx}：列 {column!r} 类型异常（bool）")
    if isinstance(value, int | float):
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            raise ProviderDataError(f"{ctx}：列 {column!r} 是非有限数值 {value!r}")
        return result
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("%", "").strip()
        try:
            return float(cleaned)
        except ValueError as exc:
            raise ProviderDataError(f"{ctx}：列 {column!r} 无法解析为数值：{value!r}") from exc
    raise ProviderDataError(f"{ctx}：列 {column!r} 类型异常（{type(value).__name__}）")


def _required_float(record: Record, column: str, ctx: str) -> float:
    value = _require(record, column, ctx)
    if _is_missing(value):
        raise ProviderDataError(f"{ctx}：必填列 {column!r} 为空")
    return _to_float(value, column, ctx)


def _optional_float(record: Record, column: str, ctx: str) -> float | None:
    if column not in record:
        return None
    value = record[column]
    if _is_missing(value):
        return None
    return _to_float(value, column, ctx)


def _required_str(record: Record, column: str, ctx: str) -> str:
    value = _require(record, column, ctx)
    if _is_missing(value):
        raise ProviderDataError(f"{ctx}：必填列 {column!r} 为空")
    if isinstance(value, int | float) and not isinstance(value, bool):
        # 代码列有时被 pandas 读成整数（600519 → 600519），补零到 6 位
        return f"{int(value):06d}"
    if not isinstance(value, str):
        raise ProviderDataError(f"{ctx}：列 {column!r} 类型异常（{type(value).__name__}）")
    return value.strip()


def _optional_str(record: Record, column: str, ctx: str) -> str | None:
    if column not in record:
        return None
    value = record[column]
    if _is_missing(value):
        return None
    return str(value).strip()


def _to_datetime(value: Any, column: str, ctx: str) -> datetime:
    """上游时间列 → 带时区 datetime（Asia/Shanghai）。

    上游一律是上海本地时间且不带时区标记；这里显式补 tzinfo，绝不产生 naive datetime。
    """
    if isinstance(value, datetime):
        return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)
    if isinstance(value, date):
        return datetime.combine(value, _DAILY_CLOSE, tzinfo=SHANGHAI)
    if isinstance(value, str):
        text = value.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
            try:
                parsed = datetime.strptime(text, fmt).replace(tzinfo=SHANGHAI)
            except ValueError:
                continue
            if fmt in ("%Y-%m-%d", "%Y%m%d"):
                return datetime.combine(parsed.date(), _DAILY_CLOSE, tzinfo=SHANGHAI)
            return parsed
        raise ProviderDataError(f"{ctx}：列 {column!r} 时间格式无法识别：{value!r}")
    raise ProviderDataError(f"{ctx}：列 {column!r} 类型异常（{type(value).__name__}）")


def _required_datetime(record: Record, column: str, ctx: str) -> datetime:
    value = _require(record, column, ctx)
    if _is_missing(value):
        raise ProviderDataError(f"{ctx}：必填列 {column!r} 为空")
    return _to_datetime(value, column, ctx)


def normalize_symbol(raw: str) -> str:
    """``sh600519`` / ``600519.SH`` / ``600519`` → ``600519``。"""
    text = str(raw).strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    text = text.split(".")[0]
    text = text.lstrip(".")
    if not (len(text) == 6 and text.isdigit()):
        raise ProviderDataError(f"非法 A 股代码：{raw!r}")
    return text


# ── 行情快照：stock_zh_a_spot_em ────────────────────────────────────────────
def transform_spot(
    records: Iterable[Record], symbols: Iterable[str], observed_at: datetime
) -> list[dict[str, Any]]:
    """全市场快照 → 只保留请求的 symbols。

    ``observed_at`` 由调用方的 Clock 注入：``stock_zh_a_spot_em`` **不返回时间戳**，
    因此快照时间只能是"我们取到它的时间"。这一点必须写进 docs（不得伪装成交易所撮合时间）。
    """
    if observed_at.tzinfo is None:
        raise ProviderDataError("observed_at 必须带时区")
    wanted = {normalize_symbol(s) for s in symbols}
    ctx = "stock_zh_a_spot_em"
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for record in records:
        raw_symbol = _optional_str(record, COL_SPOT_SYMBOL, ctx)
        if raw_symbol is None:
            raise ProviderDataError(f"{ctx}：必填列 {COL_SPOT_SYMBOL!r} 为空")
        try:
            symbol = normalize_symbol(raw_symbol)
        except ProviderDataError:
            continue  # 全市场快照里混有非 A 股代码时跳过；不是我们请求的标的，不算脏数据
        if wanted and symbol not in wanted:
            continue
        if symbol in seen:
            continue
        seen.add(symbol)

        out.append(
            {
                "symbol": symbol,
                "name": _optional_str(record, COL_SPOT_NAME, ctx),
                "last_price": _required_float(record, COL_SPOT_LAST, ctx),
                "prev_close": _required_float(record, COL_SPOT_PREV_CLOSE, ctx),
                "open": _optional_float(record, COL_SPOT_OPEN, ctx),
                "high": _optional_float(record, COL_SPOT_HIGH, ctx),
                "low": _optional_float(record, COL_SPOT_LOW, ctx),
                "volume": _optional_float(record, COL_SPOT_VOLUME, ctx),
                "turnover": _optional_float(record, COL_SPOT_AMOUNT, ctx),
                "volume_ratio": _optional_float(record, COL_SPOT_VOLUME_RATIO, ctx),
                "change_percent": _optional_float(record, COL_SPOT_CHANGE_PCT, ctx),
                "turnover_rate": _optional_float(record, COL_SPOT_TURNOVER_RATE, ctx),
                "last_timestamp": observed_at,
                "source": SOURCE_NAME,
                "source_url": quote_url(symbol),
                "volume_unit": VOLUME_UNIT,
                "amount_unit": AMOUNT_UNIT,
            }
        )
    return out


# ── K 线：stock_zh_a_hist（日线）/ stock_zh_a_hist_min_em（5 分钟）────────────
def _bar_payload(
    record: Record,
    *,
    ctx: str,
    symbol: str,
    bar_time: datetime,
    timeframe: str,
    adjustment: str,
) -> dict[str, Any]:
    open_ = _required_float(record, COL_HIST_OPEN, ctx)
    high = _required_float(record, COL_HIST_HIGH, ctx)
    low = _required_float(record, COL_HIST_LOW, ctx)
    close = _required_float(record, COL_HIST_CLOSE, ctx)
    volume = _required_float(record, COL_HIST_VOLUME, ctx)
    return {
        "symbol": symbol,
        "date": bar_time,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "turnover": _optional_float(record, COL_HIST_AMOUNT, ctx),
        "turnover_rate": _optional_float(record, COL_HIST_TURNOVER_RATE, ctx),
        "timeframe": timeframe,
        "adjustment": adjustment,
        "source": SOURCE_NAME,
        "source_url": quote_url(symbol),
        "volume_unit": VOLUME_UNIT,
        "amount_unit": AMOUNT_UNIT,
    }


def transform_daily(
    records: Iterable[Record], symbol: str, adjustment: str = DEFAULT_ADJUSTMENT
) -> list[dict[str, Any]]:
    """日线。``bar_time`` = 该交易日 15:00（收盘时刻）。

    日线在收盘前不可知，若把 bar_time 落在 00:00，任何"当日 09:45 的特征"都会误取到当日日线
    → 未来数据泄漏。落 15:00 让 ``bar_time <= data_cutoff`` 天然成立即可用（spec §4.2 / §16.1）。
    """
    ctx = "stock_zh_a_hist"
    code = normalize_symbol(symbol)
    out: list[dict[str, Any]] = []
    for record in records:
        bar_time = _required_datetime(record, COL_HIST_DATE, ctx)
        bar_time = datetime.combine(bar_time.date(), _DAILY_CLOSE, tzinfo=SHANGHAI)
        out.append(
            _bar_payload(
                record, ctx=ctx, symbol=code, bar_time=bar_time, timeframe="1d", adjustment=adjustment
            )
        )
    out.sort(key=lambda item: item["date"])
    return out


def transform_minute(
    records: Iterable[Record], symbol: str, adjustment: str = DEFAULT_ADJUSTMENT
) -> list[dict[str, Any]]:
    """5 分钟线。``bar_time`` = 上游"时间"列 = **该 K 线的结束时刻**。

    例：``2026-07-14 09:35:00`` 覆盖 09:30–09:35 的成交。用结束时刻做 bar_time，
    ``bar_time <= data_cutoff`` 才等价于"这根 K 线在 cutoff 时已经走完"（point-in-time 安全）。
    盘前/盘后的越界行直接丢弃（上游偶尔混入 09:30 之前的开盘集合竞价行）。
    """
    ctx = "stock_zh_a_hist_min_em"
    code = normalize_symbol(symbol)
    out: list[dict[str, Any]] = []
    for record in records:
        bar_time = _required_datetime(record, COL_MIN_TIME, ctx)
        clock_time = bar_time.time()
        if clock_time <= _SESSION_OPEN or clock_time > _SESSION_CLOSE:
            continue
        out.append(
            _bar_payload(
                record, ctx=ctx, symbol=code, bar_time=bar_time, timeframe="5m", adjustment=adjustment
            )
        )
    out.sort(key=lambda item: item["date"])
    return out


# ── 新闻：stock_news_em ─────────────────────────────────────────────────────
def transform_news(records: Iterable[Record], symbol: str) -> list[dict[str, Any]]:
    """个股新闻。``date`` = 发布时间（published_at），``url`` = 原文链接。

    ``stock_news_em`` 不接受时间窗参数，只能按 symbol 拉最近若干条；时间窗过滤在
    Fetcher 里按 ``start_date`` / ``end_date`` 完成（见 models/company_news.py）。
    """
    ctx = "stock_news_em"
    code = normalize_symbol(symbol)
    out: list[dict[str, Any]] = []
    for record in records:
        title = _required_str(record, COL_NEWS_TITLE, ctx)
        url = _required_str(record, COL_NEWS_URL, ctx)
        published_at = _required_datetime(record, COL_NEWS_PUBLISHED, ctx)
        out.append(
            {
                "date": published_at,
                "title": title,
                "text": _optional_str(record, COL_NEWS_BODY, ctx),
                "url": url,
                "symbols": code,
                "source": _optional_str(record, COL_NEWS_SOURCE, ctx) or SOURCE_NAME,
                "provider_source": SOURCE_NAME,
                "search_url": news_url(code),
                "keyword": _optional_str(record, COL_NEWS_KEYWORD, ctx),
            }
        )
    out.sort(key=lambda item: item["date"], reverse=True)
    return out
