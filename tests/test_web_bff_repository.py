from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from talk_to_your_stock_shared import User
from web_bff.repository import PostgresWebBffRepository


class RecordingCursor:
    def __init__(self, *, returned_row: dict[str, object]) -> None:
        self.returned_row = returned_row
        self.statements: list[str] = []

    def __enter__(self) -> RecordingCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: str, _parameters: object = None) -> None:
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("create table") or normalized.startswith("create index"):
            raise AssertionError("Repository operations must not execute schema DDL.")
        self.statements.append(normalized)

    def fetchone(self) -> dict[str, object]:
        return self.returned_row


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


if __name__ == "__main__":
    unittest.main()
