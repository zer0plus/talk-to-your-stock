from __future__ import annotations

import os
import re
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
VALIDATION_FAILURE_COUNT_STATE_KEY = "temp:generate_comps_validation_failures"
VALIDATION_CLARIFICATION = (
    "I couldn't validate those Tickers after one correction. "
    "Please confirm the Target Ticker and Peer Tickers."
)

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

EXPLICIT_PEER_COMPARISON_PATTERN = re.compile(
    (
        r"^\s*(?:please\s+)?compare\s+(?P<target>.+?)\s+"
        r"(?:with|to|against|versus)\s+(?P<peers>.+?)\s*$"
    ),
    flags=re.IGNORECASE | re.DOTALL,
)
AMBIGUOUS_COMPARISON_SUBJECTS = {
    "companies",
    "it",
    "its peers",
    "peer companies",
    "peers",
    "that",
    "them",
    "those",
    "what",
    "which",
    "who",
    "whom",
}
CONVERSATIONAL_COMPARISON_SUBJECTS = {
    "cash flow",
    "earnings",
    "ebit",
    "ebitda",
    "enterprise value",
    "free cash flow",
    "market cap",
    "market capitalization",
    "metric",
    "metrics",
    "p e",
    "pe",
    "price to earnings",
    "revenue",
    "valuation multiple",
    "valuation multiples",
}


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
        self._agent = Agent(
            name=FUNDAMENTAL_ANALYSIS_AGENT_NAME,
            description="Routes fundamental analysis Messages to deterministic Tools.",
            model=model,
            instruction=FUNDAMENTAL_ANALYSIS_INSTRUCTION,
            tools=[self.generate_comps_table],
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

        if _requires_comps_tool(request.content):
            raise AgentRoutingUnavailable(
                "An explicit peer comparison requires a successful Comps Tool result."
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
        try:
            response = await self._comps_client.generate_comps_table(request)
        except CompsToolValidationError as exc:
            failure_count = int(
                tool_context.state.get(VALIDATION_FAILURE_COUNT_STATE_KEY, 0)
            )
            tool_context.state[VALIDATION_FAILURE_COUNT_STATE_KEY] = failure_count + 1
            return {
                "error": exc.error.error.model_dump(mode="json"),
                "retry_allowed": failure_count == 0,
            }
        return response.model_dump(mode="json")


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


def _requires_comps_tool(content: str) -> bool:
    match = EXPLICIT_PEER_COMPARISON_PATTERN.match(content)
    if match is None:
        return False
    subjects = [
        match.group("target"),
        *re.split(r"\s*(?:,|\band\b)\s*", match.group("peers"), flags=re.I),
    ]
    normalized_subjects = [_normalize_comparison_subject(value) for value in subjects]
    return all(
        subject
        and subject not in AMBIGUOUS_COMPARISON_SUBJECTS
        and subject not in CONVERSATIONAL_COMPARISON_SUBJECTS
        for subject in normalized_subjects
    )


def _normalize_comparison_subject(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


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
