from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch


class FakeCursor:
    def __init__(self, *, schema_revision: str | None) -> None:
        self._schema_revision = schema_revision
        self._query = ""

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, query: str) -> None:
        if query not in {"select 1", "select version_num from alembic_version"}:
            raise AssertionError(f"Unexpected readiness query: {query}")
        self._query = query

    def fetchone(self) -> tuple[int] | tuple[str] | None:
        if self._query == "select version_num from alembic_version":
            if self._schema_revision is None:
                return None
            return (self._schema_revision,)
        return (1,)


class FakeConnection:
    def __init__(self, *, schema_revision: str | None) -> None:
        self._schema_revision = schema_revision

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return FakeCursor(schema_revision=self._schema_revision)


@contextmanager
def database_connects(
    *,
    schema_revision: str | None = "0001_web_bff_schema",
) -> Iterator[Mock]:
    connect = Mock(return_value=FakeConnection(schema_revision=schema_revision))
    with patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=connect)}):
        yield connect


@contextmanager
def database_unavailable(message: str = "database unavailable") -> Iterator[Mock]:
    connect = Mock(side_effect=RuntimeError(message))
    with patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=connect)}):
        yield connect
