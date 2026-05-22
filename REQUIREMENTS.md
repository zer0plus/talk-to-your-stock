# TalkToYourStock MVP Requirements

## Functional Requirements (MUST)
- Secure user sign-in.
- Users can create, view, and continue chat threads.
- Chat messages are persisted per thread (user + agent messages).
- Agent supports one production tool intent only: `generate_comps_table`.
- User can request comps in natural language (example: "GOOG, NVDA, TSLA, AMD").
- System extracts and validates ticker list from chat input.
- System fetches required market and fundamental data (Alpha Vantage for MVP).
- System computes deterministic comps metrics: `Equity Value`, `Enterprise Value`, `Net Debt`, `EV/Revenue`, `EV/EBITDA`, `P/E`.
- System returns an in-app visual comps table in the chat flow.
- System allows table download as `CSV` and `XLSX`.
- Each comps run is stored with inputs, outputs, timestamp, and source snapshot reference.
- Past runs are retrievable from the related thread.

## Non-Functional Requirements (MUST)
- Deterministic and auditable calculations (formula and source field traceability).
- Strict tenant isolation (users only access their own threads and runs).
- Secure secret handling (no plaintext API keys in code/DB; use secret manager or protected env vars).
- Encrypted transport (`HTTPS`) and encrypted data at rest.
- Reliable external fetch behavior (timeouts, retries, and clear failure messages).
- Interactive performance suitable for chat workflow.
- Basic observability (structured logs and error monitoring for chat/tool/data/export flows).
- Data freshness policy for quotes/fundamentals with visible "as of" timestamps.
- Basic rate limiting and abuse protection to protect provider quotas and app stability.
