from __future__ import annotations

import logging
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

import httpx
from alembic.config import Config
from alembic.script import ScriptDirectory
from talk_to_your_stock_shared import DependencyStatus, ReadinessCheck
from talk_to_your_stock_shared.readiness import check_database
from web_bff.agent_client import AGENT_SERVICE_URL_VAR


logger = logging.getLogger(__name__)


def check_agent_service(environ: Mapping[str, str]) -> ReadinessCheck:
    base_url = environ.get(AGENT_SERVICE_URL_VAR, "").strip().rstrip("/")
    if not base_url:
        return ReadinessCheck(
            status=DependencyStatus.FAIL,
            message="Agent Service readiness check failed.",
        )

    try:
        response = httpx.get(f"{base_url}/v1/ready", timeout=2)
        response.raise_for_status()
    except httpx.HTTPError:
        logger.exception("Agent Service readiness check failed.")
        return ReadinessCheck(
            status=DependencyStatus.FAIL,
            message="Agent Service readiness check failed.",
        )

    return ReadinessCheck(status=DependencyStatus.OK)


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


@lru_cache(maxsize=1)
def required_schema_revision() -> str:
    repository_root = Path(__file__).resolve().parents[2]
    config = Config(str(repository_root / "alembic.ini"))
    revision = ScriptDirectory.from_config(config).get_current_head()
    if revision is None:
        raise RuntimeError("Web BFF migrations must define exactly one head revision.")
    return revision
