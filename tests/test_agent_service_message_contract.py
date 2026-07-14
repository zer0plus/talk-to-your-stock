from __future__ import annotations

import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from agent_service.main import app


class AgentServiceMessageContractTest(unittest.TestCase):
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
