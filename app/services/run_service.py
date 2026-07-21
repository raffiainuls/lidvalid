"""Run orchestration: creates Run/RunTable rows, executes validation_core in a
background thread pool, streams progress to the in-process event bus, and
persists final per-table results + findings.

Dev-mode simplification: this replaces the Celery+Redis worker described in
docs/validation-platform/03-arsitektur.md with a plain `threading.Thread` +
`ThreadPoolExecutor` inside the FastAPI process (see README). The public
shape — one row per table, retry, resume-by-skipping-finished-tables, live
progress — matches the target architecture; only the execution substrate
differs.
"""
from __future__ import annotations

import threading
import traceback
from collections import deque
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy.orm import Session

from validation_core.categories import get_category, values_match, META_COLUMNS
from validation_core.connectors import create_connector
from validation_core.models import RunSettings, TableSpec
from validation_core.runner import run_table as vc_run_table, ProgressEvent

from .. import models
from ..database import SessionLocal
from . import connections_service
from .events_bus import bus


def _utcnow():
    return datetime.now(timezone.utc)


def reap_orphaned_runs(db: Session) -> int:
    """Called once at process startup (see main.py's lifespan). Any Run still
    'running'/'queued' at this point belonged to a PREVIOUS process (crash,
    manual restart, deploy) — the background thread that owned it died with
    that process and will never touch it again, since `_execute_run` only
    ever runs inside the process that called `start_run_async`. Left alone,
    such a run stays "running" forever in the UI, looking alive when it
    isn't (a real incident: a genuinely long-running validation ended up
    silently indistinguishable from a truly stuck/dead one, both showing
    per-table progress=0.0 until completion — see TECHNICAL.md/README for
    the postmortem). Marking it failed here makes that state visible and
    obviously actionable (re-run) instead of quietly wrong forever.
    Returns how many runs were reaped, for the startup log line."""
    orphaned = db.query(models.Run).filter(models.Run.status.in_(["running", "queued"])).all()
    for run in orphaned:
        for rt in run.tables:
            if rt.status in ("running", "pending"):
                rt.status = "error"
                rt.error = (
                    "Run diinterupsi: proses server sebelumnya berhenti (restart/crash) "
                    "sebelum tabel ini selesai — bukan kegagalan validasi. Jalankan ulang."
                )
                rt.finished_at = _utcnow()
        run.status = "failed"
        run.error = (
            "Run diinterupsi: server berhenti/di-restart sebelum run ini selesai. "
            "Progress yang sudah tercatat tetap akurat untuk tabel yang sempat selesai."
        )
        run.finished_at = _utcnow()
        # NOTE: deliberately NOT reusing _summarize_run() here -- it calls
        # db.refresh(rt) per table, which would discard the in-memory status
        # changes just made above (still uncommitted) and reload the STALE
        # pre-reap status from the DB, undercounting "error" and overcounting
        # "running"/"pending" in the summary.
        counts: dict[str, int] = {}
        for rt in run.tables:
            counts[rt.status] = counts.get(rt.status, 0) + 1
        run.summary = {
            "tables_total": len(run.tables),
            "pass": counts.get("pass", 0),
            "fail": counts.get("fail", 0),
            "error": counts.get("error", 0),
        }
    if orphaned:
        db.commit()
    return len(orphaned)


def build_run_settings(config: models.ValidationConfig) -> RunSettings:
    base = RunSettings()
    overrides = config.settings or {}
    for field_name in (
        "meta_columns", "id_chunk_size", "full_mode_row_threshold", "stat_ref_date",
        "table_concurrency", "skip_period_breakdown", "fuzzy_threshold", "retry_max",
        "retry_backoff_seconds", "heartbeat_seconds", "rowlevel_sample_cap",
        "numeric_rel_tolerance", "numeric_abs_tolerance", "rowlevel_target_chunk_rows",
    ):
        if field_name in overrides:
            value = overrides[field_name]
            if field_name == "meta_columns":
                value = frozenset(value) | META_COLUMNS
            setattr(base, field_name, value)
    return base


def to_table_spec(ct: models.ConfigTable) -> TableSpec:
    return TableSpec(
        source_table=ct.source_table,
        target_table=ct.target_table,
        key_columns=list(ct.key_columns or ["id"]),
        chunk_column=ct.chunk_column,
        date_column=ct.date_column,
        exclude_columns=list(ct.exclude_columns or []),
        mode_override=ct.mode_override,
        start_date=ct.start_date,
        end_date=ct.end_date,
        enabled=ct.enabled,
        note=ct.note or "",
    )


