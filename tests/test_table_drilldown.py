"""Regression test for a real perf incident: the table-drilldown page (Missing
Keys / Value Diffs tabs) used to load a run_table's ENTIRE `rowlevel_findings`
relationship via the ORM -- up to `rowlevel_sample_cap` (10k) rows per finding
type -- and render every single row into one HTML response, on every tab view
(even "ringkasan", which doesn't display them at all). Under load (a real
68-table ClickHouse run running concurrently in the same process) a drilldown
page for a table with ~20k diffs took minutes to respond. Fixed by querying
only COUNTs/GROUP BY aggregates for badges, and paginating (200 rows/page) the
actual row fetch for whichever tab is currently being viewed.

These tests build enough findings to span multiple pages (250 > 200 page
size) and assert the route only returns one page's worth of rows at a time,
with the right totals/pagination metadata.
"""
from __future__ import annotations

import importlib
from pathlib import Path

from conftest import reload_app_with_fresh_db


def _make_app(tmp_path: Path):
    from cryptography.fernet import Fernet

    db_module, models_module = reload_app_with_fresh_db(
        f"sqlite:///{tmp_path / 'drilldown_test.sqlite'}",
        secret_key=Fernet.generate_key().decode("ascii"),
    )
    import app.main as main_module
    importlib.reload(main_module)

    from fastapi.testclient import TestClient
    return TestClient(main_module.app), db_module, models_module


def _seed_run_table(db_module, models_module, n_diffs: int, n_missing: int, name_suffix: str = ""):
    db = db_module.SessionLocal()
    try:
        conn = models_module.Connection(name=f"c{name_suffix}", engine="sqlite", database="x")
        db.add(conn); db.commit(); db.refresh(conn)
        config = models_module.ValidationConfig(
            name=f"cfg{name_suffix}", source_connection_id=conn.id, target_connection_id=conn.id,
        )
        db.add(config); db.commit(); db.refresh(config)
        run = models_module.Run(config_id=config.id, status="completed")
        db.add(run); db.commit(); db.refresh(run)
        rt = models_module.RunTable(run_id=run.id, source_table="t", target_table="t", status="fail")
        db.add(rt); db.commit(); db.refresh(rt)

        for i in range(n_diffs):
            col = "amount" if i % 2 == 0 else "name"
            db.add(models_module.FindingRowLevel(
                run_table_id=rt.id, finding_type="value_diff", row_key=f"key_{i:04d}",
                column_name=col, source_value="a", target_value="b",
            ))
        for i in range(n_missing):
            db.add(models_module.FindingRowLevel(
                run_table_id=rt.id, finding_type="missing_in_target", row_key=f"mk_{i:04d}",
            ))
        db.commit()
        return run.id, rt.id
    finally:
        db.close()


def _login(client):
    r = client.post("/login", data={"email": "admin@lidvalid.local", "password": "admin123"})
    assert r.status_code in (200, 303)


def test_diffs_tab_is_paginated_not_loaded_in_full(tmp_path):
    client, db_module, models_module = _make_app(tmp_path)
    with client:
        run_id, rt_id = _seed_run_table(db_module, models_module, n_diffs=250, n_missing=0)
        _login(client)

        r1 = client.get(f"/runs/{run_id}/tables/{rt_id}?tab=diffs&page=1")
        assert r1.status_code == 200
        assert "key_0000" in r1.text and "key_0199" in r1.text
        assert "key_0200" not in r1.text  # page 2 content must not leak into page 1
        assert "Value Diffs (250)" in r1.text
        assert "Halaman 1 / 2" in r1.text

        r2 = client.get(f"/runs/{run_id}/tables/{rt_id}?tab=diffs&page=2")
        assert r2.status_code == 200
        assert "key_0200" in r2.text and "key_0249" in r2.text
        assert "key_0000" not in r2.text
        assert "Halaman 2 / 2" in r2.text


def test_diffs_tab_column_filter_combines_with_pagination(tmp_path):
    client, db_module, models_module = _make_app(tmp_path)
    with client:
        run_id, rt_id = _seed_run_table(db_module, models_module, n_diffs=250, n_missing=0)
        _login(client)

        # even indices -> "amount" (125 rows), odd -> "name" (125 rows)
        r = client.get(f"/runs/{run_id}/tables/{rt_id}?tab=diffs&column=amount&page=1")
        assert r.status_code == 200
        assert "key_0000" in r.text  # amount, index 0
        assert "key_0001" not in r.text  # name, filtered out
        assert "key_0248" in r.text  # last "amount" row -- all 125 fit on one page
        assert "Halaman" not in r.text  # pager hides itself when everything fits on page 1


