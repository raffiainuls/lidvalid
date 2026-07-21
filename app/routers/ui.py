"""Server-rendered HTML pages + form actions (PRG pattern: POST -> redirect).

No React/Vite here -- see README "Deviations from the target architecture".
Pages are Jinja2 templates; the run-detail page polls two small endpoints
with plain `fetch()` for live-ish progress (no build step required).
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from validation_core.connectors import SUPPORTED_ENGINES

from .. import models, security
from ..auth import get_current_user, require_login, require_role, scope_query, check_owner, is_admin
from ..database import get_db
from ..services import connections_service, discovery_service, export_service, run_service
from ..services.events_bus import bus

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

# Cache-busting for static assets: StaticFiles serves /static/app.css with no
# Cache-Control override, so browsers are free to cache it heuristically --
# a real incident, not hypothetical: after deploying a CSS rewrite (modal
# popup styling), a user's browser kept serving the OLD app.css, so the new
# markup rendered as plain unstyled divs instead of a centered modal, even
# though the server was confirmed (via curl) to be serving the updated file.
# Tying the stylesheet URL to the file's own mtime (a Jinja global available
# in every template via base.html) means the URL itself changes whenever the
# file changes, so a stale cached copy under the OLD url is simply never
# requested again -- no manual version bump, no relying on users hard-refreshing.
#
# asset_version is a CALLABLE, not a plain value, and that's deliberate after
# a second incident: an earlier version computed the mtime once here at
# import time, so it only ever changed on a server restart. Editing app.css
# again without also restarting (which happens constantly during active
# development) silently reproduced the exact same stale-cache bug this was
# built to prevent, just one layer up. Stat-ing the file fresh on every
# render removes the restart-timing dependency entirely -- the version
# string is always correct regardless of when the process last started.
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _asset_version() -> int:
    return int((_STATIC_DIR / "app.css").stat().st_mtime)


templates.env.globals["asset_version"] = _asset_version

_ROW_KEY_RE = re.compile(r"^row_(\d+)_(\w+)$")


def _parse_indexed_rows(form) -> list[dict]:
    """Parse config_detail.html's `row_<i>_<field>` fields into row dicts.
    Indexed (not same-name parallel arrays) because an unchecked checkbox
    submits nothing at all, which would otherwise shift every other array
    out of alignment.

    Every field is collected as a LIST via `form.getlist()`, not `.get()` --
    key_columns/exclude_columns render as either a plain `<input>` (one
    string, possibly comma-separated -- manually-added rows before their
    columns are loaded) or a `<select multiple>` (one option name per
    selected value -- rows where the table's real columns are known). Both
    shapes are normalized by `_flatten_csv()` below, so the save logic
    doesn't need to know which widget produced the values."""
    rows: dict[int, dict] = {}
    for key in set(form.keys()):
        m = _ROW_KEY_RE.match(key)
        if not m:
            continue
        idx, field = int(m.group(1)), m.group(2)
        rows.setdefault(idx, {})[field] = form.getlist(key)
    return [rows[i] for i in sorted(rows.keys())]


def _first(row: dict, field: str, default: str = "") -> str:
    values = row.get(field) or []
    return values[0] if values else default


def _flatten_csv(row: dict, field: str) -> list[str]:
    """Collect a possibly-multi-valued form field into one flat column list,
    splitting any comma-separated entries (from a plain text input) and
    trimming whitespace either way."""
    out: list[str] = []
    for v in row.get(field) or []:
        out.extend(c.strip() for c in v.split(",") if c.strip())
    return out


# --------------------------------------------------------------------- auth
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/dashboard"):
    return templates.TemplateResponse(request, "login.html", {"request": request, "user": None, "next": next, "error": None})


@router.post("/login")
def login_submit(request: Request, email: str = Form(...), password: str = Form(...),
                  next: str = Form("/dashboard"), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user or not security.verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "login.html", {"request": request, "user": None, "next": next, "error": "Email atau password salah"},
            status_code=401,
        )
    request.session["user_id"] = user.id
    return RedirectResponse(url=next or "/dashboard", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@router.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse(url="/dashboard")


# -------------------------------------------------------------------- profile
@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, user: models.User = Depends(require_login)):
    return templates.TemplateResponse(request, "profile.html", {
        "request": request, "user": user, "active": "profile",
    })


@router.post("/profile")
def profile_update(display_name: str = Form(""), email: str = Form(...),
                    user: models.User = Depends(require_login), db: Session = Depends(get_db)):
    email = email.strip()
    dupe = db.query(models.User).filter(models.User.email == email, models.User.id != user.id).first()
    if dupe:
        return RedirectResponse(url="/profile?error=Email sudah dipakai user lain", status_code=303)
    user.display_name = display_name.strip()
    user.email = email
    db.commit()
    return RedirectResponse(url="/profile?ok=Profil diperbarui", status_code=303)


@router.post("/profile/password")
def profile_change_password(current_password: str = Form(...), new_password: str = Form(...),
                             confirm_password: str = Form(...),
                             user: models.User = Depends(require_login), db: Session = Depends(get_db)):
    if not security.verify_password(current_password, user.password_hash):
        return RedirectResponse(url="/profile?error=Password saat ini salah", status_code=303)
    if new_password != confirm_password:
        return RedirectResponse(url="/profile?error=Konfirmasi password baru tidak cocok", status_code=303)
    if len(new_password) < 8:
        return RedirectResponse(url="/profile?error=Password baru minimal 8 karakter", status_code=303)
    user.password_hash = security.hash_password(new_password)
    db.commit()
    return RedirectResponse(url="/profile?ok=Password diperbarui", status_code=303)


# ---------------------------------------------------------------- dashboard
@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: models.User = Depends(require_login), db: Session = Depends(get_db)):
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

    return templates.TemplateResponse(request, "dashboard.html", {
        "request": request, "user": user, "active": "dashboard",
        "recent_runs": recent_runs, "last_run": last_run, "running_runs": running_runs,
        "summary": summary, "pass_rate": pass_rate,
        "trend": trend, "problem_tables": problem_tables,
    })


