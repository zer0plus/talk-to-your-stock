from __future__ import annotations

import os
import subprocess
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from comps_service.calculator import CompanyCompsInput
from comps_service.main import (
    app,
    get_company_data_source,
    get_ticker_validator,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_DATABASE_URL_VAR = "COMPS_MIGRATION_TEST_DATABASE_URL"
INTERNAL_TOOL_TOKEN = "postgres-test-internal-token"


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
            )
            for ticker in tickers
        ]


class CompsMigrationsTest(unittest.TestCase):
    def test_upgrade_renders_comps_run_and_table_schema(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                str(REPO_ROOT / "alembic.ini"),
                "upgrade",
                "head",
                "--sql",
            ],
            cwd=REPO_ROOT,
            env={
                **os.environ,
                "DATABASE_URL": "postgresql://postgres:postgres@localhost/test",
            },
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        sql = " ".join(result.stdout.lower().split())
        self.assertIn("create table comps_runs", sql)
        self.assertIn("create table comps_tables", sql)
        self.assertIn("unique (invocation_id)", sql)
        self.assertIn(
            "foreign key(run_id) references comps_runs (id) on delete cascade",
            sql,
        )

    @unittest.skipUnless(
        os.environ.get(MIGRATION_DATABASE_URL_VAR),
        f"{MIGRATION_DATABASE_URL_VAR} is required for PostgreSQL integration.",
    )
    def test_migrated_database_persists_run_and_table_across_requests(self) -> None:
        database_url = os.environ[MIGRATION_DATABASE_URL_VAR]
        migration_config = Config(str(REPO_ROOT / "alembic.ini"))
        env = {
            "DATABASE_URL": database_url,
            "COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN,
        }

        with patch.dict(os.environ, env, clear=False):
            command.upgrade(migration_config, "head")
            try:
                app.dependency_overrides[get_company_data_source] = (
                    ControlledCompanyDataSource
                )
                app.dependency_overrides[get_ticker_validator] = (
                    SupportedTickerValidator
                )
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
                run_id = created.json()["run"]["id"]

                app.dependency_overrides.clear()
                app.dependency_overrides[get_company_data_source] = (
                    ControlledCompanyDataSource
                )
                app.dependency_overrides[get_ticker_validator] = (
                    SupportedTickerValidator
                )
                run_readback = TestClient(app).get(f"/v1/runs/{run_id}")
                table_readback = TestClient(app).get(f"/v1/runs/{run_id}/table")

                self.assertEqual(run_readback.status_code, 200, run_readback.text)
                self.assertEqual(table_readback.status_code, 200, table_readback.text)
                self.assertEqual(run_readback.json()["run"], created.json()["run"])
                self.assertEqual(table_readback.json(), created.json()["table"])
            finally:
                app.dependency_overrides.clear()
                command.downgrade(migration_config, "base")


if __name__ == "__main__":
    unittest.main()
