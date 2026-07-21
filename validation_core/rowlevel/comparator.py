"""Pure comparison functions for one id-range chunk.

Ported from `validation_database/running_validation.py`
(`compare_chunk_multi`, `_column_diff_mask`, `_composite_key`,
`detect_value_columns`, `build_minmax_query`, `build_range_query_multi`).
The chunked-by-id legacy path is the only one ported — the single-column
legacy fetch-everything path (`validate_data_integer/string/date`,
`compare_chunk`, `build_range_query`) was dead weight for tables over a few
hundred thousand rows and is dropped (see
docs/validation-platform/01-analisa-existing.md §7).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype, is_datetime64_any_dtype, is_integer_dtype

from ..categories import META_COLUMNS
from ..connectors.base import Connector, Dialect

# Defaults mirror RunSettings.numeric_rel_tolerance / numeric_abs_tolerance —
# duplicated here as literal defaults so column_diff_mask stays usable
# standalone (e.g. from tests) without needing a RunSettings instance.
DEFAULT_REL_TOL = 1e-6
DEFAULT_ABS_TOL = 1e-9


def composite_key(df: pd.DataFrame, key_columns: list[str]) -> pd.Series:
    """Build a single string key from one or more key columns (joined by '_')."""
    if len(key_columns) == 1:
        return df[key_columns[0]].astype(str)
    return df[list(key_columns)].agg(lambda row: "_".join(str(x) for x in row), axis=1)


def column_diff_mask(
    a: pd.Series, b: pd.Series, threshold: float,
    rel_tol: float = DEFAULT_REL_TOL, abs_tol: float = DEFAULT_ABS_TOL,
) -> pd.Series:
    """Type-aware 'values differ' mask for two aligned Series, treating
    (NaN, NaN) / (None, None) as equal. Numeric vs numeric -> tolerant numeric
    compare; datetime -> datetime compare; otherwise string compare.

    `threshold` is accepted for interface symmetry with the legacy fuzzy-match
    path but unused here — exact string compare after alignment, matching
    compare_chunk_multi's behavior in the original (fuzzy matching was only
    ever wired into the abandoned single-column legacy path).

    Numeric tolerance (`rel_tol`/`abs_tol`, `math.isclose`-style: equal when
    `|a-b| <= max(rel_tol * max(|a|,|b|), abs_tol)`) exists because row-level
    comparison fetches raw values straight from each engine's driver, and two
    engines can serialize the *same* underlying float with different
    precision (e.g. a DECIMAL/DOUBLE column read back as `482.437346437` from
    one driver and `482.43734643734643` from another) — without tolerance
    every row of a wide float column shows up as a "diff", drowning out real
    ones. This is engine-agnostic: it applies to any two Series regardless of
    which connectors fetched them (MySQL vs ClickHouse, ClickHouse vs
    ClickHouse mart-to-mart, etc).

    Genuine integer columns (`int64`/`uint64` dtype with no NULLs — pandas
    can't represent NaN in an integer dtype, so a NULL-free integer Series
    really is a column of exact counts/ids/quantities, not a floating
    measurement) are compared EXACTLY, no tolerance — otherwise a real
    off-by-one on a large id/count could be swallowed by a relative
    tolerance calibrated for float noise.
    """
    if is_numeric_dtype(a) and is_numeric_dtype(b):
        an, bn = pd.to_numeric(a, errors="coerce"), pd.to_numeric(b, errors="coerce")
        both_present = ~(an.isna() & bn.isna())

        if is_integer_dtype(an) and is_integer_dtype(bn):
            return (an != bn) & both_present

        max_abs = np.fmax(an.abs().to_numpy(), bn.abs().to_numpy())
        tol = np.maximum(max_abs * rel_tol, abs_tol)
        diff = (an - bn).abs().to_numpy()
        with np.errstate(invalid="ignore"):
            is_close = diff <= tol
        return pd.Series(~is_close, index=a.index) & both_present
    if is_datetime64_any_dtype(a) or is_datetime64_any_dtype(b):
        an, bn = pd.to_datetime(a, errors="coerce"), pd.to_datetime(b, errors="coerce")
        return (an != bn) & ~(an.isna() & bn.isna())
    an, bn = a.astype("string"), b.astype("string")
    return (an != bn) & ~(an.isna() & bn.isna())


def detect_value_columns(
    source: Connector,
    source_table_ref: str,
    target: Connector,
    target_table_ref: str,
    key_columns: list[str],
    exclude_columns: list[str] | None = None,
) -> list[str]:
    """Columns present in BOTH tables, excluding key column(s) and pipeline
    meta columns. Order follows the source table."""
    src_cols = list(source.query_df(f"SELECT * FROM {source_table_ref} LIMIT 1").columns)
    tgt_cols = set(target.query_df(f"SELECT * FROM {target_table_ref} LIMIT 1").columns)
    excludes = set(META_COLUMNS) | set(key_columns) | set(exclude_columns or [])
    return [c for c in src_cols if c in tgt_cols and c not in excludes]


def build_minmax_query(dialect: Dialect, chunk_column: str, table_ref: str) -> str:
    """MIN/MAX of the numeric chunk column + total row count, used to derive
    chunk boundaries. The count feeds density-aware chunk sizing (see
    RowLevelValidator.run): id-RANGE chunking alone collapses to one giant
    chunk on composite-key tables where the id range is tiny but each id
    has many rows."""
    c = dialect.quote_ident(chunk_column)
    return f"SELECT MIN({c}) AS lo, MAX({c}) AS hi, COUNT(*) AS n FROM {table_ref}"


def build_range_query_multi(
    dialect: Dialect,
    key_columns: list[str],
    value_columns: list[str],
    table_ref: str,
    chunk_column: str,
    lo: int | None,
    hi: int | None,
) -> str:
    """Fetch key + value columns for one id range. `lo is None` -> full-table
    scan (used when there's no numeric column to chunk on)."""
    cols = list(key_columns) + list(value_columns)
    sel = ", ".join(dialect.quote_ident(c) for c in cols)
    if lo is None:
        return f"SELECT {sel} FROM {table_ref}"
    c = dialect.quote_ident(chunk_column)
    return f"SELECT {sel} FROM {table_ref} WHERE {c} BETWEEN {lo} AND {hi}"


def compare_chunk_multi(
    first_df: pd.DataFrame,
    second_df: pd.DataFrame,
    key_columns: list[str],
    value_columns: list[str],
    mode: str,
    threshold: float,
    rel_tol: float = DEFAULT_REL_TOL,
    abs_tol: float = DEFAULT_ABS_TOL,
) -> tuple[list[str], list[str], list[dict]]:
    """Compare one chunk. first_df = source rows, second_df = target rows.

    mode='missing' -> only missing-key detection (value_columns may be empty).
    mode='full'    -> missing-key detection + per-column value differences.

    Returns (missing_in_source, missing_in_target, differing_records):
      missing_in_source: keys the TARGET has that the SOURCE doesn't.
      missing_in_target: keys the SOURCE has that the TARGET doesn't.
      differing_records: long-format dicts {key, column, source_value, target_value}.
    """
    expected_cols = list(key_columns) + list(value_columns)

    def _normalize(df):
        if df.shape[1] != len(expected_cols):
            return pd.DataFrame(columns=expected_cols)
        df = df.copy()
        df.columns = expected_cols
        return df

    first_df = _normalize(first_df)
    second_df = _normalize(second_df)
    first_df["__key"] = composite_key(first_df, key_columns)
    second_df["__key"] = composite_key(second_df, key_columns)

    s1 = set(first_df["__key"])
    s2 = set(second_df["__key"])
    missing_in_target = list(s1 - s2)  # source has, target doesn't
    missing_in_source = list(s2 - s1)  # target has, source doesn't

    if mode == "missing" or not value_columns:
        return missing_in_source, missing_in_target, []

    f = first_df.drop_duplicates("__key", keep="last")
    s = second_df.drop_duplicates("__key", keep="last")
    merged = pd.merge(
        f[["__key"] + list(value_columns)],
        s[["__key"] + list(value_columns)],
        on="__key", how="inner", suffixes=("__s", "__t"),
    )
    if merged.empty:
        return missing_in_source, missing_in_target, []

    records = []
    for col in value_columns:
        a, b = merged[f"{col}__s"], merged[f"{col}__t"]
        mask = column_diff_mask(a, b, threshold, rel_tol, abs_tol)
        if mask.any():
            sub = merged.loc[mask, ["__key", f"{col}__s", f"{col}__t"]]
            part = pd.DataFrame({
                "key": sub["__key"].values,
                "column": col,
                "source_value": sub[f"{col}__s"].values,
                "target_value": sub[f"{col}__t"].values,
            })
            records.extend(part.to_dict("records"))
    return missing_in_source, missing_in_target, records
