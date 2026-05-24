# TalkToYourStock

This repo is currently in system-design and API-contract phase.

## Serve OpenAPI docs locally

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

4. Start the server:

```bash
python -m uvicorn app.main:app --reload --port 8000
```

5. Open docs:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Raw OpenAPI JSON: http://localhost:8000/openapi.json

6. When done:

```bash
deactivate
```

## Source of truth
- OpenAPI contract: `api/openapi.yaml`
- Architecture decision record: `docs/adr/ADR-001-mvp-high-level-architecture.md`
- Fundamental data caching ADR: `docs/adr/ADR-002-fundamental-data-caching-strategy.md`

## Design Decisions
- API style: REST for request/response endpoints, plus SSE for real-time chat/run updates.
- Run model: a run is created only for table-generation comps requests; non-comps chat replies do not create a run.
- Auditability: comps outputs are traceable via run-level formula/input trace endpoints.
- Auth boundary: bearer JWT for user-facing endpoints; internal tool endpoint is service-only.
