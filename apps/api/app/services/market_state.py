"""市场时段 DTO。

实时行情和历史行情是两个独立数据域。这里仅描述交易时段，不选择或替换任何价格。
"""

from __future__ import annotations

from datetime import datetime

from apps.api.app.core.clock import to_shanghai
from apps.api.app.core.runtime import get_trading_calendar
from apps.api.app.core.trading_calendar import market_phase
from apps.api.app.schemas.quotes import MarketDTO


def current_market(now: datetime) -> MarketDTO:
    calendar = get_trading_calendar()
    local_day = to_shanghai(now).date()
    is_trading_day = calendar.is_trading_day(local_day)
    latest_trading_day = (
        local_day if is_trading_day else calendar.previous_trading_day(local_day)
    )
    return MarketDTO(
        phase=market_phase(now, calendar),
        is_trading_day=is_trading_day,
        latest_trading_day=latest_trading_day.isoformat(),
    )
