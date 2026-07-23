"""Column type categorization and cross-engine value comparison helpers.

Ported from `validation-data/scripts/db_validator.py`. This is the single
source of truth for "are these two metric values equal" — used by the
aggregate validator's Report 3 (stat) and Report 4/5 (period breakdown), so a
precision-tolerance fix made in one place can't silently miss the other.
"""
import math
import re

# Superset of the two legacy tools' pipeline/meta column sets:
#   validation-data:     PIPELINE_COLS = {'ingested_at', 'version'}
#   validation_database:  CLICKHOUSE_META_COLUMNS = {'ingested_at', 'version',
#                                                     '_dlt_load_id', '_dlt_id'}
# Neither tool excluded the dlt-internal columns from the OTHER tool's checks —
# this was a real divergence (see docs/validation-platform/01-analisa-existing.md
# section 5). Unifying it here means a column can't slip through one report but
# not the other.
META_COLUMNS = frozenset({'ingested_at', 'version', '_dlt_load_id', '_dlt_id'})

_CATEGORIES = {
    'numeric': [
        'int', 'integer', 'tinyint', 'smallint', 'mediumint', 'bigint',
        'float', 'double', 'decimal', 'real', 'numeric',
        'int8', 'int16', 'int32', 'int64',
        'uint8', 'uint16', 'uint32', 'uint64',
        'float32', 'float64',
        # Fix for known issue #1 (validation-data CLAUDE.md): MySQL YEAR type
        # has no entry here in the legacy tool, so it fell back to 'string'
        # while ClickHouse stores the mirrored column as UInt16 (numeric) —
        # `_shared_stat_cols` then required equal categories on both sides,
        # so shared metric building was skipped and, when it wasn't skipped
        # (mismatched category slipping through), `length(UInt16)` raised in
        # ClickHouse. Declaring 'year' numeric here fixes it at the source.
        'year',
        # Oracle's NUMBER covers both integers and decimals (no separate
        # int/float types) -- without this it fell through to the 'string'
        # fallback below and every stat/period comparison involving an
        # Oracle numeric column got skipped (category mismatch vs the
        # other side's real numeric type).
        'number',
    ],
    'string': [
        'varchar', 'char', 'text', 'tinytext', 'mediumtext',
        'longtext', 'string', 'fixedstring', 'enum',
    ],
    # 'date32' is ClickHouse's wider-range Date32 (1900-01-01..2299-12-31,
    # vs plain Date's 1970-01-01..2149-06-06) -- without it here, a Date32
    # column falls through every keyword list to the 'string' fallback,
    # which would silently skip it from all date-specific handling below
    # (floor-1970 / ClickHouse max-date ceiling, see connectors/clickhouse.py).
    'date': ['date', 'date32'],
    'timestamp': ['datetime', 'timestamp', 'datetime64'],
    'boolean': ['boolean', 'bool'],
}


def get_category(col_type: str) -> str:
    """Map a raw DB column type string to a comparison category.

    Handles ClickHouse `Nullable(...)` (recursively stripped) and `Array(...)`
    prefixes. Falls back to 'string' for anything unrecognized.
    """
    t = col_type.lower().strip()
    if t.startswith('array'):
        return 'array'
    if t.startswith('nullable('):
        return get_category(t[9:-1])
    for cat, keywords in _CATEGORIES.items():
        for kw in keywords:
            if t == kw or t.startswith(kw + '(') or t.startswith(kw + ' '):
                return cat
    return 'string'


def date_ceiling_bounds(source_dialect, target_dialect, source_type, target_type) -> tuple[str | None, str | None]:
    """Resolve the tightest ClickHouse-storable ceiling for a date/timestamp
    column pairing, split by category ('date' bound, 'timestamp' bound) so
    each side's expression can be capped with a literal matching ITS OWN
    category. Only whichever side(s) are ClickHouse contribute a bound (see
    Dialect.date_max_bound); both entries are None when neither side is
    ClickHouse — nothing to mirror, since no other engine here enforces a
    comparable ceiling.

    Shared by AggregateValidator (Report 2/3, MIN/MAX/stat columns) and
    RowLevelValidator (chunk fetches) so both apply the identical clamp to
    the identical column — a value ClickHouse silently truncated on ingest
    must be capped the same way on BOTH sides, or a genuinely-later value
    on the other engine would show as a false mismatch/diff. Also what
    keeps a ClickHouse DateTime64(9) row at its own storage max (2299-12-31)
    from ever reaching pandas as a raw value: pandas' datetime64[ns] only
    goes up to ~2262-04-11, and clickhouse_connect's default read format
    crashes the whole query if it tries to convert an out-of-range value
    (real incident, see connectors/clickhouse.py's overflow-safe query_df
    fallback -- this ceiling is the fix that keeps that fallback from ever
    needing to trigger in the first place).
    """
    date_bound: str | None = None
    ts_bound: str | None = None
    for dialect, col_type in ((source_dialect, source_type), (target_dialect, target_type)):
        # pd.isna FIRST, before any truthiness test: a one-sided column comes
        # through a merged schema DataFrame as a missing value whose exact
        # type depends on that frame's dtype -- np.nan (float) OR pd.NA
        # (e.g. arrow/string-backed frames). `not pd.NA` raises "boolean
        # value of NA is ambiguous" (real incident: datamart_orders_smdv).
        import pandas as pd  # local import: keep this module importable without pandas at parse time

        if col_type is None or pd.isna(col_type):
            continue
        col_type = str(col_type)
        if not col_type:
            continue
        bound = dialect.date_max_bound(col_type)
        if not bound:
            continue
        cat = get_category(col_type)
        if cat == "date":
            date_bound = bound if date_bound is None else min(date_bound, bound)
        elif cat == "timestamp":
            ts_bound = bound if ts_bound is None else min(ts_bound, bound)
    return date_bound, ts_bound


def ceil_stat(v) -> int:
    """Round a stat value up to an integer before comparing.

    MySQL and ClickHouse return different precision for the same data (e.g.
    MySQL AVG gives 4 decimals, ClickHouse full precision), so raw float
    comparison flags false mismatches. The 1e-9 nudge keeps float noise like
    4.0000000000001 from ceiling up to 5.
    """
    return math.ceil(float(v) - 1e-9)


def values_match(sv, tv) -> bool:
    """True if two report metric values should be treated as equal.

    Numeric values are compared after ceiling-rounding (ceil_stat). Values
    that aren't numeric (e.g. datetime strings, where MySQL returns
    '2025-08-14 00:38:00' and ClickHouse toString() returns
    '...00:38:00.000') are compared after stripping trailing '.0+' and
    surrounding whitespace.
    """
    import pandas as pd  # local import: keep this module importable without pandas at parse time

    if pd.isna(sv) and pd.isna(tv):
        return True
    try:
        sv_f, tv_f = float(sv), float(tv)
        if math.isnan(sv_f) or math.isnan(tv_f):
            return True  # one side NaN post-cast — treat as skip, not a mismatch
        return ceil_stat(sv_f) == ceil_stat(tv_f)
    except (TypeError, ValueError):
        def _norm(v):
            return re.sub(r'\.0+$', '', str(v).strip())
        return _norm(sv) == _norm(tv)
