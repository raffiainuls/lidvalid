"""Bridges the `connections` ORM table to validation_core Connectors."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from validation_core.connectors import ConnectionParams, create_connector

from .. import models, security


def to_connection_params(conn: models.Connection) -> ConnectionParams:
    return ConnectionParams(
        engine=conn.engine,
        host=conn.host or "",
        port=conn.port or 0,
        database=conn.database or "",
        username=conn.username or "",
        password=security.decrypt_secret(conn.secret_encrypted),
        params=conn.params or {},
    )


def test_connection(conn: models.Connection) -> dict:
    connector = create_connector(to_connection_params(conn))
    try:
        return connector.test_connection()
    finally:
        connector.close()


def test_connection_payload(engine: str, host: str, port: int, database: str,
                             username: str, password: str, params: dict | None = None) -> dict:
    """Test a connection without persisting it first (Add-connection form)."""
    connector = create_connector(ConnectionParams(
        engine=engine, host=host, port=port, database=database,
        username=username, password=password, params=params or {},
    ))
    try:
        return connector.test_connection()
    finally:
        connector.close()


def record_test_result(db: Session, conn: models.Connection, result: dict) -> None:
    conn.status = "ok" if result.get("ok") else "failed"
    conn.last_tested_at = datetime.now(timezone.utc)
    conn.last_test_message = result.get("error") or (
        f"OK — {result.get('latency_ms')}ms" if result.get("latency_ms") is not None else "OK"
    )
    db.commit()
