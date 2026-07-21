"""Dashboard data assembly -- shared by the Jinja2 route (app/routers/ui.py,
pre-rewrite UI) and the JSON API route (app/routers/api.py, React SPA) so the
two never drift out of sync while both exist side by side during the
frontend migration.
"""
from __future__ import annotations

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from .. import models
from ..auth import is_admin, scope_query


def get_dashboard_data(db: Session, user: models.User) -> dict:
    recent_runs = scope_query(db.query(models.Run), models.Run, user).order_by(desc(models.Run.id)).limit(10).all()
    last_run = recent_runs[0] if recent_runs else None
    running_runs = scope_query(db.query(models.Run), models.Run, user).filter(
        models.Run.status.in_(["running", "queued"])
    ).all()
    summary = (last_run.summary or {}) if last_run else {}
    total = summary.get("tables_total") or 0
    passc = summary.get("pass") or 0
    pass_rate = f"{round(passc / total * 100)}%" if total else "—"

    # Pass-rate trend: last N *completed* runs, oldest -> newest, for the bar chart.
    trend_runs = (
        scope_query(db.query(models.Run), models.Run, user)
        .filter(models.Run.status == "completed")
        .order_by(desc(models.Run.id)).limit(12).all()
    )
    trend = []
    for r in reversed(trend_runs):
        s = r.summary or {}
        t = s.get("tables_total") or 0
        rate = round((s.get("pass") or 0) / t * 100) if t else 0
        dominant = "pass" if rate >= 80 else ("fail" if (s.get("fail") or 0) >= (s.get("error") or 0) else "error")
        trend.append({"id": r.id, "rate": rate, "dominant": dominant})

    # Problem tables: source/target pairs that show up as FAIL/ERROR most often across run history.
    problem_q = (
        db.query(
            models.RunTable.source_table, models.RunTable.target_table,
            func.count(models.RunTable.id).label("bad_count"),
        )
        .filter(models.RunTable.status.in_(["fail", "error"]))
    )
    if not is_admin(user):
        problem_q = problem_q.join(models.Run, models.RunTable.run_id == models.Run.id).filter(
            models.Run.owner_id == user.id
        )
    problem_rows = (
        problem_q
        .group_by(models.RunTable.source_table, models.RunTable.target_table)
        .order_by(desc("bad_count"))
        .limit(6)
        .all()
    )
    max_bad = max((row.bad_count for row in problem_rows), default=1)
    problem_tables = [
        {"source_table": row.source_table, "target_table": row.target_table,
         "bad_count": row.bad_count, "pct": round(row.bad_count / max_bad * 100)}
        for row in problem_rows
    ]

    return {
        "recent_runs": recent_runs, "last_run": last_run, "running_runs": running_runs,
        "summary": summary, "pass_rate": pass_rate,
        "trend": trend, "problem_tables": problem_tables,
    }
