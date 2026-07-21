from __future__ import annotations

import os
from collections.abc import Mapping
from json import JSONDecodeError
from typing import Protocol

import httpx
from pydantic import ValidationError

from talk_to_your_stock_shared import (
    ErrorResponse,
    GenerateCompsToolRequest,
    GenerateCompsToolResponse,
)

COMPS_SERVICE_URL_VAR = "COMPS_SERVICE_URL"
COMPS_SERVICE_INTERNAL_TOKEN_VAR = "COMPS_SERVICE_INTERNAL_TOKEN"


class CompsToolClient(Protocol):
    async def generate_comps_table(
        self,
        request: GenerateCompsToolRequest,
    ) -> GenerateCompsToolResponse: ...


class CompsToolUnavailable(RuntimeError):
    pass


class CompsToolValidationError(RuntimeError):
    def __init__(self, error: ErrorResponse) -> None:
        super().__init__(error.error.message)
        self.error = error


class HttpCompsToolClient:
    def __init__(self, *, base_url: str, internal_token: str) -> None:
        if not base_url.strip():
            raise CompsToolUnavailable(f"{COMPS_SERVICE_URL_VAR} is required.")
        if not internal_token.strip():
            raise CompsToolUnavailable(
                f"{COMPS_SERVICE_INTERNAL_TOKEN_VAR} is required."
            )
        self._base_url = base_url.rstrip("/")
        self._internal_token = internal_token

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> HttpCompsToolClient:
        env = os.environ if environ is None else environ
        return cls(
            base_url=env.get(COMPS_SERVICE_URL_VAR, ""),
            internal_token=env.get(COMPS_SERVICE_INTERNAL_TOKEN_VAR, ""),
        )

    async def generate_comps_table(
        self,
        request: GenerateCompsToolRequest,
    ) -> GenerateCompsToolResponse:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self._base_url}/v1/internal/tools/generate-comps-table",
                    headers={"Authorization": f"Bearer {self._internal_token}"},
                    json=request.model_dump(mode="json"),
                )
        except httpx.HTTPError as exc:
            raise CompsToolUnavailable("Comps Service unavailable.") from exc

        if response.status_code == 400:
            try:
                error = ErrorResponse.model_validate(response.json())
            except (JSONDecodeError, ValidationError, ValueError) as exc:
                raise CompsToolUnavailable(
                    "Comps Service returned an invalid validation error."
                ) from exc
            raise CompsToolValidationError(error)

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise CompsToolUnavailable(
                f"Comps Service returned HTTP {response.status_code}."
            ) from exc

        try:
            return GenerateCompsToolResponse.model_validate(response.json())
        except (JSONDecodeError, ValidationError, ValueError) as exc:
            raise CompsToolUnavailable(
                "Comps Service returned an invalid Tool response."
            ) from exc
