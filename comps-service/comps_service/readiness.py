from __future__ import annotations

import logging
from collections.abc import Mapping

from talk_to_your_stock_shared import DependencyStatus, ReadinessCheck
from talk_to_your_stock_shared.migrations import required_schema_revision
from talk_to_your_stock_shared.readiness import check_database

from .provider_config import InvalidProviderConfiguration, seconds_setting
from .tool_validation import (
    ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS_VAR,
    ALPHA_VANTAGE_TIMEOUT_SECONDS_VAR,
    DEFAULT_ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS,
    DEFAULT_ALPHA_VANTAGE_TIMEOUT_SECONDS,
)


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


def check_run_data_source(environ: Mapping[str, str]) -> ReadinessCheck:
    try:
        seconds_setting(
            environ,
            name=ALPHA_VANTAGE_TIMEOUT_SECONDS_VAR,
            default=DEFAULT_ALPHA_VANTAGE_TIMEOUT_SECONDS,
        )
        seconds_setting(
            environ,
            name=ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS_VAR,
            default=DEFAULT_ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS,
        )
    except InvalidProviderConfiguration as exc:
        return ReadinessCheck(
            status=DependencyStatus.FAIL,
            message=str(exc),
        )
    return ReadinessCheck(
        status=DependencyStatus.OK,
    )
