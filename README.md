# LidValid

A unified data validation web platform — merging two legacy tools:

- **`validation-data`** ([d:\data-pipeline-batch\validation-data](../../data-pipeline-batch/validation-data)) — aggregate/statistical validation (row count, completeness, uniqueness, column stats, period breakdown)
- **`validation_database`** ([d:\Project\validation_database](../validation_database)) — row-level validation (missing IDs both directions, per-row value diff via chunked-by-id)

This is the working implementation of the planning document package at
[`d:\data-pipeline-batch\docs\validation-platform\`](../../data-pipeline-batch/docs/validation-platform/)
(PRD, architecture, data model, API spec, wireframe, roadmap). Read that first for the full vision
context — this document describes **what actually runs** in this implementation.

> For deep technical explanation — architecture, data flow, and a walkthrough of every
> file/function/line of code — read **TECHNICAL.md** (kept locally, not part of the git repo/GitHub
> push). This document (README) focuses on "what & how to use"; TECHNICAL.md focuses on "how it
> works internally".

Core feature: **Tiered Validation** — run aggregate checks (cheap) for all tables, then
automatically run row-level checks (precise, more expensive) only for tables that FAIL. One run can
directly answer both "which tables differ" *and* "which exact rows/columns" without switching tools.

## Try it now (2 minutes)

```powershell
cd D:\Project\validahub   # local folder name hasn't been renamed even though the brand is "LidValid"
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# creates 2 sample SQLite databases + a config + 1 complete run, ready to view
.venv\Scripts\python.exe scripts\seed_demo.py

# build the React frontend once (creates frontend/dist -- app/main.py serves
# it and falls back to a clear error message if this step is skipped)
cd frontend && npm ci && npm run build && cd ..

# run the server
.venv\Scripts\uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000** → log in with `admin@lidvalid.local` / `admin123` (also printed to
the console on first run). Config **"Demo: Contoh Validasi"** already has 1 completed run —
`ws_materials` PASSES, `ws_orders` FAILS (5 rows deliberately dropped + 2 value diffs) with full
drilldown to the exact row & column that differ.

For active frontend development (hot reload instead of rebuilding on every change), run
`npm run dev` in `frontend/` instead of the build step above — Vite's dev server proxies `/api`
calls to the FastAPI backend (see `frontend/vite.config.ts`), so keep `uvicorn` running alongside it.

To validate **real** data (actual MySQL/ClickHouse): create a new Connection at `/connections` with
engine `mysql`/`clickhouse`, then create a Config as usual. The server needs the same network
access as Dagster (VPN to the ClickHouse K8s if needed) — see §10 in TECHNICAL.md for how this was
solved for the VPS deployment (SSH reverse tunnel).

To deploy to a server/VPS (Docker + Caddy + automatic HTTPS), see the **Deployment** section below.

## Running tests

```powershell
.venv\Scripts\python.exe -m pytest -v
```

147 tests, all against local SQLite (no real MySQL/ClickHouse needed) — covering cross-engine
edge cases ported from both legacy tools (see `tests/test_categories.py`,
`tests/test_rowlevel_comparator.py`, `tests/test_aggregate_validator.py`, `tests/test_tiered_runner.py`),
a regression test (`tests/test_app_run_service.py`) for a real threading bug found while building
this (see notes below), and `tests/test_rbac.py` for role gating + per-user data scoping (see the
"RBAC & Data Scoping" section below). All HTTP-level tests hit the JSON API (`app/routers/api.py`)
via `httpx`/`TestClient` — there's no browser/React test harness here; the frontend's own build
(`npm run build` under `frontend/`, type-checked via `tsc`) is the check for that half of the stack.

## Structure

```
lidvalid/
├── README.md             # this document — what & how to use
├── TECHNICAL.md           # architecture, data flow, per-file/function/line code walkthrough (local only, not in git)
├── validation_core/      # Engine — pure Python port, knows nothing about the web/DB metadata
│   ├── categories.py      # get_category, values_match, META_COLUMNS (fixes the YEAR→UInt16 bug)
│   ├── models.py           # TableSpec, RunSettings (plain dataclasses)
│   ├── events.py           # ProgressEvent
│   ├── connectors/         # Dialect (mysql/clickhouse/sqlite) + Connector — one implementation, 5 reports
│   ├── aggregate/          # AggregateValidator — port of db_validator.py (Report 1-5)
│   ├── rowlevel/           # compare_chunk_multi + RowLevelValidator — port of chunked-by-id
│   ├── runner/             # run_table() — Tiered Validation (aggregate → rowlevel for FAILs) + retry
│   └── excel_export.py     # exports .xlsx compatible with the old format (transition feature, not the main output)
├── app/                  # FastAPI backend -- JSON API only, serves the built React SPA as static files
│   ├── main.py             # entry point, admin bootstrap (env-driven in production), SPA static/fallback routes
│   ├── database.py         # SQLAlchemy engine/session (SQLite by default, Postgres via DATABASE_URL)
│   ├── models.py           # ORM (13 tables + owner_id per-user scoping)
│   ├── security.py         # Fernet encryption + password hashing (env var required in production)
│   ├── auth.py             # session auth + CSRF (require_login_api/require_role_api) + data scoping (scope_query/check_owner)
│   ├── services/           # connections/discovery/run/export/dashboard services — bridge ORM ↔ validation_core
│   └── routers/            # api.py — the entire HTTP surface, every route RBAC-gated
├── frontend/             # React SPA (Vite + TypeScript + Tailwind + shadcn/ui + Radix)
│   ├── src/pages/           # one file per route (dashboard, connections, configs, run detail/drilldown, ...)
│   ├── src/components/      # shadcn/ui primitives (src/components/ui) + app-specific components
│   ├── src/hooks/           # TanStack Query hooks per resource (use-configs.ts, use-runs.ts, ...)
│   ├── src/lib/             # api.ts (fetch wrapper + CSRF), types.ts (API response shapes)
│   └── dist/                # `npm run build` output — served by app/main.py, not committed to git
├── tests/                # pytest — 147 tests, all run locally against SQLite, no real DB needed
│   └── test_rbac.py        # role gating + per-user data scoping
├── scripts/
│   ├── seed_demo.py         # one-click end-to-end demo generator
│   ├── create_user.py       # create/update accounts (no user-management UI yet)
│   └── migrate_to_postgres.py  # one-time SQLite → Postgres data migration
├── Dockerfile / .dockerignore   # multi-stage: Node builds frontend/dist, then the Python image
├── docker-compose.yml     # app + Postgres + Caddy (auto-HTTPS)
├── Caddyfile
└── .env.example           # production env var template
```

## What actually works

- **Tiered validation** end-to-end: 5-report aggregate, automatic escalation to row-level
  chunked-by-id for FAILing tables, results merged into a single run.
- **3 engines**: MySQL, ClickHouse (production parity with both legacy tools), plus SQLite (new — for
  local demo/testing without a VPN).
- **All edge-case fixes from both legacy tools are preserved** (see
  `docs/validation-platform/01-analisa-existing.md` §2.3 in the source project): pre-1970 date
  flooring, `BINARY LOWER(TRIM())` for MySQL vs ClickHouse collation, `_ceil_stat` for AVG precision,
  `toString()` for sentinel dates, `.000` normalization, reserved-keyword backticking, `FINAL` for
  ReplacingMergeTree, MySQL+SQLAlchemy-specific `%%` escaping (see comments in
  `validation_core/connectors/mysql.py`).
- **Two-way date clamping (floor 1970 + ClickHouse max ceiling) on ALL date-related validation**:
  every date/timestamp column being compared (Report 2 uniqueness, Report 3 min/max/datediff,
  Report 4/5 period breakdown, investigate query) is floored to `1970-01-01` AND capped to the actual
  maximum value ClickHouse can store for that column type (`Date` → `2149-06-06`, `Date32` →
  `2299-12-31`, `DateTime` → `2106-02-07 06:28:15`, `DateTime64` → `2299-12-31`) — on BOTH sides of
  the comparison, not just whichever side happens to be MySQL, so out-of-range values (which
  ClickHouse silently clamps on ingestion anyway) don't show up as a false mismatch against the
  other side that still has the raw value. See `validation_core/connectors/clickhouse.py::clickhouse_date_max()`.
- **1 legacy bug fixed**: MySQL `YEAR` is now mapped to the numeric category (previously fell back to
  string, causing a `length(UInt16)` crash in ClickHouse — `validation-data/CLAUDE.md` known issue #1).
- **Config builder** via the web: encrypted connections (Fernet), table mapping with a row editor
  (add/remove without reload), auto-suggest mapping by prefix. Importing legacy YAML configs isn't
  implemented yet (see the "Deviations from the target architecture" table below).
- **Auto-suggest key columns from the DDL (including composite keys)**: when "Auto-suggest from
  connection" is used, `key_columns` is automatically filled from the table's PRIMARY KEY (MySQL, via
  `INFORMATION_SCHEMA.KEY_COLUMN_USAGE`) or sorting key (ClickHouse, via `system.tables.sorting_key`)
  — if the PK/sorting key is composite (e.g. `order_id, material_id`), it's automatically used as a
  composite key, preserving the DDL's column order. Falls back to `id` if nothing is detected.
- **Key/chunk/date/exclude columns as dropdowns, not manual typing**: for tables already in a config
  and for suggestion results (auto-suggest or copied from another config), the "Key columns" &
  "Exclude" fields become `<select multiple>` (Ctrl+click to pick more than one — composite keys are
  a click away, not comma-typed) and "Chunk col"/"Date col" become `<select>` — all populated from
  the ACTUAL columns of that table on the source connection. For manually-added rows ("+ Add row"),
  a 🔍 button next to the table name loads its column list via AJAX as soon as the table name is
  typed.
- **Copy table mappings from another config**: on the config page, a dropdown lets you pick another
  config, then a "Copy mapping" button — copies `key_columns`/`chunk_column`/`date_column`/
  `exclude_columns`/`mode_override` that were already filled in for the same table, skipping tables
  already present in the current config. Results land as unsaved rows for review before "Save Table
  Mapping" — not an immediate overwrite.
- **Runs**: triggered from the UI, background execution (thread pool, configurable per-table
  concurrency), automatic 3× retry for transient errors, cancel, resume (skip tables already
  passed/failed). Progress is polled every 2 seconds (see "Live progress" below).
- **Drilldown**: per table — Summary, Aggregate Findings, Column Types (Report 2/3's raw source-vs-target
  type comparison per column, including columns whose CATEGORY differs — previously only used
  silently to skip stat comparison, now visible), Period (mismatches only), Missing Keys,
  Value Diffs, SQL (every query executed, for audit).
- **Global loading indicator**: every navigation (link click, form submit) immediately shows a thin
  progress bar at the top of the page + the submit button turns into "⏳ Loading..." — this app is
  full page-reload, no SPA, so without this marker a slow page (large table drilldown, etc.) looks
  identical to a hung app to non-technical users.
- **Excel export** compatible with the legacy format (Summary + one findings sheet per table).
- **Auth + RBAC**: session cookie, 3 roles (admin/editor/viewer) ENFORCED on every route (not just
  recorded) — viewer reads only, editor manages their own data, admin sees/manages everyone's. See
  the "RBAC & Data Scoping" section below for the full matrix.
- **Per-user data scoping**: Connections, Configs, and Runs each have an owner — new users do NOT
  see other users' data by default; admins see everything.
- **Interactive dashboard**: every table row (recent runs, config list, run history, per-run table
  list) is clickable straight to its detail page — not just a small text link. Includes a pass-rate
  trend widget (bar chart of the last few runs) and "Most Problematic Tables" (source/target pairs
  that FAIL/ERROR most often across run history).
- **Column filter on the Value Diffs tab**: a dropdown listing every column with a value diff plus
  its count, so you don't have to scroll hundreds of rows to check a specific column.
- **Numeric precision tolerance in row-level value diffs**: numeric (float/decimal) columns are
  compared with a relative+absolute tolerance (similar to `math.isclose`), not exact comparison —
  eliminating false positives from two engines serializing the SAME float value at different decimal
  precision (e.g. `482.437346437` vs `482.43734643734643`). Pure integer columns (no NULLs) are still
  compared exactly — see `validation_core/rowlevel/comparator.py::column_diff_mask`. Applies to any
  engine pair (MySQL vs ClickHouse, ClickHouse vs ClickHouse, etc.) since the comparison operates on
  data already pulled into pandas, not at the SQL level.

## Real bugs found & fixed while building this

While running 2 tables **concurrently** via `run_service.py`, one table would occasionally fail with
a confusing `IndexError: tuple index out of range` (from inside SQLAlchemy itself, not from
validation_core). Root cause: the original code read `source_conn.database` and `run.mode` (ORM
attributes) **from inside the worker thread**. SQLAlchemy expires all attributes after
`db.commit()`, so the next read triggers a lazy-reload through the same Session — and a single
`Session` is not safe to use concurrently from multiple threads. This race occasionally corrupted
row data at the cursor level.

Fix: every value the worker thread needs (`source_db_name`, `target_db_name`, `run_mode`) is now
captured as a **plain string** in the main thread, before the thread pool opens — the worker thread
now never touches a SQLAlchemy object at all, only `validation_core` + raw DB connections.
Regression test is in `tests/test_app_run_service.py` (runs 4 tables concurrently, repeatedly, to
confirm the race is truly gone, not just "usually passes").

### Incident: `database is locked` — Internal Server Error while a long run was in progress

While a **68 real ClickHouse tables** run (`tiered` mode, hours-long) was running in the background,
OTHER HTTP requests that only needed to read (e.g. checking login) also failed with
`sqlite3.OperationalError: database is locked`. Cause: SQLite's default (*rollback journal* /
`DELETE` mode) takes an EXCLUSIVE lock on the entire file for the duration of a write transaction —
commit after commit from the validation worker was enough to block other read requests (any page)
until the driver's timeout (5 seconds), then fail.

