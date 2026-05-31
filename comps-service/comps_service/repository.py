from __future__ import annotations

from uuid import UUID

from psycopg.types.json import Jsonb

from comps_service.db import connect
from talk_to_your_stock_shared import CompsTable, Run, TraceResponse


class RunNotFoundError(Exception):
    pass


class CompsRepository:
    def save_run_result(self, *, run: Run, table: CompsTable, trace: TraceResponse) -> None:
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    id, thread_id, trigger_message_id, status, target_ticker, peer_tickers,
                    currency, as_of, warnings, error_message, created_at, started_at, completed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    warnings = EXCLUDED.warnings,
                    error_message = EXCLUDED.error_message,
                    completed_at = EXCLUDED.completed_at
                """,
                (
                    run.id,
                    run.thread_id,
                    run.trigger_message_id,
                    run.status.value,
                    run.target_ticker,
                    Jsonb(run.peer_tickers),
                    run.currency,
                    run.as_of,
                    Jsonb(run.warnings),
                    run.error_message,
                    run.created_at,
                    run.started_at,
                    run.completed_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO run_tables (run_id, target_ticker, currency, as_of, rows, summary)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    rows = EXCLUDED.rows,
                    summary = EXCLUDED.summary
                """,
                (
                    table.run_id,
                    table.target_ticker,
                    table.currency,
                    table.as_of,
                    Jsonb([row.model_dump(mode="json") for row in table.rows]),
                    Jsonb(table.summary.model_dump(mode="json")),
                ),
            )
            conn.execute(
                """
                INSERT INTO run_traces (run_id, formulas)
                VALUES (%s, %s)
                ON CONFLICT (run_id) DO UPDATE SET formulas = EXCLUDED.formulas
                """,
                (trace.run_id, Jsonb([formula.model_dump(mode="json") for formula in trace.formulas])),
            )
            conn.execute(
                "UPDATE threads SET latest_run_id = %s, updated_at = %s WHERE id = %s",
                (run.id, run.completed_at or run.created_at, run.thread_id),
            )
            conn.commit()

    def get_run(self, run_id: UUID) -> Run:
        with connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = %s", (run_id,)).fetchone()
        if row is None:
            raise RunNotFoundError(str(run_id))
        return Run(**row)

    def get_table(self, run_id: UUID) -> CompsTable:
        with connect() as conn:
            row = conn.execute("SELECT * FROM run_tables WHERE run_id = %s", (run_id,)).fetchone()
        if row is None:
            raise RunNotFoundError(str(run_id))
        return CompsTable(
            run_id=row["run_id"],
            target_ticker=row["target_ticker"],
            currency=row["currency"],
            as_of=row["as_of"],
            rows=row["rows"],
            summary=row["summary"],
        )

    def get_trace(self, run_id: UUID) -> TraceResponse:
        with connect() as conn:
            row = conn.execute("SELECT * FROM run_traces WHERE run_id = %s", (run_id,)).fetchone()
        if row is None:
            raise RunNotFoundError(str(run_id))
        return TraceResponse(run_id=row["run_id"], formulas=row["formulas"])
