from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from google.adk.events import Event
from google.genai import types

from agent_service.session_context import AdkSessionContext


class AgentSessionContextTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_invocation_and_result_survive_context_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_url = (
                "sqlite+aiosqlite:///"
                f"{Path(directory) / 'agent-session-context.sqlite3'}"
            )
            user_id = uuid4()
            thread_id = uuid4()
            invocation_id = str(uuid4())
            first_context = AdkSessionContext.from_database_url(
                app_name="talk-to-your-stock",
                database_url=database_url,
            )

            await first_context.record_event(
                user_id=user_id,
                thread_id=thread_id,
                event=Event(
                    invocation_id=invocation_id,
                    author="fundamental_analysis_agent",
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part.from_function_call(
                                name="generate_comps_table",
                                args={"target_ticker": "AAPL", "peer_tickers": ["MSFT"]},
                            )
                        ],
                    ),
                ),
            )
            await first_context.record_event(
                user_id=user_id,
                thread_id=thread_id,
                event=Event(
                    invocation_id=invocation_id,
                    author="generate_comps_table",
                    content=types.Content(
                        role="user",
                        parts=[
                            types.Part.from_function_response(
                                name="generate_comps_table",
                                response={"run_id": str(uuid4()), "status": "succeeded"},
                            )
                        ],
                    ),
                ),
            )
            await first_context.close()

            reloaded_context = AdkSessionContext.from_database_url(
                app_name="talk-to-your-stock",
                database_url=database_url,
            )
            session = await reloaded_context.get_session(
                user_id=user_id,
                thread_id=thread_id,
            )
            assert session is not None

            tool_call = session.events[0].content.parts[0].function_call
            tool_result = session.events[1].content.parts[0].function_response
            self.assertEqual(tool_call.name, "generate_comps_table")
            self.assertEqual(tool_call.args["target_ticker"], "AAPL")
            self.assertEqual(tool_result.name, "generate_comps_table")
            self.assertEqual(tool_result.response["status"], "succeeded")
            await reloaded_context.close()


if __name__ == "__main__":
    unittest.main()
