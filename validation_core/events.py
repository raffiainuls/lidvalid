"""Progress events emitted by the row-level chunk loop and the tiered runner.

Lives at the package top level (not inside `runner/`) so `rowlevel` can depend
on it without creating a `rowlevel <-> runner` import cycle (`runner.tiered`
depends on `rowlevel.runner`, which needs these event types).

Consumed by the FastAPI layer (app/services/run_service.py) to update
`run_tables.progress`, append to `run_events`, and push to the in-process
SSE/polling event bus. Kept dependency-free (no DB, no web) so validation_core
stays usable standalone / from a CLI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

EventKind = str  # 'phase' | 'checkpoint' | 'heartbeat' | 'retry' | 'table_started' | 'table_done'


@dataclass
class ProgressEvent:
    kind: EventKind
    message: str
    data: dict[str, Any] = field(default_factory=dict)


OnEvent = Callable[[ProgressEvent], None]


def noop_on_event(_event: ProgressEvent) -> None:
    pass
