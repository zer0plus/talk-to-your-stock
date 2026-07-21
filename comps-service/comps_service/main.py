from __future__ import annotations

import hmac
import os
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from talk_to_your_stock_shared import (
    DependencyStatus,
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    GenerateCompsToolRequest,
    GenerateCompsToolResponse,
    HealthResponse,
    PeerSelectionMode,
    ReadinessCheck,
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

from .tool_validation import (
    RuntimeConfigurationError,
    ToolValidationError,
    UpstreamValidationError,
    validate_generate_comps_request,
)

COMPS_SERVICE_INTERNAL_TOKEN_VAR = "COMPS_SERVICE_INTERNAL_TOKEN"
GENERATE_COMPS_TOOL_PATH = "/v1/internal/tools/generate-comps-table"

app = FastAPI(
    title="TalkToYourStock Comps Service",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


@app.middleware("http")
async def authenticate_internal_tool_routes(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    if request.url.path == GENERATE_COMPS_TOOL_PATH:
        auth_error = _internal_tool_auth_error(request.headers.get("authorization"))
        if auth_error is not None:
            return auth_error

    return await call_next(request)


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
        service=ServiceName.COMPS_SERVICE,
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
        service=ServiceName.COMPS_SERVICE,
        database_checker=check_database,
        additional_checks={
            "run_execution": ReadinessCheck(
                status=DependencyStatus.FAIL,
                message="Comps run execution is not implemented yet.",
            )
        },
    )
    response.status_code = readiness_http_status(readiness)
    return readiness


@app.post(
    GENERATE_COMPS_TOOL_PATH,
    response_model=GenerateCompsToolResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
        501: {"model": ErrorResponse},
    },
    tags=["Internal"],
)
def generate_comps_table(
    _request: GenerateCompsToolRequest,
) -> JSONResponse:
    if _request.peer_selection_mode == PeerSelectionMode.AUTO:
        return _error_response(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            code=ErrorCode.INTERNAL_ERROR,
            message="Auto peer selection is not implemented yet.",
        )

    try:
        validate_generate_comps_request(_request)
    except ToolValidationError as exc:
        return _error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.VALIDATION_ERROR,
            message=exc.message,
            details=exc.details,
        )
    except RuntimeConfigurationError as exc:
        return _error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=ErrorCode.INTERNAL_ERROR,
            message=exc.message,
            details=exc.details,
        )
    except UpstreamValidationError as exc:
        return _error_response(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code=ErrorCode.UPSTREAM_ERROR,
            message=exc.message,
            details=exc.details,
        )

    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        content=ErrorResponse(
            error=ErrorDetail(
                code=ErrorCode.INTERNAL_ERROR,
                message="Comps run execution is not implemented yet.",
            )
        ).model_dump(mode="json"),
    )


def _error_response(
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error=ErrorDetail(
                code=code,
                message=message,
                details=details,
            )
        ).model_dump(mode="json"),
    )


def _internal_tool_auth_error(authorization: str | None) -> JSONResponse | None:
    token = os.environ.get(COMPS_SERVICE_INTERNAL_TOKEN_VAR, "").strip()
    if not token:
        return _error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=ErrorCode.INTERNAL_ERROR,
            message=(
                f"Missing required configuration: "
                f"{COMPS_SERVICE_INTERNAL_TOKEN_VAR}."
            ),
            details={"missing_configuration": [COMPS_SERVICE_INTERNAL_TOKEN_VAR]},
        )

    parts = (authorization or "").split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return _error_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.UNAUTHORIZED,
            message="Unauthorized internal tool call.",
        )

    try:
        actual_token = parts[1].encode("ascii")
        expected_token = token.encode("ascii")
    except UnicodeEncodeError:
        actual_token = b""
        expected_token = b"\x00"

    if not hmac.compare_digest(actual_token, expected_token):
        return _error_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.UNAUTHORIZED,
            message="Unauthorized internal tool call.",
        )

    return None


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
