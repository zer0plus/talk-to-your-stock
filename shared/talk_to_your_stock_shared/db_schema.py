APP_SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT,
    avatar_url TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS threads (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    last_message_at TIMESTAMPTZ,
    latest_run_id UUID,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY,
    thread_id UUID NOT NULL REFERENCES threads(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    run_id UUID,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_thread_created ON messages(thread_id, created_at);

CREATE TABLE IF NOT EXISTS runs (
    id UUID PRIMARY KEY,
    thread_id UUID NOT NULL REFERENCES threads(id),
    trigger_message_id UUID NOT NULL,
    status TEXT NOT NULL,
    target_ticker TEXT NOT NULL,
    peer_tickers JSONB NOT NULL,
    currency TEXT NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_runs_thread_created ON runs(thread_id, created_at);

CREATE TABLE IF NOT EXISTS run_tables (
    run_id UUID PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    target_ticker TEXT NOT NULL,
    currency TEXT NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    rows JSONB NOT NULL,
    summary JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS run_traces (
    run_id UUID PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    formulas JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS fundamental_cache (
    symbol TEXT NOT NULL,
    statement_type TEXT NOT NULL,
    period_type TEXT NOT NULL,
    latest_fiscal_date DATE,
    payload_jsonb JSONB NOT NULL,
    source_hash TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    next_expected_refresh_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (symbol, statement_type, period_type)
);

CREATE INDEX IF NOT EXISTS idx_fundamental_cache_refresh
    ON fundamental_cache(next_expected_refresh_at);
"""
