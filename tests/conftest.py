import importlib
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from validation_core.connectors import ConnectionParams, create_connector


def reload_app_with_fresh_db(database_url: str, secret_key: str | None = None):
    """Point the `app` package at a fresh SQLite file and re-register its ORM
    models against a brand-new declarative Base -- for tests that need a
    fully isolated app instance (own tables, no cross-test bleed).

    Handles BOTH orderings deterministically, which a naive "import then
    always reload" sequence does not:
    - `app.models` never imported yet in this process -> a plain `import`
      already executes the class definitions against the just-reloaded
      (fresh) Base. Reloading it AGAIN right after is redundant and actively
      wrong: SQLAlchemy raises `Table 'x' is already defined for this
      MetaData instance` from redefining the same tables on the same fresh
      metadata twice in a row.
    - `app.models` already imported (e.g. another test file did
      `from app import models` at collection time, which happens for ALL
      test files before any test body runs) -> its classes are bound to a
      STALE Base from that earlier import, so a reload is REQUIRED to
      re-register them against the new one.
    Checking `sys.modules` first picks the right branch regardless of
    what other test files happened to import before this one ran --
    depending on pytest's collection order here previously caused this exact
    reload dance to pass or fail depending on which OTHER test files existed.

    ALSO reloads `app.services.run_service` (same already-imported check) for
    a second, independent reason: it does `from ..database import
    SessionLocal` at module level -- a direct name binding to whatever
    `SessionLocal` object existed at THAT import time. Reloading
    `app.database` alone creates a new `SessionLocal`, but every OTHER module
    that already bound the OLD one by name keeps using it forever unless
    reloaded too. Left unreloaded, `run_service._execute_run` (the run's
    background thread) opens sessions against the PREVIOUS test's database
    file -- the run row it's trying to update doesn't even exist there, so
    the thread silently no-ops and the run sits "queued" forever, which is
    exactly what happened before this was found (only reproduced when a
    prior test file had already exercised run_service against a different
    fresh DB). Modules that only do `from .. import models` / `from
    ..database import get_db` (a function looked up dynamically at call
    time) don't have this problem and don't need reloading.
    """
    os.environ["DATABASE_URL"] = database_url
    if secret_key:
        os.environ["LIDVALID_SECRET_KEY"] = secret_key

    models_already_imported = "app.models" in sys.modules
    run_service_already_imported = "app.services.run_service" in sys.modules

    import app.database as db_module
    importlib.reload(db_module)

    import app.models as models_module
    if models_already_imported:
        importlib.reload(models_module)

    db_module.init_db()

    if run_service_already_imported:
        import app.services.run_service as run_service_module
        importlib.reload(run_service_module)

    return db_module, models_module


def _make_sqlite(path: Path, ddl: str, rows: list[tuple], insert_sql: str) -> None:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript(ddl)
    if rows:
        conn.executemany(insert_sql, rows)
    conn.commit()
    conn.close()


@pytest.fixture
def orders_pair(tmp_path):
    """Two SQLite DBs modeling a MySQL-source/ClickHouse-target style pair:
    target has 2 missing rows, 1 changed value, and 2 extra pipeline columns."""
    src_path = tmp_path / "source.sqlite"
    tgt_path = tmp_path / "target.sqlite"

    _make_sqlite(
        src_path,
        "CREATE TABLE ws_orders (id INTEGER PRIMARY KEY, customer_name TEXT, amount REAL, created_at TEXT);",
        [(i, f"Customer {i}", float(i * 1000), f"2025-{(i % 12) + 1:02d}-15 10:00:00") for i in range(1, 201)],
        "INSERT INTO ws_orders VALUES (?,?,?,?)",
    )

    target_rows = []
    for i in range(1, 201):
        if i in (10, 20):  # missing in target
            continue
        amount = 999999.0 if i == 100 else float(i * 1000)  # one changed value
        target_rows.append((i, f"Customer {i}", amount, f"2025-{(i % 12) + 1:02d}-15 10:00:00", "2026-01-01", f"dlt_{i}"))
    _make_sqlite(
        tgt_path,
        "CREATE TABLE raw_ws_orders (id INTEGER PRIMARY KEY, customer_name TEXT, amount REAL, "
        "created_at TEXT, ingested_at TEXT, _dlt_id TEXT);",
        target_rows,
        "INSERT INTO raw_ws_orders VALUES (?,?,?,?,?,?)",
    )

    source = create_connector(ConnectionParams(engine="sqlite", database=str(src_path)))
    target = create_connector(ConnectionParams(engine="sqlite", database=str(tgt_path)))
    yield source, target
    source.close()
    target.close()


@pytest.fixture
def identical_pair(tmp_path):
    """Two SQLite DBs with identical data -- should PASS every report."""
    src_path = tmp_path / "identical_source.sqlite"
    tgt_path = tmp_path / "identical_target.sqlite"
    rows = [(i, f"Item {i}", float(i), f"2025-06-{(i % 27) + 1:02d}") for i in range(1, 51)]
    ddl = "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT, val REAL, d TEXT);"
    _make_sqlite(src_path, ddl, rows, "INSERT INTO t VALUES (?,?,?,?)")
    _make_sqlite(tgt_path, ddl, rows, "INSERT INTO t VALUES (?,?,?,?)")

    source = create_connector(ConnectionParams(engine="sqlite", database=str(src_path)))
    target = create_connector(ConnectionParams(engine="sqlite", database=str(tgt_path)))
    yield source, target
    source.close()
    target.close()
