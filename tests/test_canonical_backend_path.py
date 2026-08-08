from __future__ import annotations

import json
import unittest
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import Mock, patch
from uuid import UUID

import httpx
from fastapi.testclient import TestClient
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService
from google.genai import types
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agent_service.comps_client import HttpCompsToolClient
from agent_service.fundamental_agent import FundamentalAnalysisAgent
from agent_service.main import (
    app as agent_app,
    get_fundamental_agent,
    get_session_context,
)
from agent_service.session_context import AdkSessionContext
from comps_service.artifacts import SourceSnapshot
from comps_service.calculator import CompanyCompsInput
from comps_service.main import (
    app as comps_app,
    get_company_data_source,
    get_repository as get_comps_repository,
    get_ticker_validator,
)
from comps_service.provider import AlphaVantageCompanyDataSource
from comps_service.run_service import LoadedCompanyData
from talk_to_your_stock_shared import (
    GenerateCompsDraftResponse,
    PaginationMeta,
    Run,
    RunStatus,
    RunTableDraftResponse,
    RunTableResponse,
    TraceResponse,
)
from tests.live_service import running_service
from tests.test_web_bff_threads_messages import RecordingRepository
from web_bff.main import app as web_bff_app, get_repository as get_web_repository

INTERNAL_TOKEN = "canonical-path-token"
LOCAL_USER_ID = "00000000-0000-0000-0000-000000000001"


class LoseFirstFinalizeResponse:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.finalize_attempts = 0

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        is_finalize = (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and scope["path"].endswith("/finalize")
        )
        if not is_finalize:
            await self.app(scope, receive, send)
            return

        self.finalize_attempts += 1
        if self.finalize_attempts > 1:
            await self.app(scope, receive, send)
            return

        async def discard_response(_message: Message) -> None:
            pass

        await self.app(scope, receive, discard_response)
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b"{"})


