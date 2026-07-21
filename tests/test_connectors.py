"""Coverage for connector-level primary-key/sorting-key discovery
(get_primary_key) — backs the "auto-fill key columns from DDL" feature in
the config-builder's Auto-suggest flow. MySQL/ClickHouse need a live server
so only the pure parsing logic (parse_sorting_key) is unit-tested for
ClickHouse; SQLite is exercised end-to-end since it's a real local engine.
"""
import sqlite3

from validation_core.connectors import ConnectionParams, create_connector
from validation_core.connectors.clickhouse import ClickHouseDialect, clickhouse_date_max, parse_sorting_key
from validation_core.connectors.mysql import MySqlDialect
from validation_core.connectors.sqlite_demo import SqliteDialect


class TestSqlitePrimaryKey:
    def test_single_column_primary_key(self, tmp_path):
        path = tmp_path / "single.sqlite"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        conn.commit()
        conn.close()

        connector = create_connector(ConnectionParams(engine="sqlite", database=str(path)))
        try:
            assert connector.get_primary_key("", "t") == ["id"]
        finally:
            connector.close()

    def test_composite_primary_key_preserves_declared_order(self, tmp_path):
        path = tmp_path / "composite.sqlite"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE ws_order_item_stocks ("
            "order_id INTEGER, material_id INTEGER, qty REAL, "
            "PRIMARY KEY (order_id, material_id))"
        )
        conn.commit()
        conn.close()

        connector = create_connector(ConnectionParams(engine="sqlite", database=str(path)))
        try:
            assert connector.get_primary_key("", "ws_order_item_stocks") == ["order_id", "material_id"]
        finally:
            connector.close()

    def test_no_explicit_primary_key_returns_empty(self, tmp_path):
        path = tmp_path / "nopk.sqlite"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE t (a TEXT, b TEXT)")
        conn.commit()
        conn.close()

        connector = create_connector(ConnectionParams(engine="sqlite", database=str(path)))
        try:
            assert connector.get_primary_key("", "t") == []
        finally:
            connector.close()


class TestClickHouseSortingKeyParsing:
    def test_single_column(self):
        assert parse_sorting_key("id") == ["id"]

    def test_composite_plain_columns(self):
        assert parse_sorting_key("order_id, material_id") == ["order_id", "material_id"]

    def test_expression_tokens_are_dropped(self):
        # toYYYYMM(created_at) isn't usable as a key_column (can't be quoted
        # / BETWEEN'd as a bare identifier) -- only the plain `id` survives.
        assert parse_sorting_key("toYYYYMM(created_at), id") == ["id"]

    def test_all_expression_tokens_yields_empty(self):
        assert parse_sorting_key("cityHash64(id)") == []

    def test_empty_or_none_returns_empty(self):
        assert parse_sorting_key("") == []
        assert parse_sorting_key(None) == []


class TestClickHouseStreamFailureMessage:
    """Regression: a chunk fetch that exceeded send_receive_timeout (or got
    its connection cut server-side) surfaced as StreamFailureError with an
    EMPTY message -- the run's error column literally showed nothing after
    'StreamFailureError:'. Real incident: datamart_logger_monitoring, whole
    table collapsed into one >10-minute chunk vs the old 600s timeout."""

    def test_empty_stream_failure_gets_actionable_message(self):
        from validation_core.connectors.clickhouse import ClickHouseConnector

        msg = ClickHouseConnector._stream_failure_msg(Exception(""), "SELECT * FROM t")
        assert "send_receive_timeout" in msg
        assert "SELECT * FROM t" in msg
        # must be classified transient by runner/retry.py so network blips retry
        from validation_core.runner.retry import is_transient_error
        assert is_transient_error(RuntimeError(msg))

    def test_non_empty_stream_failure_keeps_original_detail(self):
        from validation_core.connectors.clickhouse import ClickHouseConnector

        msg = ClickHouseConnector._stream_failure_msg(Exception("Code: 241. Memory limit exceeded"), "SELECT 1")
        assert "Memory limit exceeded" in msg


