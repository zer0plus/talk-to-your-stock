from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import httpx

from talk_to_your_stock_shared import Message, MessageRole, MessageStatus, Thread, User
from web_bff.agent_client import AgentServiceUnavailable, HttpAgentClient


class WebBffAgentClientTest(unittest.TestCase):
    def test_malformed_agent_response_is_wrapped_as_upstream_error(self) -> None:
        client = HttpAgentClient(base_url="http://agent-service.test")
        now = datetime.now(timezone.utc)
        user = User(
            id=uuid4(),
            email="dev@example.com",
            created_at=now,
            updated_at=now,
        )
        thread = Thread(
            id=uuid4(),
            user_id=user.id,
            title="Comps",
            message_count=1,
            created_at=now,
            updated_at=now,
        )
        message = Message(
            id=uuid4(),
            thread_id=thread.id,
            role=MessageRole.USER,
            content="Compare AAPL with MSFT",
            status=MessageStatus.COMPLETE,
            created_at=now,
        )

        malformed_responses = [
            httpx.Response(
                200,
                content=b"not-json",
                request=httpx.Request("POST", "http://agent-service.test/v1/internal/agent/respond"),
            ),
            httpx.Response(
                200,
                json={"run": None},
                request=httpx.Request("POST", "http://agent-service.test/v1/internal/agent/respond"),
            ),
        ]

        for response in malformed_responses:
            with self.subTest(response=response.content):
                with patch("web_bff.agent_client.httpx.post", return_value=response):
                    with self.assertRaises(AgentServiceUnavailable) as context:
                        client.respond_to_user_message(
                            user=user,
                            thread=thread,
                            user_message=message,
                        )

                self.assertIn("invalid response", str(context.exception))


if __name__ == "__main__":
    unittest.main()
