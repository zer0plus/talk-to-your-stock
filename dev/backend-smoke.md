# Canonical Backend Smoke

This manual smoke verifies the complete local Comps path with User-supplied Peer
Tickers using real Google ADK, Alpha Vantage, and FX behavior. It covers
PostgreSQL, Web BFF, Agent Service, Comps Service, persisted Run artifacts, and
ADK-native event history.

Run every command from the repository root in the same terminal. The smoke is
opt-in because it consumes live model and provider quota.

## Scope

This smoke verifies:

- all three backend services are ready against PostgreSQL;
- real Alpha Vantage evidence can be normalized with an explicit FX rate;
- a Message sent through Web BFF produces a persisted Run, Comps Table, and
  Trace through the real Agent and Comps service boundaries; and
- the ADK session contains the Tool call and Tool result for the same product
  Message and Run identifiers.

It does not verify Fundamental Cache, Redis workers or progress streaming,
CSV/XLSX exports, frontend UI, broad conversational behavior, auto peer
selection, production authentication, or public deployment.

## Prerequisites

Install Docker with Compose, `curl`, and `jq`. Create the local environment:

```bash
cp dev/.env.example dev/.env
```

Set these values in `dev/.env`:

| Variable | Purpose |
| --- | --- |
| `TALK_TO_YOUR_STOCK_ENV=local` | Selects the local-only runtime |
| `DEV_AUTH_USER_ID` | Deterministic local User ID for product-state ownership |
| `DEV_AUTH_EMAIL` | Deterministic local User email |
| `GOOGLE_API_KEY` | Real Google ADK model access |
| `COMPS_SERVICE_INTERNAL_TOKEN` | Local Agent-to-Comps Service Credential |
| `ALPHA_VANTAGE_API_KEY` | Real provider and FX access |

`DEV_AUTH_USER_ID` and `DEV_AUTH_EMAIL` are required configuration, but they do
not authenticate the operator. Local requests use no login, Authorization
header, User token, or credential verification. Keep the managed-auth variables
empty for this local smoke. Set `ALPHA_VANTAGE_QUOTE_ENTITLEMENT` only when the
provider key has the named `realtime` or `delayed` entitlement.

The canonical Message path can make multiple Alpha Vantage requests per company.
Use a provider plan with enough remaining quota and keep the configured request
interval appropriate for that plan.

## 1. Start The Stack

```bash
docker compose --env-file dev/.env -f dev/docker-compose.yml up --build -d
docker compose --env-file dev/.env -f dev/docker-compose.yml ps
```

Compose starts PostgreSQL, applies the Alembic migrations, and starts Web BFF,
Agent Service, and Comps Service.

## 2. Verify Readiness

```bash
for port in 8000 8001 8002; do
  readiness="$(curl --fail --silent --show-error "http://127.0.0.1:${port}/v1/ready")"
  jq -e '.status == "ready" and ([.checks[].status] | all(. == "ok"))' <<<"$readiness"
  jq '{service, status, checks}' <<<"$readiness"
done
```

Every request must return HTTP `200`, `status: "ready"`, and only `ok` checks.
A `503`, failed assertion, missing credential, stale migration, or unavailable
service means the smoke has failed and should not continue.

## 3. Verify Real Provider And FX Behavior

This check loads IBM provider evidence in USD and normalizes it into CAD,
forcing an explicit `CURRENCY_EXCHANGE_RATE` request. It uses the credential
already supplied to the running Comps Service and does not persist a Run.

```bash
docker compose --env-file dev/.env -f dev/docker-compose.yml exec -T comps-service \
python - <<'PY'
from comps_service.provider import AlphaVantageCompanyDataSource
from comps_service.tool_validation import AlphaVantageTickerValidator

validated_ticker_matches = {}
validator = AlphaVantageTickerValidator(
    validated_ticker_matches=validated_ticker_matches,
)
assert validator.is_supported("IBM")

loaded = AlphaVantageCompanyDataSource(
    validated_ticker_matches=validated_ticker_matches,
).load(
    tickers=["IBM"],
    currency="CAD",
)
company = loaded.companies[0]
evidence = loaded.raw_provider_evidence["IBM"]
fx = evidence["currency_exchange_rates"]["USD_CAD"][
    "Realtime Currency Exchange Rate"
]

assert company.ticker == "IBM"
assert company.currency == "CAD"
assert evidence["symbol_search"]["1. symbol"] == "IBM"
assert evidence["symbol_search"]["8. currency"] == "USD"
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

A provider informational or rate-limit response, missing evidence, mismatched
currency, or unusable rate must fail. There is no fixture, stale-value, or 1:1
FX fallback in this runtime path.

## 4. Send A Canonical Message

These requests intentionally contain no Authorization header.

```bash
SMOKE_BASE_URL="http://127.0.0.1:8000"

