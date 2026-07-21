"""Transient-error retry — ported from `validation_database/validate_batch.py`.

The legacy batch runner retried a whole table up to 3 times with a
20*attempt second backoff when the failure looked like a network blip (VPN
drop, connection reset) rather than a real data/config problem, so one
transient hiccup didn't fail the rest of a long batch.
"""
from __future__ import annotations

import time
from typing import Callable, TypeVar

from ..models import RunSettings

T = TypeVar("T")

_TRANSIENT_MARKERS = (
    "timed out", "timeout", "can't connect", "connection",
    "max retries", "unreachable", "broken",
    # ClickHouse HTTP stream desync/cut mid-transfer (see
    # connectors/clickhouse.py::_stream_failure_msg) -- the connector
    # already self-heals with a fresh client; if it STILL fails, one more
    # whole-table retry is worth it before declaring ERROR.
    "stream",
)


def is_transient_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _TRANSIENT_MARKERS)


def run_with_retry(
    fn: Callable[[], T],
    settings: RunSettings,
    on_retry: Callable[[int, Exception, float], None] | None = None,
) -> tuple[T, int]:
    """Run `fn()`, retrying on transient errors. Returns (result, attempts_used).
    Raises the last exception if every attempt fails (or the error isn't
    classified as transient)."""
    last_err: Exception | None = None
    for attempt in range(1, settings.retry_max + 1):
        try:
            return fn(), attempt
        except Exception as exc:  # noqa: BLE001 - re-raised below if not retryable
            last_err = exc
            if is_transient_error(exc) and attempt < settings.retry_max:
                wait = settings.retry_backoff_seconds * attempt
                if on_retry:
                    on_retry(attempt, exc, wait)
                time.sleep(wait)
                continue
            raise
    raise last_err  # pragma: no cover - unreachable, satisfies type checkers
