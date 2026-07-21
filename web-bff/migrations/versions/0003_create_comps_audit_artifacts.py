"""Create the Comps Service Trace and Source Snapshot schema.

Revision ID: 0003_comps_audit_artifacts
Revises: 0002_comps_run_schema
Create Date: 2026-07-21
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0003_comps_audit_artifacts"
down_revision: str | None = "0002_comps_run_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "comps_traces",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("formulas", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["comps_runs.id"],
            name="comps_traces_run_fk",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "comps_source_snapshots",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("raw_provider_evidence", postgresql.JSONB(), nullable=False),
        sa.Column("normalized_inputs", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["comps_runs.id"],
            name="comps_source_snapshots_run_fk",
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("comps_source_snapshots")
    op.drop_table("comps_traces")
