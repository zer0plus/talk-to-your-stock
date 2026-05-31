from __future__ import annotations

from uuid import UUID

from psycopg.types.json import Jsonb

from talk_to_your_stock_shared import Message, MessageRole, MessageStatus, Run, Thread, User, new_id
from talk_to_your_stock_shared.time import utc_now
from web_bff.db import connect


class NotFoundError(Exception):
    pass


class AppRepository:
    def ensure_user(self, *, email: str, name: str | None = None) -> User:
        now = utc_now()
        with connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
            if row is None:
                user_id = new_id()
                row = conn.execute(
                    """
                    INSERT INTO users (id, email, name, avatar_url, created_at, updated_at)
                    VALUES (%s, %s, %s, NULL, %s, %s)
                    RETURNING *
                    """,
                    (user_id, email, name, now, now),
                ).fetchone()
                conn.commit()
        return User(**row)

    def create_thread(self, *, user_id: UUID, title: str) -> Thread:
        now = utc_now()
        thread_id = new_id()
        with connect() as conn:
            row = conn.execute(
                """
                INSERT INTO threads (id, user_id, title, message_count, created_at, updated_at)
                VALUES (%s, %s, %s, 0, %s, %s)
                RETURNING *
                """,
                (thread_id, user_id, title, now, now),
            ).fetchone()
            conn.commit()
        return Thread(**row)

    def list_threads(self, *, user_id: UUID, limit: int) -> list[Thread]:
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM threads
                WHERE user_id = %s
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (user_id, limit),
            ).fetchall()
        return [Thread(**row) for row in rows]

    def get_thread(self, *, user_id: UUID, thread_id: UUID) -> Thread:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM threads WHERE id = %s AND user_id = %s",
                (thread_id, user_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(str(thread_id))
        return Thread(**row)

    def create_message(
        self,
        *,
        thread_id: UUID,
        role: MessageRole,
        content: str,
        run_id: UUID | None = None,
        status: MessageStatus = MessageStatus.COMPLETE,
    ) -> Message:
        now = utc_now()
        message_id = new_id()
        with connect() as conn:
            row = conn.execute(
                """
                INSERT INTO messages (id, thread_id, role, content, status, run_id, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (message_id, thread_id, role.value, content, status.value, run_id, now),
            ).fetchone()
            conn.execute(
                """
                UPDATE threads
                SET message_count = message_count + 1,
                    last_message_at = %s,
                    latest_run_id = COALESCE(%s, latest_run_id),
                    updated_at = %s
                WHERE id = %s
                """,
                (now, run_id, now, thread_id),
            )
            conn.commit()
        return Message(**row)

    def list_messages(self, *, thread_id: UUID, limit: int) -> list[Message]:
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM messages
                WHERE thread_id = %s
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (thread_id, limit),
            ).fetchall()
        return [Message(**row) for row in rows]

    def list_runs(self, *, thread_id: UUID, limit: int, status: str | None = None) -> list[Run]:
        query = "SELECT * FROM runs WHERE thread_id = %s"
        params: list[object] = [thread_id]
        if status:
            query += " AND status = %s"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)
        with connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [Run(**row) for row in rows]

    def seed_run_placeholder(self, run: Run) -> None:
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    id, thread_id, trigger_message_id, status, target_ticker, peer_tickers,
                    currency, as_of, warnings, error_message, created_at, started_at, completed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    run.id,
                    run.thread_id,
                    run.trigger_message_id,
                    run.status.value,
                    run.target_ticker,
                    Jsonb(run.peer_tickers),
                    run.currency,
                    run.as_of,
                    Jsonb(run.warnings),
                    run.error_message,
                    run.created_at,
                    run.started_at,
                    run.completed_at,
                ),
            )
            conn.commit()
