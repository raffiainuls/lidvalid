"""Connector factory — maps an engine name to its Connector class."""
from __future__ import annotations

from .base import Connector, ConnectionParams
from .mysql import MySqlConnector
from .clickhouse import ClickHouseConnector
from .sqlite_demo import SqliteConnector

_REGISTRY: dict[str, type[Connector]] = {
    "mysql": MySqlConnector,
    "clickhouse": ClickHouseConnector,
    "sqlite": SqliteConnector,
}

SUPPORTED_ENGINES = tuple(_REGISTRY.keys())


def create_connector(params: ConnectionParams) -> Connector:
    engine = params.engine.lower().strip()
    try:
        cls = _REGISTRY[engine]
    except KeyError:
        raise ValueError(
            f"Unsupported engine '{engine}'. Supported: {', '.join(SUPPORTED_ENGINES)}"
        )
    return cls(params)
