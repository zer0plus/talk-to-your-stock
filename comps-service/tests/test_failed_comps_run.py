from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import httpx

from comps_service.artifacts import SourceSnapshot
from comps_service.calculator import CompanyCompsInput
from comps_service.main import (
    app,
    get_company_data_source,
    get_repository,
    get_ticker_validator,
)
from comps_service.provider import AlphaVantageCompanyDataSource
from comps_service.run_service import (
    CompanyDataLoadFailure,
    CompsRunExecutionError,
    LoadedCompanyData,
    RecoveredFailedRun,
)
from talk_to_your_stock_shared import (
    ErrorResponse,
    GenerateCompsToolResponse,
    Run,
    RunStatus,
    RunTableResponse,
    TraceResponse,
)


INTERNAL_TOOL_TOKEN = "test-internal-tool-token"
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "alpha_vantage"


class SupportedTickerValidator:
    def is_supported(self, _ticker: str) -> bool:
        return True


class InMemoryCompsRunRepository:
    def __init__(self) -> None:
        self.runs: dict[UUID, Run] = {}
        self.tables: dict[UUID, RunTableResponse] = {}
        self.traces: dict[UUID, TraceResponse] = {}
        self.source_snapshots: dict[UUID, SourceSnapshot] = {}
        self.invocations: dict[UUID, UUID] = {}
        self.failures: dict[UUID, tuple[int, ErrorResponse]] = {}
        self.validation_evidence: dict[UUID, dict[str, dict[str, object]]] = {}

    def get_succeeded_result(
        self, *, invocation_id: UUID
    ) -> GenerateCompsToolResponse | None:
        run_id = self.invocations.get(invocation_id)
        if run_id is None or run_id not in self.tables:
            return None
        run = self.runs[run_id]
        return GenerateCompsToolResponse(
            run=run,
            table=self.tables[run_id],
            trace=self.traces[run_id],
            warnings=run.warnings,
        )

    def get_failed_result(
        self, *, invocation_id: UUID
    ) -> RecoveredFailedRun | None:
        run_id = self.invocations.get(invocation_id)
        if run_id is None or run_id not in self.failures:
            return None
        status_code, error = self.failures[run_id]
        return RecoveredFailedRun(
            run=self.runs[run_id],
            status_code=status_code,
            error=error,
        )

    def get_run_by_invocation(self, *, invocation_id: UUID) -> Run | None:
        run_id = self.invocations.get(invocation_id)
        return self.runs.get(run_id) if run_id is not None else None

    def get_active_run_by_invocation(self, *, invocation_id: UUID) -> Run | None:
        run = self.get_run_by_invocation(invocation_id=invocation_id)
        return run if run is not None and run.status == RunStatus.RUNNING else None

    def get_validation_evidence(self, *, invocation_id: UUID):
        return self.validation_evidence.get(invocation_id)

    def reserve_run(self, *, invocation_id: UUID, run: Run, validation_evidence) -> Run:
        run_id = self.invocations.get(invocation_id)
        if run_id is not None:
            return self.runs[run_id]
        self.invocations[invocation_id] = run.id
        self.validation_evidence[invocation_id] = deepcopy(validation_evidence)
        self.runs[run.id] = run
        return run

    def claim_run(self, *, run_id, owner_id, lease_seconds):
        del owner_id, lease_seconds
        run = self.runs[run_id]
        if run.status != RunStatus.QUEUED:
            return None
        claimed = run.model_copy(
            update={"status": RunStatus.RUNNING, "started_at": datetime.now(UTC)}
        )
        self.runs[run_id] = claimed
        return claimed

    def renew_run_lease(self, **_kwargs) -> bool:
        return True

    def complete_succeeded_run(
        self, *, owner_id, run, table, trace, source_snapshot
    ) -> bool:
        del owner_id
        self.runs[run.id] = run
        self.tables[run.id] = table
        self.traces[run.id] = trace
        self.source_snapshots[run.id] = source_snapshot
        return True

    def complete_failed_run(
        self, *, owner_id, run, status_code, error, source_snapshot
    ) -> bool:
        del owner_id
        self.runs[run.id] = run
        self.failures[run.id] = (status_code, error)
        self.source_snapshots[run.id] = source_snapshot
        return True

    def get_run(self, run_id: UUID) -> Run | None:
        return self.runs.get(run_id)

    def get_table(self, run_id: UUID) -> RunTableResponse | None:
        return self.tables.get(run_id)

    def get_trace(self, run_id: UUID) -> TraceResponse | None:
        return self.traces.get(run_id)

    def get_source_snapshot(self, run_id: UUID) -> SourceSnapshot | None:
        return self.source_snapshots.get(run_id)


