"""Create the Comps Service Run and Comps Table schema.

Revision ID: 0002_comps_run_schema
Revises: 0001_web_bff_schema
Create Date: 2026-07-17
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0002_comps_run_schema"
down_revision: str | None = "0001_web_bff_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "comps_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "invocation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            unique=True,
        ),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "trigger_message_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("target_ticker", sa.Text(), nullable=False),
        sa.Column("peer_tickers", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("warnings", postgresql.JSONB(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'succeeded', 'failed')",
            name="comps_runs_status",
        ),
        sa.CheckConstraint(
            "target_ticker ~ '^[A-Z.]{1,10}$'",
            name="comps_runs_target_ticker",
        ),
        sa.CheckConstraint(
            "cardinality(peer_tickers) >= 1",
            name="comps_runs_has_peers",
        ),
        sa.CheckConstraint(
            "char_length(currency) = 3",
            name="comps_runs_currency_length",
        ),
    )
    op.create_table(
        "comps_tables",
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("comps_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("target_ticker", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rows", postgresql.JSONB(), nullable=False),
        sa.Column("summary", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "target_ticker ~ '^[A-Z.]{1,10}$'",
            name="comps_tables_target_ticker",
        ),
        sa.CheckConstraint(
            "char_length(currency) = 3",
            name="comps_tables_currency_length",
        ),
    )
    op.create_index(
        "comps_runs_thread_created_idx",
        "comps_runs",
        ["thread_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )


def downgrade() -> None:
    op.drop_index("comps_runs_thread_created_idx", table_name="comps_runs")
    op.drop_table("comps_tables")
    op.drop_table("comps_runs")
