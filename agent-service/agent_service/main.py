from __future__ import annotations

import os

from fastapi import FastAPI, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from talk_to_your_stock_shared import (
    AgentMessageRequest,
    AgentMessageResponse,
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    ReadinessResponse,
    ServiceName,
    ServiceStatus,
)
from talk_to_your_stock_shared.readiness import (
    ENVIRONMENT_VAR,
    PRODUCTION_ENVIRONMENT,
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


@app.exception_handler(RequestValidationError)
def validation_exception_handler(
    _request: object,
    exc: RequestValidationError,
) -> JSONResponse:
    first_error = exc.errors()[0] if exc.errors() else {}
    message = str(first_error.get("msg", "Request validation failed."))
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=ErrorResponse(
            error=ErrorDetail(
                code=ErrorCode.VALIDATION_ERROR,
                message=message,
                details=_validation_error_details(exc),
            )
        ).model_dump(mode="json"),
    )


@app.get("/v1/health", response_model=HealthResponse, tags=["Health"])
def health() -> HealthResponse:
    return HealthResponse(
        status=ServiceStatus.OK,
        service=ServiceName.AGENT_SERVICE,
        time=utc_now(),
    )


@app.get(
    "/v1/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
    tags=["Health"],
)
def ready(response: Response) -> ReadinessResponse:
    readiness = build_readiness_response(
        service=ServiceName.AGENT_SERVICE,
        database_checker=check_database,
    )
    response.status_code = readiness_http_status(readiness)
    return readiness


@app.post(
    "/v1/internal/agent/respond",
    response_model=AgentMessageResponse,
    responses={400: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    tags=["Internal"],
)
def respond_to_message(_request: AgentMessageRequest) -> AgentMessageResponse | JSONResponse:
    if os.environ.get(ENVIRONMENT_VAR, "").strip().lower() == PRODUCTION_ENVIRONMENT:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=ErrorResponse(
                error=ErrorDetail(
                    code=ErrorCode.UPSTREAM_ERROR,
                    message="Production Agent routing is not implemented.",
                )
            ).model_dump(mode="json"),
        )

    return AgentMessageResponse(
        content=(
            "AgentService: Message received"
            "AgentService: routing WIP"
        ),
        run=None,
    )


def _validation_error_details(exc: RequestValidationError) -> dict[str, object]:
    errors: list[dict[str, object]] = []
    for error in exc.errors():
        errors.append(
            {
                "location": list(error.get("loc", ())),
                "message": str(error.get("msg", "Request validation failed.")),
                "type": str(error.get("type", "value_error")),
            }
        )
    return {"errors": errors}


def _custom_openapi() -> dict[str, object]:
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version="0.1.0",
        routes=app.routes,
    )
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if isinstance(operation, dict):
                operation.get("responses", {}).pop("422", None)
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = _custom_openapi
