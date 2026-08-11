from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import httpx
import yaml

from comps_service.artifacts import FailedRunInvocation, RunFailure, SourceSnapshot
from comps_service.calculator import CompanyCompsInput
from comps_service.main import (
    app,
    get_company_data_source,
    get_repository,
    get_ticker_validator,
)
from comps_service.provider import AlphaVantageCompanyDataSource
from comps_service.repository import CompsPersistenceUnavailable, InvalidRunLinkage
from comps_service.run_service import DuplicateToolInvocation, LoadedCompanyData
from comps_service.tool_validation import AlphaVantageTickerValidator
from talk_to_your_stock_shared import (
    GenerateCompsDraftResponse,
    PaginationMeta,
    Run,
    RunStatus,
    RunTableDraftResponse,
    RunTableResponse,
    TraceResponse,
)


INTERNAL_TOOL_TOKEN = "test-internal-tool-token"
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "alpha_vantage"


class SupportedTickerValidator:
    def is_supported(self, _ticker: str) -> bool:
        return True


class CountingTickerValidator(SupportedTickerValidator):
    def __init__(self) -> None:
        self.tickers: list[str] = []

    def is_supported(self, ticker: str) -> bool:
        self.tickers.append(ticker)
        return True


class UnexpectedTickerValidator:
    def is_supported(self, _ticker: str) -> bool:
        raise AssertionError("Ticker validation must not run during draft recovery.")


class ControlledCompanyDataSource:
    def load(
        self,
        *,
        tickers: list[str],
        currency: str,
    ) -> LoadedCompanyData:
        return LoadedCompanyData(
            companies=[
                CompanyCompsInput(
                    ticker=ticker,
                    company_name=f"{ticker} Inc.",
                    currency=currency,
                    share_price=10.0,
                    shares_outstanding=100.0,
                    cash=200.0,
                    total_debt=500.0,
                    revenue_ltm=250.0,
                    ebit_ltm=100.0,
                    ebitda_ltm=125.0,
                    net_income_ltm=50.0,
                    as_of=datetime(2026, 7, 17, tzinfo=UTC),
                    sources={
                        "share_price": f"alpha_vantage.quote.{ticker}.price",
                        "shares_outstanding": (
                            f"alpha_vantage.overview.{ticker}.shares_outstanding"
                        ),
                        "cash": f"alpha_vantage.balance_sheet.{ticker}.cash",
                        "total_debt": (
                            f"alpha_vantage.balance_sheet.{ticker}.total_debt"
                        ),
                        "revenue_ltm": (
                            f"alpha_vantage.income_statement.{ticker}.revenue_ltm"
                        ),
                        "ebit_ltm": (
                            f"alpha_vantage.income_statement.{ticker}.ebit_ltm"
                        ),
                        "ebitda_ltm": (
                            f"alpha_vantage.income_statement.{ticker}.ebitda_ltm"
                        ),
                        "net_income_ltm": (
                            f"alpha_vantage.income_statement.{ticker}.net_income_ltm"
                        ),
                    },
                    source_as_of={
                        field: datetime(2026, 7, 17, tzinfo=UTC)
                        for field in (
                            "share_price",
                            "shares_outstanding",
                            "cash",
                            "total_debt",
                            "revenue_ltm",
                            "ebit_ltm",
                            "ebitda_ltm",
                            "net_income_ltm",
                        )
                    },
                )
                for ticker in tickers
            ],
            raw_provider_evidence={
                ticker: {
                    "provider": "alpha_vantage",
                    "payload": {"raw_marker": f"raw-provider-{ticker}"},
                }
                for ticker in tickers
            },
        )


class UnexpectedCompanyDataSource:
    def load(self, *, tickers: list[str], currency: str) -> LoadedCompanyData:
        del tickers, currency
        raise AssertionError("Provider loading must not run during draft recovery.")


class ReverseOrderCompanyDataSource(ControlledCompanyDataSource):
    def load(
        self,
        *,
        tickers: list[str],
        currency: str,
    ) -> LoadedCompanyData:
        loaded = super().load(tickers=tickers, currency=currency)
        return LoadedCompanyData(
            companies=list(reversed(loaded.companies)),
            raw_provider_evidence=loaded.raw_provider_evidence,
        )


class InMemoryCompsRunRepository:
    def __init__(self) -> None:
        self.runs: dict[UUID, Run] = {}
        self.draft_tables: dict[UUID, RunTableDraftResponse] = {}
        self.tables: dict[UUID, RunTableResponse] = {}
        self.traces: dict[UUID, TraceResponse] = {}
        self.source_snapshots: dict[UUID, SourceSnapshot] = {}
        self.failures: dict[UUID, RunFailure] = {}
        self.invocations: dict[UUID, UUID] = {}

    def reserve_run(self, *, invocation_id: UUID, run: Run) -> None:
        if invocation_id in self.invocations:
            raise DuplicateToolInvocation(
                "Tool invocation has already produced a Run."
            )
        self.invocations[invocation_id] = run.id
        self.runs[run.id] = run

    def claim_run_for_calculation(
        self,
        *,
        run_id: UUID,
        started_at: datetime,
    ) -> bool:
        run = self.runs[run_id]
        if run.status != RunStatus.QUEUED:
            return False
        self.runs[run_id] = run.model_copy(
            update={"status": RunStatus.RUNNING, "started_at": started_at}
        )
        return True

    def save_calculated_run(
        self,
        *,
        invocation_id: UUID,
        run: Run,
        table: RunTableDraftResponse,
        trace: TraceResponse,
        source_snapshot: SourceSnapshot,
    ) -> None:
        existing_run_id = self.invocations.get(invocation_id)
        if existing_run_id is not None and existing_run_id != run.id:
            raise DuplicateToolInvocation(
                "Tool invocation has already produced a Run."
            )
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
        failure: RunFailure,
        source_snapshot: SourceSnapshot,
    ) -> None:
        existing_run_id = self.invocations.get(invocation_id)
        if existing_run_id is not None and existing_run_id != run.id:
            raise DuplicateToolInvocation(
                "Tool invocation has already produced a Run."
            )
        self.invocations[invocation_id] = run.id
        self.runs[run.id] = run
        self.failures[run.id] = failure
        self.source_snapshots[run.id] = source_snapshot

    def get_run(self, run_id: UUID) -> Run | None:
        return self.runs.get(run_id)

    def get_calculated_run_by_invocation(
        self,
        invocation_id: UUID,
    ) -> GenerateCompsDraftResponse | FailedRunInvocation | Run | None:
        run_id = self.invocations.get(invocation_id)
        if run_id is None:
            return None
        run = self.runs[run_id]
        if run.status != RunStatus.RUNNING:
            if run.id in self.failures:
                return FailedRunInvocation(run=run, failure=self.failures[run.id])
            return run
        if run_id not in self.draft_tables:
            return run
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


