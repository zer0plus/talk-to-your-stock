"""Persist the error returned by a failed Comps generation.

Revision ID: 0005_generation_failure
Revises: 0004_comparison_takeaway
Create Date: 2026-08-11
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0005_generation_failure"
down_revision: str | None = "0004_comparison_takeaway"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "comps_runs",
        sa.Column("generation_failure", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("comps_runs", "generation_failure")
