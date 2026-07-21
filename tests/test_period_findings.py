"""Coverage for period-mismatch detail: which column/metric caused a period
to be flagged, not just "this period doesn't match".

Real user confusion this fixes: the Periode tab only ever showed
Source/Target row counts + Δ, so periods with Δ=0 (row counts IDENTICAL on
both sides) still appeared in the mismatch list with no visible reason --
looking like an unexplained false alarm. The actual cause in those rows is
always a per-period STAT mismatch (sum/min/max/datediff of some shared
column) rather than a row-count difference; `match` in
AggregateValidator.gen_report_period_breakdown was always
`row_match & (stat_mismatch == 0)`, so it correctly caught these cases --
the UI just never said WHY.
"""
import pandas as pd

from validation_core.aggregate import AggregateValidator
from validation_core.models import RunSettings

from app import models
from app.services.run_service import _persist_aggregate_findings


class TestMismatchDetailComputation:
    def test_same_row_count_period_still_reports_which_stat_differs(self, orders_pair):
        """Period 2025-05: id=100 isn't missing (row counts match exactly),
        only its `amount` value was changed -- shifting that month's
        SUM/MAX. This is the EXACT scenario that confused the user: Δ=0 yet
        still listed as mismatched."""
        source, target = orders_pair
        validator = AggregateValidator(
            source, target, "", "ws_orders", "", "raw_ws_orders",
            date_column="created_at", settings=RunSettings(),
        )
        result = validator.run()
        row = result.monthly_breakdown[result.monthly_breakdown["period"] == "2025-05"].iloc[0]

        assert bool(row["row_match"]) is True
        assert row["source_row"] == row["target_row"]  # Δ = 0
        assert bool(row["match"]) is False  # yet still flagged...

        details = row["mismatch_detail"]
        assert any(d["column"] == "amount" and d["metric"] == "sum" for d in details)
        assert any(d["column"] == "amount" and d["metric"] == "max" for d in details)

    def test_row_count_mismatch_period_also_gets_stat_detail(self, orders_pair):
        """Period 2025-11 has a genuine missing row (id=10) AND its
        row-count-matching-column stats differ too -- both facts should be
        independently visible."""
        source, target = orders_pair
        validator = AggregateValidator(
            source, target, "", "ws_orders", "", "raw_ws_orders",
            date_column="created_at", settings=RunSettings(),
        )
        result = validator.run()
        row = result.monthly_breakdown[result.monthly_breakdown["period"] == "2025-11"].iloc[0]

        assert bool(row["row_match"]) is False
        assert row["source_row"] != row["target_row"]
        assert len(row["mismatch_detail"]) > 0


class TestParsePeriodAlias:
    def test_simple_metrics(self):
        assert AggregateValidator._parse_period_alias("sum_entity_id") == ("sum", "entity_id")
        assert AggregateValidator._parse_period_alias("min_entity_id") == ("min", "entity_id")
        assert AggregateValidator._parse_period_alias("max_entity_id") == ("max", "entity_id")
        assert AggregateValidator._parse_period_alias("datediff_deleted_at") == ("datediff", "deleted_at")

    def test_len_metrics_dont_misparse_as_min_or_max(self):
        # "min_len_x" must not parse as metric="min", column="len_x"
        assert AggregateValidator._parse_period_alias("min_len_entity_name") == ("min_len", "entity_name")
        assert AggregateValidator._parse_period_alias("max_len_entity_name") == ("max_len", "entity_name")
        assert AggregateValidator._parse_period_alias("sum_len_entity_name") == ("sum_len", "entity_name")

    def test_column_name_containing_metric_word(self):
        # "sum_summary_id" -- greedy prefix match takes "sum_" first, giving
        # column="summary_id". Documents the known limitation rather than
        # silently doing something surprising: a column literally named
        # e.g. "min_x" would misparse. Not a real-world concern (no such
        # columns in this codebase's schemas) but worth being explicit about.
        assert AggregateValidator._parse_period_alias("sum_summary_id") == ("sum", "summary_id")


class TestPersistPeriodFindings:
    """_persist_aggregate_findings must emit ONE finding for the row-count
    reason (only when row counts actually differ) and ONE finding per
    (column, metric) mismatch -- so a Δ=0 period that's still flagged shows
    up with an explanatory row instead of nothing."""

    class _FakeAggregateResult:
        def __init__(self, monthly_breakdown):
            self.column_details = pd.DataFrame()
            self.src_type_details = pd.DataFrame()
            self.tgt_type_details = pd.DataFrame()
            self.monthly_breakdown = monthly_breakdown
            self.yearly_breakdown = pd.DataFrame()

    class _FakeResult:
        def __init__(self, monthly_breakdown, summary_row_match=True):
            self.aggregate = TestPersistPeriodFindings._FakeAggregateResult(monthly_breakdown)
            self.aggregate_summary = {
                "row_match": summary_row_match, "source_rows": 100, "target_rows": 100, "row_diff": 0,
            }

    def test_zero_delta_period_persists_only_metric_findings(self):
        df = pd.DataFrame([{
            "period": "2025-05", "source_row": 17, "target_row": 17, "difference": 0,
            "row_match": True, "match": False,
            "mismatch_detail": [
                {"column": "amount", "metric": "sum", "source": 1700000.0, "target": 2599999.0},
                {"column": "amount", "metric": "max", "source": 196000.0, "target": 999999.0},
            ],
        }])
        rt = models.RunTable(source_table="t", target_table="t")
        _persist_aggregate_findings(rt, self._FakeResult(df))

        period_findings = [f for f in rt.aggregate_findings if f.category == "period_monthly"]
        assert len(period_findings) == 2  # NOT a 3rd "row count" finding -- Δ=0, nothing to report there
        assert all(f.period == "2025-05" for f in period_findings)
        by_metric = {f.metric: f for f in period_findings}
        assert by_metric["sum"].column_name == "amount"
        assert by_metric["sum"].source_value == "1700000.0"
        assert by_metric["sum"].target_value == "2599999.0"
        assert by_metric["sum"].difference is None  # not a row-count finding
        assert by_metric["max"].column_name == "amount"

    def test_row_count_mismatch_period_persists_row_count_plus_metric_findings(self):
        df = pd.DataFrame([{
            "period": "2025-11", "source_row": 16, "target_row": 15, "difference": 1,
            "row_match": False, "match": False,
            "mismatch_detail": [
                {"column": "amount", "metric": "sum", "source": 1600000.0, "target": 1590000.0},
            ],
        }])
        rt = models.RunTable(source_table="t", target_table="t")
        _persist_aggregate_findings(rt, self._FakeResult(df))

        period_findings = [f for f in rt.aggregate_findings if f.category == "period_monthly"]
        assert len(period_findings) == 2  # 1 row-count finding + 1 metric finding
        row_count_finding = next(f for f in period_findings if f.column_name is None and f.metric is None)
        assert row_count_finding.source_value == "16" and row_count_finding.target_value == "15"
        assert row_count_finding.difference == 1.0
        metric_finding = next(f for f in period_findings if f.metric == "sum")
        assert metric_finding.column_name == "amount"

    def test_matching_period_persists_nothing(self):
        df = pd.DataFrame([{
            "period": "2025-01", "source_row": 10, "target_row": 10, "difference": 0,
            "row_match": True, "match": True, "mismatch_detail": [],
        }])
        rt = models.RunTable(source_table="t", target_table="t")
        _persist_aggregate_findings(rt, self._FakeResult(df))
        assert [f for f in rt.aggregate_findings if f.category == "period_monthly"] == []
