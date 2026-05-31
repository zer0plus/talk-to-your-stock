from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from agent_service.fundamental_agent.runner import FundamentalAgentRunner
from agent_service.settings import settings
from talk_to_your_stock_shared import AgentRequest, AgentResponse, Readiness
from talk_to_your_stock_shared.time import utc_now

runner: FundamentalAgentRunner | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global runner
    settings.validate_gemini_credentials()
    runner = FundamentalAgentRunner()
    yield


app = FastAPI(title="TalkToYourStock Agent Service", version="0.1.0", lifespan=lifespan)


@app.get("/v1/health", tags=["Health"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "agent-service", "time": utc_now().isoformat()}


@app.get("/v1/ready", tags=["Health"], response_model=Readiness)
def ready() -> Readiness:
    return Readiness(
        status="ready",
        checks={"agent": "ok", "gemini": "ok", "comps_service": "ok"},
        time=utc_now(),
    )


@app.post("/v1/agent/respond", response_model=AgentResponse)
async def respond(request: AgentRequest) -> AgentResponse:
    if runner is None:
        raise RuntimeError("Agent runner was not initialized.")
    return await runner.respond(request)
