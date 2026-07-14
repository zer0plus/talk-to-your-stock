from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_service.main import app as agent_app
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

    def test_production_agent_readiness_accepts_required_configuration(self) -> None:
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

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["checks"]["configuration"]["status"], "ok")
        self.assertEqual(body["checks"]["database"]["status"], "ok")

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


if __name__ == "__main__":
    unittest.main()
