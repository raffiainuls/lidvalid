"""RBAC (role gating) and per-user data-scoping (ownership) coverage.

Two independent concerns, both tested here:
1. Role gating: viewer can only read; editor can create/run/manage; there is
   no admin-only ACTION anymore (see app/auth.py's require_role convention)
   -- admin is instead distinguished by DATA VISIBILITY (see #2).
2. Data scoping: Connection/ValidationConfig/Run rows are per-owner. A
   non-admin only ever sees their own rows (list views omit others', direct
   access to another owner's row 404s); admin sees everyone's.

Ported to the JSON API (app/routers/api.py) when the frontend moved from
server-rendered Jinja2 to a React SPA -- require_login_api/require_role_api
return real 401/403 responses instead of RedirectToLogin's 303, which is a
real improvement this suite now asserts on directly rather than following
a redirect Location header.
"""
from __future__ import annotations

import importlib
from pathlib import Path

from conftest import reload_app_with_fresh_db


def _make_app(tmp_path: Path):
    from cryptography.fernet import Fernet

    db_module, models_module = reload_app_with_fresh_db(
        f"sqlite:///{tmp_path / 'rbac_test.sqlite'}",
        secret_key=Fernet.generate_key().decode("ascii"),
    )
    import app.main as main_module
    importlib.reload(main_module)
    import app.security as security_module
    importlib.reload(security_module)

    from fastapi.testclient import TestClient
    return TestClient(main_module.app), db_module, models_module, security_module


