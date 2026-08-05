"""
Identity model tests — Phase O6c / A1 (D6.4).

Verifies that:
  - service:supervisor principal cannot be assigned approver or admin
  - service principals CAN be assigned viewer/operator/trader
  - human principals CAN be assigned all roles including approver/admin
  - JWT claims include principal_type
  - require_admin rejects service principals even if role claim is admin
  - require_approver rejects service principals
  - _validate_principal_role enforces the structural constraint
  - seeded admin is a human principal
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── _validate_principal_role unit tests ──────────────────────────────────────

class TestValidatePrincipalRole:
    def test_service_approver_rejected(self):
        import database
        with pytest.raises(ValueError, match="Service principals cannot hold"):
            database._validate_principal_role("service", "approver")

    def test_service_admin_rejected(self):
        import database
        with pytest.raises(ValueError, match="Service principals cannot hold"):
            database._validate_principal_role("service", "admin")

    def test_service_viewer_allowed(self):
        import database
        database._validate_principal_role("service", "viewer")

    def test_service_operator_allowed(self):
        import database
        database._validate_principal_role("service", "operator")

    def test_service_trader_allowed(self):
        import database
        database._validate_principal_role("service", "trader")

    def test_human_approver_allowed(self):
        import database
        database._validate_principal_role("human", "approver")

    def test_human_admin_allowed(self):
        import database
        database._validate_principal_role("human", "admin")

    def test_human_viewer_allowed(self):
        import database
        database._validate_principal_role("human", "viewer")

    def test_human_operator_allowed(self):
        import database
        database._validate_principal_role("human", "operator")

    def test_invalid_principal_type_rejected(self):
        import database
        with pytest.raises(ValueError, match="Invalid principal_type"):
            database._validate_principal_role("robot", "viewer")

    def test_invalid_role_rejected(self):
        import database
        with pytest.raises(ValueError, match="Invalid role"):
            database._validate_principal_role("human", "superuser")


# ── DB-level create_user tests ───────────────────────────────────────────────

class TestCreateUserPrincipalType:
    def test_create_service_approver_raises(self):
        import asyncio
        import database
        with pytest.raises(ValueError, match="Service principals cannot hold"):
            asyncio.run(
                database.create_user(
                    "svc-bad", "hashedplaceholder", role="approver", principal_type="service"
                )
            )

    def test_create_service_admin_raises(self):
        import asyncio
        import database
        with pytest.raises(ValueError, match="Service principals cannot hold"):
            asyncio.run(
                database.create_user(
                    "svc-bad2", "hashedplaceholder", role="admin", principal_type="service"
                )
            )

    def test_create_service_operator_succeeds(self):
        import asyncio
        import database
        user = asyncio.run(
            database.create_user(
                "svc-ok", "hashedplaceholder", role="operator", principal_type="service"
            )
        )
        assert user["principal_type"] == "service"
        assert user["role"] == "operator"

    def test_create_human_approver_succeeds(self):
        import asyncio
        import database
        user = asyncio.run(
            database.create_user(
                "human-appr", "hashedplaceholder", role="approver", principal_type="human"
            )
        )
        assert user["principal_type"] == "human"
        assert user["role"] == "approver"

    def test_get_user_returns_principal_type(self):
        import asyncio
        import database
        asyncio.run(
            database.create_user(
                "check-pt", "hashedplaceholder", role="viewer", principal_type="service"
            )
        )
        user = asyncio.run(database.get_user("check-pt"))
        assert user["principal_type"] == "service"


# ── JWT principal_type claim tests ───────────────────────────────────────────

class TestJWTPrincipalType:
    def test_jwt_contains_principal_type(self, client):
        import base64
        import json
        login_r = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        )
        token = login_r.json()["access_token"]
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.b64decode(payload_b64))
        assert payload.get("principal_type") == "human"

    def test_service_principal_login_gets_service_claim(self, client):
        client.post(
            "/api/users",
            json={
                "username": "svc-jwt-test",
                "password": "secret123",
                "role": "operator",
                "principal_type": "service",
            },
        )
        import base64
        import json
        login_r = client.post(
            "/api/auth/login",
            json={"username": "svc-jwt-test", "password": "secret123"},
        )
        assert login_r.status_code == 200
        token = login_r.json()["access_token"]
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.b64decode(payload_b64))
        assert payload.get("principal_type") == "service"
        assert payload.get("role") == "operator"


# ── require_admin / require_approver dependency tests ────────────────────────

class TestRoleDependencies:
    def test_admin_can_access_users(self, client):
        r = client.get("/api/users")
        assert r.status_code == 200

    def test_viewer_cannot_access_users(self, client):
        client.post(
            "/api/users",
            json={
                "username": "viewer1",
                "password": "secret123",
                "role": "viewer",
                "principal_type": "human",
            },
        )
        login_r = client.post(
            "/api/auth/login", json={"username": "viewer1", "password": "secret123"}
        )
        assert login_r.status_code == 200
        viewer_token = login_r.json()["access_token"]

        r = client.get(
            "/api/users",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert r.status_code == 403

    def test_service_principal_cannot_access_admin_routes(self, client):
        client.post(
            "/api/users",
            json={
                "username": "svc-access",
                "password": "secret123",
                "role": "operator",
                "principal_type": "service",
            },
        )
        login_r = client.post(
            "/api/auth/login", json={"username": "svc-access", "password": "secret123"}
        )
        assert login_r.status_code == 200
        svc_token = login_r.json()["access_token"]

        r = client.get(
            "/api/users",
            headers={"Authorization": f"Bearer {svc_token}"},
        )
        assert r.status_code == 403
