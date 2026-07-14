# Development Setup

The local backend stack starts PostgreSQL plus the three ADR-defined service
boundaries:

- Web BFF: http://localhost:8000
- Agent Service: http://localhost:8001
- Comps Service: http://localhost:8002

## Start The Stack

1. Create a local env file:

```bash
cp dev/.env.example dev/.env
```

2. Keep `TALK_TO_YOUR_STOCK_ENV=local` and set an explicit dev-auth identity in
   `dev/.env`. The example identity and `COMPS_SERVICE_INTERNAL_TOKEN` are
   intentionally local-only.

3. Set `ALPHA_VANTAGE_API_KEY` in `dev/.env`. Comps Service readiness requires
   a real provider key because the current tool validation path uses Alpha
   Vantage. `TEST_ALPHA_VANTAGE_API_KEY` is only used when
   `RUN_LIVE_ALPHA_VANTAGE_TESTS=1` for opt-in live tests.

4. Start the services:

```bash
docker compose -f dev/docker-compose.yml up --build -d
```

Compose waits for PostgreSQL, runs the one-shot `web-bff-migrate` service, and
starts the Web BFF only after `python -m alembic upgrade head` succeeds.

5. Check readiness:

```bash
curl -i http://localhost:8000/v1/ready
curl -i http://localhost:8001/v1/ready
curl -i http://localhost:8002/v1/ready
```

Each readiness response uses the shared contract from `api/openapi.yaml` and
includes `configuration` and `database` checks. A failed required check returns
HTTP `503` with `status: "not_ready"`.

Agent Service readiness also includes `agent_session`. It prepares and verifies
the ADK-owned session/event tables used to retain complete Agent and Tool event
history for each User and Thread.

Web BFF database readiness also requires the current Alembic schema revision.
Missing or stale migrations keep the Web BFF not ready.

## Production Mode

Set `TALK_TO_YOUR_STOCK_ENV=production` for production-like readiness checks.
Production mode does not accept `DEV_AUTH_*` config. It requires:

- Web BFF: `MANAGED_AUTH_JWKS_URL`, `MANAGED_AUTH_ISSUER`,
  `MANAGED_AUTH_AUDIENCE`
- Agent Service: `GOOGLE_ADK_APP_NAME`, `GOOGLE_API_KEY`, `COMPS_SERVICE_URL`,
  `COMPS_SERVICE_INTERNAL_TOKEN`
- Comps Service: `ALPHA_VANTAGE_API_KEY`, `COMPS_SERVICE_INTERNAL_TOKEN`
- All services: `DATABASE_URL`

Missing production configuration fails readiness clearly. The local dev-auth
identity is not a production fallback.
