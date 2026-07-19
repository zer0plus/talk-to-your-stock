from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import yaml

from comps_service.calculator import CompanyCompsInput
from comps_service.main import (
    app,
    get_company_data_source,
    get_repository,
    get_ticker_validator,
)
from comps_service.repository import CompsPersistenceUnavailable, InvalidRunLinkage
from comps_service.run_service import DuplicateToolInvocation
from talk_to_your_stock_shared import Run, RunTableResponse


INTERNAL_TOOL_TOKEN = "test-internal-tool-token"
REPO_ROOT = Path(__file__).resolve().parents[2]


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
        self.invocations: dict[UUID, UUID] = {}

    def save_succeeded_run(
        self,
        *,
        invocation_id: UUID,
        run: Run,
        table: RunTableResponse,
    ) -> None:
        if invocation_id in self.invocations:
            raise DuplicateToolInvocation(
                "Tool invocation has already produced a Run."
            )
        self.invocations[invocation_id] = run.id
        self.runs[run.id] = run
        self.tables[run.id] = table

    def get_run(self, run_id: UUID) -> Run | None:
        return self.runs.get(run_id)

    def get_table(self, run_id: UUID) -> RunTableResponse | None:
        return self.tables.get(run_id)


class InvalidLinkageCompsRunRepository(InMemoryCompsRunRepository):
    def save_succeeded_run(
        self,
        *,
        invocation_id: UUID,
        run: Run,
        table: RunTableResponse,
    ) -> None:
        del invocation_id, run, table
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

    def test_readback_contract_exposes_only_persisted_run_artifacts(self) -> None:
        source_contract = yaml.safe_load(
            (REPO_ROOT / "api" / "openapi.yaml").read_text()
        )
        generated_contract = TestClient(app).get("/openapi.json").json()

        for path in ("/v1/runs/{run_id}", "/v1/runs/{run_id}/table"):
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

        trace_path = "/v1/runs/{run_id}/trace"
        self.assertNotIn(trace_path, source_contract["paths"])
        self.assertNotIn(trace_path, generated_contract["paths"])

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

        for suffix in ("", "/table"):
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

        for suffix in ("", "/table"):
            with self.subTest(suffix=suffix):
                response = client.get(f"/v1/runs/{uuid4()}{suffix}")
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(
                    response.json()["error"]["code"],
                    "INTERNAL_ERROR",
                )


if __name__ == "__main__":
    unittest.main()
