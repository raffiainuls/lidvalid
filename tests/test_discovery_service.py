"""Coverage for app/services/discovery_service.py -- the column/key
discovery layer backing the config-builder's dropdown fields (key_columns,
chunk_column, date_column, exclude_columns). Uses plain `models.Connection`
objects (never persisted to a DB session -- these functions only read
attributes off them) against real local SQLite files, so no app server or
SQLAlchemy session is needed.
"""
import sqlite3
import sys

sys.path.insert(0, r"d:\Project\lidvalid")

from app import models
from app.services import discovery_service


def _sqlite_connection(path) -> models.Connection:
    return models.Connection(engine="sqlite", database=str(path))


def test_list_columns_returns_name_type_category(tmp_path):
    path = tmp_path / "t.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE orders (id INTEGER, amount REAL, created_at TEXT)")
    conn.commit(); conn.close()

    cols = discovery_service.list_columns(_sqlite_connection(path), "orders")
    names = [c["name"] for c in cols]
    assert names == ["id", "amount", "created_at"]
    assert all({"name", "type", "category"} <= c.keys() for c in cols)


def test_list_columns_on_missing_table_returns_empty_not_a_crash(tmp_path):
    # Regression: get_schema() on a nonexistent table returns an EMPTY
    # DataFrame (no columns at all), and the old code did
    # `df.columns[-1]` unconditionally -- IndexError on an empty Index,
    # surfacing as a confusing "index -1 is out of bounds" error instead of
    # a clean "no columns found".
    path = tmp_path / "t.sqlite"
    sqlite3.connect(path).close()  # empty db, no tables at all

    cols = discovery_service.list_columns(_sqlite_connection(path), "does_not_exist")
    assert cols == []


def test_columns_by_table_batches_multiple_tables_one_connection(tmp_path):
    path = tmp_path / "t.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE a (id INTEGER, x TEXT)")
    conn.execute("CREATE TABLE b (id INTEGER, y REAL, z TEXT)")
    conn.commit(); conn.close()

    result = discovery_service.columns_by_table(_sqlite_connection(path), ["a", "b", "a"])  # dup on purpose
    assert result["a"] == ["id", "x"]
    assert result["b"] == ["id", "y", "z"]
    assert len(result) == 2  # duplicate "a" request didn't re-fetch or duplicate the key


def test_columns_by_table_missing_table_maps_to_empty_list(tmp_path):
    path = tmp_path / "t.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE a (id INTEGER)")
    conn.commit(); conn.close()

    result = discovery_service.columns_by_table(_sqlite_connection(path), ["a", "nope"])
    assert result["a"] == ["id"]
    assert result["nope"] == []


def test_columns_by_table_bad_connection_degrades_to_all_empty(tmp_path):
    # A database file path that doesn't exist yet: sqlite3.connect() creates
    # it lazily and succeeds, so simulate a genuinely broken connection with
    # an unsupported engine name instead -- create_connector() raises there.
    bad_conn = models.Connection(engine="not-a-real-engine", database="whatever")
    result = discovery_service.columns_by_table(bad_conn, ["a", "b"])
    assert result == {"a": [], "b": []}
