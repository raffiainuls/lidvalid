from validation_core.aggregate import AggregateValidator
from validation_core.connectors.clickhouse import clickhouse_date_max
from validation_core.models import RunSettings


class TestAggregateValidatorIdenticalData:
    def test_pass_when_data_identical(self, identical_pair):
        source, target = identical_pair
        validator = AggregateValidator(
            source, target, "", "t", "", "t", date_column="d", settings=RunSettings(),
        )
        result = validator.run()
        summary = result.summarize()
        assert summary["status"] == "PASS"
        assert summary["row_match"] is True
        assert summary["stat_mismatch"] == 0
        assert summary["monthly_mismatch"] == 0


class TestAggregateValidatorWithDrift:
    def test_detects_row_count_and_stat_mismatch(self, orders_pair):
        source, target = orders_pair
        validator = AggregateValidator(
            source, target, "", "ws_orders", "", "raw_ws_orders",
            date_column="created_at", settings=RunSettings(),
        )
        result = validator.run()
        summary = result.summarize()

        assert summary["status"] == "FAIL"
        assert summary["source_rows"] == 200
        assert summary["target_rows"] == 198  # 2 rows missing (id 10, 20)
        assert summary["row_diff"] == 2
        assert summary["stat_mismatch"] > 0  # amount sum/max shifted by the id=100 change

    def test_extra_pipeline_columns_reported_but_not_penalized(self, orders_pair):
        source, target = orders_pair
        validator = AggregateValidator(
            source, target, "", "ws_orders", "", "raw_ws_orders", settings=RunSettings(),
        )
        result = validator.run()
        summary = result.summarize()
        assert set(summary["extra_target_columns"]) == {"ingested_at", "_dlt_id"}
        # meta columns must not appear as stat_mismatch_detail entries
        assert not any(d.startswith("ingested_at:") or d.startswith("_dlt_id:") for d in summary["stat_mismatch_detail"])

    def test_investigate_query_generated_when_date_column_and_mismatch_present(self, orders_pair):
        source, target = orders_pair
        validator = AggregateValidator(
            source, target, "", "ws_orders", "", "raw_ws_orders",
            date_column="created_at", settings=RunSettings(),
        )
        result = validator.run()
        assert result.investigate_query is not None
        assert "ws_orders" in result.investigate_query or "raw_ws_orders" in result.investigate_query

    def test_skip_period_breakdown_setting(self, orders_pair):
        source, target = orders_pair
        validator = AggregateValidator(
            source, target, "", "ws_orders", "", "raw_ws_orders",
            date_column="created_at", settings=RunSettings(skip_period_breakdown=True),
        )
        result = validator.run()
        assert result.monthly_breakdown.empty
        assert result.yearly_breakdown.empty
        assert result.investigate_query is None


class TestDateColumnMissingOnOneSide:
    def test_one_sided_date_column_skips_breakdown_instead_of_crashing(self, tmp_path):
        """Real incident (datamart_orders_smdv): the configured date_column
        existed only in the source schema -- the target-side monthly
        breakdown query failed with UNKNOWN_IDENTIFIER and ERROR'd the whole
        table. A one-sided date_column must instead disable the date-based
        reports for that table and let Reports 1-3 run normally."""
        import sqlite3

        from validation_core.connectors import ConnectionParams, create_connector

        src_path, tgt_path = tmp_path / "s.sqlite", tmp_path / "t.sqlite"
        conn = sqlite3.connect(src_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v REAL, master_updated_at TEXT)")
        conn.executemany("INSERT INTO t VALUES (?,?,?)", [(i, float(i), "2025-01-01") for i in range(1, 11)])
        conn.commit(); conn.close()
        conn = sqlite3.connect(tgt_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v REAL)")  # no master_updated_at
        conn.executemany("INSERT INTO t VALUES (?,?)", [(i, float(i)) for i in range(1, 11)])
        conn.commit(); conn.close()

        source = create_connector(ConnectionParams(engine="sqlite", database=str(src_path)))
        target = create_connector(ConnectionParams(engine="sqlite", database=str(tgt_path)))
        try:
            validator = AggregateValidator(
                source, target, "", "t", "", "t",
                date_column="master_updated_at", settings=RunSettings(),
            )
            result = validator.run()  # must NOT raise
            assert result.monthly_breakdown.empty
            assert result.yearly_breakdown.empty
            assert result.investigate_query is None
            assert "Period Breakdown SKIPPED" in result.queries
            assert "master_updated_at" in result.queries["Period Breakdown SKIPPED"]
            # Reports 1-3 still ran in full
            summary = result.summarize()
            assert summary["source_rows"] == 10 and summary["target_rows"] == 10
        finally:
            source.close()
            target.close()


