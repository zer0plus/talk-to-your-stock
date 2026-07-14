from __future__ import annotations

import os
from collections.abc import Mapping
from uuid import UUID, uuid4

from talk_to_your_stock_shared import (
    Message,
    MessageRole,
    MessageStatus,
    PaginationMeta,
    Thread,
    User,
)
from talk_to_your_stock_shared.readiness import DATABASE_URL_VAR
from talk_to_your_stock_shared.time import utc_now


class RepositoryConfigurationError(RuntimeError):
    pass


class PostgresWebBffRepository:
    def __init__(self, *, database_url: str) -> None:
        if not database_url.strip():
            raise RepositoryConfigurationError(f"{DATABASE_URL_VAR} is required.")
        self._database_url = database_url

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> PostgresWebBffRepository:
        env = os.environ if environ is None else environ
        return cls(database_url=env.get(DATABASE_URL_VAR, ""))

    def upsert_user(self, user: User) -> User:
        now = utc_now()
        with self._connect() as connection:
            with connection.cursor(row_factory=self._dict_row()) as cursor:
                cursor.execute(
                    """
                    insert into web_bff_users (
                        id, email, name, avatar_url, created_at, updated_at
                    )
                    values (%s, %s, %s, %s, %s, %s)
                    on conflict (id) do update set
                        email = excluded.email,
                        name = excluded.name,
                        avatar_url = excluded.avatar_url,
                        updated_at = excluded.updated_at
                    returning id, email, name, avatar_url, created_at, updated_at
                    """,
                    (user.id, user.email, user.name, user.avatar_url, now, now),
                )
                return User.model_validate(cursor.fetchone())

    def create_thread(self, *, user_id: UUID, title: str) -> Thread:
        now = utc_now()
        thread_id = uuid4()
        with self._connect() as connection:
            with connection.cursor(row_factory=self._dict_row()) as cursor:
                cursor.execute(
                    """
                    insert into web_bff_threads (
                        id, user_id, title, message_count, created_at, updated_at
                    )
                    values (%s, %s, %s, 0, %s, %s)
                    returning id, user_id, title, message_count, last_message_at,
                        latest_run_id, created_at, updated_at
                    """,
                    (thread_id, user_id, title, now, now),
                )
                return Thread.model_validate(cursor.fetchone())

    def list_threads(
        self,
        *,
        user_id: UUID,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[Thread], PaginationMeta]:
        offset = _cursor_to_offset(cursor)
        with self._connect() as connection:
            with connection.cursor(row_factory=self._dict_row()) as db_cursor:
                db_cursor.execute(
                    """
                    select id, user_id, title, message_count, last_message_at,
                        latest_run_id, created_at, updated_at
                    from web_bff_threads
                    where user_id = %s
                    order by updated_at desc, id desc
                    limit %s offset %s
                    """,
                    (user_id, limit + 1, offset),
                )
                rows = db_cursor.fetchall()

        has_more = len(rows) > limit
        threads = [Thread.model_validate(row) for row in rows[:limit]]
        next_cursor = str(offset + limit) if has_more else None
        return threads, PaginationMeta(has_more=has_more, next_cursor=next_cursor)

    def get_thread(self, *, thread_id: UUID, user_id: UUID) -> Thread | None:
        with self._connect() as connection:
            with connection.cursor(row_factory=self._dict_row()) as cursor:
                cursor.execute(
                    """
                    select id, user_id, title, message_count, last_message_at,
                        latest_run_id, created_at, updated_at
                    from web_bff_threads
                    where id = %s and user_id = %s
                    """,
                    (thread_id, user_id),
                )
                row = cursor.fetchone()
        return Thread.model_validate(row) if row is not None else None

    def create_message(
        self,
        *,
        thread_id: UUID,
        role: MessageRole,
        content: str,
        status: MessageStatus,
        run_id: UUID | None = None,
    ) -> Message:
        now = utc_now()
        message_id = uuid4()
        with self._connect() as connection:
            with connection.cursor(row_factory=self._dict_row()) as cursor:
                cursor.execute(
                    """
                    insert into web_bff_messages (
                        id, thread_id, role, content, status, run_id, created_at
                    )
                    values (%s, %s, %s, %s, %s, %s, %s)
                    returning id, thread_id, role, content, status, run_id, created_at
                    """,
                    (
                        message_id,
                        thread_id,
                        role.value,
                        content,
                        status.value,
                        run_id,
                        now,
                    ),
                )
                message = Message.model_validate(cursor.fetchone())
                cursor.execute(
                    """
                    update web_bff_threads
                    set message_count = message_count + 1,
                        last_message_at = %s,
                        latest_run_id = coalesce(%s, latest_run_id),
                        updated_at = %s
                    where id = %s
                    """,
                    (now, run_id, now, thread_id),
                )
        return message

    def list_messages(
        self,
        *,
        thread_id: UUID,
        user_id: UUID,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[Message] | None, PaginationMeta]:
        if self.get_thread(thread_id=thread_id, user_id=user_id) is None:
            return None, PaginationMeta(has_more=False, next_cursor=None)

        offset = _cursor_to_offset(cursor)
        with self._connect() as connection:
            with connection.cursor(row_factory=self._dict_row()) as db_cursor:
                db_cursor.execute(
                    """
                    select id, thread_id, role, content, status, run_id, created_at
                    from web_bff_messages
                    where thread_id = %s
                    order by created_at asc, id asc
                    limit %s offset %s
                    """,
                    (thread_id, limit + 1, offset),
                )
                rows = db_cursor.fetchall()

        has_more = len(rows) > limit
        messages = [Message.model_validate(row) for row in rows[:limit]]
        next_cursor = str(offset + limit) if has_more else None
        return messages, PaginationMeta(has_more=has_more, next_cursor=next_cursor)

    def _connect(self):
        import psycopg

        return psycopg.connect(self._database_url)

    @staticmethod
    def _dict_row():
        from psycopg.rows import dict_row

        return dict_row


def _cursor_to_offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        return max(int(cursor), 0)
    except ValueError:
        return 0