def create_run(
    db: Session,
    config: models.ValidationConfig,
    mode: str | None = None,
    table_filter: list[str] | None = None,
    trigger_type: str = "manual",
) -> models.Run:
    run = models.Run(
        config_id=config.id,
        trigger_type=trigger_type,
        mode=mode or config.default_mode,
        table_filter=table_filter,
        status="queued",
    )
    db.add(run)
    db.flush()

    tables = [t for t in config.tables if t.enabled]
    if table_filter:
        wanted = set(table_filter)
        tables = [t for t in tables if t.source_table in wanted]

    for ct in tables:
        db.add(models.RunTable(
            run_id=run.id, config_table_id=ct.id,
            source_table=ct.source_table, target_table=ct.target_table,
            status="pending",
        ))
    db.commit()
    db.refresh(run)
    return run


def start_run_async(run_id: int) -> None:
    thread = threading.Thread(target=_execute_run, args=(run_id,), daemon=True)
    thread.start()


RESUME_SCOPES = {
    # scope -> predicate over a RunTable's status. Evolved twice on user
    # request: originally resume only picked up never-finished leftovers,
    # then "everything non-pass", now an explicit per-click choice.
    "all": lambda status: True,
    "non_pass": lambda status: status != "pass",
    "fail": lambda status: status == "fail",
    "error": lambda status: status == "error",
}


def resume_run(db: Session, finished_run: models.Run, scope: str = "non_pass") -> models.Run | None:
    """Create a new run re-validating tables from `finished_run` selected by
    `scope` (see RESUME_SCOPES): "all" every table, "fail" only FAIL,
    "error" only ERROR, "non_pass" everything that isn't PASS (default, and
    what the older parameterless behavior was).

    Returns None (creating nothing) when the scope matches no tables --
    e.g. "Hanya ERROR" clicked on a run with zero error tables. The caller
    surfaces that as a flash message. Deliberately NOT falling back to
    "all" here: before scopes existed, an empty selection fell through to
    `table_filter=None` (= every table), which would make a misclick
    silently launch a full 99-table run."""
    predicate = RESUME_SCOPES.get(scope) or RESUME_SCOPES["non_pass"]
    remaining = [rt.source_table for rt in finished_run.tables if predicate(rt.status)]
    if not remaining:
        return None
    new_run = create_run(db, finished_run.config, mode=finished_run.mode,
                          table_filter=remaining, trigger_type="revalidate")
    start_run_async(new_run.id)
    return new_run


