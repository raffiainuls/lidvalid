"""Config sharing: an owner can grant OTHER users access to a specific
ValidationConfig (and everything under it -- Runs/RunTables/Findings) at one
of three tiers (view/run/edit), without making them the owner or an admin.

Covers: share CRUD is owner/admin-only; each permission tier gates the right
actions (view=read-only, run=+trigger/cancel runs, edit=+mapping changes);
a config with no owner/admin/share relationship still 404s; GET /configs
includes shared-with-me rows with the right metadata; Connection brief info
(host/port, never credentials) is visible to a shared user.
"""
from __future__ import annotations

import importlib
from pathlib import Path

from conftest import reload_app_with_fresh_db


def _make_app(tmp_path: Path):
    from cryptography.fernet import Fernet

    db_module, models_module = reload_app_with_fresh_db(
        f"sqlite:///{tmp_path / 'sharing_test.sqlite'}",
        secret_key=Fernet.generate_key().decode("ascii"),
    )
    import app.main as main_module
    importlib.reload(main_module)
    import app.security as security_module
    importlib.reload(security_module)

    from fastapi.testclient import TestClient
    return TestClient(main_module.app), db_module, models_module, security_module


def _create_user(db_module, models_module, security_module, username: str, role: str = "editor") -> int:
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
    r = client.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return client.cookies.get("csrf_token")


def _logout(client) -> None:
    client.post("/api/logout")


def _seed_owned_connection(db_module, models_module, owner_id: int, name: str) -> int:
    db = db_module.SessionLocal()
    try:
        conn = models_module.Connection(
            owner_id=owner_id, name=name, engine="sqlite", host="dbhost.local", port=5432, database="x.sqlite",
        )
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


def _share(client, csrf, config_id, username, permission):
    return client.post(
        f"/api/configs/{config_id}/shares", json={"username": username, "permission": permission},
        headers={"X-CSRF-Token": csrf},
    )


