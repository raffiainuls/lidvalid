"""Regression test for a real bug: clicking "Cancel" on a running Run did
nothing until the ENTIRE batch of tables finished on its own.

`_execute_run` (run_service.py) submits every table to a `ThreadPoolExecutor`
up front, then loops over `as_completed(futures)` -- which blocks until every
submitted future is done, full stop. The cancel flag was only checked before
submitting each table (a window that closes in milliseconds, long before a
user could ever click Cancel on a real run) and once more AFTER the
as_completed loop returned -- i.e. after every table had already run to
completion, making the check pointless.

Fixed by checking the cancel flag inside the as_completed loop and, on first
seeing it, calling `future.cancel()` on every future -- which only succeeds
for work that hasn't started yet (still queued behind the currently-running
batch, bounded by table_concurrency). Tables already mid-flight can't be
force-stopped (no cooperative cancellation inside validation_core), so they
still finish naturally, but everything queued behind them is skipped
immediately instead of waited on.

This test fakes `vc_run_table` to sleep instead of hitting real connectors,
so it can assert on timing without needing slow real data: with
table_concurrency=2 and N tables, cancelling shortly after start should
finish in roughly one slow-call's duration, not N/2. N is deliberately large
(20) relative to table_concurrency so the "broken" and "fixed" timings are
far enough apart (~10s vs a couple seconds) that the assertion has real
margin against CPU scheduling noise under load -- a tight threshold here was
observed to flake on a loaded machine (a few extra tables slipping into
flight before the cancel flag was observed pushed a 2-3-table wait past a
tight bound, even though the fix was working correctly).

A SECOND, independent bug surfaced while chasing that flakiness: on a run
where cancel is requested before ANY table gets submitted (the submit loop's
own `if is_cancel_requested: break` -- reachable in production whenever the
thread pool is saturated or cancel just happens to land that early), every
RunTable falls to the OLDER cleanup path below the executor block:
`for rt in run_tables: db.refresh(rt); if rt.status == "running": rt.status
= "cancelled"`. That flip was never actually reaching the database: the
very next call, `run.summary = _summarize_run(run_tables, db)`, does its own
`db.refresh(rt)` per table BEFORE the flip was committed, silently
reloading the stale pre-cancel "running" status and discarding it -- same
pitfall `reap_orphaned_runs` was written to avoid elsewhere in this file,
just missed here. Fixed with an extra `db.commit()` right after the flip,
before `_summarize_run` runs. `test_cancel_before_any_table_starts_marks_everything_cancelled`
below reproduces this deterministically (cancel requested before
`start_run_async` is even called) instead of relying on timing luck.
"""
from __future__ import annotations

import importlib
import threading
import time
from pathlib import Path

from conftest import reload_app_with_fresh_db

SLOW_SECONDS = 1.0


class _FakeResult:
    def __init__(self):
        self.status = "pass"
        self.tier_reached = 1
        self.mode = "aggregate"
        self.attempts = 1
        self.queries = {}
        self.aggregate_summary = None
        self.rowlevel = None
        self.error = None
        self.error_trace = None


def _make_isolated_db(tmp_path: Path):
    return reload_app_with_fresh_db(f"sqlite:///{tmp_path / 'cancel_test.sqlite'}")


