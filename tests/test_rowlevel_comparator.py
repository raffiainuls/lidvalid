import pandas as pd

from validation_core.rowlevel.comparator import (
    column_diff_mask,
    composite_key,
    compare_chunk_multi,
)


class TestCompositeKey:
    def test_single_column(self):
        df = pd.DataFrame({"id": [1, 2, 3]})
        result = composite_key(df, ["id"])
        assert list(result) == ["1", "2", "3"]

    def test_multi_column_joined_with_underscore(self):
        df = pd.DataFrame({"order_id": [1, 1], "material_id": [10, 20]})
        result = composite_key(df, ["order_id", "material_id"])
        assert list(result) == ["1_10", "1_20"]


class TestColumnDiffMask:
    def test_numeric_nan_both_sides_is_equal(self):
        a = pd.Series([1.0, float("nan")])
        b = pd.Series([1.0, float("nan")])
        assert list(column_diff_mask(a, b, 1.0)) == [False, False]

    def test_numeric_real_difference(self):
        a = pd.Series([1, 2, 3])
        b = pd.Series([1, 99, 3])
        assert list(column_diff_mask(a, b, 1.0)) == [False, True, False]

    def test_string_compare(self):
        a = pd.Series(["x", "y"])
        b = pd.Series(["x", "z"])
        assert list(column_diff_mask(a, b, 1.0)) == [False, True]

    def test_datetime_compare(self):
        a = pd.to_datetime(pd.Series(["2025-01-01", "2025-02-01"]))
        b = pd.to_datetime(pd.Series(["2025-01-01", "2025-03-01"]))
        assert list(column_diff_mask(a, b, 1.0)) == [False, True]


class TestNumericTolerance:
    """Regression coverage for the precision-tolerance fix: row-level
    numeric diffs were exact-compare only, so two engines serializing the
    *same* float with different precision (e.g. DECIMAL/DOUBLE read back as
    482.437346437 vs 482.43734643734643) showed up as false-positive diffs,
    drowning out real ones in the Value Diffs tab."""

    def test_float_precision_noise_is_not_flagged(self):
        a = pd.Series([482.437346437, 1216.513761467, 1383.486238532])
        b = pd.Series([482.43734643734643, 1216.51376146789, 1383.48623853211])
        assert list(column_diff_mask(a, b, 1.0)) == [False, False, False]

    def test_float_genuine_difference_is_still_flagged(self):
        a = pd.Series([150000.0, 482.437346437])
        b = pd.Series([149000.0, 482.43734643734643])
        assert list(column_diff_mask(a, b, 1.0)) == [True, False]

    def test_integer_columns_have_no_tolerance_leakage(self):
        # A relative tolerance calibrated for float noise could otherwise
        # swallow a genuine off-by-one on a large integer (1e-6 * 10_000_000
        # = 10, which would hide a diff of 1) -- integer dtype must stay exact.
        a = pd.Series([10_000_000])
        b = pd.Series([10_000_001])
        assert list(column_diff_mask(a, b, 1.0)) == [True]

    def test_custom_tolerance_can_be_tightened(self):
        a = pd.Series([100.0])
        b = pd.Series([100.00001])  # diff = 1e-5
        assert list(column_diff_mask(a, b, 1.0)) == [False]  # default tolerance (rel_tol=1e-6) absorbs it
        assert list(column_diff_mask(a, b, 1.0, rel_tol=1e-9, abs_tol=1e-12)) == [True]  # tightened catches it


class TestCompareChunkMulti:
    def _frames(self):
        source = pd.DataFrame({"id": [1, 2, 3, 4], "amount": [10, 20, 30, 40]})
        target = pd.DataFrame({"id": [2, 3, 4, 5], "amount": [20, 999, 40, 50]})
        return source, target

    def test_missing_direction_semantics(self):
        source, target = self._frames()
        missing_in_source, missing_in_target, diffs = compare_chunk_multi(
            source, target, ["id"], ["amount"], "full", 1.0,
        )
        # id=1 only in source -> target is missing it
        assert missing_in_target == ["1"]
        # id=5 only in target -> source is missing it
        assert missing_in_source == ["5"]

    def test_value_diff_long_format(self):
        source, target = self._frames()
        _, _, diffs = compare_chunk_multi(source, target, ["id"], ["amount"], "full", 1.0)
        assert diffs == [{"key": "3", "column": "amount", "source_value": 30, "target_value": 999}]

    def test_missing_mode_skips_value_diffs_even_if_value_columns_given(self):
        source, target = self._frames()
        _, _, diffs = compare_chunk_multi(source, target, ["id"], ["amount"], "missing", 1.0)
        assert diffs == []

    def test_empty_chunk_is_normalized_not_crashed(self):
        empty_source = pd.DataFrame()
        empty_target = pd.DataFrame()
        missing_in_source, missing_in_target, diffs = compare_chunk_multi(
            empty_source, empty_target, ["id"], ["amount"], "full", 1.0,
        )
        assert missing_in_source == [] and missing_in_target == [] and diffs == []

    def test_composite_key_diff(self):
        source = pd.DataFrame({"order_id": [1, 1], "material_id": [10, 20], "qty": [5, 7]})
        target = pd.DataFrame({"order_id": [1, 1], "material_id": [10, 20], "qty": [5, 99]})
        _, _, diffs = compare_chunk_multi(source, target, ["order_id", "material_id"], ["qty"], "full", 1.0)
        assert diffs == [{"key": "1_20", "column": "qty", "source_value": 7, "target_value": 99}]

    def test_float_precision_noise_does_not_flood_diffs(self):
        # Mirrors a real report: a `yearly_need` float column where the two
        # engines return the same value at different precision on every row
        # -- these must NOT show up as value diffs, only the genuine one does.
        source = pd.DataFrame({
            "id": [1, 2, 3],
            "yearly_need": [482.437346437, 1216.513761467, 500.0],
        })
        target = pd.DataFrame({
            "id": [1, 2, 3],
            "yearly_need": [482.43734643734643, 1216.51376146789, 999.0],  # id=3 is a real diff
        })
        _, _, diffs = compare_chunk_multi(source, target, ["id"], ["yearly_need"], "full", 1.0)
        assert diffs == [{"key": "3", "column": "yearly_need", "source_value": 500.0, "target_value": 999.0}]
