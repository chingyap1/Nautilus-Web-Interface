"""
Tests for /api/users — user management endpoints.

Coverage:
  - GET  /api/users          — list users (admin only)
  - POST /api/users          — create user
  - DELETE /api/users/{id}   — deactivate user
  - POST /api/users/{id}/password — change password

Admin seeded by init_db on startup; non-admin JWT used to test 403s.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── List users ───────────────────────────────────────────────────────────────

def test_list_users_contains_admin(client):
    r = client.get("/api/users")
    assert r.status_code == 200
    body = r.json()
    assert "users" in body
    usernames = [u["username"] for u in body["users"]]
    assert "admin" in usernames


def test_list_users_no_passwords(client):
    r = client.get("/api/users")
    assert r.status_code == 200
    for user in r.json()["users"]:
        assert "hashed_password" not in user


# ── Create user ──────────────────────────────────────────────────────────────

def test_create_user_returns_201(client):
    r = client.post("/api/users", json={"username": "trader1", "password": "secret123", "role": "trader"})
    assert r.status_code == 201
    body = r.json()
    assert body["success"] is True
    assert body["user"]["username"] == "trader1"
    assert body["user"]["role"] == "trader"


def test_create_user_appears_in_list(client):
    client.post("/api/users", json={"username": "trader2", "password": "secret123", "role": "trader"})
    r = client.get("/api/users")
    usernames = [u["username"] for u in r.json()["users"]]
    assert "trader2" in usernames


def test_create_duplicate_username_returns_409(client):
    client.post("/api/users", json={"username": "dupuser", "password": "secret123", "role": "trader"})
    r = client.post("/api/users", json={"username": "dupuser", "password": "secret123", "role": "trader"})
    assert r.status_code == 409


def test_create_user_short_password_returns_422(client):
    r = client.post("/api/users", json={"username": "badpwuser", "password": "abc", "role": "trader"})
    assert r.status_code == 422


def test_create_user_invalid_role_returns_422(client):
    r = client.post("/api/users", json={"username": "baduser", "password": "secret123", "role": "superuser"})
    assert r.status_code == 422


def test_create_service_principal_approver_returns_422(client):
    """Service principals cannot be assigned approver role (D6.4)."""
    r = client.post(
        "/api/users",
        json={
            "username": "svc-test",
            "password": "secret123",
            "role": "approver",
            "principal_type": "service",
        },
    )
    assert r.status_code == 422


def test_create_service_principal_admin_returns_422(client):
    """Service principals cannot be assigned admin role (D6.4)."""
    r = client.post(
        "/api/users",
        json={
            "username": "svc-admin",
            "password": "secret123",
            "role": "admin",
            "principal_type": "service",
        },
    )
    assert r.status_code == 422


def test_create_service_principal_operator_succeeds(client):
    """Service principals can be assigned operator role (D6.4)."""
    r = client.post(
        "/api/users",
        json={
            "username": "svc-supervisor",
            "password": "secret123",
            "role": "operator",
            "principal_type": "service",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["user"]["principal_type"] == "service"
    assert body["user"]["role"] == "operator"


def test_create_service_principal_viewer_succeeds(client):
    """Service principals can be assigned viewer role (D6.4)."""
    r = client.post(
        "/api/users",
        json={
            "username": "svc-viewer",
            "password": "secret123",
            "role": "viewer",
            "principal_type": "service",
        },
    )
    assert r.status_code == 201
    assert r.json()["user"]["principal_type"] == "service"


def test_create_human_approver_succeeds(client):
    """Human principals can be assigned approver role (D6.4)."""
    r = client.post(
        "/api/users",
        json={
            "username": "human-approver",
            "password": "secret123",
            "role": "approver",
            "principal_type": "human",
        },
    )
    assert r.status_code == 201
    assert r.json()["user"]["role"] == "approver"
    assert r.json()["user"]["principal_type"] == "human"


def test_list_users_includes_principal_type(client):
    """List users should include principal_type field."""
    r = client.get("/api/users")
    assert r.status_code == 200
    for user in r.json()["users"]:
        assert "principal_type" in user


def test_seeded_admin_is_human_principal(client):
    """The seeded admin user must be a human principal (D6.4)."""
    r = client.get("/api/users")
    assert r.status_code == 200
    admin_user = [u for u in r.json()["users"] if u["username"] == "admin"][0]
    assert admin_user["principal_type"] == "human"
    assert admin_user["role"] == "admin"


# ── Delete user ──────────────────────────────────────────────────────────────

def test_delete_user_removes_from_list(client):
    create_r = client.post("/api/users", json={"username": "todel", "password": "secret123", "role": "trader"})
    user_id = create_r.json()["user"]["id"]

    del_r = client.delete(f"/api/users/{user_id}")
    assert del_r.status_code == 200
    assert del_r.json()["success"] is True

    r = client.get("/api/users")
    usernames = [u["username"] for u in r.json()["users"] if u["is_active"]]
    assert "todel" not in usernames


def test_delete_nonexistent_user_returns_404(client):
    r = client.delete("/api/users/USR-NONEXISTENT")
    assert r.status_code == 404


# ── Change password ──────────────────────────────────────────────────────────

def test_change_password_allows_new_login(client):
    create_r = client.post("/api/users", json={"username": "pwtest", "password": "oldpass123", "role": "trader"})
    user_id = create_r.json()["user"]["id"]

    r = client.post(f"/api/users/{user_id}/password", json={"password": "newpass456"})
    assert r.status_code == 200
    assert r.json()["success"] is True

    # New credentials should work for login
    login_r = client.post("/api/auth/login", json={"username": "pwtest", "password": "newpass456"})
    assert login_r.status_code == 200


def test_change_password_old_credentials_fail(client):
    create_r = client.post("/api/users", json={"username": "pwtest2", "password": "oldpass123", "role": "trader"})
    user_id = create_r.json()["user"]["id"]

    client.post(f"/api/users/{user_id}/password", json={"password": "newpass456"})

    login_r = client.post("/api/auth/login", json={"username": "pwtest2", "password": "oldpass123"})
    assert login_r.status_code == 401


def test_change_password_nonexistent_user_returns_404(client):
    r = client.post("/api/users/USR-NONEXISTENT/password", json={"password": "newpass456"})
    assert r.status_code == 404


# ── Auth guard ───────────────────────────────────────────────────────────────

def test_list_users_requires_auth(tmp_path, monkeypatch):
    import database
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test2.db")
    from fastapi.testclient import TestClient
    from nautilus_fastapi import app
    with TestClient(app) as c:
        r = c.get("/api/users")
        assert r.status_code == 401


def test_create_user_requires_auth(tmp_path, monkeypatch):
    import database
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test3.db")
    from fastapi.testclient import TestClient
    from nautilus_fastapi import app
    with TestClient(app) as c:
        r = c.post("/api/users", json={"username": "x", "password": "secret123", "role": "trader"})
        assert r.status_code == 401


# ── Login improvements ───────────────────────────────────────────────────────

def test_login_returns_role(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


def test_login_new_user_succeeds_after_create(client):
    client.post("/api/users", json={"username": "logintest", "password": "secret123", "role": "trader"})
    r = client.post("/api/auth/login", json={"username": "logintest", "password": "secret123"})
    assert r.status_code == 200
    assert "access_token" in r.json()
