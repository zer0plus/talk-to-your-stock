from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from comps_service.main import app
from tests.readiness_fakes import database_connects

COMPS_ENV = {
    "TALK_TO_YOUR_STOCK_ENV": "local",
    "DATABASE_URL": (
        "postgresql://postgres:postgres@localhost:5432/talk_to_your_stock"
    ),
    "ALPHA_VANTAGE_API_KEY": "test-key",
    "COMPS_SERVICE_INTERNAL_TOKEN": "test-token",
}


class CompsReadinessTest(unittest.TestCase):
    def test_readiness_fails_when_comps_schema_revision_is_stale(self) -> None:
        with (
            patch.dict(os.environ, COMPS_ENV, clear=True),
            database_connects(schema_revision="0001_web_bff_schema"),
        ):
            response = TestClient(app).get("/v1/ready")

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["status"], "not_ready")
        self.assertEqual(response.json()["checks"]["database"]["status"], "fail")
        self.assertIn(
            "required revision",
            response.json()["checks"]["database"]["message"],
        )

    def test_readiness_reports_real_run_data_source_available(self) -> None:
        with patch.dict(os.environ, COMPS_ENV, clear=True), database_connects():
            response = TestClient(app).get("/v1/ready")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ready")
        self.assertEqual(response.json()["checks"]["configuration"]["status"], "ok")
        self.assertEqual(response.json()["checks"]["database"]["status"], "ok")
        self.assertEqual(
            response.json()["checks"]["run_data_source"],
            {"status": "ok", "message": None},
        )

    def test_readiness_rejects_invalid_provider_runtime_settings(self) -> None:
        cases = (
            (
                "ALPHA_VANTAGE_TIMEOUT_SECONDS",
                "not-a-number",
                "ALPHA_VANTAGE_TIMEOUT_SECONDS must be a number of seconds.",
            ),
            (
                "ALPHA_VANTAGE_TIMEOUT_SECONDS",
                "-1",
                "ALPHA_VANTAGE_TIMEOUT_SECONDS must not be negative.",
            ),
            (
                "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS",
                "not-a-number",
                (
                    "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS "
                    "must be a number of seconds."
                ),
            ),
            (
                "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS",
                "-1",
                (
                    "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS "
                    "must not be negative."
                ),
            ),
        )

        for name, value, message in cases:
            with self.subTest(name=name, value=value):
                env = {**COMPS_ENV, name: value}
                with patch.dict(os.environ, env, clear=True), database_connects():
                    response = TestClient(app).get("/v1/ready")

                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(response.json()["status"], "not_ready")
                self.assertEqual(
                    response.json()["checks"]["run_data_source"],
                    {"status": "fail", "message": message},
                )


if __name__ == "__main__":
    unittest.main()
