"""Small JSON endpoints used by page-embedded polling/lookup JS.
No SSE (see README) -- a 2s `fetch()` poll is simple, robust, and plenty
responsive for a validation run that takes seconds to minutes per table.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models
from ..auth import require_login
from ..database import get_db
from ..services import discovery_service

router = APIRouter()


@router.get("/runs/{run_id}/status")
def run_status(run_id: int, user: models.User = Depends(require_login), db: Session = Depends(get_db)):
    run = db.get(models.Run, run_id)
    return {"id": run.id, "status": run.status, "summary": run.summary or {}}


@router.get("/configs/{config_id}/table-columns")
def config_table_columns(config_id: int, table: str, side: str = "source",
                          user: models.User = Depends(require_login), db: Session = Depends(get_db)):
    """Column names for one table on the config's source/target connection —
    used by config_detail.html's JS to turn a freshly-typed table name (in a
    manually-added row) into proper key/chunk/date/exclude dropdowns, the
    same way server-rendered rows already get theirs at page-load time."""
    cfg = db.get(models.ValidationConfig, config_id)
    if not cfg:
        return {"columns": [], "error": "config not found"}
    conn = cfg.target_connection if side == "target" else cfg.source_connection
    if not table:
        return {"columns": []}
    try:
        cols = discovery_service.list_columns(conn, table)
        return {"columns": [c["name"] for c in cols]}
    except Exception as exc:  # noqa: BLE001 - surfaced to the JS caller, not a 500
        return {"columns": [], "error": str(exc)}
