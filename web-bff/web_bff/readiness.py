from __future__ import annotations

import logging
from collections.abc import Mapping

import httpx
from talk_to_your_stock_shared import (
    DependencyStatus,
    ReadinessCheck,
    ReadinessResponse,
    ReadinessState,
    ServiceName,
)
from talk_to_your_stock_shared.migrations import required_schema_revision
from talk_to_your_stock_shared.readiness import check_database
from web_bff.agent_client import AGENT_SERVICE_URL_VAR
from web_bff.comps_client import COMPS_SERVICE_URL_VAR


logger = logging.getLogger(__name__)


def check_agent_service(environ: Mapping[str, str]) -> ReadinessCheck:
    return _check_http_service(
        environ=environ,
        url_var=AGENT_SERVICE_URL_VAR,
        expected_service=ServiceName.AGENT_SERVICE,
        display_name="Agent Service",
    )


def check_comps_service(environ: Mapping[str, str]) -> ReadinessCheck:
    return _check_http_service(
        environ=environ,
        url_var=COMPS_SERVICE_URL_VAR,
        expected_service=ServiceName.COMPS_SERVICE,
        display_name="Comps Service",
    )


def _check_http_service(
    *,
    environ: Mapping[str, str],
    url_var: str,
    expected_service: ServiceName,
    display_name: str,
) -> ReadinessCheck:
    base_url = environ.get(url_var, "").strip().rstrip("/")
    if not base_url:
        return _failed_service_check(display_name)

    try:
        response = httpx.get(f"{base_url}/v1/ready", timeout=2)
        response.raise_for_status()
        readiness = ReadinessResponse.model_validate(response.json())
    except (httpx.HTTPError, ValueError):
        logger.exception("%s readiness check failed.", display_name)
        return _failed_service_check(display_name)

    if readiness.service != expected_service:
        logger.error("%s readiness response identified another service.", display_name)
        return _failed_service_check(display_name)

    if readiness.status != ReadinessState.READY:
        logger.error("%s reported that it is not ready.", display_name)
        return _failed_service_check(display_name)

    return ReadinessCheck(status=DependencyStatus.OK)


def _failed_service_check(display_name: str) -> ReadinessCheck:
    return ReadinessCheck(
        status=DependencyStatus.FAIL,
        message=f"{display_name} readiness check failed.",
    )


def check_web_bff_database(environ: Mapping[str, str]) -> ReadinessCheck:
    try:
        revision = required_schema_revision()
    except Exception:  # pragma: no cover - exact Alembic errors vary.
        logger.exception("Web BFF migration configuration check failed.")
        return ReadinessCheck(
            status=DependencyStatus.FAIL,
            message="Web BFF migration configuration is invalid.",
        )
    return check_database(
        environ,
        required_schema_revision=revision,
    )
