import math

from validation_core.categories import get_category, values_match, ceil_stat, META_COLUMNS


class TestGetCategory:
    def test_basic_numeric_types(self):
        for t in ("int", "bigint", "decimal(10,2)", "float", "UInt16", "Int64"):
            assert get_category(t) == "numeric", t

    def test_year_fixed_as_numeric(self):
        # Known issue #1 from validation-data/CLAUDE.md: MySQL YEAR fell back
        # to 'string' while ClickHouse mirrors it as UInt16 (numeric) ->
        # length(UInt16) crash. Must map to numeric now.
        assert get_category("YEAR") == "numeric"
        assert get_category("year") == "numeric"

    def test_string_types(self):
        for t in ("varchar(255)", "CHAR", "text", "String", "Enum8('a'=1)"):
            assert get_category(t) == "string", t

    def test_date_and_timestamp(self):
        assert get_category("date") == "date"
        assert get_category("DateTime") == "timestamp"
        assert get_category("datetime64(3)") == "timestamp"

    def test_date32_is_date_not_string(self):
        # ClickHouse's wider-range Date32 didn't match any keyword before it
        # was added explicitly -- it fell through to the 'string' fallback,
        # silently skipping it from floor-1970/max-date ceiling handling.
        assert get_category("Date32") == "date"
        assert get_category("Nullable(Date32)") == "date"

    def test_boolean(self):
        assert get_category("boolean") == "boolean"
        assert get_category("Bool") == "boolean"

    def test_nullable_prefix_stripped_recursively(self):
        assert get_category("Nullable(DateTime)") == "timestamp"
        assert get_category("Nullable(Nullable(Int32))") == "numeric"

    def test_array_prefix(self):
        assert get_category("Array(String)") == "array"
        assert get_category("Array(UInt8)") == "array"

    def test_unknown_type_falls_back_to_string(self):
        assert get_category("some_weird_udt") == "string"


class TestValuesMatch:
    def test_both_nan_is_match(self):
        assert values_match(float("nan"), float("nan")) is True

    def test_ceiling_rounding_absorbs_precision_noise(self):
        # MySQL AVG 4-decimal vs ClickHouse full precision.
        assert values_match(15.5714, 15.571428571428571) is True
        assert ceil_stat(4.0000000000001) == 4  # 1e-9 nudge, doesn't round up to 5

    def test_real_numeric_difference_is_not_a_match(self):
        assert values_match(100, 105) is False

    def test_datetime_string_trailing_zero_milliseconds(self):
        # MySQL '...00:38:00' vs ClickHouse toString() '...00:38:00.000'
        assert values_match("2025-08-14 00:38:00", "2025-08-14 00:38:00.000") is True

    def test_genuinely_different_strings_do_not_match(self):
        assert values_match("abc", "xyz") is False

    def test_one_sided_nan_is_skipped_not_a_mismatch(self):
        assert values_match(float("nan"), 5) is True
        assert values_match(5, float("nan")) is True


def test_meta_columns_is_superset_of_both_legacy_tools():
    # validation-data PIPELINE_COLS ∪ validation_database CLICKHOUSE_META_COLUMNS
    assert META_COLUMNS == {"ingested_at", "version", "_dlt_load_id", "_dlt_id"}
