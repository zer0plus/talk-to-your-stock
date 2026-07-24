from __future__ import annotations

import os
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from talk_to_your_stock_shared import GenerateCompsToolRequest

from .provider_config import InvalidProviderConfiguration, seconds_setting

ALPHA_VANTAGE_API_KEY_VAR = "ALPHA_VANTAGE_API_KEY"
ALPHA_VANTAGE_BASE_URL_VAR = "ALPHA_VANTAGE_BASE_URL"
ALPHA_VANTAGE_TIMEOUT_SECONDS_VAR = "ALPHA_VANTAGE_TIMEOUT_SECONDS"
ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS_VAR = (
    "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS"
)
DEFAULT_ALPHA_VANTAGE_BASE_URL = "https://www.alphavantage.co/query"
DEFAULT_ALPHA_VANTAGE_TIMEOUT_SECONDS = 20.0
DEFAULT_ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS = 1.1


@dataclass(frozen=True)
class ToolValidationError(Exception):
    message: str
    details: dict[str, object]


@dataclass(frozen=True)
class RuntimeConfigurationError(Exception):
    message: str
    details: dict[str, object]


@dataclass(frozen=True)
class UpstreamValidationError(Exception):
    message: str
    details: dict[str, object]


class AlphaVantageRequestLimiter:
    def __init__(self) -> None:
        self._last_request_at = 0.0
        self._lock = threading.Lock()

    def wait_for_slot(self, interval_seconds: float) -> None:
        with self._lock:
            elapsed_seconds = time.monotonic() - self._last_request_at
            if elapsed_seconds < interval_seconds:
                time.sleep(interval_seconds - elapsed_seconds)
            self._last_request_at = time.monotonic()


ALPHA_VANTAGE_REQUEST_LIMITER = AlphaVantageRequestLimiter()


class TickerDirectory:
    def __init__(self) -> None:
        self._support_by_ticker: dict[str, bool] = {}
        self._lock = threading.Lock()

    def find(self, ticker: str) -> bool | None:
        with self._lock:
            return self._support_by_ticker.get(ticker.upper())

    def remember(self, ticker: str, *, is_supported: bool) -> None:
        with self._lock:
            self._support_by_ticker[ticker.upper()] = is_supported


_TICKER_DIRECTORY = TickerDirectory()


class AlphaVantageTickerValidator:
    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        request_limiter: AlphaVantageRequestLimiter | None = None,
        ticker_directory: TickerDirectory | None = None,
    ) -> None:
        self.environ = os.environ if environ is None else environ
        self._request_limiter = request_limiter or ALPHA_VANTAGE_REQUEST_LIMITER
        self._ticker_directory = ticker_directory or _TICKER_DIRECTORY

    def is_supported(self, ticker: str) -> bool:
        known_support = self._ticker_directory.find(ticker)
        if known_support is not None:
            return known_support
        payload = self._search_symbol(ticker)
        matches = payload.get("bestMatches")
        if not isinstance(matches, list):
            raise UpstreamValidationError(
                message="Alpha Vantage symbol search returned an unexpected payload.",
                details={"provider": "alpha_vantage"},
            )
        is_supported = any(
            self._match_symbol(match) == ticker.upper() for match in matches
        )
        self._ticker_directory.remember(ticker, is_supported=is_supported)
        return is_supported

    def _search_symbol(self, ticker: str) -> dict[str, Any]:
        api_key = self._api_key()
        try:
            self._wait_for_rate_limit_slot()
            with httpx.Client(timeout=self._timeout_seconds()) as client:
                response = client.get(
                    self.environ.get(
                        ALPHA_VANTAGE_BASE_URL_VAR,
                        DEFAULT_ALPHA_VANTAGE_BASE_URL,
                    ),
                    params={
                        "function": "SYMBOL_SEARCH",
                        "keywords": ticker,
                        "apikey": api_key,
                    },
                )
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                raise UpstreamValidationError(
                    message="Alpha Vantage symbol search returned malformed JSON.",
                    details={"provider": "alpha_vantage"},
                ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamValidationError(
                message="Alpha Vantage symbol search request failed.",
                details={"provider": "alpha_vantage"},
            ) from exc

        if not isinstance(payload, dict):
            raise UpstreamValidationError(
                message="Alpha Vantage symbol search returned a non-object payload.",
                details={"provider": "alpha_vantage"},
            )

        for key in ("Error Message", "Note", "Information"):
            value = payload.get(key)
            if value:
                raise UpstreamValidationError(
                    message=str(value),
                    details={"provider": "alpha_vantage", "provider_key": key},
                )

        return payload

    def _api_key(self) -> str:
        api_key = self.environ.get(ALPHA_VANTAGE_API_KEY_VAR, "").strip()
        if not api_key:
            raise RuntimeConfigurationError(
                message=f"Missing required configuration: {ALPHA_VANTAGE_API_KEY_VAR}.",
                details={"missing_configuration": [ALPHA_VANTAGE_API_KEY_VAR]},
            )
        return api_key

    def _timeout_seconds(self) -> float:
        return self._float_env(
            ALPHA_VANTAGE_TIMEOUT_SECONDS_VAR,
            DEFAULT_ALPHA_VANTAGE_TIMEOUT_SECONDS,
        )

    def _wait_for_rate_limit_slot(self) -> None:
        interval_seconds = self._float_env(
            ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS_VAR,
            DEFAULT_ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS,
        )
        self._request_limiter.wait_for_slot(interval_seconds)

    def _float_env(self, name: str, default: float) -> float:
        try:
            return seconds_setting(
                self.environ,
                name=name,
                default=default,
            )
        except InvalidProviderConfiguration as exc:
            raise RuntimeConfigurationError(
                message=str(exc),
                details={"invalid_configuration": [name]},
            ) from exc

    def _match_symbol(self, match: object) -> str | None:
        if not isinstance(match, dict):
            return None
        if match.get("3. type") != "Equity":
            return None
        symbol = match.get("1. symbol")
        return str(symbol).upper() if symbol else None


def validate_generate_comps_request(
    request: GenerateCompsToolRequest,
    *,
    ticker_validator: AlphaVantageTickerValidator | None = None,
) -> None:
    # Future auto mode should select Peer Tickers before this explicit-peer validation.
    target_ticker = request.target_ticker.upper()
    peer_tickers = [ticker.upper() for ticker in request.peer_tickers]
    duplicate_peer_tickers = sorted(
        {ticker for ticker in peer_tickers if peer_tickers.count(ticker) > 1}
    )
    if duplicate_peer_tickers:
        raise ToolValidationError(
            message="Peer tickers must be unique.",
            details={"duplicate_peer_tickers": duplicate_peer_tickers},
        )

    self_comparison_tickers = sorted(
        {ticker for ticker in peer_tickers if ticker == target_ticker}
    )
    if self_comparison_tickers:
        raise ToolValidationError(
            message="Target ticker cannot also be a peer ticker.",
            details={
                "target_ticker": target_ticker,
                "self_comparison_tickers": self_comparison_tickers,
            },
        )

    validator = ticker_validator or AlphaVantageTickerValidator()
    requested_tickers = [
        target_ticker,
        *peer_tickers,
    ]
    unsupported_tickers = [
        ticker
        for ticker in sorted(set(requested_tickers))
        if not validator.is_supported(ticker)
    ]
    if unsupported_tickers:
        raise ToolValidationError(
            message=f"Unsupported ticker: {', '.join(unsupported_tickers)}.",
            details={"unsupported_tickers": unsupported_tickers},
        )
