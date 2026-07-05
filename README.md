# TalkToYourStock

This repo is currently in system-design and staged implementation phase. The
current implementation establishes the ADR-defined service folders, OpenAPI
contract, shared schema types, health/readiness endpoints, and local backend
stack. Product behavior is intentionally added through narrow implementation
slices.

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
dev/              # Local Docker Compose stack and environment examples
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

For readiness checks, set the local environment and database URL first:

```bash
export TALK_TO_YOUR_STOCK_ENV=local
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/talk_to_your_stock
export DEV_AUTH_USER_ID=00000000-0000-0000-0000-000000000001
export DEV_AUTH_EMAIL=dev@example.com
```

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

6. Check readiness endpoints:

```bash
curl -i http://localhost:8000/v1/ready
curl -i http://localhost:8001/v1/ready
curl -i http://localhost:8002/v1/ready
```

7. Open Web BFF skeleton docs:

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Generated service OpenAPI JSON: http://localhost:8000/openapi.json

8. When done:

```bash
deactivate
```

## Local Docker Stack

The Docker Compose stack starts PostgreSQL plus Web BFF, Agent Service, and
Comps Service:

```bash
cp dev/.env.example dev/.env
docker compose -f dev/docker-compose.yml up --build -d
```

See `dev/README.md` for the readiness contract and required local/production
environment configuration.

## Design Decisions

TBD
