from __future__ import annotations

import asyncio
import unittest
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agent_service.comps_client import CompsToolUnavailable, CompsToolValidationError
from agent_service.fundamental_agent import FundamentalAnalysisAgent
from agent_service.main import app, get_fundamental_agent, get_session_context
from agent_service.session_context import AdkSessionContext
from talk_to_your_stock_shared import (
    CompsRow,
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    GenerateCompsToolRequest,
    GenerateCompsToolResponse,
    MinMedianMax,
    Run,
    RunStatus,
    RunTableResponse,
    TraceResponse,
)
from talk_to_your_stock_shared.schemas import RunTableSummary, RunTableSummaryStats


class ScriptedLlm(BaseLlm):
    responses: list[types.Content]
    requests: list[LlmRequest] = []

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        del stream
        self.requests.append(llm_request)
        yield LlmResponse(content=self.responses.pop(0), partial=False)


class RecordingCompsClient:
    def __init__(
        self,
        *responses: GenerateCompsToolResponse | Exception,
    ) -> None:
        self.responses = list(responses)
        self.requests: list[GenerateCompsToolRequest] = []

    async def generate_comps_table(
        self,
        request: GenerateCompsToolRequest,
    ) -> GenerateCompsToolResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class AgentCompsRoutingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.session_context = AdkSessionContext(
            app_name="talk-to-your-stock",
            session_service=InMemorySessionService(),
        )
        app.dependency_overrides[get_session_context] = lambda: self.session_context

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        get_session_context.cache_clear()
        get_fundamental_agent.cache_clear()

    def test_explicit_peer_prompt_calls_comps_tool_and_returns_table_backed_analysis(
        self,
    ) -> None:
        user_id = uuid4()
        thread_id = uuid4()
        user_message_id = uuid4()
        tool_response = _successful_tool_response(
            thread_id=thread_id,
            trigger_message_id=user_message_id,
        )
        model = ScriptedLlm(
            model="scripted",
            responses=[
                types.Content(
                    role="model",
                    parts=[
                        types.Part.from_function_call(
                            name="generate_comps_table",
                            args={
                                "target_ticker": "AAPL",
                                "peer_tickers": ["MSFT", "NVDA"],
                            },
                        )
                    ],
                ),
                types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            text=(
                                "AAPL trades at 10.0x EV/EBITDA versus the "
                                "peer median of 25.0x in the generated Comps Table."
                            )
                        )
                    ],
                ),
            ],
        )
        comps_client = RecordingCompsClient(tool_response)
        agent = FundamentalAnalysisAgent(model=model, comps_client=comps_client)
        app.dependency_overrides[get_fundamental_agent] = lambda: agent

        response = TestClient(app).post(
            "/v1/internal/agent/respond",
            json={
                "user_id": str(user_id),
                "thread_id": str(thread_id),
                "user_message_id": str(user_message_id),
                "content": "Compare Apple with Microsoft and Nvidia",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["run"], tool_response.run.model_dump(mode="json"))
        self.assertEqual(
            body["content"],
            (
                "AAPL trades at 10.0x EV/EBITDA versus the peer median of "
                "25.0x in the generated Comps Table."
            ),
        )
        self.assertEqual(len(comps_client.requests), 1)
        request = comps_client.requests[0]
        self.assertEqual(request.invocation_id, user_message_id)
        self.assertEqual(request.thread_id, thread_id)
        self.assertEqual(request.trigger_message_id, user_message_id)
        self.assertEqual(request.target_ticker, "AAPL")
        self.assertEqual(request.peer_tickers, ["MSFT", "NVDA"])
        self.assertEqual(request.peer_selection_mode.value, "user_supplied")
        self.assertEqual(request.analysis_period.value, "latest")

        session = asyncio.run(
            self.session_context.get_session(user_id=user_id, thread_id=thread_id)
        )
        assert session is not None
        self.assertEqual(
            [event.author for event in session.events],
            [
                "user",
                "fundamental_analysis_agent",
                "fundamental_analysis_agent",
                "fundamental_analysis_agent",
            ],
        )
        tool_call = session.events[1].content.parts[0].function_call
        tool_result = session.events[2].content.parts[0].function_response
        self.assertEqual(tool_call.name, "generate_comps_table")
        self.assertEqual(tool_call.args["target_ticker"], "AAPL")
        self.assertEqual(tool_result.name, "generate_comps_table")
        self.assertEqual(tool_result.response["run"]["id"], str(tool_response.run.id))

    def test_agent_retries_one_pre_run_validation_error_with_corrected_tickers(
        self,
    ) -> None:
        user_id = uuid4()
        thread_id = uuid4()
        user_message_id = uuid4()
        tool_response = _successful_tool_response(
            thread_id=thread_id,
            trigger_message_id=user_message_id,
        )
        model = ScriptedLlm(
            model="scripted",
            responses=[
                _tool_call(target_ticker="AAPLL", peer_tickers=["MSFT", "NVDA"]),
                _tool_call(target_ticker="AAPL", peer_tickers=["MSFT", "NVDA"]),
                types.Content(
                    role="model",
                    parts=[types.Part(text="The corrected Tickers produced a Comps Table.")],
                ),
            ],
        )
        validation_error = CompsToolValidationError(
            ErrorResponse(
                error=ErrorDetail(
                    code=ErrorCode.VALIDATION_ERROR,
                    message="Unsupported ticker: AAPLL.",
                    details={"unsupported_tickers": ["AAPLL"]},
                )
            )
        )
        comps_client = RecordingCompsClient(validation_error, tool_response)
        agent = FundamentalAnalysisAgent(model=model, comps_client=comps_client)
        app.dependency_overrides[get_fundamental_agent] = lambda: agent

        response = TestClient(app).post(
            "/v1/internal/agent/respond",
            json={
                "user_id": str(user_id),
                "thread_id": str(thread_id),
                "user_message_id": str(user_message_id),
                "content": "Compare Apple with Microsoft and Nvidia",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run"]["id"], str(tool_response.run.id))
        self.assertEqual(len(comps_client.requests), 2)
        self.assertEqual(comps_client.requests[0].target_ticker, "AAPLL")
        self.assertEqual(comps_client.requests[1].target_ticker, "AAPL")

        session = asyncio.run(
            self.session_context.get_session(user_id=user_id, thread_id=thread_id)
        )
        assert session is not None
        tool_results = [
            part.function_response.response
            for event in session.events
            for part in event.content.parts
            if part.function_response is not None
        ]
        self.assertEqual(len(tool_results), 2)
        self.assertEqual(tool_results[0]["error"]["code"], "VALIDATION_ERROR")
        self.assertTrue(tool_results[0]["retry_allowed"])
        self.assertEqual(tool_results[1]["run"]["id"], str(tool_response.run.id))

    def test_second_pre_run_validation_error_stops_without_another_tool_call(
        self,
    ) -> None:
        user_id = uuid4()
        thread_id = uuid4()
        user_message_id = uuid4()
        model = ScriptedLlm(
            model="scripted",
            responses=[
                _tool_call(target_ticker="AAPLL", peer_tickers=["MSFT"]),
                _tool_call(target_ticker="APPLE", peer_tickers=["MSFT"]),
                _tool_call(target_ticker="APPL", peer_tickers=["MSFT"]),
            ],
        )
        validation_errors = [
            CompsToolValidationError(
                ErrorResponse(
                    error=ErrorDetail(
                        code=ErrorCode.VALIDATION_ERROR,
                        message=f"Unsupported ticker: {ticker}.",
                        details={"unsupported_tickers": [ticker]},
                    )
                )
            )
            for ticker in ("AAPLL", "APPLE")
        ]
        comps_client = RecordingCompsClient(*validation_errors)
        agent = FundamentalAnalysisAgent(model=model, comps_client=comps_client)
        app.dependency_overrides[get_fundamental_agent] = lambda: agent

        response = TestClient(app).post(
            "/v1/internal/agent/respond",
            json={
                "user_id": str(user_id),
                "thread_id": str(thread_id),
                "user_message_id": str(user_message_id),
                "content": "Compare Apple with Microsoft",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["run"])
        self.assertEqual(
            response.json()["content"],
            (
                "I couldn't validate those Tickers after one correction. "
                "Please confirm the Target Ticker and Peer Tickers."
            ),
        )
        self.assertEqual(len(comps_client.requests), 2)
        self.assertEqual(len(model.responses), 1)

        session = asyncio.run(
            self.session_context.get_session(user_id=user_id, thread_id=thread_id)
        )
        assert session is not None
        self.assertEqual(
            [event.author for event in session.events],
            [
                "user",
                "fundamental_analysis_agent",
                "fundamental_analysis_agent",
                "fundamental_analysis_agent",
                "fundamental_analysis_agent",
                "fundamental_analysis_agent",
            ],
        )
        tool_results = [
            part.function_response.response
            for event in session.events
            for part in event.content.parts
            if part.function_response is not None
        ]
        self.assertEqual(len(tool_results), 2)
        self.assertTrue(tool_results[0]["retry_allowed"])
        self.assertFalse(tool_results[1]["retry_allowed"])
        self.assertEqual(
            session.events[-1].content.parts[0].text,
            response.json()["content"],
        )

    def test_comps_service_unavailability_is_reported_clearly(self) -> None:
        model = ScriptedLlm(
            model="scripted",
            responses=[_tool_call(target_ticker="AAPL", peer_tickers=["MSFT"])],
        )
        comps_client = RecordingCompsClient(
            CompsToolUnavailable("Comps Service unavailable.")
        )
        agent = FundamentalAnalysisAgent(model=model, comps_client=comps_client)
        app.dependency_overrides[get_fundamental_agent] = lambda: agent

        response = TestClient(app).post(
            "/v1/internal/agent/respond",
            json={
                "user_id": str(uuid4()),
                "thread_id": str(uuid4()),
                "user_message_id": str(uuid4()),
                "content": "Compare Apple with Microsoft",
            },
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "UPSTREAM_ERROR")
        self.assertEqual(
            response.json()["error"]["message"],
            "Comps Service unavailable.",
        )
        self.assertEqual(len(comps_client.requests), 1)


def _successful_tool_response(
    *,
    thread_id: Any,
    trigger_message_id: Any,
) -> GenerateCompsToolResponse:
    now = datetime.now(timezone.utc)
    run_id = uuid4()
    run = Run(
        id=run_id,
        thread_id=thread_id,
        trigger_message_id=trigger_message_id,
        status=RunStatus.SUCCEEDED,
        target_ticker="AAPL",
        peer_tickers=["MSFT", "NVDA"],
        currency="USD",
        as_of=now,
        created_at=now,
        started_at=now,
        completed_at=now,
    )
    rows = [
        CompsRow(
            ticker=ticker,
            is_target=ticker == "AAPL",
            currency="USD",
            enterprise_value=enterprise_value,
            ebitda_ltm=10.0,
            ev_to_ebitda=ev_to_ebitda,
            as_of=now,
        )
        for ticker, enterprise_value, ev_to_ebitda in (
            ("AAPL", 100.0, 10.0),
            ("MSFT", 200.0, 20.0),
            ("NVDA", 300.0, 30.0),
        )
    ]
    empty_stats = MinMedianMax(min=None, median=None, max=None)
    table = RunTableResponse(
        run_id=run_id,
        target_ticker="AAPL",
        currency="USD",
        as_of=now,
        rows=rows,
        summary=RunTableSummary(
            stats=RunTableSummaryStats(
                ev_to_revenue=empty_stats,
                ev_to_ebit=empty_stats,
                ev_to_ebitda=MinMedianMax(min=20.0, median=25.0, max=30.0),
                pe=empty_stats,
            )
        ),
    )
    return GenerateCompsToolResponse(
        run=run,
        table=table,
        trace=TraceResponse(run_id=run_id, formulas=[]),
    )


def _tool_call(
    *,
    target_ticker: str,
    peer_tickers: list[str],
) -> types.Content:
    return types.Content(
        role="model",
        parts=[
            types.Part.from_function_call(
                name="generate_comps_table",
                args={
                    "target_ticker": target_ticker,
                    "peer_tickers": peer_tickers,
                },
            )
        ],
    )


if __name__ == "__main__":
    unittest.main()