def _execute_run(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = db.get(models.Run, run_id)
        config = run.config
        source_conn = config.source_connection
        target_conn = config.target_connection
        source_params = connections_service.to_connection_params(source_conn)
        target_params = connections_service.to_connection_params(target_conn)
        settings = build_run_settings(config)
        # Captured as plain strings *before* any db.commit() below: SQLAlchemy
        # expires ORM attributes on commit (expire_on_commit=True), and a
        # later re-read of source_conn.database/run.mode from a worker thread
        # would lazy-reload via the SAME Session concurrently from multiple
        # threads at once -- Session/Connection aren't thread-safe, and that
        # race corrupted row data (IndexError: tuple index out of range deep
        # in SQLAlchemy's row processing). Worker threads must only ever see
        # plain values, never ORM objects bound to this Session.
        source_db_name = source_conn.database
        target_db_name = target_conn.database
        run_mode = run.mode

        run.status = "running"
        run.started_at = _utcnow()
        run_tables = list(run.tables)
        for rt in run_tables:
            rt.status = "running"
            rt.started_at = _utcnow()
        db.commit()

        # Per-table bounded event trail, persisted to RunTable.event_log at
        # completion. The event bus alone is in-memory only -- gone after
        # the run finishes (or the server restarts), which used to leave an
        # errored table with NO trace of what it was doing when it died.
        # deque(maxlen=...) keeps the LAST N events (the interesting end of
        # the story) while bounding memory for chunked tables that emit
        # hundreds of checkpoints. Thread-safety: each trail is appended
        # only by its own table's worker thread, and read only after that
        # worker's future completes (happens-before via future.result()).
        event_trails: dict[int, deque] = {rt.id: deque(maxlen=200) for rt in run_tables}

        def make_on_event(run_table_id: int):
            trail = event_trails[run_table_id]

            def on_event(event: ProgressEvent) -> None:
                trail.append({
                    "ts": _utcnow().strftime("%H:%M:%S"),
                    "kind": event.kind, "message": event.message,
                })
                bus.publish(run_id, {
                    "run_table_id": run_table_id, "kind": event.kind,
                    "message": event.message, "data": event.data,
                })
            return on_event

        # Resolve every TableSpec up front, on the main thread's session --
        # worker threads never touch SQLAlchemy, only validation_core + raw
        # DB connectors, so no Session is ever shared across threads.
        table_specs: dict[int, TableSpec] = {}
        for rt in run_tables:
            if rt.config_table_id:
                ct = db.get(models.ConfigTable, rt.config_table_id)
                table_specs[rt.id] = to_table_spec(ct)
            else:
                table_specs[rt.id] = TableSpec(source_table=rt.source_table, target_table=rt.target_table)

        def worker(rt_id: int, table_spec: TableSpec):
            src = create_connector(source_params)
            tgt = create_connector(target_params)
            try:
                result = vc_run_table(
                    src, tgt, source_db_name, target_db_name,
                    table_spec, run_mode, settings, make_on_event(rt_id),
                )
                return rt_id, result, None, None
            except Exception as exc:  # noqa: BLE001 - surfaced as an ERROR row, run continues
                # vc_run_table has its own catch-all, so this mostly guards
                # connector-construction failures around it.
                return rt_id, None, str(exc), traceback.format_exc()
            finally:
                src.close()
                tgt.close()

        with ThreadPoolExecutor(max_workers=max(1, settings.table_concurrency)) as ex:
            futures = {}
            for rt in run_tables:
                if bus.is_cancel_requested(run_id):
                    break
                futures[ex.submit(worker, rt.id, table_specs[rt.id])] = rt

            # `as_completed` alone blocks until every submitted future is
            # done, so cancel used to have no real effect once table_count
            # tables were already in the pool: only the currently-in-flight
            # ones (bounded by table_concurrency) can actually be stopped
            # early -- ThreadPoolExecutor can't kill a thread mid-query --
            # but everything still QUEUED behind them can be, via
            # future.cancel() (only succeeds for work that hasn't started).
            # Calling it once, as soon as cancel is first observed, turns a
            # "wait for all N tables" run into "wait for whichever handful
            # were already running", which is the whole point of a cancel
            # button. See README/TECHNICAL for the incident this came from.
            cancel_seen = False
            for future in as_completed(futures):
                if not cancel_seen and bus.is_cancel_requested(run_id):
                    cancel_seen = True
                    for f in futures:
                        f.cancel()

                rt = futures[future]
                try:
                    rt_id, result, err, err_trace = future.result()
                except CancelledError:
                    rt.status = "cancelled"
                    rt.finished_at = _utcnow()
                    rt.event_log = list(event_trails.get(rt.id, []))
                    db.commit()
                    bus.publish(run_id, {"run_table_id": rt.id, "kind": "table_done",
                                          "message": f"{rt.source_table}: CANCELLED", "data": {}})
                    continue

                rt = db.get(models.RunTable, rt_id)
                trail = list(event_trails.get(rt_id, []))
                if err is not None:
                    rt.status = "error"
                    rt.error = err
                    rt.finished_at = _utcnow()
                    if err_trace:
                        trail.append({"ts": _utcnow().strftime("%H:%M:%S"),
                                      "kind": "traceback", "message": err_trace})
                else:
                    _persist_table_result(rt, result, settings)
                    if result.error_trace:
                        trail.append({"ts": _utcnow().strftime("%H:%M:%S"),
                                      "kind": "traceback", "message": result.error_trace})
                rt.event_log = trail
                db.commit()
                bus.publish(run_id, {"run_table_id": rt_id, "kind": "table_done",
                                      "message": f"{rt.source_table}: {rt.status.upper()}", "data": {}})

        if bus.is_cancel_requested(run_id):
            # Catches tables that never even got submitted to the executor
            # (the submit loop's own `if is_cancel_requested: break`, above --
            # relevant when cancel is observed before ANY table starts, e.g.
            # a very fast cancel or a saturated thread pool) as well as any
            # edge case the as_completed loop's per-future handling missed.
            for rt in run_tables:
                db.refresh(rt)
                if rt.status == "running":
                    rt.status = "cancelled"
            run.status = "cancelled"
        else:
            run.status = "completed"

        run.finished_at = _utcnow()
        run.summary = _summarize_run(run_tables, db)
        db.commit()

    except Exception as exc:  # noqa: BLE001 - run-level failure, never crash the thread silently
        run = db.get(models.Run, run_id)
        if run:
            run.status = "failed"
            run.error = str(exc)
            run.finished_at = _utcnow()
            db.commit()
    finally:
        bus.mark_done(run_id)
        db.close()


def _persist_table_result(rt: models.RunTable, result, settings: RunSettings) -> None:
    rt.status = result.status.lower()
    rt.tier_reached = result.tier_reached
    rt.mode = result.mode
    rt.attempt = result.attempts
    rt.finished_at = _utcnow()
    rt.queries = result.queries
    # Real bug fixed here: this line was missing. tiered.run_table catches
    # every table-level exception itself and returns a NORMAL result object
    # with status="ERROR" and the reason in .error -- so the `err is not
    # None` branch in _execute_run (which sets rt.error) almost never fires,
    # and errored tables landed in the DB with status "error" but a NULL
    # error message: no clue anywhere WHY they failed.
    rt.error = result.error

    if result.aggregate_summary:
        s = result.aggregate_summary
        rt.source_rows = s["source_rows"]
        rt.target_rows = s["target_rows"]
        rt.row_diff = s["row_diff"]
        rt.source_cols = s["source_cols"]
        rt.target_cols = s["target_cols"]
        rt.extra_source_columns = s["extra_source_columns"]
        rt.extra_target_columns = s["extra_target_columns"]
        rt.agg_metrics = {
            "col_completeness_mismatch": s["col_completeness_mismatch"],
            "col_uniqueness_mismatch": s["col_uniqueness_mismatch"],
            "stat_mismatch": s["stat_mismatch"],
            "monthly_mismatch": s["monthly_mismatch"],
            "yearly_mismatch": s["yearly_mismatch"],
        }
        rt.investigate_query = result.aggregate.investigate_query
        _persist_aggregate_findings(rt, result)
        rt.column_type_details = _build_column_type_details(result.aggregate)

    if result.rowlevel:
        rl = result.rowlevel
        rt.chunks_done = rl.total_chunks
        rt.chunks_total = rl.total_chunks
        rt.rl_metrics = {
            "missing_in_source": rl.missing_in_source_count,
            "missing_in_target": rl.missing_in_target_count,
            "differing_values": rl.differing_values_count,
            "value_columns": rl.value_columns,
            "key_columns": rl.key_columns,
            "truncated": rl.truncated,
        }
        _persist_rowlevel_findings(rt, rl, settings)

    rt.progress = 1.0


def _build_column_type_details(agg) -> list[dict]:
    """One row per column (shared + source-only + target-only) for the
    "Tipe Kolom" drilldown tab: the raw type string from each side plus
    whether their CATEGORY (get_category()) agrees. This is distinct from
    the "stat" FindingAggregate rows (category="stat"), which only ever
    compare METRIC VALUES (min/max/sum/...) assuming both sides' categories
    ALREADY match -- a genuine schema-level type drift between source and
    target was previously invisible everywhere in the UI; it only ever
    surfaced internally as a silent skip from stat comparison (see
    AggregateValidator._shared_stat_cols's `if src_cat != tgt_cat: continue`).
    """
    cd = agg.column_details
    if cd.empty or "column_name" not in cd.columns:
        return []
    rows = []
    for _, row in cd.iterrows():
        src_type = row.get("source_column_type")
        tgt_type = row.get("target_column_type")
        src_type = None if pd.isna(src_type) else str(src_type)
        tgt_type = None if pd.isna(tgt_type) else str(tgt_type)
        category_match = None
        if src_type and tgt_type:
            category_match = get_category(src_type) == get_category(tgt_type)
        rows.append({
            "column": row["column_name"],
            "source_type": src_type,
            "target_type": tgt_type,
            "category_match": category_match,
        })
    return rows


def _persist_aggregate_findings(rt: models.RunTable, result) -> None:
    agg = result.aggregate
    s = result.aggregate_summary

    if not s["row_match"]:
        rt.aggregate_findings.append(models.FindingAggregate(
            category="row_count", source_value=str(s["source_rows"]),
            target_value=str(s["target_rows"]), difference=float(s["row_diff"]),
        ))

    cd = agg.column_details
    if "validate_completeness" in cd.columns:
        for _, row in cd[cd["validate_completeness"] == False].iterrows():  # noqa: E712
            if row["column_name"] in META_COLUMNS:
                continue
            rt.aggregate_findings.append(models.FindingAggregate(
                category="completeness", column_name=row["column_name"],
                source_value=str(row.get("source_completeness")), target_value=str(row.get("target_completeness")),
            ))
    if "validate_uniqueness" in cd.columns:
        for _, row in cd[cd["validate_uniqueness"] == False].iterrows():  # noqa: E712
            if row["column_name"] in META_COLUMNS:
                continue
            rt.aggregate_findings.append(models.FindingAggregate(
                category="uniqueness", column_name=row["column_name"],
                source_value=str(row.get("source_uniqueness")), target_value=str(row.get("target_uniqueness")),
            ))

    src_df, tgt_df = agg.src_type_details, agg.tgt_type_details
    if not src_df.empty and not tgt_df.empty:
        metric_cols = [c for c in src_df.columns if c not in ("column_name", "column_type", "category")]
        src_idx, tgt_idx = src_df.set_index("column_name"), tgt_df.set_index("column_name")
        shared = [c for c in src_idx.index.intersection(tgt_idx.index) if c not in META_COLUMNS]
        for col in shared:
            for m in metric_cols:
                if m not in src_idx.columns or m not in tgt_idx.columns:
                    continue
                sv, tv = src_idx.loc[col, m], tgt_idx.loc[col, m]
                if not values_match(sv, tv):
                    rt.aggregate_findings.append(models.FindingAggregate(
                        category="stat", column_name=col, metric=m,
                        source_value=str(sv), target_value=str(tv),
                    ))

    for granularity, df in (("period_monthly", agg.monthly_breakdown), ("period_yearly", agg.yearly_breakdown)):
        if df is None or df.empty or "match" not in df.columns:
            continue
        for _, row in df[df["match"] == False].iterrows():  # noqa: E712
            # A period can be "mismatch" for two INDEPENDENT reasons, and
            # both get their own finding row rather than being collapsed
            # into one vague "this period doesn't match": (1) row count
            # itself differs, and/or (2) row counts are IDENTICAL but some
            # shared column's per-period stat (sum/min/max/datediff)
            # differs. Without (2) as its own row, a period with Δ=0 still
            # showing up as mismatched looked like an unexplained false
            # alarm (real user confusion).
            if not row["row_match"]:
                rt.aggregate_findings.append(models.FindingAggregate(
                    category=granularity, period=str(row["period"]),
                    source_value=str(row["source_row"]), target_value=str(row["target_row"]),
                    difference=float(row["difference"]),
                ))
            for detail in row.get("mismatch_detail") or []:
                rt.aggregate_findings.append(models.FindingAggregate(
                    category=granularity, period=str(row["period"]),
                    column_name=detail["column"], metric=detail["metric"],
                    source_value=str(detail["source"]), target_value=str(detail["target"]),
                ))


def _persist_rowlevel_findings(rt: models.RunTable, rl, settings: RunSettings) -> None:
    cap = settings.rowlevel_sample_cap
    for key in rl.missing_in_source[:cap]:
        rt.rowlevel_findings.append(models.FindingRowLevel(finding_type="missing_in_source", row_key=str(key)))
    for key in rl.missing_in_target[:cap]:
        rt.rowlevel_findings.append(models.FindingRowLevel(finding_type="missing_in_target", row_key=str(key)))
    for d in rl.differing_values[:cap]:
        rt.rowlevel_findings.append(models.FindingRowLevel(
            finding_type="value_diff", row_key=str(d["key"]), column_name=d["column"],
            source_value=str(d["source_value"]), target_value=str(d["target_value"]),
        ))


def _summarize_run(run_tables: list[models.RunTable], db: Session) -> dict:
    """`run_tables` are the SAME ORM objects `_execute_run`'s caller just
    finished updating in-memory (via the as_completed loop or the
    cancel-cleanup fallback), in the SAME session -- they're already
    current, a `db.refresh(rt)` here would be redundant at best. It used to
    be actively harmful: called BEFORE the caller's own commit, refresh
    would silently discard whatever in-memory status flip hadn't been
    persisted yet and reload the STALE pre-flip value from the DB instead --
    this is exactly the `reap_orphaned_runs` pitfall (see its own docstring),
    just missed here. Concretely: every table cancelled via the "never even
    submitted" cleanup path came back as "running" in the persisted summary
    (and, before an added early commit turned out to be the wrong fix as
    well, in a race window where `run.status` could already read
    "cancelled" while `run.summary` was still stale) until this refresh was
    removed entirely.
    """
    counts = {"pass": 0, "fail": 0, "error": 0, "cancelled": 0}
    for rt in run_tables:
        counts[rt.status] = counts.get(rt.status, 0) + 1
    return {
        "tables_total": len(run_tables),
        "pass": counts.get("pass", 0),
        "fail": counts.get("fail", 0),
        "error": counts.get("error", 0),
        "cancelled": counts.get("cancelled", 0),
    }
