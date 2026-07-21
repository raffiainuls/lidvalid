#!/usr/bin/env python
"""Seed a demo environment: two local SQLite "databases" standing in for a
MySQL source and a ClickHouse target, registered as Connections, wired into
a Config with a couple of tables, and run once — so opening the app for the
first time shows a real dashboard/report/drilldown instead of an empty state.

Usage:
    .venv/Scripts/python.exe scripts/seed_demo.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, init_db
from app import models, security
from app.services import run_service

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
SRC_PATH = DATA_DIR / "demo_source.sqlite"
TGT_PATH = DATA_DIR / "demo_target.sqlite"


def _build_demo_databases() -> None:
    for p in (SRC_PATH, TGT_PATH):
        if p.exists():
            p.unlink()

    src = sqlite3.connect(SRC_PATH)
    src.executescript("""
        CREATE TABLE ws_orders (
            id INTEGER PRIMARY KEY, customer_name TEXT, amount REAL, status TEXT, created_at TEXT
        );
        CREATE TABLE ws_materials (
            id INTEGER PRIMARY KEY, material_name TEXT, unit TEXT, created_at TEXT
        );
    """)
    src.executemany(
        "INSERT INTO ws_orders VALUES (?,?,?,?,?)",
        [
            (i, f"Customer {i}", float(i * 15000), "completed" if i % 7 else "pending",
             f"2025-{(i % 12) + 1:02d}-{(i % 27) + 1:02d} 09:00:00")
            for i in range(1, 1201)
        ],
    )
    src.executemany(
        "INSERT INTO ws_materials VALUES (?,?,?,?)",
        [(i, f"Material {i}", "kg" if i % 2 else "pcs", f"2025-{(i % 12) + 1:02d}-01")
         for i in range(1, 81)],
    )
    src.commit()
    src.close()

    tgt = sqlite3.connect(TGT_PATH)
    tgt.executescript("""
        CREATE TABLE raw_ws_orders (
            id INTEGER PRIMARY KEY, customer_name TEXT, amount REAL, status TEXT, created_at TEXT,
            ingested_at TEXT, _dlt_id TEXT, _dlt_load_id TEXT, version INTEGER
        );
        CREATE TABLE raw_ws_materials (
            id INTEGER PRIMARY KEY, material_name TEXT, unit TEXT, created_at TEXT,
            ingested_at TEXT, _dlt_id TEXT
        );
    """)
    order_rows = []
    for i in range(1, 1201):
        if i in (42, 313, 890, 891, 1150):  # intentionally missing in target
            continue
        amount = float(i * 15000)
        if i in (100, 777):  # intentional value drift
            amount = amount + 500_000
        order_rows.append((
            i, f"Customer {i}", amount, "completed" if i % 7 else "pending",
            f"2025-{(i % 12) + 1:02d}-{(i % 27) + 1:02d} 09:00:00",
            "2026-07-11 05:30:00", f"dlt_{i}", "load_001", 1,
        ))
    tgt.executemany("INSERT INTO raw_ws_orders VALUES (?,?,?,?,?,?,?,?,?)", order_rows)
    # ws_materials mirrors perfectly -> this table should PASS
    tgt.executemany(
        "INSERT INTO raw_ws_materials VALUES (?,?,?,?,?,?)",
        [(i, f"Material {i}", "kg" if i % 2 else "pcs", f"2025-{(i % 12) + 1:02d}-01",
          "2026-07-11 05:30:00", f"dlt_m{i}")
         for i in range(1, 81)],
    )
    tgt.commit()
    tgt.close()

    print(f"Demo databases created: {SRC_PATH.name} (source), {TGT_PATH.name} (target)")


def main() -> None:
    _build_demo_databases()
    init_db()
    db = SessionLocal()
    try:
        admin = db.query(models.User).filter_by(role="admin").order_by(models.User.id).first()
        if not admin:
            admin = models.User(
                username="admin",
                password_hash=security.hash_password("admin123"),
                display_name="Admin", role="admin",
            )
            db.add(admin)
            db.commit()
            db.refresh(admin)
            print("Bootstrap admin created: admin / admin123")

        source_conn = db.query(models.Connection).filter_by(name="Demo Source (contoh)").first()
        if not source_conn:
            source_conn = models.Connection(
                owner_id=admin.id,
                name="Demo Source (contoh)", engine="sqlite", database=str(SRC_PATH), status="ok",
            )
            db.add(source_conn)

        target_conn = db.query(models.Connection).filter_by(name="Demo Target (contoh)").first()
        if not target_conn:
            target_conn = models.Connection(
                owner_id=admin.id,
                name="Demo Target (contoh)", engine="sqlite", database=str(TGT_PATH), status="ok",
            )
            db.add(target_conn)
        db.commit()
        db.refresh(source_conn)
        db.refresh(target_conn)

        config = db.query(models.ValidationConfig).filter_by(name="Demo: Contoh Validasi").first()
        if not config:
            config = models.ValidationConfig(
                owner_id=admin.id,
                name="Demo: Contoh Validasi",
                description="Contoh pasangan tabel dengan mismatch sengaja disisipkan, untuk mencoba UI tanpa akses VPN/DB nyata.",
                source_connection_id=source_conn.id, target_connection_id=target_conn.id,
                default_mode="tiered",
            )
            db.add(config)
            db.commit()
            db.refresh(config)

            db.add(models.ConfigTable(
                config_id=config.id, source_table="ws_orders", target_table="raw_ws_orders",
                key_columns=["id"], chunk_column="id", date_column="created_at",
                note="Sengaja ada 5 missing rows + 2 value diff",
            ))
            db.add(models.ConfigTable(
                config_id=config.id, source_table="ws_materials", target_table="raw_ws_materials",
                key_columns=["id"], chunk_column="id", date_column="created_at",
                note="Identik — harus PASS",
            ))
            db.commit()
            print(f"Config created: {config.name} (id={config.id})")
        else:
            print(f"Config already exists: {config.name} (id={config.id})")

        run = run_service.create_run(db, config, mode="tiered", trigger_type="manual")
        run_service.start_run_async(run.id)
        print(f"Run #{run.id} started in background...")

        from app.services.events_bus import bus
        for _ in range(120):
            db.refresh(run)
            if run.status not in ("queued", "running"):
                break
            time.sleep(0.5)
        print(f"Run #{run.id} finished: {run.status} — {run.summary}")

    finally:
        db.close()

    print("\n" + "=" * 60)
    print("Demo siap. Jalankan servernya:")
    print("  .venv/Scripts/uvicorn app.main:app --reload")
    print("Lalu buka http://127.0.0.1:8000  (login admin@lidvalid.local / admin123)")
    print("=" * 60)


if __name__ == "__main__":
    main()
