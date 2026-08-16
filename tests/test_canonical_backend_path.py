from __future__ import annotations

import os
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import httpx
from alembic import command
from alembic.config import Config

from tests.live_service import running_service_process


REPO_ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL_VAR = "VERTICAL_ACCEPTANCE_DATABASE_URL"
INTERNAL_TOKEN = "vertical-acceptance-token"
LOCAL_USER_ID = "00000000-0000-0000-0000-000000000001"


@unittest.skipUnless(
    os.environ.get(DATABASE_URL_VAR),
    f"{DATABASE_URL_VAR} is required for the production-shaped acceptance test.",
)
class CanonicalBackendPathTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = os.environ[DATABASE_URL_VAR]
        migration_config = Config(str(REPO_ROOT / "alembic.ini"))
        with patch.dict(os.environ, {"DATABASE_URL": cls.database_url}):
            command.downgrade(migration_config, "base")
            command.upgrade(migration_config, "head")

        cls.stack = ExitStack()
        try:
            inherited = dict(os.environ)
            cls.external_url = cls.stack.enter_context(
                running_service_process(
                    "tests.external_substitutes:app",
                    environ=inherited,
                    health_path="/health",
                )
            )
            cls.comps_url = cls.stack.enter_context(
                running_service_process(
                    "comps_service.main:app",
                    environ={
                        **inherited,
                        "DATABASE_URL": cls.database_url,
                        "COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOKEN,
                        "ALPHA_VANTAGE_API_KEY": "deterministic-provider-secret",
                        "ALPHA_VANTAGE_BASE_URL": (
                            f"{cls.external_url}/alpha-vantage"
                        ),
                        "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
                    },
                )
            )
            cls.agent_url = cls.stack.enter_context(
                running_service_process(
                    "agent_service.main:app",
                    environ={
                        **inherited,
                        "DATABASE_URL": cls.database_url,
                        "GOOGLE_API_KEY": "deterministic-gemini-key",
                        "GOOGLE_GEMINI_BASE_URL": cls.external_url,
                        "GEMINI_MODEL": "gemini-3.1-flash-lite",
                        "COMPS_SERVICE_URL": cls.comps_url,
                        "COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOKEN,
                    },
                )
            )
            cls.web_url = cls.stack.enter_context(
                running_service_process(
                    "web_bff.main:app",
                    environ={
                        **inherited,
                        "TALK_TO_YOUR_STOCK_ENV": "test",
                        "DATABASE_URL": cls.database_url,
                        "DEV_AUTH_USER_ID": LOCAL_USER_ID,
                        "DEV_AUTH_EMAIL": "vertical-acceptance@example.com",
                        "AGENT_SERVICE_URL": cls.agent_url,
                        "COMPS_SERVICE_URL": cls.comps_url,
                    },
                )
            )
        except BaseException:
            cls.stack.close()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls.stack.close()

    def test_explicit_peer_message_completes_one_persisted_run(self) -> None:
        httpx.post(
            f"{self.external_url}/control/alpha-vantage/success"
        ).raise_for_status()
        with httpx.Client(base_url=self.web_url, timeout=30) as client:
            thread_response = client.post(
                "/v1/threads",
                json={"title": "AAPL comps"},
            )
            self.assertEqual(thread_response.status_code, 201, thread_response.text)
            thread = thread_response.json()["thread"]

            created = client.post(
                f"/v1/threads/{thread['id']}/messages",
                json={"content": "Compare Apple with Microsoft and Nvidia"},
            )

            self.assertEqual(created.status_code, 201, created.text)
            body = created.json()
            run_id = body["run"]["id"]
            self.assertEqual(body["run"]["status"], "succeeded")
            self.assertEqual(body["assistant_message"]["run_id"], run_id)
            self.assertEqual(
                body["run"]["trigger_message_id"],
                body["user_message"]["id"],
            )

            messages = client.get(
                f"/v1/threads/{thread['id']}/messages"
            )
            linked_thread = client.get(f"/v1/threads/{thread['id']}")
            run = client.get(f"/v1/runs/{run_id}")
            table = client.get(f"/v1/runs/{run_id}/table")
            trace = client.get(f"/v1/runs/{run_id}/trace")
            source_snapshot = client.get(
                f"/v1/runs/{run_id}/source-snapshot"
            )

        self.assertEqual(messages.status_code, 200, messages.text)
        self.assertEqual(
            messages.json()["messages"],
            [body["user_message"], body["assistant_message"]],
        )
        self.assertEqual(linked_thread.status_code, 200, linked_thread.text)
        self.assertEqual(linked_thread.json()["thread"]["latest_run_id"], run_id)
        self.assertEqual(run.status_code, 200, run.text)
        self.assertEqual(run.json()["run"], body["run"])
        self.assertEqual(table.status_code, 200, table.text)
        self.assertEqual(table.json()["run_id"], run_id)
        self.assertEqual(trace.status_code, 200, trace.text)
        self.assertEqual(trace.json()["run_id"], run_id)
        self.assertEqual(source_snapshot.status_code, 200, source_snapshot.text)
        self.assertEqual(source_snapshot.json()["run_id"], run_id)
        self.assertEqual(
            {row["ticker"] for row in table.json()["rows"]},
            {"AAPL", "MSFT", "NVDA"},
        )
        self._assert_success_persisted_once(
            response=body,
            table=table.json(),
            trace=trace.json(),
            source_snapshot=source_snapshot.json(),
        )

    def test_provider_failure_completes_one_linked_failed_run(self) -> None:
        httpx.post(
            f"{self.external_url}/control/alpha-vantage/failure"
        ).raise_for_status()
        with httpx.Client(base_url=self.web_url, timeout=30) as client:
            thread_response = client.post(
                "/v1/threads",
                json={"title": "Failed AAPL comps"},
            )
            self.assertEqual(thread_response.status_code, 201, thread_response.text)
            thread = thread_response.json()["thread"]

            failed = client.post(
                f"/v1/threads/{thread['id']}/messages",
                json={"content": "Compare Apple with Microsoft and Nvidia"},
            )

            self.assertEqual(failed.status_code, 502, failed.text)
            error = failed.json()["error"]
            run_id = error["run_id"]
            self.assertEqual(error["code"], "UPSTREAM_ERROR")
            self.assertEqual(
                error["message"],
                "Alpha Vantage request limit was reached while loading AAPL.",
            )
            self.assertEqual(error["details"]["thread_id"], thread["id"])

            messages = client.get(
                f"/v1/threads/{thread['id']}/messages"
            )
            linked_thread = client.get(f"/v1/threads/{thread['id']}")
            run = client.get(f"/v1/runs/{run_id}")
            source_snapshot = client.get(
                f"/v1/runs/{run_id}/source-snapshot"
            )

        self.assertEqual(messages.status_code, 200, messages.text)
        persisted_messages = messages.json()["messages"]
        self.assertEqual(
            [(message["role"], message["status"]) for message in persisted_messages],
            [("user", "complete"), ("assistant", "failed")],
        )
        self.assertEqual(persisted_messages[-1]["run_id"], run_id)
        self.assertEqual(
            error["details"]["trigger_message_id"],
            persisted_messages[0]["id"],
        )
        self.assertEqual(linked_thread.status_code, 200, linked_thread.text)
        self.assertEqual(linked_thread.json()["thread"]["latest_run_id"], run_id)
        self.assertEqual(run.status_code, 200, run.text)
        self.assertEqual(run.json()["run"]["status"], "failed")
        self.assertEqual(run.json()["run"]["error_message"], error["message"])
        self.assertEqual(source_snapshot.status_code, 200, source_snapshot.text)
        self.assertEqual(source_snapshot.json()["run_id"], run_id)
        self.assertNotIn("deterministic-provider-secret", source_snapshot.text)
        self.assertNotIn("deterministic-provider-secret", failed.text)
        self.assertNotIn(INTERNAL_TOKEN, failed.text)
        self._assert_failure_persisted_once(
            trigger_message_id=persisted_messages[0]["id"],
            run_id=run_id,
        )

    def _assert_success_persisted_once(
        self,
        *,
        response: dict[str, object],
        table: dict[str, object],
        trace: dict[str, object],
        source_snapshot: dict[str, object],
    ) -> None:
        import psycopg
        from psycopg.rows import dict_row

        run = response["run"]
        assert isinstance(run, dict)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select r.id as run_id, r.status, t.run_id as table_run_id,
                           tr.run_id as trace_run_id, s.run_id as snapshot_run_id,
                           s.raw_provider_evidence, s.normalized_inputs,
                           s.created_at as snapshot_created_at
                    from comps_runs r
                    join comps_tables t on t.run_id = r.id
                    join comps_traces tr on tr.run_id = r.id
                    join comps_source_snapshots s on s.run_id = r.id
                    where r.trigger_message_id = %s
                    """,
                    (UUID(run["trigger_message_id"]),),
                )
                persisted = cursor.fetchall()

        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0]["status"], "succeeded")
        self.assertEqual(str(persisted[0]["run_id"]), run["id"])
        self.assertEqual(str(persisted[0]["table_run_id"]), table["run_id"])
        self.assertEqual(str(persisted[0]["trace_run_id"]), trace["run_id"])
        self.assertEqual(str(persisted[0]["snapshot_run_id"]), run["id"])
        self.assertEqual(
            persisted[0]["raw_provider_evidence"],
            source_snapshot["raw_provider_evidence"],
        )
        self.assertEqual(
            persisted[0]["normalized_inputs"],
            source_snapshot["normalized_inputs"],
        )
        self.assertEqual(
            persisted[0]["snapshot_created_at"],
            datetime.fromisoformat(source_snapshot["created_at"]),
        )

    def _assert_failure_persisted_once(
        self,
        *,
        trigger_message_id: str,
        run_id: str,
    ) -> None:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select r.id as run_id, r.status,
                           t.run_id as table_run_id,
                           tr.run_id as trace_run_id,
                           s.run_id as snapshot_run_id,
                           s.raw_provider_evidence::text as raw_evidence
                    from comps_runs r
                    left join comps_tables t on t.run_id = r.id
                    left join comps_traces tr on tr.run_id = r.id
                    join comps_source_snapshots s on s.run_id = r.id
                    where r.trigger_message_id = %s
                    """,
                    (UUID(trigger_message_id),),
                )
                persisted = cursor.fetchall()

        self.assertEqual(len(persisted), 1)
        self.assertEqual(str(persisted[0]["run_id"]), run_id)
        self.assertEqual(persisted[0]["status"], "failed")
        self.assertIsNone(persisted[0]["table_run_id"])
        self.assertIsNone(persisted[0]["trace_run_id"])
        self.assertEqual(str(persisted[0]["snapshot_run_id"]), run_id)
        self.assertNotIn(
            "deterministic-provider-secret",
            persisted[0]["raw_evidence"],
        )


if __name__ == "__main__":
    unittest.main()
