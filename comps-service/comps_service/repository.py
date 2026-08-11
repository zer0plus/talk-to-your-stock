from __future__ import annotations

import logging
import os
from base64 import b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from collections.abc import Mapping
from datetime import datetime
from typing import NoReturn
from uuid import UUID

from talk_to_your_stock_shared import (
    GenerateCompsDraftResponse,
    PaginationMeta,
    Run,
    RunStatus,
    RunTableDraftResponse,
    RunTableResponse,
    TraceResponse,
)
from talk_to_your_stock_shared.readiness import DATABASE_URL_VAR
from talk_to_your_stock_shared.time import utc_now

from .artifacts import FailedRunInvocation, RunFailure, SourceSnapshot
from .run_service import CalculatedRunNotFound, DuplicateToolInvocation


logger = logging.getLogger(__name__)
RUN_TRIGGER_MESSAGE_LINKAGE_CONSTRAINT = "comps_runs_trigger_message_linkage_fk"
RUN_INVOCATION_ID_UNIQUE_CONSTRAINT = "comps_runs_invocation_id_unique"
COMPS_TABLE_PRIMARY_KEY_CONSTRAINT = "comps_tables_pkey"
SOURCE_SNAPSHOT_PRIMARY_KEY_CONSTRAINT = "comps_source_snapshots_pkey"


class CompsPersistenceUnavailable(RuntimeError):
    pass


class InvalidRunLinkage(ValueError):
    pass


