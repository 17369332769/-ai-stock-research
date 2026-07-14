"""InstrumentDTO（spec §7 必需 DTO 之一）。"""

from __future__ import annotations

from datetime import date

from pydantic import Field

from apps.api.app.core.enums import Exchange
from apps.api.app.models.tables import Instrument
from apps.api.app.schemas.common import BaseDTO


class InstrumentDTO(BaseDTO):
    symbol: str = Field(description="6 位 A 股代码")
    name: str
    exchange: Exchange
    industry: str | None = None
    listed_at: date | None = None
    active: bool = Field(description="是否仍在上市交易")
    is_current_universe_member: bool = Field(
        description=(
            "是否为查询日沪深300当前成分股。false 表示已调出：历史页面与既有预测保留，"
            "但停止生成新预测且禁止重新添加（spec §3.1）"
        )
    )

    @classmethod
    def from_row(cls, row: Instrument, *, is_current_universe_member: bool) -> InstrumentDTO:
        return cls(
            symbol=row.symbol,
            name=row.name,
            exchange=Exchange(row.exchange),
            industry=row.industry,
            listed_at=row.listed_at,
            active=row.active,
            is_current_universe_member=is_current_universe_member,
        )
