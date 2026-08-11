from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator, Mapping
from contextlib import aclosing
from typing import Any
from uuid import UUID

from google.adk.agents import Agent, BaseAgent, RunConfig
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.runners import Runner
from google.adk.tools import ToolContext
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field

from talk_to_your_stock_shared import (
    AgentMessageRequest,
    AgentMessageResponse,
    AnalysisPeriod,
    ComparisonTakeaway,
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    FailCalculatedRunRequest,
    FinalizeComparisonTakeawayRequest,
    GenerateCompsDraftResponse,
    GenerateCompsToolRequest,
    PeerSelectionMode,
)

from .comps_client import (
    CompsToolClient,
    CompsToolError,
    CompsToolUnavailable,
    CompsToolValidationError,
    HttpCompsToolClient,
)
from .session_context import AdkSessionContext, FUNDAMENTAL_ANALYSIS_AGENT_NAME

GEMINI_MODEL_VAR = "GEMINI_MODEL"
GOOGLE_API_KEY_VAR = "GOOGLE_API_KEY"
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
AGENT_OPERATION_TIMEOUT_SECONDS = 80
FUNDAMENTAL_ROUTER_NAME = "fundamental_analysis_router"
COMPARISON_TAKEAWAY_WRITER_NAME = "comparison_takeaway_writer"
VALIDATION_CLARIFICATION = (
    "I couldn't validate those Tickers after one correction. "
    "Please confirm the Target Ticker and Peer Tickers."
)
logger = logging.getLogger(__name__)


class _ToolInvocationGate:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.validation_failures = 0
        self.completed = False
        self.calculated_run_id: UUID | None = None
        self.run_is_terminal = False


FUNDAMENTAL_ROUTING_INSTRUCTION = """
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
- Do not create a table or claim a Run exists without a successful Tool result.

If a comparison request does not identify both the Target and explicit Peers,
ask one concise clarification question before calling the Tool.
""".strip()

COMPARISON_TAKEAWAY_INSTRUCTION = """
Write the final response for the calculated Comps Table returned by the Tool.

- Treat the Tool result as the authoritative calculated Comps Table. Never
  invent, recalculate, or override final Metrics.
- Author one display-ready Comparison Takeaway using only evidence in that
  Comps Table. Its headline and interpretation are freeform prose, must identify
  the Target Ticker and a supported available Metric when comparable evidence
  exists, and must not repeat Metric values.
- Set Comparison Confidence to limited, moderate, or strong based on the quality
  and completeness of the table evidence. Explain uncertainty in the
  interpretation without adding a separate confidence reason field.
- Never produce a buy, sell, or hold verdict.
""".strip()


class FundamentalAgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)
    comparison_takeaway: ComparisonTakeaway | None = None


