"""Add persisted Comps Run ownership and recoverable failure envelopes.

Revision ID: 0004_comps_run_ownership
Revises: 0003_comps_audit_artifacts
Create Date: 2026-08-17
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0004_comps_run_ownership"
down_revision: str | None = "0003_comps_audit_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "comps_runs",
        sa.Column("calculation_owner_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "comps_runs",
        sa.Column("calculation_lease_expires_at", sa.DateTime(timezone=True)),
    )
    op.add_column("comps_runs", sa.Column("failure_http_status", sa.Integer()))
    op.add_column("comps_runs", sa.Column("failure_code", sa.Text()))
    op.add_column(
        "comps_runs",
        sa.Column("failure_details", postgresql.JSONB()),
    )
    op.add_column(
        "comps_runs",
        sa.Column(
            "validation_evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_check_constraint(
        "comps_runs_calculation_ownership",
        "comps_runs",
        """
        (status = 'running' and calculation_owner_id is not null
            and calculation_lease_expires_at is not null)
        or
        (status <> 'running' and calculation_owner_id is null
            and calculation_lease_expires_at is null)
        """,
    )
    op.create_check_constraint(
        "comps_runs_failure_envelope",
        "comps_runs",
        """
        (status = 'failed' and failure_http_status is not null
            and failure_code is not null and error_message is not null)
        or
        (status <> 'failed' and failure_http_status is null
            and failure_code is null and failure_details is null)
        """,
    )


def downgrade() -> None:
    op.drop_constraint(
        "comps_runs_failure_envelope", "comps_runs", type_="check"
    )
    op.drop_column("comps_runs", "validation_evidence")
    op.drop_constraint(
        "comps_runs_calculation_ownership", "comps_runs", type_="check"
    )
    op.drop_column("comps_runs", "failure_details")
    op.drop_column("comps_runs", "failure_code")
    op.drop_column("comps_runs", "failure_http_status")
    op.drop_column("comps_runs", "calculation_lease_expires_at")
    op.drop_column("comps_runs", "calculation_owner_id")
