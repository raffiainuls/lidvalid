"""SQLAlchemy engine/session setup.

Defaults to a local SQLite file so the app runs with zero external setup
(`uvicorn app.main:app`, no Docker/Postgres/Redis required — see README.md
"Deviations from the target architecture"). Set DATABASE_URL to point at
PostgreSQL for anything beyond local dev/demo, per
docs/validation-platform/03-arsitektur.md.
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, declarative_base

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR / 'lidvalid.sqlite'}")
_IS_SQLITE = DATABASE_URL.startswith("sqlite")

connect_args = {"check_same_thread": False} if _IS_SQLITE else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)

if _IS_SQLITE:
    # SQLite's default journal mode ("DELETE" / rollback journal) takes an
    # EXCLUSIVE lock on the whole file for the duration of any write
    # transaction -- with run_service's background thread committing
    # per-table results (findings inserts, run_tables updates) while other
    # HTTP requests concurrently try to just read `users`/`runs`, those reads
    # get blocked and the sqlite3 driver's default 5s timeout was surfacing
    # as `sqlite3.OperationalError: database is locked` / an Internal Server
    # Error on ordinary page loads. WAL mode lets readers proceed while a
    # writer is active (the actual workload here: many short reads, one
    # writer running validations in the background); busy_timeout raises the
    # driver's own wait-and-retry window for the writer-vs-writer case WAL
    # doesn't cover, instead of failing immediately.
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401 - register models on Base before create_all
    Base.metadata.create_all(bind=engine)
    # create_all() only creates brand-new tables -- it never alters a table
    # that already exists, so an Index/Column added to a model later (like
    # these, added for the table-drilldown pagination fix and the Tipe
    # Kolom tab respectively) never reaches a database that predates them.
    # There's no migration framework here, so backfill by hand.
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_findings_rowlevel_run_table_type "
            "ON findings_rowlevel (run_table_id, finding_type)"
        ))
        # SQLite's ADD COLUMN has no IF NOT EXISTS -- check PRAGMA table_info
        # first so this stays a safe no-op on databases that already have it
        # (fresh ones, via create_all() above; already-backfilled ones, on
        # every subsequent startup).
        existing_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(run_tables)"))}
        if "column_type_details" not in existing_cols:
            conn.execute(text("ALTER TABLE run_tables ADD COLUMN column_type_details TEXT"))
        if "event_log" not in existing_cols:
            conn.execute(text("ALTER TABLE run_tables ADD COLUMN event_log TEXT"))
        conn.commit()