SMOKE_THREAD_JSON="$(
  curl --fail --silent --show-error \
    --request POST \
    --header 'Content-Type: application/json' \
    --data '{"title":"Canonical backend smoke"}' \
    "${SMOKE_BASE_URL}/v1/threads"
)"
export SMOKE_THREAD_ID="$(jq -er '.thread.id' <<<"$SMOKE_THREAD_JSON")"
export SMOKE_USER_ID="$(jq -er '.thread.user_id' <<<"$SMOKE_THREAD_JSON")"
SMOKE_CONFIGURED_USER_ID="$(
  docker compose --env-file dev/.env -f dev/docker-compose.yml exec -T \
    web-bff printenv DEV_AUTH_USER_ID
)"
SMOKE_CONFIGURED_EMAIL="$(
  docker compose --env-file dev/.env -f dev/docker-compose.yml exec -T \
    web-bff printenv DEV_AUTH_EMAIL
)"
SMOKE_IDENTITY_JSON="$(
  curl --fail --silent --show-error "${SMOKE_BASE_URL}/v1/me"
)"

jq -e \
  --arg user_id "$SMOKE_CONFIGURED_USER_ID" \
  --arg email "$SMOKE_CONFIGURED_EMAIL" \
  '.user.id == $user_id and .user.email == $email' \
  <<<"$SMOKE_IDENTITY_JSON"
test "$SMOKE_USER_ID" = "$SMOKE_CONFIGURED_USER_ID"

SMOKE_MESSAGE_JSON="$(
  curl --fail --silent --show-error \
    --request POST \
    --header 'Content-Type: application/json' \
    --data '{"content":"Generate a USD comps table comparing IBM with MSFT."}' \
    "${SMOKE_BASE_URL}/v1/threads/${SMOKE_THREAD_ID}/messages"
)"
export SMOKE_MESSAGE_ID="$(jq -er '.user_message.id' <<<"$SMOKE_MESSAGE_JSON")"
export SMOKE_RUN_ID="$(jq -er '.run.id' <<<"$SMOKE_MESSAGE_JSON")"

jq -e \
  --arg message_id "$SMOKE_MESSAGE_ID" \
  --arg run_id "$SMOKE_RUN_ID" \
  '
    .user_message.role == "user"
    and .assistant_message.role == "assistant"
    and .assistant_message.run_id == $run_id
    and .run.id == $run_id
    and .run.trigger_message_id == $message_id
    and .run.status == "succeeded"
    and .run.target_ticker == "IBM"
    and .run.peer_tickers == ["MSFT"]
  ' <<<"$SMOKE_MESSAGE_JSON"
jq '{user_message, assistant_message, run}' <<<"$SMOKE_MESSAGE_JSON"
```

The Thread and `/v1/me` responses must identify the User configured through
`DEV_AUTH_USER_ID` and `DEV_AUTH_EMAIL`. The Message response must link both the
User Message and Assistant Message to one succeeded Run for IBM and MSFT.

## 5. Read Back The Run, Comps Table, And Trace

```bash
SMOKE_RUN_JSON="$(
  curl --fail --silent --show-error \
    "${SMOKE_BASE_URL}/v1/runs/${SMOKE_RUN_ID}"
)"
SMOKE_TABLE_JSON="$(
  curl --fail --silent --show-error \
    "${SMOKE_BASE_URL}/v1/runs/${SMOKE_RUN_ID}/table"
)"
SMOKE_TRACE_JSON="$(
  curl --fail --silent --show-error \
    "${SMOKE_BASE_URL}/v1/runs/${SMOKE_RUN_ID}/trace"
)"