class TestClickHouseStreamSelfHeal:
    """A StreamFailureError leaves the client's HTTP session desynced --
    retrying on the same client is pointless. The connector must rebuild the
    client (fresh connection) and re-run the query, up to STREAM_RETRIES
    times, before surfacing the failure. Real incident: chunk 4/7 of
    datamart_logger_monitoring desynced mid-stream ("unrecognized data found
    in stream: <raw float64 bytes>") and killed the whole table."""

    def _connector_with_fake_clients(self, monkeypatch, clients):
        import validation_core.connectors.clickhouse as ch_module
        from validation_core.connectors.base import ConnectionParams

        clients_iter = iter(clients)
        monkeypatch.setattr(ch_module.clickhouse_connect, "get_client",
                            lambda **kw: next(clients_iter))
        return ch_module.ClickHouseConnector(ConnectionParams(engine="clickhouse", host="fake"))

    def test_desync_rebuilds_client_and_retries(self, monkeypatch):
        from clickhouse_connect.driver.exceptions import StreamFailureError
        import pandas as pd

        class _FailingClient:
            def query_df(self, sql):
                raise StreamFailureError("unrecognized data found in stream: `69ec`")

            def close(self):
                pass

        class _HealthyClient:
            def query_df(self, sql):
                return pd.DataFrame({"ok": [1]})

            def close(self):
                pass

        conn = self._connector_with_fake_clients(monkeypatch, [_FailingClient(), _HealthyClient()])
        df = conn.query_df("SELECT 1")
        assert df["ok"].iloc[0] == 1  # healed on the rebuilt client

    def test_persistent_desync_gives_up_with_clear_error(self, monkeypatch):
        import pytest
        from clickhouse_connect.driver.exceptions import StreamFailureError

        class _AlwaysFailing:
            def query_df(self, sql):
                raise StreamFailureError("unrecognized data found in stream: `69ec`")

            def close(self):
                pass

        conn = self._connector_with_fake_clients(
            monkeypatch, [_AlwaysFailing(), _AlwaysFailing(), _AlwaysFailing(), _AlwaysFailing()])
        with pytest.raises(RuntimeError) as ei:
            conn.query_df("SELECT 1")
        assert "unrecognized data" in str(ei.value)
        assert "dicoba ulang" in str(ei.value)
        # residual failures must be table-level retryable too
        from validation_core.runner.retry import is_transient_error
        assert is_transient_error(ei.value)

    def test_compression_disabled_by_default_but_overridable(self, monkeypatch):
        import validation_core.connectors.clickhouse as ch_module
        from validation_core.connectors.base import ConnectionParams

        captured = {}

        def fake_get_client(**kw):
            captured.update(kw)

            class _C:
                def close(self):
                    pass
            return _C()

        monkeypatch.setattr(ch_module.clickhouse_connect, "get_client", fake_get_client)
        ch_module.ClickHouseConnector(ConnectionParams(engine="clickhouse", host="fake"))
        assert captured["compress"] is False  # desync-prevention default

        ch_module.ClickHouseConnector(ConnectionParams(
            engine="clickhouse", host="fake", params={"compress": True}))
        assert captured["compress"] is True


class TestClickHouseDateMax:
    """Regression coverage for the ClickHouse max-date ceiling clamp (see
    README/TECHNICAL, added alongside the pre-1970 floor). Sources: ClickHouse
    docs -- Date [1970-01-01, 2149-06-06], Date32 [1900-01-01, 2299-12-31],
    DateTime [1970-01-01, 2106-02-07 06:28:15], DateTime64 [1900-01-01,
    2299-12-31] (ignoring the max-nanosecond-precision 2262 narrowing)."""

    def test_date(self):
        assert clickhouse_date_max("Date") == "2149-06-06"

    def test_date32_has_a_wider_bound_than_plain_date(self):
        assert clickhouse_date_max("Date32") == "2299-12-31"

    def test_datetime(self):
        assert clickhouse_date_max("DateTime") == "2106-02-07 06:28:15"

    def test_datetime_with_timezone_arg(self):
        assert clickhouse_date_max("DateTime('UTC')") == "2106-02-07 06:28:15"

    def test_datetime64_has_a_wider_bound_than_plain_datetime(self):
        assert clickhouse_date_max("DateTime64(3)") == "2299-12-31 23:59:59"

    def test_nullable_prefix_stripped(self):
        assert clickhouse_date_max("Nullable(Date)") == "2149-06-06"
        assert clickhouse_date_max("Nullable(DateTime64(6))") == "2299-12-31 23:59:59"

    def test_case_insensitive(self):
        assert clickhouse_date_max("date32") == "2299-12-31"

    def test_unknown_type_falls_back_to_widest_bound(self):
        assert clickhouse_date_max("SomeWeirdFutureType") == "2299-12-31 23:59:59"


