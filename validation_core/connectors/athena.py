"""AWS Athena dialect + connector.

Athena has no host/port/username/password in the traditional sense -- it's
an AWS API (region + IAM credentials) that runs queries against S3-backed
tables cataloged in Glue, writing results to a scratch S3 location. Mapped
onto this app's fixed Connection shape:
  - username  -> AWS Access Key ID
  - password  -> AWS Secret Access Key (already stored encrypted at rest,
                 same as every other engine's password)
  - host      -> AWS region (e.g. "ap-southeast-3"); port is unused
  - database  -> Glue/Athena database (schema)
  - params    -> {"s3_staging_dir": "s3://bucket/prefix/", "work_group": "..."}
                 (work_group optional, defaults to "primary")
"""
from __future__ import annotations

import pandas as pd
from pyathena import connect
from pyathena.pandas.util import as_pandas

from .base import Connector, ConnectionParams, Dialect


class AthenaDialect(Dialect):
    name = "athena"
    supports_final = False

    def quote_ident(self, col: str) -> str:
        return f'"{col}"'

    def date_floor_1970(self, expr: str, category: str) -> str:
        if category == "date":
            return f"GREATEST({expr}, DATE '1970-01-01')"
        return f"GREATEST({expr}, TIMESTAMP '1970-01-01 00:00:00')"

    def date_ceiling(self, expr: str, category: str, max_value: str | None) -> str:
        if not max_value:
            return expr
        if category == "date":
            return f"LEAST({expr}, DATE '{max_value}')"
        value = max_value if " " in max_value else f"{max_value} 00:00:00"
        return f"LEAST({expr}, TIMESTAMP '{value}')"

    def period_expr(self, col: str, granularity: str) -> str:
        # Presto/Trino's format_datetime() uses Joda-Time tokens (yyyy, MM),
        # not '%'-prefixed ones -- deliberately NOT using date_format()
        # (which DOES use '%Y'-style tokens) since PyAthena's DBAPI parameter
        # handling for a literal '%' in raw SQL text is untested here (no
        # live Athena endpoint available) -- format_datetime() sidesteps the
        # question entirely. Cast handles a plain DATE column, which
        # format_datetime() otherwise rejects (it requires a TIMESTAMP).
        fmt = "yyyy-MM" if granularity == 'monthly' else "yyyy"
        return f"format_datetime(CAST({col} AS TIMESTAMP), '{fmt}')"

    def datediff_expr(self, col: str, ref_date: str) -> str:
        return f"date_diff('day', DATE '{ref_date}', CAST({col} AS DATE))"

    def date_range_filter(self, col: str, start_date: str, end_date: str) -> str:
        return f" WHERE CAST({col} AS DATE) >= DATE '{start_date}' AND CAST({col} AS DATE) < DATE '{end_date}'"

    def len_fn(self) -> str:
        return "length"

    def lower_fn(self) -> str:
        return "lower"

    def trim_fn(self) -> str:
        return "trim"

    def distinct_string_expr(self, expr: str) -> str:
        return f"lower(trim({expr}))"

    def is_not_null_ratio(self, col: str) -> str:
        return f"CAST(count({col}) AS DOUBLE)/count(*)"

    def count_distinct(self, expr: str) -> str:
        return f"CAST(count(DISTINCT {expr}) AS DOUBLE)/count(*)"


class AthenaConnector(Connector):
    dialect = AthenaDialect()

    def __init__(self, params: ConnectionParams):
        super().__init__(params)
        s3_staging_dir = params.params.get("s3_staging_dir")
        if not s3_staging_dir:
            raise ValueError(
                "Koneksi Athena butuh 's3_staging_dir' di parameter tambahan "
                "(mis. s3://bucket-name/athena-results/) -- tempat Athena "
                "menulis hasil query."
            )
        self._conn = connect(
            aws_access_key_id=params.username or None,
            aws_secret_access_key=params.password or None,
            region_name=params.host,
            s3_staging_dir=s3_staging_dir,
            work_group=params.params.get("work_group") or "primary",
            schema_name=params.database or None,
        )

    def query_df(self, sql: str) -> pd.DataFrame:
        cursor = self._conn.cursor()
        cursor.execute(sql)
        return as_pandas(cursor)

    def get_schema(self, database: str, table: str) -> pd.DataFrame:
        q = (
            "SELECT column_name, data_type AS column_type FROM information_schema.columns "
            f"WHERE table_schema = '{database}' AND table_name = '{table}' ORDER BY ordinal_position"
        )
        return self.query_df(q)

    def list_tables(self, database: str) -> list[str]:
        q = f"SELECT table_name AS t FROM information_schema.tables WHERE table_schema = '{database}' ORDER BY table_name"
        df = self.query_df(q)
        return df["t"].tolist() if "t" in df.columns else []

    def get_schemas_bulk(self, database: str, tables: list[str]) -> dict[str, list[str]]:
        if not tables:
            return {}
        in_list = ", ".join(f"'{t}'" for t in tables)
        q = (
            "SELECT table_name AS t, column_name FROM information_schema.columns "
            f"WHERE table_schema = '{database}' AND table_name IN ({in_list}) "
            "ORDER BY table_name, ordinal_position"
        )
        df = self.query_df(q)
        result: dict[str, list[str]] = {t: [] for t in tables}
        if "t" in df.columns:
            for t, group in df.groupby("t"):
                result[t] = group["column_name"].tolist()
        return result

    def get_primary_key(self, database: str, table: str) -> list[str]:
        # Athena/Glue tables are external (S3-backed) and have no PRIMARY
        # KEY concept -- unlike ClickHouse's sorting_key, there's no natural-
        # key signal to fall back to either. Callers already handle an empty
        # result by defaulting key_columns to ["id"].
        return []

    def _probe_sql(self) -> str:
        return "SELECT 1"

    def close(self) -> None:
        self._conn.close()