# --------------------------------------------------------------- connections
# Connections are per-user (owner_id), same as configs/runs -- not shared
# infrastructure. editor+ manage their OWN; admin sees/manages everyone's.
# Note: Connection.name is still a globally-unique column (pre-dates
# per-user scoping) -- two different users can't both use the same name.
@router.get("/connections", response_class=HTMLResponse)
def connections_list(request: Request, user: models.User = Depends(require_login), db: Session = Depends(get_db)):
    conns = scope_query(db.query(models.Connection), models.Connection, user).order_by(models.Connection.name).all()
    return templates.TemplateResponse(request, "connections.html", {
        "request": request, "user": user, "active": "connections", "connections": conns,
    })


@router.get("/connections/new", response_class=HTMLResponse)
def connection_new_form(request: Request, user: models.User = Depends(require_role("editor"))):
    return templates.TemplateResponse(request, "connection_form.html", {
        "request": request, "user": user, "active": "connections", "connection": None, "engines": SUPPORTED_ENGINES,
    })


@router.post("/connections")
def connection_create(name: str = Form(...), engine: str = Form(...), host: str = Form(""),
                       port: int = Form(0), database: str = Form(""), username: str = Form(""),
                       password: str = Form(""), user: models.User = Depends(require_role("editor")),
                       db: Session = Depends(get_db)):
    conn = models.Connection(
        owner_id=user.id,
        name=name, engine=engine, host=host, port=int(port or 0), database=database, username=username,
        secret_encrypted=security.encrypt_secret(password) if password else None,
    )
    db.add(conn)
    db.commit()
    return RedirectResponse(url="/connections?ok=Koneksi dibuat", status_code=303)


@router.get("/connections/{conn_id}/edit", response_class=HTMLResponse)
def connection_edit_form(conn_id: int, request: Request, user: models.User = Depends(require_role("editor")),
                          db: Session = Depends(get_db)):
    conn = db.get(models.Connection, conn_id)
    check_owner(conn, user)
    return templates.TemplateResponse(request, "connection_form.html", {
        "request": request, "user": user, "active": "connections", "connection": conn, "engines": SUPPORTED_ENGINES,
    })


