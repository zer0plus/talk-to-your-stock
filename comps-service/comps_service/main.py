from __future__ import annotations

import hmac
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Path, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from talk_to_your_stock_shared import (
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    GenerateCompsToolRequest,
    GenerateCompsToolResponse,
    HealthResponse,
    PeerSelectionMode,
    RunListResponse,
    ReadinessResponse,
    RunResponse,
    RunStatus,
    RunTableResponse,
    ServiceName,
    ServiceStatus,
    SourceSnapshotResponse,
    TraceResponse,
)
from talk_to_your_stock_shared.readiness import (
    build_readiness_response,
    readiness_http_status,
)
from talk_to_your_stock_shared.time import utc_now

from .provider import AlphaVantageCompanyDataSource
from .provider_config import InvalidProviderConfiguration
from .readiness import check_comps_database, check_run_data_source
from .repository import (
    CompsPersistenceUnavailable,
    InvalidRunCursor,
    InvalidRunLinkage,
    PostgresCompsRunRepository,
)
from .run_service import (
    CompanyDataSource,
    CompanyDataUnavailable,
    CompsRunRepository,
    CompsRunService,
    DuplicateToolInvocation,
    FailedCompsRun,
    RecoveredFailedCompsRun,
    RunCalculationInProgress,
)
from .tool_validation import (
    AlphaVantageTickerValidator,
    RuntimeConfigurationError,
    ToolValidationError,
    UpstreamValidationError,
    ValidatedTickerMatches,
    validate_generate_comps_request,
)

COMPS_SERVICE_INTERNAL_TOKEN_VAR = "COMPS_SERVICE_INTERNAL_TOKEN"
GENERATE_COMPS_TOOL_PATH = "/v1/internal/tools/generate-comps-table"
logger = logging.getLogger(__name__)

app = FastAPI(
    title="TalkToYourStock Comps Service",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


@app.exception_handler(InvalidRunLinkage)
def invalid_run_linkage_exception_handler(
    _request: object,
    exc: InvalidRunLinkage,
) -> JSONResponse:
    return _error_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        code=ErrorCode.VALIDATION_ERROR,
        message=str(exc),
    )


@app.exception_handler(InvalidRunCursor)
def invalid_run_cursor_exception_handler(
    _request: object,
    exc: InvalidRunCursor,
) -> JSONResponse:
    return _error_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        code=ErrorCode.VALIDATION_ERROR,
        message=str(exc),
    )


@app.exception_handler(CompsPersistenceUnavailable)
def persistence_exception_handler(
    _request: object,
    exc: CompsPersistenceUnavailable,
) -> JSONResponse:
    return _error_response(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code=ErrorCode.INTERNAL_ERROR,
        message=str(exc),
    )


@app.exception_handler(DuplicateToolInvocation)
def duplicate_tool_invocation_exception_handler(
    _request: object,
    exc: DuplicateToolInvocation,
) -> JSONResponse:
    return _error_response(
        status_code=status.HTTP_409_CONFLICT,
        code=ErrorCode.CONFLICT,
        message=str(exc),
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
        database_checker=check_comps_database,
        additional_checkers={"run_data_source": check_run_data_source},
    )
    response.status_code = readiness_http_status(readiness)
    return readiness


def get_repository() -> CompsRunRepository:
    return PostgresCompsRunRepository.from_env()


def get_validated_ticker_matches() -> ValidatedTickerMatches:
    return {}


def get_company_data_source(
    validated_ticker_matches: Annotated[
        ValidatedTickerMatches,
        Depends(get_validated_ticker_matches),
    ],
) -> CompanyDataSource:
    return AlphaVantageCompanyDataSource(
        validated_ticker_matches=validated_ticker_matches
    )


def get_ticker_validator(
    validated_ticker_matches: Annotated[
        ValidatedTickerMatches,
        Depends(get_validated_ticker_matches),
    ],
) -> AlphaVantageTickerValidator:
    from . import tool_validation

    return tool_validation.AlphaVantageTickerValidator(
        validated_ticker_matches=validated_ticker_matches
    )


