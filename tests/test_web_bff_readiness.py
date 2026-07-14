from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.readiness_fakes import database_connects
from web_bff.main import app


class WebBffReadinessTest(unittest.TestCase):
    def test_local_readiness_accepts_explicit_dev_auth_identity(self) -> None:
        env = {
            "TALK_TO_YOUR_STOCK_ENV": "local",
            "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/talk_to_your_stock",
            "DEV_AUTH_USER_ID": "00000000-0000-0000-0000-000000000001",
            "DEV_AUTH_EMAIL": "dev@example.com",
            "AGENT_SERVICE_URL": "http://agent-service:8001",
        }

        with (
            patch.dict(os.environ, env, clear=True),
            database_connects(),
        ):
            response = TestClient(app).get("/v1/ready")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["checks"]["configuration"]["status"], "ok")
        self.assertEqual(body["checks"]["database"]["status"], "ok")

    def test_production_readiness_fails_without_managed_auth_config(self) -> None:
        env = {
            "TALK_TO_YOUR_STOCK_ENV": "production",
            "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/talk_to_your_stock",
            "DEV_AUTH_USER_ID": "00000000-0000-0000-0000-000000000001",
            "DEV_AUTH_EMAIL": "dev@example.com",
            "AGENT_SERVICE_URL": "http://agent-service:8001",
        }

        with (
            patch.dict(os.environ, env, clear=True),
            database_connects(),
        ):
            response = TestClient(app).get("/v1/ready")

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "not_ready")
        self.assertEqual(body["checks"]["database"]["status"], "ok")
        self.assertEqual(body["checks"]["configuration"]["status"], "fail")
        self.assertIn("MANAGED_AUTH_JWKS_URL", body["checks"]["configuration"]["message"])
        self.assertIn("MANAGED_AUTH_ISSUER", body["checks"]["configuration"]["message"])
        self.assertIn("MANAGED_AUTH_AUDIENCE", body["checks"]["configuration"]["message"])

    def test_production_readiness_rejects_dev_auth_configuration(self) -> None:
        env = {
            "TALK_TO_YOUR_STOCK_ENV": "production",
            "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/talk_to_your_stock",
            "MANAGED_AUTH_JWKS_URL": "https://auth.example.com/.well-known/jwks.json",
            "MANAGED_AUTH_ISSUER": "https://auth.example.com",
            "MANAGED_AUTH_AUDIENCE": "talk-to-your-stock",
            "AGENT_SERVICE_URL": "http://agent-service:8001",
            "DEV_AUTH_USER_ID": "00000000-0000-0000-0000-000000000001",
            "DEV_AUTH_EMAIL": "dev@example.com",
        }

        with (
            patch.dict(os.environ, env, clear=True),
            database_connects(),
        ):
            response = TestClient(app).get("/v1/ready")

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "not_ready")
        self.assertEqual(body["checks"]["configuration"]["status"], "fail")
        self.assertIn("DEV_AUTH_*", body["checks"]["configuration"]["message"])

    def test_production_readiness_fails_until_jwt_verification_exists(self) -> None:
        env = {
            "TALK_TO_YOUR_STOCK_ENV": "production",
            "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/talk_to_your_stock",
            "MANAGED_AUTH_JWKS_URL": "https://auth.example.com/.well-known/jwks.json",
            "MANAGED_AUTH_ISSUER": "https://auth.example.com",
            "MANAGED_AUTH_AUDIENCE": "talk-to-your-stock",
            "AGENT_SERVICE_URL": "http://agent-service:8001",
        }

        with (
            patch.dict(os.environ, env, clear=True),
            database_connects(),
        ):
            response = TestClient(app).get("/v1/ready")

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "not_ready")
        self.assertEqual(body["checks"]["configuration"]["status"], "fail")
        self.assertIn("JWT verification is not implemented", body["checks"]["configuration"]["message"])

    def test_local_readiness_rejects_invalid_dev_auth_user_id(self) -> None:
        env = {
            "TALK_TO_YOUR_STOCK_ENV": "local",
            "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/talk_to_your_stock",
            "DEV_AUTH_USER_ID": "dev-user",
            "DEV_AUTH_EMAIL": "dev@example.com",
            "AGENT_SERVICE_URL": "http://agent-service:8001",
        }

        with (
            patch.dict(os.environ, env, clear=True),
            database_connects(),
        ):
            response = TestClient(app).get("/v1/ready")

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["checks"]["configuration"]["status"], "fail")
        self.assertIn("DEV_AUTH_USER_ID must be a valid UUID", body["checks"]["configuration"]["message"])


if __name__ == "__main__":
    unittest.main()
