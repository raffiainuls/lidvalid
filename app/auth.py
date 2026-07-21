"""Minimal session-based auth (PRD FR-17/US-19): email+password login, role
stored in the session cookie, three roles (admin/editor/viewer). No JWT/SSO —
this app runs single-process behind whatever reverse proxy/VPN already
gates access to the internal tools it replaces.
"""
from __future__ import annotations

from fastapi import Depends, Request
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
    def _dep(user: models.User = Depends(require_login)) -> models.User:
        if user.role not in roles and user.role != "admin":
            raise RedirectToLogin()  # simplistic: bounce to login; good enough for this scope
        return user
    return _dep


async def redirect_to_login_handler(request: Request, exc: RedirectToLogin):
    return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)
