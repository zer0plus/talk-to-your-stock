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

from comps_service.artifacts import SourceSnapshot
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
from talk_to_your_stock_shared import Run, RunTableResponse, TraceResponse


INTERNAL_TOOL_TOKEN = "test-internal-tool-token"
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "alpha_vantage"


class SupportedTickerValidator:
    def is_supported(self, _ticker: str) -> bool:
        return True


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
        self.tables: dict[UUID, RunTableResponse] = {}
        self.traces: dict[UUID, TraceResponse] = {}
        self.source_snapshots: dict[UUID, SourceSnapshot] = {}
        self.invocations: dict[UUID, UUID] = {}

    def save_succeeded_run(
        self,
        *,
        invocation_id: UUID,
        run: Run,
        table: RunTableResponse,
        trace: TraceResponse,
        source_snapshot: SourceSnapshot,
    ) -> None:
        if invocation_id in self.invocations:
            raise DuplicateToolInvocation(
                "Tool invocation has already produced a Run."
            )
        self.invocations[invocation_id] = run.id
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


class InvalidLinkageCompsRunRepository(InMemoryCompsRunRepository):
    def save_succeeded_run(
        self,
        *,
        invocation_id: UUID,
        run: Run,
        table: RunTableResponse,
        trace: TraceResponse,
        source_snapshot: SourceSnapshot,
    ) -> None:
        del invocation_id, run, table, trace, source_snapshot
        raise InvalidRunLinkage("Run must reference its persisted trigger Message.")


class SuccessfulCompsRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryCompsRunRepository()
        app.dependency_overrides[get_repository] = lambda: self.repository
        app.dependency_overrides[get_company_data_source] = ControlledCompanyDataSource
        app.dependency_overrides[get_ticker_validator] = SupportedTickerValidator
        self.addCleanup(app.dependency_overrides.clear)

    def test_explicit_peer_request_returns_succeeded_run_with_every_company(
        self,
    ) -> None:
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
                    "target_ticker": "aapl",
                    "peer_tickers": ["msft", "goog"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                    "currency": "USD",
                },
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["run"]["status"], "succeeded")
        self.assertEqual(body["run"]["target_ticker"], "AAPL")
        self.assertEqual(body["run"]["peer_tickers"], ["MSFT", "GOOG"])
        self.assertEqual(
            {row["ticker"] for row in body["table"]["rows"]},
            {"AAPL", "MSFT", "GOOG"},
        )
        self.assertEqual(
            [row["ticker"] for row in body["table"]["rows"] if row["is_target"]],
            ["AAPL"],
        )
        self.assertEqual(body["table"]["run_id"], body["run"]["id"])

    def test_alpha_vantage_payloads_are_normalized_through_the_comps_tool(
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
            return httpx.Response(200, json=payload)

        source = AlphaVantageCompanyDataSource(
            environ={
                "ALPHA_VANTAGE_API_KEY": "fixture-key",
                "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
            },
            transport=httpx.MockTransport(respond),
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
        row = response.json()["table"]["rows"][0]
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
        run_id = UUID(response.json()["run"]["id"])
        snapshot = self.repository.get_source_snapshot(run_id)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(
            set(snapshot.raw_provider_evidence["AAPL"]),
            {"global_quote", "overview", "income_statement", "balance_sheet"},
        )
        normalized = snapshot.normalized_inputs[0]
        self.assertTrue(
            all(
                "overview.AAPL.Currency=USD" in source
                for field, source in normalized.sources.items()
                if field != "shares_outstanding"
            )
        )
        trace_inputs = {
            trace_input["field"]: trace_input
            for formula in response.json()["trace"]["formulas"]
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

    def test_explicit_fx_evidence_converts_every_monetary_input(self) -> None:
        company_fixture = json.loads(
            (FIXTURE_ROOT / "usd_company_latest.json").read_text()
        )
        fx_fixture = json.loads(
            (FIXTURE_ROOT / "cad_to_usd_latest.json").read_text()
        )
        fx_requests = 0

        def respond(request):
            nonlocal fx_requests
            function = request.url.params["function"]
            if function == "CURRENCY_EXCHANGE_RATE":
                fx_requests += 1
                self.assertEqual(request.url.params["from_currency"], "CAD")
                self.assertEqual(request.url.params["to_currency"], "USD")
                return httpx.Response(200, json=fx_fixture)

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

        source = AlphaVantageCompanyDataSource(
            environ={
                "ALPHA_VANTAGE_API_KEY": "fixture-key",
                "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
            },
            transport=httpx.MockTransport(respond),
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
                "share_price": 107.4375,
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
        snapshot = self.repository.get_source_snapshot(run_id)
        assert snapshot is not None
        normalized = snapshot.normalized_inputs[0]
        self.assertEqual(normalized.currency, "USD")
        self.assertTrue(
            all(
                "currency_exchange_rate.CAD_USD.5. Exchange Rate" in source
                for field, source in normalized.sources.items()
                if field != "shares_outstanding"
            )
        )
        self.assertEqual(fx_requests, 1)

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

            run_response = client.get(f"/v1/runs/{run_id}")
            table_response = client.get(f"/v1/runs/{run_id}/table")

        self.assertEqual(run_response.status_code, 200, run_response.text)
        self.assertEqual(table_response.status_code, 200, table_response.text)
        self.assertEqual(run_response.json()["run"], created.json()["run"])
        self.assertEqual(table_response.json(), created.json()["table"])

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

    def test_repeated_invocation_returns_conflict_without_duplicate_artifacts(
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
            repeated = client.post(
                "/v1/internal/tools/generate-comps-table",
                json=request,
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )

        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(repeated.status_code, 409, repeated.text)
        self.assertEqual(repeated.json()["error"]["code"], "CONFLICT")
        self.assertIsNone(repeated.json()["error"]["details"])
        self.assertEqual(len(self.repository.runs), 1)
        self.assertEqual(len(self.repository.tables), 1)
        self.assertEqual(len(self.repository.traces), 1)
        self.assertEqual(len(self.repository.source_snapshots), 1)

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
        self.assertEqual(self.repository.runs, {})
        self.assertEqual(self.repository.tables, {})

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