**Fix**: `app/database.py` now enables **WAL mode** (`PRAGMA journal_mode=WAL`) +
`busy_timeout=30000` via a SQLAlchemy connect event — WAL lets readers keep working WHILE one writer
is active (exactly this tool's workload pattern: many short reads, one background validation
writer). Verified with a test (`tests/test_run_service.py`) that actually opens a write transaction
and tries to read concurrently — failed before this fix, passes instantly after.

**An additional mistake made during the investigation** (recorded so it isn't repeated): seeing
`run_tables.progress = 0.0` in the database for a table that had been running for hours was
initially misread as "the process is stuck." In fact, `progress` is **only updated when a table
FINISHES**, not while it's running — so a table that's slowly being processed (waiting on
ClickHouse) ALWAYS shows `progress: 0.0` until it's actually done, looking identical to a table
whose process has died. This misreading led to a server process that was ACTUALLY validating being
force-killed — the in-memory computation running at that moment was **lost, with no way to
resume**. Preventive fix: `run_service.reap_orphaned_runs()` is now called once on every server
startup — if a Run's status is still `"running"`/`"queued"` even though the process JUST started
(meaning its owning thread died along with the previous process, whether from a restart/crash), it's
immediately marked `"failed"` with a clear message instead of silently looking "alive" forever.
***Accurate real-time per-chunk table progress*** unfortunately still doesn't exist in the database
(it only lives in the in-memory event bus, lost on server restart) — a reasonable follow-up item for
a later phase is to also stream chunk checkpoints into `run_tables`, not just the event bus, so
"still legitimately running" vs. "stuck" can be told apart without guessing.

### Incident: table detail page (Value Diffs / Missing Keys) was extremely slow to open

A user reported the "Value Diffs" tab (and others) on the validation result detail page taking ages
to open. Investigation found **two independent, compounding causes**:

1. **Architectural bug (permanent, always present)**: the `table_drilldown` route
   (`app/routers/ui.py`) loaded the **ENTIRE** `rt.rowlevel_findings` relationship via the ORM — up
   to 10,000-20,000+ rows per table (bounded by `rowlevel_sample_cap`, default 10,000 per finding
   type) — then rendered ALL of those rows as `<tr>` HTML in a SINGLE response, and this happened on
   EVERY tab open (including the "Summary" tab, which doesn't display that data at all). Real
   example: the `dashboard_delivery_time` table in the production database has 20,117 value-diff
   rows.
2. **Resource contention (conditional, happened to be occurring at the time)**: at the time it was
   reported, a 68-table real ClickHouse run (config "All table datamart") was actively running on
   the SAME server process — the uvicorn process was using ~12.9 GB of RAM (out of 22 GB system
   total, only ~2.7 GB free left), the same pattern as a previously-handled OOM incident (see commit
   `dashbaord_stock_sufficiency_monthly`). Directly demonstrated: a test request to the Value Diffs
   tab for the table above took **over 5 minutes** before being force-stopped — compared to a plain
   DB query to read those same 20 thousand findings rows taking just 0.3 seconds on an idle server.

**Fix (for bug #1)**: `table_drilldown` now ONLY computes COUNT/`GROUP BY` (cheap, doesn't hydrate
ORM objects) for the count badge on each tab, and ONLY fetches ONE PAGE (200 rows) of actual findings
for whichever tab is CURRENTLY open — other tabs never touch the `findings_rowlevel` table at all.
Also added a composite index `ix_findings_rowlevel_run_table_type` on `(run_table_id, finding_type)`
(the columns always used in `WHERE`) — previously this table (120-thousand-plus rows and growing)
was fully scanned with no index at all. Since `init_db()` only does `create_all()` (no
Alembic/migrations in this project, and `create_all` never alters an existing table), this index is
backfilled via an explicit `CREATE INDEX IF NOT EXISTS` in `app/database.py` — safe to run repeatedly,
a no-op if it already exists. Regression test is in `tests/test_table_drilldown.py`.

**Bug #2 (resource contention) has not been fixed** — needs a separate investigation into why a
single 68-table run can use ~13GB of RAM (candidates: per-table chunk size, no per-worker memory
limit, pandas DataFrames not released after a table finishes) before a fix can be decided.

### Incident: the "Cancel" button didn't stop a running run

A user reported that a run they'd clicked Cancel on kept running anyway. Root cause was in
`run_service.py::_execute_run()`: ALL tables are submitted to a `ThreadPoolExecutor` up front
(non-blocking, finishes in milliseconds even with 68 tables), then the code waits via
`as_completed(futures)` — which **BLOCKS until EVERY future is done**, regardless of cancel status.
The cancel flag (`bus.is_cancel_requested()`) was only checked in 2 places: (1) before submitting
each table — this window closes almost immediately once a run has been going for a few minutes,
since submitting doesn't wait on anything; and (2) AFTER `as_completed()` returns — i.e. after ALL
tables finish, making that check useless. `validation_core` itself also has no cancellation
checkpoint inside its chunk loop, so a table that's ACTIVELY being processed genuinely can't be
force-stopped mid-flight (a `ThreadPoolExecutor` can't kill a thread that's already running).

**Fix**: the cancel-flag check moved INSIDE the `as_completed` loop, and the first time it's
detected, `future.cancel()` is called on EVERY future — this only succeeds for tables that HAVEN'T
started executing yet (still queued behind the currently-running batch, bounded by
`table_concurrency`). Tables that already started must still finish naturally (can't be forced to
stop), but everything still queued is now skipped immediately instead of waited on — turning "wait
for all 68 tables to finish" into "wait only for the currently-running batch (usually
`table_concurrency`, default 4) to finish." Regression test is in `tests/test_run_cancel.py` (fakes
`vc_run_table` to be slow without needing real data, then verifies the run finishes much faster than
the full duration of all tables, and that tables which never got to run are genuinely `cancelled`,
not `pass`).

**A SECOND bug found while testing the fix above**: once Cancel actually had an effect, the OLD code
path handling "tables that were never submitted to the executor at all" (relevant when cancel is
requested BEFORE any table starts running) turned out to have been broken for a long time too, just
never noticed because Cancel previously almost never had any real effect. The line
`run.summary = _summarize_run(run_tables, db)` calls `db.refresh(rt)` PER TABLE — but the flip to
`"cancelled"` status just done in the PREVIOUS cleanup block hadn't been committed yet, so
`refresh()` silently DISCARDED that change and reloaded the OLD status (`"running"`) from the
database. Result: the run finished with status `"cancelled"`, but ALL its tables were left showing
`"running"` forever — exactly the bug pattern already documented and avoided in
`reap_orphaned_runs()` (see its comments), just missed here. Fix: `_summarize_run()` does NOT need
`db.refresh(rt)` at all — the `run_tables` objects it receives are ALREADY up to date in memory
(whether from the `as_completed` loop or the cleanup fallback), refreshing there is only ever risky,
never necessary. Dedicated regression test (deterministic, not timing-dependent):
`tests/test_run_cancel.py::test_cancel_before_any_table_starts_marks_everything_cancelled` —
requests cancel BEFORE `start_run_async` is even called, so this "never got to submit" path is
ALWAYS hit, not just sometimes depending on how fast the OS scheduler happens to run.

### Incident: `deleted_at` (NULL on both sides) detected as a mismatch — date flooring was corrupting NULLs

A user reported (with a screenshot of a real production run): a `deleted_at` column that's NULL on
BOTH sides (source & target) still showed up as a mismatch on the Aggregate Findings tab —
`min`/`max` showed source `None` (correct, NULL) but target `1970-01-01 00:00:00.000` (WRONG —
should also be `None`), and `uniqueness` differed (`0.0` vs `0.1667`).

The cause was exactly in the date-clamping fix just added (see "What actually works"):
`ClickHouseDialect.date_floor_1970()` wraps the column with `greatest(expr, toDateTime('1970-01-01
00:00:00'))`. Since **ClickHouse 24.12**, `greatest()`/`least()` **IGNORE NULL arguments** (if one
argument is NULL, the result is the OTHER argument — not NULL) — the opposite of MySQL/SQLite, which
correctly follow the SQL standard (`GREATEST(NULL, x)` = `NULL`). As a result:
`greatest(NULL, toDateTime('1970-01-01 00:00:00'))` in ClickHouse returns `1970-01-01 00:00:00`, NOT
`NULL` — a genuine NULL was silently turned into the floor date, corrupting `MIN()`/`MAX()` (which
now compute a fake value instead of ignoring it like a real NULL) and `COUNT(DISTINCT ...)` (which
now counts one extra distinct value that shouldn't exist, since `COUNT(DISTINCT)` normally ignores
NULL — once the NULL is "disguised" as a concrete value, it gets counted).

**Directly verified against production ClickHouse** (not just a guess) — a comparison query on
`raw_ws_orders` (3,331,669 rows): the OLD expression (plain `greatest`) returned **0 NULLs** even
though **3,331,100 rows are genuinely NULL** in that column; the NEW expression (with the guard)
returns **exactly 3,331,100 NULLs** — a 100% match with the real NULL count.

**Fix**: wrap `greatest`/`least` with `if(isNull(expr), NULL, ...)` in
`ClickHouseDialect.date_floor_1970()`/`date_ceiling()` — explicitly re-asserting NULL semantics
instead of depending on whichever ClickHouse version happens to be in use (this behavior HAS already
changed once before, in 24.12, and could change again). MySQL/SQLite need no changes — their scalar
`GREATEST`/`MAX` were already correct from the start. Regression test:
`tests/test_connectors.py::TestDialectDateClamping` (tests the exact SQL shape, including the
`if(isNull(...))` guard).

### Incident: Internal Server Error when creating a config with a name already in use

A user reported an Internal Server Error while creating a new config. The server log showed
`sqlalchemy.exc.IntegrityError: UNIQUE constraint failed: validation_configs.name` — the
`config_create()` route (`app/routers/ui.py`) called `db.add()` + `db.commit()` directly without
first checking whether the config name was already in use (`ValidationConfig.name` has
`unique=True`), so on a duplicate, the database exception bubbled up as a raw 500 instead of a clear
error message. **Fix**: check `db.query(...).filter_by(name=name).first()` FIRST before inserting
(the same pattern `connection_delete()` already uses for a similar case) — if the name already
exists, redirect back to the form with a flash error "Config name already in use, pick another
name," instead of crashing.

### Behavior change: a fully-clean Tier 2 now overrides a false Tier 1 FAIL

Requested by the user right after the `deleted_at`/NULL incident above: if Tier 1 (aggregate stats)
shows a mismatch but Tier 2 (row-level, more precise — compares row by row) finds **ZERO** missing
keys AND **ZERO** differing values, the table should PASS, not remain FAIL. Previously,
`validation_core/runner/tiered.py::run_table()` treated Tier 2 in pure `tiered` mode as a
"drill-down" — once Tier 1 said FAIL, the status STAYED FAIL no matter what Tier 2 found; row-level
was only there to show WHERE the problem was, not to correct the verdict. This became a real
problem: the `deleted_at` incident above PROVED Tier 1 can false-positive (a ClickHouse version bug,
numeric precision, etc. — see also the earlier precision-tolerance and date-flooring incidents), and
once Tier 2 has proven the data is actually identical, a FAIL "stuck" from Tier 1 becomes a confusing
false alarm.

**Fix (final version, after 2 iterations)**: if Tier 2's result is completely clean (0 missing on
both sides + 0 differing values), the final status becomes PASS — overriding Tier 1's FAIL, in
**BOTH Tier 2 modes** (`full` and `missing`).

