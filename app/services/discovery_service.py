"""Table/column discovery + mapping suggestions (PRD FR-4, US-02).

Backs the config-builder table-mapping step: list what's on each side, then
suggest pairs by a naming rule (prefix, identical name) before the user
confirms/edits them.
"""
from __future__ import annotations

from validation_core.connectors import create_connector

from . import connections_service
from .. import models


def list_tables(conn: models.Connection) -> list[str]:
    connector = create_connector(connections_service.to_connection_params(conn))
    try:
        return connector.list_tables(conn.database)
    finally:
        connector.close()


def list_columns(conn: models.Connection, table: str) -> list[dict]:
    from validation_core.categories import get_category

    connector = create_connector(connections_service.to_connection_params(conn))
    try:
        df = connector.get_schema(conn.database, table)
        if df.empty or "column_name" not in df.columns:
            return []  # table not found / has no columns -- not an error, just nothing to show
        col_type_key = "column_type" if "column_type" in df.columns else df.columns[-1]
        return [
            {"name": row["column_name"], "type": row[col_type_key], "category": get_category(str(row[col_type_key]))}
            for _, row in df.iterrows()
        ]
    finally:
        connector.close()


def get_primary_key(conn: models.Connection, table: str) -> list[str]:
    connector = create_connector(connections_service.to_connection_params(conn))
    try:
        return connector.get_primary_key(conn.database, table)
    finally:
        connector.close()


def columns_by_table(conn: models.Connection, table_names: list[str]) -> dict[str, list[str]]:
    """Column names for several tables in ONE connection (opened once, reused
    for every table, closed once) — backs the key/chunk/date/exclude column
    dropdowns in the table-mapping editor.

    Uses Connector.get_schemas_bulk (one batched metadata query per engine
    that supports it) rather than one query per table -- real incident: a
    99-table config took minutes to open because the old per-table loop paid
    a full network round-trip per table over a VPN/SSH-tunneled connection.

    An EARLIER version of this function fell back to that same per-table
    loop if the bulk query raised -- meant for "one bad table" cases, but a
    batched `WHERE table_name IN (...)` query can't actually fail that way
    (a missing/unreadable table just doesn't appear in the results, it
    doesn't raise). The only realistic way get_schemas_bulk raises is the
    CONNECTION itself being unreachable -- and retrying per-table then means
    up to `len(table_names)` more attempts against the same dead connection,
    each paying its own full connect_timeout. On a 99-table config with an
    unreachable host, that turned a single ~30s failure into a ~50-MINUTE
    one -- worse than the N+1 bug this was built to fix. No retry loop, so a
    dead connection fails once, fast, same as the connector-construction
    failure below.

    Tables that fail to introspect (dropped table, no read privilege, ...)
    map to `[]` rather than raising, so one bad table doesn't blank the page
    — and so does the connection itself failing outright (e.g. VPN down):
    every requested table just maps to `[]`, which the template treats as
    "columns unknown, render a plain text input" rather than a hard error."""
    try:
        connector = create_connector(connections_service.to_connection_params(conn))
    except Exception:
        return {name: [] for name in table_names}

    try:
        try:
            return connector.get_schemas_bulk(conn.database, table_names)
        except Exception:
            return {name: [] for name in table_names}
    finally:
        connector.close()


def _blank_suggestion(
    source_table: str, target_table: str, match_rule: str, key_columns: list[str],
    key_source: str = "",
) -> dict:
    """Common shape for a not-yet-saved table-mapping row, whether it came
    from name-matching, DDL key discovery, or copying from another config —
    config_detail.html's suggestion rows render all of these identically.

    `key_source` says WHERE key_columns came from (source PK / target sorting
    key / fallback) — shown directly in the UI (see column_cells' caption in
    config_detail.html) so a wrong/fallback key is immediately visible
    instead of silently looking the same as a genuinely-detected one."""
    return {
        "source_table": source_table, "target_table": target_table, "match_rule": match_rule,
        "key_columns": key_columns, "key_source": key_source, "chunk_column": "", "date_column": "",
        "exclude_columns": [], "mode_override": "",
    }


def suggest_mappings(source_conn: models.Connection, target_conn: models.Connection, prefix: str = "") -> list[dict]:
    """Pair up source/target tables by prefix rule or identical name, and
    auto-fill `key_columns` (including composite keys) from the source
    table's PRIMARY KEY, falling back to the target's sorting key
    (ClickHouse) if the source has none — see Connector.get_primary_key().

    e.g. prefix="raw_" maps source `ws_orders` -> target `raw_ws_orders`.
    Falls back to identical-name matching for anything the prefix rule misses.
    """
    src_tables = list_tables(source_conn)
    tgt_tables = set(list_tables(target_conn))

    suggestions = []
    for src in src_tables:
        candidate = f"{prefix}{src}"
        if candidate in tgt_tables:
            matched_target = candidate
            match_rule = f"prefix:{prefix}" if prefix else "identical"
        elif src in tgt_tables:
            matched_target = src
            match_rule = "identical"
        else:
            suggestions.append(_blank_suggestion(src, "", "unmatched", ["id"], "belum ada target"))
            continue

        key_columns = get_primary_key(source_conn, src)
        if key_columns:
            key_source = f"PK/sorting key dari source ({source_conn.engine})"
        else:
            key_columns = get_primary_key(target_conn, matched_target)
            if key_columns:
                key_source = f"sorting key dari target ({target_conn.engine}) — source tidak terdeteksi"
            else:
                key_columns = ["id"]
                key_source = "⚠ TIDAK terdeteksi di source maupun target — fallback ke 'id', cek manual"

        suggestions.append(_blank_suggestion(src, matched_target, match_rule, key_columns, key_source))
    return suggestions


def suggest_from_config(other_config: models.ValidationConfig, existing_source_tables: set[str]) -> list[dict]:
    """Build suggestion rows by copying table mappings from an existing
    config (same shape as suggest_mappings()'s output — config_detail.html
    renders both identically). Lets a new config reuse key/chunk/date/exclude
    settings already worked out for the same tables elsewhere instead of
    re-typing them. Tables already present in the CURRENT config are skipped."""
    return [
        {
            "source_table": t.source_table, "target_table": t.target_table,
            "match_rule": f"copied:{other_config.name}",
            "key_columns": list(t.key_columns or ["id"]),
            "key_source": f"disalin dari config \"{other_config.name}\"",
            "chunk_column": t.chunk_column or "",
            "date_column": t.date_column or "",
            "exclude_columns": list(t.exclude_columns or []),
            "mode_override": t.mode_override or "",
        }
        for t in other_config.tables if t.source_table not in existing_source_tables
    ]
