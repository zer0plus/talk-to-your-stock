from __future__ import annotations

from fastapi import FastAPI, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from talk_to_your_stock_shared import (
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    GenerateCompsToolRequest,
    GenerateCompsToolResponse,
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

from .tool_validation import (
    RuntimeConfigurationError,
    ToolCapabilityNotImplementedError,
    ToolValidationError,
    UpstreamValidationError,
    validate_generate_comps_request,
)

app = FastAPI(
    title="TalkToYourStock Comps Service",
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
    )
    response.status_code = readiness_http_status(readiness)
    return readiness


@app.post(
    "/v1/internal/tools/generate-comps-table",
    response_model=GenerateCompsToolResponse,
    responses={
        400: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
        501: {"model": ErrorResponse},
    },
    tags=["Internal"],
)
def generate_comps_table(_request: GenerateCompsToolRequest) -> JSONResponse:
    try:
        validate_generate_comps_request(_request)
    except ToolCapabilityNotImplementedError as exc:
        return _error_response(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            code=ErrorCode.INTERNAL_ERROR,
            message=exc.message,
            details=exc.details,
        )
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
