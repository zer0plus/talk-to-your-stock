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

from web_bff.main import app

REPO_ROOT = Path(__file__).resolve().parents[1]


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
        self.assertIn("create index web_bff_threads_user_updated_idx", sql)
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
                client = TestClient(app)

                created = client.post("/v1/threads", json={"title": "Migration test"})
                listed = client.get("/v1/threads")

                self.assertEqual(created.status_code, 201, created.text)
                self.assertEqual(listed.status_code, 200, listed.text)
                self.assertEqual(
                    [thread["id"] for thread in listed.json()["threads"]],
                    [created.json()["thread"]["id"]],
                )
            finally:
                command.downgrade(migration_config, "base")


if __name__ == "__main__":
    unittest.main()
