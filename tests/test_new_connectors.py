"""Pure dialect-level coverage for the Postgres/Oracle/Athena/MaxCompute
connectors added alongside MySQL/ClickHouse/SQLite. Mirrors
TestDialectDateClamping in test_connectors.py: these are all string-
generation methods, so they're fully testable without a live server.

Postgres was additionally smoke-tested live against a throwaway schema on
this deployment's own Postgres instance (get_schema/list_tables/
get_primary_key/query_df/period_expr/date clamping/date_range_filter all
verified end-to-end) -- Oracle/Athena/MaxCompute could not be, for lack of a
reachable instance in this environment, so their SQL text is reasoned from
each engine's documented syntax rather than verified against a live server.
"""
from validation_core.categories import get_category
from validation_core.connectors.postgres import PostgresDialect
from validation_core.connectors.oracle import OracleDialect
from validation_core.connectors.athena import AthenaDialect
from validation_core.connectors.maxcompute import MaxComputeDialect


class TestPostgresDialect:
    def test_quote_ident(self):
        assert PostgresDialect().quote_ident("order_id") == '"order_id"'

    def test_floor_and_ceiling(self):
        d = PostgresDialect()
        assert d.date_floor_1970('"d"', "date") == "GREATEST(\"d\", '1970-01-01')"
        assert d.date_ceiling('"d"', "date", "2149-06-06") == "LEAST(\"d\", '2149-06-06')"
        assert d.date_ceiling('"d"', "date", None) == '"d"'

    def test_period_expr_uses_named_tokens_not_percent(self):
        d = PostgresDialect()
        assert d.period_expr("created_at", "monthly") == "TO_CHAR(created_at, 'YYYY-MM')"
        assert "%" not in d.period_expr("created_at", "monthly")
        assert d.period_expr("created_at", "yearly") == "TO_CHAR(created_at, 'YYYY')"

    def test_datediff_expr(self):
        d = PostgresDialect()
        assert d.datediff_expr("created_at", "2000-01-01") == "(CAST(created_at AS DATE) - DATE '2000-01-01')"

    def test_is_not_null_ratio_casts_to_avoid_integer_truncation(self):
        d = PostgresDialect()
        assert d.is_not_null_ratio("x") == "CAST(COUNT(x) AS DOUBLE PRECISION)/COUNT(*)"

    def test_table_ref_ignores_database_no_cross_db_qualifying(self):
        # Postgres can't do `otherdb.table` within one connection -- table_ref
        # must not prefix `database` the way MySQL/ClickHouse do.
        d = PostgresDialect()
        assert d.table_ref("somedb", "orders") == "orders"
        assert d.table_ref("somedb", "reporting.orders") == "reporting.orders"


class TestOracleDialect:
    def test_quote_ident_is_unquoted_to_avoid_case_folding_traps(self):
        # Deliberate: quoting would force exact-case matching against
        # ALL_TAB_COLUMNS' (normally upper-case) stored names.
        assert OracleDialect().quote_ident("order_id") == "order_id"

    def test_table_ref_supports_owner_qualifying(self):
        d = OracleDialect()
        assert d.table_ref("HR", "employees") == "HR.employees"
        assert d.table_ref("", "employees") == "employees"

    def test_floor_uses_to_date_for_date_category(self):
        d = OracleDialect()
        assert d.date_floor_1970("d", "date") == "GREATEST(d, TO_DATE('1970-01-01', 'YYYY-MM-DD'))"

    def test_floor_uses_to_timestamp_for_timestamp_category(self):
        d = OracleDialect()
        assert d.date_floor_1970("d", "timestamp") == \
            "GREATEST(d, TO_TIMESTAMP('1970-01-01 00:00:00', 'YYYY-MM-DD HH24:MI:SS'))"

    def test_ceiling_pads_date_only_bound_for_timestamp_category(self):
        d = OracleDialect()
        assert d.date_ceiling("d", "timestamp", "2106-02-07 06:28:15") == \
            "LEAST(d, TO_TIMESTAMP('2106-02-07 06:28:15', 'YYYY-MM-DD HH24:MI:SS'))"
        assert d.date_ceiling("d", "date", "2149-06-06") == "LEAST(d, TO_DATE('2149-06-06', 'YYYY-MM-DD'))"

    def test_ceiling_identity_without_bound(self):
        assert OracleDialect().date_ceiling("d", "date", None) == "d"

    def test_period_expr_uses_named_tokens(self):
        d = OracleDialect()
        assert d.period_expr("d", "monthly") == "TO_CHAR(d, 'YYYY-MM')"
        assert "%" not in d.period_expr("d", "monthly")

    def test_datediff_truncates_time_component(self):
        d = OracleDialect()
        assert d.datediff_expr("d", "2000-01-01") == "(TRUNC(CAST(d AS DATE)) - DATE '2000-01-01')"


