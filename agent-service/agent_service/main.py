from __future__ import annotations

from fastapi import FastAPI, Response

from talk_to_your_stock_shared import (
    HealthResponse,
    ReadinessResponse,
    ServiceName,
    ServiceStatus,
)
from talk_to_your_stock_shared.readiness import (
    build_readiness_response,
    check_database,
    readiness_http_status,
)
from talk_to_your_stock_shared.time import utc_now

app = FastAPI(
    title="TalkToYourStock Agent Service",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


@app.get("/v1/health", response_model=HealthResponse, tags=["Health"])
def health() -> HealthResponse:
    return HealthResponse(
        status=ServiceStatus.OK,
        service=ServiceName.AGENT_SERVICE,
        time=utc_now(),
    )


@app.get("/v1/ready", response_model=ReadinessResponse, tags=["Health"])
def ready(response: Response) -> ReadinessResponse:
    readiness = build_readiness_response(
        service=ServiceName.AGENT_SERVICE,
        database_checker=check_database,
    )
    response.status_code = readiness_http_status(readiness)
    return readiness
