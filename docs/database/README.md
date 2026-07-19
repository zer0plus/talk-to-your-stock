# Database Schema Migrations

This document explains the Alembic migration implementation for TalkToYourStock.
One versioned migration chain manages the shared PostgreSQL database while table
names and repositories preserve service ownership. Migrations run before Web BFF
or Comps Service accepts traffic. Repository request paths perform data access
only and never create or alter database objects.

## What Alembic Does

Alembic is a database migration tool for Python projects using SQLAlchemy. It
treats database schema changes as ordered, versioned source files. Each revision
contains an `upgrade()` operation that advances the schema and may contain a
`downgrade()` operation that reverses it.

Alembic stores the database's current revision in an `alembic_version` table.
When `alembic upgrade head` runs, Alembic reads that revision, determines which
committed revisions are missing, applies them in order, and updates the recorded
version.

See the official [Alembic tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
for the command and revision model.

## Startup Sequence

Database preparation belongs to deployment or local-stack startup rather than
an HTTP request. The Web BFF should start only after PostgreSQL is healthy and
all required migrations have succeeded.

```mermaid
sequenceDiagram
    participant Compose as Docker Compose / Deployment
    participant DB as PostgreSQL
    participant Migration as Alembic migration job
    participant API as Web BFF

    Compose->>DB: Start PostgreSQL
    DB-->>Compose: Healthy

    Compose->>Migration: Run alembic upgrade head
    Migration->>DB: Read alembic_version
    DB-->>Migration: Current revision

    alt Missing migrations exist
        Migration->>DB: Apply missing schema changes
        Migration->>DB: Update alembic_version
    else Database already current
        Note over Migration,DB: No schema work needed
    end

    Migration-->>Compose: Success
    Compose->>API: Start Web BFF
    Note over API: Only starts after schema is ready
```

If migration execution fails, startup should stop rather than allow a service to
report ready against an incompatible database.

## Request Sequence

After startup has prepared the schema, repository operations perform only
application reads and writes. They do not issue `CREATE TABLE` or `CREATE INDEX`
statements.

```mermaid
sequenceDiagram
    actor User
    participant API as Web BFF
    participant Repo as Repository
    participant DB as PostgreSQL

    User->>API: POST /v1/threads
    API->>Repo: upsert_user()
    Repo->>DB: INSERT/UPDATE User
    DB-->>Repo: User row
    API->>Repo: create_thread()
    Repo->>DB: INSERT Thread
    DB-->>Repo: Thread row
    API-->>User: 201 response

    Note over API,DB: No CREATE TABLE or CREATE INDEX<br/>inside the request
```

This removes schema catalog inspection and DDL locking from user-facing request
latency.

## Local Docker Compose Flow

The local stack uses a one-shot migration service. PostgreSQL becomes
healthy, the migration job applies the current revision, and only then does the
Web BFF begin accepting requests.

```mermaid
flowchart TD
    P["PostgreSQL starts"] --> H{"Healthy?"}
    H -- No --> W["Wait"]
    W --> H
    H -- Yes --> M["database-migrate<br/>alembic upgrade head"]
    M --> S{"Migration succeeded?"}
    S -- No --> F["Stop: schema is invalid"]
    S -- Yes --> B["Start Web BFF + Comps Service"]
    B --> R["Accept requests"]
```

A separate one-shot service makes migration failure visible and keeps schema
preparation separate from the long-running Uvicorn process.

## Implementation Layout

The migration module lives at:

```text
web-bff/
  migrations/
    env.py
    versions/
      0001_create_web_bff_schema.py
      0002_create_comps_run_schema.py
alembic.ini
```

The implementation includes:

- Python dependencies: add Alembic and SQLAlchemy.
- Migration files: create service-owned Web BFF and Comps Service tables and
  indexes in one ordered database chain.
- Web BFF repository: remove `_schema_ready`, `_ensure_schema()`, and calls to it.
- Docker Compose: run one migration job before both schema-owning services.
- Migration tests: render the upgrade through Alembic's CLI, with an opt-in real
  PostgreSQL HTTP-boundary test.
- Readiness: fail clearly when the expected database schema is unavailable.
- Local development documentation: document migration creation and execution.

It does not change:

- HTTP routes or OpenAPI contracts.
- Authentication behavior.
- Agent Service calls.
- Message persistence order.
- Pydantic request and response schemas.
- Existing repository `SELECT`, `INSERT`, and `UPDATE` behavior.

## Manual Migrations for This Repository

The repository currently uses raw `psycopg` queries and Pydantic models rather
than SQLAlchemy ORM models. Introducing an ORM is not required to use Alembic.
Migration revisions can be written manually with Alembic operations such as
`op.create_table()`, `op.create_index()`, and `op.add_column()`.

Manual revisions are the preferred starting point here. Alembic autogeneration
depends on SQLAlchemy metadata, and generated migrations must still be reviewed
and corrected. See the official
[autogenerate documentation](https://alembic.sqlalchemy.org/en/latest/autogenerate.html).

## Existing Local Databases

`IS_LIVE` is currently false, so the clean transition for disposable local data
is:

1. Delete the existing local PostgreSQL volume.
2. Start PostgreSQL with an empty database.
3. Run `alembic upgrade head`.
4. Start the application services.

## Commands

With `DATABASE_URL` set, apply all pending migrations from the repository root:

```bash
python -m alembic upgrade head
```

Inspect migration state with:

```bash
python -m alembic current
python -m alembic history
```

Docker Compose runs `upgrade head` automatically through `database-migrate`.
Web BFF and Comps Service depend on that job completing successfully.

The real PostgreSQL migration tests are opt-in because they upgrade and then
downgrade dedicated disposable databases:

```bash
WEB_BFF_MIGRATION_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost/web_bff_migration_test \
PYTHONPATH=shared:web-bff \
python -m pytest -q tests/test_web_bff_migrations.py
```

```bash
COMPS_MIGRATION_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost/comps_migration_test \
PYTHONPATH=shared:comps-service \
python -m pytest -q comps-service/tests/test_comps_migrations.py
```

Never point this test at a database containing data that must be preserved.

## Future Schema Change Workflow

For every future schema change:

1. Add one revision file with the required upgrade operation.
2. Review the generated or handwritten migration.
3. Test upgrading a database from the previous revision.
4. Commit the migration with the application change that needs it.
5. Apply migrations before deploying the new application version.

User-facing requests must never be responsible for bringing the database schema
up to date.
