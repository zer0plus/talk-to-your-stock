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
    ErrorDetail,
    ErrorResponse,
    GenerateCompsToolResponse,
    PaginationMeta,
    Run,
    RunStatus,
    RunTableResponse,
    TraceResponse,
)
from talk_to_your_stock_shared.readiness import DATABASE_URL_VAR
from talk_to_your_stock_shared.time import utc_now

from .artifacts import SourceSnapshot
from .run_service import DuplicateToolInvocation, RecoveredFailedRun


logger = logging.getLogger(__name__)
RUN_TRIGGER_MESSAGE_LINKAGE_CONSTRAINT = "comps_runs_trigger_message_linkage_fk"
RUN_INVOCATION_ID_UNIQUE_CONSTRAINT = "comps_runs_invocation_id_unique"


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

    def reserve_run(
        self,
        *,
        invocation_id: UUID,
        run: Run,
        validation_evidence: dict[str, dict[str, object]],
    ) -> Run:
        from psycopg.types.json import Jsonb

        try:
            with self._connect() as connection:
                with connection.cursor(row_factory=self._dict_row()) as cursor:
                    cursor.execute(
                        """
                        insert into comps_runs (
                            id, invocation_id, thread_id, trigger_message_id, status,
                            target_ticker, peer_tickers, currency, as_of, warnings,
                            error_message, created_at, started_at, completed_at,
                            validation_evidence
                        )
                        values (
                            %s, %s, %s, %s, 'queued', %s, %s, %s, null, %s,
                            null, %s, null, null, %s
                        )
                        on conflict (invocation_id) do nothing
                        """,
                        (
                            run.id,
                            invocation_id,
                            run.thread_id,
                            run.trigger_message_id,
                            run.target_ticker,
                            run.peer_tickers,
                            run.currency,
                            Jsonb(run.warnings),
                            run.created_at,
                            Jsonb(validation_evidence),
                        ),
                    )
                    cursor.execute(
                        """
                        select id, thread_id, trigger_message_id, status,
                            target_ticker, peer_tickers, currency, as_of, warnings,
                            error_message, created_at, started_at, completed_at
                        from comps_runs
                        where invocation_id = %s
                        """,
                        (invocation_id,),
                    )
                    row = cursor.fetchone()
        except Exception as exc:
            self._raise_unavailable(exc)
        if row is None:
            raise CompsPersistenceUnavailable("Comps Run reservation was not stored.")
        return Run.model_validate(row)

    def claim_run(
        self,
        *,
        run_id: UUID,
        owner_id: UUID,
        lease_seconds: float,
    ) -> Run | None:
        try:
            with self._connect() as connection:
                with connection.cursor(row_factory=self._dict_row()) as cursor:
                    cursor.execute(
                        """
                        update comps_runs
                        set status = 'running',
                            calculation_owner_id = %s,
                            calculation_lease_expires_at = clock_timestamp()
                                + (%s * interval '1 second'),
                            started_at = coalesce(started_at, clock_timestamp())
                        where id = %s
                          and (
                            status = 'queued'
                            or (
                                status = 'running'
                                and calculation_lease_expires_at
                                    <= clock_timestamp()
                            )
                          )
                        returning id, thread_id, trigger_message_id, status,
                            target_ticker, peer_tickers, currency, as_of, warnings,
                            error_message, created_at, started_at, completed_at
                        """,
                        (
                            owner_id,
                            lease_seconds,
                            run_id,
                        ),
                    )
                    row = cursor.fetchone()
        except Exception as exc:
            self._raise_unavailable(exc)
        return Run.model_validate(row) if row is not None else None

    def get_validation_evidence(
        self,
        *,
        invocation_id: UUID,
    ) -> dict[str, dict[str, object]] | None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        select validation_evidence
                        from comps_runs
                        where invocation_id = %s
                        """,
                        (invocation_id,),
                    )
                    row = cursor.fetchone()
        except Exception as exc:
            self._raise_unavailable(exc)
        return row[0] if row is not None else None

    def renew_run_lease(
        self,
        *,
        run_id: UUID,
        owner_id: UUID,
        lease_seconds: float,
    ) -> bool:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        update comps_runs
                        set calculation_lease_expires_at = clock_timestamp()
                            + (%s * interval '1 second')
                        where id = %s and status = 'running'
                          and calculation_owner_id = %s
                        """,
                        (lease_seconds, run_id, owner_id),
                    )
                    renewed = cursor.rowcount == 1
        except Exception as exc:
            self._raise_unavailable(exc)
        return renewed

    def complete_succeeded_run(
        self,
        *,
        owner_id: UUID,
        run: Run,
        table: RunTableResponse,
        trace: TraceResponse,
        source_snapshot: SourceSnapshot,
    ) -> bool:
        from psycopg.types.json import Jsonb

        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        update comps_runs
                        set status = 'succeeded', as_of = %s, warnings = %s,
                            error_message = null, completed_at = %s,
                            calculation_owner_id = null,
                            calculation_lease_expires_at = null
                        where id = %s and status = 'running'
                          and calculation_owner_id = %s
                        """,
                        (
                            run.as_of,
                            Jsonb(run.warnings),
                            run.completed_at,
                            run.id,
                            owner_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        return False
                    self._insert_success_artifacts(
                        cursor=cursor,
                        table=table,
                        trace=trace,
                        source_snapshot=source_snapshot,
                    )
        except Exception as exc:
            self._raise_unavailable(exc)
        return True

    def complete_failed_run(
        self,
        *,
        owner_id: UUID,
        run: Run,
        status_code: int,
        error: ErrorResponse,
        source_snapshot: SourceSnapshot,
    ) -> bool:
        from psycopg.types.json import Jsonb

        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        update comps_runs
                        set status = 'failed', error_message = %s,
                            completed_at = %s, calculation_owner_id = null,
                            calculation_lease_expires_at = null,
                            failure_http_status = %s, failure_code = %s,
                            failure_details = %s
                        where id = %s and status = 'running'
                          and calculation_owner_id = %s
                        """,
                        (
                            error.error.message,
                            run.completed_at,
                            status_code,
                            error.error.code.value,
                            Jsonb(error.error.details),
                            run.id,
                            owner_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        return False
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
        return True

    @staticmethod
    def _insert_success_artifacts(
        *,
        cursor,
        table: RunTableResponse,
        trace: TraceResponse,
        source_snapshot: SourceSnapshot,
    ) -> None:
        from psycopg.types.json import Jsonb

        cursor.execute(
            """
            insert into comps_tables (
                run_id, target_ticker, currency, as_of, rows, summary, created_at
            )
            values (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                table.run_id,
                table.target_ticker,
                table.currency,
                table.as_of,
                Jsonb([row.model_dump(mode="json") for row in table.rows]),
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
                    [formula.model_dump(mode="json") for formula in trace.formulas]
                ),
                utc_now(),
            ),
        )
        cursor.execute(
            """
            insert into comps_source_snapshots (
                run_id, raw_provider_evidence, normalized_inputs, created_at
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

    def get_succeeded_result(
        self,
        *,
        invocation_id: UUID,
    ) -> GenerateCompsToolResponse | None:
        try:
            with self._connect() as connection:
                with connection.cursor(row_factory=self._dict_row()) as cursor:
                    cursor.execute(
                        """
                        select r.id, r.thread_id, r.trigger_message_id, r.status,
                            r.target_ticker, r.peer_tickers, r.currency, r.as_of,
                            r.warnings, r.error_message, r.created_at,
                            r.started_at, r.completed_at,
                            jsonb_build_object(
                                'run_id', t.run_id,
                                'target_ticker', t.target_ticker,
                                'currency', t.currency,
                                'as_of', t.as_of,
                                'rows', t.rows,
                                'summary', t.summary
                            ) as table,
                            jsonb_build_object(
                                'run_id', tr.run_id,
                                'formulas', tr.formulas
                            ) as trace
                        from comps_runs r
                        join comps_tables t on t.run_id = r.id
                        join comps_traces tr on tr.run_id = r.id
                        where r.invocation_id = %s and r.status = 'succeeded'
                        """,
                        (invocation_id,),
                    )
                    row = cursor.fetchone()
        except Exception as exc:
            self._raise_unavailable(exc)
        if row is None:
            return None
        table = RunTableResponse.model_validate(row.pop("table"))
        trace = TraceResponse.model_validate(row.pop("trace"))
        run = Run.model_validate(row)
        return GenerateCompsToolResponse(
            run=run,
            table=table,
            trace=trace,
            warnings=run.warnings,
        )

    def get_failed_result(
        self,
        *,
        invocation_id: UUID,
    ) -> RecoveredFailedRun | None:
        try:
            with self._connect() as connection:
                with connection.cursor(row_factory=self._dict_row()) as cursor:
                    cursor.execute(
                        """
                        select id, thread_id, trigger_message_id, status,
                            target_ticker, peer_tickers, currency, as_of, warnings,
                            error_message, created_at, started_at, completed_at,
                            failure_http_status, failure_code, failure_details
                        from comps_runs
                        where invocation_id = %s and status = 'failed'
                        """,
                        (invocation_id,),
                    )
                    row = cursor.fetchone()
        except Exception as exc:
            self._raise_unavailable(exc)
        if row is None:
            return None
        status_code = row.pop("failure_http_status")
        failure_code = row.pop("failure_code")
        failure_details = row.pop("failure_details")
        run = Run.model_validate(row)
        return RecoveredFailedRun(
            run=run,
            status_code=status_code,
            error=ErrorResponse(
                error=ErrorDetail(
                    code=failure_code,
                    message=run.error_message or "Comps Run failed.",
                    details=failure_details,
                    run_id=run.id,
                )
            ),
        )

    def get_run_by_invocation(self, *, invocation_id: UUID) -> Run | None:
        try:
            with self._connect() as connection:
                with connection.cursor(row_factory=self._dict_row()) as cursor:
                    cursor.execute(
                        """
                        select id, thread_id, trigger_message_id, status,
                            target_ticker, peer_tickers, currency, as_of, warnings,
                            error_message, created_at, started_at, completed_at
                        from comps_runs
                        where invocation_id = %s
                        """,
                        (invocation_id,),
                    )
                    row = cursor.fetchone()
        except Exception as exc:
            self._raise_unavailable(exc)
        return Run.model_validate(row) if row is not None else None

    def get_active_run_by_invocation(self, *, invocation_id: UUID) -> Run | None:
        try:
            with self._connect() as connection:
                with connection.cursor(row_factory=self._dict_row()) as cursor:
                    cursor.execute(
                        """
                        select id, thread_id, trigger_message_id, status,
                            target_ticker, peer_tickers, currency, as_of, warnings,
                            error_message, created_at, started_at, completed_at
                        from comps_runs
                        where invocation_id = %s and status = 'running'
                          and calculation_lease_expires_at > clock_timestamp()
                        """,
                        (invocation_id,),
                    )
                    row = cursor.fetchone()
        except Exception as exc:
            self._raise_unavailable(exc)
        return Run.model_validate(row) if row is not None else None

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

        return psycopg.connect(
            self._database_url,
            options="-c timezone=UTC",
        )

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
