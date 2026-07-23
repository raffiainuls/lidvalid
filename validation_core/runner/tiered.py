"""Per-table orchestration: aggregate first (cheap), row-level only for
tables that FAIL aggregate (expensive, precise) — the "Tiered Validation"
concept from docs/validation-platform/02-prd.md §6, the reason these two
tools are being unified instead of just run side by side.
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field

from ..aggregate.validator import AggregateValidator, AggregateResult
from ..connectors.base import Connector
from ..models import RunSettings, TableSpec
from ..rowlevel.runner import RowLevelValidator, RowLevelResult
from ..events import OnEvent, ProgressEvent, noop_on_event
from .retry import run_with_retry


@dataclass
class TableRunResult:
    table: TableSpec
    status: str  # PASS | FAIL | ERROR
    tier_reached: int  # 1 = aggregate only, 2 = row-level also ran
    mode: str
    aggregate: AggregateResult | None = None
    aggregate_summary: dict | None = None
    rowlevel: RowLevelResult | None = None
    error: str | None = None
    # Full traceback for ERROR results. Kept separate from `error` (the
    # short one-line reason shown inline in tables/headers) so the web
    # layer can show the short form everywhere and the full trace only in
    # the drilldown Log tab.
    error_trace: str | None = None
    attempts: int = 1
    duration_s: float = 0.0
    queries: dict[str, str] = field(default_factory=dict)


def run_table(
    source: Connector,
    target: Connector,
    source_db: str,
    target_db: str,
    table: TableSpec,
    run_mode: str,
    settings: RunSettings | None = None,
    on_event: OnEvent = noop_on_event,
) -> TableRunResult:
    """Validate one source/target table pair according to `run_mode`
    (aggregate | rowlevel_missing | rowlevel_full | tiered), or `table.mode_override`
    if set (per-table override, e.g. a >5M-row table forced to rowlevel_missing).
    """
    settings = settings or RunSettings()
    effective_mode = table.mode_override or run_mode
    t0 = time.monotonic()

    def _on_retry(attempt: int, exc: Exception, wait: float) -> None:
        on_event(ProgressEvent(
            kind="retry",
            message=f"{table.source_table}: attempt {attempt}/{settings.retry_max} failed "
                     f"(transient), retry in {wait:.0f}s: {str(exc)[:160]}",
            data={"attempt": attempt, "wait_s": wait, "error": str(exc)},
        ))

    # Whatever _do() completes BEFORE an exception is stashed here so the
    # error path can still return it. Without this, a Tier 2 failure threw
    # away the fully-finished Tier 1 results (row counts, findings, queries)
    # -- the drilldown of an errored table showed "rows: — / —" and an empty
    # Temuan Agregat tab even though the aggregate phase had succeeded (real
    # user report on datamart_logger_monitoring's stream-failure error).
    partial: dict = {"aggregate": None, "aggregate_summary": None, "tier": 1, "queries": {}}

    def _do() -> TableRunResult:
        source_ref = source.dialect.table_ref(source_db, table.source_table, final=True)
        target_ref = target.dialect.table_ref(target_db, table.target_table, final=True)
        queries: dict[str, str] = {}
        partial["queries"] = queries  # same object -- keeps filling even if we blow up later

        def on_query(label: str, sql: str) -> None:
            queries[label] = sql

        aggregate_result: AggregateResult | None = None
        aggregate_summary: dict | None = None
        rowlevel_result: RowLevelResult | None = None
        tier = 1
        status = "ERROR"

        if effective_mode in ("aggregate", "tiered"):
            on_event(ProgressEvent(kind="phase", message=f"{table.source_table}: Tier 1 aggregate"))
            validator = AggregateValidator(
                source, target, source_db, table.source_table, target_db, table.target_table,
                date_column=table.date_column, start_date=table.start_date, end_date=table.end_date,
                settings=settings, on_query=on_query,
            )
            aggregate_result = validator.run()
            aggregate_summary = aggregate_result.summarize()
            status = aggregate_summary["status"]
            partial["aggregate"] = aggregate_result
            partial["aggregate_summary"] = aggregate_summary

            if effective_mode == "aggregate" or status == "PASS":
                return TableRunResult(
                    table=table, status=status, tier_reached=1, mode=effective_mode,
                    aggregate=aggregate_result, aggregate_summary=aggregate_summary,
                    queries=queries,
                )
            tier = 2
            partial["tier"] = 2
            rl_mode = "missing" if aggregate_summary["source_rows"] > settings.full_mode_row_threshold else "full"
        elif effective_mode == "rowlevel_missing":
            rl_mode = "missing"
        elif effective_mode == "rowlevel_full":
            rl_mode = "full"
        else:
            raise ValueError(f"Unknown validation mode: {effective_mode}")

        on_event(ProgressEvent(kind="phase", message=f"{table.source_table}: Tier 2 row-level ({rl_mode})"))
        rl_validator = RowLevelValidator(
            source, target, source_ref, target_ref, table, rl_mode, settings, on_event,
            source_db=source_db, target_db=target_db,
        )
        rowlevel_result = rl_validator.run()
        queries.update(rowlevel_result.queries)

        rl_ok = (
            rowlevel_result.missing_in_source_count == 0
            and rowlevel_result.missing_in_target_count == 0
            and rowlevel_result.differing_values_count == 0
        )
        if effective_mode != "tiered":
            # Pure row-level mode: row-level IS the verdict.
            status = "PASS" if rl_ok else "FAIL"
        elif rl_ok:
            # Tiered mode reaches here only when Tier 1 (aggregate stats)
            # already said FAIL. Aggregate checks can have false positives
            # that don't reflect any REAL difference in the data (e.g. a
            # NULL-handling quirk producing a bogus stat/uniqueness
            # mismatch -- see README's NULL->1970 ClickHouse incident).
            # When Tier 2 comes back completely clean (0 missing keys both
            # ways, 0 differing values), it overrides Tier 1's FAIL rather
            # than leaving a false alarm standing.
            #
            # This deliberately applies to BOTH Tier 2 modes -- an explicit
            # product decision by the user (twice), overriding an earlier
            # more conservative version that only trusted "full" mode. Note
            # what that means for "missing" mode (large tables, over
            # full_mode_row_threshold): it only checks key existence, never
            # compares values, so its differing_values_count == 0 is
            # trivially true rather than a real value-level verification --
            # a large table whose Tier 1 FAIL was caused by genuine VALUE
            # drift (not missing rows) will now read PASS. Accepted
            # tradeoff: Tier 1 stat checks have produced enough false
            # positives in practice that a clean key-existence check is
            # treated as sufficient evidence for these tables.
            status = "PASS"
        # else: Tier 2 found real missing/differing rows -- FAIL stands.

        return TableRunResult(
            table=table, status=status, tier_reached=tier, mode=effective_mode,
            aggregate=aggregate_result, aggregate_summary=aggregate_summary,
            rowlevel=rowlevel_result, queries=queries,
        )

    try:
        result, attempts = run_with_retry(_do, settings, on_retry=_on_retry)
        result.attempts = attempts
        result.duration_s = round(time.monotonic() - t0, 2)
        return result
    except Exception as exc:  # noqa: BLE001 - table-level failure, batch continues
        return TableRunResult(
            table=table, status="ERROR", tier_reached=partial["tier"], mode=effective_mode,
            # Attach whatever finished before the failure (see `partial`
            # above): a Tier-2 crash keeps its completed Tier 1 aggregate
            # results and every query that was logged along the way.
            aggregate=partial["aggregate"], aggregate_summary=partial["aggregate_summary"],
            error=str(exc), error_trace=traceback.format_exc(),
            attempts=settings.retry_max,
            duration_s=round(time.monotonic() - t0, 2),
            queries=dict(partial["queries"]),
        )
