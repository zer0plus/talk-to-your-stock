from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from json import JSONDecodeError
from typing import Protocol
from uuid import UUID

import httpx
from pydantic import ValidationError

from talk_to_your_stock_shared import (
    ErrorResponse,
    FailCalculatedRunRequest,
    FinalizeComparisonTakeawayRequest,
    GenerateCompsDraftResponse,
    GenerateCompsToolRequest,
    GenerateCompsToolResponse,
    RunResponse,
)

COMPS_SERVICE_URL_VAR = "COMPS_SERVICE_URL"
COMPS_SERVICE_INTERNAL_TOKEN_VAR = "COMPS_SERVICE_INTERNAL_TOKEN"
COMPS_GENERATION_TIMEOUT_SECONDS = 30
COMPS_TERMINAL_TIMEOUT_SECONDS = 5


class CompsToolClient(Protocol):
    async def generate_comps_table(
        self,
        request: GenerateCompsToolRequest,
    ) -> GenerateCompsDraftResponse: ...

    async def finalize_comps_run(
        self,
        run_id: UUID,
        request: FinalizeComparisonTakeawayRequest,
    ) -> GenerateCompsToolResponse: ...

    async def fail_comps_run(
        self,
        run_id: UUID,
        request: FailCalculatedRunRequest,
    ) -> RunResponse: ...


class CompsToolUnavailable(RuntimeError):
    pass


class CompsToolValidationError(RuntimeError):
    def __init__(self, error: ErrorResponse) -> None:
        super().__init__(error.error.message)
        self.error = error


class CompsToolError(RuntimeError):
    def __init__(self, *, status_code: int, error: ErrorResponse) -> None:
        super().__init__(error.error.message)
        self.status_code = status_code
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
    ) -> GenerateCompsDraftResponse:
        try:
            async with asyncio.timeout(COMPS_GENERATION_TIMEOUT_SECONDS):
                async with httpx.AsyncClient(
                    timeout=COMPS_GENERATION_TIMEOUT_SECONDS
                ) as client:
                    response = await client.post(
                        f"{self._base_url}/v1/internal/tools/generate-comps-table",
                        headers={
                            "Authorization": f"Bearer {self._internal_token}"
                        },
                        json=request.model_dump(mode="json"),
                    )
        except (httpx.HTTPError, TimeoutError) as exc:
            raise CompsToolUnavailable("Comps Service unavailable.") from exc

        if response.is_error:
            try:
                error = ErrorResponse.model_validate(response.json())
            except (JSONDecodeError, ValidationError, ValueError):
                raise CompsToolUnavailable(
                    "Comps Service returned an invalid error response."
                ) from None
            if response.status_code == 400:
                raise CompsToolValidationError(error)
            status_code = response.status_code
            if status_code not in (502, 503):
                status_code = 502
            raise CompsToolError(status_code=status_code, error=error)

        try:
            return GenerateCompsDraftResponse.model_validate(response.json())
        except (JSONDecodeError, ValidationError, ValueError):
            raise CompsToolUnavailable(
                "Comps Service returned an invalid Tool response."
            ) from None

    async def finalize_comps_run(
        self,
        run_id: UUID,
        request: FinalizeComparisonTakeawayRequest,
    ) -> GenerateCompsToolResponse:
        try:
            async with asyncio.timeout(COMPS_TERMINAL_TIMEOUT_SECONDS):
                async with httpx.AsyncClient(
                    timeout=COMPS_TERMINAL_TIMEOUT_SECONDS
                ) as client:
                    response = await client.post(
                        f"{self._base_url}/v1/internal/runs/{run_id}/finalize",
                        headers={
                            "Authorization": f"Bearer {self._internal_token}"
                        },
                        json=request.model_dump(mode="json"),
                    )
        except (httpx.HTTPError, TimeoutError) as exc:
            raise CompsToolUnavailable("Comps Service unavailable.") from exc

        if response.is_error:
            try:
                error = ErrorResponse.model_validate(response.json())
            except (JSONDecodeError, ValidationError, ValueError):
                raise CompsToolUnavailable(
                    "Comps Service returned an invalid error response."
                ) from None
            status_code = response.status_code
            if status_code not in (502, 503):
                status_code = 502
            raise CompsToolError(status_code=status_code, error=error)

        try:
            return GenerateCompsToolResponse.model_validate(response.json())
        except (JSONDecodeError, ValidationError, ValueError):
            raise CompsToolUnavailable(
                "Comps Service returned an invalid finalization response."
            ) from None

    async def fail_comps_run(
        self,
        run_id: UUID,
        request: FailCalculatedRunRequest,
    ) -> RunResponse:
        try:
            async with asyncio.timeout(COMPS_TERMINAL_TIMEOUT_SECONDS):
                async with httpx.AsyncClient(
                    timeout=COMPS_TERMINAL_TIMEOUT_SECONDS
                ) as client:
                    response = await client.post(
                        f"{self._base_url}/v1/internal/runs/{run_id}/fail",
                        headers={
                            "Authorization": f"Bearer {self._internal_token}"
                        },
                        json=request.model_dump(mode="json"),
                    )
        except (httpx.HTTPError, TimeoutError) as exc:
            raise CompsToolUnavailable("Comps Service unavailable.") from exc

        if response.is_error:
            try:
                error = ErrorResponse.model_validate(response.json())
            except (JSONDecodeError, ValidationError, ValueError):
                raise CompsToolUnavailable(
                    "Comps Service returned an invalid error response."
                ) from None
            status_code = response.status_code
            if status_code not in (502, 503):
                status_code = 502
            raise CompsToolError(status_code=status_code, error=error)

        try:
            return RunResponse.model_validate(response.json())
        except (JSONDecodeError, ValidationError, ValueError):
            raise CompsToolUnavailable(
                "Comps Service returned an invalid failed Run response."
            ) from None