# --------------------------------------------------------------- share CRUD
def test_owner_can_create_list_update_delete_share(tmp_path):
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        owner_id = _create_user(db_module, models_module, security_module, "owner1")
        _create_user(db_module, models_module, security_module, "friend1")
        conn_id = _seed_owned_connection(db_module, models_module, owner_id, "Conn1")
        cfg_id, _ = _seed_owned_config_and_run(db_module, models_module, owner_id, conn_id, "Cfg1")

        csrf = _login(client, "owner1")
        r = _share(client, csrf, cfg_id, "friend1", "view")
        assert r.status_code == 201, r.text
        share_id = r.json()["id"]
        assert r.json()["permission"] == "view"
        assert r.json()["username"] == "friend1"

        r = client.get(f"/api/configs/{cfg_id}/shares")
        assert r.status_code == 200
        assert len(r.json()) == 1

        r = client.put(
            f"/api/configs/{cfg_id}/shares/{share_id}", json={"permission": "edit"},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 200
        assert r.json()["permission"] == "edit"

        r = client.delete(f"/api/configs/{cfg_id}/shares/{share_id}", headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200
        assert client.get(f"/api/configs/{cfg_id}/shares").json() == []


def test_share_rejects_duplicate_self_and_unknown_user(tmp_path):
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        owner_id = _create_user(db_module, models_module, security_module, "owner2")
        _create_user(db_module, models_module, security_module, "friend2")
        conn_id = _seed_owned_connection(db_module, models_module, owner_id, "Conn2")
        cfg_id, _ = _seed_owned_config_and_run(db_module, models_module, owner_id, conn_id, "Cfg2")

        csrf = _login(client, "owner2")
        assert _share(client, csrf, cfg_id, "owner2", "view").status_code == 400  # self
        assert _share(client, csrf, cfg_id, "nobody_here", "view").status_code == 404  # unknown user
        assert _share(client, csrf, cfg_id, "friend2", "view").status_code == 201
        assert _share(client, csrf, cfg_id, "friend2", "run").status_code == 409  # duplicate


def test_non_owner_non_admin_cannot_manage_shares(tmp_path):
    """A user who has an EDIT-tier share still can't add/remove other
    people's access -- share management doesn't delegate."""
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        owner_id = _create_user(db_module, models_module, security_module, "owner3")
        _create_user(db_module, models_module, security_module, "editor_collab")
        _create_user(db_module, models_module, security_module, "third_party")
        conn_id = _seed_owned_connection(db_module, models_module, owner_id, "Conn3")
        cfg_id, _ = _seed_owned_config_and_run(db_module, models_module, owner_id, conn_id, "Cfg3")

        owner_csrf = _login(client, "owner3")
        assert _share(client, owner_csrf, cfg_id, "editor_collab", "edit").status_code == 201
        _logout(client)

        collab_csrf = _login(client, "editor_collab")
        r = _share(client, collab_csrf, cfg_id, "third_party", "view")
        assert r.status_code == 404  # not owner/admin -- can't manage shares at all
        assert client.get(f"/api/configs/{cfg_id}/shares").status_code == 404


# ------------------------------------------------------- permission tiers
def test_view_share_can_read_but_not_run_or_edit(tmp_path):
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        owner_id = _create_user(db_module, models_module, security_module, "owner4")
        _create_user(db_module, models_module, security_module, "viewer_friend")
        conn_id = _seed_owned_connection(db_module, models_module, owner_id, "Conn4")
        cfg_id, run_id = _seed_owned_config_and_run(db_module, models_module, owner_id, conn_id, "Cfg4")

        owner_csrf = _login(client, "owner4")
        assert _share(client, owner_csrf, cfg_id, "viewer_friend", "view").status_code == 201
        _logout(client)

        csrf = _login(client, "viewer_friend")
        assert client.get(f"/api/configs/{cfg_id}").status_code == 200
        assert client.get(f"/api/runs/{run_id}").status_code == 200
        assert client.get(f"/api/configs/{cfg_id}/status").status_code == 200

        r = client.put(f"/api/configs/{cfg_id}/tables", json={"rows": []}, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 403

        r = client.post(f"/api/configs/{cfg_id}/run", json={}, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 403

        r = client.post(f"/api/runs/{run_id}/cancel", headers={"X-CSRF-Token": csrf})
        assert r.status_code == 403


def test_run_share_can_trigger_run_but_not_edit_mappings(tmp_path):
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        owner_id = _create_user(db_module, models_module, security_module, "owner5")
        _create_user(db_module, models_module, security_module, "run_friend")
        conn_id = _seed_owned_connection(db_module, models_module, owner_id, "Conn5")
        cfg_id, run_id = _seed_owned_config_and_run(db_module, models_module, owner_id, conn_id, "Cfg5")

        owner_csrf = _login(client, "owner5")
        assert _share(client, owner_csrf, cfg_id, "run_friend", "run").status_code == 201
        _logout(client)

        csrf = _login(client, "run_friend")
        try:
            r = client.post(f"/api/runs/{run_id}/cancel", headers={"X-CSRF-Token": csrf})
            assert r.status_code == 200
        finally:
            # bus is a process-wide singleton (app/services/events_bus.py),
            # not reset between test modules -- and every test here gets a
            # FRESH sqlite db, so run_id restarts at 1 each time. Without this,
            # the cancel-request flag set above leaks into whichever other
            # test file's run happens to reuse run_id=1 next, making an
            # unrelated real run abort as "cancelled" instead of completing.
            from app.services.events_bus import bus
            bus.clear(run_id)

        r = client.put(f"/api/configs/{cfg_id}/tables", json={"rows": []}, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 403


def test_edit_share_can_change_table_mappings(tmp_path):
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        owner_id = _create_user(db_module, models_module, security_module, "owner6")
        _create_user(db_module, models_module, security_module, "edit_friend")
        conn_id = _seed_owned_connection(db_module, models_module, owner_id, "Conn6")
        cfg_id, _ = _seed_owned_config_and_run(db_module, models_module, owner_id, conn_id, "Cfg6")

        owner_csrf = _login(client, "owner6")
        assert _share(client, owner_csrf, cfg_id, "edit_friend", "edit").status_code == 201
        _logout(client)

        csrf = _login(client, "edit_friend")
        r = client.put(
            f"/api/configs/{cfg_id}/tables",
            json={"rows": [{"source_table": "t1", "target_table": "t1", "key_columns": ["id"]}]},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 200, r.text
        assert len(r.json()["tables"]) == 1


def test_user_with_no_share_gets_404_not_403(tmp_path):
    """Preserves the pre-sharing behavior: a stranger to a config gets a 404
    (existence hidden), never a 403 that would confirm the config exists."""
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        owner_id = _create_user(db_module, models_module, security_module, "owner7")
        _create_user(db_module, models_module, security_module, "stranger")
        conn_id = _seed_owned_connection(db_module, models_module, owner_id, "Conn7")
        cfg_id, run_id = _seed_owned_config_and_run(db_module, models_module, owner_id, conn_id, "Cfg7")

        _login(client, "stranger")
        assert client.get(f"/api/configs/{cfg_id}").status_code == 404
        assert client.get(f"/api/runs/{run_id}").status_code == 404


# ---------------------------------------------------------------- list view
def test_configs_list_shows_owned_and_shared_with_metadata(tmp_path):
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        owner_id = _create_user(db_module, models_module, security_module, "owner8")
        _create_user(db_module, models_module, security_module, "recipient8")
        conn_id = _seed_owned_connection(db_module, models_module, owner_id, "Conn8")
        cfg_id, _ = _seed_owned_config_and_run(db_module, models_module, owner_id, conn_id, "Cfg8")

        owner_csrf = _login(client, "owner8")
        assert _share(client, owner_csrf, cfg_id, "recipient8", "run").status_code == 201
        owned_list = client.get("/api/configs").json()
        assert owned_list[0]["is_mine"] is True
        assert owned_list[0]["share_count"] == 1
        assert owned_list[0]["shared_permission"] is None
        _logout(client)

        _login(client, "recipient8")
        shared_list = client.get("/api/configs").json()
        assert len(shared_list) == 1
        assert shared_list[0]["is_mine"] is False
        assert shared_list[0]["shared_permission"] == "run"
        assert shared_list[0]["owner_username"] == "owner8"
        assert shared_list[0]["share_count"] == 0  # not visible to non-owners


def test_shared_user_sees_connection_host_port_not_credentials(tmp_path):
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        owner_id = _create_user(db_module, models_module, security_module, "owner9")
        _create_user(db_module, models_module, security_module, "recipient9")
        conn_id = _seed_owned_connection(db_module, models_module, owner_id, "Conn9")
        cfg_id, _ = _seed_owned_config_and_run(db_module, models_module, owner_id, conn_id, "Cfg9")

        owner_csrf = _login(client, "owner9")
        assert _share(client, owner_csrf, cfg_id, "recipient9", "view").status_code == 201
        _logout(client)

        _login(client, "recipient9")
        detail = client.get(f"/api/configs/{cfg_id}").json()
        assert detail["source_connection"]["host"] == "dbhost.local"
        assert detail["source_connection"]["port"] == 5432
        assert "secret_encrypted" not in detail["source_connection"]
        assert "password" not in detail["source_connection"]


def test_admin_can_manage_shares_on_any_config(tmp_path):
    client, db_module, models_module, security_module = _make_app(tmp_path)
    with client:
        owner_id = _create_user(db_module, models_module, security_module, "owner10")
        _create_user(db_module, models_module, security_module, "root10", "admin")
        _create_user(db_module, models_module, security_module, "friend10")
        conn_id = _seed_owned_connection(db_module, models_module, owner_id, "Conn10")
        cfg_id, _ = _seed_owned_config_and_run(db_module, models_module, owner_id, conn_id, "Cfg10")

        admin_csrf = _login(client, "root10")
        assert _share(client, admin_csrf, cfg_id, "friend10", "view").status_code == 201
