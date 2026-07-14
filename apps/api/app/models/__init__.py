"""ORM 模型。Alembic 从这里发现全部表。"""

from apps.api.app.models.base import Base
from apps.api.app.models.tables import (
    Analysis,
    Bar,
    Document,
    Instrument,
    Job,
    ModelVersion,
    Prediction,
    PredictionOutcome,
    Quote,
    Universe,
    UniverseMembership,
    WatchlistItem,
)

__all__ = [
    "Analysis",
    "Bar",
    "Base",
    "Document",
    "Instrument",
    "Job",
    "ModelVersion",
    "Prediction",
    "PredictionOutcome",
    "Quote",
    "Universe",
    "UniverseMembership",
    "WatchlistItem",
]
