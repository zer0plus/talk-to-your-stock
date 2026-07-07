from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
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

    # Rejects ticker candidates that cannot be represented in Run outputs.
    def test_unrepresentable_ticker_candidate_returns_validation_error_before_run_creation(
        self,
    ) -> None:
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
                    "peer_tickers": ["BRK-B"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                },
            )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("peer_tickers", str(body["error"]["details"]))
        database_connect.assert_not_called()

    # Rejects oversized peer lists before provider or database work.
    def test_peer_tickers_are_bounded_before_provider_validation(self) -> None:
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
                    "peer_tickers": ["MSFT"] * 11,
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                },
            )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("peer_tickers", str(body["error"]["details"]))
        database_connect.assert_not_called()

    # Rejects self-comparison requests before provider or database work.
    def test_target_ticker_cannot_also_be_a_peer_ticker(self) -> None:
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
                    "peer_tickers": ["aapl"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                },
            )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")
        self.assertIn(
            "Target ticker cannot also be a peer ticker",
            body["error"]["message"],
        )
        self.assertEqual(body["error"]["details"]["target_ticker"], "AAPL")
        self.assertEqual(
            body["error"]["details"]["self_comparison_tickers"],
            ["AAPL"],
        )
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

    # Treats malformed successful provider responses as upstream failures.
    def test_non_json_provider_response_returns_upstream_error_before_run_creation(
        self,
    ) -> None:
        database_connect = Mock()

        with (
            _alpha_vantage_server(b"<html>temporarily unavailable</html>") as base_url,
            patch.dict(
                sys.modules,
                {"psycopg": SimpleNamespace(connect=database_connect)},
            ),
            patch.dict(
                os.environ,
                {
                    "ALPHA_VANTAGE_API_KEY": "test-key",
                    "ALPHA_VANTAGE_BASE_URL": base_url,
                    "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
                },
                clear=True,
            ),
        ):
            response = TestClient(app, raise_server_exceptions=False).post(
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

        self.assertEqual(response.status_code, 502)
        body = response.json()
        self.assertEqual(body["error"]["code"], "UPSTREAM_ERROR")
        self.assertIn("malformed", body["error"]["message"])
        self.assertEqual(body["error"]["details"]["provider"], "alpha_vantage")
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
            self.skipTest(
                f"{TEST_ALPHA_VANTAGE_API_KEY_VAR} is required for live "
                "Alpha Vantage validation tests."
            )
        return api_key


class _AlphaVantageResponseHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(self.server.response_body)

    def log_message(self, _format: str, *args: object) -> None:
        return


@contextmanager
def _alpha_vantage_server(response_body: bytes) -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _AlphaVantageResponseHandler)
    server.response_body = response_body
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/query"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


if __name__ == "__main__":
    unittest.main()
