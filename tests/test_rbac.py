"""RBAC (role gating) and per-user data-scoping (ownership) coverage.

Two independent concerns, both tested here:
1. Role gating: viewer can only read; editor can create/run/manage; there is
   no admin-only ACTION anymore (see app/auth.py's require_role convention)
   -- admin is instead distinguished by DATA VISIBILITY (see #2).
2. Data scoping: Connection/ValidationConfig/Run rows are per-owner. A
   non-admin only ever sees their own rows (list views omit others', direct
   access to another owner's row 404s); admin sees everyone's.
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


def _create_user(db_module, models_module, security_module, email: str, role: str) -> int:
    db = db_module.SessionLocal()
    try:
        u = models_module.User(
            email=email, password_hash=security_module.hash_password("pw12345"),
            display_name=email, role=role,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _login(client, email: str, password: str = "pw12345"):
    r = client.post("/login", data={"email": email, "password": password})
    assert r.status_code in (200, 303), r.text
    return r


def _logout(client) -> None:
    client.post("/logout")


# --------------------------------------------------------------- role gating
def test_viewer_can_read_but_not_create_or_mutate(tmp_path):
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        _create_user(db_module, models_module, security_module, "viewer@x.com", "viewer")
        _login(client, "viewer@x.com")

        # viewer+ (read-only) routes: reachable
        assert client.get("/dashboard").status_code == 200
        assert client.get("/configs").status_code == 200
        assert client.get("/connections").status_code == 200

        # editor+ routes: bounced to /login instead of acting (RedirectToLogin)
        cases = [
            ("get", "/connections/new"),
            ("get", "/configs/new"),
        ]
        for method, url in cases:
            r = getattr(client, method)(url, follow_redirects=False)
            assert r.status_code == 303, f"{method} {url} -> {r.status_code}"
            assert r.headers["location"].startswith("/login"), f"{method} {url} -> {r.headers['location']}"

        r = client.post("/connections", data={"name": "nope", "engine": "sqlite"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")

        r = client.post(
            "/configs", data={"name": "nope", "source_connection_id": 1, "target_connection_id": 1},
            follow_redirects=False,
        )
        assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_editor_can_create_connection_and_config(tmp_path):
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        _create_user(db_module, models_module, security_module, "editor@x.com", "editor")
        _login(client, "editor@x.com")

        r = client.post(
            "/connections",
            data={"name": "Editor Owned DB", "engine": "sqlite", "database": "editor.sqlite"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "ok=" in r.headers["location"]

        db = db_module.SessionLocal()
        try:
            conn = db.query(models_module.Connection).filter_by(name="Editor Owned DB").first()
            assert conn is not None
            assert conn.owner_id is not None  # stamped, not orphaned
        finally:
            db.close()


def test_unauthorized_tables_fragment_now_requires_login(tmp_path):
    """Regression test: this route previously had NO auth dependency at all."""
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        r = client.get("/runs/1/tables-fragment", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"].startswith("/login")


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
        assert "Connection A" in client.get("/connections").text
        assert "Config A" in client.get("/configs").text
        assert client.get(f"/configs/{cfg_id}").status_code == 200
        assert client.get(f"/runs/{run_id}").status_code == 200
        _logout(client)

        # B sees NONE of it -- absent from lists, 404 on direct access
        _login(client, "b@x.com")
        assert "Connection A" not in client.get("/connections").text
        assert "Config A" not in client.get("/configs").text
        assert client.get(f"/connections/{conn_id}/edit").status_code == 404
        assert client.get(f"/configs/{cfg_id}").status_code == 404
        assert client.get(f"/runs/{run_id}").status_code == 404
        assert client.get(f"/api/runs/{run_id}/status").status_code == 404


def test_admin_sees_everyones_data(tmp_path):
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        a_id = _create_user(db_module, models_module, security_module, "a2@x.com", "editor")
        _create_user(db_module, models_module, security_module, "root@x.com", "admin")

        conn_id = _seed_owned_connection(db_module, models_module, a_id, "Connection A2")
        cfg_id, run_id = _seed_owned_config_and_run(db_module, models_module, a_id, conn_id, "Config A2")

        _login(client, "root@x.com")
        assert "Connection A2" in client.get("/connections").text
        assert "Config A2" in client.get("/configs").text
        assert client.get(f"/configs/{cfg_id}").status_code == 200
        assert client.get(f"/runs/{run_id}").status_code == 200
        # admin can even edit another user's connection (bypass, not just view)
        assert client.get(f"/connections/{conn_id}/edit").status_code == 200
