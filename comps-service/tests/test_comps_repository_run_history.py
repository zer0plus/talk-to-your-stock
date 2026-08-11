from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID, uuid4

from comps_service.repository import InvalidRunCursor, PostgresCompsRunRepository
from talk_to_your_stock_shared import Run, RunStatus


class RecordingCursor:
    def __init__(
        self,
        *,
        rows: list[dict[str, object]],
        single_rows: list[object] | None = None,
    ) -> None:
        self.rows = rows
        self.single_rows = iter(single_rows or [])
        self.statement = ""
        self.parameters: object = None

    def __enter__(self) -> RecordingCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: str, parameters: object = None) -> None:
        self.statement = " ".join(statement.lower().split())
        self.parameters = parameters

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows

    def fetchone(self) -> object | None:
        return next(self.single_rows)


class RecordingConnection:
    def __init__(self, cursor: RecordingCursor) -> None:
        self.cursor_value = cursor

    def __enter__(self) -> RecordingConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self, **_kwargs: object) -> RecordingCursor:
        return self.cursor_value


class CompsRepositoryRunHistoryTest(unittest.TestCase):
    def test_invocation_recovery_returns_a_failed_run_without_draft_artifacts(
        self,
    ) -> None:
        invocation_id = uuid4()
        failed_run = _run(
            run_id=uuid4(),
            thread_id=uuid4(),
            created_at=datetime(2026, 8, 3, tzinfo=UTC),
        ).model_copy(
            update={
                "status": RunStatus.FAILED,
                "error_message": "Provider evidence was unavailable.",
            }
        )
        cursor = RecordingCursor(
            rows=[],
            single_rows=[
                {
                    "run": failed_run.model_dump(),
                    "table": None,
                    "trace": None,
                }
            ],
        )
        repository = PostgresCompsRunRepository(database_url="postgresql://test")

        with (
            patch.object(
                repository,
                "_connect",
                return_value=RecordingConnection(cursor),
            ),
            patch.object(repository, "_dict_row", return_value=None),
        ):
            recovered = repository.get_calculated_run_by_invocation(invocation_id)

        self.assertEqual(recovered, failed_run)

    def test_recovery_does_not_return_a_run_that_became_terminal(self) -> None:
        run_id = uuid4()
        invocation_id = uuid4()
        created_at = datetime(2026, 8, 3, tzinfo=UTC)
        terminal_run = _run(
            run_id=run_id,
            thread_id=uuid4(),
            created_at=created_at,
        )
        running_run = terminal_run.model_copy(update={"status": RunStatus.RUNNING})
        draft_table = {
            "run_id": run_id,
            "target_ticker": "AAPL",
            "currency": "USD",
            "as_of": created_at,
            "rows": [],
            "summary": {
                "stats": {
                    metric: {"min": None, "median": None, "max": None}
                    for metric in (
                        "ev_to_revenue",
                        "ev_to_ebit",
                        "ev_to_ebitda",
                        "pe",
                    )
                }
            },
        }
        trace = {"run_id": run_id, "formulas": []}
        cursor = RecordingCursor(
            rows=[],
            single_rows=[
                {
                    0: run_id,
                    "run": running_run.model_dump(),
                    "table": draft_table,
                    "trace": trace,
                },
                terminal_run.model_dump(),
                draft_table,
                trace,
            ],
        )
        repository = PostgresCompsRunRepository(database_url="postgresql://test")

        with (
            patch.object(
                repository,
                "_connect",
                return_value=RecordingConnection(cursor),
            ),
            patch.object(repository, "_dict_row", return_value=None),
        ):
            recovered = repository.get_calculated_run_by_invocation(invocation_id)

        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual(recovered.run.status, RunStatus.RUNNING)

    def test_status_filtered_pages_use_deterministic_newest_first_order(self) -> None:
        created_at = datetime(2026, 8, 3, tzinfo=UTC)
        thread_id = uuid4()
        runs = [
            _run(run_id=run_id, thread_id=thread_id, created_at=created_at)
            for run_id in sorted([uuid4(), uuid4(), uuid4()], reverse=True)
        ]
        first_cursor = RecordingCursor(
            rows=[run.model_dump() for run in runs]
        )
        second_cursor = RecordingCursor(rows=[runs[2].model_dump()])
        repository = PostgresCompsRunRepository(database_url="postgresql://test")

        with (
            patch.object(
                repository,
                "_connect",
                side_effect=[
                    RecordingConnection(first_cursor),
                    RecordingConnection(second_cursor),
                ],
            ),
            patch.object(repository, "_dict_row", return_value=None),
        ):
            first_page, first_meta = repository.list_runs(
                thread_id=thread_id,
                status=RunStatus.SUCCEEDED,
                limit=2,
                cursor=None,
            )
            second_page, second_meta = repository.list_runs(
                thread_id=thread_id,
                status=RunStatus.SUCCEEDED,
                limit=2,
                cursor=first_meta.next_cursor,
            )

        self.assertEqual([run.id for run in first_page], [runs[0].id, runs[1].id])
        self.assertTrue(first_meta.has_more)
        self.assertEqual([run.id for run in second_page], [runs[2].id])
        self.assertFalse(second_meta.has_more)
        self.assertIn("status = %s", first_cursor.statement)
        self.assertIn("order by created_at desc, id desc", first_cursor.statement)
        self.assertEqual(
            second_cursor.parameters,
            [
                thread_id,
                RunStatus.SUCCEEDED.value,
                created_at,
                runs[1].id,
                3,
            ],
        )

    def test_invalid_cursor_is_rejected_before_database_access(self) -> None:
        repository = PostgresCompsRunRepository(database_url="postgresql://test")

        with (
            patch.object(repository, "_connect") as connect,
            self.assertRaisesRegex(InvalidRunCursor, "Run cursor is invalid"),
        ):
            repository.list_runs(
                thread_id=uuid4(),
                status=None,
                limit=20,
                cursor="not-a-cursor",
            )

        connect.assert_not_called()


def _run(*, run_id: UUID, thread_id: UUID, created_at: datetime) -> Run:
    return Run(
        id=run_id,
        thread_id=thread_id,
        trigger_message_id=uuid4(),
        status=RunStatus.SUCCEEDED,
        target_ticker="AAPL",
        peer_tickers=["MSFT"],
        currency="USD",
        as_of=created_at,
        created_at=created_at,
    )


if __name__ == "__main__":
    unittest.main()