class TestDialectDateClamping:
    """Both floor (1970 epoch) and ceiling (ClickHouse's type-specific max)
    are applied identically on EVERY engine (see AggregateValidator's
    docstring / README) -- this validator compares any engine pair, not just
    MySQL<->ClickHouse, so it can't assume only one side needs clamping."""

    def test_mysql_floor_ignores_category(self):
        d = MySqlDialect()
        assert d.date_floor_1970("`d`", "date") == "GREATEST(`d`, '1970-01-01')"
        assert d.date_floor_1970("`d`", "timestamp") == "GREATEST(`d`, '1970-01-01')"

    def test_mysql_ceiling(self):
        d = MySqlDialect()
        assert d.date_ceiling("`d`", "date", "2149-06-06") == "LEAST(`d`, '2149-06-06')"

    def test_mysql_ceiling_identity_without_bound(self):
        d = MySqlDialect()
        assert d.date_ceiling("`d`", "date", None) == "`d`"

    def test_mysql_has_no_max_bound_of_its_own(self):
        # MySQL DATE/DATETIME reach year 9999 -- no realistic ceiling to mirror.
        assert MySqlDialect().date_max_bound("datetime") is None

    def test_sqlite_floor(self):
        d = SqliteDialect()
        assert d.date_floor_1970('"d"', "date") == 'MAX("d", \'1970-01-01\')'

    def test_sqlite_ceiling(self):
        d = SqliteDialect()
        assert d.date_ceiling('"d"', "date", "2149-06-06") == 'MIN("d", \'2149-06-06\')'

    def test_sqlite_ceiling_identity_without_bound(self):
        d = SqliteDialect()
        assert d.date_ceiling('"d"', "date", None) == '"d"'

    def test_sqlite_has_no_max_bound_of_its_own(self):
        assert SqliteDialect().date_max_bound("timestamp") is None

    def test_clickhouse_floor_uses_toDate_for_date_category(self):
        d = ClickHouseDialect()
        assert d.date_floor_1970("`d`", "date") == "if(isNull(`d`), NULL, greatest(`d`, toDate('1970-01-01')))"

    def test_clickhouse_floor_uses_toDateTime_for_timestamp_category(self):
        d = ClickHouseDialect()
        assert d.date_floor_1970("`d`", "timestamp") == \
            "if(isNull(`d`), NULL, greatest(`d`, toDateTime('1970-01-01 00:00:00')))"

    def test_clickhouse_floor_preserves_null_regression(self):
        # Real production bug: since ClickHouse 24.12, greatest()/least()
        # IGNORE NULL arguments instead of propagating NULL (unlike MySQL),
        # so a genuinely-NULL deleted_at silently became '1970-01-01' --
        # corrupting MIN/MAX and inflating uniqueness. The `if(isNull(...))`
        # guard must be present regardless of internal SQL formatting.
        d = ClickHouseDialect()
        assert d.date_floor_1970("`d`", "date").startswith("if(isNull(`d`), NULL, ")

    def test_clickhouse_ceiling_uses_toDate_for_date_category(self):
        d = ClickHouseDialect()
        assert d.date_ceiling("`d`", "date", "2149-06-06") == "if(isNull(`d`), NULL, least(`d`, toDate('2149-06-06')))"

    def test_clickhouse_ceiling_uses_toDateTime_for_timestamp_category(self):
        d = ClickHouseDialect()
        assert d.date_ceiling("`d`", "timestamp", "2106-02-07 06:28:15") == \
            "if(isNull(`d`), NULL, least(`d`, toDateTime('2106-02-07 06:28:15')))"

    def test_clickhouse_ceiling_preserves_null_regression(self):
        d = ClickHouseDialect()
        assert d.date_ceiling("`d`", "date", "2149-06-06").startswith("if(isNull(`d`), NULL, ")

    def test_clickhouse_ceiling_identity_without_bound(self):
        d = ClickHouseDialect()
        assert d.date_ceiling("`d`", "date", None) == "`d`"

    def test_clickhouse_date_max_bound_delegates_to_module_function(self):
        assert ClickHouseDialect().date_max_bound("Date32") == clickhouse_date_max("Date32") == "2299-12-31"
