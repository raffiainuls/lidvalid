"""ORM models — trimmed, SQLite-friendly version of
docs/validation-platform/04-data-model.md (13-table schema). JSON columns use
SQLAlchemy's cross-dialect JSON type (stored as TEXT on SQLite, JSONB on
Postgres if DATABASE_URL is switched later).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, JSON,
    LargeBinary, String, Text,
)
from sqlalchemy.orm import relationship

from .database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(255), default="")
    role = Column(String(20), default="editor")  # admin | editor | viewer
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)


class Connection(Base):
    __tablename__ = "connections"
    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    engine = Column(String(30), nullable=False)  # mysql | clickhouse | sqlite
    host = Column(String(255), default="")
    port = Column(Integer, default=0)
    database = Column(String(255), default="")
    username = Column(String(255), default="")
    secret_encrypted = Column(LargeBinary, nullable=True)
    params = Column(JSON, default=dict)
    status = Column(String(20), default="unknown")  # unknown | ok | failed
    last_tested_at = Column(DateTime, nullable=True)
    last_test_message = Column(Text, default="")
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class ValidationConfig(Base):
    __tablename__ = "validation_configs"
    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    description = Column(Text, default="")
    source_connection_id = Column(Integer, ForeignKey("connections.id"), nullable=False)
    target_connection_id = Column(Integer, ForeignKey("connections.id"), nullable=False)
    default_mode = Column(String(30), default="tiered")
    settings = Column(JSON, default=dict)  # RunSettings overrides
    is_archived = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    source_connection = relationship("Connection", foreign_keys=[source_connection_id])
    target_connection = relationship("Connection", foreign_keys=[target_connection_id])
    tables = relationship("ConfigTable", back_populates="config", cascade="all, delete-orphan")


class ConfigTable(Base):
    __tablename__ = "config_tables"
    id = Column(Integer, primary_key=True)
    config_id = Column(Integer, ForeignKey("validation_configs.id"), nullable=False)
    source_table = Column(String(255), nullable=False)
    target_table = Column(String(255), nullable=False)
    key_columns = Column(JSON, default=lambda: ["id"])
    chunk_column = Column(String(255), nullable=True)
    date_column = Column(String(255), nullable=True)
    exclude_columns = Column(JSON, default=list)
    mode_override = Column(String(30), nullable=True)
    start_date = Column(String(10), nullable=True)
    end_date = Column(String(10), nullable=True)
    enabled = Column(Boolean, default=True)
    note = Column(Text, default="")

    config = relationship("ValidationConfig", back_populates="tables")


class Run(Base):
    __tablename__ = "runs"
    id = Column(Integer, primary_key=True)
    config_id = Column(Integer, ForeignKey("validation_configs.id"), nullable=False)
    trigger_type = Column(String(20), default="manual")  # manual | schedule | api | revalidate
    mode = Column(String(30), default="tiered")
    table_filter = Column(JSON, nullable=True)
    status = Column(String(20), default="queued")  # queued | running | completed | failed | cancelled
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    summary = Column(JSON, default=dict)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    config = relationship("ValidationConfig")
    tables = relationship("RunTable", back_populates="run", cascade="all, delete-orphan")


class RunTable(Base):
    __tablename__ = "run_tables"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("runs.id"), nullable=False)
    config_table_id = Column(Integer, ForeignKey("config_tables.id"), nullable=True)
    source_table = Column(String(255))
    target_table = Column(String(255))
    status = Column(String(20), default="pending")  # pending|running|pass|fail|error|skipped|cancelled
    tier_reached = Column(Integer, default=0)
    mode = Column(String(30), default="")
    source_rows = Column(Integer, nullable=True)
    target_rows = Column(Integer, nullable=True)
    row_diff = Column(Integer, nullable=True)
    source_cols = Column(Integer, nullable=True)
    target_cols = Column(Integer, nullable=True)
    extra_source_columns = Column(JSON, default=list)
    extra_target_columns = Column(JSON, default=list)
    agg_metrics = Column(JSON, default=dict)
    rl_metrics = Column(JSON, default=dict)
    # Full per-column schema/type comparison (source_type, target_type,
    # category match) for EVERY shared+source-only+target-only column, not
    # just mismatches -- powers the "Tipe Kolom" drilldown tab. Separate from
    # `agg_metrics`/FindingAggregate's category="stat" rows, which only ever
    # capture METRIC mismatches (min/max/sum etc, assuming categories already
    # match); a genuine schema-level type drift between source and target
    # was previously invisible anywhere in the UI. See database.py's
    # init_db() for why this needs a manual ALTER TABLE backfill.
    column_type_details = Column(JSON, default=list)
    progress = Column(Float, default=0.0)
    chunks_done = Column(Integer, default=0)
    chunks_total = Column(Integer, default=0)
    attempt = Column(Integer, default=1)
    investigate_query = Column(Text, nullable=True)
    queries = Column(JSON, default=dict)
    error = Column(Text, nullable=True)
    # Bounded trail of progress events (phase changes, chunk checkpoints,
    # retries) recorded while THIS table was being validated, plus the full
    # traceback as the final entry when it errored -- previously this only
    # lived in the in-memory event bus (gone after the run/restart), so an
    # errored table gave no clue WHY it failed. Powers the drilldown's
    # "Log" tab. See database.py init_db() for the manual backfill.
    event_log = Column(JSON, default=list)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    run = relationship("Run", back_populates="tables")
    aggregate_findings = relationship("FindingAggregate", back_populates="run_table", cascade="all, delete-orphan")
    rowlevel_findings = relationship("FindingRowLevel", back_populates="run_table", cascade="all, delete-orphan")


class FindingAggregate(Base):
    __tablename__ = "findings_aggregate"
    id = Column(Integer, primary_key=True)
    run_table_id = Column(Integer, ForeignKey("run_tables.id"), nullable=False)
    category = Column(String(30))  # row_count | completeness | uniqueness | stat | period_monthly | period_yearly
    column_name = Column(String(255), nullable=True)
    metric = Column(String(30), nullable=True)
    period = Column(String(10), nullable=True)
    source_value = Column(Text, nullable=True)
    target_value = Column(Text, nullable=True)
    difference = Column(Float, nullable=True)

    run_table = relationship("RunTable", back_populates="aggregate_findings")


class FindingRowLevel(Base):
    __tablename__ = "findings_rowlevel"
    id = Column(Integer, primary_key=True)
    run_table_id = Column(Integer, ForeignKey("run_tables.id"), nullable=False)
    finding_type = Column(String(20))  # missing_in_source | missing_in_target | value_diff
    row_key = Column(String(500))
    column_name = Column(String(255), nullable=True)
    source_value = Column(Text, nullable=True)
    target_value = Column(Text, nullable=True)

    run_table = relationship("RunTable", back_populates="rowlevel_findings")

    __table_args__ = (
        Index("ix_findings_rowlevel_run_table_type", "run_table_id", "finding_type"),
    )


class RunEvent(Base):
    __tablename__ = "run_events"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("runs.id"), nullable=False)
    run_table_id = Column(Integer, ForeignKey("run_tables.id"), nullable=True)
    ts = Column(DateTime, default=_utcnow)
    level = Column(String(10), default="info")  # info | checkpoint | warning | error
    message = Column(Text)
    data = Column(JSON, nullable=True)
