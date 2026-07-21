"""Regression coverage for density-aware row-level chunking.

id_chunk_size slices work by chunk-column RANGE, which assumes ~1 row per id
(auto-increment PKs). Composite-key tables break that: a tiny id range can
carry the whole table -- real incident: datamart_logger_monitoring, chunk
column range 0-22827 x many rows per id x 105 columns collapsed into ONE
chunk whose fetch streamed for ~2 hours and then died mid-stream
("unrecognized data found in stream"). The fix shrinks the id-range per
chunk so one chunk carries roughly rowlevel_target_chunk_rows ROWS; sparse
or ordinary tables keep the legacy sizing untouched (the shrink never grows
a chunk).
"""
from __future__ import annotations

import sqlite3

from validation_core.connectors import ConnectionParams, create_connector
from validation_core.models import RunSettings, TableSpec
from validation_core.rowlevel.runner import RowLevelValidator


def _make_dense_pair(tmp_path, n_ids: int, rows_per_id: int):
    """Composite-key shape: chunk column (asset_id) spans a SMALL range but
    each id has many rows (one per seq)."""
    src_path, tgt_path = tmp_path / "src.sqlite", tmp_path / "tgt.sqlite"
    for p in (src_path, tgt_path):
        conn = sqlite3.connect(p)
        conn.execute("CREATE TABLE t (asset_id INTEGER, seq INTEGER, v REAL, PRIMARY KEY (asset_id, seq))")
        conn.executemany(
            "INSERT INTO t VALUES (?,?,?)",
            [(i, s, float(i * 1000 + s)) for i in range(n_ids) for s in range(rows_per_id)],
        )
        conn.commit(); conn.close()
    source = create_connector(ConnectionParams(engine="sqlite", database=str(src_path)))
    target = create_connector(ConnectionParams(engine="sqlite", database=str(tgt_path)))
    return source, target


def _run_and_capture_phases(source, target, settings):
    events = []
    table = TableSpec(source_table="t", target_table="t",
                      key_columns=["asset_id", "seq"], chunk_column="asset_id")
    validator = RowLevelValidator(
        source, target, "t", "t", table, "full", settings,
        on_event=lambda e: events.append(e.message),
    )
    result = validator.run()
    return result, events


def test_dense_composite_key_table_gets_multiple_row_bounded_chunks(tmp_path):
    # 100 ids x 200 rows/id = 20_000 rows over an id range of only 100.
    # With id_chunk_size=2M the legacy sizing puts EVERYTHING in 1 chunk;
    # target 5_000 rows/chunk must split it into ~4 chunks (25 ids each).
    source, target = _make_dense_pair(tmp_path, n_ids=100, rows_per_id=200)
    try:
        settings = RunSettings(rowlevel_target_chunk_rows=5_000)
        result, events = _run_and_capture_phases(source, target, settings)
        assert result.total_chunks > 1, "dense table must not collapse into one chunk"
        assert result.total_chunks == 4
        assert any("dense chunk column" in m for m in events), events
        # correctness unaffected: identical data -> clean result
        assert result.missing_in_source_count == 0
        assert result.missing_in_target_count == 0
        assert result.differing_values_count == 0
    finally:
        source.close()
        target.close()


def test_digit_string_chunk_column_falls_back_to_full_scan(tmp_path):
    """Real incident (datamart_wms_report_waste_bags): waste_bag_id is a
    STRING column whose values are all digits. int() on its MIN/MAX
    succeeded, so the runner treated it as numeric -- producing range
    queries that ClickHouse rejects with NO_COMMON_TYPE (String vs UInt32
    literal), and a lexicographic min/max that computed 499,912 bogus
    chunks. A string-typed chunk column must fall back to the single
    full-table scan, digits or not."""
    src_path, tgt_path = tmp_path / "s.sqlite", tmp_path / "t.sqlite"
    for p in (src_path, tgt_path):
        conn = sqlite3.connect(p)
        conn.execute("CREATE TABLE t (waste_bag_id TEXT PRIMARY KEY, v REAL)")
        conn.executemany(
            "INSERT INTO t VALUES (?,?)",
            [(f"{101012026 + i}", float(i)) for i in range(50)],
        )
        conn.commit(); conn.close()
    source = create_connector(ConnectionParams(engine="sqlite", database=str(src_path)))
    target = create_connector(ConnectionParams(engine="sqlite", database=str(tgt_path)))
    try:
        events = []
        table = TableSpec(source_table="t", target_table="t",
                          key_columns=["waste_bag_id"], chunk_column="waste_bag_id")
        validator = RowLevelValidator(
            source, target, "t", "t", table, "full", RunSettings(),
            on_event=lambda e: events.append(e.message),
        )
        result = validator.run()  # must not blow up on bogus integer ranges
        assert result.total_chunks == 1
        assert any("full-table scan" in m and "string" in m for m in events), events
        assert result.missing_in_source_count == 0
        assert result.missing_in_target_count == 0
        assert result.differing_values_count == 0
    finally:
        source.close()
        target.close()


def test_sparse_table_keeps_legacy_single_chunk(tmp_path):
    # 50 ids x 1 row/id: rows (50) << target (5_000) -> density sizing would
    # ALLOW a huge chunk, but must never grow beyond id_chunk_size; the
    # whole range still fits one legacy-sized chunk -> exactly 1 chunk and
    # no "dense" phase message.
    source, target = _make_dense_pair(tmp_path, n_ids=50, rows_per_id=1)
    try:
        settings = RunSettings(rowlevel_target_chunk_rows=5_000)
        result, events = _run_and_capture_phases(source, target, settings)
        assert result.total_chunks == 1
        assert not any("dense chunk column" in m for m in events), events
    finally:
        source.close()
        target.close()
