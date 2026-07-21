#!/usr/bin/env python
"""One-time data migration: copy every row from the local SQLite
lidvalid.sqlite into a fresh PostgreSQL database, then reset Postgres's
serial sequences to match the copied primary keys.

Reads/writes go through the same SQLAlchemy Table objects (app/models.py),
so each column's type descriptor handles the dialect-specific conversion in
both directions -- JSON (TEXT <-> JSON/JSONB), Boolean (0/1 <-> bool),
DateTime (ISO string <-> timestamp) -- without any manual parsing here.

Usage (run inside the `app` container so TARGET_DATABASE_URL can reach the
`postgres` Compose service by hostname without exposing a port to the host):
    docker compose run --rm \\
        -e SOURCE_DATABASE_URL=sqlite:////app/data/lidvalid.sqlite \\
        -e TARGET_DATABASE_URL=postgresql+psycopg://user:pass@postgres:5432/lidvalid \\
        app python scripts/migrate_to_postgres.py

Safe to inspect/re-run against an empty target: it only ever INSERTs, it
never touches or deletes the source SQLite file.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text

from app.database import Base
from app import models  # noqa: F401 -- registers tables on Base.metadata


def _strip_nul(value):
    # Postgres text/JSON columns reject embedded NUL (0x00) bytes outright
    # (`PostgreSQL text fields cannot contain NUL (0x00) bytes`) -- SQLite has
    # no such restriction, and at least one stored `investigate_query` (a raw
    # SQL string) has one baked in. Recurse into JSON columns' already-decoded
    # dict/list values too, since the NUL could be nested in there instead.
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {k: _strip_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_nul(v) for v in value]
    return value


def main() -> None:
    source_url = os.environ.get("SOURCE_DATABASE_URL", "sqlite:////app/data/lidvalid.sqlite")
    target_url = os.environ["TARGET_DATABASE_URL"]

    print(f"Source: {source_url}")
    print(f"Target: {target_url}")

    source_engine = create_engine(source_url)
    target_engine = create_engine(target_url)

    Base.metadata.create_all(bind=target_engine)

    table_names = [t.name for t in Base.metadata.sorted_tables]
    with target_engine.connect() as tgt_conn:
        # Idempotent re-run: clear any partially-migrated data from a
        # previous attempt before copying fresh, instead of erroring on
        # duplicate primary keys or silently double-inserting.
        tgt_conn.execute(text(f"TRUNCATE TABLE {', '.join(table_names)} RESTART IDENTITY CASCADE"))
        tgt_conn.commit()

    mismatches = []
    with source_engine.connect() as src_conn, target_engine.connect() as tgt_conn:
        for table in Base.metadata.sorted_tables:  # FK-dependency order
            rows = [
                {k: _strip_nul(v) for k, v in dict(r).items()}
                for r in src_conn.execute(table.select()).mappings().all()
            ]
            if rows:
                tgt_conn.execute(table.insert(), rows)
                tgt_conn.commit()

            src_count = src_conn.execute(text(f'SELECT COUNT(*) FROM "{table.name}"')).scalar()
            tgt_count = tgt_conn.execute(text(f'SELECT COUNT(*) FROM "{table.name}"')).scalar()
            ok = src_count == tgt_count
            if not ok:
                mismatches.append(table.name)
            print(f"  {table.name}: source={src_count} target={tgt_count} {'OK' if ok else 'MISMATCH'}")

            if "id" in table.c and rows:
                tgt_conn.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('\"{table.name}\"', 'id'), "
                    f"(SELECT MAX(id) FROM \"{table.name}\"))"
                ))
                tgt_conn.commit()

    if mismatches:
        print(f"\nFAILED -- row count mismatch in: {', '.join(mismatches)}")
        sys.exit(1)
    print("\nMigration complete -- all row counts match.")


if __name__ == "__main__":
    main()
