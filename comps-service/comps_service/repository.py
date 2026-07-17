from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import NoReturn
from uuid import UUID

from talk_to_your_stock_shared import Run, RunTableResponse
from talk_to_your_stock_shared.readiness import DATABASE_URL_VAR
from talk_to_your_stock_shared.time import utc_now


logger = logging.getLogger(__name__)


class CompsPersistenceUnavailable(RuntimeError):
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
        if isinstance(exc, CompsPersistenceUnavailable):
            raise exc
        logger.exception("Comps persistence operation failed.")
        raise CompsPersistenceUnavailable(
            "Comps persistence is unavailable."
        ) from exc
