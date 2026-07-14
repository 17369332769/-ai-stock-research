"""股票快照（spec §7.2）。

- 行情过期但仍有最后值 ⇒ **200** + ``freshness=stale`` + ``age_seconds``；
- **从未取得行情** ⇒ **424 PROVIDER_UNAVAILABLE**（不返回默认值/假数据）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.clock import to_shanghai
from apps.api.app.core.enums import CSI300_BENCHMARK_SYMBOL, CSI300_CODE
from apps.api.app.core.errors import InstrumentNotFound, ProviderUnavailable
from apps.api.app.repositories import analyses as analyses_repo
from apps.api.app.repositories import instruments as instruments_repo
from apps.api.app.repositories import predictions as predictions_repo
from apps.api.app.repositories import quotes as quotes_repo
from apps.api.app.schemas.quotes import RelativeStrengthDTO, SnapshotDTO
from apps.api.app.services.freshness import change_percent, to_quote_dto


async def get_snapshot(session: AsyncSession, symbol: str, now: datetime) -> SnapshotDTO:
    instrument = await instruments_repo.get(session, symbol)
    if instrument is None:
        raise InstrumentNotFound(symbol)

    quote_row = await quotes_repo.latest(session, symbol)
    if quote_row is None:
        # 从未取得行情：上游失败且无可用结果（spec §7）
        raise ProviderUnavailable(f"{symbol} 尚未取得任何行情，无法生成快照")

    quote = to_quote_dto(quote_row, now)

    relative_strength = await _relative_strength(session, symbol, quote.change_percent, now)
    latest_anomaly = await analyses_repo.latest_anomaly_id(session, symbol)
    latest_predictions = await predictions_repo.latest_ids_per_horizon(session, symbol)
    is_member = await instruments_repo.is_current_member(
        session, symbol, CSI300_CODE, to_shanghai(now).date()
    )

    return SnapshotDTO(
        symbol=instrument.symbol,
        name=instrument.name,
        quote=quote,
        relative_strength=relative_strength,
        latest_anomaly_analysis_id=latest_anomaly,
        latest_predictions=latest_predictions,
        is_current_universe_member=is_member,
    )


async def _relative_strength(
    session: AsyncSession, symbol: str, stock_change_percent: float, now: datetime
) -> RelativeStrengthDTO | None:
    """相对沪深300强弱。

    基准行情缺失时返回 ``None`` —— 前端展示"基准数据不可用"，
    而不是拿 0 冒充"大盘没动"（禁止假数据）。
    """
    benchmark_row = await quotes_repo.latest(session, CSI300_BENCHMARK_SYMBOL)
    if benchmark_row is None:
        return None

    benchmark_change = change_percent(
        float(benchmark_row.price), float(benchmark_row.previous_close), CSI300_BENCHMARK_SYMBOL
    )
    return RelativeStrengthDTO(
        benchmark=CSI300_BENCHMARK_SYMBOL,
        stock_change_percent=stock_change_percent,
        benchmark_change_percent=benchmark_change,
    )
