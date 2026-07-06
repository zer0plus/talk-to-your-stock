from __future__ import annotations

import os
from collections.abc import Mapping

import httpx

from talk_to_your_stock_shared import (
    AgentMessageRequest,
    AgentMessageResponse,
    Message,
    Thread,
    User,
)

AGENT_SERVICE_URL_VAR = "AGENT_SERVICE_URL"


class AgentServiceUnavailable(RuntimeError):
    pass


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
                timeout=30,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AgentServiceUnavailable(
                f"Agent Service returned HTTP {exc.response.status_code}."
            ) from exc
        except httpx.HTTPError as exc:
            raise AgentServiceUnavailable("Agent Service unavailable.") from exc

        return AgentMessageResponse.model_validate(response.json())
