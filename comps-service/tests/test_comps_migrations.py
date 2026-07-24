from __future__ import annotations

import os
import subprocess
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from comps_service.calculator import CompanyCompsInput
from comps_service.main import (
    app,
    get_company_data_source,
    get_ticker_validator,
)
from comps_service.repository import PostgresCompsRunRepository
from comps_service.run_service import LoadedCompanyData


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_DATABASE_URL_VAR = "COMPS_MIGRATION_TEST_DATABASE_URL"
INTERNAL_TOOL_TOKEN = "postgres-test-internal-token"


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
                ticker: {"raw_marker": f"raw-provider-{ticker}"}
                for ticker in tickers
            },
        )


class CompsMigrationsTest(unittest.TestCase):
    def test_upgrade_renders_comps_run_audit_schema(self) -> None:
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
        self.assertIn("create table comps_traces", sql)
        self.assertIn("create table comps_source_snapshots", sql)
        self.assertIn(
            "constraint comps_runs_invocation_id_unique unique (invocation_id)",
            sql,
        )
        self.assertIn(
            "unique (id, thread_id)",
            sql,
        )
        self.assertIn(
            "foreign key(trigger_message_id, thread_id) references "
            "web_bff_messages (id, thread_id) on delete restrict",
            sql,
        )
        self.assertIn(
            "foreign key(run_id) references comps_runs (id) on delete cascade",
            sql,
        )
        self.assertIn(
            "constraint comps_traces_run_fk foreign key(run_id) references "
            "comps_runs (id) on delete cascade",
            sql,
        )
        self.assertIn(
            "constraint comps_source_snapshots_run_fk foreign key(run_id) "
            "references comps_runs (id) on delete cascade",
            sql,
        )

    @unittest.skipUnless(
        os.environ.get(MIGRATION_DATABASE_URL_VAR),
        f"{MIGRATION_DATABASE_URL_VAR} is required for PostgreSQL integration.",
    )
    def test_migrated_database_enforces_linkage_and_persists_artifacts(self) -> None:
        import psycopg

        database_url = os.environ[MIGRATION_DATABASE_URL_VAR]
        migration_config = Config(str(REPO_ROOT / "alembic.ini"))
        env = {
            "DATABASE_URL": database_url,
            "COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN,
        }

        with patch.dict(os.environ, env, clear=False):
            command.downgrade(migration_config, "base")
            command.upgrade(migration_config, "0001_web_bff_schema")
            command.upgrade(migration_config, "head")
            try:
                (
                    thread_id,
                    trigger_message_id,
                    other_trigger_message_id,
                ) = _seed_web_bff_product_state(database_url)
                app.dependency_overrides[get_company_data_source] = (
                    ControlledCompanyDataSource
                )
                app.dependency_overrides[get_ticker_validator] = (
                    SupportedTickerValidator
                )
                client = TestClient(app)
                generate_request = _generate_request(
                    thread_id=thread_id,
                    trigger_message_id=trigger_message_id,
                )
                created = client.post(
                    "/v1/internal/tools/generate-comps-table",
                    json=generate_request,
                    headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
                )
                self.assertEqual(created.status_code, 200, created.text)
                run_id = created.json()["run"]["id"]

                repeated = client.post(
                    "/v1/internal/tools/generate-comps-table",
                    json=generate_request,
                    headers={"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"},
                )
                self.assertEqual(repeated.status_code, 409, repeated.text)
                self.assertEqual(repeated.json()["error"]["code"], "CONFLICT")

                for trigger_message_id_value in (
                    uuid4(),
                    other_trigger_message_id,
                ):
                    with self.subTest(
                        trigger_message_id=trigger_message_id_value,
                    ):
                        rejected = client.post(
                            "/v1/internal/tools/generate-comps-table",
                            json=_generate_request(
                                thread_id=thread_id,
                                trigger_message_id=trigger_message_id_value,
                            ),
                            headers={
                                "Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"
                            },
                        )
                        self.assertEqual(rejected.status_code, 400, rejected.text)
                        self.assertEqual(
                            rejected.json()["error"]["code"],
                            "VALIDATION_ERROR",
                        )

                with psycopg.connect(database_url) as connection:
                    with connection.cursor() as cursor:
                        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                            cursor.execute(
                                "delete from web_bff_messages where id = %s",
                                (trigger_message_id,),
                            )
                        connection.rollback()

                app.dependency_overrides.clear()
                app.dependency_overrides[get_company_data_source] = (
                    ControlledCompanyDataSource
                )
                app.dependency_overrides[get_ticker_validator] = (
                    SupportedTickerValidator
                )
                run_readback = TestClient(app).get(f"/v1/runs/{run_id}")
                table_readback = TestClient(app).get(f"/v1/runs/{run_id}/table")
                trace_readback = TestClient(app).get(f"/v1/runs/{run_id}/trace")
                source_snapshot = PostgresCompsRunRepository(
                    database_url=database_url
                ).get_source_snapshot(UUID(run_id))

                self.assertEqual(run_readback.status_code, 200, run_readback.text)
                self.assertEqual(table_readback.status_code, 200, table_readback.text)
                self.assertEqual(trace_readback.status_code, 200, trace_readback.text)
                self.assertEqual(run_readback.json()["run"], created.json()["run"])
                self.assertEqual(table_readback.json(), created.json()["table"])
                self.assertEqual(trace_readback.json(), created.json()["trace"])
                self.assertIsNotNone(source_snapshot)
                assert source_snapshot is not None
                self.assertEqual(
                    source_snapshot.raw_provider_evidence["AAPL"],
                    {"raw_marker": "raw-provider-AAPL"},
                )
                self.assertEqual(
                    _linked_record_counts(database_url, trigger_message_id),
                    (1, 1, 1, 1, 1),
                )
            finally:
                app.dependency_overrides.clear()
                command.downgrade(migration_config, "base")

