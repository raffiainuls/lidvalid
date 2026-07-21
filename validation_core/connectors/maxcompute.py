"""Alibaba Cloud MaxCompute (ODPS) dialect + connector.

Uses `pyodps` (the official SDK, `import odps`). Like Athena, MaxCompute has
no host/port -- it's addressed by a region-specific HTTP endpoint. Mapped
onto this app's fixed Connection shape:
  - username -> AccessKey ID
  - password -> AccessKey Secret (stored encrypted at rest, as usual)
  - host     -> MaxCompute endpoint URL (e.g.
                "http://service.cn-shanghai.maxcompute.aliyun.com/api")
  - database -> MaxCompute project name
  - port     -> unused

NOTE: exact SQL syntax below (date literal / GREATEST-LEAST support, TO_CHAR
format tokens) follows MaxCompute's documented, Hive-family SQL dialect, but
could not be verified against a live project in this environment (no
MaxCompute credentials available here, unlike MySQL/ClickHouse which were
tested end-to-end). Verify against a real project before relying on the
date-clamping/period-breakdown reports for this engine.
"""
from __future__ import annotations

import pandas as pd
from odps import ODPS

from .base import Connector, ConnectionParams, Dialect


class MaxComputeDialect(Dialect):
    name = "maxcompute"
    supports_final = False

    def quote_ident(self, col: str) -> str:
        return f"`{col}`"

    def date_floor_1970(self, expr: str, category: str) -> str:
        return f"GREATEST({expr}, TO_DATE('1970-01-01', 'yyyy-mm-dd'))"

    def date_ceiling(self, expr: str, category: str, max_value: str | None) -> str:
        if not max_value:
            return expr
        value = max_value.split(" ")[0]  # MaxCompute DATETIME has second precision; date-only bound is enough here
        return f"LEAST({expr}, TO_DATE('{value}', 'yyyy-mm-dd'))"

    def period_expr(self, col: str, granularity: str) -> str:
        if granularity == 'monthly':
            return f"TO_CHAR({col}, 'yyyy-mm')"
        return f"TO_CHAR({col}, 'yyyy')"

    def datediff_expr(self, col: str, ref_date: str) -> str:
        return f"DATEDIFF({col}, TO_DATE('{ref_date}', 'yyyy-mm-dd'), 'dd')"

    def date_range_filter(self, col: str, start_date: str, end_date: str) -> str:
        return (
            f" WHERE {col} >= TO_DATE('{start_date}', 'yyyy-mm-dd') "
            f"AND {col} < TO_DATE('{end_date}', 'yyyy-mm-dd')"
        )

    def len_fn(self) -> str:
        return "LENGTH"

    def lower_fn(self) -> str:
        return "LOWER"

    def trim_fn(self) -> str:
        return "TRIM"

    def distinct_string_expr(self, expr: str) -> str:
        return f"LOWER(TRIM({expr}))"

    def is_not_null_ratio(self, col: str) -> str:
        return f"CAST(COUNT({col}) AS DOUBLE)/COUNT(*)"

    def count_distinct(self, expr: str) -> str:
        return f"CAST(COUNT(DISTINCT {expr}) AS DOUBLE)/COUNT(*)"


class MaxComputeConnector(Connector):
    dialect = MaxComputeDialect()

    def __init__(self, params: ConnectionParams):
        super().__init__(params)
        self._odps = ODPS(
            access_id=params.username,
            secret_access_key=params.password,
            project=params.database,
            endpoint=params.host,
        )

    def query_df(self, sql: str) -> pd.DataFrame:
        with self._odps.execute_sql(sql).open_reader(tunnel=True) as reader:
            return reader.to_pandas()

    def get_schema(self, database: str, table: str) -> pd.DataFrame:
        t = self._odps.get_table(table, project=database or None)
        rows = [
            {"column_name": c.name, "column_type": str(c.type)}
            for c in t.table_schema.simple_columns
        ]
        return pd.DataFrame(rows)

    def list_tables(self, database: str) -> list[str]:
        return [t.name for t in self._odps.list_tables(project=database or None)]

    def get_primary_key(self, database: str, table: str) -> list[str]:
        # MaxCompute tables have no PRIMARY KEY constraint concept.
        return []

    def _probe_sql(self) -> str:
        return "SELECT 1"

    def close(self) -> None:
        # ODPS is a stateless REST client wrapper -- no persistent
        # connection/socket to release.
        pass
