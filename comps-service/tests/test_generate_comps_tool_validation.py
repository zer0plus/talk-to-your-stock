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
from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from comps_service.main import app, get_company_data_source
from comps_service.run_service import CompanyDataUnavailable, LoadedCompanyData
from comps_service.tool_validation import (
    AlphaVantageRequestLimiter,
    AlphaVantageTickerValidator,
    TickerDirectory,
)

TEST_ALPHA_VANTAGE_API_KEY_VAR = "TEST_ALPHA_VANTAGE_API_KEY"
RUN_LIVE_ALPHA_VANTAGE_TESTS_VAR = "RUN_LIVE_ALPHA_VANTAGE_TESTS"
ALPHA_VANTAGE_TEST_REQUEST_INTERVAL_SECONDS = 2.0
INTERNAL_TOOL_TOKEN = str(uuid4())


class RecordingUnavailableCompanyDataSource:
    def __init__(self) -> None:
        self.requested_tickers: list[str] = []

    def load(self, *, tickers: list[str], currency: str) -> LoadedCompanyData:
        del currency
        self.requested_tickers = tickers
        raise CompanyDataUnavailable("Provider normalization intentionally stopped.")


class GenerateCompsToolValidationTest(unittest.TestCase):
    _last_live_validation_at = 0.0

    def _internal_tool_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {INTERNAL_TOOL_TOKEN}"}

    def _env_with_internal_auth(
        self,
        values: dict[str, str] | None = None,
    ) -> dict[str, str]:
        env = {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN}
        if values is not None:
            env.update(values)
        return env

    # Rejects unauthenticated internal tool calls before body validation.
    def test_generate_comps_table_requires_bearer_auth_before_validation(self) -> None:
        database_connect = Mock()

        with (
            patch.dict(
                sys.modules,
                {"psycopg": SimpleNamespace(connect=database_connect)},
            ),
            patch.dict(
                os.environ,
                {"COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOOL_TOKEN},
                clear=True,
            ),
        ):
            response = TestClient(app).post(
                "/v1/internal/tools/generate-comps-table",
                content='{"malformed_json":',
                headers={"Content-Type": "application/json"},
            )

        self.assertEqual(response.status_code, 401)
        body = response.json()
        self.assertEqual(body["error"]["code"], "UNAUTHORIZED")
        database_connect.assert_not_called()

    def test_generate_comps_table_rejects_non_ascii_auth_header(self) -> None:
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
                headers=[("Authorization", "Bearer inválido".encode())],
            )

        self.assertEqual(response.status_code, 401)
        body = response.json()
        self.assertEqual(body["error"]["code"], "UNAUTHORIZED")

    def test_generate_comps_table_accepts_case_insensitive_bearer_scheme(self) -> None:
        ticker_validator = Mock()
        ticker_validator.is_supported.return_value = True

        with (
            patch.dict(
                os.environ,
                self._env_with_internal_auth(),
                clear=True,
            ),
            patch(
                "comps_service.tool_validation.AlphaVantageTickerValidator",
                return_value=ticker_validator,
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
                headers={"Authorization": f"bearer {INTERNAL_TOOL_TOKEN}"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn(
            "ALPHA_VANTAGE_API_KEY",
            response.json()["error"]["message"],
        )

    # Shares Alpha Vantage request pacing across validator instances.
    def test_alpha_vantage_rate_limit_is_shared_between_validators(self) -> None:
        response = Mock()
        response.json.return_value = {"bestMatches": []}
        response.raise_for_status.return_value = None
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=None)
        client.get.return_value = response
        directory = TickerDirectory()
        request_limiter = AlphaVantageRequestLimiter()

        with (
            patch("comps_service.tool_validation.httpx.Client", return_value=client),
            patch("comps_service.tool_validation.time.monotonic", return_value=100.0),
            patch("comps_service.tool_validation.time.sleep") as sleep,
        ):
            first_validator = AlphaVantageTickerValidator(
                environ={
                    "ALPHA_VANTAGE_API_KEY": "test-key",
                    "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "1.1",
                },
                request_limiter=request_limiter,
                ticker_directory=directory,
            )
            second_validator = AlphaVantageTickerValidator(
                environ={
                    "ALPHA_VANTAGE_API_KEY": "test-key",
                    "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "1.1",
                },
                request_limiter=request_limiter,
                ticker_directory=directory,
            )

            self.assertFalse(first_validator.is_supported("AAPL"))
            self.assertFalse(second_validator.is_supported("MSFT"))

        sleep.assert_called_once_with(1.1)

    def test_ticker_directory_reuses_known_valid_and_invalid_symbols(self) -> None:
        responses = [
            {
                "bestMatches": [
                    {"1. symbol": "AAPL", "3. type": "Equity"},
                ]
            },
            {"bestMatches": []},
        ]
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.side_effect = responses
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=None)
        client.get.return_value = response
        directory = TickerDirectory()

        with patch("comps_service.tool_validation.httpx.Client", return_value=client):
            first_validator = AlphaVantageTickerValidator(
                environ={
                    "ALPHA_VANTAGE_API_KEY": "test-key",
                    "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
                },
                ticker_directory=directory,
            )
            second_validator = AlphaVantageTickerValidator(
                environ={
                    "ALPHA_VANTAGE_API_KEY": "test-key",
                    "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
                },
                ticker_directory=directory,
            )

            self.assertTrue(first_validator.is_supported("AAPL"))
            self.assertTrue(second_validator.is_supported("AAPL"))
            self.assertFalse(first_validator.is_supported("ZZZZ"))
            self.assertFalse(second_validator.is_supported("ZZZZ"))

        self.assertEqual(client.get.call_count, 2)

    # Rejects user-supplied peer mode before provider or database work when peers are missing.
    def test_user_supplied_mode_requires_peer_tickers_before_run_creation(self) -> None:
        database_connect = Mock()

        with (
            patch.dict(
                sys.modules,
                {"psycopg": SimpleNamespace(connect=database_connect)},
            ),
            patch.dict(os.environ, self._env_with_internal_auth(), clear=True),
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
                headers=self._internal_tool_headers(),
            )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("peer_tickers", body["error"]["message"])
        database_connect.assert_not_called()

    # Preserves the ADR-defined auto contract without implementing peer selection.
    def test_auto_mode_returns_not_implemented_before_run_creation(self) -> None:
        database_connect = Mock()

        with (
            patch.dict(
                sys.modules,
                {"psycopg": SimpleNamespace(connect=database_connect)},
            ),
            patch.dict(os.environ, self._env_with_internal_auth(), clear=True),
        ):
            response = TestClient(app).post(
                "/v1/internal/tools/generate-comps-table",
                json={
                    "invocation_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "trigger_message_id": str(uuid4()),
                    "target_ticker": "AAPL",
                    "peer_selection_mode": "auto",
                    "analysis_period": "latest",
                },
                headers=self._internal_tool_headers(),
            )

        self.assertEqual(response.status_code, 501)
        body = response.json()
        self.assertEqual(body["error"]["code"], "INTERNAL_ERROR")
        self.assertIn("Auto peer selection is not implemented", body["error"]["message"])
        database_connect.assert_not_called()

    # Normalizes mixed-case ticker candidates before the configured Run data source.
    def test_mixed_case_tickers_pass_pre_run_validation_without_creating_run(self) -> None:
        database_connect = Mock()
        company_data_source = RecordingUnavailableCompanyDataSource()
        app.dependency_overrides[get_company_data_source] = lambda: company_data_source
        self.addCleanup(app.dependency_overrides.clear)

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
                headers=self._internal_tool_headers(),
            )

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["error"]["code"], "INTERNAL_ERROR")
        self.assertIn("intentionally stopped", body["error"]["message"])
        self.assertEqual(company_data_source.requested_tickers, ["AAPL", "MSFT"])
        database_connect.assert_not_called()

    # Rejects ticker candidates with unsupported syntax before provider or database work.
    def test_malformed_ticker_returns_validation_error_before_run_creation(self) -> None:
        database_connect = Mock()

        with (
            patch.dict(
                sys.modules,
                {"psycopg": SimpleNamespace(connect=database_connect)},
            ),
            patch.dict(os.environ, self._env_with_internal_auth(), clear=True),
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
                headers=self._internal_tool_headers(),
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
            patch.dict(os.environ, self._env_with_internal_auth(), clear=True),
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
                headers=self._internal_tool_headers(),
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
            patch.dict(os.environ, self._env_with_internal_auth(), clear=True),
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
                headers=self._internal_tool_headers(),
            )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("peer_tickers", str(body["error"]["details"]))
        database_connect.assert_not_called()

    # Rejects the deferred historical execution field before Run creation.
    def test_as_of_date_is_not_accepted_for_latest_runs(self) -> None:
        database_connect = Mock()

        with (
            patch.dict(
                sys.modules,
                {"psycopg": SimpleNamespace(connect=database_connect)},
            ),
            patch.dict(os.environ, self._env_with_internal_auth(), clear=True),
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
                    "as_of_date": "2026-07-01",
                },
                headers=self._internal_tool_headers(),
            )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("as_of_date", str(response.json()["error"]["details"]))
        database_connect.assert_not_called()

    # Rejects self-comparison requests before provider or database work.
    def test_target_ticker_cannot_also_be_a_peer_ticker(self) -> None:
        database_connect = Mock()

        with (
            patch.dict(
                sys.modules,
                {"psycopg": SimpleNamespace(connect=database_connect)},
            ),
            patch.dict(os.environ, self._env_with_internal_auth(), clear=True),
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
                headers=self._internal_tool_headers(),
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

    # Rejects duplicate peer tickers before provider or database work.
    def test_duplicate_peer_tickers_return_validation_error_before_run_creation(
        self,
    ) -> None:
        database_connect = Mock()

        with (
            patch.dict(
                sys.modules,
                {"psycopg": SimpleNamespace(connect=database_connect)},
            ),
            patch.dict(os.environ, self._env_with_internal_auth(), clear=True),
        ):
            response = TestClient(app).post(
                "/v1/internal/tools/generate-comps-table",
                json={
                    "invocation_id": str(uuid4()),
                    "thread_id": str(uuid4()),
                    "trigger_message_id": str(uuid4()),
                    "target_ticker": "AAPL",
                    "peer_tickers": ["MSFT", "msft"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                },
                headers=self._internal_tool_headers(),
            )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("Peer tickers must be unique", body["error"]["message"])
        self.assertEqual(
            body["error"]["details"]["duplicate_peer_tickers"],
            ["MSFT"],
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
                    "target_ticker": "AAPL",
                    "peer_tickers": ["ZZZZ"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                },
                headers=self._internal_tool_headers(),
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
            patch.dict(os.environ, self._env_with_internal_auth(), clear=True),
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
                headers=self._internal_tool_headers(),
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
                self._env_with_internal_auth(
                    {
                        "ALPHA_VANTAGE_API_KEY": "test-key",
                        "ALPHA_VANTAGE_BASE_URL": base_url,
                        "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
                    }
                ),
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
                headers=self._internal_tool_headers(),
            )

        self.assertEqual(response.status_code, 502)
        body = response.json()
        self.assertEqual(body["error"]["code"], "UPSTREAM_ERROR")
        self.assertIn("malformed", body["error"]["message"])
        self.assertEqual(body["error"]["details"]["provider"], "alpha_vantage")
        database_connect.assert_not_called()

    # Rejects exact Alpha Vantage matches that are not company equity symbols.
    def test_non_equity_ticker_match_returns_validation_error_before_run_creation(
        self,
    ) -> None:
        database_connect = Mock()

        with (
            _alpha_vantage_server(
                {
                    "AAPL": (
                        b'{"bestMatches":[{"1. symbol":"AAPL",'
                        b'"3. type":"Equity"}]}'
                    ),
                    "FUND": (
                        b'{"bestMatches":[{"1. symbol":"FUND",'
                        b'"3. type":"Mutual Fund"}]}'
                    ),
                }
            ) as base_url,
            patch.dict(
                sys.modules,
                {"psycopg": SimpleNamespace(connect=database_connect)},
            ),
            patch.dict(
                os.environ,
                self._env_with_internal_auth(
                    {
                        "ALPHA_VANTAGE_API_KEY": "test-key",
                        "ALPHA_VANTAGE_BASE_URL": base_url,
                        "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
                    }
                ),
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
                    "peer_tickers": ["FUND"],
                    "peer_selection_mode": "user_supplied",
                    "analysis_period": "latest",
                },
                headers=self._internal_tool_headers(),
            )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("Unsupported ticker", body["error"]["message"])
        self.assertEqual(body["error"]["details"]["unsupported_tickers"], ["FUND"])
        database_connect.assert_not_called()

    # Lets live Alpha Vantage exact-match tickers reach the configured Run data source.
    def test_valid_tickers_pass_pre_run_validation_without_creating_run(self) -> None:
        database_connect = Mock()
        company_data_source = RecordingUnavailableCompanyDataSource()
        app.dependency_overrides[get_company_data_source] = lambda: company_data_source
        self.addCleanup(app.dependency_overrides.clear)

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
                headers=self._internal_tool_headers(),
            )

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["error"]["code"], "INTERNAL_ERROR")
        self.assertIn("intentionally stopped", body["error"]["message"])
        self.assertEqual(company_data_source.requested_tickers, ["AAPL", "MSFT"])
        database_connect.assert_not_called()

    def _live_alpha_vantage_env(self) -> dict[str, str]:
        self._wait_for_live_alpha_vantage_slot()
        return self._env_with_internal_auth(
            {
                "ALPHA_VANTAGE_API_KEY": self._test_alpha_vantage_api_key(),
                "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": str(
                    ALPHA_VANTAGE_TEST_REQUEST_INTERVAL_SECONDS
                ),
            }
        )

    def _wait_for_live_alpha_vantage_slot(self) -> None:
        elapsed_seconds = time.monotonic() - self.__class__._last_live_validation_at
        if elapsed_seconds < ALPHA_VANTAGE_TEST_REQUEST_INTERVAL_SECONDS:
            time.sleep(ALPHA_VANTAGE_TEST_REQUEST_INTERVAL_SECONDS - elapsed_seconds)
        self.__class__._last_live_validation_at = time.monotonic()

    def _test_alpha_vantage_api_key(self) -> str:
        if os.environ.get(RUN_LIVE_ALPHA_VANTAGE_TESTS_VAR, "").strip() != "1":
            self.skipTest(
                f"{RUN_LIVE_ALPHA_VANTAGE_TESTS_VAR}=1 is required for live "
                "Alpha Vantage validation tests."
            )
        api_key = os.environ.get(TEST_ALPHA_VANTAGE_API_KEY_VAR, "").strip()
        if not api_key:
            self.skipTest(
                f"{TEST_ALPHA_VANTAGE_API_KEY_VAR} is required for live "
                "Alpha Vantage validation tests."
            )
        return api_key


class _AlphaVantageResponseHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        response_body = self.server.response_body
        response_bodies = getattr(self.server, "response_bodies", None)
        if response_bodies is not None:
            query = parse_qs(urlparse(self.path).query)
            keyword = query.get("keywords", [""])[0]
            response_body = response_bodies[keyword]

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, _format: str, *args: object) -> None:
        return


@contextmanager
def _alpha_vantage_server(
    response_body: bytes | dict[str, bytes],
) -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _AlphaVantageResponseHandler)
    if isinstance(response_body, dict):
        server.response_body = b'{"bestMatches":[]}'
        server.response_bodies = response_body
    else:
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