def _seed_web_bff_product_state(database_url: str) -> tuple[UUID, UUID, UUID]:
    import psycopg

    user_id = uuid4()
    thread_id = uuid4()
    trigger_message_id = uuid4()
    other_thread_id = uuid4()
    other_trigger_message_id = uuid4()
    now = datetime.now(UTC)
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                insert into web_bff_users (
                    id, email, name, avatar_url, created_at, updated_at
                ) values (%s, %s, %s, %s, %s, %s)
                """,
                (user_id, "local@example.com", "Local User", None, now, now),
            )
            for current_thread_id, current_message_id in (
                (thread_id, trigger_message_id),
                (other_thread_id, other_trigger_message_id),
            ):
                cursor.execute(
                    """
                    insert into web_bff_threads (
                        id, user_id, title, message_count, last_message_at,
                        created_at, updated_at
                    ) values (%s, %s, %s, 1, %s, %s, %s)
                    """,
                    (current_thread_id, user_id, "Comps", now, now, now),
                )
                cursor.execute(
                    """
                    insert into web_bff_messages (
                        id, thread_id, role, content, status, created_at
                    ) values (%s, %s, 'user', %s, 'complete', %s)
                    """,
                    (
                        current_message_id,
                        current_thread_id,
                        "Compare AAPL with MSFT",
                        now,
                    ),
                )
    return thread_id, trigger_message_id, other_trigger_message_id


def _generate_request(
    *,
    thread_id: UUID,
    trigger_message_id: UUID,
) -> dict[str, object]:
    return {
        "invocation_id": str(uuid4()),
        "thread_id": str(thread_id),
        "trigger_message_id": str(trigger_message_id),
        "target_ticker": "AAPL",
        "peer_tickers": ["MSFT"],
        "peer_selection_mode": "user_supplied",
        "analysis_period": "latest",
    }


def _linked_record_counts(
    database_url: str,
    trigger_message_id: UUID,
) -> tuple[int, int, int, int, int]:
    import psycopg

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select count(*) from web_bff_messages where id = %s",
                (trigger_message_id,),
            )
            message_count = cursor.fetchone()[0]
            cursor.execute("select count(*) from comps_runs")
            run_count = cursor.fetchone()[0]
            cursor.execute("select count(*) from comps_tables")
            table_count = cursor.fetchone()[0]
            cursor.execute("select count(*) from comps_traces")
            trace_count = cursor.fetchone()[0]
            cursor.execute("select count(*) from comps_source_snapshots")
            source_snapshot_count = cursor.fetchone()[0]
    return message_count, run_count, table_count, trace_count, source_snapshot_count


if __name__ == "__main__":
    unittest.main()
