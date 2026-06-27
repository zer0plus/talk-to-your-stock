from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from json import JSONDecodeError
from typing import Any
from urllib.parse import urlencode
from urllib.error import URLError
from urllib.request import urlopen


class AlphaVantageConfigError(RuntimeError):
    pass


class AlphaVantageProviderError(RuntimeError):
    pass


JsonObject = dict[str, Any]
RequestJson = Callable[[Mapping[str, str]], JsonObject]


class AlphaVantageClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = 20,
        request_json: RequestJson | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("ALPHA_VANTAGE_API_KEY", "")).strip()
        self.base_url = base_url or os.getenv("ALPHA_VANTAGE_BASE_URL", "https://www.alphavantage.co/query")
        self.timeout_seconds = timeout_seconds
        self._request_json = request_json

    def get_global_quote(self, symbol: str) -> JsonObject:
        return self._get_json(function="GLOBAL_QUOTE", symbol=symbol)

    def get_overview(self, symbol: str) -> JsonObject:
        return self._get_json(function="OVERVIEW", symbol=symbol)

    def get_income_statement(self, symbol: str) -> JsonObject:
        return self._get_json(function="INCOME_STATEMENT", symbol=symbol)

    def get_balance_sheet(self, symbol: str) -> JsonObject:
        return self._get_json(function="BALANCE_SHEET", symbol=symbol)

    def get_fx_daily(self, from_currency: str, to_currency: str) -> JsonObject:
        return self._get_json(
            function="FX_DAILY",
            from_symbol=from_currency.upper(),
            to_symbol=to_currency.upper(),
            outputsize="compact",
        )

    def _get_json(self, **params: str) -> JsonObject:
        if not self.api_key:
            raise AlphaVantageConfigError("ALPHA_VANTAGE_API_KEY is required.")

        request_params = {**params, "apikey": self.api_key}
        payload = (
            self._request_json(request_params)
            if self._request_json is not None
            else self._urllib_request_json(request_params)
        )
        if not isinstance(payload, dict):
            raise AlphaVantageProviderError("Alpha Vantage returned a non-object JSON payload.")
        self._raise_for_provider_error(payload)
        return payload

    def _urllib_request_json(self, params: Mapping[str, str]) -> JsonObject:
        query = urlencode(params)
        try:
            with urlopen(f"{self.base_url}?{query}", timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (JSONDecodeError, OSError, URLError) as exc:
            raise AlphaVantageProviderError("Alpha Vantage request failed.") from exc
        if not isinstance(payload, dict):
            raise AlphaVantageProviderError("Alpha Vantage returned a non-object JSON payload.")
        return payload

    def _raise_for_provider_error(self, payload: JsonObject) -> None:
        message = (
            payload.get("Error Message")
            or payload.get("Information")
            or payload.get("Note")
        )
        if message:
            raise AlphaVantageProviderError(str(message))