@router.post("/connections/{conn_id}")
def connection_update(conn_id: int, name: str = Form(...), engine: str = Form(...), host: str = Form(""),
                       port: int = Form(0), database: str = Form(""), username: str = Form(""),
                       password: str = Form(""), user: models.User = Depends(require_role("editor")),
                       db: Session = Depends(get_db)):
    conn = db.get(models.Connection, conn_id)
    check_owner(conn, user)
    conn.name, conn.engine, conn.host = name, engine, host
    conn.port, conn.database, conn.username = int(port or 0), database, username
    if password:
        conn.secret_encrypted = security.encrypt_secret(password)
    db.commit()
    return RedirectResponse(url="/connections?ok=Koneksi diperbarui", status_code=303)


@router.post("/connections/{conn_id}/test")
def connection_test(conn_id: int, user: models.User = Depends(require_role("editor")), db: Session = Depends(get_db)):
    conn = db.get(models.Connection, conn_id)
    check_owner(conn, user)
    result = connections_service.test_connection(conn)
    connections_service.record_test_result(db, conn, result)
    if result.get("ok"):
        return RedirectResponse(url=f"/connections?ok=Test OK ({result.get('latency_ms')}ms)", status_code=303)
    return RedirectResponse(url=f"/connections?error=Test gagal: {result.get('error')}", status_code=303)


@router.post("/connections/{conn_id}/delete")
def connection_delete(conn_id: int, user: models.User = Depends(require_role("editor")), db: Session = Depends(get_db)):
    conn = db.get(models.Connection, conn_id)
    check_owner(conn, user)
    in_use = db.query(models.ValidationConfig).filter(
        (models.ValidationConfig.source_connection_id == conn_id)
        | (models.ValidationConfig.target_connection_id == conn_id)
    ).count()
    if in_use:
        return RedirectResponse(url="/connections?error=Koneksi dipakai config, tidak bisa dihapus", status_code=303)
    db.delete(conn)
    db.commit()
    return RedirectResponse(url="/connections?ok=Koneksi dihapus", status_code=303)


# -------------------------------------------------------------------- configs
@router.get("/configs", response_class=HTMLResponse)
def configs_list(request: Request, user: models.User = Depends(require_login), db: Session = Depends(get_db)):
    configs = scope_query(db.query(models.ValidationConfig), models.ValidationConfig, user).filter_by(
        is_archived=False
    ).order_by(models.ValidationConfig.name).all()
    last_runs = {}
    for c in configs:
        r = db.query(models.Run).filter_by(config_id=c.id).order_by(desc(models.Run.id)).first()
        if r:
            last_runs[c.id] = r
    return templates.TemplateResponse(request, "configs.html", {
        "request": request, "user": user, "active": "configs", "configs": configs, "last_runs": last_runs,
    })


@router.get("/configs/new", response_class=HTMLResponse)
def config_new_form(request: Request, user: models.User = Depends(require_role("editor")), db: Session = Depends(get_db)):
    conns = scope_query(db.query(models.Connection), models.Connection, user).order_by(models.Connection.name).all()
    return templates.TemplateResponse(request, "config_form.html", {
        "request": request, "user": user, "active": "configs", "connections": conns,
    })


