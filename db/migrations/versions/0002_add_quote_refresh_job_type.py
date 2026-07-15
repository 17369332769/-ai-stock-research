"""允许单只股票行情刷新作业

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REVERSIBLE = True


def upgrade() -> None:
    op.drop_constraint("ck_jobs_type", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_type",
        "jobs",
        "job_type IN ('instrument_backfill', 'analysis_refresh', 'quote_refresh', 'prediction', 'settlement')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE job_type = 'quote_refresh'")
    op.drop_constraint("ck_jobs_type", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_type",
        "jobs",
        "job_type IN ('instrument_backfill', 'analysis_refresh', 'prediction', 'settlement')",
    )
