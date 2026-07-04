from __future__ import annotations

import os
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_service.main import app as agent_app
from comps_service.main import app as comps_app
from talk_to_your_stock_shared import DependencyStatus, ReadinessCheck
from web_bff.main import app as web_bff_app


LOCAL_ENV = {
    "TALK_TO_YOUR_STOCK_ENV": "local",
    "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/talk_to_your_stock",
    "DEV_AUTH_USER_ID": "00000000-0000-0000-0000-000000000001",
    "DEV_AUTH_EMAIL": "dev@example.com",
}


class BackendServiceReadinessTest(unittest.TestCase):
    def test_local_stack_services_report_ready_when_configuration_and_database_pass(
        self,
    ) -> None:
        for service_name, app in (
            ("web-bff", web_bff_app),
            ("agent-service", agent_app),
            ("comps-service", comps_app),
        ):
            with self.subTest(service=service_name), self._service_database_check(
                status=DependencyStatus.OK,
            ):
                response = self._get_ready(app, LOCAL_ENV)

            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["service"], service_name)
            self.assertEqual(body["status"], "ready")
            self.assertEqual(body["checks"]["configuration"]["status"], "ok")
            self.assertEqual(body["checks"]["database"]["status"], "ok")

    def test_database_failure_makes_readiness_not_ready(self) -> None:
        with self._service_database_check(
            status=DependencyStatus.FAIL,
            message="database unavailable",
        ):
            response = self._get_ready(comps_app, LOCAL_ENV)

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "not_ready")
        self.assertEqual(body["checks"]["configuration"]["status"], "ok")
        self.assertEqual(body["checks"]["database"]["status"], "fail")
        self.assertEqual(body["checks"]["database"]["message"], "database unavailable")

    def test_production_agent_readiness_requires_adk_configuration(self) -> None:
        env = {
            "TALK_TO_YOUR_STOCK_ENV": "production",
            "DATABASE_URL": LOCAL_ENV["DATABASE_URL"],
        }

        with self._service_database_check(status=DependencyStatus.OK):
            response = self._get_ready(agent_app, env)

        self.assertEqual(response.status_code, 503)
        message = response.json()["checks"]["configuration"]["message"]
        self.assertIn("GOOGLE_ADK_APP_NAME", message)
        self.assertIn("GOOGLE_API_KEY", message)

    def test_production_comps_readiness_requires_provider_configuration(self) -> None:
        env = {
            "TALK_TO_YOUR_STOCK_ENV": "production",
            "DATABASE_URL": LOCAL_ENV["DATABASE_URL"],
        }

        with self._service_database_check(status=DependencyStatus.OK):
            response = self._get_ready(comps_app, env)

        self.assertEqual(response.status_code, 503)
        message = response.json()["checks"]["configuration"]["message"]
        self.assertIn("ALPHA_VANTAGE_API_KEY", message)

    def _get_ready(self, app: FastAPI, env: dict[str, str]):
        with patch.dict(os.environ, env, clear=True):
            return TestClient(app).get("/v1/ready")

    @contextmanager
    def _service_database_check(
        self,
        *,
        status: DependencyStatus,
        message: str | None = None,
    ) -> Iterator[None]:
        check = ReadinessCheck(status=status, message=message)
        with (
            patch("web_bff.main.check_database", return_value=check),
            patch("agent_service.main.check_database", return_value=check),
            patch("comps_service.main.check_database", return_value=check),
        ):
            yield


if __name__ == "__main__":
    unittest.main()