@router.post("/configs")
def config_create(name: str = Form(...), description: str = Form(""), source_connection_id: int = Form(...),
                   target_connection_id: int = Form(...), default_mode: str = Form("tiered"),
                   user: models.User = Depends(require_role("editor")), db: Session = Depends(get_db)):
    # `name` has a UNIQUE constraint (models.ValidationConfig) -- checked here
    # explicitly rather than catching the resulting IntegrityError, so a
    # duplicate name is a normal flash-error redirect back to the form
    # instead of an unhandled 500 (this crashed as a real Internal Server
    # Error before this check existed: sqlite3.IntegrityError: UNIQUE
    # constraint failed: validation_configs.name).
    if db.query(models.ValidationConfig).filter_by(name=name).first():
        return RedirectResponse(
            url=f"/configs/new?error=Nama config \"{name}\" sudah dipakai, pilih nama lain", status_code=303,
        )
    # Referenced connections must belong to this user (or user is admin) --
    # otherwise a tampered form could wire a config to someone else's connection.
    src_conn = db.get(models.Connection, int(source_connection_id))
    tgt_conn = db.get(models.Connection, int(target_connection_id))
    check_owner(src_conn, user)
    check_owner(tgt_conn, user)
    cfg = models.ValidationConfig(
        owner_id=user.id,
        name=name, description=description,
        source_connection_id=int(source_connection_id), target_connection_id=int(target_connection_id),
        default_mode=default_mode,
    )
    db.add(cfg)
    db.commit()
    db.refresh(cfg)
    return RedirectResponse(url=f"/configs/{cfg.id}?ok=Config dibuat — lanjutkan pemetaan tabel", status_code=303)


def _other_configs(db: Session, config_id: int, user: models.User) -> list[models.ValidationConfig]:
    """Other active configs (this user's own, or all if admin), for the
    'copy mapping from' dropdown."""
    q = scope_query(db.query(models.ValidationConfig), models.ValidationConfig, user)
    return (
        q.filter(models.ValidationConfig.id != config_id, models.ValidationConfig.is_archived == False)  # noqa: E712
        .order_by(models.ValidationConfig.name).all()
    )


def _table_columns_for(cfg: models.ValidationConfig, tables, suggestions: list[dict]) -> dict[str, list[str]]:
    """Column names (from the SOURCE connection) for every table that will
    appear in the mapping editor — both already-saved rows and not-yet-saved
    suggestion rows — so the template can render real dropdowns instead of
    free-text inputs wherever the table's columns are actually knowable."""
    names = [t.source_table for t in tables] + [s["source_table"] for s in suggestions]
    return discovery_service.columns_by_table(cfg.source_connection, names)


@router.get("/configs/{config_id}", response_class=HTMLResponse)
def config_detail(config_id: int, request: Request, user: models.User = Depends(require_login),
                   db: Session = Depends(get_db)):
    cfg = db.get(models.ValidationConfig, config_id)
    check_owner(cfg, user)
    runs = scope_query(db.query(models.Run), models.Run, user).filter_by(
        config_id=config_id
    ).order_by(desc(models.Run.id)).limit(20).all()
    return templates.TemplateResponse(request, "config_detail.html", {
        "request": request, "user": user, "active": "configs",
        "config": cfg, "tables": cfg.tables, "runs": runs, "suggestions": [],
        "configs_for_copy": _other_configs(db, config_id, user),
        "table_columns": _table_columns_for(cfg, cfg.tables, []),
    })


@router.post("/configs/{config_id}/suggest", response_class=HTMLResponse)
def config_suggest(config_id: int, request: Request, prefix: str = Form(""),
                    user: models.User = Depends(require_role("editor")), db: Session = Depends(get_db)):
    cfg = db.get(models.ValidationConfig, config_id)
    check_owner(cfg, user)
    existing = {t.source_table for t in cfg.tables}
    all_suggestions = discovery_service.suggest_mappings(cfg.source_connection, cfg.target_connection, prefix)
    suggestions = [s for s in all_suggestions if s["target_table"] and s["source_table"] not in existing]
    runs = db.query(models.Run).filter_by(config_id=config_id).order_by(desc(models.Run.id)).limit(20).all()
    return templates.TemplateResponse(request, "config_detail.html", {
        "request": request, "user": user, "active": "configs",
        "config": cfg, "tables": cfg.tables, "runs": runs, "suggestions": suggestions,
        "configs_for_copy": _other_configs(db, config_id, user),
        "table_columns": _table_columns_for(cfg, cfg.tables, suggestions),
    })