class TestAthenaDialect:
    def test_quote_ident(self):
        assert AthenaDialect().quote_ident("order_id") == '"order_id"'

    def test_period_expr_uses_joda_tokens_not_percent(self):
        # format_datetime (not date_format) specifically to avoid any '%'
        # in the SQL text -- PyAthena's DBAPI param-substitution behavior on
        # a literal '%' couldn't be verified against a live endpoint here.
        d = AthenaDialect()
        expr = d.period_expr("created_at", "monthly")
        assert expr == "format_datetime(CAST(created_at AS TIMESTAMP), 'yyyy-MM')"
        assert "%" not in expr

    def test_floor_and_ceiling(self):
        d = AthenaDialect()
        assert d.date_floor_1970("d", "date") == "GREATEST(d, DATE '1970-01-01')"
        assert d.date_floor_1970("d", "timestamp") == "GREATEST(d, TIMESTAMP '1970-01-01 00:00:00')"
        assert d.date_ceiling("d", "date", "2149-06-06") == "LEAST(d, DATE '2149-06-06')"
        assert d.date_ceiling("d", "timestamp", "2106-02-07 06:28:15") == \
            "LEAST(d, TIMESTAMP '2106-02-07 06:28:15')"
        assert d.date_ceiling("d", "date", None) == "d"

    def test_datediff_expr(self):
        d = AthenaDialect()
        assert d.datediff_expr("d", "2000-01-01") == "date_diff('day', DATE '2000-01-01', CAST(d AS DATE))"

    def test_get_primary_key_returns_empty_no_pk_concept(self):
        from validation_core.connectors.athena import AthenaConnector
        # get_primary_key doesn't touch instance state -- callable unbound,
        # no need to construct a real (AWS-session-requiring) connector.
        assert AthenaConnector.get_primary_key(None, "db", "table") == []

    def test_missing_s3_staging_dir_raises_clear_error(self):
        from validation_core.connectors.athena import AthenaConnector
        from validation_core.connectors.base import ConnectionParams
        import pytest
        with pytest.raises(ValueError, match="s3_staging_dir"):
            AthenaConnector(ConnectionParams(engine="athena", host="ap-southeast-1"))


class TestMaxComputeDialect:
    def test_quote_ident_uses_backticks(self):
        assert MaxComputeDialect().quote_ident("order_id") == "`order_id`"

    def test_period_expr(self):
        d = MaxComputeDialect()
        assert d.period_expr("d", "monthly") == "TO_CHAR(d, 'yyyy-mm')"
        assert d.period_expr("d", "yearly") == "TO_CHAR(d, 'yyyy')"

    def test_floor_and_ceiling(self):
        d = MaxComputeDialect()
        assert d.date_floor_1970("d", "date") == "GREATEST(d, TO_DATE('1970-01-01', 'yyyy-mm-dd'))"
        assert d.date_ceiling("d", "date", "2149-06-06") == "LEAST(d, TO_DATE('2149-06-06', 'yyyy-mm-dd'))"
        assert d.date_ceiling("d", "date", None) == "d"

    def test_ceiling_strips_time_component_from_bound(self):
        d = MaxComputeDialect()
        assert d.date_ceiling("d", "timestamp", "2106-02-07 06:28:15") == \
            "LEAST(d, TO_DATE('2106-02-07', 'yyyy-mm-dd'))"


class TestNewEnginesRegistered:
    def test_all_four_engines_are_registered(self):
        from validation_core.connectors import SUPPORTED_ENGINES
        for engine in ("postgres", "oracle", "athena", "maxcompute"):
            assert engine in SUPPORTED_ENGINES


class TestCategoriesCoverNewEngineTypes:
    """Regression coverage for type-string gaps this integration surfaced --
    Postgres's bare 'integer' and Oracle's 'number' both fell through to the
    'string' fallback before these were added, silently skipping every
    stat/period comparison for the affected columns."""

    def test_postgres_integer(self):
        assert get_category("integer") == "numeric"

    def test_postgres_character_varying_is_string(self):
        assert get_category("character varying") == "string"

    def test_postgres_timestamp_without_time_zone(self):
        assert get_category("timestamp without time zone") == "timestamp"

    def test_oracle_number(self):
        assert get_category("NUMBER") == "numeric"

    def test_oracle_varchar2_falls_back_to_string(self):
        assert get_category("VARCHAR2") == "string"
