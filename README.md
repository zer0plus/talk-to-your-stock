# TalkToYourStock

TalkToYourStock is a chat-first stock analysis app. The MVP focuses on one core flow: a user asks for trading comps, the agent triggers a deterministic comps tool, and the app returns a visual/downloadable table.

## Source Of Truth

- OpenAPI contract: `api/openapi.yaml`
- High-level architecture ADR: `docs/adr/ADR-001-mvp-high-level-architecture.md`
- Fundamental data caching ADR: `docs/adr/ADR-002-fundamental-data-caching-strategy.md`
- Agent architecture ADR: `docs/adr/ADR-003-agent-architecture.md`
- Agent implementation rules: `AGENTS.md`

## Repository Layout

```text
web-bff/          # User-facing FastAPI BFF, docs, auth boundary, chat API
agent-service/    # Agent orchestration boundary, MVP fundamental agent
comps-service/    # Deterministic comps generation and internal exports/ module
shared/           # Tiny cross-service contracts, enums, IDs, schemas
infra/            # Local infrastructure such as Docker Compose
api/              # OpenAPI source of truth
docs/adr/         # Architecture decisions
```

## Local Backend With Docker

Set Gemini credentials first. For local development with Google AI Studio:

```bash
export GOOGLE_GENAI_USE_VERTEXAI=FALSE
export GOOGLE_API_KEY="your_google_ai_studio_api_key"
export GEMINI_MODEL=gemini-3.1-flash-lite
export ALPHA_VANTAGE_API_KEY="your_alpha_vantage_api_key"
# Leave empty for free end-of-day GLOBAL_QUOTE. Premium can use delayed or realtime.
export ALPHA_VANTAGE_QUOTE_ENTITLEMENT=""
# Alpha Vantage free tier allows roughly 1 request per second.
export ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS=1.1
```

Start the full local backend stack:

```bash
docker compose -p talk-to-your-stock -f infra/docker-compose.yml up --build
```

Services:

- Web BFF: http://localhost:8000
- Agent Service: http://localhost:8001
- Comps Service: http://localhost:8002
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

Stop the stack:

```bash
docker compose -p talk-to-your-stock -f infra/docker-compose.yml down
```

Delete local Postgres data if you want a clean DB:

```bash
docker compose -p talk-to-your-stock -f infra/docker-compose.yml down -v
```

## Local Python Venv

Use this when running services outside Docker.

```bash
python3 -m venv .venv
source .venv/bin/activate
PIP_USER=0 python -m pip install --upgrade pip
PIP_USER=0 python -m pip install --no-user -r requirements.txt
```

Start only Postgres and Redis:

```bash
docker compose -p talk-to-your-stock -f infra/docker-compose.yml up postgres redis
```

Run services in separate terminals:

```bash
source .venv/bin/activate
PYTHONPATH=shared:comps-service python -m uvicorn comps_service.main:app --reload --port 8002
```

```bash
source .venv/bin/activate
PYTHONPATH=shared:agent-service COMPS_SERVICE_URL=http://localhost:8002 \
  python -m uvicorn agent_service.main:app --reload --port 8001
```

```bash
source .venv/bin/activate
PYTHONPATH=shared:web-bff AGENT_SERVICE_URL=http://localhost:8001 COMPS_SERVICE_URL=http://localhost:8002 \
  python -m uvicorn web_bff.main:app --reload --port 8000
```

When done:

```bash
deactivate
```

## Smoke Test

Create a thread:

```bash
curl -s -X POST http://localhost:8000/v1/threads \
  -H 'Content-Type: application/json' \
  -d '{"title":"Demo comps"}'
```

Send a comps message using the returned `thread.id`:

```bash
curl -s -X POST http://localhost:8000/v1/threads/$THREAD_ID/messages \
  -H 'Content-Type: application/json' \
  -d '{"content":"Give me comps for GOOG, NVDA, TSLA, AMD"}'
```

Send a non-comps conversational message:

```bash
curl -s -X POST http://localhost:8000/v1/threads/$THREAD_ID/messages \
  -H 'Content-Type: application/json' \
  -d '{"content":"What is the ticker for Tesla?"}'
```

Expected behavior:

- Comps messages return an assistant message plus a non-null `run`.
- Conversational ticker questions return an assistant message with `run: null`.
- Generated run tables are available at `/v1/runs/{run_id}/table`.
- CSV/XLSX downloads are available at `/v1/runs/{run_id}/export.csv` and `/v1/runs/{run_id}/export.xlsx`.

## Design Decisions

- API style: REST for request/response endpoints, plus SSE for real-time chat/run updates.
- Run model: a run is created only for table-generation comps requests; non-comps chat replies do not create a run.
- Auditability: comps outputs are traceable via run-level formula/input trace endpoints.
- Auth boundary: Google OAuth is the intended auth model; local skeleton uses a demo user header until auth is implemented.
- Agent orchestration: `agent-service` uses Google ADK with Gemini and fails startup if Gemini credentials are missing.
- Export ownership: MVP CSV/XLSX exports live inside `comps-service/exports/`.
