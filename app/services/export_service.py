"""Excel export for a completed run, built directly from persisted ORM rows
(RunTable + findings) rather than the transient in-memory AggregateResult
DataFrames — those only exist during execution. This is a reduced-fidelity
export vs. the legacy tool's full per-column stat sheets (findings only, not
every matching column) — consistent with the drilldown UI's scope (see
README "Deviations from the target architecture").
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from validation_core.excel_export import _sheet_name, _apply_style

from .. import models

EXPORT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def export_run_to_excel(db: Session, run: models.Run) -> Path:
    run_tables = (
        db.query(models.RunTable).filter_by(run_id=run.id).order_by(models.RunTable.id).all()
    )

    summary_rows = []
    for rt in run_tables:
        agg = rt.agg_metrics or {}
        rl = rt.rl_metrics or {}
        summary_rows.append({
            "target_table": rt.target_table,
            "status": rt.status.upper(),
            "tier_reached": rt.tier_reached,
            "mode": rt.mode,
            "source_rows": rt.source_rows,
            "target_rows": rt.target_rows,
            "row_diff": rt.row_diff,
            "col_completeness_mismatch": agg.get("col_completeness_mismatch"),
            "col_uniqueness_mismatch": agg.get("col_uniqueness_mismatch"),
            "stat_mismatch": agg.get("stat_mismatch"),
            "monthly_mismatch": agg.get("monthly_mismatch"),
            "yearly_mismatch": agg.get("yearly_mismatch"),
            "missing_in_source": rl.get("missing_in_source"),
            "missing_in_target": rl.get("missing_in_target"),
            "differing_values": rl.get("differing_values"),
            "error": rt.error or "",
        })
    summary_df = pd.DataFrame(summary_rows)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = EXPORT_DIR / f"run_{run.id}_{ts}.xlsx"

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        for rt in run_tables:
            agg_rows = [
                {"category": f.category, "column": f.column_name, "metric": f.metric,
                 "period": f.period, "source": f.source_value, "target": f.target_value,
                 "difference": f.difference}
                for f in rt.aggregate_findings
            ]
            if agg_rows:
                pd.DataFrame(agg_rows).to_excel(writer, sheet_name=_sheet_name(rt.target_table, "_findings"), index=False)

            rl_rows = [
                {"type": f.finding_type, "key": f.row_key, "column": f.column_name,
                 "source": f.source_value, "target": f.target_value}
                for f in rt.rowlevel_findings
            ]
            if rl_rows:
                pd.DataFrame(rl_rows).to_excel(writer, sheet_name=_sheet_name(rt.target_table, "_rowlevel"), index=False)

    _apply_style(str(out_path), summary_df)
    return out_path