class _FakeDialect:
    """Minimal stand-in exposing only what `_date_ceiling_bounds` touches
    (`.name`, `.date_max_bound`) -- no real DB connection needed, mirrors how
    `parse_sorting_key`/`clickhouse_date_max` are unit-tested standalone."""

    def __init__(self, name: str, is_clickhouse: bool = False):
        self.name = name
        self._is_clickhouse = is_clickhouse

    def date_max_bound(self, col_type: str):
        return clickhouse_date_max(col_type) if self._is_clickhouse else None


class _FakeConnector:
    def __init__(self, dialect: _FakeDialect):
        self.dialect = dialect


def _make_validator(source_dialect_name: str, target_dialect_name: str) -> AggregateValidator:
    source = _FakeConnector(_FakeDialect(source_dialect_name, source_dialect_name == "clickhouse"))
    target = _FakeConnector(_FakeDialect(target_dialect_name, target_dialect_name == "clickhouse"))
    return AggregateValidator(source, target, "", "t", "", "t")


class TestDateCeilingBounds:
    """`_date_ceiling_bounds` resolves the ClickHouse-side ceiling for a
    date/timestamp column pairing (see README/TECHNICAL) -- only whichever
    side is ClickHouse contributes a bound, since that's the only engine
    here with a hard storage-enforced max worth mirroring on the other side."""

    def test_neither_side_clickhouse_returns_no_bounds(self):
        v = _make_validator("mysql", "sqlite")
        assert v._date_ceiling_bounds("date", "date") == (None, None)

    def test_clickhouse_target_date_category(self):
        v = _make_validator("mysql", "clickhouse")
        date_bound, ts_bound = v._date_ceiling_bounds("date", "Date")
        assert date_bound == "2149-06-06"
        assert ts_bound is None

    def test_clickhouse_target_timestamp_category(self):
        v = _make_validator("mysql", "clickhouse")
        date_bound, ts_bound = v._date_ceiling_bounds("datetime", "DateTime64(3)")
        assert ts_bound == "2299-12-31 23:59:59"
        assert date_bound is None

    def test_both_sides_clickhouse_takes_the_tighter_bound(self):
        # Date32 allows up to 2299-12-31; plain Date only up to 2149-06-06 --
        # capping both at the SAME (tighter) bound is what makes the
        # comparison meaningful (see AggregateValidator docstring).
        v = _make_validator("clickhouse", "clickhouse")
        date_bound, _ = v._date_ceiling_bounds("Date32", "Date")
        assert date_bound == "2149-06-06"

    def test_missing_type_on_one_side_is_ignored(self):
        v = _make_validator("mysql", "clickhouse")
        date_bound, _ = v._date_ceiling_bounds(None, "Date")
        assert date_bound == "2149-06-06"

    def test_pandas_na_missing_type_does_not_crash(self):
        # Real incident (datamart_orders_smdv): a one-sided column's missing
        # type can arrive as pd.NA (not np.nan) depending on the schema
        # DataFrame's dtype -- `not pd.NA` raises "boolean value of NA is
        # ambiguous", which ERROR'd the whole table. Both NA flavors must be
        # treated as "no type on this side", same as None.
        import numpy as np
        import pandas as pd

        v = _make_validator("mysql", "clickhouse")
        for missing in (pd.NA, np.nan, float("nan")):
            date_bound, ts_bound = v._date_ceiling_bounds(missing, "Date")
            assert date_bound == "2149-06-06", f"missing={missing!r}"
            assert ts_bound is None
            # and NA on the ClickHouse side -> no bound at all
            assert v._date_ceiling_bounds("datetime", missing) == (None, None)