def _create_user(db_module, models_module, security_module, username: str, role: str) -> int:
    db = db_module.SessionLocal()
    try:
        u = models_module.User(
            username=username, password_hash=security_module.hash_password("pw12345"),
            display_name=username, role=role,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _login(client, username: str, password: str = "pw12345") -> str:
    """Logs in and returns the CSRF token every mutating /api call needs as
    the X-CSRF-Token header (double-submit cookie -- see app/auth.py)."""
    r = client.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return client.cookies.get("csrf_token")


def _logout(client) -> None:
    client.post("/api/logout")


# --------------------------------------------------------------- role gating
def test_viewer_can_read_but_not_create_or_mutate(tmp_path):
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        _create_user(db_module, models_module, security_module, "viewer@x.com", "viewer")
        csrf = _login(client, "viewer@x.com")

        # viewer+ (read-only) routes: reachable
        assert client.get("/api/dashboard").status_code == 200
        assert client.get("/api/configs").status_code == 200
        assert client.get("/api/connections").status_code == 200

        # editor+ routes: a viewer IS authenticated, so this is a 403
        # (insufficient role), not a 401 (not authenticated at all).
        r = client.post(
            "/api/connections", json={"name": "nope", "engine": "sqlite"},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 403

        r = client.post(
            "/api/configs",
            json={"name": "nope", "description": "", "source_connection_id": 1, "target_connection_id": 1, "default_mode": "tiered"},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 403


def test_editor_can_create_connection_and_config(tmp_path):
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        _create_user(db_module, models_module, security_module, "editor@x.com", "editor")
        csrf = _login(client, "editor@x.com")

        r = client.post(
            "/api/connections",
            json={"name": "Editor Owned DB", "engine": "sqlite", "database": "editor.sqlite"},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 201, r.text

        db = db_module.SessionLocal()
        try:
            conn = db.query(models_module.Connection).filter_by(name="Editor Owned DB").first()
            assert conn is not None
            assert conn.owner_id is not None  # stamped, not orphaned
        finally:
            db.close()


def test_unauthenticated_run_detail_requires_login(tmp_path):
    """Regression test: the old fragment route this replaces previously had
    NO auth dependency at all."""
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        r = client.get("/api/runs/1")
        assert r.status_code == 401


# --------------------------------------------------------------- data scoping
def _seed_owned_connection(db_module, models_module, owner_id: int, name: str) -> int:
    db = db_module.SessionLocal()
    try:
        conn = models_module.Connection(owner_id=owner_id, name=name, engine="sqlite", database="x.sqlite")
        db.add(conn)
        db.commit()
        db.refresh(conn)
        return conn.id
    finally:
        db.close()


def _seed_owned_config_and_run(db_module, models_module, owner_id: int, conn_id: int, name: str):
    db = db_module.SessionLocal()
    try:
        cfg = models_module.ValidationConfig(
            owner_id=owner_id, name=name, source_connection_id=conn_id, target_connection_id=conn_id,
        )
        db.add(cfg)
        db.commit()
        db.refresh(cfg)

        run = models_module.Run(owner_id=owner_id, config_id=cfg.id, status="completed")
        db.add(run)
        db.commit()
        db.refresh(run)
        return cfg.id, run.id
    finally:
        db.close()


def test_editor_cannot_see_or_reach_another_editors_data(tmp_path):
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        a_id = _create_user(db_module, models_module, security_module, "a@x.com", "editor")
        _create_user(db_module, models_module, security_module, "b@x.com", "editor")

        conn_id = _seed_owned_connection(db_module, models_module, a_id, "Connection A")
        cfg_id, run_id = _seed_owned_config_and_run(db_module, models_module, a_id, conn_id, "Config A")

        # A sees their own stuff
        _login(client, "a@x.com")
        names = [c["name"] for c in client.get("/api/connections").json()]
        assert "Connection A" in names
        cfg_names = [c["name"] for c in client.get("/api/configs").json()]
        assert "Config A" in cfg_names
        assert client.get(f"/api/configs/{cfg_id}").status_code == 200
        assert client.get(f"/api/runs/{run_id}").status_code == 200
        _logout(client)

        # B sees NONE of it -- absent from lists, 404 on direct access
        _login(client, "b@x.com")
        names = [c["name"] for c in client.get("/api/connections").json()]
        assert "Connection A" not in names
        cfg_names = [c["name"] for c in client.get("/api/configs").json()]
        assert "Config A" not in cfg_names
        assert client.get(f"/api/connections/{conn_id}").status_code == 404
        assert client.get(f"/api/configs/{cfg_id}").status_code == 404
        assert client.get(f"/api/runs/{run_id}").status_code == 404


def test_admin_sees_everyones_data(tmp_path):
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        a_id = _create_user(db_module, models_module, security_module, "a2@x.com", "editor")
        _create_user(db_module, models_module, security_module, "root@x.com", "admin")

        conn_id = _seed_owned_connection(db_module, models_module, a_id, "Connection A2")
        cfg_id, run_id = _seed_owned_config_and_run(db_module, models_module, a_id, conn_id, "Config A2")

        _login(client, "root@x.com")
        names = [c["name"] for c in client.get("/api/connections").json()]
        assert "Connection A2" in names
        cfg_names = [c["name"] for c in client.get("/api/configs").json()]
        assert "Config A2" in cfg_names
        assert client.get(f"/api/configs/{cfg_id}").status_code == 200
        assert client.get(f"/api/runs/{run_id}").status_code == 200
        # admin can even reach another user's connection (bypass, not just view)
        assert client.get(f"/api/connections/{conn_id}").status_code == 200


# ------------------------------------------------------------- self-registration
def test_self_register_creates_active_editor_and_logs_in(tmp_path):
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        r = client.post(
            "/api/register",
            json={"username": "newbie", "password": "pw123456", "display_name": "New Bie"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["username"] == "newbie"
        assert body["role"] == "editor"

        # session cookie from registration works immediately, no separate login
        me = client.get("/api/me")
        assert me.status_code == 200
        assert me.json()["username"] == "newbie"

        db = db_module.SessionLocal()
        try:
            u = db.query(models_module.User).filter_by(username="newbie").first()
            assert u.is_active is True
            assert u.role == "editor"
        finally:
            db.close()


def test_self_register_sees_no_other_users_data(tmp_path):
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        other_id = _create_user(db_module, models_module, security_module, "someone_else", "editor")
        _seed_owned_connection(db_module, models_module, other_id, "Someone Else's Connection")

        r = client.post("/api/register", json={"username": "fresh_editor", "password": "pw123456"})
        assert r.status_code == 201, r.text
        names = [c["name"] for c in client.get("/api/connections").json()]
        assert names == []


def test_self_register_rejects_duplicate_username(tmp_path):
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        r1 = client.post("/api/register", json={"username": "dupe", "password": "pw123456"})
        assert r1.status_code == 201
        _logout(client)
        r2 = client.post("/api/register", json={"username": "dupe", "password": "pw223456"})
        assert r2.status_code == 409


def test_self_register_rejects_short_password(tmp_path):
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        r = client.post("/api/register", json={"username": "shortpw", "password": "abc123"})
        assert r.status_code == 400


def test_self_register_cannot_choose_role(tmp_path):
    """The request body has no `role` field at all -- passing one is just
    ignored by Pydantic rather than accepted, so there's no way to self-grant
    admin/viewer through this endpoint."""
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        r = client.post(
            "/api/register",
            json={"username": "sneaky", "password": "pw123456", "role": "admin"},
        )
        assert r.status_code == 201
        assert r.json()["role"] == "editor"
