"""QuoteDTO 与股票快照（spec §7.2）。

新鲜度只在 API 层判定，前端不得自行计算（spec §5.1）：
``age > settings.quote_stale_seconds`` ⇒ ``freshness=stale`` 且附 ``age_seconds``。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from apps.api.app.core.enums import Freshness, QuoteAgeStatus
from apps.api.app.core.trading_calendar import MarketPhase
from apps.api.app.schemas.common import BaseDTO
from apps.api.app.schemas.jobs import JobDTO


class MarketDTO(BaseDTO):
    """由 API 判定的市场时段；前端不读取本机时钟自行推断。"""

    phase: MarketPhase
    is_trading_day: bool
    latest_trading_day: str


class QuoteDTO(BaseDTO):
    """近实时行情。

    spec §7.2 的示例只列出 price/change_percent/observed_at/source/freshness；
    这里额外带上 F2 明确要求的**成交量与量比**，以及 stale 时必须出现的 ``age_seconds``。
    """

    symbol: str
    price: float
    previous_close: float
    change_percent: float = Field(description="price / previous_close - 1")
    change_amount: float = Field(description="price - previous_close")
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None
    amount: float | None = None
    volume_ratio: float | None = Field(default=None, description="量比（F2）")
    turnover_rate: float | None = Field(default=None, description="换手率（%）")
    bid1: float | None = Field(default=None, description="买一价；来源未提供时为 null")
    ask1: float | None = Field(default=None, description="卖一价；来源未提供时为 null")
    observed_at: datetime = Field(description="数据源观测时间（Asia/Shanghai）")
    market_time: datetime | None = Field(
        default=None,
        description="上游明确提供的行情时间；当前来源未提供时为 null",
    )
    fetched_at: datetime = Field(description="本系统取得该行情的时间（Asia/Shanghai）")
    source: str
    source_url: str | None = None
    freshness: Freshness
    age_status: QuoteAgeStatus = Field(description="latest/delayed/stale 分级状态")
    data_age_seconds: int = Field(description="距系统成功获取的秒数")
    age_seconds: int | None = Field(
        default=None,
        description="兼容字段：仅 freshness=stale 时出现",
    )


class RelativeStrengthDTO(BaseDTO):
    """相对大盘强弱（spec §7.2）。基准行情缺失时整个对象为 null，不编造 0。"""

    benchmark: str
    stock_change_percent: float
    benchmark_change_percent: float


class SnapshotDTO(BaseDTO):
    """``GET /stocks/{symbol}/snapshot``（spec §7.2 逐字段对齐）。"""

    symbol: str
    name: str
    quote: QuoteDTO | None = Field(
        default=None,
        description="实时行情尚未取得时为 null；历史行情不填入此字段",
    )
    market: MarketDTO
    relative_strength: RelativeStrengthDTO | None = None
    latest_anomaly_analysis_id: uuid.UUID | None = None
    latest_predictions: list[uuid.UUID] = Field(
        default_factory=list, description="各 horizon 的最新预测 id；无预测时为空数组"
    )
    is_current_universe_member: bool = Field(
        description="是否属于查询日沪深300；false 也可能只是普通范围外关注"
    )
    pool_source: Literal["csi300", "extra"] | None = Field(
        default=None,
        description="当前研究池来源；未在研究池时为 null",
    )
    is_universe_exit: bool = Field(
        default=False,
        description="仅曾属于沪深300、当前已调出且不是额外关注时为 true",
    )
    backfill_job: JobDTO | None = Field(
        default=None,
        description="可恢复的排队、运行或失败回补作业",
    )
    analysis_job: JobDTO | None = Field(
        default=None,
        description="可恢复的排队、运行或失败分析刷新作业",
    )
    missing: list[str] = Field(default_factory=list)


class SnapshotResponse(SnapshotDTO):
    """spec §7.2 的响应是**裸对象**（不带 data 包裹）；§7 又要求所有响应带 request_id，
    因此在裸对象上追加 request_id 字段（同时也在 ``X-Request-Id`` 响应头返回）。"""

    request_id: str