class _StagedFundamentalAgent(BaseAgent):
    async def _run_async_impl(
        self,
        ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        router, takeaway_writer = self.sub_agents
        calculated_draft = False
        async with aclosing(router.run_async(ctx)) as events:
            async for event in events:
                yield event
                if _tool_response_from_event(event) is not None:
                    calculated_draft = True
                    break

        if not calculated_draft:
            return

        async with aclosing(takeaway_writer.run_async(ctx)) as events:
            async for event in events:
                yield event


class AgentRoutingUnavailable(RuntimeError):
    pass


class AgentToolError(RuntimeError):
    def __init__(self, *, status_code: int, error: ErrorResponse) -> None:
        super().__init__(error.error.message)
        self.status_code = status_code
        self.error = error


class FundamentalAnalysisAgent:
    def __init__(
        self,
        *,
        model: str | BaseLlm,
        comps_client: CompsToolClient,
    ) -> None:
        self._comps_client = comps_client
        self._tool_invocation_gates: dict[str, _ToolInvocationGate] = {}
        router = Agent(
            name=FUNDAMENTAL_ROUTER_NAME,
            description="Routes fundamental analysis Messages to deterministic Tools.",
            model=model,
            instruction=FUNDAMENTAL_ROUTING_INSTRUCTION,
            tools=[self.generate_comps_table],
            after_model_callback=_keep_only_first_comps_tool_call,
        )
        takeaway_writer = Agent(
            name=COMPARISON_TAKEAWAY_WRITER_NAME,
            description="Authors a response from a calculated Comps Table.",
            model=model,
            instruction=COMPARISON_TAKEAWAY_INSTRUCTION,
            output_schema=FundamentalAgentOutput,
        )
        self._agent = _StagedFundamentalAgent(
            name=FUNDAMENTAL_ANALYSIS_AGENT_NAME,
            sub_agents=[router, takeaway_writer],
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
            try:
                async with asyncio.timeout(AGENT_OPERATION_TIMEOUT_SECONDS):
                    return await self._run_turn(
                        request=request,
                        session_context=session_context,
                    )
            except TimeoutError as exc:
                raise AgentRoutingUnavailable("Agent response timed out.") from exc
        except AgentRoutingUnavailable as exc:
            if invocation_gate.calculated_run_id is None:
                raise
            raise AgentToolError(
                status_code=502,
                error=ErrorResponse(
                    error=ErrorDetail(
                        code=ErrorCode.UPSTREAM_ERROR,
                        message=str(exc),
                        details={
                            "thread_id": str(request.thread_id),
                            "trigger_message_id": str(request.user_message_id),
                        },
                        run_id=invocation_gate.calculated_run_id,
                    )
                ),
            ) from exc
        finally:
            if (
                invocation_gate.calculated_run_id is not None
                and not invocation_gate.run_is_terminal
            ):
                await self._fail_calculated_run(invocation_gate.calculated_run_id)
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
        calculated_tool_response: GenerateCompsDraftResponse | None = None
        final_text: str | None = None
        terminal_validation_error = False
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
                    calculated_tool_response = tool_response
                    continue
                if _is_terminal_validation_error(event):
                    terminal_validation_error = True
                    break
                text = _text_from_event(event)
                if text:
                    final_text = text
        except CompsToolError as exc:
            raise AgentToolError(
                status_code=exc.status_code,
                error=exc.error,
            ) from None
        except CompsToolUnavailable as exc:
            raise AgentRoutingUnavailable(str(exc)) from exc
        except Exception as exc:
            raise AgentRoutingUnavailable("Agent routing unavailable.") from exc
        finally:
            if terminal_validation_error:
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

        if calculated_tool_response is None:
            if not final_text:
                raise AgentRoutingUnavailable("Agent returned no response.")
            return AgentMessageResponse(content=final_text, run=None)

        if not final_text:
            raise AgentRoutingUnavailable("Agent returned no response.")
        try:
            agent_output = FundamentalAgentOutput.model_validate_json(final_text)
        except ValueError as exc:
            raise AgentRoutingUnavailable(
                "Agent returned an invalid structured response."
            ) from exc

        if agent_output.comparison_takeaway is None:
            raise AgentRoutingUnavailable(
                "Agent returned no Comparison Takeaway for the calculated table."
            )
        finalize_request = FinalizeComparisonTakeawayRequest(
            comparison_takeaway=agent_output.comparison_takeaway
        )
        try:
            try:
                finalized = await self._comps_client.finalize_comps_run(
                    calculated_tool_response.run.id,
                    finalize_request,
                )
            except CompsToolUnavailable:
                finalized = await self._comps_client.finalize_comps_run(
                    calculated_tool_response.run.id,
                    finalize_request,
                )
        except CompsToolError as exc:
            run = calculated_tool_response.run
            error = exc.error.error
            raise AgentToolError(
                status_code=exc.status_code,
                error=exc.error.model_copy(
                    update={
                        "error": error.model_copy(
                            update={
                                "details": {
                                    **(error.details or {}),
                                    "thread_id": str(run.thread_id),
                                    "trigger_message_id": str(
                                        run.trigger_message_id
                                    ),
                                },
                                "run_id": run.id,
                            }
                        )
                    }
                ),
            ) from None
        except CompsToolUnavailable as exc:
            raise AgentRoutingUnavailable(str(exc)) from exc
        self._tool_invocation_gates[
            str(request.user_message_id)
        ].run_is_terminal = True
        return AgentMessageResponse(
            content=agent_output.content,
            run=finalized.run,
        )

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
                        "message": (
                            "A Comps Table was already calculated for this Message. "
                            "Use the existing Tool result to write the Comparison "
                            "Takeaway."
                        ),
                    },
                    "retry_allowed": False,
                }
            try:
                try:
                    response = await self._comps_client.generate_comps_table(request)
                except CompsToolUnavailable:
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
            invocation_gate.calculated_run_id = response.run.id
            return response.model_dump(mode="json")

    async def _fail_calculated_run(self, run_id: UUID) -> None:
        try:
            await self._comps_client.fail_comps_run(
                run_id,
                FailCalculatedRunRequest(
                    error_message=(
                        "The Agent could not complete the calculated analysis."
                    )
                ),
            )
        except (CompsToolError, CompsToolUnavailable) as exc:
            logger.error(
                "Calculated Run failure transition unavailable: run_id=%s message=%s",
                run_id,
                str(exc),
            )


def _keep_only_first_comps_tool_call(
    callback_context: Any,
    llm_response: Any,
) -> Any:
    del callback_context
    content = llm_response.content
    if content is None or not content.parts:
        return llm_response

    first_comps_call = next(
        (
            part
            for part in content.parts
            if part.function_call is not None
            and part.function_call.name == "generate_comps_table"
        ),
        None,
    )
    if first_comps_call is None:
        return llm_response
    return llm_response.model_copy(
        update={"content": content.model_copy(update={"parts": [first_comps_call]})}
    )


def _tool_response_from_event(event: Any) -> GenerateCompsDraftResponse | None:
    content = getattr(event, "content", None)
    for part in getattr(content, "parts", ()) or ():
        function_response = getattr(part, "function_response", None)
        if (
            function_response is None
            or function_response.name != "generate_comps_table"
        ):
            continue
        try:
            return GenerateCompsDraftResponse.model_validate(
                dict(function_response.response)
            )
        except (TypeError, ValueError):
            return None
    return None


def _text_from_event(event: Any) -> str | None:
    if getattr(event, "author", None) not in {
        FUNDAMENTAL_ROUTER_NAME,
        COMPARISON_TAKEAWAY_WRITER_NAME,
    }:
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
            and function_response.response.get("error", {}).get("code")
            == "VALIDATION_ERROR"
        ):
            return True
    return False
