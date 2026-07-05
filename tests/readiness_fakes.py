from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch


class FakeCursor:
    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, query: str) -> None:
        if query != "select 1":
            raise AssertionError(f"Unexpected readiness query: {query}")

    def fetchone(self) -> tuple[int]:
        return (1,)


class FakeConnection:
    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return FakeCursor()


@contextmanager
def database_connects() -> Iterator[Mock]:
    connect = Mock(return_value=FakeConnection())
    with patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=connect)}):
        yield connect


@contextmanager
def database_unavailable(message: str = "database unavailable") -> Iterator[Mock]:
    connect = Mock(side_effect=RuntimeError(message))
    with patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=connect)}):
        yield connect
