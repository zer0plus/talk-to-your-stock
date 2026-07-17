from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock
from uuid import UUID


class ThreadTurnCoordinator:
    def __init__(self) -> None:
        self._locks: dict[UUID, Lock] = {}
        self._lock_ref_counts: dict[UUID, int] = {}
        self._locks_guard = Lock()

    @contextmanager
    def turn(self, *, thread_id: UUID) -> Iterator[None]:
        with self._locks_guard:
            lock = self._locks.get(thread_id)
            if lock is None:
                lock = Lock()
                self._locks[thread_id] = lock
            self._lock_ref_counts[thread_id] = (
                self._lock_ref_counts.get(thread_id, 0) + 1
            )

        try:
            with lock:
                yield
        finally:
            with self._locks_guard:
                remaining = self._lock_ref_counts[thread_id] - 1
                if remaining == 0:
                    del self._lock_ref_counts[thread_id]
                    del self._locks[thread_id]
                else:
                    self._lock_ref_counts[thread_id] = remaining
