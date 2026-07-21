"""Chunked-by-id row-level validator — generalized port of the `chunk_by_id`
path in `validation_database/running_validation.py::main()`.

Chunking is done on a numeric key column (`chunk_column`), not on a date
column: an id always falls in the same range on both sides regardless of its
`created_at`, so missing-key detection stays correct across ALL periods while
memory stays bounded to one chunk at a time. Falls back to a single
full-table scan when the chunk column isn't numeric.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ..connectors.base import Connector
from ..models import RunSettings, TableSpec
from .comparator import (
    build_minmax_query,
    build_range_query_multi,
    compare_chunk_multi,
    detect_value_columns,
)
from ..events import ProgressEvent, OnEvent, noop_on_event

# Hard safety valve: total differing-value records kept in memory across all
# chunks. The legacy tool had a real OOM incident on a wide table in `full`
# mode (`realloc of size 4294967296 failed` — see
# docs/validation-platform/01-analisa-existing.md §3.5). Counts keep
# accumulating past this cap (so totals stay accurate); only the detail
# records stop being retained. The web layer is expected to persist a sample
# well under this cap anyway (RunSettings.rowlevel_sample_cap) and point to a
# full export for anything beyond it.
MAX_DIFF_RECORDS_IN_MEMORY = 200_000


@dataclass
class RowLevelResult:
    mode: str
    key_columns: list[str]
    chunk_column: str
    value_columns: list[str]
    total_chunks: int
    missing_in_source: list[str]
    missing_in_target: list[str]
    differing_values: list[dict]
    missing_in_source_count: int
    missing_in_target_count: int
    differing_values_count: int
    truncated: bool = False
    queries: dict[str, str] = field(default_factory=dict)


class RowLevelValidator:
    def __init__(
        self,
        source: Connector,
        target: Connector,
        source_table_ref: str,
        target_table_ref: str,
        table: TableSpec,
        mode: str,
        settings: RunSettings | None = None,
        on_event: OnEvent = noop_on_event,
    ):
        self.source = source
        self.target = target
        self.source_table_ref = source_table_ref
        self.target_table_ref = target_table_ref
        self.table = table
        self.mode = "missing" if mode not in ("missing", "full") else mode
        self.settings = settings or RunSettings()
        self.on_event = on_event
        self.queries: dict[str, str] = {}

    def _emit(self, kind: str, message: str, **data) -> None:
        self.on_event(ProgressEvent(kind=kind, message=message, data=data))

    def run(self) -> RowLevelResult:
        key_columns = list(self.table.key_columns)
        chunk_column = self.table.effective_chunk_column()
        chunk_size = int(self.settings.id_chunk_size)

        value_columns: list[str] = []
        if self.mode == "full":
            self._emit("phase", f"{self.table.source_table}: detecting common columns")
            value_columns = detect_value_columns(
                self.source, self.source_table_ref,
                self.target, self.target_table_ref,
                key_columns, self.table.exclude_columns,
            )

        scope = "missing-key only" if self.mode == "missing" else f"{len(value_columns)} columns"
        self._emit(
            "phase",
            f"Validation mode: {self.mode} | key={key_columns} | chunk_column={chunk_column} ({scope})",
            value_columns=value_columns,
        )

        full_scan = False
        full_scan_reason = "not numeric"
        gmin = gmax = None
        total_rows = 0
        try:
            self._emit("phase", f"computing {chunk_column} MIN/MAX range")
            mm1_q = build_minmax_query(self.source.dialect, chunk_column, self.source_table_ref)
            mm2_q = build_minmax_query(self.target.dialect, chunk_column, self.target_table_ref)
            self.queries["MinMax Source"] = mm1_q
            self.queries["MinMax Target"] = mm2_q
            mm1 = self.source.query_df(mm1_q)
            mm2 = self.target.query_df(mm2_q)
            bounds = (mm1.iloc[0, 0], mm1.iloc[0, 1], mm2.iloc[0, 0], mm2.iloc[0, 1])
            # STRING columns holding digit-only values pass int() just fine,
            # which used to fool this into treating them as numeric -- the
            # range queries then compare a String column against integer
            # literals, which MySQL silently coerces but ClickHouse rejects
            # with NO_COMMON_TYPE (real incident:
            # datamart_wms_report_waste_bags, waste_bag_id String). And even
            # where it "works", MIN/MAX of a string column is LEXICOGRAPHIC
            # -- the derived id range is meaningless (that same incident
            # computed 499,912 chunks). The dtype the driver hands back
            # tells the truth about the column type: str => not numeric.
            if any(isinstance(v, (str, bytes)) for v in bounds):
                full_scan_reason = "bertipe string di source/target (nilai digit-only tetap BUKAN kolom numerik)"
                raise TypeError("string-typed chunk column")
            gmin = int(min(bounds[0], bounds[2]))
            gmax = int(max(bounds[1], bounds[3]))
            total_rows = int(max(mm1.iloc[0, 2], mm2.iloc[0, 2]))
        except (ValueError, TypeError):
            full_scan = True

        if full_scan:
            chunk_bounds = [(None, None)]
            self._emit("phase", f"chunk_column '{chunk_column}' {full_scan_reason} -> single full-table scan")
        else:
            # Density-aware sizing: id_chunk_size slices by id RANGE, which
            # assumes ~1 row per id (auto-increment PKs). Composite-key
            # tables break that assumption -- a tiny id range can carry the
            # WHOLE table (real incident: datamart_logger_monitoring, range
            # 0-22827, 105 columns -> ONE chunk, a ~2h fetch that broke the
            # HTTP stream mid-transfer). Shrink the id-range per chunk so a
            # chunk carries roughly rowlevel_target_chunk_rows rows. Only
            # ever shrinks (min with id_chunk_size): sparse/normal tables
            # keep the legacy behavior exactly.
            id_span = gmax - gmin + 1
            if total_rows > 0 and id_span > 0:
                rows_per_id = total_rows / id_span
                # > 1.5, not > 0: ordinary auto-increment tables sit at ~1
                # row per id (or below, with deleted gaps) and keep the
                # legacy sizing exactly -- only genuinely dense composite-key
                # shapes trigger the shrink.
                if rows_per_id > 1.5:
                    density_size = max(1, int(self.settings.rowlevel_target_chunk_rows / rows_per_id))
                    if density_size < chunk_size:
                        self._emit(
                            "phase",
                            f"dense chunk column: ~{rows_per_id:.1f} rows/id over range [{gmin}, {gmax}] "
                            f"-> chunk_size {chunk_size} shrunk to {density_size} "
                            f"(~{self.settings.rowlevel_target_chunk_rows} rows/chunk)",
                        )
                        chunk_size = density_size
            chunk_bounds = [(lo, lo + chunk_size - 1) for lo in range(gmin, gmax + 1, chunk_size)]
            self._emit(
                "phase",
                f"{chunk_column} range [{gmin}, {gmax}], chunk_size={chunk_size}, total_chunks={len(chunk_bounds)}",
            )

        total_chunks = len(chunk_bounds)
        all_missing_source: list[str] = []
        all_missing_target: list[str] = []
        all_diffs: list[dict] = []
        missing_source_count = missing_target_count = diff_count = 0
        truncated = False

        for idx, (lo, hi) in enumerate(chunk_bounds, start=1):
            rng = "full-scan" if lo is None else f"{chunk_column}[{lo}-{hi}]"
            q1 = build_range_query_multi(self.source.dialect, key_columns, value_columns, self.source_table_ref, chunk_column, lo, hi)
            q2 = build_range_query_multi(self.target.dialect, key_columns, value_columns, self.target_table_ref, chunk_column, lo, hi)
            self.queries[f"Chunk {idx} Source"] = q1
            self.queries[f"Chunk {idx} Target"] = q2

            self._emit("phase", f"chunk {idx}/{total_chunks} {rng}: fetching", chunk=idx, total=total_chunks)
            t0 = time.monotonic()
            with ThreadPoolExecutor(max_workers=2) as ex:
                fu1 = ex.submit(self.source.query_df, q1)
                fu2 = ex.submit(self.target.query_df, q2)
                d1 = fu1.result()
                d2 = fu2.result()

            self._emit(
                "phase",
                f"chunk {idx}/{total_chunks} {rng}: comparing (source={len(d1)} target={len(d2)} rows)",
                chunk=idx, total=total_chunks,
            )
            m_src, m_tgt, recs = compare_chunk_multi(
                d1, d2, key_columns, value_columns, self.mode, self.settings.fuzzy_threshold,
                self.settings.numeric_rel_tolerance, self.settings.numeric_abs_tolerance,
            )

            missing_source_count += len(m_src)
            missing_target_count += len(m_tgt)
            diff_count += len(recs)
            if len(all_missing_source) < MAX_DIFF_RECORDS_IN_MEMORY:
                all_missing_source.extend(m_src)
            if len(all_missing_target) < MAX_DIFF_RECORDS_IN_MEMORY:
                all_missing_target.extend(m_tgt)
            if len(all_diffs) < MAX_DIFF_RECORDS_IN_MEMORY:
                all_diffs.extend(recs)
            else:
                truncated = True

            elapsed = round(time.monotonic() - t0, 2)
            self._emit(
                "checkpoint",
                f"chunk {idx}/{total_chunks} {rng} rows source={len(d1)} target={len(d2)} | "
                f"this chunk: missing_source+={len(m_src)} missing_target+={len(m_tgt)} diff+={len(recs)} | "
                f"running totals: missing_source={missing_source_count} missing_target={missing_target_count} diff={diff_count}",
                chunk=idx, total=total_chunks, elapsed_s=elapsed,
                missing_source_total=missing_source_count,
                missing_target_total=missing_target_count,
                diff_total=diff_count,
            )

        return RowLevelResult(
            mode=self.mode,
            key_columns=key_columns,
            chunk_column=chunk_column,
            value_columns=value_columns,
            total_chunks=total_chunks,
            missing_in_source=all_missing_source,
            missing_in_target=all_missing_target,
            differing_values=all_diffs,
            missing_in_source_count=missing_source_count,
            missing_in_target_count=missing_target_count,
            differing_values_count=diff_count,
            truncated=truncated,
            queries=dict(self.queries),
        )
