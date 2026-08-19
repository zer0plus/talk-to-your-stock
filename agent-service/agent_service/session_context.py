from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from inspect import isawaitable
from uuid import UUID

from google.adk.events import Event
from google.adk.sessions import BaseSessionService, DatabaseSessionService, Session
from google.genai import types

from talk_to_your_stock_shared import (
    AgentMessageRequest,
    AgentMessageResponse,
    DependencyStatus,
    ReadinessCheck,
)
from talk_to_your_stock_shared.readiness import DATABASE_URL_VAR

GOOGLE_ADK_APP_NAME_VAR = "GOOGLE_ADK_APP_NAME"
LOCAL_ADK_APP_NAME = "talk-to-your-stock"
FUNDAMENTAL_ANALYSIS_AGENT_NAME = "fundamental_analysis_agent"
logger = logging.getLogger(__name__)


class AgentSessionUnavailable(RuntimeError):
    pass


class AgentInvocationInProgress(RuntimeError):
    pass


class AgentResponseConflict(ValueError):
    pass


class AdkSessionContext:
    def __init__(
        self,
        *,
        app_name: str,
        session_service: BaseSessionService | None,
        invocation_database_url: str | None = None,
        unavailable_message: str | None = None,
    ) -> None:
        if not app_name.strip():
            raise AgentSessionUnavailable(f"{GOOGLE_ADK_APP_NAME_VAR} is required.")
        self._app_name = app_name
        self._session_service = session_service
        self._invocation_database_url = invocation_database_url
        self._unavailable_message = unavailable_message
        self._prepared = not isinstance(session_service, DatabaseSessionService)
        self._turn_locks: dict[tuple[UUID, UUID], asyncio.Lock] = {}
        self._turn_lock_ref_counts: dict[tuple[UUID, UUID], int] = {}
        self._turn_locks_guard = asyncio.Lock()
        self._terminal_responses: dict[
            UUID,
            tuple[UUID, UUID, str, AgentMessageResponse],
        ] = {}

    @property
    def app_name(self) -> str:
        return self._app_name

    @property
    def session_service(self) -> BaseSessionService:
        return self._require_prepared_service()

    @classmethod
    def unavailable(cls, message: str) -> AdkSessionContext:
        return cls(
            app_name=LOCAL_ADK_APP_NAME,
            session_service=None,
            unavailable_message=message,
        )

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> AdkSessionContext:
        env = os.environ if environ is None else environ
        database_url = env.get(DATABASE_URL_VAR, "").strip()
        if not database_url:
            raise AgentSessionUnavailable(f"{DATABASE_URL_VAR} is required.")
        app_name = env.get(GOOGLE_ADK_APP_NAME_VAR, "").strip() or LOCAL_ADK_APP_NAME
        return cls.from_database_url(
            app_name=app_name,
            database_url=_adk_database_url(database_url),
            invocation_database_url=database_url,
        )

    @classmethod
    def from_database_url(
        cls,
        *,
        app_name: str,
        database_url: str,
        invocation_database_url: str | None = None,
    ) -> AdkSessionContext:
        try:
            session_service = DatabaseSessionService(database_url)
        except Exception as exc:
            raise AgentSessionUnavailable(
                "Agent session configuration is invalid."
            ) from exc
        return cls(
            app_name=app_name,
            session_service=session_service,
            invocation_database_url=invocation_database_url,
        )

    @asynccontextmanager
    async def invocation(self, *, message_id: UUID) -> AsyncIterator[None]:
        if self._invocation_database_url is None:
            yield
            return
        try:
            connection = await asyncio.to_thread(
                _acquire_invocation_lock,
                self._invocation_database_url,
                message_id,
            )
        except Exception as exc:
            raise AgentSessionUnavailable(
                "Agent invocation ownership unavailable."
            ) from exc
        if connection is None:
            raise AgentInvocationInProgress(
                "Message invocation is already being routed."
            )
        try:
            yield
        finally:
            try:
                await asyncio.to_thread(
                    _release_invocation_lock,
                    connection,
                    message_id,
                )
            except Exception as exc:
                raise AgentSessionUnavailable(
                    "Agent invocation ownership unavailable."
                ) from exc

    async def get_session(
        self,
        *,
        user_id: UUID,
        thread_id: UUID,
    ) -> Session | None:
        session_service = self._require_prepared_service()
        try:
            return await session_service.get_session(
                app_name=self._app_name,
                user_id=str(user_id),
                session_id=str(thread_id),
            )
        except Exception as exc:
            raise AgentSessionUnavailable("Agent session unavailable.") from exc

    async def prepare(self) -> None:
        if self._session_service is None and self._unavailable_message is not None:
            return
        session_service = self._require_service()
        prepare_tables = getattr(session_service, "prepare_tables", None)
        if prepare_tables is not None:
            try:
                await prepare_tables()
            except Exception as exc:
                raise AgentSessionUnavailable("Agent session unavailable.") from exc
        self._prepared = True

    async def readiness_check(self) -> ReadinessCheck:
        try:
            session_service = self._require_prepared_service()
            await session_service.get_session(
                app_name=self._app_name,
                user_id="readiness",
                session_id="readiness",
            )
            if self._invocation_database_url is not None:
                await asyncio.to_thread(
                    _check_terminal_response_store,
                    self._invocation_database_url,
                )
        except Exception:
            logger.exception("Agent session readiness check failed.")
            return ReadinessCheck(
                status=DependencyStatus.FAIL,
                message="Agent session readiness check failed.",
            )
        return ReadinessCheck(status=DependencyStatus.OK)

    async def get_terminal_response(
        self,
        *,
        request: AgentMessageRequest,
    ) -> AgentMessageResponse | None:
        if self._invocation_database_url is not None:
            try:
                return await asyncio.to_thread(
                    _get_terminal_response,
                    self._invocation_database_url,
                    request,
                )
            except AgentResponseConflict:
                raise
            except Exception as exc:
                raise AgentSessionUnavailable(
                    "Agent response replay unavailable."
                ) from exc

        stored = self._terminal_responses.get(request.user_message_id)
        if stored is None:
            return None
        _validate_terminal_response_identity(
            request=request,
            user_id=stored[0],
            thread_id=stored[1],
            content=stored[2],
        )
        return stored[3]

    async def save_terminal_response(
        self,
        *,
        request: AgentMessageRequest,
        response: AgentMessageResponse,
    ) -> AgentMessageResponse:
        if self._invocation_database_url is not None:
            try:
                return await asyncio.to_thread(
                    _save_terminal_response,
                    self._invocation_database_url,
                    request,
                    response,
                )
            except AgentResponseConflict:
                raise
            except Exception as exc:
                raise AgentSessionUnavailable(
                    "Agent response persistence unavailable."
                ) from exc

        existing = await self.get_terminal_response(request=request)
        if existing is not None:
            return existing
        self._terminal_responses[request.user_message_id] = (
            request.user_id,
            request.thread_id,
            request.content,
            response,
        )
        return response

    @asynccontextmanager
    async def turn(
        self,
        *,
        user_id: UUID,
        thread_id: UUID,
    ) -> AsyncIterator[None]:
        lock_key = (user_id, thread_id)
        async with self._turn_locks_guard:
            lock = self._turn_locks.get(lock_key)
            if lock is None:
                lock = asyncio.Lock()
                self._turn_locks[lock_key] = lock
            self._turn_lock_ref_counts[lock_key] = (
                self._turn_lock_ref_counts.get(lock_key, 0) + 1
            )

        try:
            async with lock:
                yield
        finally:
            async with self._turn_locks_guard:
                remaining = self._turn_lock_ref_counts[lock_key] - 1
                if remaining == 0:
                    del self._turn_lock_ref_counts[lock_key]
                    del self._turn_locks[lock_key]
                else:
                    self._turn_lock_ref_counts[lock_key] = remaining

    async def begin_turn(
        self,
        *,
        user_id: UUID,
        thread_id: UUID,
        user_message_id: UUID,
        user_content: str,
    ) -> Session:
        session = await self._get_or_create_session(
            user_id=user_id,
            thread_id=thread_id,
        )
        invocation_id = str(user_message_id)
        await self._append_event(
            session,
            Event(
                invocation_id=invocation_id,
                author="user",
                content=types.Content(
                    role="user",
                    parts=[types.Part(text=user_content)],
                ),
            ),
        )
        return session

    async def complete_turn(
        self,
        *,
        session: Session,
        user_message_id: UUID,
        assistant_content: str,
    ) -> None:
        await self._append_event(
            session,
            Event(
                invocation_id=str(user_message_id),
                author=FUNDAMENTAL_ANALYSIS_AGENT_NAME,
                content=types.Content(
                    role="model",
                    parts=[types.Part(text=assistant_content)],
                ),
            ),
        )

    async def record_event(
        self,
        *,
        user_id: UUID,
        thread_id: UUID,
        event: Event,
    ) -> None:
        session = await self._get_or_create_session(
            user_id=user_id,
            thread_id=thread_id,
        )
        await self._append_event(session, event)

    async def close(self) -> None:
        close = getattr(self._session_service, "close", None)
        if close is None:
            return
        result = close()
        if isawaitable(result):
            await result

    async def _get_or_create_session(
        self,
        *,
        user_id: UUID,
        thread_id: UUID,
    ) -> Session:
        session = await self.get_session(user_id=user_id, thread_id=thread_id)
        if session is not None:
            return session
        try:
            session_service = self._require_prepared_service()
            return await session_service.create_session(
                app_name=self._app_name,
                user_id=str(user_id),
                session_id=str(thread_id),
            )
        except Exception as exc:
            session = await self.get_session(user_id=user_id, thread_id=thread_id)
            if session is not None:
                return session
            raise AgentSessionUnavailable("Agent session unavailable.") from exc

    async def _append_event(self, session: Session, event: Event) -> None:
        session_service = self._require_prepared_service()
        try:
            await session_service.append_event(session, event)
        except Exception as exc:
            raise AgentSessionUnavailable("Agent session unavailable.") from exc

    def _require_service(self) -> BaseSessionService:
        if self._session_service is None:
            raise AgentSessionUnavailable(
                self._unavailable_message or "Agent session unavailable."
            )
        return self._session_service

    def _require_prepared_service(self) -> BaseSessionService:
        session_service = self._require_service()
        if not self._prepared:
            raise AgentSessionUnavailable("Agent session has not been prepared.")
        return session_service


