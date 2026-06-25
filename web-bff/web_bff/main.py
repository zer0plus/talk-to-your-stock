from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI

from talk_to_your_stock_shared import (
    DependencyStatus,
    HealthResponse,
    ReadinessCheck,
    ReadinessResponse,
    ReadinessState,
    ServiceName,
    ServiceStatus,
)
from talk_to_your_stock_shared.time import utc_now

ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = ROOT / "api" / "openapi.yaml"


@lru_cache(maxsize=1)
def load_spec() -> dict[str, Any]:
    with SPEC_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


app = FastAPI(
    title="TalkToYourStock Web BFF",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


@app.get("/v1/health", response_model=HealthResponse, tags=["Health"])
def health() -> HealthResponse:
    return HealthResponse(
        status=ServiceStatus.OK,
        service=ServiceName.WEB_BFF,
        time=utc_now(),
    )


@app.get("/v1/ready", response_model=ReadinessResponse, tags=["Health"])
def ready() -> ReadinessResponse:
    return ReadinessResponse(
        status=ReadinessState.READY,
        service=ServiceName.WEB_BFF,
        checks={
            "configuration": ReadinessCheck(status=DependencyStatus.OK),
        },
        time=utc_now(),
    )


def custom_openapi() -> dict[str, Any]:
    return load_spec()


app.openapi = custom_openapi