@router.post("/configs/{config_id}/copy-from", response_class=HTMLResponse)
def config_copy_mappings(config_id: int, request: Request, source_config_id: int = Form(...),
                          user: models.User = Depends(require_role("editor")), db: Session = Depends(get_db)):
    cfg = db.get(models.ValidationConfig, config_id)
    check_owner(cfg, user)
    other = db.get(models.ValidationConfig, source_config_id)
    check_owner(other, user)
    existing = {t.source_table for t in cfg.tables}
    suggestions = discovery_service.suggest_from_config(other, existing) if other else []
    runs = db.query(models.Run).filter_by(config_id=config_id).order_by(desc(models.Run.id)).limit(20).all()
    return templates.TemplateResponse(request, "config_detail.html", {
        "request": request, "user": user, "active": "configs",
        "config": cfg, "tables": cfg.tables, "runs": runs, "suggestions": suggestions,
        "configs_for_copy": _other_configs(db, config_id, user),
        "table_columns": _table_columns_for(cfg, cfg.tables, suggestions),
    })


@router.post("/configs/{config_id}/tables")
async def config_save_tables(config_id: int, request: Request, user: models.User = Depends(require_role("editor")),
                              db: Session = Depends(get_db)):
    form = await request.form()
    rows = _parse_indexed_rows(form)
    cfg = db.get(models.ValidationConfig, config_id)
    check_owner(cfg, user)

    for t in list(cfg.tables):  # replace-all: simplest correct approach at this scale
        db.delete(t)
    db.flush()

    for row in rows:
        source_table = _first(row, "source_table").strip()
        target_table = _first(row, "target_table").strip()
        if not source_table or not target_table:
            continue
        db.add(models.ConfigTable(
            config_id=config_id,
            source_table=source_table,
            target_table=target_table,
            key_columns=_flatten_csv(row, "key_columns") or ["id"],
            chunk_column=_first(row, "chunk_column").strip() or None,
            date_column=_first(row, "date_column").strip() or None,
            exclude_columns=_flatten_csv(row, "exclude_columns"),
            mode_override=_first(row, "mode_override").strip() or None,
            enabled="on" in (row.get("enabled") or []),
        ))
    db.commit()
    return RedirectResponse(url=f"/configs/{config_id}?ok=Pemetaan tabel disimpan", status_code=303)


# ---------------------------------------------------------------------- runs
@router.post("/configs/{config_id}/run")
def config_run_now(config_id: int, mode: str = Form(""), user: models.User = Depends(require_role("editor")),
                    db: Session = Depends(get_db)):
    cfg = db.get(models.ValidationConfig, config_id)
    check_owner(cfg, user)
    run = run_service.create_run(db, cfg, mode=mode or None, trigger_type="manual")
    run_service.start_run_async(run.id)
    return RedirectResponse(url=f"/runs/{run.id}", status_code=303)


STATUS_HISTORY_RUNS = 15


@router.get("/configs/{config_id}/status", response_class=HTMLResponse)
def config_table_status(config_id: int, request: Request, user: models.User = Depends(require_login),
                         db: Session = Depends(get_db)):
    """Per-config table matrix: the LATEST status of every table across all
    runs, plus per-table run history. Solves a real usability complaint:
    re-running a subset creates a new Run that only shows the re-run tables,
    so there was no single place to see "where does every table stand NOW"
    once runs became partial."""
    cfg = db.get(models.ValidationConfig, config_id)
    check_owner(cfg, user)
    runs = (
        db.query(models.Run).filter_by(config_id=config_id)
        .order_by(desc(models.Run.id)).limit(STATUS_HISTORY_RUNS).all()
    )
    run_ids = [r.id for r in runs]
    rts = (
        db.query(models.RunTable).filter(models.RunTable.run_id.in_(run_ids))
        .order_by(desc(models.RunTable.run_id)).all()
    ) if run_ids else []

    by_table: dict[str, dict] = {}
    for rt in rts:  # newest run first, so first sighting == latest state
        entry = by_table.setdefault(rt.source_table, {"latest": rt, "history": []})
        entry["history"].append(rt)

    rows = []
    config_table_names = set()
    for ct in cfg.tables:
        config_table_names.add(ct.source_table)
        e = by_table.get(ct.source_table)
        rows.append({
            "source_table": ct.source_table, "target_table": ct.target_table,
            "enabled": ct.enabled, "removed": False,
            "latest": e["latest"] if e else None,
            "history": e["history"] if e else [],
        })
    # Tables that appear in run history but were since removed from the
    # config -- still shown (flagged) so history isn't silently hidden.
    for name, e in by_table.items():
        if name not in config_table_names:
            rows.append({
                "source_table": name, "target_table": e["latest"].target_table,
                "enabled": False, "removed": True,
                "latest": e["latest"], "history": e["history"],
            })

    counts: dict[str, int] = {}
    for row in rows:
        key = row["latest"].status if row["latest"] else "never_ran"
        counts[key] = counts.get(key, 0) + 1

    return templates.TemplateResponse(request, "config_status.html", {
        "request": request, "user": user, "active": "configs",
        "config": cfg, "rows": rows, "counts": counts,
        "history_limit": STATUS_HISTORY_RUNS,
    })


