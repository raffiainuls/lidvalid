"""ClickHouse dialect + connector.

Ported from `validation-data/scripts/db_validator.py` (aggregate side, FINAL /
toString wrapping) and `validation_database/running_validation.py`
(`fetch_data_clickhouse` streaming fetch, reserved-word backtick quoting).
"""
from __future__ import annotations

import re

import pandas as pd
import clickhouse_connect

from .base import Connector, ConnectionParams, Dialect

_SIMPLE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Hard storage-enforced upper bounds per ClickHouse date/datetime type.
# Sources (ClickHouse docs, checked 2026-07): Date [1970-01-01, 2149-06-06];
# Date32 [1900-01-01, 2299-12-31]; DateTime [1970-01-01 00:00:00,
# 2106-02-07 06:28:15] (32-bit seconds-since-epoch); DateTime64
# [1900-01-01, 2299-12-31 23:59:59.999999999] (narrows to 2262-04-11 only at
# the maximum nanosecond precision -- that one-off narrowing isn't
# distinguished here, 2299-12-31 is used for every DateTime64 precision).
_DATE_MAX = "2149-06-06"
_DATE32_MAX = "2299-12-31"
_DATETIME_MAX = "2106-02-07 06:28:15"
_DATETIME64_MAX = "2299-12-31 23:59:59"


def clickhouse_date_max(col_type: str) -> str:
    """The upper bound ClickHouse can physically store for `col_type`.

    A value beyond this gets silently truncated to it at ingestion, same
    motivating problem as `date_floor_1970` but at the other end of the
    range: a genuinely-later value on the OTHER side of a comparison would
    show a false mismatch against ClickHouse's already-clamped one unless
    both sides are capped at the same bound first. Standalone/pure so it's
    unit-testable without a live ClickHouse connection (mirrors
    `parse_sorting_key` above).
    """
    # Order matters: "datetime"/"datetime64" both start with "date" too, so
    # the more specific prefixes must be checked FIRST or they'd never be
    # reached (a bug caught by tests -- DateTime64 was silently getting
    # Date's narrower bound instead of its own wider one).
    t = col_type.lower().strip()
    if t.startswith("nullable("):
        t = t[9:-1]
    if t.startswith("datetime64"):
        return _DATETIME64_MAX
    if t.startswith("datetime") or t.startswith("timestamp"):
        return _DATETIME_MAX
    if t.startswith("date32"):
        return _DATE32_MAX
    if t.startswith("date"):
        return _DATE_MAX
    return _DATETIME64_MAX  # unrecognized timestamp-ish type -- widest safe bound


def parse_sorting_key(raw: str) -> list[str]:
    """Split a ClickHouse `sorting_key` string (the table's ORDER BY clause)
    into plain column names, dropping any expression tokens (e.g.
    "toYYYYMM(created_at), id" -> ["id"]) that can't be used as a
    key_column. Pulled out as a standalone function so the parsing logic is
    unit-testable without a live ClickHouse connection."""
    if not raw:
        return []
    tokens = [t.strip() for t in raw.split(",")]
    return [t for t in tokens if _SIMPLE_IDENT_RE.match(t)]


