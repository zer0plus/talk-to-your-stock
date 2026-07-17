# Agent Service Chat Persistence

The Web BFF and Google ADK persist different views of a Thread:

- The Web BFF stores product-visible User and Assistant Messages plus optional
  Run linkage.
- The Agent Service uses one durable ADK session per `(app_name, user_id,
  thread_id)` for the complete semantic Agent context. ADK session events
  include User and Assistant content, Tool invocations, and Tool results.
- ADK's `DatabaseSessionService` owns its session/event tables in the shared
  PostgreSQL database. The Agent Service prepares that schema during application
  startup; readiness and Message requests never prepare database objects.
- The application database does not duplicate ADK logs, spans, hidden
  reasoning, or other observability-only events.
- No fixed Message-count truncation is applied. Any future context compaction
  requires an explicit design rather than silently dropping older events.

The current Agent route establishes and persists this ADK session context. Real
model routing and `generate_comps_table` execution remain separate work; when
the ADK Runner is wired, it must use the same User and Thread session identity.

## Persistence Flow

```mermaid
sequenceDiagram
    actor User
    participant BFF as Web BFF
    participant ProductDB as Product Message Store
    participant Agent as Agent Service
    participant Session as ADK Session Store
    participant Tool as generate_comps_table

    User->>BFF: "Compare AAPL with NVDA"
    BFF->>ProductDB: Persist User Message
    BFF->>Agent: user_id, thread_id, message_id, content
    Agent->>Session: Load or create session for User + Thread
    Agent->>Session: Append User Message
    rect rgb(245, 245, 245)
        Note over Agent,Tool: Future ADK Runner flow; not implemented by issue #18
        Agent->>Tool: Invoke with AAPL and NVDA
        Tool-->>Agent: Tool result
        Agent->>Session: Store invocation and result
        Agent->>Session: Store Assistant response
        Agent-->>BFF: Assistant response + optional Run
    end
    BFF->>ProductDB: Persist Assistant Message

    User->>BFF: "Now compare it to MSFT"
    BFF->>ProductDB: Persist User Message
    BFF->>Agent: Same user_id + thread_id, new Message
    Agent->>Session: Load complete prior event history
    rect rgb(245, 245, 245)
        Note over Agent,Session: Future Runner receives AAPL, NVDA,<br/>Tool invocation, and Tool result
        Agent-->>BFF: Context-aware response
    end
```

## Ownership Rules

1. The Web BFF persists the User Message before invoking the Agent Service.
2. `user_id` scopes ADK sessions to a User; the Thread UUID is the ADK session
   ID.
3. The Agent loads the complete session and appends the current User event
   before response processing.
4. The Agent appends the Assistant event after response processing. ADK Runner
   Tool events remain between those events in invocation order.
5. The Web BFF persists the returned Assistant Message and optional Run link.
