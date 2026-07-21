"""Shared dataclasses used across aggregate/rowlevel/runner modules."""
from __future__ import annotations

from dataclasses import dataclass, field

from .categories import META_COLUMNS


@dataclass
class TableSpec:
    """One row of `config_tables` — a source/target table pair to validate."""

    source_table: str
    target_table: str
    key_columns: list[str] = field(default_factory=lambda: ["id"])
    chunk_column: str | None = None  # default: key_columns[0]
    date_column: str | None = None  # for aggregate period breakdown
    exclude_columns: list[str] = field(default_factory=list)
    mode_override: str | None = None  # aggregate | rowlevel_missing | rowlevel_full | tiered
    start_date: str | None = None
    end_date: str | None = None
    enabled: bool = True
    note: str = ""

    def effective_chunk_column(self) -> str:
        return self.chunk_column or self.key_columns[0]


@dataclass
class RunSettings:
    """Tunable defaults — see docs/validation-platform/03-arsitektur.md §6."""

    mode: str = "tiered"
    meta_columns: frozenset = field(default_factory=lambda: META_COLUMNS)
    id_chunk_size: int = 2_000_000
    # Density-aware cap for row-level chunking: id_chunk_size slices by id
    # RANGE, which silently degrades to "the whole table in one chunk" on
    # composite-key tables whose chunk-column range is small but dense
    # (real incident: datamart_logger_monitoring, id range 0-22827 x many
    # rows per id x 105 columns = a single ~2h fetch that eventually broke
    # the HTTP stream). The chunk id-range is shrunk so one chunk carries
    # roughly this many ROWS instead. Never grows chunks beyond
    # id_chunk_size; only shrinks.
    rowlevel_target_chunk_rows: int = 500_000
    full_mode_row_threshold: int = 5_000_000
    stat_ref_date: str = "2010-01-15"
    table_concurrency: int = 4
    skip_period_breakdown: bool = False
    fuzzy_threshold: float = 1.0
    # Row-level numeric value-diff tolerance (math.isclose-style: two values
    # are treated as equal when |a-b| <= max(rel_tol*max(|a|,|b|), abs_tol)).
    # Absorbs cross-engine float serialization noise (e.g. a DECIMAL/DOUBLE
    # column read back as 482.437346437 from one driver and
    # 482.43734643734643 from another) without hiding genuine differences.
    # Applies to any numeric column pair regardless of which two engines are
    # being compared — see rowlevel/comparator.py::column_diff_mask. Does NOT
    # apply to integer-typed columns (compared exactly — see that function's
    # docstring for why).
    numeric_rel_tolerance: float = 1e-6
    numeric_abs_tolerance: float = 1e-9
    retry_max: int = 3
    retry_backoff_seconds: int = 20
    heartbeat_seconds: int = 30
    rowlevel_sample_cap: int = 10_000


VALID_MODES = ("aggregate", "rowlevel_missing", "rowlevel_full", "tiered")
