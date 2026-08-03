from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from talk_to_your_stock_shared import Run, RunStatus, Thread, User
from web_bff.repository import PostgresWebBffRepository


class RecordingCursor:
    def __init__(
        self,
        *,
        returned_row: dict[str, object] | None = None,
        returned_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self.returned_row = returned_row
        self.returned_rows = returned_rows or []
        self.statements: list[str] = []
        self.parameters: list[object] = []

    def __enter__(self) -> RecordingCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: str, _parameters: object = None) -> None:
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("create table") or normalized.startswith("create index"):
            raise AssertionError("Repository operations must not execute schema DDL.")
        self.statements.append(normalized)
        self.parameters.append(_parameters)

    def fetchone(self) -> dict[str, object] | None:
        return self.returned_row

    def fetchall(self) -> list[dict[str, object]]:
        return self.returned_rows


class RecordingConnection:
    def __init__(self, cursor: RecordingCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> RecordingConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self, **_kwargs: object) -> RecordingCursor:
        return self._cursor


class WebBffRepositoryTest(unittest.TestCase):
    def test_user_write_does_not_attempt_schema_changes(self) -> None:
        now = datetime.now(timezone.utc)
        user = User(
            id=uuid4(),
            email="dev@example.com",
            created_at=now,
            updated_at=now,
        )
        cursor = RecordingCursor(returned_row=user.model_dump())
        repository = PostgresWebBffRepository(database_url="postgresql://test")

        with (
            patch.object(
                repository,
                "_connect",
                return_value=RecordingConnection(cursor),
            ),
            patch.object(repository, "_dict_row", return_value=None),
        ):
            stored_user = repository.upsert_user(user)

        self.assertEqual(stored_user, user)
        self.assertEqual(len(cursor.statements), 1)
        self.assertTrue(cursor.statements[0].startswith("insert into web_bff_users"))

    def test_list_runs_filters_and_orders_newest_first_deterministically(self) -> None:
        now = datetime.now(timezone.utc)
        user_id = uuid4()
        thread_id = uuid4()
        trigger_message_id = uuid4()
        thread = Thread(
            id=thread_id,
            user_id=user_id,
            title="Comps",
            message_count=1,
            created_at=now,
            updated_at=now,
        )
        older = Run(
            id=uuid4(),
            thread_id=thread_id,
            trigger_message_id=trigger_message_id,
            status=RunStatus.SUCCEEDED,
            target_ticker="AAPL",
            peer_tickers=["MSFT"],
            currency="USD",
            as_of=now,
            created_at=now,
        )
        newer = older.model_copy(
            update={"id": uuid4(), "created_at": now + timedelta(seconds=1)}
        )
        owned_thread_cursor = RecordingCursor(returned_row=thread.model_dump())
        run_cursor = RecordingCursor(
            returned_rows=[newer.model_dump(), older.model_dump()]
        )
        repository = PostgresWebBffRepository(database_url="postgresql://test")

        with (
            patch.object(
                repository,
                "_connect",
                side_effect=[
                    RecordingConnection(owned_thread_cursor),
                    RecordingConnection(run_cursor),
                ],
            ),
            patch.object(repository, "_dict_row", return_value=None),
        ):
            runs, page = repository.list_runs(
                thread_id=thread_id,
                user_id=user_id,
                status=RunStatus.SUCCEEDED,
                limit=2,
                cursor=None,
            )

        assert runs is not None
        self.assertEqual([run.id for run in runs], [newer.id, older.id])
        self.assertFalse(page.has_more)
        self.assertIn("status = %s", run_cursor.statements[0])
        self.assertIn("order by created_at desc, id desc", run_cursor.statements[0])
        self.assertEqual(
            run_cursor.parameters[0],
            [thread_id, RunStatus.SUCCEEDED.value, 3],
        )

    def test_list_runs_cursor_continues_after_equal_timestamps(self) -> None:
        now = datetime.now(timezone.utc)
        user_id = uuid4()
        thread_id = uuid4()
        thread = Thread(
            id=thread_id,
            user_id=user_id,
            title="Comps",
            message_count=1,
            created_at=now,
            updated_at=now,
        )
        runs = [
            Run(
                id=run_id,
                thread_id=thread_id,
                trigger_message_id=uuid4(),
                status=RunStatus.SUCCEEDED,
                target_ticker="AAPL",
                peer_tickers=["MSFT"],
                currency="USD",
                as_of=now,
                created_at=now,
            )
            for run_id in sorted([uuid4(), uuid4(), uuid4()], reverse=True)
        ]
        first_page_cursor = RecordingCursor(
            returned_rows=[run.model_dump() for run in runs]
        )
        second_page_cursor = RecordingCursor(returned_rows=[runs[2].model_dump()])
        repository = PostgresWebBffRepository(database_url="postgresql://test")

        with (
            patch.object(
                repository,
                "_connect",
                side_effect=[
                    RecordingConnection(
                        RecordingCursor(returned_row=thread.model_dump())
                    ),
                    RecordingConnection(first_page_cursor),
                    RecordingConnection(
                        RecordingCursor(returned_row=thread.model_dump())
                    ),
                    RecordingConnection(second_page_cursor),
                ],
            ),
            patch.object(repository, "_dict_row", return_value=None),
        ):
            first_page, first_meta = repository.list_runs(
                thread_id=thread_id,
                user_id=user_id,
                status=None,
                limit=2,
                cursor=None,
            )
            second_page, second_meta = repository.list_runs(
                thread_id=thread_id,
                user_id=user_id,
                status=None,
                limit=2,
                cursor=first_meta.next_cursor,
            )

        assert first_page is not None
        assert second_page is not None
        self.assertEqual([run.id for run in first_page], [runs[0].id, runs[1].id])
        self.assertTrue(first_meta.has_more)
        self.assertEqual([run.id for run in second_page], [runs[2].id])
        self.assertFalse(second_meta.has_more)
        self.assertEqual(
            second_page_cursor.parameters[0],
            [thread_id, now, runs[1].id, 3],
        )

    def test_list_runs_rejects_invalid_cursor(self) -> None:
        now = datetime.now(timezone.utc)
        user_id = uuid4()
        thread_id = uuid4()
        thread = Thread(
            id=thread_id,
            user_id=user_id,
            title="Comps",
            message_count=0,
            created_at=now,
            updated_at=now,
        )
        repository = PostgresWebBffRepository(database_url="postgresql://test")

        with (
            patch.object(
                repository,
                "_connect",
                return_value=RecordingConnection(
                    RecordingCursor(returned_row=thread.model_dump())
                ),
            ),
            patch.object(repository, "_dict_row", return_value=None),
        ):
            with self.assertRaisesRegex(ValueError, "Run cursor is invalid"):
                repository.list_runs(
                    thread_id=thread_id,
                    user_id=user_id,
                    status=None,
                    limit=20,
                    cursor="not-a-cursor",
                )


if __name__ == "__main__":
    unittest.main()