@router.post("/configs/{config_id}/rerun-table")
def config_rerun_table(config_id: int, source_table: str = Form(...),
                        user: models.User = Depends(require_role("editor")), db: Session = Depends(get_db)):
    cfg = db.get(models.ValidationConfig, config_id)
    check_owner(cfg, user)
    valid = {t.source_table for t in cfg.tables if t.enabled}
    if source_table not in valid:
        return RedirectResponse(
            url=f"/configs/{config_id}/status?error=Tabel {source_table} tidak ada (atau nonaktif) di config ini",
            status_code=303,
        )
    run = run_service.create_run(db, cfg, mode=None, table_filter=[source_table], trigger_type="revalidate")
    run_service.start_run_async(run.id)
    # Back to the status page (NOT the new run's page): the whole point of
    # this flow is keeping the full per-table picture in view -- the new
    # run would only show this one table.
    return RedirectResponse(
        url=f"/configs/{config_id}/status?ok=Re-run {source_table} dimulai (Run #{run.id})",
        status_code=303,
    )


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(run_id: int, request: Request, user: models.User = Depends(require_login), db: Session = Depends(get_db)):
    run = db.get(models.Run, run_id)
    check_owner(run, user)
    run_tables = db.query(models.RunTable).filter_by(run_id=run_id).order_by(models.RunTable.id).all()
    events, _ = bus.get_since(run_id, 0)
    return templates.TemplateResponse(request, "run_detail.html", {
        "request": request, "user": user, "active": "configs",
        "run": run, "run_tables": run_tables, "events": events[-100:],
    })


@router.get("/runs/{run_id}/tables-fragment", response_class=HTMLResponse)
def run_tables_fragment(run_id: int, request: Request, user: models.User = Depends(require_login),
                         db: Session = Depends(get_db)):
    run = db.get(models.Run, run_id)
    check_owner(run, user)
    run_tables = db.query(models.RunTable).filter_by(run_id=run_id).order_by(models.RunTable.id).all()
    events, _ = bus.get_since(run_id, 0)
    return templates.TemplateResponse(request, "_run_tables_fragment.html", {
        "request": request, "run_tables": run_tables, "events": events[-100:],
    })


@router.post("/runs/{run_id}/cancel")
def run_cancel(run_id: int, user: models.User = Depends(require_role("editor")), db: Session = Depends(get_db)):
    run = db.get(models.Run, run_id)
    check_owner(run, user)
    bus.request_cancel(run_id)
    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)


_SCOPE_LABELS = {"all": "semua tabel", "fail": "FAIL", "error": "ERROR", "non_pass": "non-PASS"}


