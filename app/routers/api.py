"""JSON API backing the React SPA (frontend/) -- the sole HTTP interface
since app/routers/ui.py's server-rendered Jinja2 pages were retired. Every
route uses require_login_api/require_role_api (real 401/403 responses)
rather than a redirect, since a fetch() call can't usefully follow one.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from validation_core.connectors import SUPPORTED_ENGINES

from .. import models, security
from ..auth import check_owner, scope_query, is_admin, require_login_api, require_role_api, require_csrf
from ..database import get_db
from ..services import connections_service, dashboard_service, discovery_service, export_service, run_service
from ..services.events_bus import bus

router = APIRouter()


# ------------------------------------------------------------------- auth
class LoginBody(BaseModel):
    username: str
    password: str


class RegisterBody(BaseModel):
    username: str
    password: str
    display_name: str = ""


def _serialize_user(user: models.User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
    }


def _start_session(user: models.User, request: Request, response: Response) -> None:
    request.session["user_id"] = user.id
    csrf_token = security.generate_csrf_token()
    request.session["csrf_token"] = csrf_token
    # Non-httponly on purpose -- the SPA's JS reads this to echo it back as
    # the X-CSRF-Token header on mutating requests (double-submit pattern).
    # It's not a secret by itself; require_csrf only trusts it because it's
    # compared against the session's copy, which an attacker can't read/set.
    response.set_cookie("csrf_token", csrf_token, httponly=False, samesite="lax")


@router.post("/login")
def api_login(body: LoginBody, request: Request, response: Response, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == body.username).first()
    if not user or not security.verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Username atau password salah")
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Akun ini sudah dinonaktifkan")
    _start_session(user, request, response)
    return _serialize_user(user)


@router.post("/register", status_code=201)
def api_register(body: RegisterBody, request: Request, response: Response, db: Session = Depends(get_db)):
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username wajib diisi")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password minimal 8 karakter")
    if db.query(models.User).filter_by(username=username).first():
        raise HTTPException(status_code=409, detail="Username sudah dipakai")
    # Self-registered accounts always land as "editor" -- full create/manage
    # rights over THEIR OWN Connections/Configs/Runs, but zero visibility
    # into anyone else's (owner_id scoping, see scope_query/check_owner in
    # app/auth.py). Only an existing admin can promote an account to admin
    # (via the Users page), never this endpoint.
    user = models.User(
        username=username,
        password_hash=security.hash_password(body.password),
        display_name=body.display_name.strip(),
        role="editor",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    _start_session(user, request, response)
    return _serialize_user(user)


@router.post("/logout")
def api_logout(request: Request, response: Response, user: models.User = Depends(require_login_api),
               _csrf: None = Depends(require_csrf)):
    request.session.clear()
    response.delete_cookie("csrf_token")
    return {"ok": True}


@router.get("/me")
def api_me(user: models.User = Depends(require_login_api)):
    return _serialize_user(user)


# -------------------------------------------------------------- dashboard
def _serialize_run_summary(run: models.Run) -> dict:
    return {
        "id": run.id,
        "status": run.status,
        "mode": run.mode,
        "config_name": run.config.name,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "summary": run.summary or {},
    }


@router.get("/dashboard")
def api_dashboard(user: models.User = Depends(require_login_api), db: Session = Depends(get_db)):
    data = dashboard_service.get_dashboard_data(db, user)
    return {
        "recent_runs": [_serialize_run_summary(r) for r in data["recent_runs"]],
        "last_run": _serialize_run_summary(data["last_run"]) if data["last_run"] else None,
        "running_runs": [_serialize_run_summary(r) for r in data["running_runs"]],
        "summary": data["summary"],
        "pass_rate": data["pass_rate"],
        "trend": data["trend"],
        "problem_tables": data["problem_tables"],
    }


# ------------------------------------------------------------------ profile
class ProfileBody(BaseModel):
    display_name: str = ""
    username: str


class PasswordChangeBody(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str


@router.get("/profile")
def api_get_profile(user: models.User = Depends(require_login_api)):
    return _serialize_user(user)


@router.put("/profile")
def api_update_profile(body: ProfileBody, user: models.User = Depends(require_login_api),
                        db: Session = Depends(get_db), _csrf: None = Depends(require_csrf)):
    username = body.username.strip()
    dupe = db.query(models.User).filter(models.User.username == username, models.User.id != user.id).first()
    if dupe:
        raise HTTPException(status_code=409, detail="Username sudah dipakai user lain")
    user.display_name = body.display_name.strip()
    user.username = username
    db.commit()
    return _serialize_user(user)


@router.post("/profile/password")
def api_change_password(body: PasswordChangeBody, user: models.User = Depends(require_login_api),
                         db: Session = Depends(get_db), _csrf: None = Depends(require_csrf)):
    if not security.verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Password saat ini salah")
    if body.new_password != body.confirm_password:
        raise HTTPException(status_code=400, detail="Konfirmasi password baru tidak cocok")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password baru minimal 8 karakter")
    user.password_hash = security.hash_password(body.new_password)
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------- connections
class ConnectionBody(BaseModel):
    name: str
    engine: str
    host: str = ""
    port: int = 0
    database: str = ""
    username: str = ""
    password: str = ""


def _serialize_connection(conn: models.Connection) -> dict:
    return {
        "id": conn.id,
        "name": conn.name,
        "engine": conn.engine,
        "host": conn.host,
        "port": conn.port,
        "database": conn.database,
        "username": conn.username,
        "status": conn.status,
        "last_tested_at": conn.last_tested_at.isoformat() if conn.last_tested_at else None,
        "last_test_message": conn.last_test_message,
    }


@router.get("/connections/engines")
def api_list_engines():
    # Must stay registered before GET /connections/{conn_id} -- FastAPI
    # matches route templates in registration order, and {conn_id}: int
    # would 422 (not fall through) on a literal "engines" segment otherwise.
    return {"engines": list(SUPPORTED_ENGINES)}


@router.get("/connections")
def api_list_connections(user: models.User = Depends(require_login_api), db: Session = Depends(get_db)):
    conns = scope_query(db.query(models.Connection), models.Connection, user).order_by(models.Connection.name).all()
    return [_serialize_connection(c) for c in conns]


@router.post("/connections", status_code=201)
def api_create_connection(body: ConnectionBody, user: models.User = Depends(require_role_api("editor")),
                           db: Session = Depends(get_db), _csrf: None = Depends(require_csrf)):
    if db.query(models.Connection).filter_by(name=body.name).first():
        raise HTTPException(status_code=409, detail=f'Nama koneksi "{body.name}" sudah dipakai, pilih nama lain')
    conn = models.Connection(
        owner_id=user.id,
        name=body.name, engine=body.engine, host=body.host, port=body.port,
        database=body.database, username=body.username,
        secret_encrypted=security.encrypt_secret(body.password) if body.password else None,
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return _serialize_connection(conn)


@router.get("/connections/{conn_id}")
def api_get_connection(conn_id: int, user: models.User = Depends(require_login_api), db: Session = Depends(get_db)):
    conn = db.get(models.Connection, conn_id)
    check_owner(conn, user)
    return _serialize_connection(conn)


@router.put("/connections/{conn_id}")
def api_update_connection(conn_id: int, body: ConnectionBody, user: models.User = Depends(require_role_api("editor")),
                           db: Session = Depends(get_db), _csrf: None = Depends(require_csrf)):
    conn = db.get(models.Connection, conn_id)
    check_owner(conn, user)
    dupe = db.query(models.Connection).filter(models.Connection.name == body.name, models.Connection.id != conn_id).first()
    if dupe:
        raise HTTPException(status_code=409, detail=f'Nama koneksi "{body.name}" sudah dipakai, pilih nama lain')
    conn.name, conn.engine, conn.host = body.name, body.engine, body.host
    conn.port, conn.database, conn.username = body.port, body.database, body.username
    if body.password:
        conn.secret_encrypted = security.encrypt_secret(body.password)
    db.commit()
    db.refresh(conn)
    return _serialize_connection(conn)


@router.delete("/connections/{conn_id}")
def api_delete_connection(conn_id: int, user: models.User = Depends(require_role_api("editor")),
                           db: Session = Depends(get_db), _csrf: None = Depends(require_csrf)):
    conn = db.get(models.Connection, conn_id)
    check_owner(conn, user)
    in_use = db.query(models.ValidationConfig).filter(
        (models.ValidationConfig.source_connection_id == conn_id)
        | (models.ValidationConfig.target_connection_id == conn_id)
    ).count()
    if in_use:
        raise HTTPException(status_code=409, detail="Koneksi dipakai config, tidak bisa dihapus")
    db.delete(conn)
    db.commit()
    return {"ok": True}


@router.post("/connections/{conn_id}/test")
def api_test_connection(conn_id: int, user: models.User = Depends(require_role_api("editor")),
                         db: Session = Depends(get_db), _csrf: None = Depends(require_csrf)):
    conn = db.get(models.Connection, conn_id)
    check_owner(conn, user)
    result = connections_service.test_connection(conn)
    connections_service.record_test_result(db, conn, result)
    return {"ok": result.get("ok"), "latency_ms": result.get("latency_ms"), "error": result.get("error")}


# ------------------------------------------------------------------ configs
class ConfigCreateBody(BaseModel):
    name: str
    description: str = ""
    source_connection_id: int
    target_connection_id: int
    default_mode: str = "tiered"


class ConfigTableRow(BaseModel):
    source_table: str
    target_table: str
    key_columns: list[str] = ["id"]
    chunk_column: str | None = None
    date_column: str | None = None
    exclude_columns: list[str] = []
    mode_override: str | None = None
    enabled: bool = True


class ConfigTablesBody(BaseModel):
    rows: list[ConfigTableRow]


class ConfigSuggestBody(BaseModel):
    prefix: str = ""


class ConfigCopyFromBody(BaseModel):
    source_config_id: int


class ConfigRunBody(BaseModel):
    mode: str = ""


class RerunTableBody(BaseModel):
    source_table: str


def _serialize_connection_brief(conn: models.Connection) -> dict:
    return {"id": conn.id, "name": conn.name, "engine": conn.engine}


def _serialize_config_table(t: models.ConfigTable) -> dict:
    return {
        "id": t.id,
        "source_table": t.source_table,
        "target_table": t.target_table,
        "key_columns": t.key_columns or ["id"],
        "chunk_column": t.chunk_column,
        "date_column": t.date_column,
        "exclude_columns": t.exclude_columns or [],
        "mode_override": t.mode_override,
        "enabled": t.enabled,
        "note": t.note or "",
    }


def _other_configs_for_copy(db: Session, config_id: int, user: models.User) -> list[models.ValidationConfig]:
    q = scope_query(db.query(models.ValidationConfig), models.ValidationConfig, user)
    return (
        q.filter(models.ValidationConfig.id != config_id, models.ValidationConfig.is_archived == False)  # noqa: E712
        .order_by(models.ValidationConfig.name).all()
    )


@router.get("/configs")
def api_list_configs(user: models.User = Depends(require_login_api), db: Session = Depends(get_db)):
    configs = scope_query(db.query(models.ValidationConfig), models.ValidationConfig, user).filter_by(
        is_archived=False
    ).order_by(models.ValidationConfig.name).all()
    out = []
    for c in configs:
        last_run = db.query(models.Run).filter_by(config_id=c.id).order_by(desc(models.Run.id)).first()
        out.append({
            "id": c.id,
            "name": c.name,
            "source_connection_name": c.source_connection.name,
            "target_connection_name": c.target_connection.name,
            "table_count": len(c.tables),
            "default_mode": c.default_mode,
            "last_run": _serialize_run_summary(last_run) if last_run else None,
        })
    return out


@router.post("/configs", status_code=201)
def api_create_config(body: ConfigCreateBody, user: models.User = Depends(require_role_api("editor")),
                       db: Session = Depends(get_db), _csrf: None = Depends(require_csrf)):
    if db.query(models.ValidationConfig).filter_by(name=body.name).first():
        raise HTTPException(status_code=409, detail=f'Nama config "{body.name}" sudah dipakai, pilih nama lain')
    src_conn = db.get(models.Connection, body.source_connection_id)
    tgt_conn = db.get(models.Connection, body.target_connection_id)
    check_owner(src_conn, user)
    check_owner(tgt_conn, user)
    cfg = models.ValidationConfig(
        owner_id=user.id,
        name=body.name, description=body.description,
        source_connection_id=body.source_connection_id, target_connection_id=body.target_connection_id,
        default_mode=body.default_mode,
    )
    db.add(cfg)
    db.commit()
    db.refresh(cfg)
    return {"id": cfg.id}


@router.get("/configs/{config_id}")
def api_get_config(config_id: int, user: models.User = Depends(require_login_api), db: Session = Depends(get_db)):
    cfg = db.get(models.ValidationConfig, config_id)
    check_owner(cfg, user)
    runs = scope_query(db.query(models.Run), models.Run, user).filter_by(
        config_id=config_id
    ).order_by(desc(models.Run.id)).limit(20).all()
    return {
        "id": cfg.id,
        "name": cfg.name,
        "description": cfg.description,
        "source_connection": _serialize_connection_brief(cfg.source_connection),
        "target_connection": _serialize_connection_brief(cfg.target_connection),
        "default_mode": cfg.default_mode,
        "tables": [_serialize_config_table(t) for t in cfg.tables],
        "runs": [_serialize_run_summary(r) for r in runs],
        "configs_for_copy": [
            {"id": c.id, "name": c.name, "table_count": len(c.tables)}
            for c in _other_configs_for_copy(db, config_id, user)
        ],
        "table_columns": discovery_service.columns_by_table(
            cfg.source_connection, [t.source_table for t in cfg.tables]
        ),
    }


@router.put("/configs/{config_id}/tables")
def api_save_config_tables(config_id: int, body: ConfigTablesBody, user: models.User = Depends(require_role_api("editor")),
                            db: Session = Depends(get_db), _csrf: None = Depends(require_csrf)):
    cfg = db.get(models.ValidationConfig, config_id)
    check_owner(cfg, user)
    for t in list(cfg.tables):  # replace-all: simplest correct approach at this scale
        db.delete(t)
    db.flush()
    for row in body.rows:
        if not row.source_table.strip() or not row.target_table.strip():
            continue
        db.add(models.ConfigTable(
            config_id=config_id,
            source_table=row.source_table.strip(),
            target_table=row.target_table.strip(),
            key_columns=row.key_columns or ["id"],
            chunk_column=row.chunk_column or None,
            date_column=row.date_column or None,
            exclude_columns=row.exclude_columns or [],
            mode_override=row.mode_override or None,
            enabled=row.enabled,
        ))
    db.commit()
    db.refresh(cfg)
    return {
        "tables": [_serialize_config_table(t) for t in cfg.tables],
        "table_columns": discovery_service.columns_by_table(
            cfg.source_connection, [t.source_table for t in cfg.tables]
        ),
    }


@router.post("/configs/{config_id}/suggest")
def api_config_suggest(config_id: int, body: ConfigSuggestBody, user: models.User = Depends(require_role_api("editor")),
                        db: Session = Depends(get_db), _csrf: None = Depends(require_csrf)):
    cfg = db.get(models.ValidationConfig, config_id)
    check_owner(cfg, user)
    existing = {t.source_table for t in cfg.tables}
    all_suggestions = discovery_service.suggest_mappings(cfg.source_connection, cfg.target_connection, body.prefix)
    suggestions = [s for s in all_suggestions if s["target_table"] and s["source_table"] not in existing]
    names = [t.source_table for t in cfg.tables] + [s["source_table"] for s in suggestions]
    return {
        "suggestions": suggestions,
        "table_columns": discovery_service.columns_by_table(cfg.source_connection, names),
    }


@router.post("/configs/{config_id}/copy-from")
def api_config_copy_from(config_id: int, body: ConfigCopyFromBody, user: models.User = Depends(require_role_api("editor")),
                          db: Session = Depends(get_db), _csrf: None = Depends(require_csrf)):
    cfg = db.get(models.ValidationConfig, config_id)
    check_owner(cfg, user)
    other = db.get(models.ValidationConfig, body.source_config_id)
    check_owner(other, user)
    existing = {t.source_table for t in cfg.tables}
    suggestions = discovery_service.suggest_from_config(other, existing) if other else []
    names = [t.source_table for t in cfg.tables] + [s["source_table"] for s in suggestions]
    return {
        "suggestions": suggestions,
        "table_columns": discovery_service.columns_by_table(cfg.source_connection, names),
    }


@router.post("/configs/{config_id}/run")
def api_config_run_now(config_id: int, body: ConfigRunBody, user: models.User = Depends(require_role_api("editor")),
                        db: Session = Depends(get_db), _csrf: None = Depends(require_csrf)):
    cfg = db.get(models.ValidationConfig, config_id)
    check_owner(cfg, user)
    run = run_service.create_run(db, cfg, mode=body.mode or None, trigger_type="manual")
    run_service.start_run_async(run.id)
    return {"run_id": run.id}


STATUS_HISTORY_RUNS = 15


def _serialize_status_run_table(rt: models.RunTable) -> dict:
    return {
        "id": rt.id,
        "run_id": rt.run_id,
        "status": rt.status,
        "finished_at": rt.finished_at.isoformat() if rt.finished_at else None,
    }


@router.get("/configs/{config_id}/status")
def api_config_status(config_id: int, user: models.User = Depends(require_login_api), db: Session = Depends(get_db)):
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
            "latest": _serialize_status_run_table(e["latest"]) if e else None,
            "history": [_serialize_status_run_table(h) for h in e["history"]] if e else [],
        })
    for name, e in by_table.items():
        if name not in config_table_names:
            rows.append({
                "source_table": name, "target_table": e["latest"].target_table,
                "enabled": False, "removed": True,
                "latest": _serialize_status_run_table(e["latest"]),
                "history": [_serialize_status_run_table(h) for h in e["history"]],
            })

    counts: dict[str, int] = {}
    for row in rows:
        key = row["latest"]["status"] if row["latest"] else "never_ran"
        counts[key] = counts.get(key, 0) + 1

    return {
        "config": {"id": cfg.id, "name": cfg.name},
        "rows": rows,
        "counts": counts,
        "history_limit": STATUS_HISTORY_RUNS,
    }


@router.post("/configs/{config_id}/rerun-table")
def api_config_rerun_table(config_id: int, body: RerunTableBody, user: models.User = Depends(require_role_api("editor")),
                            db: Session = Depends(get_db), _csrf: None = Depends(require_csrf)):
    cfg = db.get(models.ValidationConfig, config_id)
    check_owner(cfg, user)
    valid = {t.source_table for t in cfg.tables if t.enabled}
    if body.source_table not in valid:
        raise HTTPException(status_code=400, detail=f"Tabel {body.source_table} tidak ada (atau nonaktif) di config ini")
    run = run_service.create_run(db, cfg, mode=None, table_filter=[body.source_table], trigger_type="revalidate")
    run_service.start_run_async(run.id)
    return {"run_id": run.id}


@router.get("/configs/{config_id}/table-columns")
def config_table_columns(config_id: int, table: str, side: str = "source",
                          user: models.User = Depends(require_role_api("editor")), db: Session = Depends(get_db)):
    """Column names for one table on the config's source/target connection —
    used by config_detail.html's JS to turn a freshly-typed table name (in a
    manually-added row) into proper key/chunk/date/exclude dropdowns, the
    same way server-rendered rows already get theirs at page-load time."""
    cfg = db.get(models.ValidationConfig, config_id)
    if not cfg:
        return {"columns": [], "error": "config not found"}
    check_owner(cfg, user)
    conn = cfg.target_connection if side == "target" else cfg.source_connection
    if not table:
        return {"columns": []}
    try:
        cols = discovery_service.list_columns(conn, table)
        return {"columns": [c["name"] for c in cols]}
    except Exception as exc:  # noqa: BLE001 - surfaced to the JS caller, not a 500
        return {"columns": [], "error": str(exc)}


# ---------------------------------------------------------------------- runs
class ResumeRunBody(BaseModel):
    scope: str = "non_pass"


_SCOPE_LABELS = {"all": "semua tabel", "fail": "FAIL", "error": "ERROR", "non_pass": "non-PASS"}


def _serialize_run_table_row(rt: models.RunTable) -> dict:
    return {
        "id": rt.id,
        "source_table": rt.source_table,
        "target_table": rt.target_table,
        "status": rt.status,
        "tier_reached": rt.tier_reached,
        "source_rows": rt.source_rows,
        "target_rows": rt.target_rows,
        "row_diff": rt.row_diff,
        "agg_stat_mismatch": (rt.agg_metrics or {}).get("stat_mismatch"),
        "missing_count": (
            (rt.rl_metrics.get("missing_in_source", 0) + rt.rl_metrics.get("missing_in_target", 0))
            if rt.rl_metrics else None
        ),
        "differing_values": (rt.rl_metrics or {}).get("differing_values") if rt.rl_metrics else None,
    }


@router.get("/runs/{run_id}")
def api_get_run(run_id: int, user: models.User = Depends(require_login_api), db: Session = Depends(get_db)):
    run = db.get(models.Run, run_id)
    check_owner(run, user)
    run_tables = db.query(models.RunTable).filter_by(run_id=run_id).order_by(models.RunTable.id).all()
    events, _ = bus.get_since(run_id, 0)
    return {
        "id": run.id,
        "status": run.status,
        "mode": run.mode,
        "trigger_type": run.trigger_type,
        "config_id": run.config_id,
        "config_name": run.config.name,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "tables": [_serialize_run_table_row(rt) for rt in run_tables],
        "events": [{"kind": e.get("kind"), "message": e.get("message")} for e in events[-100:]],
    }


@router.post("/runs/{run_id}/cancel")
def api_cancel_run(run_id: int, user: models.User = Depends(require_role_api("editor")),
                    db: Session = Depends(get_db), _csrf: None = Depends(require_csrf)):
    run = db.get(models.Run, run_id)
    check_owner(run, user)
    bus.request_cancel(run_id)
    return {"ok": True}


@router.post("/runs/{run_id}/resume")
def api_resume_run(run_id: int, body: ResumeRunBody, user: models.User = Depends(require_role_api("editor")),
                    db: Session = Depends(get_db), _csrf: None = Depends(require_csrf)):
    run = db.get(models.Run, run_id)
    check_owner(run, user)
    new_run = run_service.resume_run(db, run, scope=body.scope)
    if new_run is None:
        label = _SCOPE_LABELS.get(body.scope, body.scope)
        raise HTTPException(status_code=400, detail=f"Tidak ada tabel berstatus {label} di run ini — tidak ada yang di-re-run")
    return {"run_id": new_run.id}


@router.get("/runs/{run_id}/export.xlsx")
def api_export_run(run_id: int, user: models.User = Depends(require_login_api), db: Session = Depends(get_db)):
    run = db.get(models.Run, run_id)
    check_owner(run, user)
    path = export_service.export_run_to_excel(db, run)
    return FileResponse(path, filename=path.name,
                         media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


ROWLEVEL_PAGE_SIZE = 200


def _serialize_finding_aggregate(f: models.FindingAggregate) -> dict:
    return {
        "category": f.category,
        "column_name": f.column_name,
        "metric": f.metric,
        "period": f.period,
        "source_value": f.source_value,
        "target_value": f.target_value,
        "difference": f.difference,
    }


@router.get("/runs/{run_id}/tables/{run_table_id}")
def api_get_run_table(run_id: int, run_table_id: int, user: models.User = Depends(require_login_api),
                       db: Session = Depends(get_db)):
    """Bundles every 'small' (non-paginated) drilldown tab -- Ringkasan,
    Agregat, Tipe Kolom, Periode, SQL, Log -- into one response, so tab
    switching in the SPA is client-side with no extra round-trip. Only
    Missing Keys / Value Diffs are paginated (see /rowlevel below)."""
    run = db.get(models.Run, run_id)
    check_owner(run, user)
    rt = db.get(models.RunTable, run_table_id)
    if rt is None or rt.run_id != run_id:
        raise HTTPException(status_code=404)

    agg_findings = [f for f in rt.aggregate_findings if f.category not in ("period_monthly", "period_yearly")]
    period_findings = [f for f in rt.aggregate_findings if f.category in ("period_monthly", "period_yearly")]
    period_count = len({f.period for f in period_findings})
    column_type_details = rt.column_type_details or []
    type_mismatch_count = sum(1 for c in column_type_details if c.get("category_match") is False)

    FRL = models.FindingRowLevel
    base_q = db.query(FRL).filter(FRL.run_table_id == rt.id)
    missing_count = base_q.filter(FRL.finding_type.in_(("missing_in_source", "missing_in_target"))).count()
    total_diff_count = base_q.filter(FRL.finding_type == "value_diff").count()
    diff_column_counts: dict[str, int] = dict(
        db.query(FRL.column_name, func.count(FRL.id))
        .filter(FRL.run_table_id == rt.id, FRL.finding_type == "value_diff")
        .group_by(FRL.column_name).all()
    )

    return {
        "id": rt.id,
        "run_id": rt.run_id,
        "config_id": run.config_id,
        "source_table": rt.source_table,
        "target_table": rt.target_table,
        "status": rt.status,
        "tier_reached": rt.tier_reached,
        "mode": rt.mode,
        "source_rows": rt.source_rows,
        "target_rows": rt.target_rows,
        "row_diff": rt.row_diff,
        "source_cols": rt.source_cols,
        "target_cols": rt.target_cols,
        "extra_source_columns": rt.extra_source_columns or [],
        "extra_target_columns": rt.extra_target_columns or [],
        "rl_metrics": rt.rl_metrics or None,
        "chunks_total": rt.chunks_total,
        "investigate_query": rt.investigate_query,
        "error": rt.error,
        "agg_findings": [_serialize_finding_aggregate(f) for f in agg_findings],
        "period_findings": [_serialize_finding_aggregate(f) for f in period_findings],
        "period_count": period_count,
        "column_type_details": column_type_details,
        "type_mismatch_count": type_mismatch_count,
        "missing_count": missing_count,
        "total_diff_count": total_diff_count,
        "diff_columns": sorted(diff_column_counts.keys()),
        "diff_column_counts": diff_column_counts,
        "queries": rt.queries or {},
        "event_log": rt.event_log or [],
    }


@router.get("/runs/{run_id}/tables/{run_table_id}/rowlevel")
def api_get_run_table_rowlevel(run_id: int, run_table_id: int, type: str, column: str = "", page: int = 1,
                                user: models.User = Depends(require_login_api), db: Session = Depends(get_db)):
    run = db.get(models.Run, run_id)
    check_owner(run, user)
    rt = db.get(models.RunTable, run_table_id)
    if rt is None or rt.run_id != run_id:
        raise HTTPException(status_code=404)
    page = max(1, page)

    FRL = models.FindingRowLevel
    base_q = db.query(FRL).filter(FRL.run_table_id == rt.id)

    if type == "missing":
        count_q = base_q.filter(FRL.finding_type.in_(("missing_in_source", "missing_in_target")))
        total = count_q.count()
        total_pages = max(1, -(-total // ROWLEVEL_PAGE_SIZE))
        findings = (
            count_q.order_by(FRL.id).offset((page - 1) * ROWLEVEL_PAGE_SIZE).limit(ROWLEVEL_PAGE_SIZE).all()
        )
        rows = [{"finding_type": f.finding_type, "row_key": f.row_key} for f in findings]
    elif type == "diffs":
        diff_q = base_q.filter(FRL.finding_type == "value_diff")
        if column:
            diff_q = diff_q.filter(FRL.column_name == column)
        total = diff_q.count()
        total_pages = max(1, -(-total // ROWLEVEL_PAGE_SIZE))
        findings = diff_q.order_by(FRL.id).offset((page - 1) * ROWLEVEL_PAGE_SIZE).limit(ROWLEVEL_PAGE_SIZE).all()
        rows = [
            {"row_key": f.row_key, "column_name": f.column_name, "source_value": f.source_value, "target_value": f.target_value}
            for f in findings
        ]
    else:
        raise HTTPException(status_code=400, detail=f"type tidak dikenal: {type}")

    return {"rows": rows, "page": page, "total_pages": total_pages, "page_size": ROWLEVEL_PAGE_SIZE}


_NUMERIC_KEY_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _format_keys_for_sql(keys: list[str], key_columns: list[str]) -> str:
    def fmt(v: str) -> str:
        return v if _NUMERIC_KEY_RE.match(v) else f"'{v}'"

    header = ""
    if len(key_columns) > 1:
        header = f"-- composite key ({' + '.join(key_columns)}, digabung dgn '_') -- tetap 1 nilai per baris\n"
    return header + ",\n".join(fmt(k) for k in keys)


@router.get("/runs/{run_id}/tables/{run_table_id}/keys", response_class=PlainTextResponse)
def api_get_run_table_keys(run_id: int, run_table_id: int, kind: str = "missing_in_target", column: str = "",
                            user: models.User = Depends(require_login_api), db: Session = Depends(get_db)):
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


# --------------------------------------------------------------- users (admin)
VALID_ROLES = ("admin", "editor", "viewer")


class UserCreateBody(BaseModel):
    username: str
    password: str
    display_name: str = ""
    role: str = "editor"


class UserUpdateBody(BaseModel):
    display_name: str = ""
    username: str
    role: str
    is_active: bool


class ResetPasswordBody(BaseModel):
    new_password: str


def _serialize_user_admin(u: models.User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "display_name": u.display_name,
        "role": u.role,
        "is_active": u.is_active,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


@router.get("/users")
def api_list_users(user: models.User = Depends(require_role_api("admin")), db: Session = Depends(get_db)):
    users = db.query(models.User).order_by(models.User.id).all()
    return [_serialize_user_admin(u) for u in users]


@router.post("/users", status_code=201)
def api_create_user(body: UserCreateBody, user: models.User = Depends(require_role_api("admin")),
                     db: Session = Depends(get_db), _csrf: None = Depends(require_csrf)):
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username wajib diisi")
    if db.query(models.User).filter_by(username=username).first():
        raise HTTPException(status_code=409, detail="Username sudah dipakai")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password minimal 8 karakter")
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Role tidak dikenal: {body.role}")
    new_user = models.User(
        username=username,
        password_hash=security.hash_password(body.password),
        display_name=body.display_name.strip(),
        role=body.role,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return _serialize_user_admin(new_user)


@router.put("/users/{user_id}")
def api_update_user(user_id: int, body: UserUpdateBody, user: models.User = Depends(require_role_api("admin")),
                     db: Session = Depends(get_db), _csrf: None = Depends(require_csrf)):
    target = db.get(models.User, user_id)
    if target is None:
        raise HTTPException(status_code=404)
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username wajib diisi")
    dupe = db.query(models.User).filter(models.User.username == username, models.User.id != user_id).first()
    if dupe:
        raise HTTPException(status_code=409, detail="Username sudah dipakai user lain")
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Role tidak dikenal: {body.role}")
    # Editing your OWN account here can't demote or deactivate yourself --
    # otherwise a lone admin could lock themselves out with one click, with
    # no one else able to undo it (no other admin, no CLI access assumed).
    if target.id == user.id and (body.role != "admin" or not body.is_active):
        raise HTTPException(status_code=400, detail="Tidak bisa menurunkan role atau menonaktifkan akun sendiri")
    target.display_name = body.display_name.strip()
    target.username = username
    target.role = body.role
    target.is_active = body.is_active
    db.commit()
    db.refresh(target)
    return _serialize_user_admin(target)


@router.post("/users/{user_id}/reset-password")
def api_reset_user_password(user_id: int, body: ResetPasswordBody, user: models.User = Depends(require_role_api("admin")),
                             db: Session = Depends(get_db), _csrf: None = Depends(require_csrf)):
    target = db.get(models.User, user_id)
    if target is None:
        raise HTTPException(status_code=404)
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password baru minimal 8 karakter")
    target.password_hash = security.hash_password(body.new_password)
    db.commit()
    return {"ok": True}
