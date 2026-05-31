from __future__ import annotations

import csv
import io
import threading
import time
from typing import Any

import httpx

from comps_service.settings import settings


class AlphaVantageConfigError(RuntimeError):
    pass


class AlphaVantageProviderError(RuntimeError):
    pass


class AlphaVantageClient:
    def __init__(self) -> None:
        self._request_lock = threading.Lock()
        self._last_request_at = 0.0

    def _require_api_key(self) -> str:
        if not settings.alpha_vantage_api_key:
            raise AlphaVantageConfigError("ALPHA_VANTAGE_API_KEY is required for comps generation.")
        return settings.alpha_vantage_api_key

    def _get_json(self, params: dict[str, str]) -> dict[str, Any]:
        params = {**params, "apikey": self._require_api_key()}
        self._wait_for_rate_limit_slot()
        with httpx.Client(timeout=settings.alpha_vantage_timeout_seconds) as client:
            response = client.get(settings.alpha_vantage_base_url, params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise AlphaVantageProviderError("Alpha Vantage returned a non-object JSON payload.")
        self._raise_for_provider_error(payload)
        return payload

    def _get_csv_rows(self, params: dict[str, str]) -> list[dict[str, str]]:
        params = {**params, "apikey": self._require_api_key()}
        self._wait_for_rate_limit_slot()
        with httpx.Client(timeout=settings.alpha_vantage_timeout_seconds) as client:
            response = client.get(settings.alpha_vantage_base_url, params=params)
        response.raise_for_status()
        text = response.text.strip()
        if not text:
            return []
        if text.startswith("{"):
            payload = response.json()
            self._raise_for_provider_error(payload)
            raise AlphaVantageProviderError("Alpha Vantage returned JSON where CSV was expected.")
        rows = list(csv.DictReader(io.StringIO(text)))
        if rows and list(rows[0].keys()) == ["Information"]:
            raise AlphaVantageProviderError(rows[0].get("Information") or "Alpha Vantage informational response.")
        return rows

    def _raise_for_provider_error(self, payload: dict[str, Any]) -> None:
        for key in ("Error Message", "Note", "Information"):
            value = payload.get(key)
            if value:
                raise AlphaVantageProviderError(str(value))

    def _wait_for_rate_limit_slot(self) -> None:
        with self._request_lock:
            elapsed = time.monotonic() - self._last_request_at
            wait_seconds = settings.alpha_vantage_min_request_interval_seconds - elapsed
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            self._last_request_at = time.monotonic()

    def get_global_quote(self, symbol: str) -> dict[str, Any]:
        params = {"function": "GLOBAL_QUOTE", "symbol": symbol}
        if settings.alpha_vantage_quote_entitlement:
            params["entitlement"] = settings.alpha_vantage_quote_entitlement
        payload = self._get_json(params)
        quote = payload.get("Global Quote")
        if not isinstance(quote, dict) or not quote:
            raise AlphaVantageProviderError(f"GLOBAL_QUOTE returned no quote for {symbol}.")
        return quote

    def get_overview(self, symbol: str) -> dict[str, Any]:
        payload = self._get_json({"function": "OVERVIEW", "symbol": symbol})
        if not payload.get("Symbol"):
            raise AlphaVantageProviderError(f"OVERVIEW returned no company payload for {symbol}.")
        return payload

    def get_income_statement(self, symbol: str) -> dict[str, Any]:
        payload = self._get_json({"function": "INCOME_STATEMENT", "symbol": symbol})
        if not payload.get("quarterlyReports"):
            raise AlphaVantageProviderError(f"INCOME_STATEMENT returned no quarterly reports for {symbol}.")
        return payload

    def get_balance_sheet(self, symbol: str) -> dict[str, Any]:
        payload = self._get_json({"function": "BALANCE_SHEET", "symbol": symbol})
        if not payload.get("quarterlyReports"):
            raise AlphaVantageProviderError(f"BALANCE_SHEET returned no quarterly reports for {symbol}.")
        return payload

    def get_earnings_calendar(self, symbol: str) -> list[dict[str, str]]:
        return self._get_csv_rows(
            {"function": "EARNINGS_CALENDAR", "symbol": symbol, "horizon": settings.alpha_vantage_earnings_horizon}
        )
