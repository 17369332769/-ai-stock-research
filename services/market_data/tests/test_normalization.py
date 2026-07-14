"""规范化层测试：字段范围、时间、OHLC 一致性、去重。"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from apps.api.app.core.clock import SHANGHAI
from apps.api.app.core.errors import InvalidArgument
from services.market_data.contracts import BarRecord, DocumentRecord, QuoteRecord
from services.market_data.normalization import (
    RejectReason,
    content_hash,
    dedup_documents,
    exchange_of,
    freshness_of,
    normalize_symbol,
    validate_bar,
    validate_document,
    validate_quote,
)

NOW = datetime(2026, 7, 14, 9, 50, tzinfo=SHANGHAI)


def _quote(**overrides: object) -> QuoteRecord:
    payload: dict[str, object] = {
        "symbol": "600519",
        "price": Decimal("1215.04"),
        "previous_close": Decimal("1211.00"),
        "open": Decimal("1212.00"),
        "high": Decimal("1220.00"),
        "low": Decimal("1208.00"),
        "volume": Decimal("31234"),
        "amount": Decimal("3790000000"),
        "volume_ratio": Decimal("0.98"),
        "source": "eastmoney_via_akshare",
        "source_url": "https://quote.eastmoney.com/sh600519.html",
        "observed_at": NOW,
        "raw_payload": {},
    }
    payload.update(overrides)
    return QuoteRecord(**payload)  # type: ignore[arg-type]


def _bar(**overrides: object) -> BarRecord:
    payload: dict[str, object] = {
        "symbol": "600519",
        "timeframe": "1d",
        "bar_time": datetime(2026, 7, 13, 15, 0, tzinfo=SHANGHAI),
        "open": Decimal("1202"),
        "high": Decimal("1214"),
        "low": Decimal("1199"),
        "close": Decimal("1211"),
        "volume": Decimal("30100"),
        "amount": Decimal("3630000000"),
        "adjustment": "qfq",
        "source": "eastmoney_via_akshare",
        "source_url": "https://quote.eastmoney.com/sh600519.html",
        "observed_at": NOW,
    }
    payload.update(overrides)
    return BarRecord(**payload)  # type: ignore[arg-type]


def _doc(**overrides: object) -> DocumentRecord:
    payload: dict[str, object] = {
        "symbol": "600519",
        "document_type": "announcement",
        "title": "2026年半年度业绩预告",
        "body_text": None,
        "source": "cninfo",
        "source_url": "http://static.cninfo.com.cn/finalpage/2026-07-14/1.PDF",
        "published_at": datetime(2026, 7, 14, 8, 30, tzinfo=SHANGHAI),
        "observed_at": NOW,
    }
    payload.update(overrides)
    return DocumentRecord(**payload)  # type: ignore[arg-type]


# ── symbols ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("600519", "600519"), ("sh600519", "600519"), ("600519.SH", "600519"), (" sz000001 ", "000001")],
)
def test_normalize_symbol(raw: str, expected: str) -> None:
    assert normalize_symbol(raw) == expected


@pytest.mark.parametrize("raw", ["", "AAPL", "60051", "830001"])
def test_normalize_symbol_rejects_non_a_share(raw: str) -> None:
    """北交所 8 开头也拒 —— MVP 只做沪深300（spec §2）。"""
    with pytest.raises(InvalidArgument):
        normalize_symbol(raw)


@pytest.mark.parametrize(
    ("symbol", "exchange"),
    [("600519", "SSE"), ("601318", "SSE"), ("000001", "SZSE"), ("300750", "SZSE"), ("002594", "SZSE")],
)
def test_exchange_of(symbol: str, exchange: str) -> None:
    assert exchange_of(symbol) == exchange


# ── 行情校验 ────────────────────────────────────────────────────────────────
def test_valid_quote_passes() -> None:
    assert validate_quote(_quote(), NOW) == []


def test_quote_zero_previous_close_rejected() -> None:
    """previous_close=0 会让 change_percent 除零，且 today_close 目标无参考价。"""
    issues = validate_quote(_quote(previous_close=Decimal("0")), NOW)
    assert {issue.reason for issue in issues} >= {
        RejectReason.NON_POSITIVE_PRICE,
        RejectReason.ZERO_PREVIOUS_CLOSE,
    }


def test_quote_negative_price_rejected() -> None:
    issues = validate_quote(_quote(price=Decimal("-1")), NOW)
    assert any(issue.reason == RejectReason.NON_POSITIVE_PRICE for issue in issues)


def test_quote_price_outside_high_low_rejected() -> None:
    issues = validate_quote(_quote(high=Decimal("1200")), NOW)
    assert any(issue.reason == RejectReason.OHLC_INCONSISTENT for issue in issues)


def test_quote_high_below_low_rejected() -> None:
    issues = validate_quote(_quote(high=Decimal("1100"), low=Decimal("1300")), NOW)
    assert any(issue.reason == RejectReason.OHLC_INCONSISTENT for issue in issues)


def test_quote_negative_volume_rejected() -> None:
    issues = validate_quote(_quote(volume=Decimal("-1")), NOW)
    assert any(issue.reason == RejectReason.NEGATIVE_VOLUME for issue in issues)


def test_quote_future_observed_at_rejected() -> None:
    issues = validate_quote(_quote(observed_at=NOW + timedelta(hours=1)), NOW)
    assert any(issue.reason == RejectReason.FUTURE_TIMESTAMP for issue in issues)


def test_quote_absurd_price_rejected() -> None:
    issues = validate_quote(
        _quote(price=Decimal("999999999"), high=Decimal("999999999"), low=Decimal("1")), NOW
    )
    assert any(issue.reason == RejectReason.PRICE_OUT_OF_RANGE for issue in issues)


# ── K 线校验 ────────────────────────────────────────────────────────────────
def test_valid_bar_passes() -> None:
    assert validate_bar(_bar(), NOW) == []


def test_bar_high_below_open_rejected() -> None:
    """high >= max(open, close, low) —— 这是 OHLC 的定义。"""
    issues = validate_bar(_bar(high=Decimal("1100")), NOW)
    assert any(issue.reason == RejectReason.OHLC_INCONSISTENT for issue in issues)


def test_bar_low_above_close_rejected() -> None:
    """low <= min(open, close, high)。"""
    issues = validate_bar(_bar(low=Decimal("1300")), NOW)
    assert any(issue.reason == RejectReason.OHLC_INCONSISTENT for issue in issues)


def test_bar_negative_volume_rejected() -> None:
    issues = validate_bar(_bar(volume=Decimal("-1")), NOW)
    assert any(issue.reason == RejectReason.NEGATIVE_VOLUME for issue in issues)


def test_bar_non_positive_price_rejected() -> None:
    issues = validate_bar(_bar(low=Decimal("0"), open=Decimal("0")), NOW)
    assert any(issue.reason == RejectReason.NON_POSITIVE_PRICE for issue in issues)


@pytest.mark.leakage
def test_future_bar_rejected() -> None:
    """**数据泄漏防线**：未来 K 线绝不入库（spec §16.1）。"""
    issues = validate_bar(_bar(bar_time=NOW + timedelta(days=1)), NOW)
    assert any(issue.reason == RejectReason.FUTURE_TIMESTAMP for issue in issues)


@pytest.mark.leakage
def test_today_daily_bar_at_close_rejected_during_session() -> None:
    """09:50 时，当日 15:00 的日线还没走完 —— 它在未来，必须拒收。

    这正是把日线 bar_time 落在 15:00 的意义：盘中特征永远取不到当日日线。
    """
    today_close = datetime(2026, 7, 14, 15, 0, tzinfo=SHANGHAI)
    issues = validate_bar(_bar(bar_time=today_close), NOW)
    assert any(issue.reason == RejectReason.FUTURE_TIMESTAMP for issue in issues)


def test_bar_at_close_accepted_after_close() -> None:
    after_close = datetime(2026, 7, 14, 15, 10, tzinfo=SHANGHAI)
    today_close = datetime(2026, 7, 14, 15, 0, tzinfo=SHANGHAI)
    assert validate_bar(_bar(bar_time=today_close), after_close) == []


# ── 文档校验 ────────────────────────────────────────────────────────────────
def test_valid_document_passes() -> None:
    assert validate_document(_doc(), NOW) == []


def test_document_empty_title_rejected() -> None:
    issues = validate_document(_doc(title="   "), NOW)
    assert any(issue.reason == RejectReason.EMPTY_TITLE for issue in issues)


def test_document_bad_url_rejected() -> None:
    issues = validate_document(_doc(source_url="javascript:alert(1)"), NOW)
    assert any(issue.reason == RejectReason.BAD_URL for issue in issues)


@pytest.mark.leakage
def test_document_published_in_future_rejected() -> None:
    """未来公告不得进入特征快照（spec §16.1 泄漏测试）。"""
    issues = validate_document(_doc(published_at=NOW + timedelta(days=1)), NOW)
    assert any(issue.reason == RejectReason.FUTURE_TIMESTAMP for issue in issues)


def test_document_epoch_zero_timestamp_rejected() -> None:
    issues = validate_document(_doc(published_at=datetime(1970, 1, 1, tzinfo=SHANGHAI)), NOW)
    assert any(issue.reason == RejectReason.IMPLAUSIBLE_TIMESTAMP for issue in issues)


# ── 去重 ───────────────────────────────────────────────────────────────────
def test_content_hash_is_stable_and_url_independent() -> None:
    """同一份公告在两个板块页挂出（URL 不同）→ 同一个 content_hash。"""
    a = _doc(source_url="http://static.cninfo.com.cn/finalpage/2026-07-14/1.PDF")
    b = _doc(source_url="http://static.cninfo.com.cn/finalpage/2026-07-14/2.PDF")
    assert content_hash(a) == content_hash(b)
    assert len(content_hash(a)) == 64  # documents.content_hash char(64)


def test_content_hash_normalizes_whitespace_and_width() -> None:
    """排版变化（全角空格、换行）不该产生"新公告"。"""
    a = _doc(title="业绩预告")
    b = _doc(title="  业绩预告\n")
    assert content_hash(a) == content_hash(b)


def test_content_hash_differs_across_symbols() -> None:
    assert content_hash(_doc(symbol="600519")) != content_hash(_doc(symbol="000001"))


def test_content_hash_differs_across_document_types() -> None:
    assert content_hash(_doc(document_type="announcement")) != content_hash(
        _doc(document_type="news")
    )


def test_dedup_announcements_by_content_hash() -> None:
    docs = [
        _doc(source_url="http://static.cninfo.com.cn/a.PDF"),
        _doc(source_url="http://static.cninfo.com.cn/b.PDF"),  # 同内容不同 URL
    ]
    kept, dropped = dedup_documents(docs)
    assert len(kept) == 1
    assert len(dropped) == 1
    assert dropped[0].reason == RejectReason.DUPLICATE


def test_dedup_news_by_url_and_content() -> None:
    """spec §8：新闻按 URL 和内容哈希去重（两条规则都要有）。"""
    base = _doc(
        document_type="news",
        body_text="正文",
        source="东方财富",
        source_url="https://finance.eastmoney.com/a/1.html",
    )
    reprint = _doc(  # 转载：内容相同、URL 不同 → 内容哈希命中
        document_type="news",
        body_text="正文",
        source="同花顺",
        source_url="https://news.10jqka.com.cn/1.shtml",
    )
    resampled = _doc(  # 重复采集：URL 相同、标题被上游改过 → URL 规则命中
        document_type="news",
        title="白酒板块早盘走强（更新）",
        body_text="正文",
        source="东方财富",
        source_url="https://finance.eastmoney.com/a/1.html",
    )
    kept, dropped = dedup_documents([base, reprint, resampled])
    assert len(kept) == 1
    assert len(dropped) == 2


def test_dedup_keeps_distinct_documents() -> None:
    docs = [_doc(title="公告A"), _doc(title="公告B")]
    kept, dropped = dedup_documents(docs)
    assert len(kept) == 2
    assert dropped == []


# ── 新鲜度 ──────────────────────────────────────────────────────────────────
def test_freshness_within_threshold() -> None:
    assert freshness_of(NOW - timedelta(seconds=179), NOW, 180).value == "fresh"


def test_freshness_beyond_threshold_is_stale() -> None:
    """spec §3.2：180 秒后标 stale，禁止把旧行情冒充实时。"""
    assert freshness_of(NOW - timedelta(seconds=181), NOW, 180).value == "stale"