jq -e --arg run_id "$SMOKE_RUN_ID" \
  '.run.id == $run_id and .run.status == "succeeded"' \
  <<<"$SMOKE_RUN_JSON"
jq -e --arg run_id "$SMOKE_RUN_ID" \
  '
    .run_id == $run_id
    and .target_ticker == "IBM"
    and ([.rows[].ticker] | sort) == (["IBM", "MSFT"] | sort)
    and (.rows | length) == 2
  ' <<<"$SMOKE_TABLE_JSON"
jq -e --arg run_id "$SMOKE_RUN_ID" \
  '.run_id == $run_id and (.formulas | length) > 0' \
  <<<"$SMOKE_TRACE_JSON"

jq '{run: .run}' <<<"$SMOKE_RUN_JSON"
jq '{run_id, target_ticker, currency, as_of, rows, summary}' <<<"$SMOKE_TABLE_JSON"
jq '{run_id, formula_count: (.formulas | length), first_formula: .formulas[0]}' \
  <<<"$SMOKE_TRACE_JSON"
```

All three readbacks go through Web BFF. Web BFF calls Comps Service for the
Comps-owned artifacts rather than reading its tables directly.

## 6. Inspect ADK-Native Tool Events

The Agent uses the deterministic local User ID as the ADK User ID, the product
Thread ID as the ADK session ID, and the triggering Message ID as the ADK
invocation ID. This command loads that session through ADK's
`DatabaseSessionService`; it does not query or duplicate ADK tables directly.

```bash
docker compose --env-file dev/.env -f dev/docker-compose.yml exec -T \
  -e SMOKE_USER_ID \
  -e SMOKE_THREAD_ID \
  -e SMOKE_MESSAGE_ID \
  -e SMOKE_RUN_ID \
  agent-service python - <<'PY'
import asyncio
import os
from uuid import UUID

from agent_service.session_context import AdkSessionContext


async def inspect() -> None:
    context = AdkSessionContext.from_env()
    await context.prepare()
    try:
        session = await context.get_session(
            user_id=UUID(os.environ["SMOKE_USER_ID"]),
            thread_id=UUID(os.environ["SMOKE_THREAD_ID"]),
        )
        assert session is not None

        invocation_id = os.environ["SMOKE_MESSAGE_ID"]
        run_id = os.environ["SMOKE_RUN_ID"]
        tool_calls = []
        tool_results = []

        for event in session.events:
            if event.invocation_id != invocation_id:
                continue
            for part in event.content.parts:
                if (
                    part.function_call is not None
                    and part.function_call.name == "generate_comps_table"
                ):
                    tool_calls.append(part.function_call)
                if (
                    part.function_response is not None
                    and part.function_response.name == "generate_comps_table"
                ):
                    tool_results.append(part.function_response)

        assert len(tool_calls) == 1
        assert len(tool_results) == 1
        assert tool_calls[0].args["target_ticker"] == "IBM"
        assert tool_calls[0].args["peer_tickers"] == ["MSFT"]
        assert tool_results[0].response["run"]["id"] == run_id

        print(
            {
                "adk_user_id": session.user_id,
                "adk_session_id": session.id,
                "invocation_id": invocation_id,
                "tool": tool_calls[0].name,
                "run_id": tool_results[0].response["run"]["id"],
            }
        )
    finally:
        await context.close()


asyncio.run(inspect())
PY
```

The printed invocation ID must equal `SMOKE_MESSAGE_ID`, and the Tool result Run
ID must equal `SMOKE_RUN_ID`. This demonstrates correlation without adding a
second application-owned Agent event store.

## 7. Finish Or Troubleshoot

Inspect service logs when any step fails:

```bash
docker compose --env-file dev/.env -f dev/docker-compose.yml logs \
  web-bff agent-service comps-service
```

Stop the stack without deleting the PostgreSQL volume:

```bash
docker compose --env-file dev/.env -f dev/docker-compose.yml down
```

The smoke passes only when readiness, real provider/FX normalization, the
canonical Message, all three artifact readbacks, and ADK correlation all pass.
