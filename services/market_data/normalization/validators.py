"""数据质量校验：范围、时间、OHLC 一致性。

分工（重要）：

- **网关层**（openbb_gateway）管 *schema*：字段缺失、类型改变、非有限数值 → ``ProviderUnavailable``。
  上游换字段必须炸得很响，不能静默。
- **本模块**管 *语义*：价格为负、high < low、K 线时间在未来、公告 published_at 在未来……
  这类脏数据**逐条拒收**并给出结构化理由，由 ingest 汇总成 ``IngestReport``，
  写进作业 warnings 并打日志。整批全脏 → ingest 抛 ``ProviderUnavailable``。

拒收是**记录在案**的，不是静默丢弃（spec §8：不得静默使用缓存冒充新数据）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from apps.api.app.core.enums import Freshness
from services.market_data.contracts import BarRecord, DocumentRecord, QuoteRecord
from services.market_data.normalization.symbols import is_valid_symbol

# A 股价格上界（贵州茅台历史高点约 2600 元；留足冗余，只为挡住 1e9 这种明显脏值）
MAX_PRICE = Decimal("100000")
MIN_PRICE = Decimal("0.001")

# 允许的时钟偏差：上游时间戳可能比本地时钟略快
CLOCK_SKEW = timedelta(minutes=5)

# 公告/新闻允许的最大回溯（防止把 1970 epoch 脏值当成真公告）
MAX_DOCUMENT_AGE = timedelta(days=365 * 20)


class RejectReason(StrEnum):
    BAD_SYMBOL = "bad_symbol"
    NON_POSITIVE_PRICE = "non_positive_price"
    PRICE_OUT_OF_RANGE = "price_out_of_range"
    ZERO_PREVIOUS_CLOSE = "zero_previous_close"
    NEGATIVE_VOLUME = "negative_volume"
    OHLC_INCONSISTENT = "ohlc_inconsistent"
    FUTURE_TIMESTAMP = "future_timestamp"
    NAIVE_DATETIME = "naive_datetime"
    EMPTY_TITLE = "empty_title"
    BAD_URL = "bad_url"
    IMPLAUSIBLE_TIMESTAMP = "implausible_timestamp"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class Rejection:
    """一条被拒收的记录。``key`` 足以在日志里定位到具体记录。"""

    key: str
    reason: RejectReason
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"key": self.key, "reason": self.reason.value, "detail": self.detail}


def _check_positive_price(key: str, name: str, value: Decimal | None) -> list[Rejection]:
    if value is None:
        return []
    if value <= 0:
        return [Rejection(key, RejectReason.NON_POSITIVE_PRICE, f"{name}={value} 非正数")]
    if value < MIN_PRICE or value > MAX_PRICE:
        return [
            Rejection(
                key, RejectReason.PRICE_OUT_OF_RANGE, f"{name}={value} 超出 [{MIN_PRICE},{MAX_PRICE}]"
            )
        ]
    return []


def validate_quote(quote: QuoteRecord, now: datetime) -> list[Rejection]:
    key = f"quote:{quote.symbol}@{quote.observed_at.isoformat()}"
    issues: list[Rejection] = []

    if not is_valid_symbol(quote.symbol):
        issues.append(Rejection(key, RejectReason.BAD_SYMBOL, f"symbol={quote.symbol!r}"))

    for name, value in (
        ("price", quote.price),
        ("previous_close", quote.previous_close),
        ("open", quote.open),
        ("high", quote.high),
        ("low", quote.low),
    ):
        issues.extend(_check_positive_price(key, name, value))

    if quote.previous_close == 0:
        # change_percent 会除零；且 quotes.previous_close NOT NULL 语义上必须可用
        issues.append(Rejection(key, RejectReason.ZERO_PREVIOUS_CLOSE, "previous_close=0"))

    for name, value in (("volume", quote.volume), ("amount", quote.amount)):
        if value is not None and value < 0:
            issues.append(Rejection(key, RejectReason.NEGATIVE_VOLUME, f"{name}={value} 为负"))

    issues.extend(_check_intraday_ohlc(key, quote))

    if quote.observed_at > now + CLOCK_SKEW:
        issues.append(
            Rejection(
                key,
                RejectReason.FUTURE_TIMESTAMP,
                f"observed_at={quote.observed_at.isoformat()} 晚于当前时刻 {now.isoformat()}",
            )
        )
    return issues


def _check_intraday_ohlc(key: str, quote: QuoteRecord) -> list[Rejection]:
    """快照的 high/low 必须包住 open 与最新价。"""
    high, low = quote.high, quote.low
    if high is None or low is None:
        return []
    issues: list[Rejection] = []
    if high < low:
        issues.append(Rejection(key, RejectReason.OHLC_INCONSISTENT, f"high={high} < low={low}"))
        return issues
    bounded = [("price", quote.price)]
    if quote.open is not None:
        bounded.append(("open", quote.open))
    for name, value in bounded:
        if value > high:
            issues.append(Rejection(key, RejectReason.OHLC_INCONSISTENT, f"{name}={value} > high={high}"))
        if value < low:
            issues.append(Rejection(key, RejectReason.OHLC_INCONSISTENT, f"{name}={value} < low={low}"))
    return issues


def validate_bar(bar: BarRecord, now: datetime) -> list[Rejection]:
    """K 线校验。

    OHLC 一致性（spec 要求逐条断言）：
        high >= max(open, close, low)
        low  <= min(open, close, high)
        全部 > 0，volume >= 0
    未来 K 线（``bar_time > now``）**必须拒收** —— 它是数据泄漏的直接入口（spec §16.1 泄漏测试）。
    """
    key = f"bar:{bar.symbol}:{bar.timeframe}@{bar.bar_time.isoformat()}"
    issues: list[Rejection] = []

    if not is_valid_symbol(bar.symbol):
        issues.append(Rejection(key, RejectReason.BAD_SYMBOL, f"symbol={bar.symbol!r}"))

    for name, value in (("open", bar.open), ("high", bar.high), ("low", bar.low), ("close", bar.close)):
        issues.extend(_check_positive_price(key, name, value))

    if bar.volume < 0:
        issues.append(Rejection(key, RejectReason.NEGATIVE_VOLUME, f"volume={bar.volume} 为负"))
    if bar.amount is not None and bar.amount < 0:
        issues.append(Rejection(key, RejectReason.NEGATIVE_VOLUME, f"amount={bar.amount} 为负"))

    highest = max(bar.open, bar.close, bar.low)
    lowest = min(bar.open, bar.close, bar.high)
    if bar.high < highest:
        issues.append(
            Rejection(
                key,
                RejectReason.OHLC_INCONSISTENT,
                f"high={bar.high} < max(open={bar.open}, close={bar.close}, low={bar.low})",
            )
        )
    if bar.low > lowest:
        issues.append(
            Rejection(
                key,
                RejectReason.OHLC_INCONSISTENT,
                f"low={bar.low} > min(open={bar.open}, close={bar.close}, high={bar.high})",
            )
        )

    if bar.bar_time > now + CLOCK_SKEW:
        issues.append(
            Rejection(
                key,
                RejectReason.FUTURE_TIMESTAMP,
                f"bar_time={bar.bar_time.isoformat()} 在未来（now={now.isoformat()}）—— 拒收，防止数据泄漏",
            )
        )
    return issues


def validate_document(document: DocumentRecord, now: datetime) -> list[Rejection]:
    key = f"doc:{document.source_url}"
    issues: list[Rejection] = []

    if document.symbol is not None and not is_valid_symbol(document.symbol):
        issues.append(Rejection(key, RejectReason.BAD_SYMBOL, f"symbol={document.symbol!r}"))
    if not document.title.strip():
        issues.append(Rejection(key, RejectReason.EMPTY_TITLE, "title 为空"))
    if not document.source_url.startswith(("http://", "https://", "file://")):
        issues.append(Rejection(key, RejectReason.BAD_URL, f"source_url={document.source_url!r}"))

    if document.published_at > now + CLOCK_SKEW:
        issues.append(
            Rejection(
                key,
                RejectReason.FUTURE_TIMESTAMP,
                f"published_at={document.published_at.isoformat()} 在未来（now={now.isoformat()}）",
            )
        )
    if document.published_at < now - MAX_DOCUMENT_AGE:
        issues.append(
            Rejection(
                key,
                RejectReason.IMPLAUSIBLE_TIMESTAMP,
                f"published_at={document.published_at.isoformat()} 早于 20 年前，疑似脏时间戳",
            )
        )
    return issues


def freshness_of(observed_at: datetime, now: datetime, stale_seconds: int) -> Freshness:
    """行情新鲜度（spec §7.2）。禁止把 stale 当 fresh 展示。"""
    age = (now - observed_at).total_seconds()
    return Freshness.FRESH if age <= stale_seconds else Freshness.STALE


def age_seconds(observed_at: datetime, now: datetime) -> int:
    return max(0, int((now - observed_at).total_seconds()))