def test_missing_tab_is_paginated(tmp_path):
    client, db_module, models_module = _make_app(tmp_path)
    with client:
        run_id, rt_id = _seed_run_table(db_module, models_module, n_diffs=0, n_missing=210)
        _login(client)

        r1 = client.get(f"/runs/{run_id}/tables/{rt_id}?tab=missing&page=1")
        assert r1.status_code == 200
        assert "mk_0000" in r1.text and "mk_0199" in r1.text
        assert "mk_0200" not in r1.text
        assert "Missing Keys (210)" in r1.text

        r2 = client.get(f"/runs/{run_id}/tables/{rt_id}?tab=missing&page=2")
        assert "mk_0200" in r2.text and "mk_0209" in r2.text


def test_ringkasan_tab_does_not_touch_rowlevel_findings_at_all(tmp_path):
    """The summary tab reads rt.rl_metrics (a precomputed JSON blob), never
    the findings tables -- opening it on a heavily-mismatched table shouldn't
    pull any rowlevel rows at all."""
    client, db_module, models_module = _make_app(tmp_path)
    with client:
        run_id, rt_id = _seed_run_table(db_module, models_module, n_diffs=250, n_missing=210)
        _login(client)
        r = client.get(f"/runs/{run_id}/tables/{rt_id}?tab=ringkasan")
        assert r.status_code == 200
        assert "key_0000" not in r.text
        assert "mk_0000" not in r.text


def test_tipekolom_tab_shows_schema_comparison_and_flags_category_mismatch(tmp_path):
    client, db_module, models_module = _make_app(tmp_path)
    with client:
        run_id, rt_id = _seed_run_table(db_module, models_module, n_diffs=0, n_missing=0)
        _login(client)

        db = db_module.SessionLocal()
        try:
            rt = db.get(models_module.RunTable, rt_id)
            rt.column_type_details = [
                {"column": "id", "source_type": "int", "target_type": "Int64", "category_match": True},
                {"column": "amount", "source_type": "decimal", "target_type": "String", "category_match": False},
                {"column": "legacy_only", "source_type": "varchar", "target_type": None, "category_match": None},
            ]
            db.commit()
        finally:
            db.close()

        r = client.get(f"/runs/{run_id}/tables/{rt_id}?tab=tipekolom")
        assert r.status_code == 200
        assert "Tipe Kolom (1)" in r.text  # nav badge counts only the 1 real mismatch
        assert "id" in r.text and "int" in r.text and "Int64" in r.text
        assert "amount" in r.text and "decimal" in r.text and "String" in r.text
        assert "beda kategori" in r.text
        assert "legacy_only" in r.text

        # a fresh table with no column_type_details yet (e.g. an old run,
        # or one that never reached Report 3) must render without crashing
        run_id2, rt_id2 = _seed_run_table(db_module, models_module, n_diffs=0, n_missing=0, name_suffix="2")
        r2 = client.get(f"/runs/{run_id2}/tables/{rt_id2}?tab=tipekolom")
        assert r2.status_code == 200
        assert "Tipe Kolom (" not in r2.text  # no count badge when there's nothing to flag
        assert "Belum ada data tipe kolom" in r2.text


def test_periode_tab_shows_metric_detail_for_zero_delta_periods(tmp_path):
    """Real user confusion: periods with Δ=0 (row counts identical) still
    appeared in the mismatch list with no visible reason. The tab must show
    WHICH column/metric differed for those, and the nav badge must count
    distinct mismatched PERIODS, not the (now larger) number of finding rows."""
    client, db_module, models_module = _make_app(tmp_path)
    with client:
        run_id, rt_id = _seed_run_table(db_module, models_module, n_diffs=0, n_missing=0)
        _login(client)

        db = db_module.SessionLocal()
        try:
            rt = db.get(models_module.RunTable, rt_id)
            # 2025-05: Δ=0, only flagged via 2 metric findings (the exact
            # confusing case). 2025-11: genuine row-count diff, ALSO gets
            # its own metric finding -- 3 findings, 2 distinct periods.
            db.add(models_module.FindingAggregate(
                run_table_id=rt.id, category="period_monthly", period="2025-05",
                column_name="amount", metric="sum", source_value="1700000.0", target_value="2599999.0",
            ))
            db.add(models_module.FindingAggregate(
                run_table_id=rt.id, category="period_monthly", period="2025-05",
                column_name="amount", metric="max", source_value="196000.0", target_value="999999.0",
            ))
            db.add(models_module.FindingAggregate(
                run_table_id=rt.id, category="period_monthly", period="2025-11",
                source_value="16", target_value="15", difference=1.0,
            ))
            db.commit()
        finally:
            db.close()

        r = client.get(f"/runs/{run_id}/tables/{rt_id}?tab=periode")
        assert r.status_code == 200
        assert "Periode (2)" in r.text  # 2 distinct periods, not 3 findings
        assert "2025-05" in r.text and "2025-11" in r.text
        assert "amount" in r.text and "sum" in r.text and "max" in r.text
        assert "row count" in r.text  # the plain row-count finding for 2025-11
        assert "1700000.0" in r.text and "2599999.0" in r.text