@app.post(
    GENERATE_COMPS_TOOL_PATH,
    response_model=GenerateCompsToolResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
        501: {"model": ErrorResponse},
    },
    tags=["Internal"],
)
def generate_comps_table(
    request: GenerateCompsToolRequest,
    repository: Annotated[CompsRunRepository, Depends(get_repository)],
    company_data_source: Annotated[
        CompanyDataSource,
        Depends(get_company_data_source),
    ],
    ticker_validator: Annotated[
        AlphaVantageTickerValidator,
        Depends(get_ticker_validator),
    ],
    validated_ticker_matches: Annotated[
        ValidatedTickerMatches,
        Depends(get_validated_ticker_matches),
    ],
) -> GenerateCompsToolResponse | JSONResponse:
    if request.peer_selection_mode == PeerSelectionMode.AUTO:
        return _error_response(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            code=ErrorCode.INTERNAL_ERROR,
            message="Auto peer selection is not implemented yet.",
        )

    try:
        run_service = CompsRunService(
            repository=repository,
            company_data_source=company_data_source,
        )
    except InvalidProviderConfiguration as exc:
        return _error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=ErrorCode.INTERNAL_ERROR,
            message=str(exc),
            details={"invalid_configuration": [exc.name]},
        )
    try:
        recovered = run_service.recover_terminal(request)
        if recovered is not None:
            return recovered
        requires_external_validation = run_service.requires_external_validation(
            request
        )
    except RecoveredFailedCompsRun as exc:
        return _recovered_failure_response(exc)
    except RunCalculationInProgress as exc:
        return _calculation_in_progress_response(exc)
    try:
        if requires_external_validation:
            validate_generate_comps_request(
                request,
                ticker_validator=ticker_validator,
            )
        else:
            persisted_validation = repository.get_validation_evidence(
                invocation_id=request.invocation_id,
            )
            if persisted_validation is not None:
                validated_ticker_matches.update(persisted_validation)
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
        logger.error(
            (
                "Comps Tool validation failed: thread_id=%s "
                "trigger_message_id=%s message=%s"
            ),
            request.thread_id,
            request.trigger_message_id,
            exc.message,
        )
        return _error_response(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code=ErrorCode.UPSTREAM_ERROR,
            message=exc.message,
            details=exc.details,
        )

    try:
        return run_service.generate(
            request,
            validation_evidence=validated_ticker_matches,
        )
    except RecoveredFailedCompsRun as exc:
        return _recovered_failure_response(exc)
    except RunCalculationInProgress as exc:
        return _calculation_in_progress_response(exc)
    except FailedCompsRun as exc:
        logger.error(
            (
                "Comps Run failed: run_id=%s thread_id=%s "
                "trigger_message_id=%s message=%s"
            ),
            exc.run_id,
            request.thread_id,
            request.trigger_message_id,
            str(exc),
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.error.model_dump(mode="json"),
        )


@app.get(
    "/v1/runs/{run_id}",
    response_model=RunResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    tags=["Runs"],
)
def get_run(
    run_id: Annotated[UUID, Path()],
    repository: Annotated[CompsRunRepository, Depends(get_repository)],
) -> RunResponse | JSONResponse:
    run = repository.get_run(run_id)
    if run is None:
        return _error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.NOT_FOUND,
            message="Run not found.",
        )
    return RunResponse(run=run)


@app.get(
    "/v1/threads/{thread_id}/runs",
    response_model=RunListResponse,
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    tags=["Runs"],
)
def list_runs(
    thread_id: Annotated[UUID, Path()],
    repository: Annotated[CompsRunRepository, Depends(get_repository)],
    status_filter: Annotated[RunStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    cursor: str | None = None,
) -> RunListResponse:
    runs, page = repository.list_runs(
        thread_id=thread_id,
        status=status_filter,
        limit=limit,
        cursor=cursor,
    )
    return RunListResponse(runs=runs, page=page)


@app.get(
    "/v1/runs/{run_id}/table",
    response_model=RunTableResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    tags=["Runs"],
)
def get_run_table(
    run_id: Annotated[UUID, Path()],
    repository: Annotated[CompsRunRepository, Depends(get_repository)],
) -> RunTableResponse | JSONResponse:
    table = repository.get_table(run_id)
    if table is None:
        return _error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.NOT_FOUND,
            message="Comps Table not found.",
        )
    return table


@app.get(
    "/v1/runs/{run_id}/trace",
    response_model=TraceResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    tags=["Runs"],
)
def get_run_trace(
    run_id: Annotated[UUID, Path()],
    repository: Annotated[CompsRunRepository, Depends(get_repository)],
) -> TraceResponse | JSONResponse:
    trace = repository.get_trace(run_id)
    if trace is None:
        return _error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.NOT_FOUND,
            message="Trace not found.",
        )
    return trace


@app.get(
    "/v1/runs/{run_id}/source-snapshot",
    response_model=SourceSnapshotResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    tags=["Runs"],
)
def get_run_source_snapshot(
    run_id: Annotated[UUID, Path()],
    repository: Annotated[CompsRunRepository, Depends(get_repository)],
) -> SourceSnapshotResponse | JSONResponse:
    source_snapshot = repository.get_source_snapshot(run_id)
    if source_snapshot is None:
        return _error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.NOT_FOUND,
            message="Source Snapshot not found.",
        )
    return SourceSnapshotResponse.model_validate(
        source_snapshot.model_dump(mode="python")
    )


def _error_response(
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    details: dict[str, object] | None = None,
    run_id: UUID | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error=ErrorDetail(
                code=code,
                message=message,
                details=details,
                run_id=run_id,
            )
        ).model_dump(mode="json"),
    )


def _recovered_failure_response(exc: RecoveredFailedCompsRun) -> JSONResponse:
    return JSONResponse(
        status_code=exc.result.status_code,
        content=exc.result.error.model_dump(mode="json"),
    )


def _calculation_in_progress_response(exc: RunCalculationInProgress) -> JSONResponse:
    return _error_response(
        status_code=status.HTTP_409_CONFLICT,
        code=ErrorCode.CONFLICT,
        message=str(exc),
        details={
            "thread_id": str(exc.run.thread_id),
            "trigger_message_id": str(exc.run.trigger_message_id),
            "status": exc.run.status.value,
        },
        run_id=exc.run.id,
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
