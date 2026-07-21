"""Connector + Dialect interfaces.

A `Dialect` captures the per-engine SQL differences the aggregate validator
needs (identifier quoting, FINAL, date flooring, period expressions, ...) so
`aggregate.validator.AggregateValidator` has ONE implementation of the five
reports instead of the mysql/clickhouse if-else branching the original
`db_validator.py` had baked in. A `Connector` wraps the actual DB client.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class ConnectionParams:
    """Everything needed to open a connection. Mirrors `connections` table."""
    engine: str
    host: str = ""
    port: int = 0
    database: str = ""
    username: str = ""
    password: str = ""
    params: dict = field(default_factory=dict)


class Dialect:
    """Default/generic dialect. Engine dialects override what differs."""

    name = "generic"
    supports_final = False

    def quote_ident(self, col: str) -> str:
        return col

    def table_ref(self, database: str, table: str, final: bool = False) -> str:
        ref = f"{database}.{table}" if database else table
        if final and self.supports_final:
            ref += " FINAL"
        return ref

    def date_floor_1970(self, expr: str, category: str) -> str:
        """Floor a date/datetime expression to 1970-01-01.

        ClickHouse Date/DateTime can't represent years before the Unix
        epoch — pre-1970 values get clamped to 1970-01-01 at ingestion.
        Comparing a genuinely-pre-1970 value on ONE side against
        ClickHouse's already-clamped value on the other would show a false
        mismatch on min/max/datediff/distinct, so EVERY engine floors the
        same way before aggregating (symmetric on purpose — this validator
        compares any engine pair, not just MySQL<->ClickHouse, so it can't
        assume only one side needs it). `category` is 'date' or 'timestamp'
        — engines whose floor is a plain string-literal comparison (MySQL,
        SQLite) ignore it; ClickHouse needs it to pick a type-matching
        literal (toDate vs toDateTime). Identity by default.
        """
        return expr

    def date_ceiling(self, expr: str, category: str, max_value: str | None) -> str:
        """Cap a date/timestamp expression at `max_value`, the upper bound
        of whichever side of this comparison is ClickHouse (the only engine
        here with a hard storage-enforced ceiling — see
        ClickHouseDialect.date_max_bound / connectors.clickhouse.clickhouse_date_max).
        `max_value` is `None` when neither side is ClickHouse, meaning
        there's no physical ceiling to mirror — identity in that case, and
        by default.
        """
        return expr

    def date_max_bound(self, col_type: str) -> str | None:
        """This engine's hard storage-enforced upper bound for `col_type`,
        or None if the engine has no such limit worth mirroring (MySQL
        DATE/DATETIME reach year 9999, SQLite has no limit at all — neither
        is a realistic constraint on real business data). Only
        ClickHouseDialect overrides this with a real value.
        """
        return None

    def wrap_minmax_datetime(self, expr: str) -> str:
        """Wrap a MIN/MAX(...) datetime expression for safe stringification.

        ClickHouse: wrapped in toString() to prevent pandas
        OutOfBoundsDatetime on sentinel dates like 2299-12-31. Identity
        elsewhere (matches the legacy tool's behavior — MySQL side is NOT
        wrapped).
        """
        return expr

    def period_expr(self, col: str, granularity: str) -> str:
        raise NotImplementedError

    def period_expr_literal(self, col: str, granularity: str) -> str:
        """Same as period_expr but for human-facing SQL text (the
        investigate-query file) meant to be pasted directly into a DB client.
        Bypasses whatever execution-layer escaping period_expr() needs for
        programmatic execution (see MySqlDialect — %% vs % ). Identity here;
        only MySQL needs to override it.
        """
        return self.period_expr(col, granularity)

    def datediff_expr(self, col: str, ref_date: str) -> str:
        raise NotImplementedError

    def date_range_filter(self, col: str, start_date: str, end_date: str) -> str:
        raise NotImplementedError

    def len_fn(self) -> str:
        return "LENGTH"

    def lower_fn(self) -> str:
        return "LOWER"

    def trim_fn(self) -> str:
        return "TRIM"

    def distinct_string_expr(self, expr: str) -> str:
        """Distinct-comparison expression for a string column.

        Base implementation: case-insensitive, trimmed. MySQL overrides this
        to add BINARY (see MySqlDialect.distinct_string_expr).
        """
        return f"{self.lower_fn()}({self.trim_fn()}({expr}))"

    def is_not_null_ratio(self, col: str) -> str:
        """`<non-null count> / <total count>` expression for completeness."""
        return f"COUNT({col})/COUNT(*)"

    def count_distinct(self, expr: str) -> str:
        return f"COUNT(DISTINCT {expr})/COUNT(*)"


class Connector(ABC):
    """A live handle to one database, bound to one Dialect."""

    dialect: Dialect

    def __init__(self, params: ConnectionParams):
        self.params = params

    @abstractmethod
    def query_df(self, sql: str) -> pd.DataFrame:
        """Run a query, return a pandas DataFrame."""

    def query_df_stream(self, sql: str):
        """Optional streaming variant (ClickHouse). Default: yield one block."""
        yield self.query_df(sql)

    @abstractmethod
    def get_schema(self, database: str, table: str) -> pd.DataFrame:
        """Return DataFrame[column_name, column_type] for a table."""

    @abstractmethod
    def list_tables(self, database: str) -> list[str]:
        ...

    def get_schemas_bulk(self, database: str, tables: list[str]) -> dict[str, list[str]]:
        """{table_name: [column_name, ...]} for MULTIPLE tables in as few
        round-trips as possible -- backs the config-detail page's per-table
        column dropdowns. Default falls back to one get_schema() call per
        table (correct, but O(n) round-trips -- real incident: a 99-table
        config took minutes to open because of exactly this, each table's
        query paying its own network round-trip over a VPN/SSH-tunneled
        connection). Override this per-engine wherever the metadata store
        supports a single `WHERE table_name IN (...)`-style query instead
        (see MySqlConnector/PostgresConnector/etc.)."""
        result: dict[str, list[str]] = {}
        for t in tables:
            if t in result:
                continue
            df = self.get_schema(database, t)
            result[t] = df["column_name"].tolist() if "column_name" in df.columns else []
        return result

    def get_primary_key(self, database: str, table: str) -> list[str]:
        """Best-effort discovery of the table's natural key (PRIMARY KEY for
        MySQL, sorting key for ClickHouse) — used to auto-fill `key_columns`
        when suggesting table mappings, including composite keys. Returns
        `[]` if unknown or not implemented for this engine; callers should
        fall back to a sane default (e.g. `["id"]`) in that case."""
        return []

    def test_connection(self) -> dict[str, Any]:
        start = time.monotonic()
        try:
            self.query_df(self._probe_sql())
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            return {"ok": True, "latency_ms": latency_ms, "error": None}
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller, not swallowed
            return {"ok": False, "latency_ms": None, "error": str(exc)}

    def _probe_sql(self) -> str:
        return "SELECT 1"

    def close(self) -> None:
        pass
