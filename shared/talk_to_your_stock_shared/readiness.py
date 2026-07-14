from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Callable
from urllib.parse import urlparse
from uuid import UUID

from talk_to_your_stock_shared.enums import (
    DependencyStatus,
    ReadinessState,
    ServiceName,
)
from talk_to_your_stock_shared.schemas import ReadinessCheck, ReadinessResponse
from talk_to_your_stock_shared.time import utc_now

ENVIRONMENT_VAR = "TALK_TO_YOUR_STOCK_ENV"
DATABASE_URL_VAR = "DATABASE_URL"
LOCAL_ENVIRONMENTS = {"local", "development", "test"}
PRODUCTION_ENVIRONMENT = "production"
VALID_ENVIRONMENTS = LOCAL_ENVIRONMENTS | {PRODUCTION_ENVIRONMENT}
logger = logging.getLogger(__name__)


DatabaseChecker = Callable[[Mapping[str, str]], ReadinessCheck]


def build_readiness_response(
    *,
    service: ServiceName,
    environ: Mapping[str, str] | None = None,
    database_checker: DatabaseChecker | None = None,
    additional_checkers: Mapping[str, DatabaseChecker] | None = None,
) -> ReadinessResponse:
    env = os.environ if environ is None else environ
    checker = check_database if database_checker is None else database_checker

    checks = {
        "configuration": check_configuration(service=service, environ=env),
        "database": checker(env),
    }
    for name, dependency_checker in (additional_checkers or {}).items():
        checks[name] = dependency_checker(env)
    status = (
        ReadinessState.READY
        if all(check.status != DependencyStatus.FAIL for check in checks.values())
        else ReadinessState.NOT_READY
    )
    return ReadinessResponse(
        status=status,
        service=service,
        checks=checks,
        time=utc_now(),
    )


def readiness_http_status(readiness: ReadinessResponse) -> int:
    return 200 if readiness.status == ReadinessState.READY else 503


def check_configuration(
    *,
    service: ServiceName,
    environ: Mapping[str, str],
) -> ReadinessCheck:
    environment = environ.get(ENVIRONMENT_VAR, "").strip().lower()
    if environment not in VALID_ENVIRONMENTS:
        return ReadinessCheck(
            status=DependencyStatus.FAIL,
            message=(
                f"{ENVIRONMENT_VAR} must be one of "
                f"{', '.join(sorted(VALID_ENVIRONMENTS))}."
            ),
        )

    missing = [
        name
        for name in _required_config_names(service=service, environment=environment)
        if not environ.get(name, "").strip()
    ]
    if missing:
        return ReadinessCheck(
            status=DependencyStatus.FAIL,
            message=f"Missing required configuration: {', '.join(missing)}.",
        )

    if environment == PRODUCTION_ENVIRONMENT and _has_dev_auth_config(environ):
        return ReadinessCheck(
            status=DependencyStatus.FAIL,
            message="DEV_AUTH_* configuration is not allowed in production mode.",
        )

    if service == ServiceName.WEB_BFF and environment in LOCAL_ENVIRONMENTS:
        try:
            UUID(environ["DEV_AUTH_USER_ID"].strip())
        except ValueError:
            return ReadinessCheck(
                status=DependencyStatus.FAIL,
                message="DEV_AUTH_USER_ID must be a valid UUID.",
            )

    if service == ServiceName.WEB_BFF and environment == PRODUCTION_ENVIRONMENT:
        return ReadinessCheck(
            status=DependencyStatus.FAIL,
            message="Managed JWT verification is not implemented.",
        )

    return ReadinessCheck(status=DependencyStatus.OK)


def check_database(
    environ: Mapping[str, str],
    *,
    required_schema_revision: str | None = None,
) -> ReadinessCheck:
    database_url = environ.get(DATABASE_URL_VAR, "").strip()
    if not database_url:
        return ReadinessCheck(
            status=DependencyStatus.FAIL,
            message=f"Missing required configuration: {DATABASE_URL_VAR}.",
        )

    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgresql", "postgres"}:
        return ReadinessCheck(
            status=DependencyStatus.FAIL,
            message=f"{DATABASE_URL_VAR} must use a PostgreSQL URL.",
        )

    try:
        import psycopg
    except ModuleNotFoundError:
        return ReadinessCheck(
            status=DependencyStatus.FAIL,
            message="PostgreSQL readiness check requires psycopg to be installed.",
        )

    try:
        with psycopg.connect(database_url, connect_timeout=2) as connection:
            with connection.cursor() as cursor:
                cursor.execute("select 1")
                cursor.fetchone()
                if required_schema_revision is not None:
                    try:
                        cursor.execute("select version_num from alembic_version")
                        revision = cursor.fetchone()
                    except Exception:  # pragma: no cover - exact driver errors vary.
                        logger.exception("PostgreSQL migration readiness check failed.")
                        return ReadinessCheck(
                            status=DependencyStatus.FAIL,
                            message="PostgreSQL database migrations are not applied.",
                        )
                    if revision != (required_schema_revision,):
                        return ReadinessCheck(
                            status=DependencyStatus.FAIL,
                            message=(
                                "PostgreSQL database migration is not at the required "
                                f"revision {required_schema_revision}."
                            ),
                        )
    except Exception as exc:  # pragma: no cover - exact driver errors vary.
        logger.exception("PostgreSQL readiness check failed.")
        message = "PostgreSQL readiness check failed."
        if environ.get(ENVIRONMENT_VAR, "").strip().lower() in LOCAL_ENVIRONMENTS:
            message = f"{message} {exc}"
        return ReadinessCheck(
            status=DependencyStatus.FAIL,
            message=message,
        )

    return ReadinessCheck(status=DependencyStatus.OK)


def _required_config_names(
    *,
    service: ServiceName,
    environment: str,
) -> list[str]:
    required = [ENVIRONMENT_VAR]

    if service == ServiceName.WEB_BFF:
        if environment == PRODUCTION_ENVIRONMENT:
            required.extend(
                [
                    "MANAGED_AUTH_JWKS_URL",
                    "MANAGED_AUTH_ISSUER",
                    "MANAGED_AUTH_AUDIENCE",
                ]
            )
        else:
            required.extend(["DEV_AUTH_USER_ID", "DEV_AUTH_EMAIL"])
        required.extend(["AGENT_SERVICE_URL"])
    elif service == ServiceName.AGENT_SERVICE:
        if environment == PRODUCTION_ENVIRONMENT:
            required.extend(
                [
                    "GOOGLE_ADK_APP_NAME",
                    "GOOGLE_API_KEY",
                    "COMPS_SERVICE_URL",
                    "COMPS_SERVICE_INTERNAL_TOKEN",
                ]
            )
    elif service == ServiceName.COMPS_SERVICE:
        required.extend(["ALPHA_VANTAGE_API_KEY", "COMPS_SERVICE_INTERNAL_TOKEN"])

    return required


def _has_dev_auth_config(environ: Mapping[str, str]) -> bool:
    return any(
        environ.get(name, "").strip()
        for name in ("DEV_AUTH_USER_ID", "DEV_AUTH_EMAIL")
    )