def company_input(ticker: str) -> CompanyCompsInput:
    evidence_date = datetime(2026, 7, 17, tzinfo=UTC)
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
    return CompanyCompsInput(
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
        as_of=evidence_date,
        sources={field: f"provider.{ticker}.{field}" for field in fields},
        source_as_of={field: evidence_date for field in fields},
    )


class PartialEvidenceFailureDataSource:
    def load(
        self,
        *,
        tickers: list[str],
        currency: str,
    ) -> LoadedCompanyData:
        del tickers, currency
        raise CompanyDataLoadFailure(
            CompsRunExecutionError(
                "Alpha Vantage INCOME_STATEMENT returned no evidence for MSFT."
            ),
            partial_data=LoadedCompanyData(
                companies=[company_input("AAPL")],
                raw_provider_evidence={
                    "AAPL": {"global_quote": {"05. price": "10.0"}},
                    "MSFT": {"global_quote": {"05. price": "20.0"}},
                },
            ),
        )


class MissingPeerDataSource:
    def load(
        self,
        *,
        tickers: list[str],
        currency: str,
    ) -> LoadedCompanyData:
        del tickers, currency
        return LoadedCompanyData(
            companies=[company_input("AAPL")],
            raw_provider_evidence={
                "AAPL": {"global_quote": {"05. price": "10.0"}}
            },
        )


class ZeroLtmMetricsDataSource:
    def load(
        self,
        *,
        tickers: list[str],
        currency: str,
    ) -> LoadedCompanyData:
        del tickers, currency
        return LoadedCompanyData(
            companies=[
                replace(
                    company_input("AAPL"),
                    revenue_ltm=0,
                    ebit_ltm=0,
                    ebitda_ltm=0,
                    net_income_ltm=0,
                ),
                company_input("MSFT"),
            ],
            raw_provider_evidence={},
        )


class FailedCompsRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryCompsRunRepository()
        app.dependency_overrides[get_repository] = lambda: self.repository
        app.dependency_overrides[get_company_data_source] = (
            PartialEvidenceFailureDataSource
        )
        app.dependency_overrides[get_ticker_validator] = SupportedTickerValidator
        self.addCleanup(app.dependency_overrides.clear)

    def test_execution_failure_persists_failed_run_and_partial_evidence(self) -> None:
        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            client = TestClient(app)
            failed = client.post(
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

            self.assertEqual(failed.status_code, 502, failed.text)
            self.assertEqual(failed.json()["error"]["code"], "UPSTREAM_ERROR")
            run_id = UUID(failed.json()["error"]["run_id"])
            readback = client.get(f"/v1/runs/{run_id}")
            table_readback = client.get(f"/v1/runs/{run_id}/table")
            trace_readback = client.get(f"/v1/runs/{run_id}/trace")

        self.assertEqual(readback.status_code, 200, readback.text)
        run = readback.json()["run"]
        self.assertEqual(run["status"], "failed")
        self.assertIsNone(run["as_of"])
        self.assertEqual(
            run["error_message"],
            "Alpha Vantage INCOME_STATEMENT returned no evidence for MSFT.",
        )
        self.assertEqual(run["target_ticker"], "AAPL")
        self.assertEqual(run["peer_tickers"], ["MSFT"])
        self.assertEqual(table_readback.status_code, 404, table_readback.text)
        self.assertEqual(trace_readback.status_code, 404, trace_readback.text)
        self.assertEqual(self.repository.tables, {})
        self.assertEqual(self.repository.traces, {})

        snapshot = self.repository.get_source_snapshot(run_id)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(
            set(snapshot.raw_provider_evidence),
            {"AAPL", "MSFT"},
        )
        self.assertEqual(
            [company.ticker for company in snapshot.normalized_inputs],
            ["AAPL"],
        )

    def test_provider_failure_preserves_payloads_gathered_before_failure(self) -> None:
        provider_key = "FAKE_PROVIDER_KEY_123"
        fixture = json.loads(
            (FIXTURE_ROOT / "usd_company_latest.json").read_text()
        )

        def respond(request):
            function = request.url.params["function"]
            symbol = request.url.params["symbol"]
            if function == "INCOME_STATEMENT" and symbol == "MSFT":
                return httpx.Response(
                    429,
                    json={
                        "Information": (
                            "Provider quota exhausted. "
                            f"API key as {provider_key}"
                        ),
                        provider_key: "echoed credential key",
                    },
                )
            payload = deepcopy(fixture[function])
            if function == "GLOBAL_QUOTE":
                payload["Global Quote"]["01. symbol"] = symbol
            else:
                symbol_field = "Symbol" if function == "OVERVIEW" else "symbol"
                payload[symbol_field] = symbol
            return httpx.Response(200, json=payload)

        source = AlphaVantageCompanyDataSource(
            environ={
                "ALPHA_VANTAGE_API_KEY": provider_key,
                "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
            },
            transport=httpx.MockTransport(respond),
            validated_ticker_matches={
                ticker: {
                    "1. symbol": ticker,
                    "3. type": "Equity",
                    "8. currency": "USD",
                    provider_key: "echoed credential key",
                }
                for ticker in ("AAPL", "MSFT", "GOOG")
            },
        )
        app.dependency_overrides[get_company_data_source] = lambda: source
        thread_id = uuid4()
        trigger_message_id = uuid4()

        with (
            patch.dict(
                os.environ,
                {
                    "COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN,
                    "TALK_TO_YOUR_STOCK_ENV": "local",
                },
                clear=True,
            ),
            self.assertLogs("comps_service.main", level="ERROR") as captured_logs,
        ):
            failed = TestClient(app).post(
                "/v1/internal/tools/generate-comps-table",
                json={
                    "invocation_id": str(uuid4()),
                    "thread_id": str(thread_id),
                    "trigger_message_id": str(trigger_message_id),
                    "target_ticker": "AAPL",
                    "peer_tickers": ["MSFT", "GOOG"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                },
                headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
            )

        self.assertEqual(failed.status_code, 502, failed.text)
        self.assertEqual(
            failed.json()["error"]["message"],
            "Alpha Vantage request limit was reached while loading MSFT.",
        )
        self.assertEqual(
            failed.json()["error"]["details"],
            {
                "provider": "alpha_vantage",
                "operation": "INCOME_STATEMENT",
                "subject": "MSFT",
                "thread_id": str(thread_id),
                "trigger_message_id": str(trigger_message_id),
            },
        )
        self.assertNotIn(provider_key, failed.text)
        run_id = UUID(failed.json()["error"]["run_id"])
        snapshot = self.repository.get_source_snapshot(run_id)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(
            [company.ticker for company in snapshot.normalized_inputs],
            ["AAPL"],
        )
        self.assertEqual(
            set(snapshot.raw_provider_evidence),
            {"AAPL", "MSFT", "GOOG"},
        )
        self.assertEqual(
            set(snapshot.raw_provider_evidence["MSFT"]),
            {
                "symbol_search",
                "global_quote",
                "overview",
                "income_statement",
            },
        )
        self.assertEqual(
            set(snapshot.raw_provider_evidence["GOOG"]),
            {"symbol_search"},
        )
        self.assertEqual(
            snapshot.raw_provider_evidence["GOOG"]["symbol_search"]["1. symbol"],
            "GOOG",
        )
        self.assertEqual(
            snapshot.raw_provider_evidence["MSFT"]["income_statement"],
            {
                "Information": (
                    "Provider quota exhausted. API key as [REDACTED]"
                ),
                "[REDACTED]": "echoed credential key",
            },
        )
        self.assertNotIn(
            provider_key,
            json.dumps(snapshot.raw_provider_evidence),
        )
        log_output = "\n".join(captured_logs.output)
        self.assertIn(str(run_id), log_output)
        self.assertIn(str(thread_id), log_output)
        self.assertIn(str(trigger_message_id), log_output)
        self.assertIn(failed.json()["error"]["message"], log_output)
        self.assertNotIn(provider_key, log_output)

    def test_provider_failure_preserves_non_json_response_body(self) -> None:
        provider_key = "FAKE_PROVIDER_KEY_123"
        response_body = f"<html>Proxy failure for {provider_key}</html>"

        source = AlphaVantageCompanyDataSource(
            environ={
                "ALPHA_VANTAGE_API_KEY": provider_key,
                "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
            },
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    502,
                    text=response_body,
                    headers={"Content-Type": "text/html"},
                )
            ),
            validated_ticker_matches={
                "AAPL": {
                    "1. symbol": "AAPL",
                    "3. type": "Equity",
                    "8. currency": "USD",
                },
            },
        )
        app.dependency_overrides[get_company_data_source] = lambda: source

        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            failed = TestClient(app).post(
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

        self.assertEqual(failed.status_code, 502, failed.text)
        run_id = UUID(failed.json()["error"]["run_id"])
        snapshot = self.repository.get_source_snapshot(run_id)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(
            snapshot.raw_provider_evidence["AAPL"]["global_quote"],
            {
                "raw_response_body": "<html>Proxy failure for [REDACTED]</html>"
            },
        )

    def test_fx_failure_preserves_invalid_fx_payload(self) -> None:
        company_fixture = json.loads(
            (FIXTURE_ROOT / "usd_company_latest.json").read_text()
        )
        fx_fixture = json.loads(
            (FIXTURE_ROOT / "cad_to_usd_latest.json").read_text()
        )
        company_fixture["OVERVIEW"]["Currency"] = "CAD"
        for function in ("INCOME_STATEMENT", "BALANCE_SHEET"):
            for report in company_fixture[function]["quarterlyReports"]:
                report["reportedCurrency"] = "CAD"
        fx_fixture["Realtime Currency Exchange Rate"][
            "6. Last Refreshed"
        ] = "not-a-date"

        def respond(request):
            function = request.url.params["function"]
            if function == "CURRENCY_EXCHANGE_RATE":
                return httpx.Response(200, json=deepcopy(fx_fixture))
            symbol = request.url.params["symbol"]
            payload = deepcopy(company_fixture[function])
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
            validated_ticker_matches={
                ticker: {
                    "1. symbol": ticker,
                    "3. type": "Equity",
                    "8. currency": "USD",
                }
                for ticker in ("AAPL", "MSFT")
            },
        )
        app.dependency_overrides[get_company_data_source] = lambda: source

        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            failed = TestClient(app).post(
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

        self.assertEqual(failed.status_code, 502, failed.text)
        run_id = UUID(failed.json()["error"]["run_id"])
        snapshot = self.repository.get_source_snapshot(run_id)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(
            snapshot.raw_provider_evidence["AAPL"][
                "currency_exchange_rates"
            ]["CAD_USD"]["Realtime Currency Exchange Rate"]["6. Last Refreshed"],
            "not-a-date",
        )

    def test_missing_requested_peer_fails_the_whole_run(self) -> None:
        app.dependency_overrides[get_company_data_source] = MissingPeerDataSource

        with patch.dict(
            os.environ,
            {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
            clear=True,
        ):
            failed = TestClient(app).post(
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

        self.assertEqual(failed.status_code, 502, failed.text)
        run_id = UUID(failed.json()["error"]["run_id"])
        run = self.repository.get_run(run_id)
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.status.value, "failed")
        self.assertEqual(run.peer_tickers, ["MSFT"])
        self.assertIn("every requested peer", run.error_message or "")
        self.assertEqual(self.repository.tables, {})
        self.assertEqual(self.repository.traces, {})

    def test_missing_individual_metric_becomes_null_and_run_warning(self) -> None:
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
            if function == "INCOME_STATEMENT" and symbol == "MSFT":
                for metric in ("totalRevenue", "ebit", "ebitda", "netIncome"):
                    payload["quarterlyReports"][0][metric] = "None"
            return httpx.Response(200, json=payload)

        source = AlphaVantageCompanyDataSource(
            environ={
                "ALPHA_VANTAGE_API_KEY": "fixture-key",
                "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
            },
            transport=httpx.MockTransport(respond),
            validated_ticker_matches={
                ticker: {
                    "1. symbol": ticker,
                    "3. type": "Equity",
                    "8. currency": "USD",
                }
                for ticker in ("AAPL", "MSFT")
            },
        )
        app.dependency_overrides[get_company_data_source] = lambda: source

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

        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        warnings = [
            "MSFT.revenue_ltm is unavailable; ev_to_revenue is null.",
            "MSFT.ebit_ltm is unavailable; ev_to_ebit is null.",
            "MSFT.ebitda_ltm is unavailable; ev_to_ebitda is null.",
            "MSFT.net_income_ltm is unavailable; pe is null.",
        ]
        self.assertEqual(body["warnings"], warnings)
        self.assertEqual(body["run"]["warnings"], warnings)
        msft_row = next(
            row for row in body["table"]["rows"] if row["ticker"] == "MSFT"
        )
        for metric in (
            "revenue_ltm",
            "ebit_ltm",
            "ebitda_ltm",
            "net_income_ltm",
            "ev_to_revenue",
            "ev_to_ebit",
            "ev_to_ebitda",
            "pe",
        ):
            with self.subTest(metric=metric):
                self.assertIsNone(msft_row[metric])
        self.assertIsNotNone(msft_row["enterprise_value"])
        self.assertEqual(
            {row["ticker"] for row in body["table"]["rows"]},
            {"AAPL", "MSFT"},
        )

        run_id = UUID(body["run"]["id"])
        persisted = client.get(f"/v1/runs/{run_id}")
        self.assertEqual(persisted.status_code, 200, persisted.text)
        self.assertEqual(persisted.json()["run"]["warnings"], warnings)
        snapshot = self.repository.get_source_snapshot(run_id)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        msft_input = next(
            company
            for company in snapshot.normalized_inputs
            if company.ticker == "MSFT"
        )
        self.assertIsNone(msft_input.revenue_ltm)
        self.assertIsNone(msft_input.ebit_ltm)
        self.assertIsNone(msft_input.ebitda_ltm)
        self.assertIsNone(msft_input.net_income_ltm)

    def test_zero_ltm_metrics_become_null_with_run_warnings(self) -> None:
        app.dependency_overrides[get_company_data_source] = ZeroLtmMetricsDataSource

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

        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        warnings = [
            "AAPL.revenue_ltm is zero; ev_to_revenue is null.",
            "AAPL.ebit_ltm is zero; ev_to_ebit is null.",
            "AAPL.ebitda_ltm is zero; ev_to_ebitda is null.",
            "AAPL.net_income_ltm is zero; pe is null.",
        ]
        self.assertEqual(body["warnings"], warnings)
        self.assertEqual(body["run"]["warnings"], warnings)
        target_row = body["table"]["rows"][0]
        for metric in ("ev_to_revenue", "ev_to_ebit", "ev_to_ebitda", "pe"):
            with self.subTest(metric=metric):
                self.assertIsNone(target_row[metric])

        run_id = UUID(body["run"]["id"])
        persisted = client.get(f"/v1/runs/{run_id}")
        self.assertEqual(persisted.status_code, 200, persisted.text)
        self.assertEqual(persisted.json()["run"]["warnings"], warnings)


if __name__ == "__main__":
    unittest.main()
