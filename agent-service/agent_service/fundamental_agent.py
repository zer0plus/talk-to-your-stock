from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from google.adk.agents import Agent, RunConfig
from google.adk.models.base_llm import BaseLlm
from google.adk.runners import Runner
from google.adk.tools import ToolContext
from google.genai import types

from talk_to_your_stock_shared import (
    AgentMessageRequest,
    AgentMessageResponse,
    AnalysisPeriod,
    GenerateCompsToolRequest,
    GenerateCompsToolResponse,
    PeerSelectionMode,
)

from .comps_client import (
    CompsToolClient,
    CompsToolUnavailable,
    CompsToolValidationError,
    HttpCompsToolClient,
)
from .session_context import AdkSessionContext, FUNDAMENTAL_ANALYSIS_AGENT_NAME

GEMINI_MODEL_VAR = "GEMINI_MODEL"
GOOGLE_API_KEY_VAR = "GOOGLE_API_KEY"
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
VALIDATION_CLARIFICATION = (
    "I couldn't validate those Tickers after one correction. "
    "Please confirm the Target Ticker and Peer Tickers."
)


class _ToolInvocationGate:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.validation_failures = 0
        self.completed = False


FUNDAMENTAL_ANALYSIS_INSTRUCTION = """
You are the Fundamental Analysis Agent for TalkToYourStock.

For conversational finance or fundamentals questions, answer directly without
calling a Tool or creating a Run.

For a request that compares one company with explicit peer companies:
- Convert company names or user language into canonical exchange Tickers.
- Call generate_comps_table exactly once with one target_ticker and all explicit
  peer_tickers. The Tool fixes Peer Selection Mode to user_supplied and Analysis
  Period to latest.
- If the Tool returns a pre-Run validation error with retry_allowed=true, make at
  most one corrected Tool call. If retry_allowed=false, ask the User to confirm
  the Target Ticker and Peer Tickers; do not call the Tool again.
- Treat the successful Tool result as the complete, authoritative Comps output.
  Never invent, recalculate, or override final Metrics.
- Do not create a table or claim a Run exists without a successful Tool result.

If a comparison request does not identify both the Target and explicit Peers,
ask one concise clarification question before calling the Tool.
""".strip()

class AgentRoutingUnavailable(RuntimeError):
    pass


