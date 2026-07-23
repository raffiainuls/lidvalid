"""ClickHouseConnector.query_df's overflow-safety fallback.

Real incident: a row-level chunk fetch crashed the entire table validation
with pandas OutOfBoundsDatetime -- a DateTime64(9) column held a value at
ClickHouse's own max (2299-12-31), which is valid there but overflows
pandas' datetime64[ns] (~2262-04-11 ceiling). clickhouse_connect's default
"native" read format tries to build a datetime64[ns] column and blows up on
the whole query, not just the offending row.
"""
from __future__ import annotations

import pandas as pd
import pytest

from validation_core.connectors.clickhouse import ClickHouseConnector


def _make_connector() -> ClickHouseConnector:
    """Bypasses __init__ (which opens a real connection) -- these tests only
    exercise _query_df_overflow_safe, which depends solely on self._client."""
    return object.__new__(ClickHouseConnector)


class _FakeClient:
    def __init__(self, ok_df: pd.DataFrame, fail_once: bool = True):
        self.ok_df = ok_df
        self.fail_once = fail_once
        self.calls: list[dict] = []

    def query_df(self, sql: str, query_formats: dict | None = None):
        self.calls.append({"sql": sql, "query_formats": query_formats})
        if self.fail_once and query_formats is None:
            raise pd.errors.OutOfBoundsDatetime("Out of bounds nanosecond timestamp: 2299-12-31T00:00:00.000")
        return self.ok_df


class TestQueryDfOverflowSafe:
    def test_retries_with_int_format_on_overflow_and_succeeds(self):
        conn = _make_connector()
        expected = pd.DataFrame({"id": [1, 2], "ts": [2299123456789, 2299123456790]})
        conn._client = _FakeClient(expected)

        result = conn._query_df_overflow_safe("SELECT id, ts FROM t")

        assert result is expected
        assert len(conn._client.calls) == 2
        assert conn._client.calls[0]["query_formats"] is None
        assert conn._client.calls[1]["query_formats"] == {"*Date*": "int"}

    def test_no_retry_needed_when_query_succeeds_first_try(self):
        conn = _make_connector()
        expected = pd.DataFrame({"id": [1]})
        conn._client = _FakeClient(expected, fail_once=False)

        result = conn._query_df_overflow_safe("SELECT id FROM t")

        assert result is expected
        assert len(conn._client.calls) == 1

    def test_plain_overflow_error_is_also_caught(self):
        """clickhouse_connect's OutOfBoundsDatetime is chained from a plain
        OverflowError (see numpy's npy_datetimestruct_to_datetime) -- some
        pandas/numpy version combos may surface the OverflowError directly."""
        class _OverflowClient(_FakeClient):
            def query_df(self, sql, query_formats=None):
                self.calls.append({"sql": sql, "query_formats": query_formats})
                if query_formats is None:
                    raise OverflowError("Overflow occurred in npy_datetimestruct_to_datetime")
                return self.ok_df

        conn = _make_connector()
        expected = pd.DataFrame({"id": [1]})
        conn._client = _OverflowClient(expected)

        result = conn._query_df_overflow_safe("SELECT id FROM t")
        assert result is expected

    def test_other_exceptions_are_not_swallowed(self):
        class _BoomClient(_FakeClient):
            def query_df(self, sql, query_formats=None):
                raise ValueError("some unrelated failure")

        conn = _make_connector()
        conn._client = _BoomClient(pd.DataFrame())
        with pytest.raises(ValueError, match="some unrelated failure"):
            conn._query_df_overflow_safe("SELECT id FROM t")
