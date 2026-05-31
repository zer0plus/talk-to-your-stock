from __future__ import annotations

import inspect
from typing import Any
from uuid import uuid4

from google.adk import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agent_service.fundamental_agent.system_prompt import SYSTEM_INSTRUCTIONS
from agent_service.fundamental_agent.tools import CompsToolClient
from agent_service.settings import settings
from talk_to_your_stock_shared import AgentRequest, AgentResponse, GenerateCompsToolRequest, GenerateCompsToolResponse


class FundamentalAgentRunner:
    """Google ADK-backed MVP fundamental analysis agent runner."""

    def __init__(self, comps_tool: CompsToolClient | None = None) -> None:
        settings.validate_gemini_credentials()
        self.comps_tool = comps_tool or CompsToolClient()

    async def respond(self, request: AgentRequest) -> AgentResponse:
        tool_response: GenerateCompsToolResponse | None = None

        def generate_comps_table(target_ticker: str, peer_tickers: list[str]) -> dict[str, Any]:
            """Generate a deterministic trading comps table.

            Args:
                target_ticker: The primary company ticker to analyze, for example GOOGL.
                peer_tickers: Comparable company tickers. If the user supplied multiple tickers,
                    use every ticker after the first one as peers.
            """
            nonlocal tool_response
            tool_response = self.comps_tool.generate_comps_table(
                GenerateCompsToolRequest(
                    invocation_id=uuid4(),
                    thread_id=request.thread_id,
                    trigger_message_id=request.trigger_message_id,
                    target_ticker=target_ticker.upper(),
                    peer_tickers=[ticker.upper() for ticker in peer_tickers],
                    currency="USD",
                )
            )
            return tool_response.model_dump(mode="json")

        agent = Agent(
            name="fundamental_analysis_agent",
            model=settings.gemini_model,
            description="Fundamental analysis and trading comps agent for TalkToYourStock.",
            instruction=SYSTEM_INSTRUCTIONS,
            tools=[generate_comps_table],
        )
        session_service = InMemorySessionService()
        session = session_service.create_session(
            app_name=settings.app_name,
            user_id="demo-user",
            session_id=str(request.thread_id),
        )
        if inspect.isawaitable(session):
            session = await session

        runner = Runner(
            app_name=settings.app_name,
            agent=agent,
            session_service=session_service,
        )
        user_message = types.Content(
            role="user",
            parts=[types.Part(text=request.content)],
        )

        final_text = ""
        async for event in runner.run_async(
            user_id="demo-user",
            session_id=session.id,
            new_message=user_message,
        ):
            if not event.content or not event.content.parts:
                continue
            text = "".join(part.text or "" for part in event.content.parts)
            if text:
                final_text = text

        if not final_text:
            raise RuntimeError("Google ADK completed without a final assistant text response.")

        return AgentResponse(
            assistant_content=final_text,
            run=tool_response.run if tool_response else None,
            table=tool_response.table if tool_response else None,
        )
