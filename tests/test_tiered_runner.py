import validation_core.runner.tiered as tiered_module
from validation_core.models import RunSettings, TableSpec
from validation_core.runner import run_table


def _table(**overrides):
    base = dict(source_table="ws_orders", target_table="raw_ws_orders", key_columns=["id"], date_column="created_at")
    base.update(overrides)
    return TableSpec(**base)


class TestTieredMode:
    def test_pass_stops_at_tier_1(self, identical_pair):
        source, target = identical_pair
        table = TableSpec(source_table="t", target_table="t", key_columns=["id"], date_column="d")
        result = run_table(source, target, "", "", table, "tiered", RunSettings())
        assert result.status == "PASS"
        assert result.tier_reached == 1
        assert result.rowlevel is None  # row-level never ran -- the whole point of tiering

    def test_fail_escalates_to_tier_2_and_finds_the_exact_diffs(self, orders_pair):
        source, target = orders_pair
        result = run_table(source, target, "", "", _table(), "tiered", RunSettings(full_mode_row_threshold=5_000_000))
        assert result.status == "FAIL"
        assert result.tier_reached == 2
        assert result.rowlevel is not None
        assert result.rowlevel.missing_in_target_count == 2  # ids 10, 20
        assert result.rowlevel.differing_values_count == 1   # id 100 amount changed
        assert sorted(result.rowlevel.missing_in_target) == ["10", "20"]

    def test_large_table_threshold_forces_missing_only_mode(self, orders_pair):
        source, target = orders_pair
        # threshold of 1 forces every table into rowlevel_missing during tier 2
        result = run_table(source, target, "", "", _table(), "tiered", RunSettings(full_mode_row_threshold=1))
        assert result.rowlevel.mode == "missing"
        assert result.rowlevel.value_columns == []
        assert result.rowlevel.differing_values_count == 0  # missing mode never looks at values


class TestPureModes:
    def test_aggregate_mode_never_runs_rowlevel_even_on_fail(self, orders_pair):
        source, target = orders_pair
        result = run_table(source, target, "", "", _table(), "aggregate", RunSettings())
        assert result.status == "FAIL"
        assert result.tier_reached == 1
        assert result.rowlevel is None

    def test_rowlevel_full_mode_is_the_verdict_on_its_own(self, orders_pair):
        source, target = orders_pair
        result = run_table(source, target, "", "", _table(), "rowlevel_full", RunSettings())
        assert result.aggregate is None  # tier 1 never ran
        assert result.status == "FAIL"
        assert result.rowlevel.differing_values_count == 1

    def test_rowlevel_missing_mode_passes_when_only_a_value_differs(self, orders_pair):
        # missing-only mode doesn't look at values at all, so the id=100 diff
        # is invisible to it -- only the 2 actually-missing rows would fail it.
        source, target = orders_pair
        result = run_table(source, target, "", "", _table(), "rowlevel_missing", RunSettings())
        assert result.status == "FAIL"  # still fails, because of the 2 missing rows
        assert result.rowlevel.missing_in_target_count == 2
        assert result.rowlevel.differing_values_count == 0


class _FakeDialect:
    def table_ref(self, database, table, final=False):
        return table


class _FakeConnector:
    dialect = _FakeDialect()


class _FakeAggregateResult:
    def __init__(self, status):
        self._status = status

    def summarize(self):
        # source_rows just needs to be present (run_table() checks it
        # against full_mode_row_threshold to pick "missing" vs "full") --
        # the actual Tier 2 mode used in these tests comes from the faked
        # RowLevelValidator's return value below, not from this threshold
        # check, so the exact number here doesn't matter.
        return {"status": self._status, "source_rows": 10}


class _FakeRowLevelResult:
    def __init__(self, mode, missing_source=0, missing_target=0, differing=0):
        self.mode = mode
        self.missing_in_source_count = missing_source
        self.missing_in_target_count = missing_target
        self.differing_values_count = differing
        self.queries = {}


def _patch_validators_rl_raises(monkeypatch, agg_status="FAIL"):
    """Tier 1 succeeds, Tier 2 blows up (e.g. a stream failure mid-chunk)."""
    class _FakeAggValidator:
        def __init__(self, *a, **kw):
            pass

        def run(self):
            return _FakeAggregateResult(agg_status)

    class _ExplodingRLValidator:
        def __init__(self, *a, **kw):
            pass

        def run(self):
            raise ValueError("boom: chunk fetch died")

    monkeypatch.setattr(tiered_module, "AggregateValidator", _FakeAggValidator)
    monkeypatch.setattr(tiered_module, "RowLevelValidator", _ExplodingRLValidator)


def _patch_validators(monkeypatch, agg_status, rl_mode, rl_clean):
    """Fake both Tier 1 and Tier 2 validators so the PASS/FAIL DECISION
    logic in run_table() can be tested directly, without needing real data
    that happens to reproduce a Tier-1-false-positive/Tier-2-clean scenario
    (the actual production trigger -- a ClickHouse NULL-handling quirk, see
    README -- isn't reproducible against the SQLite fixtures used here)."""
    class _FakeAggValidator:
        def __init__(self, *a, **kw):
            pass

        def run(self):
            return _FakeAggregateResult(agg_status)

    class _FakeRLValidator:
        def __init__(self, *a, **kw):
            pass

        def run(self):
            n = 0 if rl_clean else 3
            return _FakeRowLevelResult(rl_mode, missing_source=n, differing=n)

    monkeypatch.setattr(tiered_module, "AggregateValidator", _FakeAggValidator)
    monkeypatch.setattr(tiered_module, "RowLevelValidator", _FakeRLValidator)


