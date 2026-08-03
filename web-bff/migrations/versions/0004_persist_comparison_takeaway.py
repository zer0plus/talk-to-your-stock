"""Persist the Comparison Takeaway with each Comps Table.

Revision ID: 0004_comparison_takeaway
Revises: 0003_comps_audit_artifacts
Create Date: 2026-08-03
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0004_comparison_takeaway"
down_revision: str | None = "0003_comps_audit_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "comps_tables",
        sa.Column("comparison_takeaway", postgresql.JSONB(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("comps_tables", "comparison_takeaway")