class CanonicalCompsLlm(BaseLlm):
    call_count: int = 0
    final_response: str | None = None

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        del llm_request, stream
        self.call_count += 1
        if self.call_count > 1:
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            text=self.final_response
                            or json.dumps(
                                {
                                    "content": (
                                        "AAPL trades below its peers on "
                                        "EV / Revenue."
                                    ),
                                    "comparison_takeaway": {
                                        "headline": (
                                            "AAPL trades at a discount to its "
                                            "peers on EV / Revenue."
                                        ),
                                        "interpretation": (
                                            "AAPL's EV / Revenue is below the peer "
                                            "group, while the available evidence "
                                            "supports moderate confidence."
                                        ),
                                        "confidence": "moderate",
                                    },
                                }
                            )
                        )
                    ],
                ),
                partial=False,
            )
            return
        yield LlmResponse(
            content=types.Content(
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
            partial=False,
        )


class InMemoryCompsRepository:
    def __init__(self) -> None:
        self.runs: dict[UUID, Run] = {}
        self.draft_tables: dict[UUID, RunTableDraftResponse] = {}
        self.tables: dict[UUID, RunTableResponse] = {}
        self.traces: dict[UUID, TraceResponse] = {}
        self.source_snapshots: dict[UUID, SourceSnapshot] = {}
        self.invocations: dict[UUID, UUID] = {}

    def save_calculated_run(
        self,
        *,
        invocation_id: UUID,
        run: Run,
        table: RunTableDraftResponse,
        trace: TraceResponse,
        source_snapshot: SourceSnapshot,
    ) -> None:
        self.invocations[invocation_id] = run.id
        self.runs[run.id] = run
        self.draft_tables[run.id] = table
        self.traces[run.id] = trace
        self.source_snapshots[run.id] = source_snapshot

    def save_failed_run(
        self,
        *,
        invocation_id: UUID,
        run: Run,
        source_snapshot: SourceSnapshot,
    ) -> None:
        self.invocations[invocation_id] = run.id
        self.runs[run.id] = run
        self.source_snapshots[run.id] = source_snapshot

    def get_run(self, run_id: UUID) -> Run | None:
        return self.runs.get(run_id)

    def get_calculated_run_by_invocation(
        self,
        invocation_id: UUID,
    ) -> GenerateCompsDraftResponse | None:
        run_id = self.invocations.get(invocation_id)
        if run_id is None:
            return None
        run = self.runs[run_id]
        if run.status != RunStatus.RUNNING:
            return None
        return GenerateCompsDraftResponse(
            run=run,
            table=self.draft_tables[run_id],
            trace=self.traces[run_id],
            warnings=run.warnings,
        )

    def list_runs(
        self,
        *,
        thread_id: UUID,
        status: RunStatus | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[Run], PaginationMeta]:
        del cursor
        runs = [run for run in self.runs.values() if run.thread_id == thread_id]
        if status is not None:
            runs = [run for run in runs if run.status == status]
        runs.sort(key=lambda run: (run.created_at, run.id), reverse=True)
        return runs[:limit], PaginationMeta(has_more=False, next_cursor=None)

    def get_table(self, run_id: UUID) -> RunTableResponse | None:
        return self.tables.get(run_id)

    def get_draft_table(self, run_id: UUID) -> RunTableDraftResponse | None:
        return self.draft_tables.get(run_id)

    def get_trace(self, run_id: UUID) -> TraceResponse | None:
        return self.traces.get(run_id)

    def get_source_snapshot(self, run_id: UUID) -> SourceSnapshot | None:
        return self.source_snapshots.get(run_id)

    def finalize_succeeded_run(
        self,
        *,
        run: Run,
        table: RunTableResponse,
    ) -> None:
        self.runs[run.id] = run
        self.tables[run.id] = table

    def finalize_failed_run(self, *, run: Run) -> None:
        self.runs[run.id] = run


class CanonicalBackendPathTest(unittest.TestCase):
    def tearDown(self) -> None:
        web_bff_app.dependency_overrides.clear()
        agent_app.dependency_overrides.clear()
        comps_app.dependency_overrides.clear()
        get_session_context.cache_clear()
        get_fundamental_agent.cache_clear()

    def test_explicit_peer_message_returns_and_reads_comps_artifacts(self) -> None:
        comps_repository = InMemoryCompsRepository()
        company_data_source = Mock()
        company_data_source.load.return_value = _loaded_company_data()
        ticker_validator = Mock()
        ticker_validator.is_supported.return_value = True
        comps_app.dependency_overrides[get_comps_repository] = (
            lambda: comps_repository
        )
        comps_app.dependency_overrides[get_company_data_source] = (
            lambda: company_data_source
        )
        comps_app.dependency_overrides[get_ticker_validator] = (
            lambda: ticker_validator
        )

        session_context = AdkSessionContext(
            app_name="talk-to-your-stock",
            session_service=InMemorySessionService(),
        )
        agent_app.dependency_overrides[get_session_context] = (
            lambda: session_context
        )
        web_repository = RecordingRepository()
        web_bff_app.dependency_overrides[get_web_repository] = (
            lambda: web_repository
        )

        lost_finalize_app = LoseFirstFinalizeResponse(comps_app)
        with running_service(lost_finalize_app) as comps_service_url:
            fundamental_agent = FundamentalAnalysisAgent(
                model=CanonicalCompsLlm(model="canonical-comps"),
                comps_client=HttpCompsToolClient(
                    base_url=comps_service_url,
                    internal_token=INTERNAL_TOKEN,
                ),
            )
            agent_app.dependency_overrides[get_fundamental_agent] = (
                lambda: fundamental_agent
            )
            with running_service(agent_app) as agent_service_url:
                env = {
                    "TALK_TO_YOUR_STOCK_ENV": "local",
                    "DATABASE_URL": "postgresql://unused-by-test",
                    "DEV_AUTH_USER_ID": LOCAL_USER_ID,
                    "DEV_AUTH_EMAIL": "dev@example.com",
                    "AGENT_SERVICE_URL": agent_service_url,
                    "COMPS_SERVICE_URL": comps_service_url,
                    "COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOKEN,
                }
                with patch.dict("os.environ", env, clear=True):
                    client = TestClient(web_bff_app)
                    thread = client.post(
                        "/v1/threads",
                        json={"title": "AAPL comps"},
                    ).json()["thread"]

                    created = client.post(
                        f"/v1/threads/{thread['id']}/messages",
                        json={
                            "content": "Compare Apple with Microsoft and Nvidia"
                        },
                    )

                    self.assertEqual(created.status_code, 201, created.text)
                    body = created.json()
                    run_id = body["run"]["id"]
                    self.assertEqual(body["user_message"]["role"], "user")
                    self.assertEqual(body["assistant_message"]["role"], "assistant")
                    self.assertEqual(body["assistant_message"]["run_id"], run_id)
                    self.assertEqual(
                        body["run"]["trigger_message_id"],
                        body["user_message"]["id"],
                    )

                    run = client.get(f"/v1/runs/{run_id}")
                    table = client.get(f"/v1/runs/{run_id}/table")
                    trace = client.get(f"/v1/runs/{run_id}/trace")
                    history = client.get(
                        f"/v1/threads/{thread['id']}/runs",
                        params={"status": "succeeded", "limit": 1},
                    )

        self.assertEqual(run.status_code, 200, run.text)
        self.assertEqual(run.json()["run"]["id"], run_id)
        self.assertEqual(table.status_code, 200, table.text)
        self.assertEqual(
            {row["ticker"] for row in table.json()["rows"]},
            {"AAPL", "MSFT", "NVDA"},
        )
        persisted_takeaway = comps_repository.tables[
            UUID(run_id)
        ].comparison_takeaway.model_dump(mode="json")
        self.assertEqual(table.json()["comparison_takeaway"], persisted_takeaway)
        self.assertEqual(
            persisted_takeaway["headline"],
            "AAPL trades at a discount to its peers on EV / Revenue.",
        )
        self.assertEqual(
            set(table.json()["comparison_takeaway"]),
            {"headline", "interpretation", "confidence"},
        )
        self.assertEqual(trace.status_code, 200, trace.text)
        self.assertEqual(trace.json()["run_id"], run_id)
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(
            [run["id"] for run in history.json()["runs"]],
            [run_id],
        )
        self.assertEqual(len(web_repository.messages), 2)
        self.assertIn(UUID(run_id), comps_repository.runs)
        self.assertEqual(lost_finalize_app.finalize_attempts, 2)

    def test_agent_output_failure_is_visible_and_linked_to_the_thread(self) -> None:
        comps_repository = InMemoryCompsRepository()
        company_data_source = Mock()
        company_data_source.load.return_value = _loaded_company_data()
        ticker_validator = Mock()
        ticker_validator.is_supported.return_value = True
        comps_app.dependency_overrides[get_comps_repository] = (
            lambda: comps_repository
        )
        comps_app.dependency_overrides[get_company_data_source] = (
            lambda: company_data_source
        )
        comps_app.dependency_overrides[get_ticker_validator] = (
            lambda: ticker_validator
        )

        session_context = AdkSessionContext(
            app_name="talk-to-your-stock",
            session_service=InMemorySessionService(),
        )
        agent_app.dependency_overrides[get_session_context] = (
            lambda: session_context
        )
        web_repository = RecordingRepository()
        web_bff_app.dependency_overrides[get_web_repository] = (
            lambda: web_repository
        )

        with running_service(comps_app) as comps_service_url:
            fundamental_agent = FundamentalAnalysisAgent(
                model=CanonicalCompsLlm(
                    model="canonical-comps",
                    final_response="{",
                ),
                comps_client=HttpCompsToolClient(
                    base_url=comps_service_url,
                    internal_token=INTERNAL_TOKEN,
                ),
            )
            agent_app.dependency_overrides[get_fundamental_agent] = (
                lambda: fundamental_agent
            )
            with running_service(agent_app) as agent_service_url:
                env = {
                    "TALK_TO_YOUR_STOCK_ENV": "local",
                    "DATABASE_URL": "postgresql://unused-by-test",
                    "DEV_AUTH_USER_ID": LOCAL_USER_ID,
                    "DEV_AUTH_EMAIL": "dev@example.com",
                    "AGENT_SERVICE_URL": agent_service_url,
                    "COMPS_SERVICE_URL": comps_service_url,
                    "COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOKEN,
                }
                with patch.dict("os.environ", env, clear=True):
                    client = TestClient(web_bff_app)
                    thread = client.post(
                        "/v1/threads",
                        json={"title": "AAPL comps"},
                    ).json()["thread"]

                    failed = client.post(
                        f"/v1/threads/{thread['id']}/messages",
                        json={
                            "content": "Compare Apple with Microsoft and Nvidia"
                        },
                    )

                    self.assertEqual(failed.status_code, 502, failed.text)
                    error = failed.json()["error"]
                    run_id = error["run_id"]
                    messages = client.get(
                        f"/v1/threads/{thread['id']}/messages"
                    ).json()["messages"]
                    linked_thread = client.get(
                        f"/v1/threads/{thread['id']}"
                    ).json()["thread"]
                    run = client.get(f"/v1/runs/{run_id}").json()["run"]

        self.assertEqual(
            error["message"],
            "Agent returned an invalid structured response.",
        )
        self.assertEqual(
            [(message["role"], message["status"]) for message in messages],
            [("user", "complete"), ("assistant", "failed")],
        )
        self.assertEqual(messages[-1]["run_id"], run_id)
        self.assertEqual(linked_thread["latest_run_id"], run_id)
        self.assertEqual(run["status"], "failed")

    def test_failed_run_error_is_visible_and_linked_to_the_thread(self) -> None:
        provider_key = "FAKE_BOUNDARY_KEY_123"
        comps_repository = InMemoryCompsRepository()

        def provider_response(_request):
            return httpx.Response(
                429,
                json={
                    "Information": (
                        "Provider quota exhausted. "
                        f"API key as {provider_key}"
                    )
                },
            )

        company_data_source = AlphaVantageCompanyDataSource(
            environ={
                "ALPHA_VANTAGE_API_KEY": provider_key,
                "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
            },
            transport=httpx.MockTransport(provider_response),
            validated_ticker_matches={
                ticker: {
                    "1. symbol": ticker,
                    "3. type": "Equity",
                    "8. currency": "USD",
                }
                for ticker in ("AAPL", "MSFT", "NVDA")
            },
        )
        ticker_validator = Mock()
        ticker_validator.is_supported.return_value = True
        comps_app.dependency_overrides[get_comps_repository] = (
            lambda: comps_repository
        )
        comps_app.dependency_overrides[get_company_data_source] = (
            lambda: company_data_source
        )
        comps_app.dependency_overrides[get_ticker_validator] = (
            lambda: ticker_validator
        )

        session_context = AdkSessionContext(
            app_name="talk-to-your-stock",
            session_service=InMemorySessionService(),
        )
        agent_app.dependency_overrides[get_session_context] = (
            lambda: session_context
        )
        web_repository = RecordingRepository()
        web_bff_app.dependency_overrides[get_web_repository] = (
            lambda: web_repository
        )

        with running_service(comps_app) as comps_service_url:
            agent_app.dependency_overrides[get_fundamental_agent] = lambda: (
                FundamentalAnalysisAgent(
                    model=CanonicalCompsLlm(model="canonical-comps"),
                    comps_client=HttpCompsToolClient(
                        base_url=comps_service_url,
                        internal_token=INTERNAL_TOKEN,
                    ),
                )
            )
            with running_service(agent_app) as agent_service_url:
                env = {
                    "TALK_TO_YOUR_STOCK_ENV": "local",
                    "DATABASE_URL": "postgresql://unused-by-test",
                    "DEV_AUTH_USER_ID": LOCAL_USER_ID,
                    "DEV_AUTH_EMAIL": "dev@example.com",
                    "AGENT_SERVICE_URL": agent_service_url,
                    "COMPS_SERVICE_URL": comps_service_url,
                    "COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOKEN,
                }
                with patch.dict("os.environ", env, clear=True):
                    client = TestClient(web_bff_app)
                    thread = client.post(
                        "/v1/threads",
                        json={"title": "AAPL comps"},
                    ).json()["thread"]

                    with (
                        self.assertLogs(
                            "comps_service.main",
                            level="ERROR",
                        ) as comps_logs,
                        self.assertLogs(
                            "agent_service.main",
                            level="ERROR",
                        ) as agent_logs,
                        self.assertLogs(
                            "web_bff.main",
                            level="ERROR",
                        ) as web_logs,
                    ):
                        failed = client.post(
                            f"/v1/threads/{thread['id']}/messages",
                            json={
                                "content": (
                                    "Compare Apple with Microsoft and Nvidia"
                                )
                            },
                        )

                    self.assertEqual(failed.status_code, 502, failed.text)
                    error = failed.json()["error"]
                    run_id = error["run_id"]
                    self.assertEqual(error["code"], "UPSTREAM_ERROR")
                    self.assertEqual(
                        error["message"],
                        "Alpha Vantage request limit was reached while loading AAPL.",
                    )

                    messages = client.get(
                        f"/v1/threads/{thread['id']}/messages"
                    ).json()["messages"]
                    linked_thread = client.get(
                        f"/v1/threads/{thread['id']}"
                    ).json()["thread"]
                    run = client.get(f"/v1/runs/{run_id}").json()["run"]

        self.assertEqual(
            [(message["role"], message["status"]) for message in messages],
            [("user", "complete"), ("assistant", "failed")],
        )
        self.assertEqual(messages[-1]["run_id"], run_id)
        self.assertEqual(linked_thread["latest_run_id"], run_id)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_message"], error["message"])
        self.assertNotIn(provider_key, failed.text)
        self.assertNotIn(INTERNAL_TOKEN, failed.text)
        for log_output in (
            "\n".join(comps_logs.output),
            "\n".join(agent_logs.output),
            "\n".join(web_logs.output),
        ):
            self.assertIn(run_id, log_output)
            self.assertIn(thread["id"], log_output)
            self.assertIn(error["message"], log_output)
            self.assertNotIn(provider_key, log_output)
            self.assertNotIn(INTERNAL_TOKEN, log_output)
        snapshot = comps_repository.source_snapshots[UUID(run_id)]
        snapshot_json = json.dumps(snapshot.model_dump(mode="json"))
        self.assertNotIn(provider_key, snapshot_json)
        self.assertNotIn(INTERNAL_TOKEN, snapshot_json)


def _loaded_company_data() -> LoadedCompanyData:
    as_of = datetime(2026, 7, 17, tzinfo=UTC)
    fields = (
        "share_price",
        "shares_outstanding",
        "cash",
        "total_debt",
        "revenue_ltm",
        "ebit_ltm",
        "ebitda_ltm",
        "net_income_ltm",
    )
    companies = [
        CompanyCompsInput(
            ticker=ticker,
            company_name=f"{ticker} Inc.",
            currency="USD",
            share_price=10.0,
            shares_outstanding=100.0,
            cash=200.0,
            total_debt=500.0,
            revenue_ltm=250.0,
            ebit_ltm=100.0,
            ebitda_ltm=125.0,
            net_income_ltm=50.0,
            as_of=as_of,
            sources={field: f"provider.{ticker}.{field}" for field in fields},
            source_as_of={field: as_of for field in fields},
        )
        for ticker in ("AAPL", "MSFT", "NVDA")
    ]
    return LoadedCompanyData(
        companies=companies,
        raw_provider_evidence={
            company.ticker: {"provider": "controlled"}
            for company in companies
        },
    )


if __name__ == "__main__":
    unittest.main()