class InvalidLinkageCompsRunRepository(InMemoryCompsRunRepository):
    def reserve_run(self, *, invocation_id: UUID, run: Run) -> None:
        del invocation_id, run
        raise InvalidRunLinkage("Run must reference its persisted trigger Message.")

    def save_calculated_run(
        self,
        *,
        invocation_id: UUID,
        run: Run,
        table: RunTableDraftResponse,
        trace: TraceResponse,
        source_snapshot: SourceSnapshot,
    ) -> None:
        del invocation_id, run, table, trace, source_snapshot
        raise InvalidRunLinkage("Run must reference its persisted trigger Message.")


class AlreadyClaimedCompsRunRepository(InMemoryCompsRunRepository):
    def claim_run_for_calculation(
        self,
        *,
        run_id: UUID,
        started_at: datetime,
    ) -> bool:
        self.runs[run_id] = self.runs[run_id].model_copy(
            update={"status": RunStatus.RUNNING, "started_at": started_at}
        )
        return False


class SuccessfulCompsRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryCompsRunRepository()
        app.dependency_overrides[get_repository] = lambda: self.repository
        app.dependency_overrides[get_company_data_source] = ControlledCompanyDataSource
        app.dependency_overrides[get_ticker_validator] = SupportedTickerValidator
        self.addCleanup(app.dependency_overrides.clear)

    def test_same_invocation_reserves_one_run_before_calculation(self) -> None:
        request = {
            "invocation_id": str(uuid4()),
            "thread_id": str(uuid4()),
            "trigger_message_id": str(uuid4()),
            "target_ticker": "aapl",
            "peer_tickers": ["msft"],
            "peer_selection_mode": "user_supplied",
            "analysis_period": "latest",
            "currency": "USD",
        }
        headers = {"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"}

        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            client = TestClient(app)
            created = client.post(
                "/v1/internal/tools/reserve-comps-run",
                json=request,
                headers=headers,
            )
            repeated = client.post(
                "/v1/internal/tools/reserve-comps-run",
                json=request,
                headers=headers,
            )

        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(repeated.json(), created.json())
        self.assertEqual(created.json()["run"]["status"], "queued")
        self.assertIsNone(created.json()["run"]["as_of"])
        self.assertEqual(created.json()["run"]["id"], request["invocation_id"])

    def test_reserved_run_is_then_calculated_without_creating_another_run(
        self,
    ) -> None:
        request = {
            "invocation_id": str(uuid4()),
            "thread_id": str(uuid4()),
            "trigger_message_id": str(uuid4()),
            "target_ticker": "AAPL",
            "peer_tickers": ["MSFT"],
            "peer_selection_mode": "user_supplied",
            "analysis_period": "latest",
            "currency": "USD",
        }
        headers = {"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"}

        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            client = TestClient(app)
            reserved = client.post(
                "/v1/internal/tools/reserve-comps-run",
                json=request,
                headers=headers,
            )
            calculated = client.post(
                "/v1/internal/tools/generate-comps-table",
                json=request,
                headers=headers,
            )

        self.assertEqual(calculated.status_code, 200, calculated.text)
        self.assertEqual(
            calculated.json()["run"]["id"],
            reserved.json()["run"]["id"],
        )
        self.assertEqual(len(self.repository.runs), 1)
        self.assertIsNotNone(calculated.json()["run"]["as_of"])

    def test_calculation_reuses_the_reservation_validation(self) -> None:
        ticker_validator = CountingTickerValidator()
        app.dependency_overrides[get_ticker_validator] = lambda: ticker_validator
        request = {
            "invocation_id": str(uuid4()),
            "thread_id": str(uuid4()),
            "trigger_message_id": str(uuid4()),
            "target_ticker": "AAPL",
            "peer_tickers": ["MSFT"],
            "peer_selection_mode": "user_supplied",
            "analysis_period": "latest",
        }
        headers = {"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"}

        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            client = TestClient(app)
            client.post(
                "/v1/internal/tools/reserve-comps-run",
                json=request,
                headers=headers,
            )
            calculated = client.post(
                "/v1/internal/tools/generate-comps-table",
                json=request,
                headers=headers,
            )

        self.assertEqual(calculated.status_code, 200, calculated.text)
        self.assertEqual(ticker_validator.tickers, ["AAPL", "MSFT"])

    def test_concurrent_retry_does_not_repeat_provider_calculation(self) -> None:
        repository = AlreadyClaimedCompsRunRepository()
        app.dependency_overrides[get_repository] = lambda: repository
        app.dependency_overrides[get_company_data_source] = UnexpectedCompanyDataSource

        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            response = TestClient(app).post(
                "/v1/internal/tools/generate-comps-table",
                json={
                    "invocation_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "trigger_message_id": str(uuid4()),
                    "target_ticker": "AAPL",
                    "peer_tickers": ["MSFT"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                },
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "CONFLICT")

    def test_agent_takeaway_finalizes_calculated_run_with_every_company(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/internal/tools/generate-comps-table",
                json={
                    "invocation_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "trigger_message_id": str(uuid4()),
                    "target_ticker": "aapl",
                    "peer_tickers": ["msft", "goog"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                    "currency": "USD",
                },
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )

            self.assertEqual(response.status_code, 200, response.text)
            draft = response.json()
            self.assertEqual(draft["run"]["status"], "running")
            self.assertNotIn("comparison_takeaway", draft["table"])
            response = client.post(
                f"/v1/internal/runs/{draft['run']['id']}/finalize",
                json={
                    "comparison_takeaway": {
                        "headline": (
                            "AAPL appears broadly aligned with its peers on "
                            "EV / Revenue."
                        ),
                        "interpretation": (
                            "AAPL's EV / Revenue sits near the peer group, while "
                            "the limited group size leaves room for uncertainty."
                        ),
                        "confidence": "moderate",
                    }
                },
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["run"]["status"], "succeeded")
        self.assertEqual(body["run"]["target_ticker"], "AAPL")
        self.assertEqual(body["run"]["peer_tickers"], ["MSFT", "GOOG"])
        self.assertEqual(body["run"]["as_of"], body["table"]["as_of"])
        self.assertEqual(
            {row["ticker"] for row in body["table"]["rows"]},
            {"AAPL", "MSFT", "GOOG"},
        )
        self.assertEqual(
            [row["ticker"] for row in body["table"]["rows"] if row["is_target"]],
            ["AAPL"],
        )
        self.assertEqual(body["table"]["run_id"], body["run"]["id"])
        self.assertEqual(
            body["table"]["comparison_takeaway"],
            {
                "headline": (
                    "AAPL appears broadly aligned with its peers on EV / Revenue."
                ),
                "interpretation": (
                    "AAPL's EV / Revenue sits near the peer group, while the "
                    "limited group size leaves room for uncertainty."
                ),
                "confidence": "moderate",
            },
        )

    def test_shape_valid_takeaway_is_persisted_without_inspecting_its_prose(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            client = TestClient(app)
            calculated = client.post(
                "/v1/internal/tools/generate-comps-table",
                json={
                    "invocation_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "trigger_message_id": str(uuid4()),
                    "target_ticker": "AAPL",
                    "peer_tickers": ["MSFT"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                    "currency": "USD",
                },
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )
            response = client.post(
                f"/v1/internal/runs/{calculated.json()['run']['id']}/finalize",
                json={
                    "comparison_takeaway": {
                        "headline": "Buy this immediately.",
                        "interpretation": "No ticker or Metric is named here.",
                        "confidence": "strong",
                    }
                },
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["run"]["status"], "succeeded")
        self.assertEqual(
            response.json()["table"]["comparison_takeaway"]["headline"],
            "Buy this immediately.",
        )

    def test_agent_failure_transitions_a_calculated_run_to_failed(self) -> None:
        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            client = TestClient(app)
            calculated = client.post(
                "/v1/internal/tools/generate-comps-table",
                json={
                    "invocation_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "trigger_message_id": str(uuid4()),
                    "target_ticker": "AAPL",
                    "peer_tickers": ["MSFT"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                    "currency": "USD",
                },
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )
            run_id = calculated.json()["run"]["id"]
            failed = client.post(
                f"/v1/internal/runs/{run_id}/fail",
                json={"error_message": "The Agent could not complete the analysis."},
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )
            readback = client.get(f"/v1/runs/{run_id}")

        self.assertEqual(failed.status_code, 200, failed.text)
        self.assertEqual(failed.json()["run"]["status"], "failed")
        self.assertEqual(
            failed.json()["run"]["error_message"],
            "The Agent could not complete the analysis.",
        )
        self.assertIsNotNone(failed.json()["run"]["completed_at"])
        self.assertEqual(readback.json(), failed.json())

    def test_provider_payload_with_missing_currency_is_persisted(
        self,
    ) -> None:
        fixture = json.loads(
            (FIXTURE_ROOT / "usd_company_latest.json").read_text()
        )

        def respond(request):
            function = request.url.params["function"]
            symbol = request.url.params["symbol"]
            payload = deepcopy(fixture[function])
            if function == "GLOBAL_QUOTE":
                payload["Global Quote"]["01. symbol"] = symbol
            else:
                symbol_field = "Symbol" if function == "OVERVIEW" else "symbol"
                payload[symbol_field] = symbol
            if function == "OVERVIEW" and symbol != "AAPL":
                payload["Name"] = f"{symbol} Example Company"
            if function == "INCOME_STATEMENT" and symbol == "MSFT":
                payload["quarterlyReports"][0]["reportedCurrency"] = "None"
            return httpx.Response(200, json=payload)

        source = AlphaVantageCompanyDataSource(
            environ={
                "ALPHA_VANTAGE_API_KEY": "fixture-key",
                "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
            },
            transport=httpx.MockTransport(respond),
            validated_ticker_matches=self._validated_ticker_matches(
                "AAPL",
                "MSFT",
            ),
        )
        app.dependency_overrides[get_company_data_source] = lambda: source

        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            response = TestClient(app).post(
                "/v1/internal/tools/generate-comps-table",
                json={
                    "invocation_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "trigger_message_id": str(uuid4()),
                    "target_ticker": "AAPL",
                    "peer_tickers": ["MSFT"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                    "currency": "USD",
                },
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        row = body["table"]["rows"][0]
        self.assertEqual(
            {
                "ticker": row["ticker"],
                "company_name": row["company_name"],
                "currency": row["currency"],
                "share_price": row["share_price"],
                "shares_outstanding": row["shares_outstanding"],
                "cash": row["cash"],
                "total_debt": row["total_debt"],
                "revenue_ltm": row["revenue_ltm"],
                "ebit_ltm": row["ebit_ltm"],
                "ebitda_ltm": row["ebitda_ltm"],
                "net_income_ltm": row["net_income_ltm"],
                "as_of": row["as_of"],
            },
            {
                "ticker": "AAPL",
                "company_name": "Example Technology Inc.",
                "currency": "USD",
                "share_price": 143.25,
                "shares_outstanding": 1000.0,
                "cash": 100.0,
                "total_debt": 300.0,
                "revenue_ltm": 1000.0,
                "ebit_ltm": 200.0,
                "ebitda_ltm": 240.0,
                "net_income_ltm": 120.0,
                "as_of": "2026-07-17T00:00:00Z",
            },
        )
        run_id = UUID(body["run"]["id"])
        client = TestClient(app)
        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            finalized = client.post(
                f"/v1/internal/runs/{run_id}/finalize",
                json={
                    "comparison_takeaway": {
                        "headline": "AAPL is comparable to MSFT on EV / Revenue.",
                        "interpretation": (
                            "AAPL's EV / Revenue can be compared with the "
                            "available peer evidence, with limited confidence "
                            "from one peer."
                        ),
                        "confidence": "limited",
                    }
                },
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )
        persisted_run = client.get(f"/v1/runs/{run_id}")
        persisted_table = client.get(f"/v1/runs/{run_id}/table")
        self.assertEqual(persisted_run.status_code, 200)
        self.assertEqual(finalized.status_code, 200, finalized.text)
        self.assertEqual(persisted_run.json()["run"]["status"], "succeeded")
        self.assertEqual(persisted_table.status_code, 200)
        self.assertEqual(
            {row["ticker"] for row in persisted_table.json()["rows"]},
            {"AAPL", "MSFT"},
        )
        self.assertEqual(client.get(f"/v1/runs/{run_id}/trace").status_code, 200)
        history = client.get(
            f"/v1/threads/{body['run']['thread_id']}/runs",
            params={"status": "succeeded", "limit": 1},
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(
            [run["id"] for run in history.json()["runs"]],
            [str(run_id)],
        )
        snapshot = self.repository.get_source_snapshot(run_id)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(
            set(snapshot.raw_provider_evidence["AAPL"]),
            {
                "symbol_search",
                "global_quote",
                "overview",
                "income_statement",
                "balance_sheet",
            },
        )
        self.assertEqual(
            snapshot.raw_provider_evidence["MSFT"]["income_statement"][
                "quarterlyReports"
            ][0]["reportedCurrency"],
            "None",
        )
        msft_input = next(
            company
            for company in snapshot.normalized_inputs
            if company.ticker == "MSFT"
        )
        self.assertEqual(msft_input.currency, "USD")
        normalized = snapshot.normalized_inputs[0]
        self.assertTrue(
            all(
                "overview.AAPL.Currency=USD" in source
                for field, source in normalized.sources.items()
                if field not in {"share_price", "shares_outstanding"}
            )
        )
        self.assertIn(
            "symbol_search.AAPL.8. currency=USD",
            normalized.sources["share_price"],
        )
        trace_inputs = {
            trace_input["field"]: trace_input
            for formula in body["trace"]["formulas"]
            if formula["ticker"] == "AAPL"
            for trace_input in formula["inputs"]
            if not trace_input["source"].startswith("calculated.")
        }
        self.assertEqual(
            trace_inputs["share_price"]["as_of"],
            "2026-07-17T00:00:00Z",
        )
        for field in (
            "shares_outstanding",
            "cash",
            "total_debt",
            "revenue_ltm",
            "ebit_ltm",
            "ebitda_ltm",
            "net_income_ltm",
        ):
            with self.subTest(field=field):
                self.assertEqual(
                    trace_inputs[field]["as_of"],
                    "2026-06-30T00:00:00Z",
                )

    def test_quote_and_fundamentals_use_their_own_currency_evidence(self) -> None:
        company_fixture = json.loads(
            (FIXTURE_ROOT / "usd_company_latest.json").read_text()
        )
        fx_fixture = json.loads(
            (FIXTURE_ROOT / "cad_to_usd_latest.json").read_text()
        )
        fx_requests: list[tuple[str, str]] = []
        validated_ticker_matches: dict[str, dict[str, object]] = {}

        def respond(request):
            function = request.url.params["function"]
            if function == "SYMBOL_SEARCH":
                ticker = request.url.params["keywords"]
                return httpx.Response(
                    200,
                    json={
                        "bestMatches": [
                            {
                                "1. symbol": ticker,
                                "3. type": "Equity",
                                "8. currency": "GBP",
                            }
                        ]
                    },
                )
            if function == "CURRENCY_EXCHANGE_RATE":
                from_currency = request.url.params["from_currency"]
                to_currency = request.url.params["to_currency"]
                fx_requests.append((from_currency, to_currency))
                payload = deepcopy(fx_fixture)
                exchange_rate = payload["Realtime Currency Exchange Rate"]
                exchange_rate["1. From_Currency Code"] = from_currency
                exchange_rate["3. To_Currency Code"] = to_currency
                exchange_rate["5. Exchange Rate"] = (
                    "1.25000000" if from_currency == "GBP" else "0.75000000"
                )
                exchange_rate["6. Last Refreshed"] = (
                    "2026-07-15 21:59:01"
                    if from_currency == "GBP"
                    else "2026-05-31 21:59:01"
                )
                return httpx.Response(200, json=payload)

            symbol = request.url.params["symbol"]
            payload = deepcopy(company_fixture[function])
            if function == "GLOBAL_QUOTE":
                payload["Global Quote"]["01. symbol"] = symbol
            elif function == "OVERVIEW":
                payload["Symbol"] = symbol
                payload["Currency"] = "CAD"
            else:
                payload["symbol"] = symbol
                for report in payload["quarterlyReports"]:
                    report["reportedCurrency"] = "CAD"
            return httpx.Response(200, json=payload)

        provider_environ = {
            "ALPHA_VANTAGE_API_KEY": "fixture-key",
            "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
        }
        transport = httpx.MockTransport(respond)
        validator = AlphaVantageTickerValidator(
            environ=provider_environ,
            transport=transport,
            validated_ticker_matches=validated_ticker_matches,
        )
        source = AlphaVantageCompanyDataSource(
            environ=provider_environ,
            transport=transport,
            validated_ticker_matches=validated_ticker_matches,
        )
        app.dependency_overrides[get_ticker_validator] = lambda: validator
        app.dependency_overrides[get_company_data_source] = lambda: source

        client = TestClient(app)
        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            response = client.post(
                "/v1/internal/tools/generate-comps-table",
                json={
                    "invocation_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "trigger_message_id": str(uuid4()),
                    "target_ticker": "AAPL",
                    "peer_tickers": ["MSFT"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                    "currency": "USD",
                },
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()["table"]["rows"][0]
        self.assertEqual(row["currency"], "USD")
        self.assertEqual(
            {
                "share_price": row["share_price"],
                "shares_outstanding": row["shares_outstanding"],
                "cash": row["cash"],
                "total_debt": row["total_debt"],
                "revenue_ltm": row["revenue_ltm"],
                "ebit_ltm": row["ebit_ltm"],
                "ebitda_ltm": row["ebitda_ltm"],
                "net_income_ltm": row["net_income_ltm"],
            },
            {
                "share_price": 179.0625,
                "shares_outstanding": 1000.0,
                "cash": 75.0,
                "total_debt": 225.0,
                "revenue_ltm": 750.0,
                "ebit_ltm": 150.0,
                "ebitda_ltm": 180.0,
                "net_income_ltm": 90.0,
            },
        )
        run_id = UUID(response.json()["run"]["id"])
        trace_readback = client.get(f"/v1/runs/{run_id}/trace")
        self.assertEqual(trace_readback.status_code, 200, trace_readback.text)
        snapshot = self.repository.get_source_snapshot(run_id)
        assert snapshot is not None
        normalized = snapshot.normalized_inputs[0]
        self.assertEqual(normalized.currency, "USD")
        self.assertEqual(
            normalized.source_as_of["share_price"].isoformat(),
            "2026-07-15T21:59:01+00:00",
        )
        self.assertEqual(
            normalized.source_as_of["shares_outstanding"].isoformat(),
            "2026-06-30T00:00:00+00:00",
        )
        for field in (
            "cash",
            "total_debt",
            "revenue_ltm",
            "ebit_ltm",
            "ebitda_ltm",
            "net_income_ltm",
        ):
            with self.subTest(field=field):
                self.assertEqual(
                    normalized.source_as_of[field].isoformat(),
                    "2026-05-31T21:59:01+00:00",
                )
        self.assertIn(
            "symbol_search.AAPL.8. currency=GBP",
            normalized.sources["share_price"],
        )
        self.assertIn(
            "currency_exchange_rate.GBP_USD.5. Exchange Rate",
            normalized.sources["share_price"],
        )
        self.assertTrue(
            all(
                "currency_exchange_rate.CAD_USD.5. Exchange Rate" in source
                for field, source in normalized.sources.items()
                if field not in {"share_price", "shares_outstanding"}
            )
        )
        self.assertEqual(
            snapshot.raw_provider_evidence["AAPL"]["symbol_search"]["8. currency"],
            "GBP",
        )
        self.assertEqual(
            set(
                snapshot.raw_provider_evidence["AAPL"][
                    "currency_exchange_rates"
                ]
            ),
            {"GBP_USD", "CAD_USD"},
        )
        trace_inputs = {
            trace_input["field"]: trace_input
            for formula in trace_readback.json()["formulas"]
            if formula["ticker"] == "AAPL"
            for trace_input in formula["inputs"]
            if not trace_input["source"].startswith("calculated.")
        }
        self.assertEqual(
            trace_inputs["share_price"]["as_of"],
            "2026-07-15T21:59:01Z",
        )
        for field in (
            "cash",
            "total_debt",
            "revenue_ltm",
            "ebit_ltm",
            "ebitda_ltm",
            "net_income_ltm",
        ):
            with self.subTest(trace_field=field):
                self.assertEqual(
                    trace_inputs[field]["as_of"],
                    "2026-05-31T21:59:01Z",
                )
        self.assertEqual(fx_requests, [("GBP", "USD"), ("CAD", "USD")])

    def test_trace_references_the_provider_reports_used_for_inputs(self) -> None:
        fixture = json.loads(
            (FIXTURE_ROOT / "usd_company_latest.json").read_text()
        )
        income_reports = fixture["INCOME_STATEMENT"]["quarterlyReports"]
        oldest_report = deepcopy(income_reports[-1])
        oldest_report.update(
            {
                "fiscalDateEnding": "2025-06-30",
                "totalRevenue": "999",
                "ebit": "999",
                "ebitda": "999",
                "netIncome": "999",
            }
        )
        fixture["INCOME_STATEMENT"]["quarterlyReports"] = [
            oldest_report,
            income_reports[0],
            income_reports[3],
            income_reports[1],
            income_reports[2],
        ]
        fixture["OVERVIEW"]["SharesOutstanding"] = "None"
        latest_balance_report = fixture["BALANCE_SHEET"]["quarterlyReports"][0]
        older_balance_report = deepcopy(latest_balance_report)
        older_balance_report.update(
            {
                "fiscalDateEnding": "2026-03-31",
                "cashAndCashEquivalentsAtCarryingValue": "999",
                "shortLongTermDebtTotal": "999",
                "commonStockSharesOutstanding": "999",
            }
        )
        fixture["BALANCE_SHEET"]["quarterlyReports"] = [
            older_balance_report,
            latest_balance_report,
        ]

        def respond(request):
            function = request.url.params["function"]
            symbol = request.url.params["symbol"]
            payload = deepcopy(fixture[function])
            if function == "GLOBAL_QUOTE":
                payload["Global Quote"]["01. symbol"] = symbol
            else:
                symbol_field = "Symbol" if function == "OVERVIEW" else "symbol"
                payload[symbol_field] = symbol
            return httpx.Response(200, json=payload)

        source = AlphaVantageCompanyDataSource(
            environ={
                "ALPHA_VANTAGE_API_KEY": "fixture-key",
                "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
            },
            transport=httpx.MockTransport(respond),
            validated_ticker_matches=self._validated_ticker_matches(
                "AAPL",
                "MSFT",
            ),
        )
        app.dependency_overrides[get_company_data_source] = lambda: source

        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            response = TestClient(app).post(
                "/v1/internal/tools/generate-comps-table",
                json={
                    "invocation_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "trigger_message_id": str(uuid4()),
                    "target_ticker": "AAPL",
                    "peer_tickers": ["MSFT"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                    "currency": "USD",
                },
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        aapl_row = next(
            row for row in body["table"]["rows"] if row["ticker"] == "AAPL"
        )
        self.assertEqual(aapl_row["revenue_ltm"], 1000.0)
        trace_inputs = {
            trace_input["field"]: trace_input
            for formula in body["trace"]["formulas"]
            if formula["ticker"] == "AAPL"
            for trace_input in formula["inputs"]
        }
        for input_field, provider_field in {
            "revenue_ltm": "totalRevenue",
            "ebit_ltm": "ebit",
            "ebitda_ltm": "ebitda",
            "net_income_ltm": "netIncome",
        }.items():
            with self.subTest(input_field=input_field):
                report_sources = " + ".join(
                    "alpha_vantage.income_statement.AAPL."
                    f"quarterlyReports[{raw_index}].{provider_field}"
                    for raw_index in (1, 3, 4, 2)
                )
                self.assertEqual(
                    trace_inputs[input_field]["source"],
                    f"{report_sources}; "
                    "alpha_vantage.overview.AAPL.Currency=USD",
                )
        expected_balance_sources = {
            "shares_outstanding": (
                "alpha_vantage.balance_sheet.AAPL.quarterlyReports[1]."
                "commonStockSharesOutstanding"
            ),
            "cash": (
                "alpha_vantage.balance_sheet.AAPL.quarterlyReports[1]."
                "cashAndCashEquivalentsAtCarryingValue; "
                "alpha_vantage.overview.AAPL.Currency=USD"
            ),
            "total_debt": (
                "alpha_vantage.balance_sheet.AAPL.quarterlyReports[1]."
                "shortLongTermDebtTotal; "
                "alpha_vantage.overview.AAPL.Currency=USD"
            ),
        }
        for input_field, expected_source in expected_balance_sources.items():
            with self.subTest(input_field=input_field):
                self.assertEqual(
                    trace_inputs[input_field]["source"],
                    expected_source,
                )

        snapshot = self.repository.get_source_snapshot(UUID(body["run"]["id"]))
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        normalized_input = next(
            company
            for company in snapshot.normalized_inputs
            if company.ticker == "AAPL"
        )
        for input_field in (
            "revenue_ltm",
            "ebit_ltm",
            "ebitda_ltm",
            "net_income_ltm",
            *expected_balance_sources,
        ):
            with self.subTest(persisted_input_field=input_field):
                self.assertEqual(
                    normalized_input.sources[input_field],
                    trace_inputs[input_field]["source"],
                )
        self.assertEqual(
            [
                report["fiscalDateEnding"]
                for report in snapshot.raw_provider_evidence["AAPL"][
                    "balance_sheet"
                ]["quarterlyReports"]
            ],
            ["2026-03-31", "2026-06-30"],
        )

    def _validated_ticker_matches(
        self,
        *tickers: str,
        currency: str = "USD",
    ) -> dict[str, dict[str, object]]:
        return {
            ticker: {
                "1. symbol": ticker,
                "3. type": "Equity",
                "8. currency": currency,
            }
            for ticker in tickers
        }

    def test_succeeded_run_and_table_are_available_through_readback_contracts(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            client = TestClient(app)
            created = client.post(
                "/v1/internal/tools/generate-comps-table",
                json={
                    "invocation_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "trigger_message_id": str(uuid4()),
                    "target_ticker": "AAPL",
                    "peer_tickers": ["MSFT"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                },
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )
            run_id = created.json()["run"]["id"]
            finalized = client.post(
                f"/v1/internal/runs/{run_id}/finalize",
                json={
                    "comparison_takeaway": {
                        "headline": "AAPL is comparable to MSFT on EV / Revenue.",
                        "interpretation": (
                            "AAPL's EV / Revenue is supported by the available "
                            "peer evidence, with limited confidence from one peer."
                        ),
                        "confidence": "limited",
                    }
                },
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )

            run_response = client.get(f"/v1/runs/{run_id}")
            table_response = client.get(f"/v1/runs/{run_id}/table")

        self.assertEqual(run_response.status_code, 200, run_response.text)
        self.assertEqual(table_response.status_code, 200, table_response.text)
        self.assertEqual(run_response.json()["run"], finalized.json()["run"])
        self.assertEqual(table_response.json(), finalized.json()["table"])

    def test_repeated_finalization_returns_the_original_without_rewriting_it(
        self,
    ) -> None:
        request = {
            "comparison_takeaway": {
                "headline": "AAPL is comparable to MSFT on EV / Revenue.",
                "interpretation": (
                    "AAPL's EV / Revenue is supported by the available peer evidence."
                ),
                "confidence": "limited",
            }
        }
        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            client = TestClient(app)
            created = client.post(
                "/v1/internal/tools/generate-comps-table",
                json={
                    "invocation_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "trigger_message_id": str(uuid4()),
                    "target_ticker": "AAPL",
                    "peer_tickers": ["MSFT"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                },
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )
            run_id = created.json()["run"]["id"]
            first = client.post(
                f"/v1/internal/runs/{run_id}/finalize",
                json=request,
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )
            repeated = client.post(
                f"/v1/internal/runs/{run_id}/finalize",
                json=request,
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )
            changed = client.post(
                f"/v1/internal/runs/{run_id}/finalize",
                json={
                    "comparison_takeaway": {
                        **request["comparison_takeaway"],
                        "headline": "A different interpretation.",
                    }
                },
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )
            table = client.get(f"/v1/runs/{run_id}/table")

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json(), first.json())
        self.assertEqual(changed.status_code, 404, changed.text)
        self.assertEqual(table.json(), first.json()["table"])

    def test_succeeded_run_trace_is_available_through_public_readback(self) -> None:
        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            client = TestClient(app)
            created = client.post(
                "/v1/internal/tools/generate-comps-table",
                json={
                    "invocation_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "trigger_message_id": str(uuid4()),
                    "target_ticker": "AAPL",
                    "peer_tickers": ["MSFT"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                },
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )
            run_id = created.json()["run"]["id"]

            trace_response = client.get(f"/v1/runs/{run_id}/trace")

        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(trace_response.status_code, 200, trace_response.text)
        self.assertEqual(trace_response.json(), created.json()["trace"])
        equity_value = trace_response.json()["formulas"][0]
        self.assertEqual(equity_value["expression"], "share_price * shares_outstanding")
        self.assertEqual(equity_value["output_value"], 1000.0)
        self.assertEqual(
            equity_value["inputs"][0]["source"],
            "alpha_vantage.quote.AAPL.price",
        )
        self.assertEqual(equity_value["inputs"][0]["as_of"], "2026-07-17T00:00:00Z")
        target_external_inputs = [
            trace_input
            for formula in trace_response.json()["formulas"]
            if formula["ticker"] == "AAPL"
            for trace_input in formula["inputs"]
            if not trace_input["source"].startswith("calculated.")
        ]
        self.assertEqual(
            {trace_input["field"] for trace_input in target_external_inputs},
            {
                "share_price",
                "shares_outstanding",
                "cash",
                "total_debt",
                "revenue_ltm",
                "ebit_ltm",
                "ebitda_ltm",
                "net_income_ltm",
            },
        )
        self.assertTrue(
            all(
                trace_input["source"].startswith("alpha_vantage.")
                for trace_input in target_external_inputs
            )
        )

    def test_source_snapshot_preserves_evidence_without_public_exposure(self) -> None:
        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            client = TestClient(app)
            created = client.post(
                "/v1/internal/tools/generate-comps-table",
                json={
                    "invocation_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "trigger_message_id": str(uuid4()),
                    "target_ticker": "AAPL",
                    "peer_tickers": ["MSFT"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                },
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )
            run_id = UUID(created.json()["run"]["id"])
            trace_readback = client.get(f"/v1/runs/{run_id}/trace")

        self.assertEqual(created.status_code, 200, created.text)
        source_snapshot = self.repository.get_source_snapshot(run_id)
        self.assertIsNotNone(source_snapshot)
        assert source_snapshot is not None
        self.assertEqual(
            source_snapshot.raw_provider_evidence["AAPL"]["payload"],
            {"raw_marker": "raw-provider-AAPL"},
        )
        self.assertEqual(
            [company.ticker for company in source_snapshot.normalized_inputs],
            ["AAPL", "MSFT"],
        )
        self.assertEqual(source_snapshot.normalized_inputs[0].share_price, 10.0)
        self.assertNotIn("source_snapshot", created.json())
        self.assertNotIn("raw-provider", created.text)
        self.assertNotIn("raw-provider", trace_readback.text)

    def test_repeated_invocation_recovers_the_existing_calculated_run(
        self,
    ) -> None:
        invocation_id = uuid4()
        request = {
            "invocation_id": str(invocation_id),
            "thread_id": str(uuid4()),
            "trigger_message_id": str(uuid4()),
            "target_ticker": "AAPL",
            "peer_tickers": ["MSFT"],
            "peer_selection_mode": "user_supplied",
            "analysis_period": "latest",
        }

        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            client = TestClient(app)
            created = client.post(
                "/v1/internal/tools/generate-comps-table",
                json=request,
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )
            app.dependency_overrides[get_company_data_source] = (
                UnexpectedCompanyDataSource
            )
            app.dependency_overrides[get_ticker_validator] = UnexpectedTickerValidator
            repeated = client.post(
                "/v1/internal/tools/generate-comps-table",
                json=request,
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )

        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json(), created.json())
        self.assertEqual(len(self.repository.runs), 1)
        self.assertEqual(len(self.repository.draft_tables), 1)
        self.assertEqual(len(self.repository.tables), 0)
        self.assertEqual(len(self.repository.traces), 1)
        self.assertEqual(len(self.repository.source_snapshots), 1)

    def test_repeated_invocation_with_different_input_returns_conflict(self) -> None:
        invocation_id = uuid4()
        request = {
            "invocation_id": str(invocation_id),
            "thread_id": str(uuid4()),
            "trigger_message_id": str(uuid4()),
            "target_ticker": "AAPL",
            "peer_tickers": ["MSFT"],
            "peer_selection_mode": "user_supplied",
            "analysis_period": "latest",
        }

        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            client = TestClient(app)
            created = client.post(
                "/v1/internal/tools/generate-comps-table",
                json=request,
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )
            repeated = client.post(
                "/v1/internal/tools/generate-comps-table",
                json={**request, "peer_tickers": ["GOOG"]},
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )

        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(repeated.status_code, 409, repeated.text)
        self.assertEqual(repeated.json()["error"]["code"], "CONFLICT")

    def test_repeated_invocation_cannot_bypass_unsupported_mode_validation(
        self,
    ) -> None:
        request = {
            "invocation_id": str(uuid4()),
            "thread_id": str(uuid4()),
            "trigger_message_id": str(uuid4()),
            "target_ticker": "AAPL",
            "peer_tickers": ["MSFT"],
            "peer_selection_mode": "user_supplied",
            "analysis_period": "latest",
        }

        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            client = TestClient(app)
            created = client.post(
                "/v1/internal/tools/reserve-comps-run",
                json=request,
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )
            repeated = client.post(
                "/v1/internal/tools/generate-comps-table",
                json={**request, "peer_selection_mode": "auto"},
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )

        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(repeated.status_code, 501, repeated.text)

    def test_runtime_path_fails_clearly_without_provider_configuration(
        self,
    ) -> None:
        app.dependency_overrides.pop(get_company_data_source)

        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            response = TestClient(app).post(
                "/v1/internal/tools/generate-comps-table",
                json={
                    "invocation_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "trigger_message_id": str(uuid4()),
                    "target_ticker": "AAPL",
                    "peer_tickers": ["MSFT"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                },
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["error"]["code"], "INTERNAL_ERROR")
        self.assertIn(
            "ALPHA_VANTAGE_API_KEY",
            response.json()["error"]["message"],
        )
        run_id = UUID(response.json()["error"]["run_id"])
        self.assertEqual(self.repository.runs[run_id].status.value, "failed")
        self.assertEqual(self.repository.tables, {})
        self.assertEqual(self.repository.traces, {})
        self.assertEqual(
            self.repository.source_snapshots[run_id].raw_provider_evidence,
            {
                "AAPL": {"symbol_search": None},
                "MSFT": {"symbol_search": None},
            },
        )

    def test_invalid_run_linkage_returns_validation_error_without_artifacts(
        self,
    ) -> None:
        repository = InvalidLinkageCompsRunRepository()
        app.dependency_overrides[get_repository] = lambda: repository

        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            response = TestClient(app).post(
                "/v1/internal/tools/generate-comps-table",
                json={
                    "invocation_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "trigger_message_id": str(uuid4()),
                    "target_ticker": "AAPL",
                    "peer_tickers": ["MSFT"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                },
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("trigger Message", response.json()["error"]["message"])
        self.assertEqual(repository.runs, {})
        self.assertEqual(repository.tables, {})
        self.assertEqual(repository.traces, {})
        self.assertEqual(repository.source_snapshots, {})

    def test_company_input_order_does_not_change_deterministic_table_order(
        self,
    ) -> None:
        app.dependency_overrides[get_company_data_source] = (
            ReverseOrderCompanyDataSource
        )

        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            response = TestClient(app).post(
                "/v1/internal/tools/generate-comps-table",
                json={
                    "invocation_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "trigger_message_id": str(uuid4()),
                    "target_ticker": "AAPL",
                    "peer_tickers": ["MSFT", "GOOG"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                },
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            [row["ticker"] for row in response.json()["table"]["rows"]],
            ["AAPL", "MSFT", "GOOG"],
        )

    def test_readback_contract_exposes_persisted_run_artifacts(self) -> None:
        source_contract = yaml.safe_load(
            (REPO_ROOT / "api" / "openapi.yaml").read_text()
        )
        generated_contract = TestClient(app).get("/openapi.json").json()

        for path in (
            "/v1/runs/{run_id}",
            "/v1/runs/{run_id}/table",
            "/v1/runs/{run_id}/trace",
        ):
            with self.subTest(path=path):
                source_operation = source_contract["paths"][path]["get"]
                generated_operation = generated_contract["paths"][path]["get"]
                self.assertEqual(source_operation["security"], [])
                self.assertEqual(
                    set(source_operation["responses"]),
                    {"200", "400", "404", "503"},
                )
                self.assertEqual(
                    set(generated_operation["responses"]),
                    {"200", "400", "404", "503"},
                )

        self.assertEqual(
            source_contract["paths"]["/v1/runs/{run_id}/trace"]["get"]
            ["responses"]["200"]["content"]["application/json"]["schema"],
            {"$ref": "#/components/schemas/TraceResponse"},
        )
        self.assertNotIn(
            "/v1/runs/{run_id}/source-snapshot",
            source_contract["paths"],
        )
        self.assertNotIn("SourceSnapshot", source_contract["components"]["schemas"])
        self.assertNotIn("SourceSnapshot", generated_contract["components"]["schemas"])

    def test_source_contract_describes_the_reserve_calculate_finalize_fail_handshake(
        self,
    ) -> None:
        source_contract = yaml.safe_load(
            (REPO_ROOT / "api" / "openapi.yaml").read_text()
        )
        generated_contract = TestClient(app).get("/openapi.json").json()

        reserve_path = "/v1/internal/tools/reserve-comps-run"
        self.assertEqual(
            source_contract["paths"][reserve_path]["post"]["responses"]["200"]
            ["content"]["application/json"]["schema"],
            {"$ref": "#/components/schemas/RunResponse"},
        )
        self.assertIn(reserve_path, generated_contract["paths"])

        generate_path = "/v1/internal/tools/generate-comps-table"
        self.assertEqual(
            source_contract["paths"][generate_path]["post"]["responses"]["200"]
            ["content"]["application/json"]["schema"],
            {"$ref": "#/components/schemas/GenerateCompsDraftResponse"},
        )

        for path in (
            "/v1/internal/runs/{run_id}/finalize",
            "/v1/internal/runs/{run_id}/fail",
        ):
            with self.subTest(path=path):
                self.assertIn(path, source_contract["paths"])
                self.assertIn(path, generated_contract["paths"])
                self.assertTrue(source_contract["paths"][path]["post"]["x-internal"])

        finalize_path = "/v1/internal/runs/{run_id}/finalize"
        for contract in (source_contract, generated_contract):
            self.assertIn(
                "original successful Run unchanged",
                contract["paths"][finalize_path]["post"]["description"],
            )

    def test_generate_contract_declares_invocation_conflict(self) -> None:
        source_contract = yaml.safe_load(
            (REPO_ROOT / "api" / "openapi.yaml").read_text()
        )
        generated_contract = TestClient(app).get("/openapi.json").json()
        path = "/v1/internal/tools/generate-comps-table"

        self.assertEqual(
            source_contract["paths"][path]["post"]["responses"]["409"],
            {"$ref": "#/components/responses/Conflict"},
        )
        self.assertEqual(
            source_contract["components"]["responses"]["Conflict"]
            ["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/ErrorResponse",
        )
        self.assertEqual(
            generated_contract["paths"][path]["post"]["responses"]["409"]
            ["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/ErrorResponse",
        )
        self.assertIn(
            "CONFLICT",
            source_contract["components"]["schemas"]["ErrorResponse"]["properties"]
            ["error"]["properties"]["code"]["enum"],
        )

    def test_readback_returns_structured_not_found_and_validation_errors(
        self,
    ) -> None:
        client = TestClient(app)

        for suffix in ("", "/table", "/trace"):
            with self.subTest(error="not_found", suffix=suffix):
                response = client.get(f"/v1/runs/{uuid4()}{suffix}")
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(response.json()["error"]["code"], "NOT_FOUND")

            with self.subTest(error="invalid_id", suffix=suffix):
                response = client.get(f"/v1/runs/not-a-uuid{suffix}")
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(
                    response.json()["error"]["code"],
                    "VALIDATION_ERROR",
                )

    def test_readback_returns_service_unavailable_when_persistence_fails(
        self,
    ) -> None:
        def unavailable_repository() -> InMemoryCompsRunRepository:
            raise CompsPersistenceUnavailable("Comps persistence is unavailable.")

        app.dependency_overrides[get_repository] = unavailable_repository
        client = TestClient(app)

        for suffix in ("", "/table", "/trace"):
            with self.subTest(suffix=suffix):
                response = client.get(f"/v1/runs/{uuid4()}{suffix}")
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(
                    response.json()["error"]["code"],
                    "INTERNAL_ERROR",
                )


if __name__ == "__main__":
    unittest.main()
