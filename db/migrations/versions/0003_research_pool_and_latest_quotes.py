"""拆分沪深300研究池并新增最新报价表。

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REVERSIBLE = True


def upgrade() -> None:
    op.add_column("quotes", sa.Column("turnover_rate", sa.Numeric(12, 4)))
    op.create_table(
        "latest_quotes",
        sa.Column("symbol", sa.String(12), sa.ForeignKey("instruments.symbol"), primary_key=True),
        sa.Column("price", sa.Numeric(18, 4), nullable=False),
        sa.Column("previous_close", sa.Numeric(18, 4), nullable=False),
        sa.Column("open", sa.Numeric(18, 4)),
        sa.Column("high", sa.Numeric(18, 4)),
        sa.Column("low", sa.Numeric(18, 4)),
        sa.Column("volume", sa.Numeric(24, 4)),
        sa.Column("amount", sa.Numeric(24, 4)),
        sa.Column("volume_ratio", sa.Numeric(12, 4)),
        sa.Column("turnover_rate", sa.Numeric(12, 4)),
        sa.Column("bid1", sa.Numeric(18, 4)),
        sa.Column("ask1", sa.Numeric(18, 4)),
        sa.Column("market_time", sa.DateTime(timezone=True)),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
    )

    # 先把已有快照中的最后一条物化为 latest_quotes，历史 quotes 保持不动。
    op.execute(
        """
        INSERT INTO latest_quotes (
            symbol, price, previous_close, open, high, low, volume, amount,
            volume_ratio, turnover_rate, bid1, ask1, market_time, fetched_at,
            source, source_url, raw_payload
        )
        SELECT DISTINCT ON (symbol)
            symbol, price, previous_close, open, high, low, volume, amount,
            volume_ratio, turnover_rate, NULL, NULL, NULL, observed_at,
            source, source_url, raw_payload
        FROM quotes
        ORDER BY symbol, observed_at DESC
        ON CONFLICT (symbol) DO NOTHING
        """
    )

    # 当前沪深300由 universe_memberships 自动提供，不再伪装成用户自选。
    op.execute(
        """
        DELETE FROM watchlist_items w
        USING universe_memberships m
        WHERE w.symbol = m.symbol
          AND m.universe_code = 'CSI300'
          AND m.effective_to IS NULL
        """
    )


def downgrade() -> None:
    # 回滚时恢复旧产品语义：当前成分重新进入 watchlist_items。历史/报价不删除。
    op.execute(
        """
        INSERT INTO watchlist_items (symbol, universe_code, display_order)
        SELECT symbol, universe_code,
               (ROW_NUMBER() OVER (ORDER BY symbol) - 1)::integer
        FROM universe_memberships
        WHERE universe_code = 'CSI300' AND effective_to IS NULL
        ON CONFLICT (symbol) DO NOTHING
        """
    )
    op.drop_table("latest_quotes")
    op.drop_column("quotes", "turnover_rate")
