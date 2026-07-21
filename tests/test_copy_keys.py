"""Coverage for the "Copy key bermasalah" feature (Missing Keys / Value
Diffs tabs): explicit user request -- they run a manual re-insert script
against the pipeline once they know which rows are missing/differing
(`WHERE id IN (...)`), and needed every affected key, not just the current
page of the (paginated) Missing Keys/Value Diffs tabs.
"""
from __future__ import annotations

import importlib
from pathlib import Path

from conftest import reload_app_with_fresh_db

from app.routers.ui import _format_keys_for_sql


def _make_app(tmp_path: Path):
    from cryptography.fernet import Fernet

    db_module, models_module = reload_app_with_fresh_db(
        f"sqlite:///{tmp_path / 'copykeys_test.sqlite'}",
        secret_key=Fernet.generate_key().decode("ascii"),
    )
    import app.main as main_module
    importlib.reload(main_module)

    from fastapi.testclient import TestClient
    return TestClient(main_module.app), db_module, models_module


def _login(client):
    r = client.post("/login", data={"email": "admin@lidvalid.local", "password": "admin123"})
    assert r.status_code in (200, 303)


def _seed_table(db_module, models_module, key_columns=None):
    db = db_module.SessionLocal()
    try:
        conn = models_module.Connection(name="c", engine="sqlite", database="x")
        db.add(conn); db.commit(); db.refresh(conn)
        config = models_module.ValidationConfig(
            name="cfg", source_connection_id=conn.id, target_connection_id=conn.id,
        )
        db.add(config); db.commit(); db.refresh(config)
        run = models_module.Run(config_id=config.id, status="completed")
        db.add(run); db.commit(); db.refresh(run)
        rt = models_module.RunTable(
            run_id=run.id, source_table="t", target_table="t", status="fail",
            rl_metrics={"key_columns": key_columns or ["id"]},
        )
        db.add(rt); db.commit(); db.refresh(rt)
        return run.id, rt.id, db_module.SessionLocal
    finally:
        db.close()


class TestFormatKeysForSql:
    def test_numeric_keys_unquoted(self):
        # _format_keys_for_sql only formats -- sorting the distinct set is
        # the route's job (it queries the DB and hands over an already
        # sorted list), so this is passed pre-sorted like the real caller.
        out = _format_keys_for_sql(["1", "2", "3"], ["id"])
        assert out == "1,\n2,\n3"

    def test_string_keys_quoted(self):
        out = _format_keys_for_sql(["a", "b"], ["code"])
        assert out == "'a',\n'b'"

    def test_composite_key_gets_header_and_is_not_split(self):
        out = _format_keys_for_sql(["1_2", "3_4"], ["order_id", "material_id"])
        assert out.startswith("-- composite key (order_id + material_id")
        assert "1_2" in out and "3_4" in out
        # NOT split into tuples -- the join is lossy, so the raw value is
        # shown as-is rather than guessing a (possibly wrong) split.
        assert "(1, 2)" not in out


class TestKeysEndpoint:
    def test_missing_in_target_and_source_are_independent(self, tmp_path):
        client, db_module, models_module = _make_app(tmp_path)
        with client:
            run_id, rt_id, SessionLocal = _seed_table(db_module, models_module)
            _login(client)
            db = SessionLocal()
            try:
                for key in ("30", "10", "20"):
                    db.add(models_module.FindingRowLevel(
                        run_table_id=rt_id, finding_type="missing_in_target", row_key=key,
                    ))
                for key in ("99",):
                    db.add(models_module.FindingRowLevel(
                        run_table_id=rt_id, finding_type="missing_in_source", row_key=key,
                    ))
                db.commit()
            finally:
                db.close()

            r = client.get(f"/runs/{run_id}/tables/{rt_id}/keys?kind=missing_in_target")
            assert r.status_code == 200
            assert r.text == "10,\n20,\n30"

            r2 = client.get(f"/runs/{run_id}/tables/{rt_id}/keys?kind=missing_in_source")
            assert r2.text == "99"

    def test_value_diff_dedupes_keys_across_columns(self, tmp_path):
        """Key 1 differs in BOTH `amount` and `name` -- must appear ONCE in
        the "all columns" copy, not twice (the user wants unique row ids to
        re-insert, not one entry per differing column)."""
        client, db_module, models_module = _make_app(tmp_path)
        with client:
            run_id, rt_id, SessionLocal = _seed_table(db_module, models_module)
            _login(client)
            db = SessionLocal()
            try:
                db.add(models_module.FindingRowLevel(
                    run_table_id=rt_id, finding_type="value_diff", row_key="1",
                    column_name="amount", source_value="10", target_value="20"))
                db.add(models_module.FindingRowLevel(
                    run_table_id=rt_id, finding_type="value_diff", row_key="1",
                    column_name="name", source_value="a", target_value="b"))
                db.add(models_module.FindingRowLevel(
                    run_table_id=rt_id, finding_type="value_diff", row_key="2",
                    column_name="amount", source_value="1", target_value="2"))
                db.commit()
            finally:
                db.close()

            r = client.get(f"/runs/{run_id}/tables/{rt_id}/keys?kind=value_diff")
            assert r.text == "1,\n2"  # deduped, not "1,\n1,\n2"

            r_filtered = client.get(f"/runs/{run_id}/tables/{rt_id}/keys?kind=value_diff&column=name")
            assert r_filtered.text == "1"  # only key 1 differs on `name`

    def test_composite_key_table_shows_joined_value_with_header(self, tmp_path):
        client, db_module, models_module = _make_app(tmp_path)
        with client:
            run_id, rt_id, SessionLocal = _seed_table(
                db_module, models_module, key_columns=["order_id", "material_id"])
            _login(client)
            db = SessionLocal()
            try:
                db.add(models_module.FindingRowLevel(
                    run_table_id=rt_id, finding_type="missing_in_target", row_key="5_9"))
                db.commit()
            finally:
                db.close()

            r = client.get(f"/runs/{run_id}/tables/{rt_id}/keys?kind=missing_in_target")
            assert "order_id + material_id" in r.text
            assert "5_9" in r.text

    def test_unknown_kind_returns_400(self, tmp_path):
        client, db_module, models_module = _make_app(tmp_path)
        with client:
            run_id, rt_id, _ = _seed_table(db_module, models_module)
            _login(client)
            r = client.get(f"/runs/{run_id}/tables/{rt_id}/keys?kind=bogus")
            assert r.status_code == 400

    def test_wrong_run_id_returns_404(self, tmp_path):
        client, db_module, models_module = _make_app(tmp_path)
        with client:
            run_id, rt_id, _ = _seed_table(db_module, models_module)
            _login(client)
            r = client.get(f"/runs/{run_id + 999}/tables/{rt_id}/keys?kind=missing_in_target")
            assert r.status_code == 404

    def test_no_matching_findings_returns_placeholder_not_empty_string(self, tmp_path):
        client, db_module, models_module = _make_app(tmp_path)
        with client:
            run_id, rt_id, _ = _seed_table(db_module, models_module)
            _login(client)
            r = client.get(f"/runs/{run_id}/tables/{rt_id}/keys?kind=missing_in_target")
            assert r.status_code == 200
            assert "tidak ada key" in r.text.lower()
