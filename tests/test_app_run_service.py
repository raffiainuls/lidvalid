"""Regression test for a real cross-thread SQLAlchemy bug found while
building the demo: `_execute_run` read `source_conn.database` / `run.mode`
(ORM attributes) from inside worker threads. SQLAlchemy expires attributes on
`db.commit()`, so a later read lazy-reloads via the Session -- and a Session
is not safe for concurrent use from multiple threads. Two tables validated in
parallel could corrupt row data (`IndexError: tuple index out of range` deep
in SQLAlchemy's cython row processing), non-deterministically. Fixed by
capturing plain strings before opening the thread pool. This test runs two
tables concurrently (table_concurrency=2) against real sqlite files, several
times, to make sure the race is actually gone and not just "usually fine".
"""
from __future__ import annotations

import importlib
import sqlite3
import time
from pathlib import Path

from conftest import reload_app_with_fresh_db


def _make_app(tmp_path: Path):
    from cryptography.fernet import Fernet

    db_module, _models_module = reload_app_with_fresh_db(
        f"sqlite:///{tmp_path / 'app_test.sqlite'}",
        secret_key=Fernet.generate_key().decode("ascii"),
    )
    import app.main as main_module
    importlib.reload(main_module)

    from fastapi.testclient import TestClient
    return TestClient(main_module.app), db_module, main_module


def _build_pair(tmp_path: Path, n_tables: int):
    src_path = tmp_path / "src.sqlite"
    tgt_path = tmp_path / "tgt.sqlite"
    src = sqlite3.connect(src_path)
    tgt = sqlite3.connect(tgt_path)
    for i in range(n_tables):
        src.execute(f"CREATE TABLE t{i} (id INTEGER PRIMARY KEY, v REAL, d TEXT)")
        tgt.execute(f"CREATE TABLE raw_t{i} (id INTEGER PRIMARY KEY, v REAL, d TEXT)")
        rows = [(j, float(j), f"2025-0{(j % 9) + 1}-01") for j in range(1, 101)]
        src.executemany(f"INSERT INTO t{i} VALUES (?,?,?)", rows)
        tgt.executemany(f"INSERT INTO raw_t{i} VALUES (?,?,?)", rows)  # identical -> all should PASS
    src.commit(); tgt.commit()
    src.close(); tgt.close()
    return src_path, tgt_path


def test_concurrent_multi_table_run_does_not_corrupt_via_shared_session(tmp_path):
    client, db_module, main_module = _make_app(tmp_path)
    with client:
        src_path, tgt_path = _build_pair(tmp_path, n_tables=4)

        r = client.post("/login", data={"email": "admin@lidvalid.local", "password": "admin123"})
        assert r.status_code in (200, 303)

        from app import models

        db = db_module.SessionLocal()
        try:
            source_conn = models.Connection(name="src", engine="sqlite", database=str(src_path))
            target_conn = models.Connection(name="tgt", engine="sqlite", database=str(tgt_path))
            db.add(source_conn); db.add(target_conn)
            db.commit()
            db.refresh(source_conn); db.refresh(target_conn)

            config = models.ValidationConfig(
                name="Concurrency test", source_connection_id=source_conn.id,
                target_connection_id=target_conn.id, default_mode="tiered",
                settings={"table_concurrency": 4},
            )
            db.add(config)
            db.commit()
            db.refresh(config)

            for i in range(4):
                db.add(models.ConfigTable(
                    config_id=config.id, source_table=f"t{i}", target_table=f"raw_t{i}",
                    key_columns=["id"], chunk_column="id", date_column="d",
                ))
            db.commit()

            from app.services import run_service
            run = run_service.create_run(db, config, mode="tiered", trigger_type="manual")
            run_service.start_run_async(run.id)

            for _ in range(60):
                db.refresh(run)
                if run.status not in ("queued", "running"):
                    break
                time.sleep(0.25)

            assert run.status == "completed", f"run did not complete cleanly: {run.status} / {run.error}"
            run_tables = db.query(models.RunTable).filter_by(run_id=run.id).all()
            errors = [(rt.source_table, rt.error) for rt in run_tables if rt.status == "error"]
            assert errors == [], f"tables failed with ERROR status (the bug this test guards against): {errors}"
            assert all(rt.status == "pass" for rt in run_tables), \
                f"expected all 4 identical tables to PASS: {[(rt.source_table, rt.status) for rt in run_tables]}"
        finally:
            db.close()
