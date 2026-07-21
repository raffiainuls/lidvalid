"""PostgreSQL dialect + connector.

Uses `psycopg` (already a dependency for the app's own Postgres-backed
storage, see app/database.py) via SQLAlchemy, same create_engine/pd.read_sql
shape as MySqlConnector.
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from .base import Connector, ConnectionParams, Dialect


class PostgresDialect(Dialect):
    name = "postgres"
    supports_final = False

    def quote_ident(self, col: str) -> str:
        return f'"{col}"'

    def table_ref(self, database: str, table: str, final: bool = False) -> str:
        # Unlike MySQL/ClickHouse, Postgres can't cross-database qualify a
        # table within one connection (`otherdb.table` isn't valid SQL) --
        # `database` here is already the DB picked at connect time. `table`
        # itself may still carry a schema prefix (e.g. "reporting.orders"),
        # which Postgres DOES support, so it's passed through unchanged.
        return table

    def date_floor_1970(self, expr: str, category: str) -> str:
        return f"GREATEST({expr}, '1970-01-01')"

    def date_ceiling(self, expr: str, category: str, max_value: str | None) -> str:
        if not max_value:
            return expr
        return f"LEAST({expr}, '{max_value}')"

    def period_expr(self, col: str, granularity: str) -> str:
        # TO_CHAR uses named tokens (YYYY, MM), not '%'-prefixed ones -- no
        # pyformat-escaping concern here (see MySqlDialect.period_expr for
        # why that matters for engines that DO use '%Y'-style tokens).
        if granularity == 'monthly':
            return f"TO_CHAR({col}, 'YYYY-MM')"
        return f"TO_CHAR({col}, 'YYYY')"

    def datediff_expr(self, col: str, ref_date: str) -> str:
        # Subtracting two DATEs gives an integer day count directly; casting
        # first keeps this correct even when `col` is a TIMESTAMP (subtracting
        # two timestamps instead yields an INTERVAL, not an integer).
        return f"(CAST({col} AS DATE) - DATE '{ref_date}')"

    def date_range_filter(self, col: str, start_date: str, end_date: str) -> str:
        return f" WHERE {col}::date >= '{start_date}' AND {col}::date < '{end_date}'"

    def len_fn(self) -> str:
        return "LENGTH"

    def lower_fn(self) -> str:
        return "LOWER"

    def trim_fn(self) -> str:
        return "TRIM"

    def distinct_string_expr(self, expr: str) -> str:
        return f"LOWER(TRIM({expr}))"

    def is_not_null_ratio(self, col: str) -> str:
        # Postgres integer/integer division truncates (unlike MySQL, which
        # auto-promotes to decimal) -- same reasoning as SqliteDialect.
        return f"CAST(COUNT({col}) AS DOUBLE PRECISION)/COUNT(*)"

    def count_distinct(self, expr: str) -> str:
        return f"CAST(COUNT(DISTINCT {expr}) AS DOUBLE PRECISION)/COUNT(*)"


class PostgresConnector(Connector):
    dialect = PostgresDialect()

    def __init__(self, params: ConnectionParams):
        super().__init__(params)
        url = URL.create(
            drivername="postgresql+psycopg",
            username=params.username,
            password=params.password,
            host=params.host,
            port=int(params.port or 5432),
            database=params.database,
        )
        self._engine = create_engine(
            url,
            connect_args={"connect_timeout": 30},
            pool_pre_ping=True,
        )

    def query_df(self, sql: str) -> pd.DataFrame:
        with self._engine.connect() as conn:
            return pd.read_sql(sql, conn)

    @staticmethod
    def _split_schema(table: str, database: str) -> tuple[str, str]:
        if "." in table:
            schema, name = table.split(".", 1)
            return schema, name
        return (database or "public"), table

    def get_schema(self, database: str, table: str) -> pd.DataFrame:
        schema, tbl = self._split_schema(table, database)
        q = (
            f"SELECT column_name, data_type AS column_type FROM information_schema.columns "
            f"WHERE table_schema = '{schema}' AND table_name = '{tbl}' ORDER BY ordinal_position"
        )
        return self.query_df(q)

    def list_tables(self, database: str) -> list[str]:
        q = (
            "SELECT table_name AS t FROM information_schema.tables "
            "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') ORDER BY table_name"
        )
        df = self.query_df(q)
        return df["t"].tolist() if "t" in df.columns else []

    def get_schemas_bulk(self, database: str, tables: list[str]) -> dict[str, list[str]]:
        if not tables:
            return {}
        # Each table may carry its own schema prefix (see _split_schema) --
        # grouped by (schema, bare name) pairs so a single query still covers
        # every one of them regardless of which schema each lives in.
        by_schema: dict[str, list[str]] = {}
        bare_by_full = {}
        for full in tables:
            schema, tbl = self._split_schema(full, database)
            by_schema.setdefault(schema, []).append(tbl)
            bare_by_full[full] = (schema, tbl)

        rows_by_key: dict[tuple[str, str], list[str]] = {}
        for schema, tbls in by_schema.items():
            in_list = ", ".join(f"'{t}'" for t in tbls)
            q = (
                "SELECT table_name AS t, column_name FROM information_schema.columns "
                f"WHERE table_schema = '{schema}' AND table_name IN ({in_list}) "
                "ORDER BY table_name, ordinal_position"
            )
            df = self.query_df(q)
            if "t" in df.columns:
                for t, group in df.groupby("t"):
                    rows_by_key[(schema, t)] = group["column_name"].tolist()

        return {full: rows_by_key.get(key, []) for full, key in bare_by_full.items()}

    def get_primary_key(self, database: str, table: str) -> list[str]:
        schema, tbl = self._split_schema(table, database)
        q = (
            "SELECT a.attname AS c, array_position(i.indkey, a.attnum) AS pos "
            "FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            f"WHERE i.indrelid = '{schema}.{tbl}'::regclass AND i.indisprimary "
            "ORDER BY pos"
        )
        df = self.query_df(q)
        return df["c"].tolist() if "c" in df.columns else []

    def _probe_sql(self) -> str:
        return "SELECT 1 AS ok"

    def close(self) -> None:
        self._engine.dispose()
