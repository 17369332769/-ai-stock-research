"""初始 schema：spec §6 的 12 张表与 4 个索引

Revision ID: 0001
Revises:
Create Date: 2026-07-14

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 可逆性声明：可逆迁移允许 `alembic downgrade -1`（spec §19.2）
REVERSIBLE = True


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("symbol", sa.String(12), primary_key=True),
        sa.Column("exchange", sa.String(8), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("industry", sa.String(120)),
        sa.Column("listed_at", sa.Date()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("exchange IN ('SSE', 'SZSE')", name="ck_instruments_exchange"),
    )

    op.create_table(
        "universes",
        sa.Column("code", sa.String(16), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("benchmark_symbol", sa.String(12), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "universe_memberships",
        sa.Column("universe_code", sa.String(16), sa.ForeignKey("universes.code"), primary_key=True),
        sa.Column("symbol", sa.String(12), sa.ForeignKey("instruments.symbol"), primary_key=True),
        sa.Column("effective_from", sa.Date(), primary_key=True),
        sa.Column("effective_to", sa.Date()),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_universe_memberships_period",
        ),
    )

    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(12), sa.ForeignKey("instruments.symbol"), nullable=False),
        sa.Column(
            "universe_code",
            sa.String(16),
            sa.ForeignKey("universes.code"),
            nullable=False,
            server_default="CSI300",
        ),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("symbol", name="uq_watchlist_items_symbol"),
    )

    op.create_table(
        "quotes",
        sa.Column("symbol", sa.String(12), sa.ForeignKey("instruments.symbol"), primary_key=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("price", sa.Numeric(18, 4), nullable=False),
        sa.Column("previous_close", sa.Numeric(18, 4), nullable=False),
        sa.Column("open", sa.Numeric(18, 4)),
        sa.Column("high", sa.Numeric(18, 4)),
        sa.Column("low", sa.Numeric(18, 4)),
        sa.Column("volume", sa.Numeric(24, 4)),
        sa.Column("amount", sa.Numeric(24, 4)),
        sa.Column("volume_ratio", sa.Numeric(12, 4)),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
    )

    op.create_table(
        "bars",
        sa.Column("symbol", sa.String(12), sa.ForeignKey("instruments.symbol"), primary_key=True),
        sa.Column("timeframe", sa.String(8), primary_key=True),
        sa.Column("bar_time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("open", sa.Numeric(18, 4), nullable=False),
        sa.Column("high", sa.Numeric(18, 4), nullable=False),
        sa.Column("low", sa.Numeric(18, 4), nullable=False),
        sa.Column("close", sa.Numeric(18, 4), nullable=False),
        sa.Column("volume", sa.Numeric(24, 4), nullable=False),
        sa.Column("amount", sa.Numeric(24, 4)),
        sa.Column("adjustment", sa.String(8), nullable=False, server_default="qfq"),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("timeframe IN ('5m', '1d')", name="ck_bars_timeframe"),
    )

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("symbol", sa.String(12), sa.ForeignKey("instruments.symbol")),
        sa.Column("document_type", sa.String(16), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text()),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", postgresql.CHAR(64), nullable=False, unique=True),
        sa.CheckConstraint("document_type IN ('announcement', 'news')", name="ck_documents_type"),
    )

    op.create_table(
        "analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("symbol", sa.String(12), sa.ForeignKey("instruments.symbol"), nullable=False),
        sa.Column("analysis_type", sa.String(24), nullable=False),
        sa.Column("direction", sa.String(12)),
        sa.Column("horizon", sa.String(16)),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("model_provider", sa.String(80)),
        sa.Column("model_name", sa.String(120)),
        sa.Column("data_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "analysis_type IN ('document', 'anomaly', 'daily_brief')", name="ck_analyses_type"
        ),
        sa.CheckConstraint(
            "direction IS NULL OR direction IN ('positive', 'negative', 'neutral', 'unknown')",
            name="ck_analyses_direction",
        ),
        sa.CheckConstraint(
            "horizon IS NULL OR horizon IN ('intraday', 'short', 'medium', 'unknown')",
            name="ck_analyses_horizon",
        ),
        sa.CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="ck_analyses_confidence"),
        sa.CheckConstraint("jsonb_typeof(evidence) = 'array'", name="ck_analyses_evidence_array"),
    )

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_type", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(12), sa.ForeignKey("instruments.symbol")),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("completed_steps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_steps", sa.Integer(), nullable=False),
        sa.Column("current_step", sa.String(40)),
        sa.Column("warnings", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("error_code", sa.String(40)),
        sa.Column("error_message", sa.Text()),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "job_type IN ('instrument_backfill', 'analysis_refresh', 'prediction', 'settlement')",
            name="ck_jobs_type",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_jobs_status"
        ),
        sa.CheckConstraint("completed_steps >= 0", name="ck_jobs_completed_nonneg"),
        sa.CheckConstraint("total_steps > 0", name="ck_jobs_total_positive"),
        sa.CheckConstraint("completed_steps <= total_steps", name="ck_jobs_progress"),
        sa.CheckConstraint("jsonb_typeof(warnings) = 'array'", name="ck_jobs_warnings_array"),
    )

    op.create_table(
        "model_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("model_key", sa.String(40), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("target_horizon", sa.String(16), nullable=False),
        sa.Column("feature_schema", postgresql.JSONB(), nullable=False),
        sa.Column("train_start", sa.Date(), nullable=False),
        sa.Column("train_end", sa.Date(), nullable=False),
        sa.Column("validation_metrics", postgresql.JSONB(), nullable=False),
        sa.Column("artifact_uri", sa.Text(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "target_horizon IN ('today_close', 'next_5d')", name="ck_model_versions_horizon"
        ),
        sa.CheckConstraint(
            "status IN ('candidate', 'active', 'retired')", name="ck_model_versions_status"
        ),
        sa.UniqueConstraint("model_key", "version", name="uq_model_versions_key_version"),
    )

    op.create_table(
        "predictions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("symbol", sa.String(12), sa.ForeignKey("instruments.symbol"), nullable=False),
        sa.Column(
            "model_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("model_versions.id"),
            nullable=False,
        ),
        sa.Column("horizon", sa.String(16), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reference_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("probability_up", sa.Numeric(5, 4), nullable=False),
        sa.Column("expected_return", sa.Numeric(12, 8), nullable=False),
        sa.Column("lower_return", sa.Numeric(12, 8), nullable=False),
        sa.Column("upper_return", sa.Numeric(12, 8), nullable=False),
        sa.Column("confidence_label", sa.String(8), nullable=False),
        sa.Column("data_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("features_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("horizon IN ('today_close', 'next_5d')", name="ck_predictions_horizon"),
        sa.CheckConstraint("probability_up BETWEEN 0 AND 1", name="ck_predictions_probability"),
        sa.CheckConstraint(
            "confidence_label IN ('low', 'medium', 'high')", name="ck_predictions_confidence"
        ),
        sa.UniqueConstraint(
            "symbol", "model_version_id", "horizon", "as_of", name="uq_predictions_identity"
        ),
    )

    op.create_table(
        "prediction_outcomes",
        sa.Column(
            "prediction_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("predictions.id"), primary_key=True
        ),
        sa.Column("actual_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("actual_return", sa.Numeric(12, 8), nullable=False),
        sa.Column("direction_correct", sa.Boolean(), nullable=False),
        sa.Column("absolute_error", sa.Numeric(12, 8), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_index("bars_symbol_time_idx", "bars", ["symbol", "timeframe", sa.text("bar_time DESC")])
    op.create_index(
        "universe_membership_active_idx",
        "universe_memberships",
        ["universe_code", "effective_from", "effective_to", "symbol"],
    )
    op.create_index(
        "documents_symbol_published_idx", "documents", ["symbol", sa.text("published_at DESC")]
    )
    op.create_index(
        "predictions_symbol_asof_idx", "predictions", ["symbol", "horizon", sa.text("as_of DESC")]
    )

    # 预测账本不可篡改：核心字段一旦写入禁止 UPDATE（验收 §15.8）。
    # 应用层不提供 update 方法，数据库触发器兜底 —— 任何绕过 ORM 的直连修改也会被拒绝。
    op.execute(
        """
        CREATE FUNCTION forbid_prediction_core_update() RETURNS trigger AS $$
        BEGIN
          IF (NEW.symbol, NEW.model_version_id, NEW.horizon, NEW.as_of, NEW.target_at,
              NEW.reference_price, NEW.probability_up, NEW.expected_return,
              NEW.lower_return, NEW.upper_return, NEW.confidence_label,
              NEW.data_cutoff, NEW.features_snapshot)
             IS DISTINCT FROM
             (OLD.symbol, OLD.model_version_id, OLD.horizon, OLD.as_of, OLD.target_at,
              OLD.reference_price, OLD.probability_up, OLD.expected_return,
              OLD.lower_return, OLD.upper_return, OLD.confidence_label,
              OLD.data_cutoff, OLD.features_snapshot)
          THEN
            RAISE EXCEPTION '预测账本不可篡改：predictions 核心字段禁止 UPDATE（prediction_id=%）', OLD.id;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_predictions_immutable
        BEFORE UPDATE ON predictions
        FOR EACH ROW EXECUTE FUNCTION forbid_prediction_core_update();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_predictions_immutable ON predictions;")
    op.execute("DROP FUNCTION IF EXISTS forbid_prediction_core_update();")
    op.drop_index("predictions_symbol_asof_idx", table_name="predictions")
    op.drop_index("documents_symbol_published_idx", table_name="documents")
    op.drop_index("universe_membership_active_idx", table_name="universe_memberships")
    op.drop_index("bars_symbol_time_idx", table_name="bars")
    op.drop_table("prediction_outcomes")
    op.drop_table("predictions")
    op.drop_table("model_versions")
    op.drop_table("jobs")
    op.drop_table("analyses")
    op.drop_table("documents")
    op.drop_table("bars")
    op.drop_table("quotes")
    op.drop_table("watchlist_items")
    op.drop_table("universe_memberships")
    op.drop_table("universes")
    op.drop_table("instruments")
