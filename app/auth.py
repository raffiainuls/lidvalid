"""Minimal session-based auth (PRD FR-17/US-19): username+password login, role
stored in the session cookie, three roles (admin/editor/viewer). No JWT/SSO —
this app runs single-process behind whatever reverse proxy/VPN already
gates access to the internal tools it replaces.
"""
from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from . import models
from .database import get_db


def get_current_user(request: Request, db: Session = Depends(get_db)) -> models.User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(models.User, user_id)
    # A deactivated account (see /api/users) must lose access immediately --
    # not just on next login -- so an existing session is treated as if it
    # were never authenticated at all, the same as a bad/missing session.
    if user is not None and not user.is_active:
        return None
    return user


# Real 401/403 responses (not a redirect) -- the React SPA's fetch layer
# can catch these and route to /login itself, unlike a redirect it can't
# meaningfully act on (a fetch() follows it silently and returns the login
# PAGE's HTML as the "response", breaking res.json()).

def require_login_api(request: Request, db: Session = Depends(get_db)) -> models.User:
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


def require_role_api(*roles: str):
    """admin always passes (bypass), regardless of `roles`. There is no
    viewer->editor->admin hierarchy here -- `require_role_api("viewer")`
    would incorrectly block editors, so it's never used."""
    def _dep(user: models.User = Depends(require_login_api)) -> models.User:
        if user.role not in roles and user.role != "admin":
            raise HTTPException(status_code=403, detail="insufficient role")
        return user
    return _dep


def require_csrf(request: Request) -> None:
    """Double-submit CSRF check for mutating /api requests: the SPA echoes
    back its non-httponly `csrf_token` cookie as the X-CSRF-Token header,
    proving the request came from our own frontend JS (a cross-site
    attacker's page can trigger the request but can't read the cookie)."""
    expected = request.session.get("csrf_token")
    got = request.headers.get("x-csrf-token")
    if not expected or not got or not hmac.compare_digest(expected, got):
        raise HTTPException(status_code=403, detail="missing/invalid CSRF token")


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
    """404 if `obj` doesn't exist or isn't owned by `user` and `user` isn't
    admin. Also functions as the missing "not found" check most single-
    object routes here didn't have before (db.get() returning None on a bad
    id)."""
    if obj is None or (not is_admin(user) and obj.owner_id != user.id):
        raise HTTPException(status_code=404)
