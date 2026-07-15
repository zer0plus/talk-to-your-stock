from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from inspect import isawaitable
from uuid import UUID

from google.adk.events import Event
from google.adk.sessions import BaseSessionService, DatabaseSessionService, Session
from google.genai import types

from talk_to_your_stock_shared import DependencyStatus, ReadinessCheck
from talk_to_your_stock_shared.readiness import DATABASE_URL_VAR

GOOGLE_ADK_APP_NAME_VAR = "GOOGLE_ADK_APP_NAME"
LOCAL_ADK_APP_NAME = "talk-to-your-stock"
FUNDAMENTAL_ANALYSIS_AGENT_NAME = "fundamental_analysis_agent"
logger = logging.getLogger(__name__)


class AgentSessionUnavailable(RuntimeError):
    pass


class AdkSessionContext:
    def __init__(
        self,
        *,
        app_name: str,
        session_service: BaseSessionService | None,
        unavailable_message: str | None = None,
    ) -> None:
        if not app_name.strip():
            raise AgentSessionUnavailable(f"{GOOGLE_ADK_APP_NAME_VAR} is required.")
        self._app_name = app_name
        self._session_service = session_service
        self._unavailable_message = unavailable_message
        self._prepared = not isinstance(session_service, DatabaseSessionService)

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
        )

    @classmethod
    def from_database_url(
        cls,
        *,
        app_name: str,
        database_url: str,
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
        )

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
        except Exception:
            logger.exception("Agent session readiness check failed.")
            return ReadinessCheck(
                status=DependencyStatus.FAIL,
                message="Agent session readiness check failed.",
            )
        return ReadinessCheck(status=DependencyStatus.OK)

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
