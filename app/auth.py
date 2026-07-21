"""Minimal session-based auth (PRD FR-17/US-19): email+password login, role
stored in the session cookie, three roles (admin/editor/viewer). No JWT/SSO —
this app runs single-process behind whatever reverse proxy/VPN already
gates access to the internal tools it replaces.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from . import models
from .database import get_db


class RedirectToLogin(Exception):
    pass


def get_current_user(request: Request, db: Session = Depends(get_db)) -> models.User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.get(models.User, user_id)


def require_login(request: Request, db: Session = Depends(get_db)) -> models.User:
    user = get_current_user(request, db)
    if not user:
        raise RedirectToLogin()
    return user


def require_role(*roles: str):
    """admin always passes (bypass), regardless of `roles`. Convention used
    across routers/: viewer+ routes use plain `require_login`; editor+ routes
    use `require_role("editor")`; admin-only routes use `require_role("admin")`.
    There is no viewer->editor->admin hierarchy here -- `require_role("viewer")`
    would incorrectly block editors, so it's never used."""
    def _dep(user: models.User = Depends(require_login)) -> models.User:
        if user.role not in roles and user.role != "admin":
            raise RedirectToLogin()  # simplistic: bounce to login; good enough for this scope
        return user
    return _dep


async def redirect_to_login_handler(request: Request, exc: RedirectToLogin):
    return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)


# ------------------------------------------------------------- data scoping
# Per-user isolation for Connection/ValidationConfig/Run: everyone but admin
# only ever sees rows they own. admin sees everything (oversight/troubleshooting).

def is_admin(user: models.User) -> bool:
    return user.role == "admin"


def scope_query(query, model, user: models.User):
    """Restrict a list query to rows owned by `user`; admin sees everything."""
    if is_admin(user):
        return query
    return query.filter(model.owner_id == user.id)


def check_owner(obj, user: models.User) -> None:
    """404 (not a RedirectToLogin -- this is an authz, not authn, failure) if
    `obj` doesn't exist or isn't owned by `user` and `user` isn't admin.
    Also functions as the missing "not found" check most single-object
    routes here didn't have before (db.get() returning None on a bad id)."""
    if obj is None or (not is_admin(user) and obj.owner_id != user.id):
        raise HTTPException(status_code=404)