class ClickHouseDialect(Dialect):
    name = "clickhouse"
    supports_final = True

    def quote_ident(self, col: str) -> str:
        return f"`{col}`"

    def table_ref(self, database: str, table: str, final: bool = False) -> str:
        ref = f"{database}.{table}"
        if final:
            ref += " FINAL"
        return ref

    def wrap_minmax_datetime(self, expr: str) -> str:
        # Prevents pandas OutOfBoundsDatetime on sentinel dates (e.g. 2299-12-31).
        return f"toString({expr})"

    def date_floor_1970(self, expr: str, category: str) -> str:
        lit = "toDate('1970-01-01')" if category == "date" else "toDateTime('1970-01-01 00:00:00')"
        # Since ClickHouse 24.12, greatest()/least() IGNORE NULL arguments
        # instead of propagating NULL (the opposite of MySQL/SQLite's
        # GREATEST/LEAST, and a real production bug caught by a user: a
        # genuinely NULL `deleted_at` got silently turned into
        # '1970-01-01', corrupting MIN/MAX and inflating uniqueness --
        # COUNT(DISTINCT ...) no longer skipped it like a true NULL would
        # be. `if(isNull(x), NULL, ...)` re-asserts NULL explicitly so this
        # doesn't depend on ClickHouse version behavior at all. See
        # https://github.com/ClickHouse/ClickHouse/issues/65039
        return f"if(isNull({expr}), NULL, greatest({expr}, {lit}))"

    def date_ceiling(self, expr: str, category: str, max_value: str | None) -> str:
        if not max_value:
            return expr
        caster = "toDate" if category == "date" else "toDateTime"
        # Same NULL-preservation concern as date_floor_1970 above -- and
        # necessary even though `expr` here is usually ALREADY the
        # NULL-safe output of date_floor_1970: least() would otherwise
        # re-introduce the same bug on top of it.
        return f"if(isNull({expr}), NULL, least({expr}, {caster}('{max_value}')))"

    def date_max_bound(self, col_type: str) -> str:
        return clickhouse_date_max(col_type)

    def period_expr(self, col: str, granularity: str) -> str:
        if granularity == 'monthly':
            return f"formatDateTime(toDate({col}), '%Y-%m')"
        return f"toString(toYear(toDate({col})))"

    def datediff_expr(self, col: str, ref_date: str) -> str:
        return f"dateDiff('day', toDate('{ref_date}'), toDate({col}))"

    def date_range_filter(self, col: str, start_date: str, end_date: str) -> str:
        return f" WHERE toDate({col}) >= '{start_date}' AND toDate({col}) < '{end_date}'"

    def len_fn(self) -> str:
        return "length"

    def lower_fn(self) -> str:
        return "lower"

    def trim_fn(self) -> str:
        return "trim"

    def distinct_string_expr(self, expr: str) -> str:
        return f"lower(trim({expr}))"

    def is_not_null_ratio(self, col: str) -> str:
        return f"countIf(isNotNull({col}))/count(*)"


