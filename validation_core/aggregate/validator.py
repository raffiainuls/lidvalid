"""Aggregate/statistical validator — generalized port of
`validation-data/scripts/db_validator.py::DBValidator`.

The original hardcoded "source is mysql-or-clickhouse, target is always
clickhouse". This version takes any two `Connector`s (each carrying its own
`Dialect`), so the same five reports work for MySQL<->ClickHouse,
ClickHouse<->ClickHouse (mart layer), or ClickHouse<->SQLite (local demo/test)
without branching. All edge-case fixes from the original are preserved via
the Dialect methods they were generalized into — see
docs/validation-platform/01-analisa-existing.md §2.3 for the full list and
each dialect method's docstring for the specific fix it encodes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from ..categories import get_category, values_match, date_ceiling_bounds, META_COLUMNS
from ..connectors.base import Connector
from ..models import RunSettings


@dataclass
class AggregateResult:
    table_details: dict
    column_details: pd.DataFrame
    src_type_details: pd.DataFrame
    tgt_type_details: pd.DataFrame
    monthly_breakdown: pd.DataFrame
    yearly_breakdown: pd.DataFrame
    investigate_query: str | None
    queries: dict[str, str] = field(default_factory=dict)

    # --- derived summary, mirrors run_batch.py's _build_summary / _calc_stat_mismatch ---
    def summarize(self) -> dict:
        r1 = self.table_details
        row_match = bool(r1["validate_total_row"])

        shared = self.column_details
        if "source_column_type" in shared.columns:
            shared = shared[
                shared["source_column_type"].notna() & shared["target_column_type"].notna()
            ]
        if "column_name" in shared.columns:
            shared = shared[~shared["column_name"].isin(META_COLUMNS)]
        col_completeness_mismatch = (
            int((shared["validate_completeness"] == False).sum())  # noqa: E712
            if "validate_completeness" in shared.columns else 0
        )
        col_uniqueness_mismatch = (
            int((shared["validate_uniqueness"] == False).sum())  # noqa: E712
            if "validate_uniqueness" in shared.columns else 0
        )

        stat_mismatch, stat_detail = _calc_stat_mismatch(self.src_type_details, self.tgt_type_details)

        monthly_mismatch = (
            int((self.monthly_breakdown["match"] == False).sum())  # noqa: E712
            if "match" in self.monthly_breakdown.columns else 0
        )
        yearly_mismatch = (
            int((self.yearly_breakdown["match"] == False).sum())  # noqa: E712
            if "match" in self.yearly_breakdown.columns else 0
        )

        overall_ok = (
            row_match
            and col_completeness_mismatch == 0
            and col_uniqueness_mismatch == 0
            and stat_mismatch == 0
            and monthly_mismatch == 0
            and yearly_mismatch == 0
        )

        return {
            "status": "PASS" if overall_ok else "FAIL",
            "source_rows": int(r1["source_total_row"]),
            "target_rows": int(r1["target_total_row"]),
            "row_diff": int(r1["source_total_row"]) - int(r1["target_total_row"]),
            "row_match": row_match,
            "source_cols": int(r1["source_total_column"]),
            "target_cols": int(r1["target_total_column"]),
            "extra_source_columns": r1["source_extra_column"],
            "extra_target_columns": r1["target_extra_column"],
            "col_completeness_mismatch": col_completeness_mismatch,
            "col_uniqueness_mismatch": col_uniqueness_mismatch,
            "stat_mismatch": stat_mismatch,
            "stat_mismatch_detail": stat_detail[:10],
            "monthly_mismatch": monthly_mismatch,
            "yearly_mismatch": yearly_mismatch,
        }


def _calc_stat_mismatch(src_df: pd.DataFrame, tgt_df: pd.DataFrame) -> tuple[int, list[str]]:
    """Compare Report-3 metrics for columns present in BOTH source and target,
    excluding pipeline/meta columns (expected to differ, not a failure)."""
    if src_df.empty or tgt_df.empty:
        return 0, []

    metric_cols = [c for c in src_df.columns if c not in ("column_name", "column_type", "category")]
    src_idx = src_df.set_index("column_name")
    tgt_idx = tgt_df.set_index("column_name")
    shared = [c for c in src_idx.index.intersection(tgt_idx.index) if c not in META_COLUMNS]

    mismatch_count = 0
    details = []
    for col in shared:
        for m in metric_cols:
            if m not in src_idx.columns or m not in tgt_idx.columns:
                continue
            sv, tv = src_idx.loc[col, m], tgt_idx.loc[col, m]
            if not values_match(sv, tv):
                mismatch_count += 1
                details.append(f"{col}:{m}({sv}→{tv})")
    return mismatch_count, details


class AggregateValidator:
    """Runs Report 1-5 between one source table and one target table."""

    def __init__(
        self,
        source: Connector,
        target: Connector,
        source_db: str,
        source_table: str,
        target_db: str,
        target_table: str,
        date_column: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        settings: RunSettings | None = None,
        on_query: Callable[[str, str], None] | None = None,
    ):
        self.source = source
        self.target = target
        self.source_db = source_db
        self.source_table = source_table
        self.target_db = target_db
        self.target_table = target_table
        self.source_table_path = f"{source_db}.{source_table}" if source_db else source_table
        self.target_table_path = f"{target_db}.{target_table}" if target_db else target_table
        self.date_column = date_column
        self.start_date = start_date
        self.end_date = end_date
        self.settings = settings or RunSettings()
        self._on_query = on_query or (lambda label, sql: None)
        self.queries: dict[str, str] = {}

        # Same-engine dialect pair (e.g. ClickHouse mart-vs-mart, or same server
        # queried twice) can be JOINed in a single investigate query; otherwise
        # the investigate text is split into one block per engine for the
        # human to run in each engine's own client.
        self._same_dialect = source.dialect.name == target.dialect.name

    # ------------------------------------------------------------------ utils
    def _log_query(self, label: str, sql: str) -> None:
        self.queries[label] = sql
        self._on_query(label, sql)

    def _table_ref(self, side: str) -> str:
        if side == "source":
            return self.source.dialect.table_ref(self.source_db, self.source_table, final=True)
        return self.target.dialect.table_ref(self.target_db, self.target_table, final=True)

    def _date_filter(self, side: str) -> str:
        if not (self.start_date and self.end_date):
            return ""
        dialect = self.source.dialect if side == "source" else self.target.dialect
        return dialect.date_range_filter(self.date_column, self.start_date, self.end_date)

    def _null_filter(self, side: str) -> str:
        if self.start_date and self.end_date:
            return self._date_filter(side)
        return f" WHERE {self.date_column} IS NOT NULL"

    def _run_source(self, sql: str) -> pd.DataFrame:
        return self.source.query_df(sql)

    def _run_target(self, sql: str) -> pd.DataFrame:
        return self.target.query_df(sql)

    def get_schema_source(self) -> pd.DataFrame:
        df = self.source.get_schema(self.source_db, self.source_table)
        return df.rename(columns={"column_type": "source_column_type"})

    def get_schema_target(self) -> pd.DataFrame:
        df = self.target.get_schema(self.target_db, self.target_table)
        return df.rename(columns={"column_type": "target_column_type"})

    # ------------------------------------------------------------- Report 1
    def gen_report_table_details(self, df: pd.DataFrame) -> dict:
        src_q = f"SELECT COUNT(*) AS total_row FROM {self._table_ref('source')}{self._date_filter('source')}"
        tgt_q = f"SELECT COUNT(*) AS total_row FROM {self._table_ref('target')}{self._date_filter('target')}"
        self._log_query("Table Details Source", src_q)
        self._log_query("Table Details Target", tgt_q)

        src_rows = int(self._run_source(src_q)["total_row"].iloc[0])
        tgt_rows = int(self._run_target(tgt_q)["total_row"].iloc[0])

        return {
            "source_table": self.source_table_path,
            "target_table": self.target_table_path,
            "source_total_column": int(df["source_column_type"].count()),
            "target_total_column": int(df["target_column_type"].count()),
            "source_total_row": src_rows,
            "target_total_row": tgt_rows,
            "validate_total_row": src_rows == tgt_rows,
            "source_extra_column": df[df["target_column_type"].isna()]["column_name"].tolist(),
            "target_extra_column": df[df["source_column_type"].isna()]["column_name"].tolist(),
        }

    # ------------------------------------------------------- shared: dates
    def _date_ceiling_bounds(self, source_type, target_type) -> tuple[str | None, str | None]:
        """Thin wrapper -- see categories.date_ceiling_bounds (shared with
        RowLevelValidator, which applies the identical clamp to chunk
        fetches, not just this class's MIN/MAX/stat columns)."""
        return date_ceiling_bounds(self.source.dialect, self.target.dialect, source_type, target_type)

    # ------------------------------------------------------------- Report 2
    def _completeness_exprs(self, dialect, col: str, col_type: str, ceiling_bound: str | None) -> list[str]:
        cat = get_category(col_type)
        q = dialect.quote_ident(col)
        if cat == "string":
            dist_expr = dialect.distinct_string_expr(q)
        elif cat in ("date", "timestamp"):
            dist_expr = dialect.date_ceiling(dialect.date_floor_1970(q, cat), cat, ceiling_bound)
        else:
            dist_expr = q
        return [
            f"{dialect.is_not_null_ratio(q)} AS {col}_completeness",
            f"{dialect.count_distinct(dist_expr)} AS {col}_uniqueness",
        ]

    def gen_report_column_details(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.set_index("column_name", drop=False)
        src_cols = df[df["source_column_type"].notna()]["column_name"].tolist()
        tgt_cols = df[df["target_column_type"].notna()]["column_name"].tolist()

        src_parts = []
        for col in src_cols:
            source_type = str(df.loc[col, "source_column_type"])
            date_bound, ts_bound = self._date_ceiling_bounds(source_type, df.loc[col, "target_column_type"])
            cat = get_category(source_type)
            bound = date_bound if cat == "date" else (ts_bound if cat == "timestamp" else None)
            src_parts += self._completeness_exprs(self.source.dialect, col, source_type, bound)
        tgt_parts = []
        for col in tgt_cols:
            target_type = str(df.loc[col, "target_column_type"])
            date_bound, ts_bound = self._date_ceiling_bounds(df.loc[col, "source_column_type"], target_type)
            cat = get_category(target_type)
            bound = date_bound if cat == "date" else (ts_bound if cat == "timestamp" else None)
            tgt_parts += self._completeness_exprs(self.target.dialect, col, target_type, bound)

        src_result, tgt_result = pd.DataFrame(), pd.DataFrame()
        if src_parts:
            src_q = f"SELECT {', '.join(src_parts)} FROM {self._table_ref('source')}{self._date_filter('source')}"
            self._log_query("Column Details Source", src_q)
            src_result = self._run_source(src_q)
        if tgt_parts:
            tgt_q = f"SELECT {', '.join(tgt_parts)} FROM {self._table_ref('target')}{self._date_filter('target')}"
            self._log_query("Column Details Target", tgt_q)
            tgt_result = self._run_target(tgt_q)

        df["source_completeness"] = None
        df["source_uniqueness"] = None
        df["target_completeness"] = None
        df["target_uniqueness"] = None

        for col in src_cols:
            try:
                df.loc[col, "source_completeness"] = round(float(src_result[f"{col}_completeness"].iloc[0]), 4)
                df.loc[col, "source_uniqueness"] = round(float(src_result[f"{col}_uniqueness"].iloc[0]), 4)
            except Exception:
                pass
        for col in tgt_cols:
            try:
                df.loc[col, "target_completeness"] = round(float(tgt_result[f"{col}_completeness"].iloc[0]), 4)
                df.loc[col, "target_uniqueness"] = round(float(tgt_result[f"{col}_uniqueness"].iloc[0]), 4)
            except Exception:
                pass

        # A raw == here is not NaN-safe: a column's completeness/uniqueness
        # ratio is COUNT(...)/COUNT(*), which is NaN on both sides for a
        # table that's empty on both source and target -- `nan == nan` is
        # `False` in Python, so every column would be miscoded as a
        # mismatch (fabricating one FindingAggregate row each) even though
        # "identically undefined on both sides" isn't a real discrepancy.
        # NOT values_match() here -- its ceil_stat rounding is meant for
        # cross-engine SUM/MIN/MAX precision noise on largish numbers, and
        # would collapse any two ratios in this column's 0..1 range to the
        # same integer (e.g. 0.5 and 1.0 both ceil to 1), silently hiding
        # real completeness/uniqueness differences. These ratios are already
        # rounded to 4dp above, so plain equality is correct once NaN-vs-NaN
        # is special-cased as a match.
        def _ratio_match(a, b) -> bool:
            a_na, b_na = pd.isna(a), pd.isna(b)
            if a_na and b_na:
                return True
            if a_na or b_na:
                return False
            return a == b

        df["validate_completeness"] = df.apply(
            lambda r: _ratio_match(r["source_completeness"], r["target_completeness"]), axis=1
        )
        df["validate_uniqueness"] = df.apply(
            lambda r: _ratio_match(r["source_uniqueness"], r["target_uniqueness"]), axis=1
        )
        return df.reset_index(drop=True)

    # ------------------------------------------------------------- Report 3
    def _col_metric_selects(self, dialect, col: str, col_type: str, ceiling_bound: str | None) -> list[str]:
        cat = get_category(col_type)
        q = dialect.quote_ident(col)
        parts = [f"COUNT({q}) AS {col}_count"]

        if cat == "numeric":
            parts += [f"SUM({q}) AS {col}_sum", f"MIN({q}) AS {col}_min", f"MAX({q}) AS {col}_max"]
        elif cat == "array":
            lf = dialect.len_fn()
            parts += [f"SUM({lf}({q})) AS {col}_sum", f"MIN({lf}({q})) AS {col}_min", f"MAX({lf}({q})) AS {col}_max"]
        elif cat in ("date", "timestamp"):
            dq = dialect.date_ceiling(dialect.date_floor_1970(q, cat), cat, ceiling_bound)
            parts.append(f"{dialect.wrap_minmax_datetime(f'MIN({dq})')} AS {col}_min")
            parts.append(f"{dialect.wrap_minmax_datetime(f'MAX({dq})')} AS {col}_max")
            parts.append(f"SUM({dialect.datediff_expr(dq, self.settings.stat_ref_date)}) AS {col}_datediff")
        elif cat == "string":
            dist_expr = dialect.distinct_string_expr(q)
            len_expr = f"{dialect.trim_fn()}({q})"
            lf = dialect.len_fn()
            parts += [
                f"COUNT(DISTINCT {dist_expr}) AS {col}_countd",
                f"MIN({lf}({len_expr})) AS {col}_len_min",
                f"MAX({lf}({len_expr})) AS {col}_len_max",
                f"AVG({lf}({len_expr})) AS {col}_len_avg",
            ]
        return parts

    @staticmethod
    def _metric_keys(col_type: str) -> list[str]:
        cat = get_category(col_type)
        keys = ["count"]
        if cat == "numeric" or cat == "array":
            keys += ["sum", "min", "max"]
        elif cat in ("date", "timestamp"):
            keys += ["min", "max", "datediff"]
        elif cat == "string":
            keys += ["countd", "len_min", "len_max", "len_avg"]
        return keys

    def gen_report_column_type_details(self, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        df = df.set_index("column_name", drop=False)
        src_cols_df = df[df["source_column_type"].notna()][["column_name", "source_column_type"]]
        tgt_cols_df = df[df["target_column_type"].notna()][["column_name", "target_column_type"]]

        src_parts = []
        for _, row in src_cols_df.iterrows():
            col, source_type = row["column_name"], row["source_column_type"]
            date_bound, ts_bound = self._date_ceiling_bounds(source_type, df.loc[col, "target_column_type"])
            cat = get_category(str(source_type))
            bound = date_bound if cat == "date" else (ts_bound if cat == "timestamp" else None)
            src_parts += self._col_metric_selects(self.source.dialect, col, source_type, bound)
        tgt_parts = []
        for _, row in tgt_cols_df.iterrows():
            col, target_type = row["column_name"], row["target_column_type"]
            date_bound, ts_bound = self._date_ceiling_bounds(df.loc[col, "source_column_type"], target_type)
            cat = get_category(str(target_type))
            bound = date_bound if cat == "date" else (ts_bound if cat == "timestamp" else None)
            tgt_parts += self._col_metric_selects(self.target.dialect, col, target_type, bound)

        src_result, tgt_result = pd.DataFrame(), pd.DataFrame()
        if src_parts:
            src_q = f"SELECT {', '.join(src_parts)} FROM {self._table_ref('source')}{self._date_filter('source')}"
            self._log_query("Column Type Details Source", src_q)
            src_result = self._run_source(src_q)
        if tgt_parts:
            tgt_q = f"SELECT {', '.join(tgt_parts)} FROM {self._table_ref('target')}{self._date_filter('target')}"
            self._log_query("Column Type Details Target", tgt_q)
            tgt_result = self._run_target(tgt_q)

        src_rows, tgt_rows = [], []
        src_names = src_cols_df["column_name"].tolist()
        tgt_names = tgt_cols_df["column_name"].tolist()

        for col in df["column_name"].tolist():
            if col in src_names:
                col_type = df.loc[col, "source_column_type"]
                row = {"column_name": col, "column_type": col_type, "category": get_category(col_type)}
                for k in self._metric_keys(col_type):
                    ck = f"{col}_{k}"
                    row[k] = src_result[ck].iloc[0] if ck in src_result.columns else None
                src_rows.append(row)
            if col in tgt_names:
                col_type = df.loc[col, "target_column_type"]
                row = {"column_name": col, "column_type": col_type, "category": get_category(col_type)}
                for k in self._metric_keys(col_type):
                    ck = f"{col}_{k}"
                    row[k] = tgt_result[ck].iloc[0] if ck in tgt_result.columns else None
                tgt_rows.append(row)

        return pd.DataFrame(src_rows), pd.DataFrame(tgt_rows)

    # ------------------------------------------------------- shared helpers
    def _shared_stat_cols(self, df: pd.DataFrame) -> list[tuple[str, str, str | None]]:
        """[(col, category, ceiling_bound), ...] for shared non-meta columns
        with equal category. `ceiling_bound` is precomputed here (rather than
        re-derived per dialect call) since both sides' raw types are only
        available together at this point -- callers just thread it through."""
        shared = df[df["source_column_type"].notna() & df["target_column_type"].notna()]
        result = []
        for _, row in shared.iterrows():
            col = row["column_name"]
            if col in self.settings.meta_columns:
                continue
            src_cat = get_category(str(row["source_column_type"]))
            tgt_cat = get_category(str(row["target_column_type"]))
            if src_cat != tgt_cat:
                continue
            if src_cat in ("numeric", "string", "date", "timestamp", "boolean"):
                bound = None
                if src_cat in ("date", "timestamp"):
                    date_bound, ts_bound = self._date_ceiling_bounds(
                        row["source_column_type"], row["target_column_type"],
                    )
                    bound = date_bound if src_cat == "date" else ts_bound
                result.append((col, src_cat, bound))
        return result

    # Longest-prefix-first: "min_len_x" must not misparse as metric="min",
    # column="len_x". Mirrors the exact aliases _period_stat_selects below
    # generates -- kept in sync by hand since the aliases are plain f-strings,
    # not a shared table (small, stable set: sum/min/max/datediff, +_len for
    # strings).
    _PERIOD_METRIC_PREFIXES = ("min_len_", "max_len_", "sum_len_", "sum_", "min_", "max_", "datediff_")

    @classmethod
    def _parse_period_alias(cls, alias: str) -> tuple[str, str]:
        """Split a period-stat select alias (e.g. 'sum_entity_id',
        'min_len_entity_name', 'datediff_deleted_at') into (metric_label,
        column_name), so a period-level mismatch can say WHICH column/metric
        differed instead of just "this period doesn't match" -- see
        gen_report_period_breakdown's `mismatch_detail` column."""
        for prefix in cls._PERIOD_METRIC_PREFIXES:
            if alias.startswith(prefix):
                return prefix.rstrip("_"), alias[len(prefix):]
        return alias, ""  # pragma: no cover - unreachable given known generators

    def _period_stat_selects(self, dialect, col: str, cat: str, ceiling_bound: str | None = None) -> list[str]:
        q = dialect.quote_ident(col)
        if cat in ("numeric", "boolean"):
            return [f"SUM({q}) AS sum_{col}", f"MIN({q}) AS min_{col}", f"MAX({q}) AS max_{col}"]
        if cat == "string":
            len_expr = f"{dialect.trim_fn()}({q})"
            lf = dialect.len_fn()
            return [
                f"SUM({lf}({len_expr})) AS sum_len_{col}",
                f"MIN({lf}({len_expr})) AS min_len_{col}",
                f"MAX({lf}({len_expr})) AS max_len_{col}",
            ]
        if cat in ("date", "timestamp"):
            dq = dialect.date_ceiling(dialect.date_floor_1970(q, cat), cat, ceiling_bound)
            return [f"SUM({dialect.datediff_expr(dq, self.settings.stat_ref_date)}) AS datediff_{col}"]
        return []

    # ------------------------------------------------------------- Report 4/5
    def gen_report_period_breakdown(
        self, granularity: str, shared_cols: list[tuple[str, str, str | None]] | None = None,
        date_col_types: tuple[str, str] | None = None,
    ) -> pd.DataFrame:
        # Bucket a FLOORED/CEILED expression, not the raw column -- mirrors
        # exactly what _period_stat_selects/_col_metric_selects already do
        # for the same date_column's stat metrics. Without this, a non-NULL
        # sentinel date survives the IS NOT NULL filter and produces a
        # nonsense bucket (real incident: MySQL's zero-date '0000-00-00'
        # formats to the literal period string "0000-00", which then reads
        # as a bogus row-count/stat MISMATCH against a target that never
        # stored that sentinel at all). date_floor_1970 clamps it to a real
        # date on engines that support GREATEST/LEAST-style flooring (MySQL
        # included), same as it already does for pre-1970 ClickHouse dates.
        src_col, tgt_col = self.date_column, self.date_column
        if date_col_types:
            src_type, tgt_type = date_col_types
            src_cat, tgt_cat = get_category(str(src_type)), get_category(str(tgt_type))
            date_bound, ts_bound = self._date_ceiling_bounds(src_type, tgt_type)
            if src_cat in ("date", "timestamp"):
                bound = date_bound if src_cat == "date" else ts_bound
                src_col = self.source.dialect.date_ceiling(
                    self.source.dialect.date_floor_1970(self.date_column, src_cat), src_cat, bound,
                )
            if tgt_cat in ("date", "timestamp"):
                bound = date_bound if tgt_cat == "date" else ts_bound
                tgt_col = self.target.dialect.date_ceiling(
                    self.target.dialect.date_floor_1970(self.date_column, tgt_cat), tgt_cat, bound,
                )
        src_expr = self.source.dialect.period_expr(src_col, granularity)
        tgt_expr = self.target.dialect.period_expr(tgt_col, granularity)

        stat_col_cats = shared_cols or []
        src_stat_select = tgt_stat_select = ""
        if stat_col_cats:
            src_parts = [s for c, cat, bound in stat_col_cats for s in self._period_stat_selects(self.source.dialect, c, cat, bound)]
            tgt_parts = [s for c, cat, bound in stat_col_cats for s in self._period_stat_selects(self.target.dialect, c, cat, bound)]
            src_stat_select = ", " + ", ".join(src_parts)
            tgt_stat_select = ", " + ", ".join(tgt_parts)

        src_q = (
            f"SELECT {src_expr} AS period, COUNT(*) AS source_row{src_stat_select} "
            f"FROM {self._table_ref('source')}{self._null_filter('source')} "
            f"GROUP BY period ORDER BY period"
        )
        tgt_q = (
            f"SELECT {tgt_expr} AS period, COUNT(*) AS target_row{tgt_stat_select} "
            f"FROM {self._table_ref('target')}{self._null_filter('target')} "
            f"GROUP BY period ORDER BY period"
        )
        self._log_query(f"{granularity.capitalize()} Breakdown Source", src_q)
        self._log_query(f"{granularity.capitalize()} Breakdown Target", tgt_q)

        src_df = self._run_source(src_q)
        tgt_df = self._run_target(tgt_q)

        if "period" not in src_df.columns:
            src_df = pd.DataFrame(columns=["period", "source_row"])
        if "period" not in tgt_df.columns:
            tgt_df = pd.DataFrame(columns=["period", "target_row"])
        src_df["period"] = src_df["period"].astype(str)
        tgt_df["period"] = tgt_df["period"].astype(str)

        _stat_prefixes = ("sum_", "min_", "max_", "datediff_")
        src_df = src_df.rename(columns={c: f"src_{c}" for c in src_df.columns if c.startswith(_stat_prefixes)})
        tgt_df = tgt_df.rename(columns={c: f"tgt_{c}" for c in tgt_df.columns if c.startswith(_stat_prefixes)})

        merged = pd.merge(src_df, tgt_df, on="period", how="outer").sort_values("period").reset_index(drop=True)
        merged["source_row"] = pd.to_numeric(merged["source_row"], errors="coerce").fillna(0).astype(int)
        merged["target_row"] = pd.to_numeric(merged["target_row"], errors="coerce").fillna(0).astype(int)
        merged["difference"] = merged["source_row"] - merged["target_row"]
        merged["row_match"] = merged["difference"] == 0

        src_metric_cols = [c for c in merged.columns if c.startswith("src_")]
        if src_metric_cols:
            def _mismatch_detail(row):
                # One entry per (column, metric) that actually differs for
                # THIS period -- lets the UI say e.g. "sum of entity_id
                # differs" instead of just "this period doesn't match",
                # which used to leave same-row-count periods looking like an
                # unexplained false alarm (real user confusion: several
                # periods showed Δ=0 yet were still listed as mismatched,
                # with no way to tell why).
                details = []
                for sc in src_metric_cols:
                    tc = "tgt_" + sc[4:]
                    if tc not in merged.columns:
                        continue
                    if not values_match(row[sc], row[tc]):
                        metric, col = self._parse_period_alias(sc[4:])
                        details.append({"column": col, "metric": metric, "source": row[sc], "target": row[tc]})
                return details
            merged["mismatch_detail"] = merged.apply(_mismatch_detail, axis=1)
        else:
            merged["mismatch_detail"] = [[] for _ in range(len(merged))]
        merged["stat_mismatch"] = merged["mismatch_detail"].apply(len)

        merged["match"] = merged["row_match"] & (merged["stat_mismatch"] == 0)
        return merged

    # ------------------------------------------------------- investigate SQL
    def _gen_investigate_query(self, shared_cols: list[tuple[str, str, str | None]]) -> str | None:
        if not self.date_column or not shared_cols:
            return None
        dcol = self.date_column

        src_agg = ["COUNT(*) AS row_count"] + [
            s for c, cat, bound in shared_cols for s in self._period_stat_selects(self.source.dialect, c, cat, bound)
        ]
        tgt_agg = ["COUNT(*) AS row_count"] + [
            s for c, cat, bound in shared_cols for s in self._period_stat_selects(self.target.dialect, c, cat, bound)
        ]
        metric_aliases = [s.split(" AS ")[-1].strip() for s in tgt_agg if not s.strip().startswith("COUNT(*)")]

        if self._same_dialect:
            # Both sides are the same engine/server -- a single query can JOIN
            # them (mirrors the original ClickHouse<->ClickHouse WITH/FULL
            # OUTER JOIN branch in db_validator.py).
            period_expr = self.target.dialect.period_expr_literal(dcol, "monthly")
            src_ref = self._table_ref("source")
            tgt_ref = self._table_ref("target")
            agg_block = ",\n        ".join(tgt_agg)
            diff_lines = [
                "COALESCE(s.period, t.period) AS period",
                "s.row_count AS src_rows", "t.row_count AS tgt_rows",
                "s.row_count - t.row_count AS row_diff",
            ]
            for alias in metric_aliases:
                diff_lines += [f"s.{alias} AS src_{alias}", f"t.{alias} AS tgt_{alias}"]
            diff_block = ",\n    ".join(diff_lines)
            return (
                f"-- Period stat check (monthly): {self.source_table_path} vs {self.target_table_path}\n"
                f"-- date_column: {dcol}  |  Run in {self.source.dialect.name}\n\n"
                f"WITH\nsrc AS (\n    SELECT\n        {period_expr} AS period,\n        {agg_block}\n"
                f"    FROM {src_ref}\n    WHERE {dcol} IS NOT NULL\n    GROUP BY period\n),\n"
                f"tgt AS (\n    SELECT\n        {period_expr} AS period,\n        {agg_block}\n"
                f"    FROM {tgt_ref}\n    WHERE {dcol} IS NOT NULL\n    GROUP BY period\n)\n"
                f"SELECT\n    {diff_block}\nFROM src s\n"
                f"FULL OUTER JOIN tgt t ON s.period = t.period\nORDER BY period;\n"
            )

        src_period = self.source.dialect.period_expr_literal(dcol, "monthly")
        tgt_period = self.target.dialect.period_expr_literal(dcol, "monthly")
        src_block = ",\n    ".join(src_agg)
        tgt_block = ",\n    ".join(tgt_agg)
        return (
            f"-- Period stat check (monthly): {self.source_table_path} vs {self.target_table_path}\n"
            f"-- date_column: {dcol}\n\n"
            f"-- ── SOURCE (run in {self.source.dialect.name}) ──\n"
            f"SELECT\n    {src_period} AS period,\n    {src_block}\n"
            f"FROM {self._table_ref('source')}\n"
            f"WHERE {dcol} IS NOT NULL\nGROUP BY period\nORDER BY period;\n\n"
            f"-- ── TARGET (run in {self.target.dialect.name}) ──\n"
            f"SELECT\n    {tgt_period} AS period,\n    {tgt_block}\n"
            f"FROM {self._table_ref('target')}\n"
            f"WHERE {dcol} IS NOT NULL\nGROUP BY period\nORDER BY period;\n"
        )

    # ------------------------------------------------------------------ run
    def run(self) -> AggregateResult:
        src_schema = self.get_schema_source()
        tgt_schema = self.get_schema_target()

        if "column_name" not in src_schema.columns:
            src_schema = pd.DataFrame(columns=["column_name", "source_column_type"])
        if "column_name" not in tgt_schema.columns:
            tgt_schema = pd.DataFrame(columns=["column_name", "target_column_type"])
        if src_schema.empty:
            raise ValueError(f"Source table not found or has no columns: {self.source_table_path}")
        if tgt_schema.empty:
            raise ValueError(f"Target table not found or has no columns: {self.target_table_path}")

        df = pd.merge(src_schema, tgt_schema, on="column_name", how="outer")

        # A date_column that doesn't exist on BOTH sides can't be used for
        # anything date-based: the period breakdown / date-range filters put
        # it in queries against BOTH engines, and the side missing it fails
        # with UNKNOWN_IDENTIFIER -- real incident: datamart_orders_smdv's
        # date_column `master_updated_at` exists only in the source, so the
        # target's monthly-breakdown query crashed the whole table. Filtering
        # only the side that HAS it isn't an option either (the two sides
        # would be compared over different row sets). Neutralize it up front
        # -- Reports 1-3 still run in full, only the date-based extras are
        # skipped -- and leave a visible note in the query log (SQL tab).
        if self.date_column:
            row = df[df["column_name"] == self.date_column]
            src_has = (not row.empty) and not pd.isna(row.iloc[0]["source_column_type"])
            tgt_has = (not row.empty) and not pd.isna(row.iloc[0]["target_column_type"])
            if not (src_has and tgt_has):
                sides = []
                if not src_has:
                    sides.append(f"source ({self.source_table_path})")
                if not tgt_has:
                    sides.append(f"target ({self.target_table_path})")
                self._log_query(
                    "Period Breakdown SKIPPED",
                    f"-- date_column '{self.date_column}' tidak ada di: {', '.join(sides)}.\n"
                    f"-- Breakdown bulanan/tahunan & filter tanggal dilewati untuk tabel ini "
                    f"(Report 1-3 tetap jalan penuh).\n"
                    f"-- Perbaiki date_column di config kalau kolomnya memang sudah berganti nama.",
                )
                self.date_column = None

        table_details = self.gen_report_table_details(df)
        column_details = self.gen_report_column_details(df)
        src_type_details, tgt_type_details = self.gen_report_column_type_details(df)

        monthly = pd.DataFrame()
        yearly = pd.DataFrame()
        investigate_query = None

        if self.date_column and not self.settings.skip_period_breakdown:
            shared_cols = self._shared_stat_cols(df)
            date_row = df[df["column_name"] == self.date_column].iloc[0]
            date_col_types = (date_row["source_column_type"], date_row["target_column_type"])
            investigate_query = self._gen_investigate_query(shared_cols)
            monthly = self.gen_report_period_breakdown("monthly", shared_cols, date_col_types)
            yearly = self.gen_report_period_breakdown("yearly", shared_cols, date_col_types)

        return AggregateResult(
            table_details=table_details,
            column_details=column_details,
            src_type_details=src_type_details,
            tgt_type_details=tgt_type_details,
            monthly_breakdown=monthly,
            yearly_breakdown=yearly,
            investigate_query=investigate_query,
            queries=dict(self.queries),
        )
