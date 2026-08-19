from __future__ import annotations

import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from comps_service.main import app as comps_app
from talk_to_your_stock_shared import AgentMessageResponse
from tests.live_service import running_service
from web_bff.main import app, get_agent_client, get_comps_client

REPO_ROOT = Path(__file__).resolve().parents[1]


class StubAgentClient:
    def respond_to_user_message(self, **_kwargs: object) -> AgentMessageResponse:
        return AgentMessageResponse(content="Assistant reply.")


class StubCompsClient:
    pass


class WebBffMigrationsTest(unittest.TestCase):
    def test_upgrade_renders_initial_web_bff_schema(self) -> None:
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
        sql = result.stdout.lower()
        self.assertIn("create table web_bff_users", sql)
        self.assertIn("create table web_bff_threads", sql)
        self.assertIn("create table web_bff_messages", sql)
        self.assertIn("create table agent_response_envelopes", sql)
        self.assertIn(
            "create index web_bff_threads_user_updated_idx "
            "on web_bff_threads (user_id, updated_at desc, id desc)",
            sql,
        )
        self.assertIn("create index web_bff_messages_thread_created_idx", sql)

    @unittest.skipUnless(
        os.environ.get("WEB_BFF_MIGRATION_TEST_DATABASE_URL"),
        "WEB_BFF_MIGRATION_TEST_DATABASE_URL is required for PostgreSQL integration.",
    )
    def test_migrated_database_supports_thread_http_boundary(self) -> None:
        database_url = os.environ["WEB_BFF_MIGRATION_TEST_DATABASE_URL"]
        migration_config = Config(str(REPO_ROOT / "alembic.ini"))
        env = {
            "DATABASE_URL": database_url,
            "TALK_TO_YOUR_STOCK_ENV": "test",
            "DEV_AUTH_USER_ID": "00000000-0000-0000-0000-000000000001",
            "DEV_AUTH_EMAIL": "dev@example.com",
            "AGENT_SERVICE_URL": "http://agent-service.test",
        }

        with patch.dict(os.environ, env, clear=False):
            command.upgrade(migration_config, "head")
            try:
                app.dependency_overrides[get_agent_client] = StubAgentClient
                app.dependency_overrides[get_comps_client] = StubCompsClient
                client = TestClient(app)

                created = [
                    client.post("/v1/threads", json={"title": f"Thread {index}"})
                    for index in range(4)
                ]
                first_page = client.get("/v1/threads", params={"limit": 2})

                self.assertTrue(all(response.status_code == 201 for response in created))
                self.assertEqual(first_page.status_code, 200, first_page.text)
                first_page_ids = {
                    thread["id"] for thread in first_page.json()["threads"]
                }
                oldest_thread_id = created[0].json()["thread"]["id"]

                promoted = client.post(
                    f"/v1/threads/{oldest_thread_id}/messages",
                    json={
                        "message_id": str(uuid4()),
                        "content": "Move this Thread to the top",
                    },
                )
                second_page = client.get(
                    "/v1/threads",
                    params={
                        "limit": 2,
                        "cursor": first_page.json()["page"]["next_cursor"],
                    },
                )

                self.assertEqual(promoted.status_code, 201, promoted.text)
                self.assertEqual(second_page.status_code, 200, second_page.text)
                second_page_ids = {
                    thread["id"] for thread in second_page.json()["threads"]
                }
                self.assertTrue(first_page_ids.isdisjoint(second_page_ids))
                self.assertEqual(
                    second_page_ids,
                    {created[1].json()["thread"]["id"]},
                )
            finally:
                app.dependency_overrides.clear()
                command.downgrade(migration_config, "base")

    @unittest.skipUnless(
        os.environ.get("WEB_BFF_MIGRATION_TEST_DATABASE_URL"),
        "WEB_BFF_MIGRATION_TEST_DATABASE_URL is required for PostgreSQL integration.",
    )
    def test_thread_list_rejects_malformed_cursor(self) -> None:
        database_url = os.environ["WEB_BFF_MIGRATION_TEST_DATABASE_URL"]
        migration_config = Config(str(REPO_ROOT / "alembic.ini"))
        env = {
            "DATABASE_URL": database_url,
            "TALK_TO_YOUR_STOCK_ENV": "test",
            "DEV_AUTH_USER_ID": "00000000-0000-0000-0000-000000000001",
            "DEV_AUTH_EMAIL": "dev@example.com",
            "AGENT_SERVICE_URL": "http://agent-service.test",
        }

        with patch.dict(os.environ, env, clear=False):
            command.upgrade(migration_config, "head")
            try:
                response = TestClient(app).get(
                    "/v1/threads",
                    params={"cursor": "not-a-thread-cursor"},
                )

                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")
            finally:
                command.downgrade(migration_config, "base")

    @unittest.skipUnless(
        os.environ.get("WEB_BFF_MIGRATION_TEST_DATABASE_URL"),
        "WEB_BFF_MIGRATION_TEST_DATABASE_URL is required for PostgreSQL integration.",
    )
    def test_message_list_rejects_invalid_cursors(self) -> None:
        database_url = os.environ["WEB_BFF_MIGRATION_TEST_DATABASE_URL"]
        migration_config = Config(str(REPO_ROOT / "alembic.ini"))
        env = {
            "DATABASE_URL": database_url,
            "TALK_TO_YOUR_STOCK_ENV": "test",
            "DEV_AUTH_USER_ID": "00000000-0000-0000-0000-000000000001",
            "DEV_AUTH_EMAIL": "dev@example.com",
            "AGENT_SERVICE_URL": "http://agent-service.test",
        }

        with patch.dict(os.environ, env, clear=False):
            command.upgrade(migration_config, "head")
            try:
                client = TestClient(app)
                thread_id = client.post(
                    "/v1/threads",
                    json={"title": "Cursor validation"},
                ).json()["thread"]["id"]

                for cursor in ("bogus", "-1"):
                    with self.subTest(cursor=cursor):
                        response = client.get(
                            f"/v1/threads/{thread_id}/messages",
                            params={"cursor": cursor},
                        )

                        self.assertEqual(response.status_code, 400, response.text)
                        self.assertEqual(
                            response.json()["error"]["code"],
                            "VALIDATION_ERROR",
                        )
            finally:
                command.downgrade(migration_config, "base")

    @unittest.skipUnless(
        os.environ.get("WEB_BFF_MIGRATION_TEST_DATABASE_URL"),
        "WEB_BFF_MIGRATION_TEST_DATABASE_URL is required for PostgreSQL integration.",
    )
    def test_migrated_database_supports_latest_successful_run_history(self) -> None:
        import psycopg

        database_url = os.environ["WEB_BFF_MIGRATION_TEST_DATABASE_URL"]
        migration_config = Config(str(REPO_ROOT / "alembic.ini"))
        env = {
            "DATABASE_URL": database_url,
            "TALK_TO_YOUR_STOCK_ENV": "test",
            "DEV_AUTH_USER_ID": "00000000-0000-0000-0000-000000000001",
            "DEV_AUTH_EMAIL": "dev@example.com",
            "AGENT_SERVICE_URL": "http://agent-service.test",
        }

        with patch.dict(os.environ, env, clear=False):
            command.upgrade(migration_config, "head")
            try:
                app.dependency_overrides[get_agent_client] = StubAgentClient
                app.dependency_overrides[get_comps_client] = StubCompsClient
                client = TestClient(app)
                thread_id = client.post(
                    "/v1/threads", json={"title": "Run history"}
                ).json()["thread"]["id"]
                trigger_message_id = client.post(
                    f"/v1/threads/{thread_id}/messages",
                    json={
                        "message_id": str(uuid4()),
                        "content": "Compare AAPL with MSFT",
                    },
                ).json()["user_message"]["id"]
                app.dependency_overrides.pop(get_comps_client)
                created_at = datetime(2026, 8, 3, tzinfo=timezone.utc)
                first_successful_run_id = UUID(
                    "00000000-0000-0000-0000-000000000001"
                )
                newest_successful_run_id = UUID(
                    "00000000-0000-0000-0000-000000000002"
                )
                failed_run_id = uuid4()

                with psycopg.connect(database_url) as connection:
                    with connection.cursor() as cursor:
                        for run_id, run_status, run_created_at in (
                            (first_successful_run_id, "succeeded", created_at),
                            (newest_successful_run_id, "succeeded", created_at),
                            (
                                failed_run_id,
                                "failed",
                                created_at + timedelta(seconds=1),
                            ),
                        ):
                            cursor.execute(
                                """
                                insert into comps_runs (
                                    id, invocation_id, thread_id,
                                    trigger_message_id, status, target_ticker,
                                    peer_tickers, currency, as_of, warnings,
                                    error_message, created_at, started_at,
                                    completed_at, failure_http_status,
                                    failure_code
                                )
                                values (
                                    %s, %s, %s, %s, %s, 'AAPL',
                                    array['MSFT'], 'USD', %s, '[]'::jsonb,
                                    %s, %s, %s, %s, %s, %s
                                )
                                """,
                                (
                                    run_id,
                                    uuid4(),
                                    thread_id,
                                    trigger_message_id,
                                    run_status,
                                    run_created_at,
                                    (
                                        "Historical Run failed."
                                        if run_status == "failed"
                                        else None
                                    ),
                                    run_created_at,
                                    run_created_at,
                                    run_created_at,
                                    502 if run_status == "failed" else None,
                                    (
                                        "UPSTREAM_ERROR"
                                        if run_status == "failed"
                                        else None
                                    ),
                                ),
                            )

                with running_service(comps_app) as comps_service_url:
                    with patch.dict(
                        os.environ,
                        {"COMPS_SERVICE_URL": comps_service_url},
                        clear=False,
                    ):
                        history = client.get(f"/v1/threads/{thread_id}/runs")
                        latest_successful = client.get(
                            f"/v1/threads/{thread_id}/runs",
                            params={"status": "succeeded", "limit": 1},
                        )
                        next_successful = client.get(
                            f"/v1/threads/{thread_id}/runs",
                            params={
                                "status": "succeeded",
                                "limit": 1,
                                "cursor": latest_successful.json()["page"][
                                    "next_cursor"
                                ],
                            },
                        )

                self.assertEqual(history.status_code, 200, history.text)
                self.assertEqual(
                    [run["id"] for run in history.json()["runs"]],
                    [
                        str(failed_run_id),
                        str(newest_successful_run_id),
                        str(first_successful_run_id),
                    ],
                )
                self.assertEqual(latest_successful.status_code, 200)
                self.assertEqual(
                    [run["id"] for run in latest_successful.json()["runs"]],
                    [str(newest_successful_run_id)],
                )
                self.assertTrue(latest_successful.json()["page"]["has_more"])
                self.assertEqual(next_successful.status_code, 200)
                self.assertEqual(
                    [run["id"] for run in next_successful.json()["runs"]],
                    [str(first_successful_run_id)],
                )
            finally:
                app.dependency_overrides.clear()
                command.downgrade(migration_config, "base")


if __name__ == "__main__":
    unittest.main()
