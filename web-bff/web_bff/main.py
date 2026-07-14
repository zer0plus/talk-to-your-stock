from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Path, Query, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from talk_to_your_stock_shared import (
    CreateMessageRequest,
    CreateMessageResponse,
    CreateThreadRequest,
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    MessageListResponse,
    MessageRole,
    MessageStatus,
    ReadinessResponse,
    ServiceName,
    ServiceStatus,
    ThreadListResponse,
    ThreadResponse,
    User,
)
from talk_to_your_stock_shared.readiness import (
    build_readiness_response,
    check_database,
    readiness_http_status,
)
from talk_to_your_stock_shared.time import utc_now
from web_bff.agent_client import AgentServiceUnavailable, HttpAgentClient
from web_bff.auth import AuthenticationError, authenticate_user
from web_bff.repository import PostgresWebBffRepository

app = FastAPI(
    title="TalkToYourStock Web BFF",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


class ApiException(Exception):
    def __init__(self, *, status_code: int, code: ErrorCode, message: str) -> None:
        self.status_code = status_code
        self.error = ErrorResponse(
            error=ErrorDetail(
                code=code,
                message=message,
            )
        )


@app.exception_handler(ApiException)
def handle_api_exception(_request, exc: ApiException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.error.model_dump(mode="json"),
    )


@app.exception_handler(AgentServiceUnavailable)
def handle_agent_service_unavailable(
    _request,
    exc: AgentServiceUnavailable,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content=ErrorResponse(
            error=ErrorDetail(
                code=ErrorCode.UPSTREAM_ERROR,
                message=str(exc),
            )
        ).model_dump(mode="json"),
    )


@app.exception_handler(RequestValidationError)
def validation_exception_handler(
    _request,
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
        service=ServiceName.WEB_BFF,
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
        service=ServiceName.WEB_BFF,
        database_checker=check_database,
    )
    response.status_code = readiness_http_status(readiness)
    return readiness


def get_repository() -> PostgresWebBffRepository:
    return PostgresWebBffRepository.from_env()


def get_agent_client() -> HttpAgentClient:
    return HttpAgentClient.from_env()


def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    try:
        return authenticate_user(authorization=authorization)
    except AuthenticationError as exc:
        raise ApiException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.UNAUTHORIZED,
            message=str(exc),
        ) from exc


@app.get("/v1/me", response_model=dict[str, User], tags=["Auth"])
def me(
    repository: Annotated[PostgresWebBffRepository, Depends(get_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, User]:
    return {"user": repository.upsert_user(current_user)}


@app.get(
    "/v1/threads",
    response_model=ThreadListResponse,
    responses={400: {"model": ErrorResponse}},
    tags=["Threads"],
)
def list_threads(
    repository: Annotated[PostgresWebBffRepository, Depends(get_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    cursor: str | None = None,
) -> ThreadListResponse:
    user = repository.upsert_user(current_user)
    threads, page = repository.list_threads(
        user_id=user.id,
        limit=limit,
        cursor=cursor,
    )
    return ThreadListResponse(threads=threads, page=page)


@app.post(
    "/v1/threads",
    response_model=ThreadResponse,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"model": ErrorResponse}},
    tags=["Threads"],
)
def create_thread(
    request: CreateThreadRequest,
    repository: Annotated[PostgresWebBffRepository, Depends(get_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ThreadResponse:
    user = repository.upsert_user(current_user)
    thread = repository.create_thread(user_id=user.id, title=request.title)
    return ThreadResponse(thread=thread)


@app.get(
    "/v1/threads/{thread_id}",
    response_model=ThreadResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    tags=["Threads"],
)
def get_thread(
    thread_id: Annotated[UUID, Path()],
    repository: Annotated[PostgresWebBffRepository, Depends(get_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ThreadResponse:
    user = repository.upsert_user(current_user)
    thread = repository.get_thread(thread_id=thread_id, user_id=user.id)
    if thread is None:
        raise ApiException(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.NOT_FOUND,
            message="Thread not found.",
        )
    return ThreadResponse(thread=thread)


@app.get(
    "/v1/threads/{thread_id}/messages",
    response_model=MessageListResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    tags=["Messages"],
)
def list_messages(
    thread_id: Annotated[UUID, Path()],
    repository: Annotated[PostgresWebBffRepository, Depends(get_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    cursor: str | None = None,
) -> MessageListResponse:
    user = repository.upsert_user(current_user)
    messages, page = repository.list_messages(
        thread_id=thread_id,
        user_id=user.id,
        limit=limit,
        cursor=cursor,
    )
    if messages is None:
        raise ApiException(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.NOT_FOUND,
            message="Thread not found.",
        )
    return MessageListResponse(messages=messages, page=page)


@app.post(
    "/v1/threads/{thread_id}/messages",
    response_model=CreateMessageResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
    tags=["Messages"],
)
def create_message(
    thread_id: Annotated[UUID, Path()],
    request: CreateMessageRequest,
    repository: Annotated[PostgresWebBffRepository, Depends(get_repository)],
    agent_client: Annotated[HttpAgentClient, Depends(get_agent_client)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CreateMessageResponse:
    user = repository.upsert_user(current_user)
    thread = repository.get_thread(thread_id=thread_id, user_id=user.id)
    if thread is None:
        raise ApiException(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.NOT_FOUND,
            message="Thread not found.",
        )

    user_message = repository.create_message(
        thread_id=thread.id,
        role=MessageRole.USER,
        content=request.content,
        status=MessageStatus.COMPLETE,
    )
    try:
        agent_response = agent_client.respond_to_user_message(
            user=user,
            thread=thread,
            user_message=user_message,
        )
    except AgentServiceUnavailable as exc:
        raise ApiException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code=ErrorCode.UPSTREAM_ERROR,
            message=str(exc),
        ) from exc

    run = agent_response.run
    if run is not None and (
        run.thread_id != thread.id or run.trigger_message_id != user_message.id
    ):
        raise ApiException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code=ErrorCode.UPSTREAM_ERROR,
            message="Agent Service returned invalid Run linkage.",
        )

    assistant_message = repository.create_message(
        thread_id=thread.id,
        role=MessageRole.ASSISTANT,
        content=agent_response.content,
        status=MessageStatus.COMPLETE,
        run_id=run.id if run is not None else None,
    )
    return CreateMessageResponse(
        user_message=user_message,
        assistant_message=assistant_message,
        run=run,
        events_url=None,
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
