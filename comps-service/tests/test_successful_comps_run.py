from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from comps_service.calculator import CompanyCompsInput
from comps_service.main import (
    app,
    get_company_data_source,
    get_repository,
    get_ticker_validator,
)
from talk_to_your_stock_shared import Run, RunTableResponse


INTERNAL_TOOL_TOKEN = "test-internal-tool-token"


class SupportedTickerValidator:
    def is_supported(self, _ticker: str) -> bool:
        return True


class ControlledCompanyDataSource:
    def load_companies(
        self,
        *,
        tickers: list[str],
        currency: str,
    ) -> list[CompanyCompsInput]:
        return [
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
                sources={},
            )
            for ticker in tickers
        ]


class ReverseOrderCompanyDataSource(ControlledCompanyDataSource):
    def load_companies(
        self,
        *,
        tickers: list[str],
        currency: str,
    ) -> list[CompanyCompsInput]:
        return list(
            reversed(super().load_companies(tickers=tickers, currency=currency))
        )


class InMemoryCompsRunRepository:
    def __init__(self) -> None:
        self.runs: dict[UUID, Run] = {}
        self.tables: dict[UUID, RunTableResponse] = {}

    def save_succeeded_run(
        self,
        *,
        invocation_id: UUID,
        run: Run,
        table: RunTableResponse,
    ) -> None:
        del invocation_id
        self.runs[run.id] = run
        self.tables[run.id] = table

    def get_run(self, run_id: UUID) -> Run | None:
        return self.runs.get(run_id)

    def get_table(self, run_id: UUID) -> RunTableResponse | None:
        return self.tables.get(run_id)


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

    def test_runtime_path_fails_clearly_without_real_company_data_source(
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
        self.assertIn("Real provider and FX", response.json()["error"]["message"])
        self.assertEqual(self.repository.runs, {})
        self.assertEqual(self.repository.tables, {})

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


if __name__ == "__main__":
    unittest.main()