class TestTier2OverridesFalsePositiveTier1Fail:
    """Real user-reported scenario: Tier 1 (aggregate stats) flagged a
    mismatch that turned out to be a false positive (a ClickHouse
    NULL-handling quirk turning NULL deleted_at into '1970-01-01', see
    README's NULL->1970 incident) -- Tier 2 found the actual row data
    completely identical. That should PASS overall, not stay FAIL just
    because Tier 1 drilled down instead of getting overridden."""

    def test_full_mode_clean_rowlevel_overrides_tier1_fail(self, monkeypatch):
        _patch_validators(monkeypatch, agg_status="FAIL", rl_mode="full", rl_clean=True)
        table = TableSpec(source_table="t", target_table="t", key_columns=["id"], date_column="d")
        result = run_table(_FakeConnector(), _FakeConnector(), "", "", table, "tiered", RunSettings())
        assert result.status == "PASS"
        assert result.tier_reached == 2

    def test_full_mode_real_rowlevel_diff_keeps_fail(self, monkeypatch):
        _patch_validators(monkeypatch, agg_status="FAIL", rl_mode="full", rl_clean=False)
        table = TableSpec(source_table="t", target_table="t", key_columns=["id"], date_column="d")
        result = run_table(_FakeConnector(), _FakeConnector(), "", "", table, "tiered", RunSettings())
        assert result.status == "FAIL"

    def test_missing_mode_clean_also_overrides_tier1_fail(self, monkeypatch):
        # Explicit product decision (user, twice): a clean Tier 2 overrides
        # Tier 1's FAIL in BOTH modes, including "missing" (large tables,
        # e.g. dim_ws_entity_material_activities at 71M rows -- the real
        # table this surfaced on). Note "missing" mode never compares
        # values, so this accepts that a genuine value-drift-only FAIL on a
        # large table now reads PASS -- documented tradeoff, see tiered.py.
        _patch_validators(monkeypatch, agg_status="FAIL", rl_mode="missing", rl_clean=True)
        table = TableSpec(source_table="t", target_table="t", key_columns=["id"], date_column="d")
        result = run_table(_FakeConnector(), _FakeConnector(), "", "", table, "tiered", RunSettings())
        assert result.status == "PASS"

    def test_missing_mode_with_real_missing_rows_keeps_fail(self, monkeypatch):
        _patch_validators(monkeypatch, agg_status="FAIL", rl_mode="missing", rl_clean=False)
        table = TableSpec(source_table="t", target_table="t", key_columns=["id"], date_column="d")
        result = run_table(_FakeConnector(), _FakeConnector(), "", "", table, "tiered", RunSettings())
        assert result.status == "FAIL"


class TestTier2ErrorKeepsTier1Results:
    """Real user report: a table whose Tier 2 chunk fetch died showed
    `rows: — / —` and an empty Temuan Agregat tab, even though Tier 1 had
    ALREADY completed successfully -- the error branch built a bare ERROR
    result and threw the finished aggregate results away."""

    def test_tier2_crash_preserves_completed_aggregate(self, monkeypatch):
        _patch_validators_rl_raises(monkeypatch, agg_status="FAIL")
        table = TableSpec(source_table="t", target_table="t", key_columns=["id"], date_column="d")
        result = run_table(_FakeConnector(), _FakeConnector(), "", "", table, "tiered",
                           RunSettings(retry_max=1))
        assert result.status == "ERROR"
        assert "boom" in result.error
        assert result.error_trace and "Traceback" in result.error_trace
        # the whole point: Tier 1's finished work survives the Tier 2 crash
        assert result.aggregate_summary is not None
        assert result.aggregate_summary["status"] == "FAIL"
        assert result.aggregate is not None
        assert result.tier_reached == 2  # it DID reach tier 2 before dying

    def test_tier1_crash_still_returns_bare_error(self, monkeypatch):
        class _ExplodingAgg:
            def __init__(self, *a, **kw):
                pass

            def run(self):
                raise ValueError("boom in tier 1")

        monkeypatch.setattr(tiered_module, "AggregateValidator", _ExplodingAgg)
        table = TableSpec(source_table="t", target_table="t", key_columns=["id"], date_column="d")
        result = run_table(_FakeConnector(), _FakeConnector(), "", "", table, "tiered",
                           RunSettings(retry_max=1))
        assert result.status == "ERROR"
        assert result.aggregate_summary is None  # nothing had finished yet
        assert result.tier_reached == 1


class TestModeOverridePerTable:
    def test_table_mode_override_wins_over_run_mode(self, identical_pair):
        source, target = identical_pair
        table = TableSpec(source_table="t", target_table="t", key_columns=["id"], date_column="d", mode_override="aggregate")
        result = run_table(source, target, "", "", table, "tiered", RunSettings())
        assert result.mode == "aggregate"


class TestErrorHandling:
    def test_unknown_table_reports_error_status_not_a_crash(self, identical_pair):
        source, target = identical_pair
        table = TableSpec(source_table="does_not_exist", target_table="t", key_columns=["id"])
        result = run_table(source, target, "", "", table, "aggregate", RunSettings(retry_max=1))
        assert result.status == "ERROR"
        assert result.error is not None
