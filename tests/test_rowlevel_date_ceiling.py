"""Row-level chunk fetches must clamp date/timestamp columns to
ClickHouse's own storage ceiling, the same way aggregate MIN/MAX/stat
columns already do -- NOT fall back to a raw number. A ClickHouse
DateTime64(9) row at its real max (2299-12-31) overflows pandas'
datetime64[ns] (~2262-04-11 ceiling) and used to crash the whole row-level
query for that table (real incident: datamart_transactions_rabies).
"""
from __future__ import annotations

import pandas as pd

from validation_core.connectors.clickhouse import ClickHouseDialect
from validation_core.connectors.mysql import MySqlDialect
from validation_core.models import RunSettings, TableSpec
from validation_core.rowlevel.comparator import build_range_query_multi
from validation_core.rowlevel.runner import RowLevelValidator


class _FakeConnector:
    def __init__(self, dialect, schema: dict[str, str]):
        self.dialect = dialect
        self._schema = schema

    def get_schema(self, database: str, table: str) -> pd.DataFrame:
        return pd.DataFrame({
            "column_name": list(self._schema.keys()),
            "column_type": list(self._schema.values()),
        })


class TestBuildRangeQueryMultiAppliesCeiling:
    def test_wraps_only_the_ceiling_flagged_column(self):
        dialect = MySqlDialect()
        sql = build_range_query_multi(
            dialect, ["id"], ["name", "created_at"], "t", "id", 1, 100,
            date_col_ceilings={"created_at": ("timestamp", "2299-12-31 23:59:59")},
        )
        assert sql == (
            "SELECT `id`, `name`, "
            "LEAST(GREATEST(`created_at`, '1970-01-01'), '2299-12-31 23:59:59') AS `created_at` "
            "FROM t WHERE `id` BETWEEN 1 AND 100"
        )

    def test_no_ceilings_leaves_query_unchanged(self):
        dialect = MySqlDialect()
        sql = build_range_query_multi(dialect, ["id"], ["name"], "t", "id", 1, 100, None)
        assert sql == "SELECT `id`, `name` FROM t WHERE `id` BETWEEN 1 AND 100"


class TestComputeDateColCeilings:
    def test_clickhouse_datetime64_column_gets_its_own_max_as_ceiling(self):
        """The exact incident shape: source is MySQL (no realistic ceiling
        of its own), target is ClickHouse with a DateTime64 column -- the
        shared ceiling must come from ClickHouse's side, mirroring
        _date_ceiling_bounds' existing aggregate-path behavior."""
        source = _FakeConnector(MySqlDialect(), {"id": "int", "created_at": "datetime"})
        target = _FakeConnector(ClickHouseDialect(), {"id": "UInt32", "created_at": "DateTime64(9)"})
        table = TableSpec(source_table="t", target_table="t", key_columns=["id"], chunk_column="id")
        validator = RowLevelValidator(source, target, "t", "t", table, "full", RunSettings())

        ceilings = validator._compute_date_col_ceilings(["id"], ["created_at"])

        assert ceilings == {"created_at": ("timestamp", "2299-12-31 23:59:59")}

    def test_no_date_columns_returns_empty(self):
        source = _FakeConnector(MySqlDialect(), {"id": "int", "amount": "decimal"})
        target = _FakeConnector(ClickHouseDialect(), {"id": "UInt32", "amount": "Decimal(10,2)"})
        table = TableSpec(source_table="t", target_table="t", key_columns=["id"], chunk_column="id")
        validator = RowLevelValidator(source, target, "t", "t", table, "full", RunSettings())

        assert validator._compute_date_col_ceilings(["id"], ["amount"]) == {}

    def test_schema_fetch_failure_degrades_gracefully(self):
        """A schema lookup can fail for lots of reasons (permissions, a
        transient network blip) -- row-level validation worked fine without
        this optimization before it existed, so a failure here must not
        take down the whole table run."""
        class _BoomConnector(_FakeConnector):
            def get_schema(self, database, table):
                raise RuntimeError("metastore unreachable")

        source = _BoomConnector(MySqlDialect(), {})
        target = _FakeConnector(ClickHouseDialect(), {"created_at": "DateTime64(9)"})
        table = TableSpec(source_table="t", target_table="t", key_columns=["id"], chunk_column="id")
        validator = RowLevelValidator(source, target, "t", "t", table, "full", RunSettings())

        assert validator._compute_date_col_ceilings(["id"], ["created_at"]) == {}

    def test_neither_side_clickhouse_no_ceiling_applied(self):
        source = _FakeConnector(MySqlDialect(), {"id": "int", "created_at": "datetime"})
        target = _FakeConnector(MySqlDialect(), {"id": "int", "created_at": "datetime"})
        table = TableSpec(source_table="t", target_table="t", key_columns=["id"], chunk_column="id")
        validator = RowLevelValidator(source, target, "t", "t", table, "full", RunSettings())

        ceilings = validator._compute_date_col_ceilings(["id"], ["created_at"])
        # still recognized as a timestamp column (so date_floor_1970 still
        # applies, a no-op on non-ClickHouse dialects), just no ceiling bound
        assert ceilings == {"created_at": ("timestamp", None)}