class FundamentalAnalysisAgent:
    def __init__(
        self,
        *,
        model: str | BaseLlm,
        comps_client: CompsToolClient,
    ) -> None:
        self._comps_client = comps_client
        self._tool_invocation_gates: dict[str, _ToolInvocationGate] = {}
        self._agent = Agent(
            name=FUNDAMENTAL_ANALYSIS_AGENT_NAME,
            description="Routes fundamental analysis Messages to deterministic Tools.",
            model=model,
            instruction=FUNDAMENTAL_ANALYSIS_INSTRUCTION,
            tools=[self.generate_comps_table],
            after_model_callback=_keep_first_comps_tool_call,
        )

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> FundamentalAnalysisAgent:
        env = os.environ if environ is None else environ
        if not env.get(GOOGLE_API_KEY_VAR, "").strip():
            raise AgentRoutingUnavailable(f"{GOOGLE_API_KEY_VAR} is required.")
        model = env.get(GEMINI_MODEL_VAR, "").strip() or DEFAULT_GEMINI_MODEL
        try:
            return cls(
                model=model,
                comps_client=HttpCompsToolClient.from_env(env),
            )
        except CompsToolUnavailable as exc:
            raise AgentRoutingUnavailable(str(exc)) from exc

    async def respond(
        self,
        *,
        request: AgentMessageRequest,
        session_context: AdkSessionContext,
    ) -> AgentMessageResponse:
        invocation_key = str(request.user_message_id)
        invocation_gate = _ToolInvocationGate()
        self._tool_invocation_gates[invocation_key] = invocation_gate
        try:
            return await self._run_turn(
                request=request,
                session_context=session_context,
            )
        finally:
            if self._tool_invocation_gates.get(invocation_key) is invocation_gate:
                self._tool_invocation_gates.pop(invocation_key)

    async def _run_turn(
        self,
        *,
        request: AgentMessageRequest,
        session_context: AdkSessionContext,
    ) -> AgentMessageResponse:
        runner = Runner(
            app_name=session_context.app_name,
            agent=self._agent,
            session_service=session_context.session_service,
            auto_create_session=True,
        )
        successful_tool_response: GenerateCompsToolResponse | None = None
        final_text: str | None = None
        terminal_validation_error = False
        tool_succeeded = False
        event_stream = runner.run_async(
            user_id=str(request.user_id),
            session_id=str(request.thread_id),
            invocation_id=str(request.user_message_id),
            new_message=types.Content(
                role="user",
                parts=[types.Part(text=request.content)],
            ),
            run_config=RunConfig(max_llm_calls=3),
        )

        try:
            async for event in event_stream:
                tool_response = _tool_response_from_event(event)
                if tool_response is not None:
                    successful_tool_response = tool_response
                    tool_succeeded = True
                    break
                if _is_terminal_validation_error(event):
                    terminal_validation_error = True
                    break
                text = _text_from_event(event)
                if text:
                    final_text = text
        except CompsToolUnavailable as exc:
            raise AgentRoutingUnavailable(str(exc)) from exc
        except Exception as exc:
            raise AgentRoutingUnavailable("Agent routing unavailable.") from exc
        finally:
            if terminal_validation_error or tool_succeeded:
                await event_stream.aclose()

        if terminal_validation_error:
            session = await session_context.get_session(
                user_id=request.user_id,
                thread_id=request.thread_id,
            )
            if session is None:
                raise AgentRoutingUnavailable("Agent session unavailable.")
            await session_context.complete_turn(
                session=session,
                user_message_id=request.user_message_id,
                assistant_content=VALIDATION_CLARIFICATION,
            )
            return AgentMessageResponse(content=VALIDATION_CLARIFICATION, run=None)

        if successful_tool_response is not None:
            content = _tool_backed_content(successful_tool_response)
            session = await session_context.get_session(
                user_id=request.user_id,
                thread_id=request.thread_id,
            )
            if session is None:
                raise AgentRoutingUnavailable("Agent session unavailable.")
            await session_context.complete_turn(
                session=session,
                user_message_id=request.user_message_id,
                assistant_content=content,
            )
            return AgentMessageResponse(
                content=content,
                run=successful_tool_response.run,
            )

        if not final_text:
            raise AgentRoutingUnavailable("Agent returned no response.")
        return AgentMessageResponse(content=final_text, run=None)

    async def generate_comps_table(
        self,
        target_ticker: str,
        peer_tickers: list[str],
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Generate a deterministic Comps Table for one Target and explicit Peers."""
        request = GenerateCompsToolRequest(
            invocation_id=UUID(tool_context.invocation_id),
            thread_id=UUID(tool_context.session.id),
            trigger_message_id=UUID(tool_context.invocation_id),
            target_ticker=target_ticker,
            peer_tickers=peer_tickers,
            peer_selection_mode=PeerSelectionMode.USER_SUPPLIED,
            analysis_period=AnalysisPeriod.LATEST,
        )
        invocation_gate = self._tool_invocation_gates[str(tool_context.invocation_id)]
        async with invocation_gate.lock:
            if invocation_gate.completed:
                return {
                    "error": {
                        "code": "CONFLICT",
                        "message": "The Comps Tool invocation limit was reached.",
                    },
                    "retry_allowed": False,
                }
            try:
                response = await self._comps_client.generate_comps_table(request)
            except CompsToolValidationError as exc:
                invocation_gate.validation_failures += 1
                retry_allowed = invocation_gate.validation_failures == 1
                invocation_gate.completed = not retry_allowed
                return {
                    "error": exc.error.error.model_dump(mode="json"),
                    "retry_allowed": retry_allowed,
                }
            invocation_gate.completed = True
            return response.model_dump(mode="json")


def _keep_first_comps_tool_call(callback_context: Any, llm_response: Any) -> Any:
    del callback_context
    content = llm_response.content
    if content is None or not content.parts:
        return llm_response

    found_tool_call = False
    parts = []
    for part in content.parts:
        function_call = part.function_call
        if function_call is None or function_call.name != "generate_comps_table":
            parts.append(part)
        elif not found_tool_call:
            found_tool_call = True
            parts.append(part)

    if len(parts) == len(content.parts):
        return llm_response
    return llm_response.model_copy(
        update={"content": content.model_copy(update={"parts": parts})}
    )


def _tool_response_from_event(event: Any) -> GenerateCompsToolResponse | None:
    content = getattr(event, "content", None)
    for part in getattr(content, "parts", ()) or ():
        function_response = getattr(part, "function_response", None)
        if (
            function_response is None
            or function_response.name != "generate_comps_table"
        ):
            continue
        try:
            return GenerateCompsToolResponse.model_validate(
                dict(function_response.response)
            )
        except (TypeError, ValueError):
            return None
    return None


def _text_from_event(event: Any) -> str | None:
    if getattr(event, "author", None) != FUNDAMENTAL_ANALYSIS_AGENT_NAME:
        return None
    content = getattr(event, "content", None)
    text_parts = [
        part.text
        for part in getattr(content, "parts", ()) or ()
        if getattr(part, "text", None)
    ]
    text = "".join(text_parts).strip()
    return text or None


def _is_terminal_validation_error(event: Any) -> bool:
    content = getattr(event, "content", None)
    for part in getattr(content, "parts", ()) or ():
        function_response = getattr(part, "function_response", None)
        if (
            function_response is not None
            and function_response.name == "generate_comps_table"
            and function_response.response.get("retry_allowed") is False
        ):
            return True
    return False


def _tool_backed_content(response: GenerateCompsToolResponse) -> str:
    peers = _format_tickers(response.run.peer_tickers)
    return (
        f"Generated the Comps Table for {response.run.target_ticker} with {peers} "
        f"(Run {response.run.id})."
    )


def _format_tickers(tickers: list[str]) -> str:
    if len(tickers) == 1:
        return tickers[0]
    if len(tickers) == 2:
        return " and ".join(tickers)
    return f"{', '.join(tickers[:-1])}, and {tickers[-1]}"