def _adk_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    return database_url


def _invocation_lock_keys(message_id: UUID) -> tuple[int, int]:
    return (
        int.from_bytes(message_id.bytes[:4], byteorder="big", signed=True),
        int.from_bytes(message_id.bytes[4:8], byteorder="big", signed=True),
    )


def _acquire_invocation_lock(database_url: str, message_id: UUID):
    import psycopg

    connection = None
    try:
        connection = psycopg.connect(database_url, autocommit=True)
        with connection.cursor() as cursor:
            cursor.execute(
                "select pg_try_advisory_lock(%s, %s)",
                _invocation_lock_keys(message_id),
            )
            acquired = cursor.fetchone()[0]
    except Exception:
        if connection is not None:
            connection.close()
        raise
    if acquired:
        return connection
    connection.close()
    return None


def _release_invocation_lock(connection, message_id: UUID) -> None:
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "select pg_advisory_unlock(%s, %s)",
                _invocation_lock_keys(message_id),
            )
    finally:
        connection.close()


def _check_terminal_response_store(database_url: str) -> None:
    import psycopg

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("select 1 from agent_response_envelopes limit 0")


def _get_terminal_response(
    database_url: str,
    request: AgentMessageRequest,
) -> AgentMessageResponse | None:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select user_id, thread_id, request_content, response_envelope
                from agent_response_envelopes
                where message_id = %s
                """,
                (request.user_message_id,),
            )
            row = cursor.fetchone()
    if row is None:
        return None
    _validate_terminal_response_identity(
        request=request,
        user_id=row["user_id"],
        thread_id=row["thread_id"],
        content=row["request_content"],
    )
    return AgentMessageResponse.model_validate(row["response_envelope"])


def _save_terminal_response(
    database_url: str,
    request: AgentMessageRequest,
    response: AgentMessageResponse,
) -> AgentMessageResponse:
    import psycopg
    from psycopg.types.json import Jsonb

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                insert into agent_response_envelopes (
                    message_id, user_id, thread_id, request_content,
                    response_envelope, created_at
                )
                values (%s, %s, %s, %s, %s, now())
                on conflict (message_id) do nothing
                """,
                (
                    request.user_message_id,
                    request.user_id,
                    request.thread_id,
                    request.content,
                    Jsonb(response.model_dump(mode="json")),
                ),
            )
    persisted = _get_terminal_response(database_url, request)
    if persisted is None:
        raise RuntimeError("Terminal Agent response was not persisted.")
    return persisted


def _validate_terminal_response_identity(
    *,
    request: AgentMessageRequest,
    user_id: UUID,
    thread_id: UUID,
    content: str,
) -> None:
    if (
        user_id != request.user_id
        or thread_id != request.thread_id
        or content != request.content
    ):
        raise AgentResponseConflict(
            "Message identity is already used by a different Agent invocation."
        )
