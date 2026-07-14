"""Create the Web BFF product-state schema.

Revision ID: 0001_web_bff_schema
Revises:
Create Date: 2026-07-14
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0001_web_bff_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_bff_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "web_bff_threads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("web_bff_users.id"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "char_length(title) between 1 and 120",
            name="web_bff_threads_title_length",
        ),
        sa.CheckConstraint(
            "message_count >= 0",
            name="web_bff_threads_message_count_nonnegative",
        ),
    )
    op.create_table(
        "web_bff_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("web_bff_threads.id"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role in ('user', 'assistant', 'tool', 'system')",
            name="web_bff_messages_role",
        ),
        sa.CheckConstraint(
            "char_length(content) >= 1",
            name="web_bff_messages_content_nonempty",
        ),
        sa.CheckConstraint(
            "status in ('complete', 'streaming', 'failed')",
            name="web_bff_messages_status",
        ),
    )
    op.create_index(
        "web_bff_threads_user_updated_idx",
        "web_bff_threads",
        ["user_id", sa.text("updated_at DESC")],
    )
    op.create_index(
        "web_bff_messages_thread_created_idx",
        "web_bff_messages",
        ["thread_id", sa.text("created_at ASC")],
    )


def downgrade() -> None:
    op.drop_index(
        "web_bff_messages_thread_created_idx",
        table_name="web_bff_messages",
    )
    op.drop_index(
        "web_bff_threads_user_updated_idx",
        table_name="web_bff_threads",
    )
    op.drop_table("web_bff_messages")
    op.drop_table("web_bff_threads")
    op.drop_table("web_bff_users")
