from __future__ import annotations

import unittest
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


if __name__ == "__main__":
    unittest.main()
