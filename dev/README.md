# Development Setup

The local backend stack starts PostgreSQL plus the three ADR-defined service
boundaries:

- Web BFF: http://localhost:8000
- Agent Service: http://localhost:8001
- Comps Service: http://localhost:8002

## Local-Only Network Boundary

Compose publishes PostgreSQL and all three backend services only on the host's
IPv4 loopback interface (`127.0.0.1`). The services still listen on `0.0.0.0`
inside their containers so they can communicate over the private Compose
network, but other machines cannot reach the published host ports.

Keep the loopback-qualified port mappings in `dev/docker-compose.yml` until the
public ingress and production-grade service identity controls required by
ADR-001 are implemented and validated. The shared Agent-to-Comps Service
Credential is local defense in depth, not a public deployment boundary.

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

Compose waits for PostgreSQL, runs the one-shot `database-migrate` service, and
starts Web BFF and Comps Service only after `python -m alembic upgrade head`
succeeds.

5. Check readiness:

```bash
curl -i http://localhost:8000/v1/ready
curl -i http://localhost:8001/v1/ready
curl -i http://localhost:8002/v1/ready
```

Each readiness response uses the shared contract from `api/openapi.yaml` and
includes `configuration`, `database`, and relevant service capability checks. A
failed required check returns HTTP `503` with `status: "not_ready"`.

Agent Service startup prepares the ADK-owned session/event tables used to retain
complete Agent and Tool event history for each User and Thread. Readiness
includes `agent_session` to verify that store without preparing database objects.
Comps Service readiness reports `run_data_source: ok` when its real provider and
FX adapter is installed; configuration readiness independently fails when the
Alpha Vantage API key is missing. Agent Service readiness checks the configured
Comps Service and propagates any failure through `agent_routing`.
Configuration readiness requires `GOOGLE_API_KEY`, `COMPS_SERVICE_URL`, and
`COMPS_SERVICE_INTERNAL_TOKEN` in local and production modes; production also
requires `GOOGLE_ADK_APP_NAME`. Production readiness intentionally fails
`agent_routing` because public deployment controls remain deferred.

Web BFF and Comps Service database readiness also require the current Alembic
schema revision. Missing or stale migrations keep either schema owner not ready.

## Real Provider And FX Smoke Check

This opt-in check calls current Alpha Vantage payloads with your real credential.
It requests IBM provider evidence in USD and normalizes it into CAD, which forces
the explicit `CURRENCY_EXCHANGE_RATE` path. It does not use the database or
persist a Run. The check makes five provider calls, so account for your plan's
request quota.

```bash
export ALPHA_VANTAGE_API_KEY="your-real-key"
export ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS="1.1"
PYTHONPATH=shared:comps-service python - <<'PY'
from comps_service.provider import AlphaVantageCompanyDataSource

loaded = AlphaVantageCompanyDataSource().load(
    tickers=["IBM"],
    currency="CAD",
)
company = loaded.companies[0]
evidence = loaded.raw_provider_evidence["IBM"]
fx = evidence["currency_exchange_rate"]["Realtime Currency Exchange Rate"]

assert company.ticker == "IBM"
assert company.currency == "CAD"
assert evidence["overview"]["Currency"] == "USD"
assert fx["1. From_Currency Code"] == "USD"
assert fx["3. To_Currency Code"] == "CAD"
assert float(fx["5. Exchange Rate"]) > 0
assert all(
    "currency_exchange_rate.USD_CAD" in source
    for field, source in company.sources.items()
    if field != "shares_outstanding"
)

print(
    {
        "ticker": company.ticker,
        "table_currency": company.currency,
        "as_of": company.as_of.isoformat(),
        "fx_last_refreshed": fx["6. Last Refreshed"],
    }
)
PY
```

Set `ALPHA_VANTAGE_QUOTE_ENTITLEMENT=realtime` or `delayed` only when the API
key has that entitlement. A missing field, provider informational/rate-limit
payload, mismatched Ticker/currency, or unusable FX rate makes the check fail;
there is no fixture, stale-value, or 1:1 FX fallback in this runtime path.

## Production Mode

Set `TALK_TO_YOUR_STOCK_ENV=production` for production-like readiness checks.
Production mode does not accept `DEV_AUTH_*` config. It requires:

- Web BFF: `MANAGED_AUTH_JWKS_URL`, `MANAGED_AUTH_ISSUER`,
  `MANAGED_AUTH_AUDIENCE`
- Agent Service: `GOOGLE_ADK_APP_NAME`, `GOOGLE_API_KEY`, `COMPS_SERVICE_URL`,
  `COMPS_SERVICE_INTERNAL_TOKEN`
- Comps Service: `ALPHA_VANTAGE_API_KEY`, `COMPS_SERVICE_INTERNAL_TOKEN`
- All services: `DATABASE_URL`

Missing production configuration fails readiness clearly. Even with all listed
configuration, Agent routing remains not ready in production until public
deployment controls exist. The local dev-auth identity is not a production
fallback.
