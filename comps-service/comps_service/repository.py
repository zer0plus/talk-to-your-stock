from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import NoReturn
from uuid import UUID

from talk_to_your_stock_shared import Run, RunTableResponse, TraceResponse
from talk_to_your_stock_shared.readiness import DATABASE_URL_VAR
from talk_to_your_stock_shared.time import utc_now

from .artifacts import SourceSnapshot
from .run_service import DuplicateToolInvocation


logger = logging.getLogger(__name__)
RUN_TRIGGER_MESSAGE_LINKAGE_CONSTRAINT = "comps_runs_trigger_message_linkage_fk"
RUN_INVOCATION_ID_UNIQUE_CONSTRAINT = "comps_runs_invocation_id_unique"


class CompsPersistenceUnavailable(RuntimeError):
    pass


class InvalidRunLinkage(ValueError):
    pass


class PostgresCompsRunRepository:
    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url.strip()

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> PostgresCompsRunRepository:
        env = os.environ if environ is None else environ
        return cls(database_url=env.get(DATABASE_URL_VAR, ""))

    def save_succeeded_run(
        self,
        *,
        invocation_id: UUID,
        run: Run,
        table: RunTableResponse,
        trace: TraceResponse,
        source_snapshot: SourceSnapshot,
    ) -> None:
        from psycopg.types.json import Jsonb

        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        insert into comps_runs (
                            id, invocation_id, thread_id, trigger_message_id, status,
                            target_ticker, peer_tickers, currency, as_of, warnings,
                            error_message, created_at, started_at, completed_at
                        )
                        values (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            run.id,
                            invocation_id,
                            run.thread_id,
                            run.trigger_message_id,
                            run.status.value,
                            run.target_ticker,
                            run.peer_tickers,
                            run.currency,
                            run.as_of,
                            Jsonb(run.warnings),
                            run.error_message,
                            run.created_at,
                            run.started_at,
                            run.completed_at,
                        ),
                    )
                    cursor.execute(
                        """
                        insert into comps_tables (
                            run_id, target_ticker, currency, as_of, rows, summary,
                            created_at
                        )
                        values (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            table.run_id,
                            table.target_ticker,
                            table.currency,
                            table.as_of,
                            Jsonb(
                                [row.model_dump(mode="json") for row in table.rows]
                            ),
                            Jsonb(table.summary.model_dump(mode="json")),
                            utc_now(),
                        ),
                    )
                    cursor.execute(
                        """
                        insert into comps_traces (run_id, formulas, created_at)
                        values (%s, %s, %s)
                        """,
                        (
                            trace.run_id,
                            Jsonb(
                                [
                                    formula.model_dump(mode="json")
                                    for formula in trace.formulas
                                ]
                            ),
                            utc_now(),
                        ),
                    )
                    cursor.execute(
                        """
                        insert into comps_source_snapshots (
                            run_id, raw_provider_evidence, normalized_inputs,
                            created_at
                        )
                        values (%s, %s, %s, %s)
                        """,
                        (
                            source_snapshot.run_id,
                            Jsonb(source_snapshot.raw_provider_evidence),
                            Jsonb(
                                [
                                    company.model_dump(mode="json")
                                    for company in source_snapshot.normalized_inputs
                                ]
                            ),
                            source_snapshot.created_at,
                        ),
                    )
        except Exception as exc:
            self._raise_unavailable(exc)

    def get_run(self, run_id: UUID) -> Run | None:
        try:
            with self._connect() as connection:
                with connection.cursor(row_factory=self._dict_row()) as cursor:
                    cursor.execute(
                        """
                        select id, thread_id, trigger_message_id, status,
                            target_ticker, peer_tickers, currency, as_of, warnings,
                            error_message, created_at, started_at, completed_at
                        from comps_runs
                        where id = %s
                        """,
                        (run_id,),
                    )
                    row = cursor.fetchone()
        except Exception as exc:
            self._raise_unavailable(exc)
        return Run.model_validate(row) if row is not None else None

    def get_table(self, run_id: UUID) -> RunTableResponse | None:
        try:
            with self._connect() as connection:
                with connection.cursor(row_factory=self._dict_row()) as cursor:
                    cursor.execute(
                        """
                        select run_id, target_ticker, currency, as_of, rows, summary
                        from comps_tables
                        where run_id = %s
                        """,
                        (run_id,),
                    )
                    row = cursor.fetchone()
        except Exception as exc:
            self._raise_unavailable(exc)
        return RunTableResponse.model_validate(row) if row is not None else None

    def get_trace(self, run_id: UUID) -> TraceResponse | None:
        try:
            with self._connect() as connection:
                with connection.cursor(row_factory=self._dict_row()) as cursor:
                    cursor.execute(
                        """
                        select run_id, formulas
                        from comps_traces
                        where run_id = %s
                        """,
                        (run_id,),
                    )
                    row = cursor.fetchone()
        except Exception as exc:
            self._raise_unavailable(exc)
        return TraceResponse.model_validate(row) if row is not None else None

    def get_source_snapshot(self, run_id: UUID) -> SourceSnapshot | None:
        try:
            with self._connect() as connection:
                with connection.cursor(row_factory=self._dict_row()) as cursor:
                    cursor.execute(
                        """
                        select run_id, raw_provider_evidence, normalized_inputs,
                            created_at
                        from comps_source_snapshots
                        where run_id = %s
                        """,
                        (run_id,),
                    )
                    row = cursor.fetchone()
        except Exception as exc:
            self._raise_unavailable(exc)
        return SourceSnapshot.model_validate(row) if row is not None else None

    def _connect(self):
        if not self._database_url:
            raise CompsPersistenceUnavailable(
                f"Missing required configuration: {DATABASE_URL_VAR}."
            )
        import psycopg

        return psycopg.connect(self._database_url)

    @staticmethod
    def _dict_row():
        from psycopg.rows import dict_row

        return dict_row

    def _raise_unavailable(self, exc: Exception) -> NoReturn:
        if isinstance(
            exc,
            (CompsPersistenceUnavailable, DuplicateToolInvocation, InvalidRunLinkage),
        ):
            raise exc
        diagnostics = getattr(exc, "diag", None)
        constraint_name = getattr(diagnostics, "constraint_name", None)
        if constraint_name == RUN_INVOCATION_ID_UNIQUE_CONSTRAINT:
            raise DuplicateToolInvocation(
                "Tool invocation has already produced a Run."
            ) from exc
        if (
            constraint_name == RUN_TRIGGER_MESSAGE_LINKAGE_CONSTRAINT
        ):
            raise InvalidRunLinkage(
                "Run must reference a persisted trigger Message in its Thread."
            ) from exc
        logger.exception("Comps persistence operation failed.")
        raise CompsPersistenceUnavailable(
            "Comps persistence is unavailable."
        ) from exc
