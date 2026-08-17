from __future__ import annotations

import os
from collections.abc import Mapping
from json import JSONDecodeError
from typing import TypeVar
from uuid import UUID

import httpx
from pydantic import BaseModel, ValidationError

from talk_to_your_stock_shared import (
    ErrorResponse,
    Run,
    RunListResponse,
    RunResponse,
    RunStatus,
    RunTableResponse,
    SourceSnapshotResponse,
    TraceResponse,
)

COMPS_SERVICE_URL_VAR = "COMPS_SERVICE_URL"
ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class CompsServiceUnavailable(RuntimeError):
    pass


class CompsArtifactNotFound(LookupError):
    pass


class CompsRequestInvalid(ValueError):
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
        self._require_run_id(requested=run_id, returned=response.run.id)
        return response.run

    def get_table(self, run_id: UUID) -> RunTableResponse:
        table = self._get(f"/v1/runs/{run_id}/table", RunTableResponse)
        self._require_run_id(requested=run_id, returned=table.run_id)
        return table

    def get_trace(self, run_id: UUID) -> TraceResponse:
        trace = self._get(f"/v1/runs/{run_id}/trace", TraceResponse)
        self._require_run_id(requested=run_id, returned=trace.run_id)
        return trace

    def get_source_snapshot(self, run_id: UUID) -> SourceSnapshotResponse:
        source_snapshot = self._get(
            f"/v1/runs/{run_id}/source-snapshot",
            SourceSnapshotResponse,
        )
        self._require_run_id(requested=run_id, returned=source_snapshot.run_id)
        return source_snapshot

    def list_runs(
        self,
        *,
        thread_id: UUID,
        status: RunStatus | None,
        limit: int,
        cursor: str | None,
    ) -> RunListResponse:
        params: dict[str, str | int] = {"limit": limit}
        if status is not None:
            params["status"] = status.value
        if cursor is not None:
            params["cursor"] = cursor
        response = self._get(
            f"/v1/threads/{thread_id}/runs",
            RunListResponse,
            params=params,
        )
        if any(run.thread_id != thread_id for run in response.runs):
            raise CompsServiceUnavailable(
                "Comps Service returned mismatched Thread Run linkage."
            )
        return response

    @staticmethod
    def _require_run_id(*, requested: UUID, returned: UUID) -> None:
        if returned != requested:
            raise CompsServiceUnavailable(
                "Comps Service returned mismatched Run linkage."
            )

    def _get(
        self,
        path: str,
        response_model: type[ResponseModel],
        *,
        params: Mapping[str, str | int] | None = None,
    ) -> ResponseModel:
        try:
            response = httpx.get(
                f"{self._base_url}{path}",
                params=params,
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise CompsServiceUnavailable("Comps Service unavailable.") from exc

        if response.status_code == 404:
            raise CompsArtifactNotFound("Comps artifact not found.")
        if response.status_code == 400:
            try:
                error = ErrorResponse.model_validate(response.json())
            except (JSONDecodeError, ValidationError, ValueError):
                raise CompsRequestInvalid("Comps request is invalid.") from None
            raise CompsRequestInvalid(error.error.message)
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