@router.post("/runs/{run_id}/resume")
def run_resume(run_id: int, scope: str = Form("non_pass"),
               user: models.User = Depends(require_role("editor")), db: Session = Depends(get_db)):
    run = db.get(models.Run, run_id)
    check_owner(run, user)
    new_run = run_service.resume_run(db, run, scope=scope)
    if new_run is None:
        label = _SCOPE_LABELS.get(scope, scope)
        return RedirectResponse(
            url=f"/runs/{run_id}?error=Tidak ada tabel berstatus {label} di run ini — tidak ada yang di-re-run",
            status_code=303,
        )
    return RedirectResponse(url=f"/runs/{new_run.id}", status_code=303)


@router.get("/runs/{run_id}/export.xlsx")
def run_export(run_id: int, user: models.User = Depends(require_login), db: Session = Depends(get_db)):
    run = db.get(models.Run, run_id)
    check_owner(run, user)
    path = export_service.export_run_to_excel(db, run)
    return FileResponse(path, filename=path.name,
                         media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


ROWLEVEL_PAGE_SIZE = 200


@router.get("/runs/{run_id}/tables/{run_table_id}", response_class=HTMLResponse)
def table_drilldown(run_id: int, run_table_id: int, request: Request, tab: str = "ringkasan",
                     column: str = "", page: int = 1,
                     user: models.User = Depends(require_login), db: Session = Depends(get_db)):
    run = db.get(models.Run, run_id)
    check_owner(run, user)
    rt = db.get(models.RunTable, run_table_id)
    page = max(1, page)
    agg_findings = [f for f in rt.aggregate_findings if f.category not in ("period_monthly", "period_yearly")]
    period_findings = [f for f in rt.aggregate_findings if f.category in ("period_monthly", "period_yearly")]
    # A mismatched period now persists ONE finding per reason (row-count
    # diff, and/or each column/metric whose per-period stat differs) rather
    # than one vague row -- so len(period_findings) counts FINDINGS, not
    # mismatched PERIODS. The nav badge should still read "N periods are
    # mismatched", not "N findings across periods".
    period_count = len({f.period for f in period_findings})

    # rowlevel_sample_cap lets a single table accumulate up to ~10k-20k+ rows
    # per finding type. Loading them all via the `rt.rowlevel_findings` ORM
    # relationship -- as this route used to, on EVERY tab view including
    # "ringkasan" which doesn't even display them -- made drilldown pages of
    # heavily-mismatched tables take minutes under load (see README incident).
    # Counts/column breakdown are cheap SQL aggregates; actual rows are only
    # fetched, one page at a time, for whichever tab is currently being viewed.
    FRL = models.FindingRowLevel
    base_q = db.query(FRL).filter(FRL.run_table_id == rt.id)
    missing_count = base_q.filter(FRL.finding_type.in_(("missing_in_source", "missing_in_target"))).count()
    total_diff_count = base_q.filter(FRL.finding_type == "value_diff").count()

    diff_column_counts: dict[str, int] = dict(
        db.query(FRL.column_name, func.count(FRL.id))
        .filter(FRL.run_table_id == rt.id, FRL.finding_type == "value_diff")
        .group_by(FRL.column_name).all()
    )
    diff_columns = sorted(diff_column_counts.keys())

    missing_findings: list = []
    diff_findings: list = []
    total_pages = 1
    if tab == "missing":
        total_pages = max(1, -(-missing_count // ROWLEVEL_PAGE_SIZE))
        missing_findings = (
            base_q.filter(FRL.finding_type.in_(("missing_in_source", "missing_in_target")))
            .order_by(FRL.id).offset((page - 1) * ROWLEVEL_PAGE_SIZE).limit(ROWLEVEL_PAGE_SIZE).all()
        )
    elif tab == "diffs":
        diff_q = base_q.filter(FRL.finding_type == "value_diff")
        page_row_count = total_diff_count
        if column:
            diff_q = diff_q.filter(FRL.column_name == column)
            page_row_count = diff_column_counts.get(column, 0)
        total_pages = max(1, -(-page_row_count // ROWLEVEL_PAGE_SIZE))
        diff_findings = (
            diff_q.order_by(FRL.id).offset((page - 1) * ROWLEVEL_PAGE_SIZE).limit(ROWLEVEL_PAGE_SIZE).all()
        )

    return templates.TemplateResponse(request, "table_drilldown.html", {
        "request": request, "user": user, "active": "configs",
        "run": run, "rt": rt, "tab": tab,
        "agg_findings": agg_findings, "period_findings": period_findings, "period_count": period_count,
        "missing_findings": missing_findings, "diff_findings": diff_findings,
        "missing_count": missing_count,
        "diff_columns": diff_columns, "diff_column_counts": diff_column_counts,
        "selected_column": column, "total_diff_count": total_diff_count,
        "page": page, "total_pages": total_pages, "page_size": ROWLEVEL_PAGE_SIZE,
    })


_NUMERIC_KEY_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _format_keys_for_sql(keys: list[str], key_columns: list[str]) -> str:
    """Render distinct row_key strings as a paste-ready SQL value list --
    bare comma-separated numbers if EVERY key looks numeric (the common
    case: auto-increment ids, ready to drop straight into `WHERE id IN
    (...)`), single-quoted otherwise (safe default for string ids).

    Composite keys (row_key is the '_'-joined value from
    validation_core.rowlevel.comparator.composite_key) are shown AS-IS, one
    per line, with a header noting the column order -- NOT split back into
    separate columns. The join is lossy if any individual value itself
    contains '_', so guessing a split would sometimes silently produce the
    WRONG tuple; showing the true stored value is honest even if it means
    the user splits it by hand for a multi-column WHERE.
    """
    def fmt(v: str) -> str:
        return v if _NUMERIC_KEY_RE.match(v) else f"'{v}'"

    header = ""
    if len(key_columns) > 1:
        header = f"-- composite key ({' + '.join(key_columns)}, digabung dgn '_') -- tetap 1 nilai per baris\n"
    return header + ",\n".join(fmt(k) for k in keys)


@router.get("/runs/{run_id}/tables/{run_table_id}/keys", response_class=PlainTextResponse)
def table_drilldown_keys(run_id: int, run_table_id: int, kind: str = "missing_in_target", column: str = "",
                          user: models.User = Depends(require_login), db: Session = Depends(get_db)):
    """Plain-text, ready-to-paste key list for a table's Missing Keys /
    Value Diffs findings -- backs the "Copy ID bermasalah" buttons on those
    tabs. Explicit user request: they run a manual re-insert script against
    the pipeline once they know which rows are missing/differing (`WHERE id
    IN (...)`), and needed every affected key -- not just the CURRENT PAGE
    of the (paginated, see ROWLEVEL_PAGE_SIZE) Missing Keys/Value Diffs
    tabs -- without re-typing them out of the rendered table.

    `kind`: "missing_in_target" | "missing_in_source" | "value_diff".
    `column`: for kind="value_diff" only, restrict to one column (mirrors
    that tab's own filter dropdown); ignored for the missing-key kinds.
    """
    rt = db.get(models.RunTable, run_table_id)
    if rt is None or rt.run_id != run_id:
        return PlainTextResponse("-- tabel tidak ditemukan", status_code=404)
    run = db.get(models.Run, run_id)
    if run is None or (not is_admin(user) and run.owner_id != user.id):
        return PlainTextResponse("-- tabel tidak ditemukan", status_code=404)

    FRL = models.FindingRowLevel
    q = db.query(FRL.row_key).filter(FRL.run_table_id == rt.id)
    if kind == "value_diff":
        q = q.filter(FRL.finding_type == "value_diff")
        if column:
            q = q.filter(FRL.column_name == column)
    elif kind in ("missing_in_source", "missing_in_target"):
        q = q.filter(FRL.finding_type == kind)
    else:
        return PlainTextResponse(f"-- kind tidak dikenal: {kind}", status_code=400)

    keys = sorted({row[0] for row in q.distinct().all()})
    if not keys:
        return PlainTextResponse("-- tidak ada key untuk kriteria ini")

    key_columns = list((rt.rl_metrics or {}).get("key_columns") or ["id"])
    return PlainTextResponse(_format_keys_for_sql(keys, key_columns))
