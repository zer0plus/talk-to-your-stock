# TalkToYourStock

This repo is currently in system-design and staged implementation phase. The
first implementation slice establishes the ADR-defined service folders,
OpenAPI contract, shared schema types, health endpoints, and local environment
examples. Product behavior is intentionally not implemented in this slice.

## Source of Truth

- OpenAPI contract: `api/openapi.yaml`
- High-level architecture ADR: `docs/adr/ADR-001-mvp-high-level-architecture.md`
- Fundamental data caching ADR: `docs/adr/ADR-002-fundamental-data-caching-strategy.md`
- Agent architecture ADR: `docs/adr/ADR-003-agent-architecture.md`
- Agent implementation rules: `AGENTS.md`

## Repository Layout

```text
web-bff/          # User-facing FastAPI BFF and auth boundary
agent-service/    # Agent orchestration boundary, MVP fundamental agent home
comps-service/    # Deterministic comps capability and internal exports/ module
shared/           # Small cross-service contracts, enums, IDs, schemas
dev/              # Local development setup, added in a later implementation slice
api/              # OpenAPI source of truth
docs/adr/         # Binding architecture decisions
```

## Local Python Venv

1. Create a repo-local virtual environment:

```bash
python3 -m venv .venv
```

2. Activate the virtual environment:

```bash
source .venv/bin/activate
```

3. Install dependencies into the virtual environment:

```bash
PIP_USER=0 python -m pip install --upgrade pip
PIP_USER=0 python -m pip install --no-user -r requirements.txt
```

4. Start one or more service skeletons:

Web BFF:

```bash
PYTHONPATH=shared:web-bff python -m uvicorn web_bff.main:app --reload --port 8000
```

Agent Service:

```bash
PYTHONPATH=shared:agent-service python -m uvicorn agent_service.main:app --reload --port 8001
```

Comps Service:

```bash
PYTHONPATH=shared:comps-service python -m uvicorn comps_service.main:app --reload --port 8002
```

5. Check health endpoints:

```bash
curl http://localhost:8000/v1/health
curl http://localhost:8001/v1/health
curl http://localhost:8002/v1/health
```

6. Open Web BFF skeleton docs:

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Generated service OpenAPI JSON: http://localhost:8000/openapi.json

7. When done:

```bash
deactivate
```

## Design Decisions

- API style: REST for request/response endpoints, plus SSE for real-time chat/run updates.
- Run model: a run is created only for table-generation comps requests; non-comps chat replies do not create a run.
- Auditability: comps outputs are traceable via run-level formula/input trace endpoints.
- Auth boundary: Google OAuth is the intended auth model; Web BFF verifies user-facing credentials.
- Agent orchestration: Google ADK owns orchestration behavior and calls deterministic tools/services.
- Export ownership: MVP CSV/XLSX exports live inside `comps-service/exports/`.
