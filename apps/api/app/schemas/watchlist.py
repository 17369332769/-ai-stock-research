"""WatchlistItemDTO 与自选股请求体（spec §7.1）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from apps.api.app.schemas.common import BaseDTO
from apps.api.app.schemas.jobs import JobDTO
from apps.api.app.schemas.quotes import MarketDTO, QuoteDTO

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
    created_at: datetime | None = None
    pool_source: Literal["csi300", "extra"] = "extra"
    can_remove: bool = True
    model_scope_warning: str | None = None
    analysis_status: Literal["waiting", "queued", "analyzing", "analyzed", "failed"] = "waiting"
    analysis_updated_at: datetime | None = None
    industry: str | None = None
    has_anomaly: bool = False
    anomaly_strength: float | None = Field(default=None, ge=0, le=1)
    has_documents: bool = False
    document_count: int = Field(default=0, ge=0)
    latest_document_at: datetime | None = None
    has_prediction: bool = False
    prediction_count: int = Field(default=0, ge=0)
    is_current_universe_member: bool = Field(
        description="是否属于当前沪深300；额外关注通常为 false，不代表曾被调出"
    )
    quote: QuoteDTO | None = Field(
        default=None, description="最新行情；从未取得行情时为 null（不编造默认值）"
    )
    market: MarketDTO = Field(description="API 判定的当前市场时段")
    backfill_job: JobDTO | None = None
    analysis_job: JobDTO | None = None
    missing: list[str] = Field(default_factory=list)


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
