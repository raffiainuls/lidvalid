"""Oracle dialect + connector.

Uses `python-oracledb` (the modern cx_Oracle successor) in its default "thin"
mode -- pure Python, no Oracle Instant Client install required. `params.host`
+ `params.port` are the listener address; `params.database` is the service
name (passed as a query param, not the URL path -- Oracle Cloud/RDS/12c+
PDBs are addressed by service_name, not the legacy SID).
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from .base import Connector, ConnectionParams, Dialect


def _dt_literal(value: str, category: str) -> str:
    """An Oracle date/timestamp literal for `value`, matching `category`.
    `value` may be date-only ('2149-06-06') or a full datetime string
    ('2106-02-07 06:28:15') -- padded to midnight when the category calls
    for a timestamp but only a date string was supplied."""
    if category == "date":
        return f"TO_DATE('{value}', 'YYYY-MM-DD')"
    if " " not in value:
        value = f"{value} 00:00:00"
    return f"TO_TIMESTAMP('{value}', 'YYYY-MM-DD HH24:MI:SS')"


class OracleDialect(Dialect):
    name = "oracle"
    supports_final = False

    def quote_ident(self, col: str) -> str:
        # Deliberately UNQUOTED: Oracle case-folds unquoted identifiers, so
        # a column name in whatever case it came back from ALL_TAB_COLUMNS/
        # USER_TAB_COLUMNS (normally upper) resolves correctly either way.
        # Quoting would force exact-case matching instead -- get_schema()
        # below lowercases column names for consistency with every other
        # engine here, and a quoted "lowercase_name" against a table created
        # unquoted (so its real, stored name is UPPERCASE) would raise
        # ORA-00904 invalid identifier.
        return col

    def table_ref(self, database: str, table: str, final: bool = False) -> str:
        # Oracle DOES support cross-schema qualification within one
        # connection (`SOME_OWNER.SOME_TABLE` is valid SQL), unlike Postgres
        # -- the base Dialect's default `database.table` behavior is correct
        # as-is, so no override needed.
        return super().table_ref(database, table, final)

    def date_floor_1970(self, expr: str, category: str) -> str:
        return f"GREATEST({expr}, {_dt_literal('1970-01-01', category)})"

    def date_ceiling(self, expr: str, category: str, max_value: str | None) -> str:
        if not max_value:
            return expr
        return f"LEAST({expr}, {_dt_literal(max_value, category)})"

    def period_expr(self, col: str, granularity: str) -> str:
        if granularity == 'monthly':
            return f"TO_CHAR({col}, 'YYYY-MM')"
        return f"TO_CHAR({col}, 'YYYY')"

    def datediff_expr(self, col: str, ref_date: str) -> str:
        # TRUNC(... , to DATE) drops the time-of-day component so a
        # TIMESTAMP column still yields a whole-day integer difference, same
        # contract as every other dialect's datediff_expr.
        return f"(TRUNC(CAST({col} AS DATE)) - DATE '{ref_date}')"

    def date_range_filter(self, col: str, start_date: str, end_date: str) -> str:
        return f" WHERE TRUNC({col}) >= DATE '{start_date}' AND TRUNC({col}) < DATE '{end_date}'"

    def len_fn(self) -> str:
        return "LENGTH"

    def lower_fn(self) -> str:
        return "LOWER"

    def trim_fn(self) -> str:
        return "TRIM"

    def distinct_string_expr(self, expr: str) -> str:
        return f"LOWER(TRIM({expr}))"

    # is_not_null_ratio / count_distinct: Oracle's NUMBER has no separate
    # integer type, so COUNT(x)/COUNT(*) already returns a decimal (unlike
    # Postgres/SQLite) -- the base Dialect's default expressions are correct
    # unmodified, no override needed.


class OracleConnector(Connector):
    dialect = OracleDialect()

    def __init__(self, params: ConnectionParams):
        super().__init__(params)
        url = URL.create(
            drivername="oracle+oracledb",
            username=params.username,
            password=params.password,
            host=params.host,
            port=int(params.port or 1521),
            query={"service_name": params.database} if params.database else {},
        )
        self._engine = create_engine(
            url,
            connect_args={"tcp_connect_timeout": 30},
            pool_pre_ping=True,
        )

    def query_df(self, sql: str) -> pd.DataFrame:
        with self._engine.connect() as conn:
            df = pd.read_sql(sql, conn)
        # Oracle upper-cases every unquoted identifier, including SELECT
        # aliases -- `SELECT COUNT(*) AS total_row` comes back with a column
        # literally named "TOTAL_ROW". Every caller in aggregate/validator.py
        # and rowlevel/comparator.py indexes results by the lowercase alias
        # it wrote in the SQL (e.g. df["total_row"]), so without this
        # every one of those lookups raises a KeyError against Oracle results
        # specifically. Lowercasing here, once, at the query boundary, keeps
        # every caller engine-agnostic instead of teaching each one about
        # Oracle's case-folding.
        df.columns = [str(c).lower() for c in df.columns]
        return df

    @staticmethod
    def _split_owner(table: str, database: str) -> tuple[str | None, str]:
        if "." in table:
            owner, name = table.split(".", 1)
            return owner.upper(), name.upper()
        return (database.upper() if database else None), table.upper()

    def get_schema(self, database: str, table: str) -> pd.DataFrame:
        owner, tbl = self._split_owner(table, database)
        if owner:
            q = (
                f"SELECT column_name, data_type AS column_type FROM all_tab_columns "
                f"WHERE owner = '{owner}' AND table_name = '{tbl}' ORDER BY column_id"
            )
        else:
            q = (
                f"SELECT column_name, data_type AS column_type FROM user_tab_columns "
                f"WHERE table_name = '{tbl}' ORDER BY column_id"
            )
        df = self.query_df(q)
        if "column_name" in df.columns:
            df["column_name"] = df["column_name"].str.lower()
        return df

    def list_tables(self, database: str) -> list[str]:
        if database:
            q = f"SELECT table_name AS t FROM all_tables WHERE owner = '{database.upper()}' ORDER BY table_name"
        else:
            q = "SELECT table_name AS t FROM user_tables ORDER BY table_name"
        df = self.query_df(q)
        return [t.lower() for t in df["t"].tolist()] if "t" in df.columns else []

    def get_schemas_bulk(self, database: str, tables: list[str]) -> dict[str, list[str]]:
        if not tables:
            return {}
        pairs = [self._split_owner(t, database) for t in tables]
        in_list = ", ".join(f"'{tbl}'" for _, tbl in pairs)
        owner = pairs[0][0]  # every table in one config shares the same connection/owner
        if owner:
            q = (
                "SELECT table_name AS t, column_name FROM all_tab_columns "
                f"WHERE owner = '{owner}' AND table_name IN ({in_list}) ORDER BY table_name, column_id"
            )
        else:
            q = (
                "SELECT table_name AS t, column_name FROM user_tab_columns "
                f"WHERE table_name IN ({in_list}) ORDER BY table_name, column_id"
            )
        df = self.query_df(q)
        rows_by_upper: dict[str, list[str]] = {}
        if "t" in df.columns:
            for t, group in df.groupby("t"):
                rows_by_upper[t] = [c.lower() for c in group["column_name"].tolist()]
        return {full: rows_by_upper.get(tbl, []) for full, (_, tbl) in zip(tables, pairs)}

    def get_primary_key(self, database: str, table: str) -> list[str]:
        owner, tbl = self._split_owner(table, database)
        if owner:
            q = (
                "SELECT cols.column_name AS c, cols.position AS pos "
                "FROM all_constraints cons "
                "JOIN all_cons_columns cols ON cons.constraint_name = cols.constraint_name "
                "AND cons.owner = cols.owner "
                f"WHERE cols.owner = '{owner}' AND cols.table_name = '{tbl}' "
                "AND cons.constraint_type = 'P' ORDER BY pos"
            )
        else:
            q = (
                "SELECT cols.column_name AS c, cols.position AS pos "
                "FROM user_constraints cons "
                "JOIN user_cons_columns cols ON cons.constraint_name = cols.constraint_name "
                f"WHERE cols.table_name = '{tbl}' AND cons.constraint_type = 'P' "
                "ORDER BY pos"
            )
        df = self.query_df(q)
        return [c.lower() for c in df["c"].tolist()] if "c" in df.columns else []

    def _probe_sql(self) -> str:
        return "SELECT 1 FROM DUAL"

    def close(self) -> None:
        self._engine.dispose()
