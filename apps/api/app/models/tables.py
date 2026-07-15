"""ORM 表定义 —— 与 spec §6 的 SQL 契约逐字段对应。

约束（CHECK / UNIQUE / FK）在这里声明，Alembic 迁移由本文件生成，
因此"数据库里的约束"与"代码里的约束"只有一处真相。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CHAR, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.app.models.base import Base

# ── 取值域：与 apps/api/app/core/enums.py 保持一致 ──────────────────────────
EXCHANGES = ("SSE", "SZSE")
TIMEFRAMES = ("5m", "1d")
DOCUMENT_TYPES = ("announcement", "news")
ANALYSIS_TYPES = ("document", "anomaly", "daily_brief")
DIRECTIONS = ("positive", "negative", "neutral", "unknown")
EVENT_HORIZONS = ("intraday", "short", "medium", "unknown")
PREDICTION_HORIZONS = ("today_close", "next_5d")
CONFIDENCE_LABELS = ("low", "medium", "high")
MODEL_STATUSES = ("candidate", "active", "retired")
JOB_TYPES = ("instrument_backfill", "analysis_refresh", "quote_refresh", "prediction", "settlement")
JOB_STATUSES = ("queued", "running", "succeeded", "failed")


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


class Instrument(Base):
    __tablename__ = "instruments"

    symbol: Mapped[str] = mapped_column(String(12), primary_key=True)
    exchange: Mapped[str] = mapped_column(String(8), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(120))
    listed_at: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (CheckConstraint(_in_clause("exchange", EXCHANGES), name="ck_instruments_exchange"),)


class Universe(Base):
    __tablename__ = "universes"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    benchmark_symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UniverseMembership(Base):
    """历史成分有效期。训练必须按当时有效成分取样，禁止用当前 300 只回填历史（spec §9.3.8）。"""

    __tablename__ = "universe_memberships"

    universe_code: Mapped[str] = mapped_column(
        String(16), ForeignKey("universes.code"), primary_key=True
    )
    symbol: Mapped[str] = mapped_column(String(12), ForeignKey("instruments.symbol"), primary_key=True)
    effective_from: Mapped[date] = mapped_column(Date, primary_key=True)
    effective_to: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_universe_memberships_period",
        ),
        Index(
            "universe_membership_active_idx",
            "universe_code",
            "effective_from",
            "effective_to",
            "symbol",
        ),
    )


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(12), ForeignKey("instruments.symbol"), nullable=False)
    universe_code: Mapped[str] = mapped_column(
        String(16), ForeignKey("universes.code"), nullable=False, default="CSI300", server_default="CSI300"
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("symbol", name="uq_watchlist_items_symbol"),)


class Quote(Base):
    """报价快照。没有 published_at —— 它不适用于报价（spec §4.2）。"""

    __tablename__ = "quotes"

    symbol: Mapped[str] = mapped_column(String(12), ForeignKey("instruments.symbol"), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    previous_close: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    open: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    high: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    low: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    volume_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class Bar(Base):
    __tablename__ = "bars"

    symbol: Mapped[str] = mapped_column(String(12), ForeignKey("instruments.symbol"), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    bar_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    adjustment: Mapped[str] = mapped_column(String(8), nullable=False, default="qfq", server_default="qfq")
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(_in_clause("timeframe", TIMEFRAMES), name="ck_bars_timeframe"),
        Index("bars_symbol_time_idx", "symbol", "timeframe", "bar_time", postgresql_using="btree"),
    )


class Document(Base):
    """公告与新闻。``content_hash`` 唯一 —— 同内容不得重复展示（验收 §15.4）。"""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    symbol: Mapped[str | None] = mapped_column(String(12), ForeignKey("instruments.symbol"))
    document_type: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body_text: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)

    __table_args__ = (
        CheckConstraint(_in_clause("document_type", DOCUMENT_TYPES), name="ck_documents_type"),
        Index("documents_symbol_published_idx", "symbol", "published_at", postgresql_using="btree"),
    )


class Analysis(Base):
    """AI 结论。``evidence`` 必须是 JSON 数组；无证据时 direction=unknown（spec §11.3）。"""

    __tablename__ = "analyses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(12), ForeignKey("instruments.symbol"), nullable=False)
    analysis_type: Mapped[str] = mapped_column(String(24), nullable=False)
    direction: Mapped[str | None] = mapped_column(String(12))
    horizon: Mapped[str | None] = mapped_column(String(16))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    model_provider: Mapped[str | None] = mapped_column(String(80))
    model_name: Mapped[str | None] = mapped_column(String(120))
    data_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(_in_clause("analysis_type", ANALYSIS_TYPES), name="ck_analyses_type"),
        CheckConstraint(
            f"direction IS NULL OR {_in_clause('direction', DIRECTIONS)}", name="ck_analyses_direction"
        ),
        CheckConstraint(
            f"horizon IS NULL OR {_in_clause('horizon', EVENT_HORIZONS)}", name="ck_analyses_horizon"
        ),
        CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="ck_analyses_confidence"),
        CheckConstraint("jsonb_typeof(evidence) = 'array'", name="ck_analyses_evidence_array"),
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(12), ForeignKey("instruments.symbol"))
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    completed_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    total_steps: Mapped[int] = mapped_column(Integer, nullable=False)
    current_step: Mapped[str | None] = mapped_column(String(40))
    warnings: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    error_code: Mapped[str | None] = mapped_column(String(40))
    error_message: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(_in_clause("job_type", JOB_TYPES), name="ck_jobs_type"),
        CheckConstraint(_in_clause("status", JOB_STATUSES), name="ck_jobs_status"),
        CheckConstraint("completed_steps >= 0", name="ck_jobs_completed_nonneg"),
        CheckConstraint("total_steps > 0", name="ck_jobs_total_positive"),
        CheckConstraint("completed_steps <= total_steps", name="ck_jobs_progress"),
        CheckConstraint("jsonb_typeof(warnings) = 'array'", name="ck_jobs_warnings_array"),
    )


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    model_key: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    target_horizon: Mapped[str] = mapped_column(String(16), nullable=False)
    feature_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    train_start: Mapped[date] = mapped_column(Date, nullable=False)
    train_end: Mapped[date] = mapped_column(Date, nullable=False)
    validation_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    artifact_uri: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(_in_clause("target_horizon", PREDICTION_HORIZONS), name="ck_model_versions_horizon"),
        CheckConstraint(_in_clause("status", MODEL_STATUSES), name="ck_model_versions_status"),
        UniqueConstraint("model_key", "version", name="uq_model_versions_key_version"),
    )


class Prediction(Base):
    """预测账本。核心字段创建后不可更新（验收 §15.8）—— 由 repository 层强制，不提供 update 方法。"""

    __tablename__ = "predictions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(12), ForeignKey("instruments.symbol"), nullable=False)
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_versions.id"), nullable=False
    )
    horizon: Mapped[str] = mapped_column(String(16), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    target_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reference_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    probability_up: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    expected_return: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    lower_return: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    upper_return: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    confidence_label: Mapped[str] = mapped_column(String(8), nullable=False)
    data_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    features_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(_in_clause("horizon", PREDICTION_HORIZONS), name="ck_predictions_horizon"),
        CheckConstraint("probability_up BETWEEN 0 AND 1", name="ck_predictions_probability"),
        CheckConstraint(_in_clause("confidence_label", CONFIDENCE_LABELS), name="ck_predictions_confidence"),
        UniqueConstraint(
            "symbol", "model_version_id", "horizon", "as_of", name="uq_predictions_identity"
        ),
        Index("predictions_symbol_asof_idx", "symbol", "horizon", "as_of", postgresql_using="btree"),
    )


class PredictionOutcome(Base):
    """结算记录。只能新增，不能改预测本身（spec §3.4）。"""

    __tablename__ = "prediction_outcomes"

    prediction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("predictions.id"), primary_key=True
    )
    actual_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    actual_return: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    direction_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    absolute_error: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
