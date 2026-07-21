"""Coverage for the per-config "Status Tabel" page + per-table re-run.

The usability problem it solves: re-running a subset (resume scopes,
per-table re-run) creates a NEW Run containing only the re-run tables, so
no single run shows where every table stands -- users had to mentally merge
several partial runs. The status page shows, per table: latest status
across ALL runs + a run-by-run history, with a re-run button per row.
"""
from __future__ import annotations

import importlib
from pathlib import Path

from conftest import reload_app_with_fresh_db


def _make_app(tmp_path: Path):
    from cryptography.fernet import Fernet

    db_module, models_module = reload_app_with_fresh_db(
        f"sqlite:///{tmp_path / 'status_test.sqlite'}",
        secret_key=Fernet.generate_key().decode("ascii"),
    )
    import app.main as main_module
    importlib.reload(main_module)

    from fastapi.testclient import TestClient
    return TestClient(main_module.app), db_module, models_module


def _seed(db_module, models_module):
    """Config with tables a & b. Run 1 ran BOTH (a=fail, b=pass); run 2 was
    a partial re-run of only a (a=pass). So current truth: a=pass (run 2),
    b=pass (run 1) -- exactly the cross-run merge the page exists for."""
    db = db_module.SessionLocal()
    try:
        conn = models_module.Connection(name="c", engine="sqlite", database="x")
        db.add(conn); db.commit(); db.refresh(conn)
        cfg = models_module.ValidationConfig(
            name="status cfg", source_connection_id=conn.id, target_connection_id=conn.id,
        )
        db.add(cfg); db.commit(); db.refresh(cfg)
        for name in ("a", "b"):
            db.add(models_module.ConfigTable(
                config_id=cfg.id, source_table=name, target_table=f"raw_{name}", key_columns=["id"],
            ))
        db.commit()

        run1 = models_module.Run(config_id=cfg.id, status="completed")
        db.add(run1); db.commit(); db.refresh(run1)
        db.add(models_module.RunTable(run_id=run1.id, source_table="a", target_table="raw_a", status="fail"))
        db.add(models_module.RunTable(run_id=run1.id, source_table="b", target_table="raw_b", status="pass"))

        run2 = models_module.Run(config_id=cfg.id, status="completed", table_filter=["a"])
        db.add(run2); db.commit(); db.refresh(run2)
        db.add(models_module.RunTable(run_id=run2.id, source_table="a", target_table="raw_a", status="pass"))
        db.commit()
        return cfg.id, run1.id, run2.id
    finally:
        db.close()


def _login(client):
    r = client.post("/login", data={"email": "admin@lidvalid.local", "password": "admin123"})
    assert r.status_code in (200, 303)


def test_status_page_merges_latest_state_across_partial_runs(tmp_path):
    client, db_module, models_module = _make_app(tmp_path)
    with client:
        cfg_id, run1_id, run2_id = _seed(db_module, models_module)
        _login(client)

        r = client.get(f"/configs/{cfg_id}/status")
        assert r.status_code == 200
        # table a: latest = PASS from run 2 (the partial re-run), history has both runs
        assert f"/runs/{run2_id}/tables/" in r.text
        # table b: latest = PASS from run 1 (untouched by the partial re-run)
        assert f"/runs/{run1_id}/tables/" in r.text
        # no ROW is in the never-ran state (the KPI card label legitimately
        # contains the same phrase, so match the row-specific markup)
        assert '<span class="subtle">belum pernah run</span>' not in r.text
        # history chips: run1's FAIL for table a must still be visible
        assert "FAIL" in r.text


def test_per_table_rerun_creates_single_table_run_and_returns_to_status(tmp_path, monkeypatch):
    client, db_module, models_module = _make_app(tmp_path)
    with client:
        cfg_id, _r1, _r2 = _seed(db_module, models_module)
        _login(client)

        from app.services import run_service
        monkeypatch.setattr(run_service, "start_run_async", lambda run_id: None)

        r = client.post(f"/configs/{cfg_id}/rerun-table", data={"source_table": "a"}, follow_redirects=False)
        assert r.status_code == 303
        assert f"/configs/{cfg_id}/status" in r.headers["location"]  # back to the matrix, not the 1-table run

        db = db_module.SessionLocal()
        try:
            new_run = db.query(models_module.Run).order_by(models_module.Run.id.desc()).first()
            assert new_run.table_filter == ["a"]
            assert new_run.trigger_type == "revalidate"
            names = [rt.source_table for rt in new_run.tables]
            assert names == ["a"], f"single-table rerun must contain ONLY that table: {names}"
        finally:
            db.close()


def test_per_table_rerun_rejects_unknown_table(tmp_path, monkeypatch):
    client, db_module, models_module = _make_app(tmp_path)
    with client:
        cfg_id, _r1, _r2 = _seed(db_module, models_module)
        _login(client)

        from app.services import run_service
        monkeypatch.setattr(run_service, "start_run_async", lambda run_id: None)

        db = db_module.SessionLocal()
        try:
            before = db.query(models_module.Run).count()
        finally:
            db.close()

        r = client.post(f"/configs/{cfg_id}/rerun-table", data={"source_table": "nope"}, follow_redirects=False)
        assert r.status_code == 303
        assert "error=" in r.headers["location"]

        db = db_module.SessionLocal()
        try:
            assert db.query(models_module.Run).count() == before  # nothing created
        finally:
            db.close()
