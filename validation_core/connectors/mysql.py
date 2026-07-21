"""MySQL dialect + connector. Ported from `validation-data/scripts/db_validator.py`
(`_init_connections`, `get_schema_source`, `_table_ref`) and the reserved-keyword
backtick quoting used throughout both legacy tools.
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL

from .base import Connector, ConnectionParams, Dialect


class MySqlDialect(Dialect):
    name = "mysql"
    supports_final = False

    def quote_ident(self, col: str) -> str:
        return f"`{col}`"

    def date_floor_1970(self, expr: str, category: str) -> str:
        return f"GREATEST({expr}, '1970-01-01')"

    def date_ceiling(self, expr: str, category: str, max_value: str | None) -> str:
        if not max_value:
            return expr
        return f"LEAST({expr}, '{max_value}')"

    def period_expr(self, col: str, granularity: str) -> str:
        # IMPORTANT: the doubled '%%' is not a typo. pandas.read_sql runs this
        # string through SQLAlchemy's mysql+pymysql dialect, which uses the
        # DBAPI's "pyformat"-style execution — literal '%' characters in raw
        # SQL text are collapsed one level (`%%` -> `%`) before the query
        # reaches the server. A single '%Y' here would be swallowed/misread
        # by that layer; MySQL itself only ever sees '%Y-%m' after the
        # collapse. See docs/validation-platform/01-analisa-existing.md and
        # the original comment in db_validator.py: "%% escapes % in pymysql
        # so it isn't treated as a Python format char".
        if granularity == 'monthly':
            return f"DATE_FORMAT({col}, '%%Y-%%m')"
        return f"DATE_FORMAT({col}, '%%Y')"

    def period_expr_literal(self, col: str, granularity: str) -> str:
        # Human-facing version (investigate-query .sql text, pasted directly
        # into a MySQL client) -- single '%', since there is no SQLAlchemy
        # pyformat layer to collapse it there.
        if granularity == 'monthly':
            return f"DATE_FORMAT({col}, '%Y-%m')"
        return f"DATE_FORMAT({col}, '%Y')"

    def datediff_expr(self, col: str, ref_date: str) -> str:
        return f"DATEDIFF({col}, '{ref_date}')"

    def date_range_filter(self, col: str, start_date: str, end_date: str) -> str:
        return f" WHERE DATE({col}) >= '{start_date}' AND DATE({col}) < '{end_date}'"

    def len_fn(self) -> str:
        return "LENGTH"

    def lower_fn(self) -> str:
        return "LOWER"

    def trim_fn(self) -> str:
        return "TRIM"

    def distinct_string_expr(self, expr: str) -> str:
        # BINARY forces byte comparison on the MySQL side: the default
        # utf8mb4_0900_ai_ci collation is accent-insensitive (a` = a, NBSP =
        # space) while ClickHouse always compares raw bytes. Don't use
        # COLLATE utf8mb4_bin instead -- it is PAD SPACE, so trailing-space
        # variants would collapse and mismatch the other way.
        return f"BINARY LOWER(TRIM({expr}))"

    def is_not_null_ratio(self, col: str) -> str:
        return f"COUNT({col})/COUNT(*)"


class MySqlConnector(Connector):
    dialect = MySqlDialect()

    def __init__(self, params: ConnectionParams):
        super().__init__(params)
        url = URL.create(
            drivername="mysql+pymysql",
            username=params.username,
            password=params.password,
            host=params.host,
            port=int(params.port or 3306),
            database=params.database,
            query={"charset": "utf8mb4"},
        )
        self._engine = create_engine(
            url,
            connect_args={
                "connect_timeout": 30,
                "read_timeout": 3600,
                "write_timeout": 3600,
            },
            pool_pre_ping=True,
        )

        @event.listens_for(self._engine, "connect")
        def _set_session_vars(dbapi_conn, _):
            with dbapi_conn.cursor() as cur:
                cur.execute("SET SESSION wait_timeout=86400")
                cur.execute("SET SESSION interactive_timeout=86400")
                cur.execute("SET SESSION net_read_timeout=3600")
                cur.execute("SET SESSION net_write_timeout=3600")

    def query_df(self, sql: str) -> pd.DataFrame:
        with self._engine.connect() as conn:
            return pd.read_sql(sql, conn)

    def get_schema(self, database: str, table: str) -> pd.DataFrame:
        q = (
            f"SELECT COLUMN_NAME AS column_name, DATA_TYPE AS column_type "
            f"FROM INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_SCHEMA = '{database}' AND TABLE_NAME = '{table}' "
            f"ORDER BY ORDINAL_POSITION"
        )
        return self.query_df(q)

    def list_tables(self, database: str) -> list[str]:
        q = (
            f"SELECT TABLE_NAME AS t FROM INFORMATION_SCHEMA.TABLES "
            f"WHERE TABLE_SCHEMA = '{database}' ORDER BY TABLE_NAME"
        )
        df = self.query_df(q)
        return df["t"].tolist() if "t" in df.columns else []

    def get_schemas_bulk(self, database: str, tables: list[str]) -> dict[str, list[str]]:
        if not tables:
            return {}
        in_list = ", ".join(f"'{t}'" for t in tables)
        q = (
            f"SELECT TABLE_NAME AS t, COLUMN_NAME AS column_name FROM INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_SCHEMA = '{database}' AND TABLE_NAME IN ({in_list}) "
            f"ORDER BY TABLE_NAME, ORDINAL_POSITION"
        )
        df = self.query_df(q)
        result: dict[str, list[str]] = {t: [] for t in tables}
        if "t" in df.columns:
            for t, group in df.groupby("t"):
                result[t] = group["column_name"].tolist()
        return result

    def get_primary_key(self, database: str, table: str) -> list[str]:
        # ORDER BY ORDINAL_POSITION is what makes a composite PK come back in
        # the correct column order (e.g. (order_id, material_id), not
        # whatever order the information_schema happens to store rows in).
        q = (
            f"SELECT COLUMN_NAME AS c FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE "
            f"WHERE TABLE_SCHEMA = '{database}' AND TABLE_NAME = '{table}' "
            f"AND CONSTRAINT_NAME = 'PRIMARY' ORDER BY ORDINAL_POSITION"
        )
        df = self.query_df(q)
        return df["c"].tolist() if "c" in df.columns else []

    def _probe_sql(self) -> str:
        return "SELECT 1 AS ok"

    def close(self) -> None:
        self._engine.dispose()
