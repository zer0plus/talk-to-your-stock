from __future__ import annotations

import unittest
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import Mock, patch
from uuid import UUID

from fastapi.testclient import TestClient
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService
from google.genai import types

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
from comps_service.run_service import LoadedCompanyData
from talk_to_your_stock_shared import Run, RunTableResponse, TraceResponse
from tests.live_service import running_service
from tests.test_web_bff_threads_messages import RecordingRepository
from web_bff.main import app as web_bff_app, get_repository as get_web_repository

INTERNAL_TOKEN = "canonical-path-token"
LOCAL_USER_ID = "00000000-0000-0000-0000-000000000001"


class CanonicalCompsLlm(BaseLlm):
    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        del llm_request, stream
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
        self.tables: dict[UUID, RunTableResponse] = {}
        self.traces: dict[UUID, TraceResponse] = {}
        self.source_snapshots: dict[UUID, SourceSnapshot] = {}

    def save_succeeded_run(
        self,
        *,
        invocation_id: UUID,
        run: Run,
        table: RunTableResponse,
        trace: TraceResponse,
        source_snapshot: SourceSnapshot,
    ) -> None:
        del invocation_id
        self.runs[run.id] = run
        self.tables[run.id] = table
        self.traces[run.id] = trace
        self.source_snapshots[run.id] = source_snapshot

    def get_run(self, run_id: UUID) -> Run | None:
        return self.runs.get(run_id)

    def get_table(self, run_id: UUID) -> RunTableResponse | None:
        return self.tables.get(run_id)

    def get_trace(self, run_id: UUID) -> TraceResponse | None:
        return self.traces.get(run_id)

    def get_source_snapshot(self, run_id: UUID) -> SourceSnapshot | None:
        return self.source_snapshots.get(run_id)


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

        with running_service(comps_app) as comps_service_url:
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

        self.assertEqual(run.status_code, 200, run.text)
        self.assertEqual(run.json()["run"]["id"], run_id)
        self.assertEqual(table.status_code, 200, table.text)
        self.assertEqual(
            {row["ticker"] for row in table.json()["rows"]},
            {"AAPL", "MSFT", "NVDA"},
        )
        self.assertEqual(trace.status_code, 200, trace.text)
        self.assertEqual(trace.json()["run_id"], run_id)
        self.assertEqual(len(web_repository.messages), 2)
        self.assertIn(UUID(run_id), comps_repository.runs)


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
