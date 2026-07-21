"""Regression coverage for a real user complaint: tables that ended in
status "error" showed NO reason anywhere in the UI. Two layered causes,
both fixed and covered here:

1. `_persist_table_result` never copied `result.error` into `RunTable.error`
   -- `tiered.run_table` catches every table-level exception itself and
   returns a NORMAL result object with status="ERROR" and the reason in
   `.error`, so the `err is not None` branch in `_execute_run` (which does
   set rt.error) almost never fired. Errored tables landed in the DB with
   status "error" and a NULL error message.
2. Even with the message, there was no CONTEXT: progress events (which
   phase it was in, retries, chunk checkpoints) only lived in the in-memory
   event bus, gone the moment the run ended. Now a bounded trail (last 200
   events) is persisted per table as `RunTable.event_log`, with the full
   traceback appended as the final entry on error -- surfaced in the
   drilldown's new "Log" tab.
"""
from __future__ import annotations

import importlib
import sqlite3
import time
from pathlib import Path

from conftest import reload_app_with_fresh_db


def _make_app(tmp_path: Path):
    from cryptography.fernet import Fernet

    db_module, models_module = reload_app_with_fresh_db(
        f"sqlite:///{tmp_path / 'errlog_test.sqlite'}",
        secret_key=Fernet.generate_key().decode("ascii"),
    )
    import app.main as main_module
    importlib.reload(main_module)

    from fastapi.testclient import TestClient
    return TestClient(main_module.app), db_module, models_module


def _run_config_with_bad_table(tmp_path, db_module, models_module):
    """Config with one GOOD table pair and one whose source table doesn't
    exist -- the run must finish with 1 pass + 1 error."""
    src_path, tgt_path = tmp_path / "src.sqlite", tmp_path / "tgt.sqlite"
    for p in (src_path, tgt_path):
        conn = sqlite3.connect(p)
        conn.execute("CREATE TABLE good_t (id INTEGER PRIMARY KEY, v REAL, d TEXT)")
        conn.executemany("INSERT INTO good_t VALUES (?,?,?)",
                         [(i, float(i), f"2025-0{(i % 9) + 1}-01") for i in range(1, 51)])
        conn.commit(); conn.close()

    db = db_module.SessionLocal()
    try:
        source_conn = models_module.Connection(name="src", engine="sqlite", database=str(src_path))
        target_conn = models_module.Connection(name="tgt", engine="sqlite", database=str(tgt_path))
        db.add(source_conn); db.add(target_conn)
        db.commit(); db.refresh(source_conn); db.refresh(target_conn)

        config = models_module.ValidationConfig(
            name="Error-log test", source_connection_id=source_conn.id,
            target_connection_id=target_conn.id, default_mode="tiered",
            settings={"retry_max": 1},  # don't waste time retrying a table that can never exist
        )
        db.add(config); db.commit(); db.refresh(config)
        db.add(models_module.ConfigTable(
            config_id=config.id, source_table="good_t", target_table="good_t",
            key_columns=["id"], chunk_column="id", date_column="d",
        ))
        db.add(models_module.ConfigTable(
            config_id=config.id, source_table="does_not_exist", target_table="also_missing",
            key_columns=["id"],
        ))
        db.commit()

        from app.services import run_service
        run = run_service.create_run(db, config, mode="tiered", trigger_type="manual")
        run_service.start_run_async(run.id)
        for _ in range(120):
            db.refresh(run)
            if run.status not in ("queued", "running"):
                break
            time.sleep(0.25)
        assert run.status == "completed", f"run ended {run.status}: {run.error}"

        tables = {rt.source_table: rt for rt in run.tables}
        return run, tables
    finally:
        db.close()


def test_errored_table_persists_reason_and_event_log(tmp_path):
    client, db_module, models_module = _make_app(tmp_path)
    with client:
        run, tables = _run_config_with_bad_table(tmp_path, db_module, models_module)

        db = db_module.SessionLocal()
        try:
            bad = db.query(models_module.RunTable).filter_by(
                run_id=run.id, source_table="does_not_exist").one()
            good = db.query(models_module.RunTable).filter_by(
                run_id=run.id, source_table="good_t").one()

            assert bad.status == "error"
            # (1) the reason itself -- previously NULL
            assert bad.error, "errored table must persist WHY it errored"
            assert "does_not_exist" in bad.error or "not found" in bad.error.lower()
            # (2) the trail, ending in a full traceback
            assert bad.event_log, "errored table must persist its event trail"
            kinds = [e["kind"] for e in bad.event_log]
            assert kinds[-1] == "traceback"
            assert "Traceback" in bad.event_log[-1]["message"]
            assert all({"ts", "kind", "message"} <= set(e.keys()) for e in bad.event_log)

            # the good table gets a trail too (phases), no traceback, no error
            assert good.status == "pass"
            assert good.error is None
            assert good.event_log
            assert all(e["kind"] != "traceback" for e in good.event_log)
        finally:
            db.close()


def test_log_tab_renders_error_and_traceback(tmp_path):
    client, db_module, models_module = _make_app(tmp_path)
    with client:
        run, _tables = _run_config_with_bad_table(tmp_path, db_module, models_module)
        r = client.post("/login", data={"email": "admin@lidvalid.local", "password": "admin123"})
        assert r.status_code in (200, 303)

        db = db_module.SessionLocal()
        try:
            bad = db.query(models_module.RunTable).filter_by(
                run_id=run.id, source_table="does_not_exist").one()
            bad_id = bad.id
        finally:
            db.close()

        r = client.get(f"/runs/{run.id}/tables/{bad_id}?tab=log")
        assert r.status_code == 200
        assert "Error:" in r.text            # the flash banner with rt.error
        assert "traceback" in r.text          # the trail's final entry kind
        assert "Traceback (most recent call last)" in r.text