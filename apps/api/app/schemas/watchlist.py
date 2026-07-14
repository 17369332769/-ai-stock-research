"""WatchlistItemDTO 与自选股请求体（spec §7.1）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from apps.api.app.schemas.common import BaseDTO
from apps.api.app.schemas.jobs import JobDTO
from apps.api.app.schemas.quotes import QuoteDTO

SYMBOL_PATTERN = r"^\d{6}$"


class WatchlistItemDTO(BaseDTO):
    """自选股条目。

    spec §7.1 的 POST 示例只回 ``{"symbol", "display_order"}``；
    这里是它的超集：首页自选股表格需要 name / 行情 / 新鲜度，而
    **前端不得自行计算数据新鲜度**（spec §5.1），所以必须由 API 提供。
    """

    symbol: str
    name: str
    display_order: int
    created_at: datetime
    is_current_universe_member: bool = Field(
        description="false ⇒ 「已调出沪深300」：保留展示，停止新预测（spec §3.1）"
    )
    quote: QuoteDTO | None = Field(
        default=None, description="最新行情；从未取得行情时为 null（不编造默认值）"
    )


class AddWatchlistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(pattern=SYMBOL_PATTERN, description="6 位 A 股代码，例如 600519")


class ReorderWatchlistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbols: list[str] = Field(
        min_length=1,
        description="必须是当前自选股的一个全排列；缺项/多项/重复一律 400 INVALID_ARGUMENT",
    )


class WatchlistAddedDTO(BaseDTO):
    """``POST /watchlist`` 的 data 体（spec §7.1）。"""

    watchlist_item: WatchlistItemDTO
    backfill_job: JobDTO | None = Field(
        default=None,
        description="首次添加时返回回补作业（HTTP 202）；已完成过回补则为 null（HTTP 201）",
    )