def test_cancel_stops_not_yet_started_tables_instead_of_waiting_for_all(tmp_path, monkeypatch):
    db_module, models_module = _make_isolated_db(tmp_path)
    import app.services.run_service as run_service

    def fake_vc_run_table(src, tgt, source_db_name, target_db_name, table_spec, run_mode, settings, on_event):
        time.sleep(SLOW_SECONDS)
        return _FakeResult()

    monkeypatch.setattr(run_service, "vc_run_table", fake_vc_run_table)

    db = db_module.SessionLocal()
    try:
        src_path, tgt_path = tmp_path / "src.sqlite", tmp_path / "tgt.sqlite"
        source_conn = models_module.Connection(name="src", engine="sqlite", database=str(src_path))
        target_conn = models_module.Connection(name="tgt", engine="sqlite", database=str(tgt_path))
        db.add(source_conn); db.add(target_conn)
        db.commit(); db.refresh(source_conn); db.refresh(target_conn)

        config = models_module.ValidationConfig(
            name="Cancel test", source_connection_id=source_conn.id,
            target_connection_id=target_conn.id, default_mode="aggregate",
            settings={"table_concurrency": 2},
        )
        db.add(config); db.commit(); db.refresh(config)

        n_tables = 20
        for i in range(n_tables):
            db.add(models_module.ConfigTable(
                config_id=config.id, source_table=f"t{i}", target_table=f"raw_t{i}",
                key_columns=["id"], chunk_column="id", date_column=None,
            ))
        db.commit()

        run = run_service.create_run(db, config, mode="aggregate", trigger_type="manual")
        started_at = time.monotonic()
        run_service.start_run_async(run.id)

        # Give the first table_concurrency=2 workers time to actually start
        # (enter time.sleep) before requesting cancel -- this is the realistic
        # case: a user cancelling a run that's already been going a while,
        # not the tiny window right at submission time.
        time.sleep(0.2)
        run_service.bus.request_cancel(run.id)

        for _ in range(100):
            db.refresh(run)
            if run.status not in ("queued", "running"):
                break
            time.sleep(0.1)
        elapsed = time.monotonic() - started_at

        assert run.status == "cancelled", f"run did not end cancelled: {run.status}"
        # Old (broken) behavior waits for all 20 tables serialized 2-at-a-time
        # ~= 10s minimum; fixed behavior only waits for however many were
        # already in flight (2, or a handful more under scheduling noise)
        # when cancel was observed. The threshold sits far below the broken
        # minimum on purpose, so occasional scheduling noise (a table or two
        # slipping into flight before cancel registers) can't flip the result.
        assert elapsed < SLOW_SECONDS * 8, (
            f"cancel took {elapsed:.2f}s -- should stop after the in-flight batch, "
            f"not wait for all {n_tables} tables"
        )

        # `db` already loaded these RunTable rows (as "pending"/"running")
        # back when the run was created above -- SQLAlchemy's identity map
        # means a plain query() returns those SAME cached Python objects
        # as-is instead of re-fetching, so without expiring them first this
        # reads back the STALE pre-run values even though the background
        # thread (a separate Session) already committed their real final
        # status. `db.refresh(run)` above dodges this because refresh()
        # forces a reload for that one object; a plain query doesn't extend
        # the same courtesy to everything else already in the map.
        db.expire_all()
        run_tables = db.query(models_module.RunTable).filter_by(run_id=run.id).all()
        statuses = {rt.source_table: rt.status for rt in run_tables}
        cancelled_count = sum(1 for s in statuses.values() if s == "cancelled")
        completed_count = sum(1 for s in statuses.values() if s == "pass")

        # Only whichever handful of tables were already in flight (bounded by
        # table_concurrency=2, though scheduling/CPU load can let a couple
        # more slip in before the cancel flag is observed) finish naturally;
        # the rest -- still queued, never started -- get cancelled. The exact
        # split isn't deterministic under load, but cancel must stop MOST of
        # the batch, not none of it (that was the bug: it used to be 0).
        assert completed_count + cancelled_count == n_tables, statuses
        assert cancelled_count > 0, f"cancel had no effect at all: {statuses}"
        assert completed_count <= n_tables // 2, (
            f"too many tables ran to completion after cancel was requested: {statuses}"
        )
    finally:
        db.close()


def test_cancel_before_any_table_starts_marks_everything_cancelled(tmp_path, monkeypatch):
    db_module, models_module = _make_isolated_db(tmp_path)
    import app.services.run_service as run_service

    def fake_vc_run_table(src, tgt, source_db_name, target_db_name, table_spec, run_mode, settings, on_event):
        time.sleep(SLOW_SECONDS)
        return _FakeResult()

    monkeypatch.setattr(run_service, "vc_run_table", fake_vc_run_table)

    db = db_module.SessionLocal()
    try:
        src_path, tgt_path = tmp_path / "src.sqlite", tmp_path / "tgt.sqlite"
        source_conn = models_module.Connection(name="src", engine="sqlite", database=str(src_path))
        target_conn = models_module.Connection(name="tgt", engine="sqlite", database=str(tgt_path))
        db.add(source_conn); db.add(target_conn)
        db.commit(); db.refresh(source_conn); db.refresh(target_conn)

        config = models_module.ValidationConfig(
            name="Cancel-before-start test", source_connection_id=source_conn.id,
            target_connection_id=target_conn.id, default_mode="aggregate",
            settings={"table_concurrency": 2},
        )
        db.add(config); db.commit(); db.refresh(config)

        n_tables = 5
        for i in range(n_tables):
            db.add(models_module.ConfigTable(
                config_id=config.id, source_table=f"t{i}", target_table=f"raw_t{i}",
                key_columns=["id"], chunk_column="id", date_column=None,
            ))
        db.commit()

        run = run_service.create_run(db, config, mode="aggregate", trigger_type="manual")
        # Deterministically reproduces the "nothing ever got submitted" path:
        # cancel is already flagged before the background thread's submit
        # loop runs its first `is_cancel_requested` check, so every table
        # breaks out immediately and none ever reach the ThreadPoolExecutor.
        run_service.bus.request_cancel(run.id)
        run_service.start_run_async(run.id)

        for _ in range(50):
            db.refresh(run)
            if run.status not in ("queued", "running"):
                break
            time.sleep(0.1)

        assert run.status == "cancelled", f"run did not end cancelled: {run.status}"
        db.expire_all()
        run_tables = db.query(models_module.RunTable).filter_by(run_id=run.id).all()
        statuses = {rt.source_table: rt.status for rt in run_tables}
        assert all(s == "cancelled" for s in statuses.values()), (
            f"tables never submitted should all be 'cancelled', not left as 'running': {statuses}"
        )
        assert run.summary["cancelled"] == n_tables, run.summary
    finally:
        db.close()
