from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from google.adk.sessions import InMemorySessionService

from agent_service.main import app, get_session_context
from agent_service.session_context import AdkSessionContext


class AgentServiceMessageContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.session_context = AdkSessionContext(
            app_name="talk-to-your-stock",
            session_service=InMemorySessionService(),
        )
        app.dependency_overrides[get_session_context] = lambda: self.session_context

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_agent_service_resumes_complete_thread_message_history(self) -> None:
        user_id = uuid4()
        thread_id = uuid4()
        client = TestClient(app)

        for content in ("Compare AAPL with NVDA", "Now compare it to MSFT"):
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
                "Compare AAPL with NVDA",
                "AgentService: Message receivedAgentService: routing WIP",
                "Now compare it to MSFT",
                "AgentService: Message receivedAgentService: routing WIP",
            ],
        )

    def test_agent_service_accepts_bff_message_request(self) -> None:
        response = TestClient(app).post(
            "/v1/internal/agent/respond",
            json={
                "user_id": str(uuid4()),
                "thread_id": str(uuid4()),
                "user_message_id": str(uuid4()),
                "content": "Compare AAPL with MSFT",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["run"], None)
        self.assertGreater(len(body["content"]), 0)

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
                    "content": "Compare AAPL with MSFT",
                },
            )

        self.assertEqual(response.status_code, 502)
        body = response.json()
        self.assertEqual(body["error"]["code"], "UPSTREAM_ERROR")
        self.assertEqual(body["error"]["message"], "Agent session unavailable.")

    def test_agent_service_production_route_fails_until_real_routing_exists(self) -> None:
        with patch.dict(os.environ, {"TALK_TO_YOUR_STOCK_ENV": "production"}, clear=True):
            response = TestClient(app).post(
                "/v1/internal/agent/respond",
                json={
                    "user_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "user_message_id": str(uuid4()),
                    "content": "Compare AAPL with MSFT",
                },
            )

        self.assertEqual(response.status_code, 502)
        body = response.json()
        self.assertEqual(body["error"]["code"], "UPSTREAM_ERROR")
        self.assertIn("Production Agent routing is not implemented", body["error"]["message"])

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
