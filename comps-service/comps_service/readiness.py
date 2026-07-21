from __future__ import annotations

import logging
from collections.abc import Mapping

from talk_to_your_stock_shared import DependencyStatus, ReadinessCheck
from talk_to_your_stock_shared.migrations import required_schema_revision
from talk_to_your_stock_shared.readiness import check_database


logger = logging.getLogger(__name__)


def check_comps_database(environ: Mapping[str, str]) -> ReadinessCheck:
    try:
        revision = required_schema_revision()
    except Exception:  # pragma: no cover - exact Alembic errors vary.
        logger.exception("Comps Service migration configuration check failed.")
        return ReadinessCheck(
            status=DependencyStatus.FAIL,
            message="Comps Service migration configuration is invalid.",
        )
    return check_database(environ, required_schema_revision=revision)


def check_run_data_source() -> ReadinessCheck:
    return ReadinessCheck(
        status=DependencyStatus.OK,
    )
