from __future__ import annotations

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

app = FastAPI(
    title="TalkToYourStock Comps Service",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


@app.get("/v1/health", response_model=HealthResponse, tags=["Health"])
def health() -> HealthResponse:
    return HealthResponse(
        status=ServiceStatus.OK,
        service=ServiceName.COMPS_SERVICE,
        time=utc_now(),
    )


@app.get("/v1/ready", response_model=ReadinessResponse, tags=["Health"])
def ready() -> ReadinessResponse:
    return ReadinessResponse(
        status=ReadinessState.READY,
        service=ServiceName.COMPS_SERVICE,
        checks={
            "configuration": ReadinessCheck(status=DependencyStatus.OK),
        },
        time=utc_now(),
    )