class TestCompletenessNaNIsNotAMismatch:
    """Real incident: a table with 0 rows on BOTH sides makes
    COUNT(x)/COUNT(*) a NaN/NaN division on engines (ClickHouse) that return
    a real float NaN rather than SQL NULL for 0/0 -- a raw `==` comparison
    then miscodes "identically undefined on both sides" as a mismatch,
    fabricating one FindingAggregate row per column (216 in the reported
    incident) for a table that has no actual discrepancy at all."""

    def test_nan_both_sides_is_not_flagged_as_mismatch(self):
        import pandas as pd
        from validation_core.connectors.clickhouse import ClickHouseDialect

        class _Conn:
            dialect = ClickHouseDialect()

        validator = AggregateValidator(_Conn(), _Conn(), "", "t", "", "t")
        nan = float("nan")
        # Stand in for the real 0-row-both-sides query result -- avoids
        # needing a live ClickHouse server to reproduce its 0/0 -> NaN
        # division behavior (SQLite returns NULL for 0/0, not NaN, so the
        # existing sqlite-backed fixtures can't reproduce this specific bug).
        validator._run_source = lambda sql: pd.DataFrame({"col_completeness": [nan], "col_uniqueness": [nan]})
        validator._run_target = lambda sql: pd.DataFrame({"col_completeness": [nan], "col_uniqueness": [nan]})

        schema_df = pd.DataFrame({
            "column_name": ["col"],
            "source_column_type": ["String"],
            "target_column_type": ["String"],
        })

        result_df = validator.gen_report_column_details(schema_df)
        row = result_df[result_df["column_name"] == "col"].iloc[0]
        assert bool(row["validate_completeness"]) is True
        assert bool(row["validate_uniqueness"]) is True

    def test_genuinely_different_completeness_is_still_flagged(self):
        """The fix must not turn off real mismatch detection -- only the
        both-NaN case is special-cased (via values_match, same as every
        other comparison in this codebase)."""
        import pandas as pd
        from validation_core.connectors.clickhouse import ClickHouseDialect

        class _Conn:
            dialect = ClickHouseDialect()

        validator = AggregateValidator(_Conn(), _Conn(), "", "t", "", "t")
        validator._run_source = lambda sql: pd.DataFrame({"col_completeness": [1.0], "col_uniqueness": [1.0]})
        validator._run_target = lambda sql: pd.DataFrame({"col_completeness": [0.5], "col_uniqueness": [1.0]})

        schema_df = pd.DataFrame({
            "column_name": ["col"],
            "source_column_type": ["String"],
            "target_column_type": ["String"],
        })

        result_df = validator.gen_report_column_details(schema_df)
        row = result_df[result_df["column_name"] == "col"].iloc[0]
        assert bool(row["validate_completeness"]) is False
        assert bool(row["validate_uniqueness"]) is True


class TestPeriodBucketingFloorsSentinelDates:
    """Real incident: MySQL's zero-date sentinel '0000-00-00' is NOT NULL, so
    it survives the `IS NOT NULL` filter, and DATE_FORMAT('0000-00-00', '%Y-%m')
    returns the literal string '0000-00' -- a fake period bucket that then
    reads as a spurious row-count/stat MISMATCH against a target that never
    stored that sentinel. period_expr must bucket the same floored/ceiled
    expression _period_stat_selects/_col_metric_selects already use for this
    exact column, not the raw column."""

    def test_monthly_query_floors_date_column_before_bucketing(self):
        import pandas as pd
        from validation_core.connectors.mysql import MySqlDialect

        class _Conn:
            dialect = MySqlDialect()

        validator = AggregateValidator(_Conn(), _Conn(), "", "t", "", "t", date_column="created_at")
        # Only the QUERY TEXT is under test -- no live DB needed.
        validator._run_source = lambda sql: pd.DataFrame()
        validator._run_target = lambda sql: pd.DataFrame()

        validator.gen_report_period_breakdown("monthly", None, date_col_types=("date", "date"))

        src_q = validator.queries["Monthly Breakdown Source"]
        assert "DATE_FORMAT(GREATEST(created_at, '1970-01-01')" in src_q

    def test_missing_date_col_types_falls_back_to_raw_column(self):
        """Callers that don't pass the column's type skip flooring rather
        than crash -- keeps the new parameter backward compatible."""
        import pandas as pd
        from validation_core.connectors.mysql import MySqlDialect

        class _Conn:
            dialect = MySqlDialect()

        validator = AggregateValidator(_Conn(), _Conn(), "", "t", "", "t", date_column="created_at")
        validator._run_source = lambda sql: pd.DataFrame()
        validator._run_target = lambda sql: pd.DataFrame()

        validator.gen_report_period_breakdown("monthly", None, date_col_types=None)

        src_q = validator.queries["Monthly Breakdown Source"]
        assert "GREATEST" not in src_q
        assert "DATE_FORMAT(created_at," in src_q
