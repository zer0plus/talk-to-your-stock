from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agent_service.fundamental_agent import FundamentalAnalysisAgent
from agent_service.main import app, get_fundamental_agent, get_session_context
from agent_service.session_context import AdkSessionContext


class ConversationLlm(BaseLlm):
    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        del llm_request, stream
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text="Conversation Response")],
            ),
            partial=False,
        )


class UnexpectedCompsClient:
    async def generate_comps_table(self, request: object) -> None:
        raise AssertionError(f"Unexpected Tool call: {request}")


class AgentServiceMessageContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.session_context = AdkSessionContext(
            app_name="talk-to-your-stock",
            session_service=InMemorySessionService(),
        )
        app.dependency_overrides[get_session_context] = lambda: self.session_context
        self.fundamental_agent = FundamentalAnalysisAgent(
            model=ConversationLlm(model="conversation"),
            comps_client=UnexpectedCompsClient(),
        )
        app.dependency_overrides[get_fundamental_agent] = (
            lambda: self.fundamental_agent
        )

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        get_session_context.cache_clear()
        get_fundamental_agent.cache_clear()

    def test_agent_service_resumes_complete_thread_message_history(self) -> None:
        user_id = uuid4()
        thread_id = uuid4()
        client = TestClient(app)

        for content in ("What is EBITDA?", "How is it used in valuation?"):
            response = client.post(
                "/v1/internal/agent/respond",
                json={
                    "user_id": str(user_id),
                    "thread_id": str(thread_id),
                    "user_message_id": str(uuid4()),
                    "content": content,
                },
            )
            self.assertEqual(response.status_code, 200)

        session = asyncio.run(
            self.session_context.get_session(
                user_id=user_id,
                thread_id=thread_id,
            )
        )
        assert session is not None
        self.assertEqual(
            [event.author for event in session.events],
            ["user", "fundamental_analysis_agent", "user", "fundamental_analysis_agent"],
        )
        self.assertEqual(
            [event.content.parts[0].text for event in session.events],
            [
                "What is EBITDA?",
                "Conversation Response",
                "How is it used in valuation?",
                "Conversation Response",
            ],
        )

    def test_agent_service_serializes_overlapping_messages_in_one_thread(self) -> None:
        async def exercise_overlapping_messages() -> tuple[list[int], list[str]]:
            with tempfile.TemporaryDirectory() as directory:
                session_context = AdkSessionContext.from_database_url(
                    app_name="talk-to-your-stock",
                    database_url=(
                        "sqlite+aiosqlite:///"
                        f"{Path(directory) / 'agent-session-context.sqlite3'}"
                    ),
                )
                await session_context.prepare()
                app.dependency_overrides[get_session_context] = lambda: session_context
                user_id = uuid4()
                thread_id = uuid4()
                transport = httpx.ASGITransport(app=app)

                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://agent-service.test",
                ) as client:
                    responses = await asyncio.gather(
                        *(
                            client.post(
                                "/v1/internal/agent/respond",
                                json={
                                    "user_id": str(user_id),
                                    "thread_id": str(thread_id),
                                    "user_message_id": str(uuid4()),
                                    "content": content,
                                },
                            )
                            for content in (
                                "What is EBITDA?",
                                "How is it used in valuation?",
                            )
                        )
                    )

                session = await session_context.get_session(
                    user_id=user_id,
                    thread_id=thread_id,
                )
                assert session is not None
                event_authors = [event.author for event in session.events]
                await session_context.close()
                return [response.status_code for response in responses], event_authors

        status_codes, event_authors = asyncio.run(exercise_overlapping_messages())

        self.assertEqual(status_codes, [200, 200])
        self.assertEqual(
            event_authors,
            ["user", "fundamental_analysis_agent"] * 2,
        )

    def test_agent_service_accepts_bff_message_request(self) -> None:
        response = TestClient(app).post(
            "/v1/internal/agent/respond",
            json={
                "user_id": str(uuid4()),
                "thread_id": str(uuid4()),
                "user_message_id": str(uuid4()),
                "content": "What is enterprise value?",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["run"], None)
        self.assertEqual(
            body["content"],
            "Conversation Response",
        )

    def test_agent_session_failure_returns_upstream_error(self) -> None:
        session_service = AsyncMock()
        session_service.get_session.side_effect = RuntimeError("database denied")
        app.dependency_overrides[get_session_context] = lambda: AdkSessionContext(
            app_name="talk-to-your-stock",
            session_service=session_service,
        )

        with self.assertLogs("agent_service.main", level="ERROR"):
            response = TestClient(app).post(
                "/v1/internal/agent/respond",
                json={
                    "user_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "user_message_id": str(uuid4()),
                    "content": "What is enterprise value?",
                },
            )

        self.assertEqual(response.status_code, 502)
        body = response.json()
        self.assertEqual(body["error"]["code"], "UPSTREAM_ERROR")
        self.assertEqual(body["error"]["message"], "Agent routing unavailable.")

    def test_missing_database_configuration_returns_upstream_error(self) -> None:
        app.dependency_overrides.pop(get_session_context)
        get_session_context.cache_clear()

        with patch.dict(os.environ, {}, clear=True), TestClient(app) as client:
            response = client.post(
                "/v1/internal/agent/respond",
                json={
                    "user_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "user_message_id": str(uuid4()),
                    "content": "What is enterprise value?",
                },
            )

        self.assertEqual(response.status_code, 502)
        body = response.json()
        self.assertEqual(body["error"]["code"], "UPSTREAM_ERROR")
        self.assertEqual(body["error"]["message"], "DATABASE_URL is required.")

    def test_agent_service_uses_configured_routing_in_production(self) -> None:
        with patch.dict(os.environ, {"TALK_TO_YOUR_STOCK_ENV": "production"}, clear=True):
            response = TestClient(app).post(
                "/v1/internal/agent/respond",
                json={
                    "user_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "user_message_id": str(uuid4()),
                    "content": "What is enterprise value?",
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["content"], "Conversation Response")

    def test_missing_comps_service_configuration_returns_upstream_error(self) -> None:
        app.dependency_overrides.pop(get_fundamental_agent)
        get_fundamental_agent.cache_clear()

        with patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}, clear=True):
            response = TestClient(app).post(
                "/v1/internal/agent/respond",
                json={
                    "user_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "user_message_id": str(uuid4()),
                    "content": "Compare Apple with Microsoft",
                },
            )

        self.assertEqual(response.status_code, 502)
        body = response.json()
        self.assertEqual(body["error"]["code"], "UPSTREAM_ERROR")
        self.assertEqual(body["error"]["message"], "COMPS_SERVICE_URL is required.")

    def test_agent_service_validation_errors_use_error_response_shape(self) -> None:
        response = TestClient(app).post(
            "/v1/internal/agent/respond",
            json={
                "user_id": str(uuid4()),
                "thread_id": str(uuid4()),
                "user_message_id": str(uuid4()),
                "content": "",
            },
        )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("details", body["error"])


if __name__ == "__main__":
    unittest.main()
