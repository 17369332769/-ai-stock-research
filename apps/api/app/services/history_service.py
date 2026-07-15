"""历史行情查询。实时 Quote 与历史 K 线保持独立。"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.enums import Timeframe
from apps.api.app.core.errors import InstrumentNotFound
from apps.api.app.models.tables import Bar
from apps.api.app.repositories import bars as bars_repo
from apps.api.app.repositories import instruments as instruments_repo
from apps.api.app.schemas.bars import BarDTO, BarRangeSummaryDTO, BarsMetaDTO

DAILY_RANGES: dict[str, int] = {
    "1m": 31,
    "3m": 93,
    "6m": 186,
    "1y": 366,
    "3y": 1096,
}


def _summarize(range_key: str, rows: list[Bar]) -> BarRangeSummaryDTO:
    first = rows[0]
    last = rows[-1]
    highest = max(rows, key=lambda row: row.close)
    lowest = min(rows, key=lambda row: row.close)
    start_close = float(first.close)
    end_close = float(last.close)
    return BarRangeSummaryDTO(
        range_key=range_key,
        count=len(rows),
        start_at=first.bar_time,
        end_at=last.bar_time,
        start_close=start_close,
        end_close=end_close,
        change_percent=(end_close / start_close - 1) if start_close != 0 else None,
        highest_close=float(highest.close),
        highest_close_at=highest.bar_time,
        lowest_close=float(lowest.close),
        lowest_close_at=lowest.bar_time,
    )


def _build_meta(rows: list[Bar], timeframe: Timeframe) -> BarsMetaDTO:
    summaries: dict[str, BarRangeSummaryDTO] = {}
    if rows:
        summaries["all"] = _summarize("all", rows)
        if timeframe is Timeframe.DAY1:
            latest_at = rows[-1].bar_time
            for key, days in DAILY_RANGES.items():
                cutoff = latest_at - timedelta(days=days)
                selected = [row for row in rows if row.bar_time >= cutoff]
                if selected:
                    summaries[key] = _summarize(key, selected)
    return BarsMetaDTO(
        timeframe=timeframe,
        total_count=len(rows),
        updated_at=max((row.observed_at for row in rows), default=None),
        summaries=summaries,
    )


async def list_bars(
    session: AsyncSession,
    symbol: str,
    *,
    timeframe: Timeframe,
    limit: int,
    now: datetime,
) -> tuple[list[BarDTO], BarsMetaDTO]:
    if await instruments_repo.get(session, symbol) is None:
        raise InstrumentNotFound(symbol)

    rows = await bars_repo.recent(
        session,
        symbol,
        timeframe=timeframe.value,
        cutoff=now,
        limit=limit,
    )
    dtos = [
        BarDTO.from_row(row, rows[index - 1].close if index > 0 else None)
        for index, row in enumerate(rows)
    ]
    return dtos, _build_meta(rows, timeframe)
