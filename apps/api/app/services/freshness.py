"""行情新鲜度（spec §3.2 / §7）。

- ``age > settings.quote_stale_seconds``（默认 180 秒）⇒ ``freshness=stale`` 且附 ``age_seconds``；
- **禁止把旧行情标记为实时**；
- 从未取得行情（一条快照都没有）⇒ 424 PROVIDER_UNAVAILABLE，绝不返回默认价格。

新鲜度只在这里算一次，前端不得自行计算（spec §5.1）。
"""

from __future__ import annotations

from datetime import datetime

from apps.api.app.core.clock import to_shanghai
from apps.api.app.core.enums import Freshness, QuoteAgeStatus
from apps.api.app.core.errors import ProviderUnavailable
from apps.api.app.core.logging import METRICS
from apps.api.app.core.settings import get_settings
from apps.api.app.models.tables import LatestQuote, Quote
from apps.api.app.schemas.common import to_float
from apps.api.app.schemas.quotes import QuoteDTO


def compute_age_seconds(observed_at: datetime, now: datetime) -> int:
    """行情年龄。时钟回拨/数据源时间超前时夹到 0，不产生负数年龄。"""
    return max(0, int((now - observed_at).total_seconds()))


def compute_freshness(age_seconds: int) -> Freshness:
    stale_after = get_settings().quote_stale_seconds
    return Freshness.STALE if age_seconds > stale_after else Freshness.FRESH


def compute_age_status(age_seconds: int) -> QuoteAgeStatus:
    if age_seconds <= 45:
        return QuoteAgeStatus.LATEST
    if age_seconds <= 120:
        return QuoteAgeStatus.DELAYED
    return QuoteAgeStatus.STALE


def change_percent(price: float, previous_close: float, symbol: str) -> float:
    """涨跌幅 = price / previous_close - 1。

    昨收为 0 是上游脏数据，fail closed（424），不返回 0.0 冒充"没涨没跌"。
    """
    if previous_close == 0:
        raise ProviderUnavailable(f"{symbol} 上游行情无效：昨收为 0，无法计算涨跌幅")
    return price / previous_close - 1


def to_quote_dto(row: Quote | LatestQuote, now: datetime) -> QuoteDTO:
    fetched_at = to_shanghai(row.fetched_at if isinstance(row, LatestQuote) else row.observed_at)
    market_time = (
        to_shanghai(row.market_time)
        if isinstance(row, LatestQuote) and row.market_time is not None
        else None
    )
    observed_at = market_time or fetched_at
    age = compute_age_seconds(fetched_at, now)
    freshness = compute_freshness(age)
    if freshness is Freshness.STALE:
        METRICS.record_stale_quote(row.symbol)

    price = float(row.price)
    previous_close = float(row.previous_close)
    return QuoteDTO(
        symbol=row.symbol,
        price=price,
        previous_close=previous_close,
        change_amount=price - previous_close,
        change_percent=change_percent(price, previous_close, row.symbol),
        open=to_float(row.open),
        high=to_float(row.high),
        low=to_float(row.low),
        volume=to_float(row.volume),
        amount=to_float(row.amount),
        volume_ratio=to_float(row.volume_ratio),
        turnover_rate=to_float(row.turnover_rate),
        bid1=to_float(row.bid1) if isinstance(row, LatestQuote) else None,
        ask1=to_float(row.ask1) if isinstance(row, LatestQuote) else None,
        observed_at=observed_at,
        market_time=market_time,
        fetched_at=fetched_at,
        source=row.source,
        source_url=row.source_url,
        freshness=freshness,
        age_status=compute_age_status(age),
        data_age_seconds=age,
        age_seconds=age if freshness is Freshness.STALE else None,
    )
