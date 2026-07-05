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
   `dev/.env`. The example identity is intentionally local-only.

3. Start the services:

```bash
docker compose -f dev/docker-compose.yml up --build -d
```

4. Check readiness:

```bash
curl -i http://localhost:8000/v1/ready
curl -i http://localhost:8001/v1/ready
curl -i http://localhost:8002/v1/ready
```

Each readiness response uses the shared contract from `api/openapi.yaml` and
includes `configuration` and `database` checks. A failed required check returns
HTTP `503` with `status: "not_ready"`.

## Production Mode

Set `TALK_TO_YOUR_STOCK_ENV=production` for production-like readiness checks.
Production mode does not accept `DEV_AUTH_*` config. It requires:

- Web BFF: `MANAGED_AUTH_JWKS_URL`, `MANAGED_AUTH_ISSUER`,
  `MANAGED_AUTH_AUDIENCE`
- Agent Service: `GOOGLE_ADK_APP_NAME`, `GOOGLE_API_KEY`
- Comps Service: `ALPHA_VANTAGE_API_KEY`
- All services: `DATABASE_URL`

Missing production configuration fails readiness clearly. The local dev-auth
identity is not a production fallback.
