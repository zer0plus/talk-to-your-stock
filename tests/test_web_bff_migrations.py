from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from talk_to_your_stock_shared import AgentMessageResponse
from web_bff.main import app, get_agent_client

REPO_ROOT = Path(__file__).resolve().parents[1]


class StubAgentClient:
    def respond_to_user_message(self, **_kwargs: object) -> AgentMessageResponse:
        return AgentMessageResponse(content="Assistant reply.")


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
                    json={"content": "Move this Thread to the top"},
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


if __name__ == "__main__":
    unittest.main()
