from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from talk_to_your_stock_shared import (
    AgentMessageRequest,
    AgentMessageResponse,
    DependencyStatus,
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    ReadinessResponse,
    ReadinessState,
    ServiceName,
    ServiceStatus,
    ReadinessCheck,
)
from talk_to_your_stock_shared.readiness import (
    build_readiness_response,
    check_database,
    readiness_http_status,
)
from talk_to_your_stock_shared.time import utc_now
from agent_service.comps_client import COMPS_SERVICE_URL_VAR
from agent_service.session_context import AdkSessionContext, AgentSessionUnavailable
from agent_service.fundamental_agent import (
    AgentRoutingUnavailable,
    FundamentalAnalysisAgent,
)

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_session_context() -> AdkSessionContext:
    try:
        return AdkSessionContext.from_env()
    except AgentSessionUnavailable as exc:
        return AdkSessionContext.unavailable(str(exc))


@lru_cache(maxsize=1)
def get_fundamental_agent() -> FundamentalAnalysisAgent:
    return FundamentalAnalysisAgent.from_env()


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    factory = application.dependency_overrides.get(
        get_session_context,
        get_session_context,
    )
    session_context = factory()
    try:
        await session_context.prepare()
        yield
    finally:
        try:
            await session_context.close()
        finally:
            if factory is get_session_context:
                get_session_context.cache_clear()


app = FastAPI(
    title="TalkToYourStock Agent Service",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)


@app.exception_handler(AgentRoutingUnavailable)
def agent_routing_exception_handler(
    _request: object,
    exc: AgentRoutingUnavailable,
) -> JSONResponse:
    return _agent_operation_error(exc)


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
async def ready(
    response: Response,
    session_context: Annotated[AdkSessionContext, Depends(get_session_context)],
) -> ReadinessResponse:
    agent_session_check = await session_context.readiness_check()
    readiness = build_readiness_response(
        service=ServiceName.AGENT_SERVICE,
        database_checker=check_database,
        additional_checks={
            "agent_session": agent_session_check,
            "agent_routing": await _agent_routing_readiness_check(),
        },
    )
    response.status_code = readiness_http_status(readiness)
    return readiness


async def _agent_routing_readiness_check() -> ReadinessCheck:
    if os.environ.get("TALK_TO_YOUR_STOCK_ENV", "").strip().lower() == "production":
        return ReadinessCheck(
            status=DependencyStatus.FAIL,
            message=(
                "Agent routing is local-only until public deployment controls "
                "are implemented."
            ),
        )

    base_url = os.environ.get(COMPS_SERVICE_URL_VAR, "").strip().rstrip("/")
    if not base_url:
        return _failed_comps_service_check()
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get(f"{base_url}/v1/ready")
        readiness = ReadinessResponse.model_validate(response.json())
    except (httpx.HTTPError, ValueError):
        logger.exception("Comps Service readiness check failed.")
        return _failed_comps_service_check()

    if (
        response.status_code != status.HTTP_200_OK
        or readiness.service != ServiceName.COMPS_SERVICE
        or readiness.status != ReadinessState.READY
    ):
        return _failed_comps_service_check()

    return ReadinessCheck(status=DependencyStatus.OK)


def _failed_comps_service_check() -> ReadinessCheck:
    return ReadinessCheck(
        status=DependencyStatus.FAIL,
        message="Comps Service is not ready.",
    )


@app.post(
    "/v1/internal/agent/respond",
    response_model=AgentMessageResponse,
    responses={400: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    tags=["Internal"],
)
async def respond_to_message(
    request: AgentMessageRequest,
    session_context: Annotated[AdkSessionContext, Depends(get_session_context)],
    fundamental_agent: Annotated[
        FundamentalAnalysisAgent,
        Depends(get_fundamental_agent),
    ],
) -> AgentMessageResponse | JSONResponse:
    try:
        async with session_context.turn(
            user_id=request.user_id,
            thread_id=request.thread_id,
        ):
            response = await fundamental_agent.respond(
                request=request,
                session_context=session_context,
            )
    except (AgentRoutingUnavailable, AgentSessionUnavailable) as exc:
        return _agent_operation_error(exc)
    return response


def _agent_operation_error(exc: Exception) -> JSONResponse:
    logger.error("Agent operation failed: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content=ErrorResponse(
            error=ErrorDetail(
                code=ErrorCode.UPSTREAM_ERROR,
                message=str(exc),
            )
        ).model_dump(mode="json"),
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
