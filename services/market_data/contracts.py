"""OpenBB 网关的记录契约（spec §5.2）。

这些是**规范化之后**的领域记录，是 OpenBB 与业务代码之间唯一的数据形态。
上游字段变化被隔离在 ``services/openbb_extensions`` 内部，不允许泄漏到这里。

溯源字段是强制的（spec §4.2）：
- 行情/K线：``source`` + ``source_url`` + ``observed_at``
- 文档：另加 ``published_at``（``published_at`` 不适用于报价快照）
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

Universe = Literal["CSI300"]
Timeframe = Literal["5m", "1d"]
DocumentKind = Literal["announcement", "news"]


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def _reject_naive_datetime(cls, v: Any) -> Any:
        if isinstance(v, datetime) and v.tzinfo is None:
            raise ValueError("拒绝 naive datetime：所有时间必须带时区")
        return v


class InstrumentRecord(_Record):
    symbol: str = Field(min_length=6, max_length=12)
    name: str
    exchange: Literal["SSE", "SZSE"]
    industry: str | None = None
    listed_at: date | None = None


class UniverseMemberRecord(_Record):
    """成分股的一段有效期。``effective_to=None`` 表示当前仍是成分股。"""

    universe: Universe
    symbol: str
    effective_from: date
    effective_to: date | None = None
    source: str
    source_url: str
    observed_at: datetime


class QuoteRecord(_Record):
    symbol: str
    price: Decimal
    previous_close: Decimal
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    volume: Decimal | None = None
    amount: Decimal | None = None
    volume_ratio: Decimal | None = None
    turnover_rate: Decimal | None = None
    bid1: Decimal | None = None
    ask1: Decimal | None = None
    market_time: datetime | None = None
    source: str
    source_url: str | None = None
    observed_at: datetime
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def change_percent(self) -> Decimal:
        if self.previous_close == 0:
            raise ValueError(f"{self.symbol} 昨收为 0，无法计算涨跌幅")
        return self.price / self.previous_close - 1


class BarRecord(_Record):
    symbol: str
    timeframe: Timeframe
    bar_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    amount: Decimal | None = None
    adjustment: str = "qfq"
    source: str
    source_url: str | None = None
    observed_at: datetime


class DocumentRecord(_Record):
    symbol: str | None
    document_type: DocumentKind
    title: str
    body_text: str | None
    source: str
    source_url: str
    published_at: datetime
    observed_at: datetime


class OpenBBGateway(Protocol):
    """业务代码访问外部数据的**唯一**出口（spec §4.2 / §5.1）。

    实现必须走 OpenBB 内部 REST；任何直接 requests/httpx 打第三方 URL 的代码都是违规，
    由 ``tests/integration/test_no_direct_third_party_calls.py`` 断言（验收 §15.19）。
    """

    async def get_universe_members(
        self, universe: Universe, as_of: date
    ) -> list[UniverseMemberRecord]: ...

    async def search_instruments(
        self, universe: Universe, query: str, as_of: date, limit: int
    ) -> list[InstrumentRecord]: ...

    async def get_quotes(self, symbols: list[str], as_of: datetime) -> list[QuoteRecord]: ...

    async def get_bars(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[BarRecord]: ...

    async def get_announcements(
        self, symbol: str, start: datetime, end: datetime
    ) -> list[DocumentRecord]: ...

    async def get_news(self, symbol: str, start: datetime, end: datetime) -> list[DocumentRecord]: ...
