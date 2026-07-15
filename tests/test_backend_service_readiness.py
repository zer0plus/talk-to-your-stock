from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.adk.sessions import InMemorySessionService

from agent_service.main import app as agent_app, get_session_context
from agent_service.session_context import AdkSessionContext, AgentSessionUnavailable
from comps_service.main import app as comps_app
from tests.readiness_fakes import database_connects, database_unavailable
from web_bff.main import app as web_bff_app


LOCAL_ENV = {
    "TALK_TO_YOUR_STOCK_ENV": "local",
    "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/talk_to_your_stock",
    "DEV_AUTH_USER_ID": "00000000-0000-0000-0000-000000000001",
    "DEV_AUTH_EMAIL": "dev@example.com",
    "AGENT_SERVICE_URL": "http://agent-service:8001",
    "COMPS_SERVICE_INTERNAL_TOKEN": "local-comps-token",
    "ALPHA_VANTAGE_API_KEY": "local-alpha-vantage-key",
}


class BackendServiceReadinessTest(unittest.TestCase):
    def setUp(self) -> None:
        agent_response = httpx.Response(
            200,
            request=httpx.Request("GET", "http://agent-service:8001/v1/ready"),
            json={"status": "ready"},
        )
        self.agent_get_patcher = patch("httpx.get", return_value=agent_response)
        self.agent_get_patcher.start()
        self.addCleanup(self.agent_get_patcher.stop)
        self.agent_session_context = AdkSessionContext(
            app_name="talk-to-your-stock",
            session_service=InMemorySessionService(),
        )
        agent_app.dependency_overrides[get_session_context] = (
            lambda: self.agent_session_context
        )
        self.addCleanup(agent_app.dependency_overrides.clear)

    def test_readiness_openapi_documents_503_response(self) -> None:
        for service_name, app in (
            ("web-bff", web_bff_app),
            ("agent-service", agent_app),
            ("comps-service", comps_app),
        ):
            with self.subTest(service=service_name):
                response = TestClient(app).get("/openapi.json")

            self.assertEqual(response.status_code, 200)
            ready_responses = response.json()["paths"]["/v1/ready"]["get"]["responses"]
            self.assertIn("503", ready_responses)
            schema = ready_responses["503"]["content"]["application/json"]["schema"]
            self.assertEqual(schema["$ref"], "#/components/schemas/ReadinessResponse")

    def test_local_stack_services_report_ready_when_configuration_and_database_pass(
        self,
    ) -> None:
        for service_name, app in (
            ("web-bff", web_bff_app),
            ("agent-service", agent_app),
            ("comps-service", comps_app),
        ):
            with self.subTest(service=service_name), database_connects():
                response = self._get_ready(app, LOCAL_ENV)

            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["service"], service_name)
            self.assertEqual(body["status"], "ready")
            self.assertEqual(body["checks"]["configuration"]["status"], "ok")
            self.assertEqual(body["checks"]["database"]["status"], "ok")

    def test_web_bff_checks_agent_readiness_through_real_http_boundary(self) -> None:
        self.agent_get_patcher.stop()

        with running_service(agent_app) as agent_service_url:
            env = {**LOCAL_ENV, "AGENT_SERVICE_URL": agent_service_url}
            with patch.dict(os.environ, env, clear=True), database_connects():
                response = TestClient(web_bff_app).get("/v1/ready")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["checks"]["agent_service"]["status"], "ok")

    def test_database_failure_makes_readiness_not_ready(self) -> None:
        with (
            database_unavailable("database unavailable"),
            self.assertLogs("talk_to_your_stock_shared.readiness", level="ERROR") as logs,
        ):
            response = self._get_ready(comps_app, LOCAL_ENV)

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "not_ready")
        self.assertEqual(body["checks"]["configuration"]["status"], "ok")
        self.assertEqual(body["checks"]["database"]["status"], "fail")
        self.assertIn("database unavailable", body["checks"]["database"]["message"])
        self.assertTrue(
            any("database unavailable" in message for message in logs.output)
        )

    def test_agent_session_failure_makes_agent_readiness_not_ready(self) -> None:
        session_service = AsyncMock()
        session_service.get_session.side_effect = RuntimeError("session database denied")
        agent_app.dependency_overrides[get_session_context] = lambda: AdkSessionContext(
            app_name="talk-to-your-stock",
            session_service=session_service,
        )

        with database_connects():
            response = self._get_ready(agent_app, LOCAL_ENV)

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "not_ready")
        self.assertEqual(body["checks"]["agent_session"]["status"], "fail")
        self.assertEqual(
            body["checks"]["agent_session"]["message"],
            "Agent session readiness check failed.",
        )

    def test_agent_startup_prepares_database_session_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "agent-session-context.sqlite3"
            session_context = AdkSessionContext.from_database_url(
                app_name="talk-to-your-stock",
                database_url=f"sqlite+aiosqlite:///{database_path}",
            )
            agent_app.dependency_overrides[get_session_context] = (
                lambda: session_context
            )

            self.assertFalse(database_path.exists())
            with TestClient(agent_app):
                self.assertTrue(database_path.exists())

    def test_agent_startup_fails_when_session_schema_cannot_be_prepared(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            blocked_parent = Path(directory) / "not-a-directory"
            blocked_parent.write_text("blocks SQLite database creation")
            session_context = AdkSessionContext.from_database_url(
                app_name="talk-to-your-stock",
                database_url=(
                    "sqlite+aiosqlite:///"
                    f"{blocked_parent / 'agent-session-context.sqlite3'}"
                ),
            )
            agent_app.dependency_overrides[get_session_context] = (
                lambda: session_context
            )

            with self.assertRaises(AgentSessionUnavailable):
                with TestClient(agent_app):
                    pass

    def test_missing_database_configuration_makes_agent_readiness_not_ready(
        self,
    ) -> None:
        agent_app.dependency_overrides.clear()
        get_session_context.cache_clear()

        response = self._get_ready(
            agent_app,
            {"TALK_TO_YOUR_STOCK_ENV": "local"},
        )

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "not_ready")
        self.assertEqual(body["checks"]["agent_session"]["status"], "fail")

    def test_invalid_database_url_makes_agent_readiness_not_ready(self) -> None:
        agent_app.dependency_overrides.clear()
        get_session_context.cache_clear()

        response = self._get_ready(
            agent_app,
            {**LOCAL_ENV, "DATABASE_URL": "not-a-database-url"},
        )

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "not_ready")
        self.assertEqual(body["checks"]["agent_session"]["status"], "fail")
        self.assertEqual(
            body["checks"]["agent_session"]["message"],
            "Agent session readiness check failed.",
        )

    def test_production_database_failure_uses_sanitized_readiness_message(self) -> None:
        env = {
            "TALK_TO_YOUR_STOCK_ENV": "production",
            "DATABASE_URL": LOCAL_ENV["DATABASE_URL"],
            "MANAGED_AUTH_JWKS_URL": "https://auth.example.com/.well-known/jwks.json",
            "MANAGED_AUTH_ISSUER": "https://auth.example.com",
            "MANAGED_AUTH_AUDIENCE": "talk-to-your-stock",
            "AGENT_SERVICE_URL": "http://agent-service:8001",
        }

        with (
            database_unavailable("password auth failed for user postgres"),
            self.assertLogs("talk_to_your_stock_shared.readiness", level="ERROR") as logs,
        ):
            response = self._get_ready(web_bff_app, env)

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "not_ready")
        self.assertEqual(body["checks"]["configuration"]["status"], "fail")
        self.assertIn(
            "JWT verification is not implemented",
            body["checks"]["configuration"]["message"],
        )
        self.assertEqual(body["checks"]["database"]["status"], "fail")
        self.assertEqual(
            body["checks"]["database"]["message"],
            "PostgreSQL readiness check failed.",
        )
        self.assertNotIn("password auth failed", body["checks"]["database"]["message"])
        self.assertTrue(
            any(
                "password auth failed for user postgres" in message
                for message in logs.output
            )
        )

    def test_invalid_environment_database_failure_uses_sanitized_readiness_message(
        self,
    ) -> None:
        env = {
            "TALK_TO_YOUR_STOCK_ENV": "prodution",
            "DATABASE_URL": LOCAL_ENV["DATABASE_URL"],
        }

        with (
            database_unavailable("password auth failed for user postgres"),
            self.assertLogs("talk_to_your_stock_shared.readiness", level="ERROR") as logs,
        ):
            response = self._get_ready(comps_app, env)

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "not_ready")
        self.assertEqual(body["checks"]["configuration"]["status"], "fail")
        self.assertEqual(body["checks"]["database"]["status"], "fail")
        self.assertEqual(
            body["checks"]["database"]["message"],
            "PostgreSQL readiness check failed.",
        )
        self.assertNotIn("password auth failed", body["checks"]["database"]["message"])
        self.assertTrue(
            any(
                "password auth failed for user postgres" in message
                for message in logs.output
            )
        )

    def test_production_agent_readiness_requires_adk_configuration(self) -> None:
        env = {
            "TALK_TO_YOUR_STOCK_ENV": "production",
            "DATABASE_URL": LOCAL_ENV["DATABASE_URL"],
        }

        with database_connects():
            response = self._get_ready(agent_app, env)

        self.assertEqual(response.status_code, 503)
        message = response.json()["checks"]["configuration"]["message"]
        self.assertIn("GOOGLE_ADK_APP_NAME", message)
        self.assertIn("GOOGLE_API_KEY", message)
        self.assertIn("COMPS_SERVICE_URL", message)
        self.assertIn("COMPS_SERVICE_INTERNAL_TOKEN", message)

    def test_production_agent_readiness_fails_until_real_routing_exists(self) -> None:
        env = {
            "TALK_TO_YOUR_STOCK_ENV": "production",
            "DATABASE_URL": LOCAL_ENV["DATABASE_URL"],
            "GOOGLE_ADK_APP_NAME": "talk-to-your-stock",
            "GOOGLE_API_KEY": "test-key",
            "COMPS_SERVICE_URL": "http://comps-service:8002",
            "COMPS_SERVICE_INTERNAL_TOKEN": "test-comps-token",
        }

        with database_connects():
            response = self._get_ready(agent_app, env)

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "not_ready")
        self.assertEqual(body["checks"]["configuration"]["status"], "ok")
        self.assertEqual(body["checks"]["database"]["status"], "ok")
        self.assertEqual(body["checks"]["agent_routing"]["status"], "fail")

    def test_production_comps_readiness_requires_provider_configuration(self) -> None:
        env = {
            "TALK_TO_YOUR_STOCK_ENV": "production",
            "DATABASE_URL": LOCAL_ENV["DATABASE_URL"],
        }

        with database_connects():
            response = self._get_ready(comps_app, env)

        self.assertEqual(response.status_code, 503)
        message = response.json()["checks"]["configuration"]["message"]
        self.assertIn("ALPHA_VANTAGE_API_KEY", message)
        self.assertIn("COMPS_SERVICE_INTERNAL_TOKEN", message)

    def test_local_comps_readiness_requires_comps_configuration(self) -> None:
        env = dict(LOCAL_ENV)
        del env["COMPS_SERVICE_INTERNAL_TOKEN"]
        del env["ALPHA_VANTAGE_API_KEY"]

        with database_connects():
            response = self._get_ready(comps_app, env)

        self.assertEqual(response.status_code, 503)
        message = response.json()["checks"]["configuration"]["message"]
        self.assertIn("COMPS_SERVICE_INTERNAL_TOKEN", message)
        self.assertIn("ALPHA_VANTAGE_API_KEY", message)

    def _get_ready(self, app: FastAPI, env: dict[str, str]):
        with patch.dict(os.environ, env, clear=True):
            return TestClient(app).get("/v1/ready")


@contextmanager
def running_service(app: FastAPI) -> Iterator[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="critical", access_log=False, ws="none")
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [sock]},
        daemon=True,
    )
    thread.start()

    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        raise RuntimeError("Test service failed to start.")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()


if __name__ == "__main__":
    unittest.main()
