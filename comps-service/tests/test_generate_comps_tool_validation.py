from __future__ import annotations

import os
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from comps_service.main import app

TEST_ALPHA_VANTAGE_API_KEY_VAR = "TEST_ALPHA_VANTAGE_API_KEY"
ALPHA_VANTAGE_TEST_REQUEST_INTERVAL_SECONDS = 2.0


class GenerateCompsToolValidationTest(unittest.TestCase):
    _last_live_validation_at = 0.0

    # Rejects user-supplied peer mode before provider or database work when peers are missing.
    def test_user_supplied_mode_requires_peer_tickers_before_run_creation(self) -> None:
        database_connect = Mock()

        with (
            patch.dict(
                sys.modules,
                {"psycopg": SimpleNamespace(connect=database_connect)},
            ),
            patch.dict(os.environ, {}, clear=True),
        ):
            response = TestClient(app).post(
                "/v1/internal/tools/generate-comps-table",
                json={
                    "invocation_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "trigger_message_id": str(uuid4()),
                    "target_ticker": "AAPL",
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                },
            )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("peer_tickers", body["error"]["message"])
        database_connect.assert_not_called()

    # Normalizes mixed-case ticker candidates before live Alpha Vantage validation.
    def test_mixed_case_tickers_pass_pre_run_validation_without_creating_run(self) -> None:
        database_connect = Mock()

        with (
            patch.dict(
                sys.modules,
                {"psycopg": SimpleNamespace(connect=database_connect)},
            ),
            patch.dict(
                os.environ,
                self._live_alpha_vantage_env(),
                clear=True,
            ),
        ):
            response = TestClient(app).post(
                "/v1/internal/tools/generate-comps-table",
                json={
                    "invocation_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "trigger_message_id": str(uuid4()),
                    "target_ticker": "aApL",
                    "peer_tickers": ["mSfT"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                },
            )

        self.assertEqual(response.status_code, 501)
        body = response.json()
        self.assertEqual(body["error"]["code"], "INTERNAL_ERROR")
        self.assertIn("not implemented", body["error"]["message"])
        database_connect.assert_not_called()

    # Rejects ticker candidates with unsupported syntax before provider or database work.
    def test_malformed_ticker_returns_validation_error_before_run_creation(self) -> None:
        database_connect = Mock()

        with (
            patch.dict(
                sys.modules,
                {"psycopg": SimpleNamespace(connect=database_connect)},
            ),
            patch.dict(os.environ, {}, clear=True),
        ):
            response = TestClient(app).post(
                "/v1/internal/tools/generate-comps-table",
                json={
                    "invocation_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "trigger_message_id": str(uuid4()),
                    "target_ticker": "AAPL",
                    "peer_tickers": ["MS FT"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                },
            )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("peer_tickers", str(body["error"]["details"]))
        database_connect.assert_not_called()

    # Rejects unsupported tickers using live Alpha Vantage search before Run creation.
    def test_unsupported_ticker_returns_validation_error_before_run_creation(self) -> None:
        database_connect = Mock()

        with (
            patch.dict(
                sys.modules,
                {"psycopg": SimpleNamespace(connect=database_connect)},
            ),
            patch.dict(
                os.environ,
                self._live_alpha_vantage_env(),
                clear=True,
            ),
        ):
            response = TestClient(app).post(
                "/v1/internal/tools/generate-comps-table",
                json={
                    "invocation_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "trigger_message_id": str(uuid4()),
                    "target_ticker": "ZZZZ",
                    "peer_tickers": ["ZZZZ"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                },
            )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("Unsupported ticker", body["error"]["message"])
        self.assertEqual(body["error"]["details"]["unsupported_tickers"], ["ZZZZ"])
        database_connect.assert_not_called()

    # Fails clearly when Alpha Vantage configuration is missing before database work.
    def test_missing_validation_configuration_fails_before_run_creation(self) -> None:
        database_connect = Mock()

        with (
            patch.dict(
                sys.modules,
                {"psycopg": SimpleNamespace(connect=database_connect)},
            ),
            patch.dict(os.environ, {}, clear=True),
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
            )

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["error"]["code"], "INTERNAL_ERROR")
        self.assertIn("ALPHA_VANTAGE_API_KEY", body["error"]["message"])
        self.assertEqual(
            body["error"]["details"]["missing_configuration"],
            ["ALPHA_VANTAGE_API_KEY"],
        )
        database_connect.assert_not_called()

    # Lets live Alpha Vantage exact-match tickers pass pre-Run validation only.
    def test_valid_tickers_pass_pre_run_validation_without_creating_run(self) -> None:
        database_connect = Mock()

        with (
            patch.dict(
                sys.modules,
                {"psycopg": SimpleNamespace(connect=database_connect)},
            ),
            patch.dict(
                os.environ,
                self._live_alpha_vantage_env(),
                clear=True,
            ),
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
            )

        self.assertEqual(response.status_code, 501)
        body = response.json()
        self.assertEqual(body["error"]["code"], "INTERNAL_ERROR")
        self.assertIn("not implemented", body["error"]["message"])
        database_connect.assert_not_called()

    def _live_alpha_vantage_env(self) -> dict[str, str]:
        self._wait_for_live_alpha_vantage_slot()
        return {
            "ALPHA_VANTAGE_API_KEY": self._test_alpha_vantage_api_key(),
            "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": str(
                ALPHA_VANTAGE_TEST_REQUEST_INTERVAL_SECONDS
            ),
        }

    def _wait_for_live_alpha_vantage_slot(self) -> None:
        elapsed_seconds = time.monotonic() - self.__class__._last_live_validation_at
        if elapsed_seconds < ALPHA_VANTAGE_TEST_REQUEST_INTERVAL_SECONDS:
            time.sleep(ALPHA_VANTAGE_TEST_REQUEST_INTERVAL_SECONDS - elapsed_seconds)
        self.__class__._last_live_validation_at = time.monotonic()

    def _test_alpha_vantage_api_key(self) -> str:
        api_key = os.environ.get(TEST_ALPHA_VANTAGE_API_KEY_VAR, "").strip()
        if not api_key:
            self.fail(
                f"{TEST_ALPHA_VANTAGE_API_KEY_VAR} is required for live "
                "Alpha Vantage validation tests."
            )
        return api_key


if __name__ == "__main__":
    unittest.main()
