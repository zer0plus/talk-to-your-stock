from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI, HTTPException, Response

from comps_service.alpha_vantage import AlphaVantageConfigError, AlphaVantageProviderError
from comps_service.db import ensure_schema
from comps_service.exports.csv_exporter import to_csv
from comps_service.exports.xlsx_exporter import to_xlsx
from comps_service.fundamental_cache import CacheRefreshInProgress
from comps_service.fundamentals import FundamentalDataUnavailable
from comps_service.repository import CompsRepository, RunNotFoundError
from comps_service.service import CompsService
from comps_service.settings import settings
from talk_to_your_stock_shared import GenerateCompsToolRequest, Readiness
from talk_to_your_stock_shared.time import utc_now

app = FastAPI(title="TalkToYourStock Comps Service", version="0.1.0")
repository = CompsRepository()
service = CompsService(repository=repository)


@app.on_event("startup")
def startup() -> None:
    ensure_schema()


@app.get("/v1/health", tags=["Health"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "comps-service", "time": utc_now().isoformat()}


@app.get("/v1/ready", tags=["Health"], response_model=Readiness)
def ready() -> Readiness:
    checks: dict[str, str] = {}
    try:
        ensure_schema()
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {type(exc).__name__}"
    try:
        service.fundamentals.ping_cache()
        checks["cache"] = "ok"
    except Exception as exc:
        checks["cache"] = f"error: {type(exc).__name__}"
    checks["provider_config"] = "ok" if settings.alpha_vantage_api_key else "missing"
    status = "ready" if all(value == "ok" for value in checks.values()) else "degraded"
    return Readiness(status=status, checks=checks, time=utc_now())


@app.post("/v1/internal/tools/generate-comps-table")
def generate_comps_table(request: GenerateCompsToolRequest):
    try:
        return service.generate_comps_table(request)
    except AlphaVantageConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except CacheRefreshInProgress as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AlphaVantageProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except FundamentalDataUnavailable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/runs/{run_id}")
def get_run(run_id: UUID):
    try:
        return {"run": repository.get_run(run_id)}
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc


@app.get("/v1/runs/{run_id}/table")
def get_table(run_id: UUID):
    try:
        return repository.get_table(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Run table not found") from exc


@app.get("/v1/runs/{run_id}/trace")
def get_trace(run_id: UUID):
    try:
        return repository.get_trace(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Run trace not found") from exc


@app.get("/v1/runs/{run_id}/export.csv")
def export_csv(run_id: UUID) -> Response:
    try:
        table = repository.get_table(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Run table not found") from exc
    return Response(
        content=to_csv(table),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{run_id}.csv"'},
    )


@app.get("/v1/runs/{run_id}/export.xlsx")
def export_xlsx(run_id: UUID) -> Response:
    try:
        table = repository.get_table(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Run table not found") from exc
    return Response(
        content=to_xlsx(table),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{run_id}.xlsx"'},
    )
