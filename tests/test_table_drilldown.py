"""Regression test for a real perf incident: the table-drilldown page (Missing
Keys / Value Diffs tabs) used to load a run_table's ENTIRE `rowlevel_findings`
relationship via the ORM -- up to `rowlevel_sample_cap` (10k) rows per finding
type -- and render every single row into one HTML response, on every tab view
(even "ringkasan", which doesn't display them at all). Under load (a real
68-table ClickHouse run running concurrently in the same process) a drilldown
page for a table with ~20k diffs took minutes to respond. Fixed by querying
only COUNTs/GROUP BY aggregates for badges, and paginating (200 rows/page) the
actual row fetch for whichever tab is currently being viewed.

Ported to GET /api/runs/{id}/tables/{id} (core fields + counts only, no
rowlevel rows) and GET /api/runs/{id}/tables/{id}/rowlevel (paginated actual
rows) when the frontend moved to a React SPA -- same split the perf fix
already established, just exposed as two JSON endpoints instead of one HTML
page with server-side tabs.

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
    r = client.post("/api/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200


def test_diffs_rowlevel_is_paginated_not_loaded_in_full(tmp_path):
    client, db_module, models_module = _make_app(tmp_path)
    with client:
        run_id, rt_id = _seed_run_table(db_module, models_module, n_diffs=250, n_missing=0)
        _login(client)

        core = client.get(f"/api/runs/{run_id}/tables/{rt_id}")
        assert core.status_code == 200
        assert core.json()["total_diff_count"] == 250

        r1 = client.get(f"/api/runs/{run_id}/tables/{rt_id}/rowlevel?type=diffs&page=1")
        assert r1.status_code == 200
        d1 = r1.json()
        keys1 = {row["row_key"] for row in d1["rows"]}
        assert "key_0000" in keys1 and "key_0199" in keys1
        assert "key_0200" not in keys1  # page 2 content must not leak into page 1
        assert d1["page"] == 1 and d1["total_pages"] == 2

        r2 = client.get(f"/api/runs/{run_id}/tables/{rt_id}/rowlevel?type=diffs&page=2")
        assert r2.status_code == 200
        d2 = r2.json()
        keys2 = {row["row_key"] for row in d2["rows"]}
        assert "key_0200" in keys2 and "key_0249" in keys2
        assert "key_0000" not in keys2
        assert d2["page"] == 2 and d2["total_pages"] == 2


def test_diffs_rowlevel_column_filter_combines_with_pagination(tmp_path):
    client, db_module, models_module = _make_app(tmp_path)
    with client:
        run_id, rt_id = _seed_run_table(db_module, models_module, n_diffs=250, n_missing=0)
        _login(client)

        # even indices -> "amount" (125 rows), odd -> "name" (125 rows)
        r = client.get(f"/api/runs/{run_id}/tables/{rt_id}/rowlevel?type=diffs&column=amount&page=1")
        assert r.status_code == 200
        data = r.json()
        keys = {row["row_key"] for row in data["rows"]}
        assert "key_0000" in keys  # amount, index 0
        assert "key_0001" not in keys  # name, filtered out
        assert "key_0248" in keys  # last "amount" row -- all 125 fit on one page
        assert data["total_pages"] == 1  # everything fits on page 1


def test_missing_rowlevel_is_paginated(tmp_path):
    client, db_module, models_module = _make_app(tmp_path)
    with client:
        run_id, rt_id = _seed_run_table(db_module, models_module, n_diffs=0, n_missing=210)
        _login(client)

        core = client.get(f"/api/runs/{run_id}/tables/{rt_id}")
        assert core.json()["missing_count"] == 210

        r1 = client.get(f"/api/runs/{run_id}/tables/{rt_id}/rowlevel?type=missing&page=1")
        assert r1.status_code == 200
        keys1 = {row["row_key"] for row in r1.json()["rows"]}
        assert "mk_0000" in keys1 and "mk_0199" in keys1
        assert "mk_0200" not in keys1

        r2 = client.get(f"/api/runs/{run_id}/tables/{rt_id}/rowlevel?type=missing&page=2")
        keys2 = {row["row_key"] for row in r2.json()["rows"]}
        assert "mk_0200" in keys2 and "mk_0209" in keys2


def test_core_endpoint_does_not_return_rowlevel_findings_at_all(tmp_path):
    """The core drilldown endpoint reads counts/aggregates only, never the
    findings themselves -- fetching a heavily-mismatched table's summary
    shouldn't pull any rowlevel rows into the response at all."""
    client, db_module, models_module = _make_app(tmp_path)
    with client:
        run_id, rt_id = _seed_run_table(db_module, models_module, n_diffs=250, n_missing=210)
        _login(client)
        r = client.get(f"/api/runs/{run_id}/tables/{rt_id}")
        assert r.status_code == 200
        assert "key_0000" not in r.text
        assert "mk_0000" not in r.text


def test_tipekolom_shows_schema_comparison_and_flags_category_mismatch(tmp_path):
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

        r = client.get(f"/api/runs/{run_id}/tables/{rt_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["type_mismatch_count"] == 1  # counts only the 1 real mismatch
        by_col = {c["column"]: c for c in data["column_type_details"]}
        assert by_col["id"] == {"column": "id", "source_type": "int", "target_type": "Int64", "category_match": True}
        assert by_col["amount"]["category_match"] is False
        assert by_col["legacy_only"]["category_match"] is None

        # a fresh table with no column_type_details yet (e.g. an old run,
        # or one that never reached Report 3) must render without crashing
        run_id2, rt_id2 = _seed_run_table(db_module, models_module, n_diffs=0, n_missing=0, name_suffix="2")
        r2 = client.get(f"/api/runs/{run_id2}/tables/{rt_id2}")
        assert r2.status_code == 200
        data2 = r2.json()
        assert data2["type_mismatch_count"] == 0
        assert data2["column_type_details"] == []


def test_periode_shows_metric_detail_for_zero_delta_periods(tmp_path):
    """Real user confusion: periods with Δ=0 (row counts identical) still
    appeared in the mismatch list with no visible reason. The response must
    carry WHICH column/metric differed for those, and period_count must
    count distinct mismatched PERIODS, not the (now larger) number of
    finding rows."""
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

        r = client.get(f"/api/runs/{run_id}/tables/{rt_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["period_count"] == 2  # 2 distinct periods, not 3 findings
        periods = {f["period"] for f in data["period_findings"]}
        assert periods == {"2025-05", "2025-11"}
        metrics = {(f["period"], f["metric"]) for f in data["period_findings"]}
        assert ("2025-05", "sum") in metrics and ("2025-05", "max") in metrics
        row_count_finding = next(f for f in data["period_findings"] if f["period"] == "2025-11")
        assert row_count_finding["metric"] is None and row_count_finding["column_name"] is None
        assert row_count_finding["source_value"] == "16" and row_count_finding["target_value"] == "15"
