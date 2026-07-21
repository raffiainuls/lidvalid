"""In-process pub/sub for live run progress.

Stands in for the Redis pub/sub channel described in
docs/validation-platform/03-arsitektur.md §2.3 — sufficient for a
single-process dev/demo deployment (see README "Deviations from the target
architecture"). Swap for Redis if the app ever runs as more than one worker
process, since this state does not cross process boundaries.
"""
from __future__ import annotations

import threading
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[int, list[dict[str, Any]]] = {}
        self._done: set[int] = set()
        self._cancel_requested: set[int] = set()

    def publish(self, run_id: int, event: dict[str, Any]) -> None:
        with self._lock:
            self._events.setdefault(run_id, []).append(event)

    def get_since(self, run_id: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            events = self._events.get(run_id, [])
            return list(events[offset:]), len(events)

    def mark_done(self, run_id: int) -> None:
        with self._lock:
            self._done.add(run_id)

    def is_done(self, run_id: int) -> bool:
        with self._lock:
            return run_id in self._done

    def clear(self, run_id: int) -> None:
        with self._lock:
            self._events.pop(run_id, None)
            self._done.discard(run_id)
            self._cancel_requested.discard(run_id)

    def request_cancel(self, run_id: int) -> None:
        with self._lock:
            self._cancel_requested.add(run_id)

    def is_cancel_requested(self, run_id: int) -> bool:
        with self._lock:
            return run_id in self._cancel_requested


bus = EventBus()