class InvalidRunCursor(ValueError):
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

    def reserve_run(self, *, invocation_id: UUID, run: Run) -> None:
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
        except Exception as exc:
            self._raise_unavailable(exc)

    def claim_run_for_calculation(
        self,
        *,
        run_id: UUID,
        started_at: datetime,
    ) -> bool:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        update comps_runs
                        set status = %s, started_at = %s
                        where id = %s and status = %s
                        """,
                        (
                            RunStatus.RUNNING.value,
                            started_at,
                            run_id,
                            RunStatus.QUEUED.value,
                        ),
                    )
                    return cursor.rowcount == 1
        except Exception as exc:
            self._raise_unavailable(exc)

    def save_calculated_run(
        self,
        *,
        invocation_id: UUID,
        run: Run,
        table: RunTableDraftResponse,
        trace: TraceResponse,
        source_snapshot: SourceSnapshot,
    ) -> None:
        from psycopg.types.json import Jsonb

        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        update comps_runs
                        set as_of = %s, warnings = %s
                        where id = %s and invocation_id = %s and status = %s
                        """,
                        (
                            run.as_of,
                            Jsonb(run.warnings),
                            run.id,
                            invocation_id,
                            RunStatus.RUNNING.value,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise DuplicateToolInvocation(
                            "Tool invocation has already produced a Run."
                        )
                    cursor.execute(
                        """
                        insert into comps_tables (
                            run_id, target_ticker, currency, as_of, rows, summary,
                            comparison_takeaway, created_at
                        )
                        values (%s, %s, %s, %s, %s, %s, %s, %s)
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
                            None,
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

    def save_failed_run(
        self,
        *,
        invocation_id: UUID,
        run: Run,
        failure: RunFailure,
        source_snapshot: SourceSnapshot,
    ) -> None:
        from psycopg.types.json import Jsonb

        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        update comps_runs
                        set status = %s, error_message = %s,
                            generation_failure = %s, completed_at = %s
                        where id = %s and invocation_id = %s and status = %s
                        """,
                        (
                            run.status.value,
                            run.error_message,
                            Jsonb(failure.model_dump(mode="json")),
                            run.completed_at,
                            run.id,
                            invocation_id,
                            RunStatus.RUNNING.value,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise DuplicateToolInvocation(
                            "Tool invocation has already produced a Run."
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

    def get_calculated_run_by_invocation(
        self,
        invocation_id: UUID,
    ) -> GenerateCompsDraftResponse | FailedRunInvocation | Run | None:
        try:
            with self._connect() as connection:
                with connection.cursor(row_factory=self._dict_row()) as cursor:
                    cursor.execute(
                        """
                        select
                            jsonb_build_object(
                                'id', runs.id,
                                'thread_id', runs.thread_id,
                                'trigger_message_id', runs.trigger_message_id,
                                'status', runs.status,
                                'target_ticker', runs.target_ticker,
                                'peer_tickers', runs.peer_tickers,
                                'currency', runs.currency,
                                'as_of', runs.as_of,
                                'warnings', runs.warnings,
                                'error_message', runs.error_message,
                                'created_at', runs.created_at,
                                'started_at', runs.started_at,
                                'completed_at', runs.completed_at
                            ) as run,
                            runs.generation_failure,
                            jsonb_build_object(
                                'run_id', tables.run_id,
                                'target_ticker', tables.target_ticker,
                                'currency', tables.currency,
                                'as_of', tables.as_of,
                                'rows', tables.rows,
                                'summary', tables.summary
                            ) as table,
                            jsonb_build_object(
                                'run_id', traces.run_id,
                                'formulas', traces.formulas
                            ) as trace
                        from comps_runs as runs
                        left join comps_tables as tables on tables.run_id = runs.id
                        left join comps_traces as traces on traces.run_id = runs.id
                        where runs.invocation_id = %s
                        """,
                        (invocation_id,),
                    )
                    row = cursor.fetchone()
        except Exception as exc:
            self._raise_unavailable(exc)
        if row is None:
            return None
        run = Run.model_validate(row["run"])
        if run.status != RunStatus.RUNNING:
            if row["generation_failure"] is not None:
                return FailedRunInvocation(
                    run=run,
                    failure=RunFailure.model_validate(row["generation_failure"]),
                )
            return run
        if row["table"] is None or row["trace"] is None:
            return run
        table = RunTableDraftResponse.model_validate(row["table"])
        trace = TraceResponse.model_validate(row["trace"])
        return GenerateCompsDraftResponse(
            run=run,
            table=table,
            trace=trace,
            warnings=run.warnings,
        )

    def list_runs(
        self,
        *,
        thread_id: UUID,
        status: RunStatus | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[Run], PaginationMeta]:
        page_cursor = _decode_run_cursor(cursor) if cursor is not None else None
        filters = []
        parameters: list[object] = [thread_id]
        if status is not None:
            filters.append("status = %s")
            parameters.append(status.value)
        if page_cursor is not None:
            filters.append("(created_at, id) < (%s, %s)")
            parameters.extend(page_cursor)
        where_suffix = "" if not filters else "and " + " and ".join(filters)
        parameters.append(limit + 1)

        try:
            with self._connect() as connection:
                with connection.cursor(row_factory=self._dict_row()) as db_cursor:
                    db_cursor.execute(
                        f"""
                        select id, thread_id, trigger_message_id, status,
                            target_ticker, peer_tickers, currency, as_of, warnings,
                            error_message, created_at, started_at, completed_at
                        from comps_runs
                        where thread_id = %s
                        {where_suffix}
                        order by created_at desc, id desc
                        limit %s
                        """,
                        parameters,
                    )
                    rows = db_cursor.fetchall()
        except Exception as exc:
            self._raise_unavailable(exc)

        has_more = len(rows) > limit
        runs = [Run.model_validate(row) for row in rows[:limit]]
        next_cursor = _encode_run_cursor(runs[-1]) if has_more else None
        return runs, PaginationMeta(has_more=has_more, next_cursor=next_cursor)

    def get_table(self, run_id: UUID) -> RunTableResponse | None:
        try:
            with self._connect() as connection:
                with connection.cursor(row_factory=self._dict_row()) as cursor:
                    cursor.execute(
                        """
                        select run_id, target_ticker, currency, as_of, rows, summary,
                            comparison_takeaway
                        from comps_tables
                        where run_id = %s and comparison_takeaway is not null
                        """,
                        (run_id,),
                    )
                    row = cursor.fetchone()
        except Exception as exc:
            self._raise_unavailable(exc)
        if row is None:
            return None
        return RunTableResponse.model_validate(row)

    def get_draft_table(self, run_id: UUID) -> RunTableDraftResponse | None:
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
        if row is None:
            return None
        return RunTableDraftResponse.model_validate(row)

    def finalize_succeeded_run(
        self,
        *,
        run: Run,
        table: RunTableResponse,
    ) -> None:
        from psycopg.types.json import Jsonb

        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        update comps_tables
                        set comparison_takeaway = %s
                        where run_id = %s and comparison_takeaway is null
                        """,
                        (
                            Jsonb(
                                table.comparison_takeaway.model_dump(mode="json")
                            ),
                            run.id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise CalculatedRunNotFound(
                            "Calculated Comps Run not found."
                        )
                    cursor.execute(
                        """
                        update comps_runs
                        set status = %s, completed_at = %s
                        where id = %s and status = %s
                        """,
                        (
                            run.status.value,
                            run.completed_at,
                            run.id,
                            RunStatus.RUNNING.value,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise CalculatedRunNotFound(
                            "Calculated Comps Run not found."
                        )
        except Exception as exc:
            self._raise_unavailable(exc)

    def finalize_failed_run(self, *, run: Run) -> None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        update comps_runs
                        set status = %s, error_message = %s, completed_at = %s
                        where id = %s and status in (%s, %s)
                        """,
                        (
                            run.status.value,
                            run.error_message,
                            run.completed_at,
                            run.id,
                            RunStatus.QUEUED.value,
                            RunStatus.RUNNING.value,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise CalculatedRunNotFound(
                            "Calculated Comps Run not found."
                        )
        except Exception as exc:
            self._raise_unavailable(exc)

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
            (
                CalculatedRunNotFound,
                CompsPersistenceUnavailable,
                DuplicateToolInvocation,
                InvalidRunLinkage,
            ),
        ):
            raise exc
        diagnostics = getattr(exc, "diag", None)
        constraint_name = getattr(diagnostics, "constraint_name", None)
        if constraint_name == RUN_INVOCATION_ID_UNIQUE_CONSTRAINT:
            raise DuplicateToolInvocation(
                "Tool invocation has already produced a Run."
            ) from exc
        if constraint_name in {
            COMPS_TABLE_PRIMARY_KEY_CONSTRAINT,
            SOURCE_SNAPSHOT_PRIMARY_KEY_CONSTRAINT,
        }:
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


def _encode_run_cursor(run: Run) -> str:
    value = f"{run.created_at.isoformat()}|{run.id}".encode()
    return urlsafe_b64encode(value).decode().rstrip("=")


def _decode_run_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padding = "=" * (-len(cursor) % 4)
        value = b64decode(cursor + padding, altchars=b"-_", validate=True).decode()
        created_at_value, run_id_value = value.split("|", maxsplit=1)
        created_at = datetime.fromisoformat(created_at_value)
        run_id = UUID(run_id_value)
    except (Base64Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidRunCursor("Run cursor is invalid.") from exc
    if created_at.tzinfo is None:
        raise InvalidRunCursor("Run cursor is invalid.")
    return created_at, run_id
