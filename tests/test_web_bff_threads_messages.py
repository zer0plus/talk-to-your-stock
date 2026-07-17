from __future__ import annotations

import asyncio
import os
import unittest
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from google.adk.sessions import InMemorySessionService
from pydantic import BaseModel
from unittest.mock import patch

from agent_service.main import app as agent_app
from agent_service.main import get_session_context as get_agent_session_context
from agent_service.session_context import AdkSessionContext
from talk_to_your_stock_shared import (
    Message,
    MessageRole,
    MessageStatus,
    PaginationMeta,
    Run,
    RunStatus,
    Thread,
    User,
)
from tests.live_service import running_service
from web_bff.main import (
    app,
    get_agent_client,
    get_repository,
    get_thread_turn_coordinator,
)
from web_bff.turn_coordinator import ThreadTurnCoordinator


LOCAL_ENV = {
    "TALK_TO_YOUR_STOCK_ENV": "local",
    "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/talk_to_your_stock",
    "DEV_AUTH_USER_ID": "00000000-0000-0000-0000-000000000001",
    "DEV_AUTH_EMAIL": "dev@example.com",
    "AGENT_SERVICE_URL": "http://agent-service.test",
}


class ControlledAgentResponse(BaseModel):
    content: str
    run: Run | None = None


class ControlledAgent:
    def __init__(
        self,
        *,
        repository: RecordingRepository,
        response: ControlledAgentResponse | None = None,
        response_factory: Callable[[Message], ControlledAgentResponse] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._repository = repository
        self._response = response or ControlledAgentResponse(content="Assistant reply.")
        self._response_factory = response_factory
        self._error = error
        self.invocations: list[dict[str, object]] = []

    def respond_to_user_message(
        self,
        *,
        user: User,
        thread: Thread,
        user_message: Message,
    ) -> ControlledAgentResponse:
        self._repository.events.append("agent.invoked")
        self.invocations.append(
            {
                "user_id": user.id,
                "thread_id": thread.id,
                "user_message_id": user_message.id,
                "content": user_message.content,
            }
        )
        if not self._repository.has_message(user_message.id):
            raise AssertionError("User Message was not persisted before Agent call.")
        if self._error is not None:
            raise self._error
        if self._response_factory is not None:
            return self._response_factory(user_message)
        return self._response


class RecordingRepository:
    def __init__(self) -> None:
        self.users: dict[UUID, User] = {}
        self.threads: dict[UUID, Thread] = {}
        self.messages: list[Message] = []
        self.events: list[str] = []

    def upsert_user(self, user: User) -> User:
        self.users[user.id] = user
        return user

    def create_thread(self, *, user_id: UUID, title: str) -> Thread:
        now = _now()
        thread = Thread(
            id=uuid4(),
            user_id=user_id,
            title=title,
            message_count=0,
            created_at=now,
            updated_at=now,
        )
        self.threads[thread.id] = thread
        return thread

    def list_threads(
        self,
        *,
        user_id: UUID,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[Thread], PaginationMeta]:
        threads = [thread for thread in self.threads.values() if thread.user_id == user_id]
        return threads[:limit], PaginationMeta(has_more=False, next_cursor=None)

    def get_thread(self, *, thread_id: UUID, user_id: UUID) -> Thread | None:
        thread = self.threads.get(thread_id)
        if thread is None or thread.user_id != user_id:
            return None
        return thread

    def create_message(
        self,
        *,
        thread_id: UUID,
        role: MessageRole,
        content: str,
        status: MessageStatus,
        run_id: UUID | None = None,
    ) -> Message:
        now = _now()
        message = Message(
            id=uuid4(),
            thread_id=thread_id,
            role=role,
            content=content,
            status=status,
            run_id=run_id,
            created_at=now,
        )
        self.messages.append(message)
        thread = self.threads[thread_id]
        self.threads[thread_id] = thread.model_copy(
            update={
                "message_count": thread.message_count + 1,
                "last_message_at": now,
                "latest_run_id": run_id if run_id is not None else thread.latest_run_id,
                "updated_at": now,
            }
        )
        self.events.append(f"message.created:{role.value}")
        return message

    def list_messages(
        self,
        *,
        thread_id: UUID,
        user_id: UUID,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[Message] | None, PaginationMeta]:
        if self.get_thread(thread_id=thread_id, user_id=user_id) is None:
            return None, PaginationMeta(has_more=False, next_cursor=None)
        messages = [message for message in self.messages if message.thread_id == thread_id]
        return messages[:limit], PaginationMeta(has_more=False, next_cursor=None)

    def has_message(self, message_id: UUID) -> bool:
        return any(message.id == message_id for message in self.messages)


class DelayedFirstMessageRepository(RecordingRepository):
    def __init__(
        self,
        *,
        first_content: str,
        second_agent_invoked: Event,
    ) -> None:
        super().__init__()
        self.first_user_message_saved = Event()
        self._first_content = first_content
        self._second_agent_invoked = second_agent_invoked

    def create_message(
        self,
        *,
        thread_id: UUID,
        role: MessageRole,
        content: str,
        status: MessageStatus,
        run_id: UUID | None = None,
    ) -> Message:
        message = super().create_message(
            thread_id=thread_id,
            role=role,
            content=content,
            status=status,
            run_id=run_id,
        )
        if role == MessageRole.USER and content == self._first_content:
            self.first_user_message_saved.set()
            self._second_agent_invoked.wait(timeout=2)
        return message


class WebBffThreadsMessagesTest(unittest.TestCase):
    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_user_can_create_and_read_owned_threads(self) -> None:
        repository = RecordingRepository()
        client = self._client(repository=repository)

        created = client.post("/v1/threads", json={"title": "AAPL comps"})

        self.assertEqual(created.status_code, 201)
        thread = created.json()["thread"]
        self.assertEqual(thread["title"], "AAPL comps")
        self.assertEqual(thread["user_id"], LOCAL_ENV["DEV_AUTH_USER_ID"])

        listed = client.get("/v1/threads")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([item["id"] for item in listed.json()["threads"]], [thread["id"]])

        fetched = client.get(f"/v1/threads/{thread['id']}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["thread"]["id"], thread["id"])

    def test_posting_message_stores_user_message_before_invoking_agent(self) -> None:
        repository = RecordingRepository()
        agent = ControlledAgent(repository=repository)
        client = self._client(repository=repository, agent=agent)
        thread_id = client.post("/v1/threads", json={"title": "Comps"}).json()["thread"]["id"]

        response = client.post(
            f"/v1/threads/{thread_id}/messages",
            json={"content": "Compare AAPL with MSFT and NVDA"},
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["user_message"]["role"], "user")
        self.assertEqual(body["assistant_message"]["role"], "assistant")
        self.assertEqual(body["assistant_message"]["content"], "Assistant reply.")
        self.assertEqual(
            repository.events,
            ["message.created:user", "agent.invoked", "message.created:assistant"],
        )

    def test_overlapping_messages_keep_product_and_agent_turns_in_the_same_order(
        self,
    ) -> None:
        first_content = "Compare AAPL with MSFT"
        second_content = "Now add NVDA"
        second_agent_invoked = Event()
        repository = DelayedFirstMessageRepository(
            first_content=first_content,
            second_agent_invoked=second_agent_invoked,
        )

        def respond(user_message: Message) -> ControlledAgentResponse:
            if user_message.content == second_content:
                second_agent_invoked.set()
            return ControlledAgentResponse(content=f"Reply to: {user_message.content}")

        agent = ControlledAgent(repository=repository, response_factory=respond)
        turn_coordinator = ThreadTurnCoordinator()
        app.dependency_overrides[get_thread_turn_coordinator] = lambda: turn_coordinator
        first_client = self._client(repository=repository, agent=agent)
        second_client = TestClient(app)
        thread_id = first_client.post(
            "/v1/threads",
            json={"title": "Comps"},
        ).json()["thread"]["id"]

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_response = executor.submit(
                first_client.post,
                f"/v1/threads/{thread_id}/messages",
                json={"content": first_content},
            )
            self.assertTrue(repository.first_user_message_saved.wait(timeout=2))
            second_response = executor.submit(
                second_client.post,
                f"/v1/threads/{thread_id}/messages",
                json={"content": second_content},
            )
            responses = [
                first_response.result(timeout=5),
                second_response.result(timeout=5),
            ]

        listed_messages = first_client.get(
            f"/v1/threads/{thread_id}/messages",
        ).json()["messages"]

        self.assertEqual([response.status_code for response in responses], [201, 201])
        self.assertEqual(
            [message["content"] for message in listed_messages],
            [
                first_content,
                f"Reply to: {first_content}",
                second_content,
                f"Reply to: {second_content}",
            ],
        )
        self.assertEqual(
            [invocation["content"] for invocation in agent.invocations],
            [first_content, second_content],
        )

    def test_posting_message_crosses_real_agent_http_boundary(self) -> None:
        session_context = AdkSessionContext(
            app_name="talk-to-your-stock",
            session_service=InMemorySessionService(),
        )
        agent_app.dependency_overrides[get_agent_session_context] = lambda: session_context
        self.addCleanup(agent_app.dependency_overrides.clear)
        self.addCleanup(get_agent_session_context.cache_clear)

        with running_service(agent_app) as agent_service_url:
            repository = RecordingRepository()
            client = self._client(
                repository=repository,
                override_agent=False,
                env={**LOCAL_ENV, "AGENT_SERVICE_URL": agent_service_url},
            )
            thread_id = client.post(
                "/v1/threads",
                json={"title": "Comps"},
            ).json()["thread"]["id"]

            response = client.post(
                f"/v1/threads/{thread_id}/messages",
                json={"content": "Compare AAPL with MSFT"},
            )

            self.assertEqual(response.status_code, 201)
            body = response.json()
            session = asyncio.run(
                session_context.get_session(
                    user_id=UUID(LOCAL_ENV["DEV_AUTH_USER_ID"]),
                    thread_id=UUID(thread_id),
                )
            )

        assert session is not None
        self.assertEqual(
            [event.invocation_id for event in session.events],
            [body["user_message"]["id"]] * 2,
        )
        self.assertEqual(
            [event.content.parts[0].text for event in session.events],
            ["Compare AAPL with MSFT", body["assistant_message"]["content"]],
        )
        self.assertEqual(
            [message.role for message in repository.messages],
            [MessageRole.USER, MessageRole.ASSISTANT],
        )

    def test_agent_run_response_links_assistant_message_to_run(self) -> None:
        repository = RecordingRepository()
        created_thread_id: UUID | None = None

        def agent_response(user_message: Message) -> ControlledAgentResponse:
            assert created_thread_id is not None
            return ControlledAgentResponse(
                content="Table-backed response.",
                run=_run(
                    thread_id=created_thread_id,
                    trigger_message_id=user_message.id,
                ),
            )

        agent = ControlledAgent(repository=repository, response_factory=agent_response)
        client = self._client(repository=repository, agent=agent)
        created_thread = client.post("/v1/threads", json={"title": "Comps"}).json()[
            "thread"
        ]
        created_thread_id = UUID(created_thread["id"])

        response = client.post(
            f"/v1/threads/{created_thread['id']}/messages",
            json={"content": "Compare TSLA with F and GM"},
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["run"]["trigger_message_id"], body["user_message"]["id"])
        self.assertEqual(body["assistant_message"]["run_id"], body["run"]["id"])
        self.assertEqual(body["events_url"], None)

        fetched = client.get(f"/v1/threads/{created_thread['id']}")
        self.assertEqual(fetched.json()["thread"]["latest_run_id"], body["run"]["id"])

    def test_agent_run_must_belong_to_current_thread_and_user_message(self) -> None:
        repository = RecordingRepository()
        agent = ControlledAgent(
            repository=repository,
            response=ControlledAgentResponse(
                content="Mismatched run.",
                run=_run(thread_id=uuid4(), trigger_message_id=uuid4()),
            ),
        )
        client = self._client(repository=repository, agent=agent)
        thread_id = client.post("/v1/threads", json={"title": "Comps"}).json()["thread"][
            "id"
        ]

        response = client.post(
            f"/v1/threads/{thread_id}/messages",
            json={"content": "Compare AAPL with MSFT"},
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "UPSTREAM_ERROR")
        self.assertIn("invalid Run linkage", response.json()["error"]["message"])
        self.assertEqual([message.role for message in repository.messages], [MessageRole.USER])

    def test_agent_unavailable_returns_clear_error_after_user_message_is_saved(
        self,
    ) -> None:
        from web_bff.agent_client import AgentServiceUnavailable

        repository = RecordingRepository()
        agent = ControlledAgent(
            repository=repository,
            error=AgentServiceUnavailable("Agent Service unavailable."),
        )
        client = self._client(repository=repository, agent=agent)
        thread_id = client.post("/v1/threads", json={"title": "Comps"}).json()["thread"]["id"]

        response = client.post(
            f"/v1/threads/{thread_id}/messages",
            json={"content": "Compare AAPL with MSFT"},
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "UPSTREAM_ERROR")
        self.assertIn("Agent Service unavailable", response.json()["error"]["message"])
        self.assertEqual([message.role for message in repository.messages], [MessageRole.USER])

    def test_missing_agent_configuration_returns_upstream_error(self) -> None:
        repository = RecordingRepository()
        client = self._client(
            repository=repository,
            override_agent=False,
            env={key: value for key, value in LOCAL_ENV.items() if key != "AGENT_SERVICE_URL"},
        )
        thread_id = client.post("/v1/threads", json={"title": "Comps"}).json()["thread"]["id"]

        response = client.post(
            f"/v1/threads/{thread_id}/messages",
            json={"content": "Compare AAPL with MSFT"},
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "UPSTREAM_ERROR")
        self.assertIn("AGENT_SERVICE_URL", response.json()["error"]["message"])

    def test_request_validation_errors_use_error_response_shape(self) -> None:
        repository = RecordingRepository()
        client = self._client(repository=repository)

        response = client.post("/v1/threads", json={"title": ""})

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("details", body["error"])

    def test_client_message_id_is_not_accepted_without_idempotency(self) -> None:
        repository = RecordingRepository()
        client = self._client(repository=repository)
        thread_id = client.post("/v1/threads", json={"title": "Comps"}).json()["thread"]["id"]

        response = client.post(
            f"/v1/threads/{thread_id}/messages",
            json={
                "content": "Compare AAPL with MSFT",
                "client_message_id": "retry-key",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def _client(
        self,
        *,
        repository: RecordingRepository,
        agent: ControlledAgent | None = None,
        override_agent: bool = True,
        env: dict[str, str] | None = None,
    ) -> TestClient:
        app.dependency_overrides[get_repository] = lambda: repository
        if override_agent:
            app.dependency_overrides[get_agent_client] = lambda: agent or ControlledAgent(
                repository=repository
            )
        env_patcher = patch.dict(os.environ, env or LOCAL_ENV, clear=True)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        return TestClient(app)


def _run(*, thread_id: UUID, trigger_message_id: UUID) -> Run:
    now = _now()
    return Run(
        id=uuid4(),
        thread_id=thread_id,
        trigger_message_id=trigger_message_id,
        status=RunStatus.SUCCEEDED,
        target_ticker="AAPL",
        peer_tickers=["MSFT"],
        currency="USD",
        as_of=now,
        created_at=now,
        completed_at=now,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


if __name__ == "__main__":
    unittest.main()
