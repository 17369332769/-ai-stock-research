"""独立的历史行情 DTO。历史 K 线不会写入实时 Quote 字段。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from apps.api.app.core.enums import Timeframe
from apps.api.app.models.tables import Bar
from apps.api.app.schemas.common import BaseDTO, PageInfo, require_float, to_float


class BarDTO(BaseDTO):
    symbol: str
    timeframe: Timeframe
    bar_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float | None = None
    adjustment: str
    source: str
    source_url: str | None = None
    observed_at: datetime
    change_amount: float | None = None
    change_percent: float | None = None

    @classmethod
    def from_row(cls, row: Bar, previous_close: Decimal | None = None) -> BarDTO:
        close = require_float(row.close)
        previous = to_float(previous_close)
        return cls(
            symbol=row.symbol,
            timeframe=Timeframe(row.timeframe),
            bar_time=row.bar_time,
            open=require_float(row.open),
            high=require_float(row.high),
            low=require_float(row.low),
            close=close,
            volume=require_float(row.volume),
            amount=to_float(row.amount),
            adjustment=row.adjustment,
            source=row.source,
            source_url=row.source_url,
            observed_at=row.observed_at,
            change_amount=(close - previous) if previous is not None else None,
            change_percent=(close / previous - 1) if previous not in (None, 0) else None,
        )


class BarRangeSummaryDTO(BaseDTO):
    range_key: str
    count: int
    start_at: datetime
    end_at: datetime
    start_close: float
    end_close: float
    change_percent: float | None
    highest_close: float
    highest_close_at: datetime
    lowest_close: float
    lowest_close_at: datetime


class BarsMetaDTO(BaseDTO):
    timeframe: Timeframe
    total_count: int
    updated_at: datetime | None
    summaries: dict[str, BarRangeSummaryDTO]


class BarsResponse(BaseDTO):
    data: list[BarDTO]
    page: PageInfo
    meta: BarsMetaDTO
    request_id: str
