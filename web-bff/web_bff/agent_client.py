from __future__ import annotations

import os
from collections.abc import Mapping
from json import JSONDecodeError

import httpx
from pydantic import ValidationError

from talk_to_your_stock_shared import (
    AgentMessageRequest,
    AgentMessageResponse,
    ErrorResponse,
    Message,
    Thread,
    User,
)

AGENT_SERVICE_URL_VAR = "AGENT_SERVICE_URL"
AGENT_SERVICE_TIMEOUT_SECONDS = 90


class AgentServiceUnavailable(RuntimeError):
    pass


class AgentServiceResponseError(RuntimeError):
    def __init__(self, *, status_code: int, error: ErrorResponse) -> None:
        super().__init__(error.error.message)
        self.status_code = status_code
        self.error = error


class HttpAgentClient:
    def __init__(self, *, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> HttpAgentClient:
        env = os.environ if environ is None else environ
        base_url = env.get(AGENT_SERVICE_URL_VAR, "").strip()
        if not base_url:
            raise AgentServiceUnavailable(
                f"{AGENT_SERVICE_URL_VAR} is required to call the Agent Service."
            )
        return cls(base_url=base_url)

    def respond_to_user_message(
        self,
        *,
        user: User,
        thread: Thread,
        user_message: Message,
    ) -> AgentMessageResponse:
        request = AgentMessageRequest(
            user_id=user.id,
            thread_id=thread.id,
            user_message_id=user_message.id,
            content=user_message.content,
        )
        try:
            response = httpx.post(
                f"{self._base_url}/v1/internal/agent/respond",
                json=request.model_dump(mode="json"),
                timeout=AGENT_SERVICE_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise AgentServiceUnavailable("Agent Service unavailable.") from exc

        if response.is_error:
            try:
                error = ErrorResponse.model_validate(response.json())
            except (JSONDecodeError, ValidationError, ValueError):
                raise AgentServiceUnavailable(
                    "Agent Service returned an invalid error response."
                ) from None
            status_code = response.status_code
            if status_code not in (502, 503):
                status_code = 502
            raise AgentServiceResponseError(status_code=status_code, error=error)

        try:
            return AgentMessageResponse.model_validate(response.json())
        except (JSONDecodeError, ValidationError, ValueError):
            raise AgentServiceUnavailable(
                "Agent Service returned an invalid response."
            ) from None
