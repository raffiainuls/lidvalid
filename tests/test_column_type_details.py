"""Coverage for `_build_column_type_details` (run_service.py), which backs
the "Tipe Kolom" drilldown tab: a full source-vs-target column TYPE
comparison for every column (shared, source-only, target-only). This is
distinct from the "stat" FindingAggregate rows, which only ever compare
METRIC VALUES (min/max/sum/...) assuming both sides' categories already
match -- a genuine schema-level type drift between source and target was
previously invisible everywhere: `AggregateValidator._shared_stat_cols`
silently SKIPS a column from stat comparison when categories disagree,
with no finding/warning surfaced anywhere.
"""
import pandas as pd

from app.services.run_service import _build_column_type_details


class _FakeAggregateResult:
    def __init__(self, column_details: pd.DataFrame):
        self.column_details = column_details


def test_flags_category_mismatch_for_shared_columns():
    df = pd.DataFrame([
        {"column_name": "id", "source_column_type": "int", "target_column_type": "Int64"},
        {"column_name": "amount", "source_column_type": "decimal", "target_column_type": "String"},
    ])
    rows = _build_column_type_details(_FakeAggregateResult(df))
    by_col = {r["column"]: r for r in rows}

    assert by_col["id"]["category_match"] is True
    assert by_col["id"]["source_type"] == "int"
    assert by_col["id"]["target_type"] == "Int64"

    assert by_col["amount"]["category_match"] is False


def test_source_only_and_target_only_columns_have_no_category_match_verdict():
    df = pd.DataFrame([
        {"column_name": "legacy_col", "source_column_type": "varchar", "target_column_type": None},
        {"column_name": "new_col", "source_column_type": None, "target_column_type": "UInt8"},
    ])
    rows = _build_column_type_details(_FakeAggregateResult(df))
    by_col = {r["column"]: r for r in rows}

    assert by_col["legacy_col"]["category_match"] is None
    assert by_col["legacy_col"]["target_type"] is None
    assert by_col["new_col"]["category_match"] is None
    assert by_col["new_col"]["source_type"] is None


def test_empty_column_details_yields_empty_list():
    assert _build_column_type_details(_FakeAggregateResult(pd.DataFrame())) == []
