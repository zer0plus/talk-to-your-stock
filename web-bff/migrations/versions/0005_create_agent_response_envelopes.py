"""Create durable terminal Agent response envelopes.

Revision ID: 0005_agent_response_envelopes
Revises: 0004_comps_run_ownership
Create Date: 2026-08-18
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0005_agent_response_envelopes"
down_revision: str | None = "0004_comps_run_ownership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_response_envelopes",
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_content", sa.Text(), nullable=False),
        sa.Column("response_envelope", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "char_length(request_content) >= 1",
            name="agent_response_envelopes_request_content_nonempty",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(response_envelope) = 'object'",
            name="agent_response_envelopes_response_object",
        ),
    )


def downgrade() -> None:
    op.drop_table("agent_response_envelopes")
