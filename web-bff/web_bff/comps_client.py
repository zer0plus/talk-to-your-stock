from __future__ import annotations

import os
from collections.abc import Mapping
from json import JSONDecodeError
from typing import TypeVar
from uuid import UUID

import httpx
from pydantic import BaseModel, ValidationError

from talk_to_your_stock_shared import (
    Run,
    RunResponse,
    RunTableResponse,
    TraceResponse,
)

COMPS_SERVICE_URL_VAR = "COMPS_SERVICE_URL"
ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class CompsServiceUnavailable(RuntimeError):
    pass


class CompsArtifactNotFound(LookupError):
    pass


class HttpCompsClient:
    def __init__(self, *, base_url: str) -> None:
        if not base_url.strip():
            raise CompsServiceUnavailable(f"{COMPS_SERVICE_URL_VAR} is required.")
        self._base_url = base_url.rstrip("/")

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> HttpCompsClient:
        env = os.environ if environ is None else environ
        return cls(base_url=env.get(COMPS_SERVICE_URL_VAR, ""))

    def get_run(self, run_id: UUID) -> Run:
        response = self._get(f"/v1/runs/{run_id}", RunResponse)
        return response.run

    def get_table(self, run_id: UUID) -> RunTableResponse:
        return self._get(f"/v1/runs/{run_id}/table", RunTableResponse)

    def get_trace(self, run_id: UUID) -> TraceResponse:
        return self._get(f"/v1/runs/{run_id}/trace", TraceResponse)

    def _get(
        self,
        path: str,
        response_model: type[ResponseModel],
    ) -> ResponseModel:
        try:
            response = httpx.get(f"{self._base_url}{path}", timeout=30)
        except httpx.HTTPError as exc:
            raise CompsServiceUnavailable("Comps Service unavailable.") from exc

        if response.status_code == 404:
            raise CompsArtifactNotFound("Comps artifact not found.")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise CompsServiceUnavailable(
                f"Comps Service returned HTTP {response.status_code}."
            ) from exc

        try:
            return response_model.model_validate(response.json())
        except (JSONDecodeError, ValidationError, ValueError) as exc:
            raise CompsServiceUnavailable(
                "Comps Service returned an invalid response."
            ) from exc
