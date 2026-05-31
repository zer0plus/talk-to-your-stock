from __future__ import annotations

import json
from functools import lru_cache
from typing import Any
from uuid import UUID

import httpx
import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from talk_to_your_stock_shared import AgentRequest, AgentResponse, MessageRole, Page, Readiness, User
from talk_to_your_stock_shared.time import utc_now
from web_bff.db import ensure_schema
from web_bff.repository import AppRepository, NotFoundError
from web_bff.settings import settings

app = FastAPI(
    title="TalkToYourStock Web BFF",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)
repository = AppRepository()


class CreateThreadRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class CreateMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
    client_message_id: str | None = Field(default=None, max_length=64)


@lru_cache(maxsize=1)
def load_openapi_spec() -> dict[str, Any]:
    with settings.openapi_path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def custom_openapi() -> dict[str, Any]:
    return load_openapi_spec()


app.openapi = custom_openapi


@app.on_event("startup")
def startup() -> None:
    ensure_schema()


async def current_user(x_demo_user_email: str | None = Header(default=None, alias="X-Demo-User-Email")) -> User:
    email = x_demo_user_email or settings.demo_user_email
    return repository.ensure_user(email=email, name=email.split("@")[0])


async def _service_get_json(base_url: str, path: str) -> Any:
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(f"{base_url}{path}")
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Resource not found")
        response.raise_for_status()
        return response.json()


@app.get("/healthz", tags=["Health"])
def legacy_health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/health", tags=["Health"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "web-bff", "time": utc_now().isoformat()}


@app.get("/v1/ready", tags=["Health"], response_model=Readiness)
def ready() -> Readiness:
    return Readiness(status="ready", checks={"db": "ok", "cache": "ok", "provider": "degraded"}, time=utc_now())


@app.get("/v1/me", tags=["Auth"])
async def me(user: User = Depends(current_user)) -> dict[str, User]:
    return {"user": user}


@app.post("/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["Auth"])
def logout() -> Response:
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/v1/threads", tags=["Threads"])
async def list_threads(
    limit: int = Query(default=20, ge=1, le=200),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    threads = repository.list_threads(user_id=user.id, limit=limit)
    return {"threads": threads, "page": Page()}


@app.post("/v1/threads", status_code=status.HTTP_201_CREATED, tags=["Threads"])
async def create_thread(request: CreateThreadRequest, user: User = Depends(current_user)) -> dict[str, Any]:
    thread = repository.create_thread(user_id=user.id, title=request.title)
    return {"thread": thread}


@app.get("/v1/threads/{thread_id}", tags=["Threads"])
async def get_thread(thread_id: UUID, user: User = Depends(current_user)) -> dict[str, Any]:
    try:
        return {"thread": repository.get_thread(user_id=user.id, thread_id=thread_id)}
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Thread not found") from exc


@app.get("/v1/threads/{thread_id}/messages", tags=["Messages"])
async def list_messages(
    thread_id: UUID,
    limit: int = Query(default=20, ge=1, le=200),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    try:
        repository.get_thread(user_id=user.id, thread_id=thread_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Thread not found") from exc
    return {"messages": repository.list_messages(thread_id=thread_id, limit=limit), "page": Page()}


@app.post("/v1/threads/{thread_id}/messages", status_code=status.HTTP_201_CREATED, tags=["Messages"])
async def create_message(thread_id: UUID, request: CreateMessageRequest, user: User = Depends(current_user)) -> dict[str, Any]:
    try:
        history = repository.list_messages(thread_id=repository.get_thread(user_id=user.id, thread_id=thread_id).id, limit=50)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Thread not found") from exc

    user_message = repository.create_message(
        thread_id=thread_id,
        role=MessageRole.USER,
        content=request.content,
    )

    agent_request = AgentRequest(
        thread_id=thread_id,
        trigger_message_id=user_message.id,
        content=request.content,
        history=history,
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{settings.agent_service_url}/v1/agent/respond",
            json=jsonable_encoder(agent_request),
        )
        response.raise_for_status()
        agent_response = AgentResponse.model_validate(response.json())

    assistant_message = repository.create_message(
        thread_id=thread_id,
        role=MessageRole.ASSISTANT,
        content=agent_response.assistant_content,
        run_id=agent_response.run.id if agent_response.run else None,
    )

    return {
        "user_message": user_message,
        "assistant_message": assistant_message,
        "run": agent_response.run,
        "events_url": f"/v1/threads/{thread_id}/events" if agent_response.run else None,
    }


@app.get("/v1/threads/{thread_id}/events", tags=["Messages"])
async def thread_events(thread_id: UUID, user: User = Depends(current_user)) -> StreamingResponse:
    try:
        repository.get_thread(user_id=user.id, thread_id=thread_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Thread not found") from exc

    async def events():
        payload = {"type": "heartbeat", "thread_id": str(thread_id), "timestamp": utc_now().isoformat()}
        yield f"data: {json.dumps(payload)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/v1/threads/{thread_id}/runs", tags=["Runs"])
async def list_runs(
    thread_id: UUID,
    limit: int = Query(default=20, ge=1, le=200),
    status_filter: str | None = Query(default=None, alias="status"),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    try:
        repository.get_thread(user_id=user.id, thread_id=thread_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Thread not found") from exc
    return {"runs": repository.list_runs(thread_id=thread_id, limit=limit, status=status_filter), "page": Page()}


@app.get("/v1/runs/{run_id}", tags=["Runs"])
async def get_run(run_id: UUID, _: User = Depends(current_user)) -> Any:
    return await _service_get_json(settings.comps_service_url, f"/v1/runs/{run_id}")


@app.get("/v1/runs/{run_id}/table", tags=["Runs"])
async def get_table(run_id: UUID, _: User = Depends(current_user)) -> Any:
    return await _service_get_json(settings.comps_service_url, f"/v1/runs/{run_id}/table")


@app.get("/v1/runs/{run_id}/trace", tags=["Runs"])
async def get_trace(run_id: UUID, _: User = Depends(current_user)) -> Any:
    return await _service_get_json(settings.comps_service_url, f"/v1/runs/{run_id}/trace")


async def _proxy_export(path: str, media_type: str, filename: str) -> Response:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{settings.comps_service_url}{path}")
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Run export not found")
        response.raise_for_status()
        return Response(
            content=response.content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


@app.get("/v1/runs/{run_id}/export.csv", tags=["Exports"])
async def export_csv(run_id: UUID, _: User = Depends(current_user)) -> Response:
    return await _proxy_export(f"/v1/runs/{run_id}/export.csv", "text/csv", f"{run_id}.csv")


@app.get("/v1/runs/{run_id}/export.xlsx", tags=["Exports"])
async def export_xlsx(run_id: UUID, _: User = Depends(current_user)) -> Response:
    return await _proxy_export(
        f"/v1/runs/{run_id}/export.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        f"{run_id}.xlsx",
    )
