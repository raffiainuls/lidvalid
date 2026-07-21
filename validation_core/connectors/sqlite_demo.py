"""SQLite dialect + connector — NOT part of the original two tools.

Added so the platform is runnable and demoable without VPN access to the real
MySQL/ClickHouse staging hosts (see docs/validation-platform/07-roadmap-migrasi.md
Fase 0). `params.database` is a filesystem path to a `.sqlite` file; `database`
prefixes are ignored since one file == one schema.

Kept deliberately close to the MySQL/ClickHouse dialects (same ratio math,
same period-expr contract) so the aggregate/rowlevel engines exercise real
code paths end-to-end in tests and in `scripts/seed_demo.py`, not mocks.
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from .base import Connector, ConnectionParams, Dialect


class SqliteDialect(Dialect):
    name = "sqlite"
    supports_final = False

    def quote_ident(self, col: str) -> str:
        return f'"{col}"'

    def table_ref(self, database: str, table: str, final: bool = False) -> str:
        return table  # one file == one schema; no db-qualified naming

    def date_floor_1970(self, expr: str, category: str) -> str:
        # SQLite MAX() with 2+ args is the scalar (row-wise) max, same
        # semantics as GREATEST() on ISO-formatted date strings.
        return f"MAX({expr}, '1970-01-01')"

    def date_ceiling(self, expr: str, category: str, max_value: str | None) -> str:
        if not max_value:
            return expr
        # SQLite MIN() with 2+ args is the scalar (row-wise) min -- mirrors
        # date_floor_1970's MAX() usage above.
        return f"MIN({expr}, '{max_value}')"

    def period_expr(self, col: str, granularity: str) -> str:
        if granularity == 'monthly':
            return f"strftime('%Y-%m', {col})"
        return f"strftime('%Y', {col})"

    def datediff_expr(self, col: str, ref_date: str) -> str:
        return f"CAST(julianday({col}) - julianday('{ref_date}') AS INTEGER)"

    def date_range_filter(self, col: str, start_date: str, end_date: str) -> str:
        return f" WHERE date({col}) >= '{start_date}' AND date({col}) < '{end_date}'"

    def len_fn(self) -> str:
        return "LENGTH"

    def lower_fn(self) -> str:
        return "LOWER"

    def trim_fn(self) -> str:
        return "TRIM"

    def distinct_string_expr(self, expr: str) -> str:
        return f"LOWER(TRIM({expr}))"

    def is_not_null_ratio(self, col: str) -> str:
        # SQLite integer/integer division truncates -- force float division.
        return f"CAST(COUNT({col}) AS REAL)/COUNT(*)"

    def count_distinct(self, expr: str) -> str:
        return f"CAST(COUNT(DISTINCT {expr}) AS REAL)/COUNT(*)"


class SqliteConnector(Connector):
    dialect = SqliteDialect()

    def __init__(self, params: ConnectionParams):
        super().__init__(params)
        self._path = params.database
        self._conn = sqlite3.connect(self._path, check_same_thread=False)

    def query_df(self, sql: str) -> pd.DataFrame:
        return pd.read_sql(sql, self._conn)

    def get_schema(self, database: str, table: str) -> pd.DataFrame:
        cur = self._conn.execute(f'PRAGMA table_info("{table}")')
        rows = cur.fetchall()
        # PRAGMA table_info -> (cid, name, type, notnull, dflt_value, pk)
        return pd.DataFrame(
            [{"column_name": r[1], "column_type": r[2] or "text"} for r in rows]
        )

    def list_tables(self, database: str) -> list[str]:
        df = self.query_df(
            "SELECT name AS t FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return df["t"].tolist() if "t" in df.columns else []

    def get_primary_key(self, database: str, table: str) -> list[str]:
        # PRAGMA table_info columns: (cid, name, type, notnull, dflt_value, pk)
        # -- `pk` is 0 for non-key columns, else its 1-based position within
        # a composite PRIMARY KEY (e.g. PRIMARY KEY (order_id, material_id)).
        cur = self._conn.execute(f'PRAGMA table_info("{table}")')
        pk_cols = [(r[5], r[1]) for r in cur.fetchall() if r[5]]
        pk_cols.sort(key=lambda x: x[0])
        return [name for _, name in pk_cols]

    def close(self) -> None:
        self._conn.close()
