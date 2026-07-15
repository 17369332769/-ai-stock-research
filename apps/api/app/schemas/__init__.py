"""对外 DTO。Pydantic/OpenAPI 是 DTO 的唯一真相；ORM 模型不得直接序列化给客户端（spec §7）。"""

from __future__ import annotations

from apps.api.app.schemas.analogs import MIN_ANALOG_CANDIDATES, AnalogDTO
from apps.api.app.schemas.analyses import AnalysisDTO, EvidenceDTO
from apps.api.app.schemas.bars import BarDTO, BarRangeSummaryDTO, BarsMetaDTO, BarsResponse
from apps.api.app.schemas.common import (
    BaseDTO,
    ErrorBody,
    ErrorResponse,
    ItemResponse,
    ListResponse,
    PageInfo,
)
from apps.api.app.schemas.documents import DocumentDTO
from apps.api.app.schemas.instruments import InstrumentDTO
from apps.api.app.schemas.jobs import JobDTO, QuoteRefreshDTO
from apps.api.app.schemas.predictions import (
    PredictionDTO,
    PredictionModelRefDTO,
    PredictionResponse,
    ReturnIntervalDTO,
    ScorecardDTO,
)
from apps.api.app.schemas.quotes import (
    QuoteDTO,
    RelativeStrengthDTO,
    SnapshotDTO,
    SnapshotResponse,
)
from apps.api.app.schemas.watchlist import (
    AddWatchlistRequest,
    ReorderWatchlistRequest,
    WatchlistAddedDTO,
    WatchlistItemDTO,
)

__all__ = [
    "MIN_ANALOG_CANDIDATES",
    "AddWatchlistRequest",
    "AnalogDTO",
    "AnalysisDTO",
    "BarDTO",
    "BarRangeSummaryDTO",
    "BarsMetaDTO",
    "BarsResponse",
    "BaseDTO",
    "DocumentDTO",
    "ErrorBody",
    "ErrorResponse",
    "EvidenceDTO",
    "InstrumentDTO",
    "ItemResponse",
    "JobDTO",
    "ListResponse",
    "PageInfo",
    "PredictionDTO",
    "PredictionModelRefDTO",
    "PredictionResponse",
    "QuoteDTO",
    "QuoteRefreshDTO",
    "RelativeStrengthDTO",
    "ReorderWatchlistRequest",
    "ReturnIntervalDTO",
    "ScorecardDTO",
    "SnapshotDTO",
    "SnapshotResponse",
    "WatchlistAddedDTO",
    "WatchlistItemDTO",
]
