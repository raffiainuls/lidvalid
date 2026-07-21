"""Regression coverage for a real incident: a genuinely long-running
validation (68 real ClickHouse tables) was mistaken for a stuck/dead process
because `run_tables.progress` only updates at table COMPLETION, not
mid-flight -- so a table that's actively being processed looks identical
("progress=0.0") to one whose owning process crashed hours ago. That
ambiguity led to killing a live server (destroying real in-progress work)
AND separately caused `sqlite3.OperationalError: database is locked` /
Internal Server Error on ordinary page loads while the long write was open.

Two independent fixes, two independent tests here:
1. WAL mode + busy_timeout (database.py) -- a concurrent read must succeed
   while a write transaction is open, not block/error.
2. reap_orphaned_runs() (run_service.py) -- a Run stuck "running"/"queued"
   at process startup (owning thread died with the previous process) gets
   marked "failed" with a clear message instead of looking alive forever.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from conftest import reload_app_with_fresh_db


def _make_isolated_db(tmp_path: Path):
    return reload_app_with_fresh_db(f"sqlite:///{tmp_path / 'run_service_test.sqlite'}")


def test_wal_mode_lets_a_concurrent_read_through_a_held_write(tmp_path):
    db_module, models_module = _make_isolated_db(tmp_path)

    errors = []

    def slow_writer():
        db = db_module.SessionLocal()
        try:
            db.add(models_module.Connection(name="writer-conn", engine="sqlite", database="x"))
            db.flush()  # opens the write transaction without committing yet
            time.sleep(2)
            db.commit()
        finally:
            db.close()

    def reader():
        time.sleep(0.5)  # start while the writer's transaction is still open
        db = db_module.SessionLocal()
        try:
            db.query(models_module.User).count()
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
        finally:
            db.close()

    w = threading.Thread(target=slow_writer)
    r = threading.Thread(target=reader)
    w.start(); r.start()
    w.join(); r.join()

    assert errors == [], f"concurrent read was blocked/errored despite WAL mode: {errors}"


def test_reap_orphaned_runs_marks_stuck_run_and_its_tables_failed(tmp_path):
    db_module, models_module = _make_isolated_db(tmp_path)
    from app.services import run_service

    db = db_module.SessionLocal()
    try:
        conn = models_module.Connection(name="c", engine="sqlite", database="x")
        db.add(conn); db.commit(); db.refresh(conn)
        config = models_module.ValidationConfig(
            name="cfg", source_connection_id=conn.id, target_connection_id=conn.id,
        )
        db.add(config); db.commit(); db.refresh(config)

        run = models_module.Run(config_id=config.id, status="running")
        db.add(run); db.commit(); db.refresh(run)

        # Mirrors the real incident: some tables already finished, most were
        # still "running" (mid-chunk, progress never updates until done) when
        # the owning process died.
        db.add(models_module.RunTable(run_id=run.id, source_table="a", target_table="a", status="pass"))
        db.add(models_module.RunTable(run_id=run.id, source_table="b", target_table="b", status="fail"))
        db.add(models_module.RunTable(run_id=run.id, source_table="c", target_table="c", status="running", progress=0.0))
        db.add(models_module.RunTable(run_id=run.id, source_table="d", target_table="d", status="pending"))
        db.commit()

        reaped = run_service.reap_orphaned_runs(db)
        assert reaped == 1

        db.refresh(run)
        assert run.status == "failed"
        assert "diinterupsi" in run.error.lower()
        assert run.summary == {"tables_total": 4, "pass": 1, "fail": 1, "error": 2}

        statuses = {rt.source_table: rt.status for rt in run.tables}
        assert statuses["a"] == "pass"   # already-finished tables are left untouched
        assert statuses["b"] == "fail"
        assert statuses["c"] == "error"  # was "running" -> reaped
        assert statuses["d"] == "error"  # was "pending" -> reaped
        for rt in run.tables:
            if rt.source_table in ("c", "d"):
                assert "diinterupsi" in rt.error.lower()
    finally:
        db.close()


def _make_resume_fixture(tmp_path, monkeypatch):
    """A finished run with one table per interesting status:
    a=pass, b=fail, c=error, d=cancelled, e=pass."""
    db_module, models_module = _make_isolated_db(tmp_path)
    from app.services import run_service

    # resume_run fires the new run's background thread immediately; these
    # tests only assert on WHICH tables got selected, so stub it out rather
    # than let a thread churn against the fake "x" connection.
    monkeypatch.setattr(run_service, "start_run_async", lambda run_id: None)

    db = db_module.SessionLocal()
    conn = models_module.Connection(name="c", engine="sqlite", database="x")
    db.add(conn); db.commit(); db.refresh(conn)
    config = models_module.ValidationConfig(
        name="cfg", source_connection_id=conn.id, target_connection_id=conn.id,
    )
    db.add(config); db.commit(); db.refresh(config)
    for name in ("a", "b", "c", "d", "e"):
        db.add(models_module.ConfigTable(
            config_id=config.id, source_table=name, target_table=name, key_columns=["id"],
        ))
    db.commit()

    run = models_module.Run(config_id=config.id, status="failed")
    db.add(run); db.commit(); db.refresh(run)
    for name, status in (("a", "pass"), ("b", "fail"), ("c", "error"), ("d", "cancelled"), ("e", "pass")):
        db.add(models_module.RunTable(run_id=run.id, source_table=name, target_table=name, status=status))
    db.commit()
    return db, run, run_service


def test_resume_scopes_select_the_right_tables(tmp_path, monkeypatch):
    """Resume evolved twice on user request; final form is an explicit
    per-click scope choice: all / fail / error / non_pass (default)."""
    db, run, run_service = _make_resume_fixture(tmp_path, monkeypatch)
    try:
        cases = {
            "non_pass": ["b", "c", "d"],   # fail + error + cancelled, skip pass
            "fail": ["b"],
            "error": ["c"],
            "all": ["a", "b", "c", "d", "e"],
        }
        for scope, expected in cases.items():
            new_run = run_service.resume_run(db, run, scope=scope)
            got = sorted(rt.source_table for rt in new_run.tables)
            assert got == expected, f"scope={scope}: {got}"
            assert new_run.trigger_type == "revalidate"

        # unknown scope falls back to non_pass rather than crashing or
        # silently running everything
        fallback = run_service.resume_run(db, run, scope="whatever")
        assert sorted(rt.source_table for rt in fallback.tables) == ["b", "c", "d"]
    finally:
        db.close()


def test_resume_with_empty_scope_selection_creates_nothing(tmp_path, monkeypatch):
    """"Hanya ERROR" on a run with zero error tables must return None (the
    route flashes a message), NOT fall through to re-running every table --
    the pre-scope code's `table_filter=remaining or None` did exactly that
    silent-full-run fallback on an empty list."""
    db, run, run_service = _make_resume_fixture(tmp_path, monkeypatch)
    try:
        # flip the only error/cancelled tables to pass so "error" matches nothing
        for rt in run.tables:
            if rt.status in ("error", "cancelled"):
                rt.status = "pass"
        db.commit()

        assert run_service.resume_run(db, run, scope="error") is None
        # sanity: a scope that DOES match still works on the same run
        new_run = run_service.resume_run(db, run, scope="fail")
        assert sorted(rt.source_table for rt in new_run.tables) == ["b"]
    finally:
        db.close()


def test_reap_orphaned_runs_is_a_noop_when_nothing_is_stuck(tmp_path):
    db_module, models_module = _make_isolated_db(tmp_path)
    from app.services import run_service

    db = db_module.SessionLocal()
    try:
        conn = models_module.Connection(name="c", engine="sqlite", database="x")
        db.add(conn); db.commit(); db.refresh(conn)
        config = models_module.ValidationConfig(
            name="cfg", source_connection_id=conn.id, target_connection_id=conn.id,
        )
        db.add(config); db.commit(); db.refresh(config)
        db.add(models_module.Run(config_id=config.id, status="completed"))
        db.commit()

        assert run_service.reap_orphaned_runs(db) == 0
    finally:
        db.close()