The first version of this fix restricted the override to `full` mode only, on the grounds that
`missing` mode (automatically used for large tables above `full_mode_row_threshold`, 5 million rows)
only checks key existence and NEVER compares column contents — `differing_values_count == 0` in that
mode is trivially true, not proof the data matches. That restriction immediately hit a real case:
`dim_ws_entity_material_activities` (71 MILLION rows → automatically `missing` mode) still FAILed
even though its Tier 2 was clean, and the user explicitly decided (twice): **Tier 2 clean = PASS,
period, regardless of mode**. A consciously accepted trade-off: large tables whose Tier 1 FAIL was
caused purely by column VALUE differences (not missing rows) will now read as PASS — deemed
acceptable because in practice Tier 1 has repeatedly proven to produce false positives (the
NULL→1970 incident, float precision, etc.), while the "values drifted but row count is exactly the
same" case is far rarer. Regression test:
`tests/test_tiered_runner.py::TestTier2OverridesFalsePositiveTier1Fail` (4 scenarios: `full`+clean →
PASS, `full`+real difference → FAIL, `missing`+clean → PASS, `missing`+missing rows present → FAIL).

### Feature: per-config "Table Status" page + per-table re-run

A real usability problem from the partial re-run flow: every re-run creates a NEW Run containing
only the re-run tables — so once a run becomes partial, no single place shows "where does every
table currently stand"; the user had to mentally merge several runs. **Solution**: the
`/configs/{id}/status` page ("Table Status," linked from the config page, run page, and table
drilldown) showing a per-table matrix: the CURRENT status across all runs (not just the last one),
which run produced it, per-run status history (small chips, newest on the left, up to 15 runs, click
to open that run's drilldown), a KPI summary (pass/fail/error), and a **per-row ↻ Re-run button**.
The per-table re-run button also appears on the table drilldown header. After a per-table re-run,
the redirect goes BACK TO THE STATUS PAGE (not to the new 1-table run's own page) — and this page
auto-refreshes every 5 seconds while any table is still running. Tables removed from the config but
that still have history are still shown (flagged), so history doesn't silently disappear.
Test: `tests/test_config_status.py`.

### Behavior change: Resume = choice of re-run scope (all / fail / error / non-PASS)

Evolved twice at the user's request. Originally, the "Resume" button only re-ran tables that were
NOT YET FINISHED (error/pending/running remnants of an interrupted run) — `fail` tables were
considered "done" and skipped. Then it changed to "re-run all non-PASS." The final form now: a
**scope dropdown next to the Re-run button** on the run page — "All non-PASS (fail + error +
cancelled)" (default), "FAIL only," "ERROR only," or "All tables." If the chosen scope matches no
table at all (e.g. "ERROR only" when there are no error tables), NO new run is created — a flash
message appears instead of silently running all tables (a dangerous fallback in the old code:
`table_filter=remaining or None` meant an empty list → `None` → ALL tables). Implementation:
`run_service.resume_run(db, run, scope)` + `RESUME_SCOPES`, tests:
`tests/test_run_service.py::test_resume_scopes_select_the_right_tables` &
`test_resume_with_empty_scope_selection_creates_nothing`.

### Incident: an ERRORed table with no reason at all — and the birth of the "Log" tab

A user asked why a table could ERROR with no visible reason. Investigation found a deeper bug than
just "the feature doesn't exist yet": **the error message was never being saved at all**.
`tiered.run_table()` catches all table-level exceptions itself and returns a NORMAL result object
with `status="ERROR"` and the reason in `.error` — but `_persist_table_result()` never copied that
`.error` into the `run_tables.error` column (the `err is not None` path in `_execute_run` that DID
save the error was almost never active, because the exception had already been caught at a lower
layer). Result: ERRORed tables were stored with status `error` but a NULL message — no clue anywhere
in the UI.

**Fix (two layers)**:
1. `rt.error = result.error` in `_persist_table_result()` — the short reason is now saved and shown
   in the drilldown header.
2. **New "Log" tab** on the table drilldown: a per-table process trail (Tier 1/Tier 2 phases, chunk
   checkpoints, retries) that previously only lived in the in-memory event bus (lost once a run
   finishes/server restarts) is now recorded per table — capped at the last 200 events
   (`collections.deque(maxlen=200)`) so a large table with hundreds of chunk checkpoints doesn't
   bloat the DB — and persisted to a new `run_tables.event_log` JSON column when the table finishes.
   If a table ERRORs, the **full Python traceback** is appended as the final entry (new
   `TableRunResult.error_trace` field), so the root cause can be seen directly in the UI without
   opening server logs. Regression test: `tests/test_error_logging.py` (a real run with 1 valid
   table + 1 nonexistent table → the errored table must have `error` populated + a trail ending in a
   traceback; the passing table has a trail with no traceback; the Log tab renders all of it).

### Incident: `boolean value of NA is ambiguous` — a table ERRORed because of a one-sided column

Table `datamart_orders_smdv` ERRORed with the message `boolean value of NA is ambiguous`. Thanks to
the newly-added "Log" tab, the full traceback was immediately visible in the UI: a crash in
`_date_ceiling_bounds()` at `if not col_type` — column `master_updated_at` only exists on ONE side
(target), so its type on the other side in the merged-schema DataFrame is a missing value. The
problem: pandas missing values can come as `np.nan` (float) OR `pd.NA` depending on the DataFrame's
dtype, and `not pd.NA` raises a TypeError (pandas deliberately refuses to convert NA to a boolean) —
the existing `isinstance(col_type, float)` guard never got a chance to run because `not col_type` to
its LEFT was evaluated first. **Fix**: check `pd.isna()` FIRST before testing truthiness at all
(`if col_type is None or pd.isna(col_type): continue`). Directly verified against that table's
production schema (34 columns, including the one-sided one) — all passed. Regression test:
`tests/test_aggregate_validator.py::test_pandas_na_missing_type_does_not_crash` (all three missing
variants: `pd.NA`, `np.nan`, `float("nan")`).

**A second layer of the same incident** (found after the fix above landed): that one-sided column
(`master_updated_at`) also turned out to be the **configured `date_column`** for this table — and
the monthly/yearly breakdown uses `date_column` in queries on BOTH sides, so the target side (which
doesn't have that column) immediately failed with `UNKNOWN_IDENTIFIER`, ERRORing the whole table.
**Fix**: before any report runs, `AggregateValidator.run()` now checks that `date_column` exists on
BOTH schemas — if not, every date-based feature (monthly/yearly breakdown, investigate query, date
range filter) is skipped for that table (Reports 1-3 still run in full), with a clear note in the SQL
tab ("Period Breakdown SKIPPED" + reason + suggestion to fix the config). Filtering only on the side
that HAS the column isn't an option — the two sides would then be compared over different row sets.
Verified against the same production table: it now completes without crashing, with a GENUINE FAIL
verdict (13,036 vs 16,012 rows — a real ~3,000-row difference that genuinely should be flagged).
Regression test: `tests/test_aggregate_validator.py::TestDateColumnMissingOnOneSide`.

### Incident: an empty `StreamFailureError` — a large chunk fetch cut off at 600 seconds

Table `datamart_logger_monitoring` ERRORed with a `StreamFailureError:` whose message was EMPTY. The
new Log tab showed the timeline: chunk fetch started at 10:38:12, died at 10:48:48 — **636 seconds,
right past `send_receive_timeout=600`** in the ClickHouse connector. A twofold root cause: (1)
row-level chunking splits by id RANGE (`asset_rtmd_id` 0–22827, well below `id_chunk_size` 2
million) — but this is a composite-key table with MANY rows per id × 105 columns, so the entire table
fell into ONE chunk whose fetch took >10 minutes; (2) once the timeout severed the connection
mid-stream, `clickhouse_connect` raised a `StreamFailureError` containing whatever text the server
had managed to send — which was EMPTY, because the server hadn't managed to say anything.

**Fix**: (1) `send_receive_timeout` raised from 600 to 3600 seconds, consistent with the 3600-second
read/write timeout already used by the MySQL connector, and overridable per-connection via
`params.send_receive_timeout`; (2) an empty `StreamFailureError` is now replaced with an actionable
message (likely cause + suggestion to reduce `id_chunk_size` + the query snippet), and it
deliberately includes the word "connection" so it's classified as transient by the retry runner — a
network/VPN blip mid-stream now gets automatically retried like any other connection error.
Regression test: `tests/test_connectors.py::TestClickHouseStreamFailureMessage`.

**Second round of the same incident — the timeout wasn't the root cause.** Even at a 3600-second
timeout, the same table failed again: the fetch ran for almost 2 HOURS before the stream broke
mid-way (`unrecognized data found in stream`). The real root cause: **row-level chunking splits by
id RANGE** (`id_chunk_size`, default 2 million), which assumes ~1 row per id (an auto-increment PK).
A composite-key table breaks that assumption: `asset_rtmd_id` only spans 0–22827 but each id has
~139 rows (3.17 million rows total × 105 columns) → the ENTIRE table fell into ONE chunk.
**Fundamental fix: density-aware chunking** — the MIN/MAX query now also fetches COUNT(*), and if
density exceeds 1.5 rows/id, the id range per chunk shrinks so a chunk carries roughly
±`rowlevel_target_chunk_rows` ROWS (default 500 thousand; overridable per-config via `settings`).
Normal tables (~1 row/id) are unchanged — shrinking only ever MAKES chunks smaller, never bigger.
For the incident table: 3.17 million rows → 7 chunks × ±453 thousand rows (a few minutes per chunk
fetch, well under the timeout, memory bounded) instead of one 2-hour fetch. Regression test:
`tests/test_rowlevel_chunking.py` (a dense composite-key table → split into several row-bounded
chunks with correct validation results; a sparse/normal table → still 1 legacy chunk, no "dense"
message).

**Third round — stream desync `unrecognized data found in stream`.** After chunking was fixed (7
chunks, the first 3 went fine), chunk 4 still failed: the fetch hung for ~14 minutes then the
`clickhouse_connect` parser lost its position in the stream — the reported hex was clearly raw
float64 data read at the wrong offset. This is the classic desync pattern for **compressed HTTP
streams** passing through a proxy/LB (this deployment sits behind a domain/proxy): one frame
boundary gets re-chunked/disrupted, and every byte after it is misread. **Fix** (two layers): (1)
**response compression disabled by default** (`compress=False` in `get_client`; can be re-enabled
per connection via `params {"compress": true}`) — bandwidth goes up but the failure mode disappears;
(2) **self-heal in the connector**: a `StreamFailureError` leaves the HTTP client session in an
undefined state, so retrying on the same client is pointless — the connector now REBUILDS the client
(a fresh connection) and re-runs the query, up to 2 retries, before finally giving up with a clear
message; plus a "stream" marker was added to transient-error classification so any remaining failures
can still be retried at the table level. **Directly verified**: the chunk 4 that had failed twice was
refetched with a fresh connector — 1.16 million rows × 110 columns finished cleanly in 4.5 minutes
with no desync. Regression test: `tests/test_connectors.py::TestClickHouseStreamSelfHeal`.

**Incident: `NO_COMMON_TYPE` — a digit-only String chunk column mistaken for numeric.** Table
`datamart_wms_report_waste_bags` ERRORed: `waste_bag_id` is `Nullable(String)` on both sides, but its
contents are all digits (e.g. `101012026`). The old "numeric or not" detection used
`int(min_value)` — digit strings pass that conversion, so the runner incorrectly treated the column
as numeric: (1) the range query `WHERE waste_bag_id >= 101012026` used an integer literal, which
MySQL tolerates but ClickHouse rejects (`NO_COMMON_TYPE` String vs UInt32); (2) MIN/MAX on that
string sort LEXICOGRAPHICALLY, so the id range was nonsensical — this incident produced 499,912
chunks for a 254-thousand-row table. **Fix**: check the dtype of the MIN/MAX values the driver
returns — if `str`/`bytes` (the column is a string, whatever its content), fall back to the existing
single full-table-scan path already used for non-numeric columns, with a log message explaining why.
The incident table is only 254 thousand rows × 35 columns — a full scan is safe. Regression test:
`tests/test_rowlevel_chunking.py::test_digit_string_chunk_column_falls_back_to_full_scan`.

**A related bug surfaced by this incident** (the user's question: "why did the aggregate validation
disappear too, when the error was in Tier 2?"): a table that ERRORed in Tier 2 showed `rows: — / —`
and an empty Aggregate Findings tab — even though Tier 1 had ALREADY finished completely before Tier
2 failed. Cause: the `except` block in `tiered.run_table()` built a plain ERROR `TableRunResult`
carrying no results at all — `aggregate_result` was just a local variable inside `_do()` that died
along with the exception. **Fix**: results already completed are stashed into a `partial` dict as
each phase finishes (Tier 1's result, which tier was reached, every query executed so far) — the
error path now attaches all of that, so an ERRORed table still shows its Tier 1's row count,
aggregate findings, column types, period breakdown, and SQL tab, plus the Tier 2 error + traceback on
the Log tab. Regression test: `tests/test_tiered_runner.py::TestTier2ErrorKeepsTier1Results`.

### Feature: the Period tab now shows which column/metric mismatched, not just "this period differs"

A user was (reasonably) confused: the Period tab only showed Source rows/Target rows/Δ, so a period
with **Δ = 0** (row count IDENTICAL on both sides) still showed up in the mismatch list with no
visible reason — looking like a false alarm. This wasn't a bug: `match` in
`gen_report_period_breakdown` was never just about row count —
```python
merged["match"] = merged["row_match"] & (merged["stat_mismatch"] == 0)
```
`stat_mismatch` is computed from SUM/MIN/MAX/datediff of the other shared columns for that period —
if row count matches but one of those stats differs, the period is still flagged as a mismatch. The
problem: WHICH column/metric caused it was never stored, just computed briefly and then discarded.

**Fix**: `gen_report_period_breakdown` now stores `mismatch_detail` — a list of
`{column, metric, source, target}` per period, not just the `stat_mismatch` number.
`_persist_aggregate_findings` splits it into a SEPARATE finding per reason: one "row count" finding
(ONLY if row count actually differs), PLUS one finding per (column, metric) that mismatched — so a
Δ=0 period that's still flagged now has an explicit row saying "column X's sum differs," not nothing.
The Period tab shows new **Type** (row count / metric name: sum, min, max, datediff, sum_len, etc.)
and **Column** columns. The nav badge still counts mismatched PERIODS (not finding rows, which can
now outnumber periods). Regression test: `tests/test_period_findings.py` (`mismatch_detail`
computation using the real `orders_pair` fixture, metric alias parsing, persistence into separate
findings) + `tests/test_table_drilldown.py::test_periode_tab_shows_metric_detail_for_zero_delta_periods`.

### Feature: copy problematic keys (Missing Keys / Value Diffs) ready to paste into `WHERE id IN (...)`

User request: they have a manual script to re-insert into the pipeline, and need the list of
missing/differing IDs to use in `WHERE id IN (...)`. A **📋 Copy** button on the Missing Keys tab (2
buttons: missing in TARGET / missing in SOURCE) and Value Diffs (respects the active column filter,
or all columns if unfiltered) — fetches EVERY matching key (not just the currently-displayed page,
since both tabs are paginated) via a new endpoint
`GET /runs/{run_id}/tables/{run_table_id}/keys?kind=...&column=...`, displaying it in a textarea
that's auto-selected (so a manual Ctrl+C always works) while attempting an auto-copy to the clipboard
(`navigator.clipboard` requires HTTPS/localhost — if the server is accessed over plain HTTP on an
internal network, auto-copy can silently fail; hence the textarea fallback is MANDATORY, not just a
nice-to-have).

Result format: plain numbers comma-separated if EVERY key is numeric (ready to paste straight into
`IN (...)`), quoted (`'...'`) if any aren't. For differing keys in Value Diffs, the same key is NOT
repeated even if it appears across multiple different columns (distinct) — the user needs a unique
list of row ids to re-insert, not one row per differing column. For composite keys (more than 1 key
column), the key is shown AS-IS (joined with `_`, per `composite_key()` in the comparator) with a
header explaining the column order — DELIBERATELY not split back into a per-column tuple, since
joining with `_` is lossy (if any original value itself contains `_`, guessing the split could
silently get it wrong) — showing the true raw value is more honest than a guess that could quietly
be incorrect. Regression test: `tests/test_copy_keys.py`.

### Feature: visual polish (dropdowns, config table editor, copy-key popup) + stale CSS cache incident

Direct user request from screenshots: dropdowns looked plain/off-theme, the table mapping editor on
the config page looked messy, and the "Copy key" panel should be a centered popup. **Fixes** (purely
CSS/template, no backend logic touched):
- **Every `<select>`** app-wide previously had NO custom styling at all (relying on the browser/OS
  default look) — now gets a consistent border/radius, a custom chevron (different color for
  light/dark), hover/focus effects — automatically on EVERY page without touching a single template
  (a purely global CSS fix). `<select multiple>` is excluded from the chevron (browsers render it as
  an always-open listbox, not a closed dropdown) but still gets a matching border/color.
- **Table mapping editor** (`config_detail.html`): every cell `vertical-align: top`, consistent
  padding, uniform select/multi-select/input sizing, the delete button (✕) is now a neater small
  circle.
- **Copy Key panel**: changed from an inline textarea below the button into a **centered modal popup**
  (dark overlay, a card with a title + close button, a re-clickable "Copy to Clipboard" button) —
  generic CSS (`.modal-backdrop`/`.modal-card`) so it can be reused if another modal is needed later.

**A follow-on incident**: after deploying, the modal STILL rendered plain/unstyled in a user's
browser even though the server had been confirmed (via curl) to be serving the new CSS — **the
browser was caching the old `app.css`**. `StaticFiles` (used for `/static`) doesn't send any
`Cache-Control` header, so browsers are free to cache as aggressively as their own heuristics allow;
every time the CSS changes, a user whose browser had already opened this app would keep seeing the
old version until a manual hard-refresh. **Fix**: the stylesheet `<link>` in `base.html` now has a
query string `?v={{ asset_version }}` — `asset_version` is a Jinja *global* (set ONCE at startup in
`ui.py`, from the `app.css` file's own mtime) automatically available in EVERY template via
`base.html`. Once the CSS file changes AND the server is restarted (the only time the asset actually
changes, given there's no hot-reload in this app), its mtime changes, the stylesheet URL changes —
any old cached copy in any browser becomes irrelevant since it's never requested again under that old
URL. No manual version bump needed, doesn't depend on the user remembering to hard-refresh.

(Superseded by the React rewrite: Vite's production build already names every bundle
`<name>-<contenthash>.js`/`.css` under `frontend/dist/assets/`, so a changed file always gets a new
URL by construction — the same problem, solved by the build tool instead of a hand-rolled Jinja
global. `tests/test_asset_versioning.py`, which covered the old mechanism, was removed along with
`app/static/app.css` and `ui.py`.)

## RBAC & Data Scoping

Added 2026-07-21 during the migration to the production VPS. Summary (the full per-route matrix is
in TECHNICAL.md §9, local-only):

- **viewer** — can log in, view dashboard/configs/connections/runs. Cannot create or change anything.
- **editor** — all viewer rights, PLUS create/manage THEIR OWN Connections & Configs, run
  validations, cancel/resume runs. Cannot view/change data owned by OTHER users.
- **admin** — can view & manage EVERY user's data (bypasses ownership). No route is purely
  "admin-only" — the distinction is DATA visibility scope, not which actions are available.

Admins get a "Users" page in the app (create accounts, change role, reset password, deactivate).
Accounts can also be created/updated via the CLI, e.g. for first-run bootstrap or scripting:
```powershell
.venv\Scripts\python.exe scripts\create_user.py --username jane --password secret --role editor --name "Jane Doe"
```

Anyone can also self-register from the login page (`POST /api/register`) — the account activates
immediately as **editor**, with zero visibility into any other user's data until it creates its own
Connections/Configs. It can never self-grant admin or viewer; only an existing admin changes roles,
from the Users page.

Login is by **username + password** (not email).

## Deployment

Docker Compose (`app` + `postgres` + `caddy`) — see TECHNICAL.md §10 (local-only) for full
architectural detail and two networking gotchas that were found (`host.docker.internal` pointing at
the wrong gateway, UFW blocking container→host traffic). The `app` image build is multi-stage: a
`node:22-alpine` stage runs `npm ci && npm run build` for `frontend/`, then the Python stage copies
in just the built `frontend/dist` (not the frontend's source or `node_modules`) — no separate
frontend deploy step needed. Summary:

```powershell
# on the server/VPS, after cloning/copying the repo there:
cp .env.example .env
# fill in .env: LIDVALID_ENV=production, LIDVALID_SECRET_KEY (generate a fresh one for a NEW
# install; for MIGRATING an existing database, use the SAME key as the source's data/secret.key —
# see TECHNICAL.md §10.4/§5.3 — a different key means every stored connection password becomes
# undecryptable)
docker compose up -d --build
```

Change the hostname in `Caddyfile` from `<vps-ip>.sslip.io` to your server's public IP (or a real
domain if you have one) before deploying — Caddy handles the HTTPS certificate automatically.

If the target database (MySQL/ClickHouse) is only reachable over a private VPN and the server
doesn't have its own VPN profile yet: it can be bridged temporarily via an SSH reverse tunnel from a
device whose VPN is active (TECHNICAL.md §10.6) — the server will keep depending on that device's
live connection until it gets its own permanent VPN profile.

## Deviations from the target architecture (and why)

The [03-arsitektur.md](../../data-pipeline-batch/docs/validation-platform/03-arsitektur.md) document
specifies FastAPI + Celery/Redis + PostgreSQL + React. The environment this implementation was
originally built in **had no Node/npm installed** and **the Docker daemon wasn't running**, so
several decisions were adjusted so this project could be tried immediately with no extra setup:

| Target architecture | This implementation | Why | How to upgrade later |
|---|---|---|---|
| PostgreSQL | **Done** — Postgres in Docker Compose, migrated from the original SQLite install via `scripts/migrate_to_postgres.py` | Local dev/demo (`scripts/seed_demo.py`) still defaults to zero-setup SQLite via `DATABASE_URL` | — |
| Celery + Redis worker | in-process **`threading.Thread` + `ThreadPoolExecutor`** | Redis/Celery need a separate service, hard to install offline easily in the original session | Swap `run_service.start_run_async` for a Celery `.delay()` task; event/progress structure is already isolated in `events_bus.py` to make swapping to Redis pub/sub easy |
| React SPA | **Done** — Vite + TypeScript + Tailwind + shadcn/ui + Radix, under `frontend/`; `app/routers/api.py` is now the only HTTP interface (the old server-rendered Jinja2 UI was removed) | Originally deferred (no Node/npm in that session); added once Node was available | — |
| SSE for live progress | **Polling `fetch()` every 2 seconds** (now via TanStack Query's `refetchInterval` in the SPA) | Simpler & more robust without needing cross-process Redis pub/sub | `events_bus.py` is already an in-memory pub/sub — just wrap it in a `StreamingResponse` SSE if wanted |
| "All columns" drilldown (match + mismatch) | **Only findings/mismatches** are stored & shown | Lighter on the DB, and more focused on "what's wrong" — but genuinely not a full comparison table like the old Excel | Add a JSON column on `RunTable` to store the full `column_details`/`src_type_details` if ever needed |
| Import legacy YAML config | **Not implemented** | Out of scope for the original session | `validation_core.models.TableSpec` already has the exact semantics — just needs a YAML → `ConfigTable` rows parser |
| Full RBAC (admin/editor/viewer enforced on every endpoint) | **Implemented (2026-07-21)** — see "RBAC & Data Scoping" above | Originally deferred to focus time on the engine + core flow; added when migrating to a shared production VPS | Done — no further upgrade needed for the current scope |
| Slack/email notifications, cron scheduler | **Not implemented** | Phase 2 in the roadmap, out of scope for this MVP | — |
| Backfill trigger from the UI | **Not implemented** | Phase 3 in the roadmap | — |

A standalone CLI (`validation_core.runner.run_table` called directly from Python) still works with
no web layer at all — see `tests/conftest.py` for an example of calling a connector directly.

## Demo credentials

- Login: `admin@lidvalid.local` / `admin123` — **change this after the demo**, or delete
  `data/lidvalid.sqlite` to start clean.
- The connection encryption key (`data/secret.key`) is auto-created on first run. **Don't commit this
  file** (already in `.gitignore`). For production, set `LIDVALID_SECRET_KEY` as an env var instead
  of relying on the local file.