class ClickHouseConnector(Connector):
    dialect = ClickHouseDialect()

    # Stream-desync self-heal: how many times query_df rebuilds its client
    # and re-runs a query that died with StreamFailureError before giving up.
    STREAM_RETRIES = 2

    def __init__(self, params: ConnectionParams):
        super().__init__(params)
        self._client = self._build_client()

    def _build_client(self):
        params = self.params
        http_port = params.params.get("http_port", params.port or 8123)
        return clickhouse_connect.get_client(
            host=params.host,
            port=int(http_port),
            username=params.username,
            password=params.password,
            connect_timeout=30,
            # 3600 to match the MySQL connector's read/write timeouts. The
            # original 600 was too short for real workloads: a row-level
            # chunk fetch of a wide table (105 columns) whose composite-key
            # id range collapses the whole table into ONE chunk took >10
            # minutes and got its stream cut at exactly the timeout --
            # surfacing as a StreamFailureError with an EMPTY message (see
            # _stream_failure_msg below; real incident:
            # datamart_logger_monitoring).
            send_receive_timeout=int(params.params.get("send_receive_timeout", 3600)),
            # Compression OFF by default: repeated real incidents of
            # clickhouse_connect's native-format parser desyncing mid-stream
            # ("unrecognized data found in stream: <hex that is plainly raw
            # float64 column data read at the wrong offset>") on long
            # streaming responses through this deployment's proxy/LB.
            # Compressed streams are the classic trigger -- one mangled/
            # re-chunked frame boundary and every byte after it is misread.
            # Uncompressed costs bandwidth but removes the failure mode;
            # opt back in per-connection with params {"compress": true}.
            compress=bool(params.params.get("compress", False)),
        )

    def _rebuild_client(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
        self._client = self._build_client()

    @staticmethod
    def _stream_failure_msg(exc: Exception, sql: str) -> str:
        """clickhouse_connect raises StreamFailureError with whatever error
        text arrived on the wire -- when the connection is simply cut
        (client-side send_receive_timeout, server killing the query, LB
        idle-timeout), that text is EMPTY and the user sees a blank error.
        Substitute an actionable message. Includes the word "connection" on
        purpose: runner/retry.py classifies it as transient, so a genuine
        network blip gets retried like other connection errors."""
        detail = str(exc).strip()
        if detail:
            return f"ClickHouse stream failure: {detail}"
        return (
            "ClickHouse connection/stream terputus di tengah transfer tanpa pesan error dari "
            "server. Penyebab paling umum: (1) query/transfer melebihi send_receive_timeout "
            "client, (2) server menghentikan query (max_execution_time / max_memory_usage), "
            "(3) network/VPN putus. Untuk tabel lebar yang chunk-nya besar, pertimbangkan "
            "memperkecil id_chunk_size di settings config. "
            f"Query (awal): {sql[:200]}"
        )

    def query_df(self, sql: str) -> pd.DataFrame:
        from clickhouse_connect.driver.exceptions import StreamFailureError

        # Self-heal on stream desync: a StreamFailureError leaves the client's
        # HTTP session in an unknown state (the parser lost its place in the
        # stream), so retrying on the SAME client is pointless -- rebuild the
        # client (fresh connection) and re-run. Without this, one desynced
        # chunk killed the whole table, and the table-level retry re-ran
        # Tier 1 + every earlier chunk from scratch.
        last_exc: Exception | None = None
        for attempt in range(1 + self.STREAM_RETRIES):
            try:
                return self._client.query_df(sql)
            except StreamFailureError as exc:
                last_exc = exc
                if attempt < self.STREAM_RETRIES:
                    self._rebuild_client()
                    continue
        raise RuntimeError(
            self._stream_failure_msg(last_exc, sql)
            + f" (sudah dicoba ulang {self.STREAM_RETRIES}x dengan koneksi baru)"
        ) from last_exc

    def query_df_stream(self, sql: str):
        # Streams native blocks in a single request -- no LIMIT/OFFSET pagination
        # (which is O(n^2) on ClickHouse and times out on large tables).
        from clickhouse_connect.driver.exceptions import StreamFailureError
        try:
            with self._client.query_df_stream(sql) as stream:
                for batch_df in stream:
                    if not batch_df.empty:
                        yield batch_df
        except StreamFailureError as exc:
            raise RuntimeError(self._stream_failure_msg(exc, sql)) from exc

    def get_schema(self, database: str, table: str) -> pd.DataFrame:
        q = (
            f"SELECT name AS column_name, type AS column_type "
            f"FROM system.columns "
            f"WHERE database = '{database}' AND table = '{table}' "
            f"ORDER BY position"
        )
        return self.query_df(q)

    def list_tables(self, database: str) -> list[str]:
        q = f"SELECT name AS t FROM system.tables WHERE database = '{database}' ORDER BY name"
        df = self.query_df(q)
        return df["t"].tolist() if "t" in df.columns else []

    def get_primary_key(self, database: str, table: str) -> list[str]:
        # ClickHouse has no PRIMARY KEY concept like MySQL -- `sorting_key`
        # (the table's ORDER BY clause) is the closest natural-key signal,
        # and is the same field used to dedup ReplacingMergeTree rows, so it
        # usually IS the intended composite key. It can contain expressions
        # (e.g. "toYYYYMM(created_at), id") rather than bare column names --
        # those tokens aren't usable as a key_column (can't be quoted/BETWEEN'd
        # as-is), so only plain-identifier tokens are kept; the rest are
        # silently dropped rather than producing a broken suggestion.
        q = f"SELECT sorting_key AS sk FROM system.tables WHERE database = '{database}' AND name = '{table}'"
        df = self.query_df(q)
        if df.empty:
            return []
        return parse_sorting_key(str(df["sk"].iloc[0] or ""))

    def _probe_sql(self) -> str:
        return "SELECT 1 AS ok"

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
