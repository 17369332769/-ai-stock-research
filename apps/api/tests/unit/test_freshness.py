"""行情新鲜度（spec §3.2 / §7 / 验收 §15.2）。

180 秒是硬边界：<=180 秒 fresh；>180 秒 stale 且必须附 age_seconds。
**禁止把旧行情标记为实时**；昨收为 0 的脏数据 fail closed，不返回 0.0 冒充"没涨没跌"。
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from apps.api.app.core.enums import Freshness, QuoteAgeStatus
from apps.api.app.core.errors import ProviderUnavailable
from apps.api.app.core.settings import get_settings
from apps.api.app.models.tables import Quote
from apps.api.app.services.freshness import (
    change_percent,
    compute_age_seconds,
    compute_age_status,
    compute_freshness,
    to_quote_dto,
)
from apps.api.tests.conftest import AT_0950, SYMBOL


def make_quote(age_seconds: int, previous_close: str = "1211.05") -> Quote:
    return Quote(
        symbol=SYMBOL,
        observed_at=AT_0950 - timedelta(seconds=age_seconds),
        price=Decimal("1215.04"),
        previous_close=Decimal(previous_close),
        open=None,
        high=None,
        low=None,
        volume=Decimal("31000"),
        amount=None,
        volume_ratio=Decimal("1.12"),
        source="eastmoney_via_akshare",
        source_url=None,
        raw_payload={},
    )


def test_stale_threshold_is_180_seconds() -> None:
    assert get_settings().quote_stale_seconds == 180


def test_age_at_boundary_is_fresh() -> None:
    assert compute_freshness(180) is Freshness.FRESH


def test_age_past_boundary_is_stale() -> None:
    assert compute_freshness(181) is Freshness.STALE


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (45, QuoteAgeStatus.LATEST),
        (46, QuoteAgeStatus.DELAYED),
        (120, QuoteAgeStatus.DELAYED),
        (121, QuoteAgeStatus.STALE),
        (180, QuoteAgeStatus.STALE),
        (181, QuoteAgeStatus.STALE),
    ],
)
def test_age_status_boundaries(age: int, expected: QuoteAgeStatus) -> None:
    """003：列表、详情与摘要共用的权威三档状态边界。"""
    assert compute_age_status(age) is expected


def test_age_seconds_never_negative() -> None:
    """数据源时间超前 / 时钟回拨时不产生负数年龄。"""
    future = AT_0950 + timedelta(seconds=30)
    assert compute_age_seconds(future, AT_0950) == 0


def test_fresh_quote_has_no_age_seconds() -> None:
    dto = to_quote_dto(make_quote(age_seconds=10), AT_0950)
    assert dto.freshness is Freshness.FRESH
    assert dto.age_seconds is None


def test_stale_quote_carries_age_seconds() -> None:
    """spec §7：行情过期但仍有最后值 ⇒ 200 + freshness=stale + age_seconds。"""
    dto = to_quote_dto(make_quote(age_seconds=600), AT_0950)
    assert dto.freshness is Freshness.STALE
    assert dto.age_seconds == 600


def test_change_percent_math() -> None:
    assert change_percent(110.0, 100.0, SYMBOL) == pytest.approx(0.10)


def test_change_percent_zero_previous_close_fails_closed() -> None:
    with pytest.raises(ProviderUnavailable):
        change_percent(110.0, 0.0, SYMBOL)


def test_to_quote_dto_zero_previous_close_fails_closed() -> None:
    """脏数据不得被"默认值"掩盖。"""
    with pytest.raises(ProviderUnavailable):
        to_quote_dto(make_quote(age_seconds=1, previous_close="0"), AT_0950)


def test_quote_dto_carries_volume_and_volume_ratio() -> None:
    """F2 要求展示成交量与量比。"""
    dto = to_quote_dto(make_quote(age_seconds=1), AT_0950)
    assert dto.volume == pytest.approx(31000.0)
    assert dto.volume_ratio == pytest.approx(1.12)


def test_quote_dto_numbers_are_json_numbers_not_strings() -> None:
    """spec §7.2 的示例里 price 是 JSON 数字（1215.04），不是字符串。"""
    dto = to_quote_dto(make_quote(age_seconds=1), AT_0950)
    payload = dto.model_dump(mode="json")
    assert isinstance(payload["price"], float)
    assert isinstance(payload["change_percent"], float)


def test_quote_dto_observed_at_is_timezone_aware() -> None:
    dto = to_quote_dto(make_quote(age_seconds=1), AT_0950)
    assert dto.observed_at.tzinfo is not None
