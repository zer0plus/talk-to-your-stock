from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from comps_service.main import app
from tests.readiness_fakes import database_connects


class CompsReadinessTest(unittest.TestCase):
    def test_readiness_fails_when_comps_schema_revision_is_stale(self) -> None:
        env = {
            "TALK_TO_YOUR_STOCK_ENV": "local",
            "DATABASE_URL": (
                "postgresql://postgres:postgres@localhost:5432/talk_to_your_stock"
            ),
            "ALPHA_VANTAGE_API_KEY": "test-key",
            "COMPS_SERVICE_INTERNAL_TOKEN": "test-token",
        }

        with (
            patch.dict(os.environ, env, clear=True),
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
        env = {
            "TALK_TO_YOUR_STOCK_ENV": "local",
            "DATABASE_URL": (
                "postgresql://postgres:postgres@localhost:5432/talk_to_your_stock"
            ),
            "ALPHA_VANTAGE_API_KEY": "test-key",
            "COMPS_SERVICE_INTERNAL_TOKEN": "test-token",
        }

        with patch.dict(os.environ, env, clear=True), database_connects():
            response = TestClient(app).get("/v1/ready")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ready")
        self.assertEqual(response.json()["checks"]["configuration"]["status"], "ok")
        self.assertEqual(response.json()["checks"]["database"]["status"], "ok")
        self.assertEqual(
            response.json()["checks"]["run_data_source"],
            {"status": "ok", "message": None},
        )


if __name__ == "__main__":
    unittest.main()
