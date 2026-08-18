"""B5 — MCP approve/dispatch/reject API gate tests (D6.4–D6.6, D7, D3–D6).

Tests the 4 FastAPI endpoints in routers/mcp_actions.py:

1. POST /api/mcp/propose — create a command proposal for human approval
2. POST /api/mcp/approvals — approve a pending proposal (step-up for HIGH/CRITICAL)
3. POST /api/mcp/approvals/{approval_id}/dispatch — dispatch an approved command
4. POST /api/mcp/proposals/{proposal_id}/reject — reject a pending proposal

Gate criteria:
- All 3 endpoints return correct status codes and JSON shapes for happy paths.
- Step-up matrix: HIGH/CRITICAL blocked without elevated session (403,
  ``step_up_required``); MEDIUM/LOW allowed with plain approver role.
- Adversarial cases fail closed: payload mutation, replay, expiry, revocation,
  interlock-paused.
- Service principals cannot hit any endpoint (403).
- Reject is idempotent-safe: rejecting an already-rejected/dispatched proposal
  returns 409.
- Every approve/dispatch/reject call is visible in the audit log.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Fixtures (mirror test_supervision_api.py patterns)
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Authenticated admin test client with isolated DB."""
    import database

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")

    from fastapi.testclient import TestClient
    from nautilus_fastapi import app

    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert r.status_code == 200, f"Login failed: {r.text}"
        token = r.json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c


@pytest.fixture
def viewer_client(tmp_path, monkeypatch):
    """Authenticated viewer test client with isolated DB."""
    import database

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")

    from fastapi.testclient import TestClient
    from nautilus_fastapi import app

    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert r.status_code == 200
        admin_token = r.json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {admin_token}"})
        c.post(
            "/api/users", json={"username": "viewer1", "password": "secret123", "role": "viewer"}
        )

        r = c.post("/api/auth/login", json={"username": "viewer1", "password": "secret123"})
        assert r.status_code == 200
        viewer_token = r.json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {viewer_token}"})
        yield c


@pytest.fixture
def operator_client(tmp_path, monkeypatch):
    """Authenticated operator test client with isolated DB."""
    import database

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")

    from fastapi.testclient import TestClient
    from nautilus_fastapi import app

    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert r.status_code == 200
        admin_token = r.json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {admin_token}"})
        c.post("/api/users", json={"username": "op1", "password": "secret123", "role": "operator"})

        r = c.post("/api/auth/login", json={"username": "op1", "password": "secret123"})
        assert r.status_code == 200
        op_token = r.json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {op_token}"})
        yield c


@pytest.fixture(autouse=True)
def reset_rate_limit_counters():
    try:
        import nautilus_fastapi

        nautilus_fastapi._login_counters.clear()
        nautilus_fastapi._global_counters.clear()
    except (ImportError, AttributeError):
        pass
    yield


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    """Route database and command modules to one writable DB per test."""
    import commands
    import database

    path = tmp_path / "nautilus-test.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    monkeypatch.setattr(commands, "DB_PATH", path)
    asyncio.run(database.init_db())
    asyncio.run(commands.init_commands_db())
    import stores

    monkeypatch.setattr(stores, "DB_PATH", path)
    asyncio.run(stores.init_stores_db())


@pytest.fixture(autouse=True)
def reset_adapter_state():
    """Clear the shared MCP adapter audit log and reset interlock before each test."""
    try:
        from state import mcp_adapter

        mcp_adapter.audit.clear()
        mcp_adapter.resume_interlock(actor="test-setup", reason="test reset")
    except (ImportError, AttributeError):
        pass
    yield


@pytest.fixture(autouse=True)
def reset_step_up():
    """Reset the step-up verifier to a clean TOTP verifier before each test."""
    from step_up import TOTPStepUpVerifier, set_step_up_verifier

    set_step_up_verifier(TOTPStepUpVerifier())
    yield


# ---------------------------------------------------------------------------
# Helpers — create proposals directly via the adapter
# ---------------------------------------------------------------------------


def _create_proposal(
    command_name: str = "flatten",
    target_agent_id: str = "agent-btc",
    payload: dict | None = None,
    requester: str = "supervision",
) -> str:
    """Create a proposal via the shared MCP adapter and return its ID."""
    from state import mcp_adapter

    if payload is None:
        payload = {"instrument": "BTC/USD.KRAKEN"}

    proposal = mcp_adapter.propose(
        command_name=command_name,
        target_agent_id=target_agent_id,
        requester=requester,
        payload=payload,
    )
    return proposal.proposal_id


def _create_low_risk_proposal() -> str:
    """Create a LOW-risk proposal (cancel_order) — no step-up needed."""
    return _create_proposal(
        command_name="cancel_order",
        payload={"client_order_id": "O-12345"},
    )


def _create_medium_risk_proposal() -> str:
    """Create a MEDIUM-risk proposal (start_strategy) — no step-up needed."""
    return _create_proposal(
        command_name="start_strategy",
        payload={"strategy_id": "ma_cross"},
    )


def _create_high_risk_proposal() -> str:
    """Create a HIGH-risk proposal (flatten) — step-up required."""
    return _create_proposal(
        command_name="flatten",
        payload={"instrument": "BTC/USD.KRAKEN"},
    )


def _create_critical_risk_proposal() -> str:
    """Create a CRITICAL-risk proposal (kill_switch) — step-up required."""
    return _create_proposal(
        command_name="kill_switch",
        payload={},
    )


def _set_elevated(principal: str = "admin") -> None:
    """Mark the principal as having an active elevated session."""
    from step_up import TOTPStepUpVerifier, get_step_up_verifier

    verifier = get_step_up_verifier()
    if isinstance(verifier, TOTPStepUpVerifier):
        verifier._elevated_until[principal] = float(time.time()) + 300.0


def _set_totp_secret(principal: str = "admin", secret: str = "JBSWY3DPEHPK3PXP") -> None:
    """Set a known TOTP secret for the principal."""
    from step_up import TOTPStepUpVerifier, get_step_up_verifier

    verifier = get_step_up_verifier()
    if isinstance(verifier, TOTPStepUpVerifier):
        verifier.set_secret(principal, secret)


def _generate_totp(secret: str = "JBSWY3DPEHPK3PXP") -> str:
    """Generate a valid TOTP code for the given secret."""
    from step_up import _totp_code

    return _totp_code(secret, int(time.time()))


# ---------------------------------------------------------------------------
# 0. POST /api/mcp/propose — create a proposal
# ---------------------------------------------------------------------------


class TestPropose:
    """POST /api/mcp/propose — create a command proposal."""

    def test_propose_low_risk_success(self, client):
        """LOW-risk proposal: created successfully with approver role."""
        r = client.post(
            "/api/mcp/propose",
            json={
                "command_name": "cancel_order",
                "target_agent_id": "agent-btc",
                "requester": "test",
                "payload": {"client_order_id": "O-12345"},
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert "proposal_id" in body
        assert body["command_name"] == "cancel_order"
        assert body["target_agent_id"] == "agent-btc"
        assert body["status"] == "pending"
        assert body["payload"] == {"client_order_id": "O-12345"}

    def test_propose_with_idempotency_key(self, client):
        """Propose with explicit idempotency key."""
        r = client.post(
            "/api/mcp/propose",
            json={
                "command_name": "cancel_order",
                "target_agent_id": "agent-btc",
                "requester": "test",
                "payload": {"client_order_id": "O-67890"},
                "idempotency_key": "test-key-123",
            },
        )
        assert r.status_code == 200
        assert r.json()["idempotency_key"] == "test-key-123"

    def test_propose_unknown_command_returns_400(self, client):
        """Unknown command name returns 400."""
        r = client.post(
            "/api/mcp/propose",
            json={
                "command_name": "nonexistent_command",
                "target_agent_id": "agent-btc",
                "requester": "test",
                "payload": {},
            },
        )
        assert r.status_code == 400

    def test_propose_interlock_paused_returns_409(self, client):
        """Propose fails closed (409) when interlock is PAUSED."""
        client.post("/api/supervision/interlock/engage", json={"reason": "test"})
        r = client.post(
            "/api/mcp/propose",
            json={
                "command_name": "cancel_order",
                "target_agent_id": "agent-btc",
                "requester": "test",
                "payload": {"client_order_id": "O-blocked"},
            },
        )
        assert r.status_code == 409

    def test_propose_viewer_blocked(self, viewer_client):
        """Viewer role cannot propose (403)."""
        r = viewer_client.post(
            "/api/mcp/propose",
            json={
                "command_name": "cancel_order",
                "target_agent_id": "agent-btc",
                "requester": "test",
                "payload": {"client_order_id": "O-viewer"},
            },
        )
        assert r.status_code == 403

    def test_propose_operator_blocked(self, operator_client):
        """Operator role cannot propose (403)."""
        r = operator_client.post(
            "/api/mcp/propose",
            json={
                "command_name": "cancel_order",
                "target_agent_id": "agent-btc",
                "requester": "test",
                "payload": {"client_order_id": "O-op"},
            },
        )
        assert r.status_code == 403

    def test_propose_audited(self, client):
        """Propose action is recorded in the audit log."""
        client.post(
            "/api/mcp/propose",
            json={
                "command_name": "cancel_order",
                "target_agent_id": "agent-btc",
                "requester": "test",
                "payload": {"client_order_id": "O-audit"},
            },
        )
        from state import mcp_adapter

        actions = [e["action"] for e in mcp_adapter.audit.entries()]
        assert "propose" in actions

    def test_propose_then_approve_full_flow(self, client):
        """Full flow: propose via API, then approve via API."""
        r1 = client.post(
            "/api/mcp/propose",
            json={
                "command_name": "cancel_order",
                "target_agent_id": "agent-btc",
                "requester": "test",
                "payload": {"client_order_id": "O-flow"},
            },
        )
        assert r1.status_code == 200
        pid = r1.json()["proposal_id"]

        r2 = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        assert r2.status_code == 200
        assert r2.json()["status"] == "active"


# ---------------------------------------------------------------------------
# 1. POST /api/mcp/approvals — approve
# ---------------------------------------------------------------------------


class TestApprove:
    """POST /api/mcp/approvals — approve a pending proposal."""

    def test_approve_low_risk_no_step_up(self, client):
        """LOW-risk proposal: plain approver role is sufficient."""
        pid = _create_low_risk_proposal()
        r = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        assert r.status_code == 200
        body = r.json()
        assert body["proposal_id"] == pid
        assert body["status"] == "active"
        assert "approval_id" in body
        assert "payload_hash" in body
        assert "expires_at" in body
        assert body["approver"] == "admin"

    def test_approve_medium_risk_no_step_up(self, client):
        """MEDIUM-risk proposal: plain approver role is sufficient."""
        pid = _create_medium_risk_proposal()
        r = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        assert r.status_code == 200
        assert r.json()["status"] == "active"

    def test_approve_high_risk_requires_step_up(self, client):
        """HIGH-risk proposal: 403 with step_up_required when not elevated."""
        pid = _create_high_risk_proposal()
        r = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        assert r.status_code == 403
        detail = r.json()["detail"]
        if isinstance(detail, dict):
            assert detail["reason"] == "step_up_required"
        else:
            assert "step_up_required" in str(detail)

    def test_approve_critical_risk_requires_step_up(self, client):
        """CRITICAL-risk proposal: 403 with step_up_required when not elevated."""
        pid = _create_critical_risk_proposal()
        r = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        assert r.status_code == 403

    def test_approve_high_risk_with_elevated_session(self, client):
        """HIGH-risk proposal: succeeds when elevated session is active."""
        _set_elevated("admin")
        pid = _create_high_risk_proposal()
        r = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        assert r.status_code == 200
        assert r.json()["status"] == "active"

    def test_approve_high_risk_with_valid_totp_code(self, client):
        """HIGH-risk proposal: succeeds with a valid TOTP step-up code."""
        _set_totp_secret("admin")
        pid = _create_high_risk_proposal()
        code = _generate_totp()
        r = client.post("/api/mcp/approvals", json={"proposal_id": pid, "step_up_code": code})
        assert r.status_code == 200
        assert r.json()["status"] == "active"

    def test_approve_high_risk_with_invalid_totp_code(self, client):
        """HIGH-risk proposal: 403 with step_up_required when TOTP code is wrong."""
        _set_totp_secret("admin")
        pid = _create_high_risk_proposal()
        r = client.post("/api/mcp/approvals", json={"proposal_id": pid, "step_up_code": "000000"})
        assert r.status_code == 403

    def test_approve_proposal_not_found(self, client):
        r = client.post("/api/mcp/approvals", json={"proposal_id": "PRP-NONEXISTENT"})
        assert r.status_code == 404

    def test_approve_non_pending_proposal_returns_410(self, client):
        """Approving an already-dispatched proposal returns 410."""
        _set_elevated("admin")
        pid = _create_high_risk_proposal()
        # Approve + dispatch first
        r1 = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        assert r1.status_code == 200
        aid = r1.json()["approval_id"]
        r2 = client.post(f"/api/mcp/approvals/{aid}/dispatch")
        assert r2.status_code == 200
        # Now try to approve again
        r3 = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        assert r3.status_code == 410

    def test_approve_viewer_blocked(self, viewer_client):
        """Viewer role cannot approve (403)."""
        pid = _create_low_risk_proposal()
        r = viewer_client.post("/api/mcp/approvals", json={"proposal_id": pid})
        assert r.status_code == 403

    def test_approve_operator_blocked(self, operator_client):
        """Operator role cannot approve (403)."""
        pid = _create_low_risk_proposal()
        r = operator_client.post("/api/mcp/approvals", json={"proposal_id": pid})
        assert r.status_code == 403

    def test_approve_audited(self, client):
        """Approve action is recorded in the audit log."""
        pid = _create_low_risk_proposal()
        client.post("/api/mcp/approvals", json={"proposal_id": pid})
        from state import mcp_adapter

        actions = [e["action"] for e in mcp_adapter.audit.entries()]
        assert "approve" in actions


# ---------------------------------------------------------------------------
# 2. POST /api/mcp/approvals/{approval_id}/dispatch — dispatch
# ---------------------------------------------------------------------------


class TestDispatch:
    """POST /api/mcp/approvals/{approval_id}/dispatch — dispatch an approved command."""

    def test_dispatch_low_risk_success(self, client):
        """Full flow: approve LOW-risk, then dispatch."""
        pid = _create_low_risk_proposal()
        r1 = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        assert r1.status_code == 200
        aid = r1.json()["approval_id"]

        r2 = client.post(f"/api/mcp/approvals/{aid}/dispatch")
        assert r2.status_code == 200
        body = r2.json()
        assert body["status"] == "dispatched"
        assert body["proposal_id"] == pid
        assert "dispatch_id" in body
        assert "dispatched_at" in body

    def test_dispatch_high_risk_with_step_up(self, client):
        """Full flow: approve HIGH-risk with step-up, then dispatch."""
        _set_elevated("admin")
        pid = _create_high_risk_proposal()
        r1 = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        assert r1.status_code == 200
        aid = r1.json()["approval_id"]

        r2 = client.post(f"/api/mcp/approvals/{aid}/dispatch")
        assert r2.status_code == 200
        assert r2.json()["status"] == "dispatched"

    def test_dispatch_approval_not_found(self, client):
        r = client.post("/api/mcp/approvals/APR-NONEXISTENT/dispatch")
        assert r.status_code == 404

    def test_dispatch_replay_fails(self, client):
        """Dispatching an already-consumed approval fails (409)."""
        pid = _create_low_risk_proposal()
        r1 = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        aid = r1.json()["approval_id"]

        r2 = client.post(f"/api/mcp/approvals/{aid}/dispatch")
        assert r2.status_code == 200

        r3 = client.post(f"/api/mcp/approvals/{aid}/dispatch")
        assert r3.status_code == 409

    def test_dispatch_high_risk_without_step_up_fails(self, client):
        """Dispatching a HIGH-risk approval without step-up fails (403)."""
        _set_elevated("admin")
        pid = _create_high_risk_proposal()
        r1 = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        assert r1.status_code == 200
        aid = r1.json()["approval_id"]

        # Revoke elevation before dispatch
        from step_up import get_step_up_verifier

        get_step_up_verifier().revoke_elevation("admin")

        r2 = client.post(f"/api/mcp/approvals/{aid}/dispatch")
        assert r2.status_code == 403

    def test_dispatch_audited(self, client):
        """Dispatch action is recorded in the audit log."""
        pid = _create_low_risk_proposal()
        r1 = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        aid = r1.json()["approval_id"]
        client.post(f"/api/mcp/approvals/{aid}/dispatch")

        from state import mcp_adapter

        actions = [e["action"] for e in mcp_adapter.audit.entries()]
        assert "dispatch" in actions

    def test_dispatch_viewer_blocked(self, viewer_client):
        """Viewer role cannot dispatch (403)."""
        r = viewer_client.post("/api/mcp/approvals/APR-WHATEVER/dispatch")
        assert r.status_code == 403

    def test_dispatch_operator_blocked(self, operator_client):
        """Operator role cannot dispatch (403)."""
        r = operator_client.post("/api/mcp/approvals/APR-WHATEVER/dispatch")
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# 3. POST /api/mcp/proposals/{proposal_id}/reject — reject
# ---------------------------------------------------------------------------


class TestReject:
    """POST /api/mcp/proposals/{proposal_id}/reject — reject a pending proposal."""

    def test_reject_pending_proposal(self, client):
        """Reject a pending proposal successfully."""
        pid = _create_low_risk_proposal()
        r = client.post(f"/api/mcp/proposals/{pid}/reject", json={"reason": "too risky"})
        assert r.status_code == 200
        body = r.json()
        assert body["proposal_id"] == pid
        assert body["status"] == "rejected"
        assert body["reason"] == "too risky"

    def test_reject_default_reason(self, client):
        """Reject with no body uses default reason."""
        pid = _create_low_risk_proposal()
        r = client.post(f"/api/mcp/proposals/{pid}/reject")
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"

    def test_reject_not_found(self, client):
        r = client.post("/api/mcp/proposals/PRP-NONEXISTENT/reject")
        assert r.status_code == 404

    def test_reject_already_rejected_returns_409(self, client):
        """Rejecting an already-rejected proposal returns 409."""
        pid = _create_low_risk_proposal()
        r1 = client.post(f"/api/mcp/proposals/{pid}/reject")
        assert r1.status_code == 200
        r2 = client.post(f"/api/mcp/proposals/{pid}/reject")
        assert r2.status_code == 409

    def test_reject_dispatched_returns_409(self, client):
        """Rejecting an already-dispatched proposal returns 409."""
        pid = _create_low_risk_proposal()
        r1 = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        aid = r1.json()["approval_id"]
        client.post(f"/api/mcp/approvals/{aid}/dispatch")

        r2 = client.post(f"/api/mcp/proposals/{pid}/reject")
        assert r2.status_code == 409

    def test_reject_no_step_up_required(self, client):
        """Reject does not require step-up even for CRITICAL-risk proposals."""
        pid = _create_critical_risk_proposal()
        r = client.post(f"/api/mcp/proposals/{pid}/reject")
        assert r.status_code == 200

    def test_reject_viewer_blocked(self, viewer_client):
        """Viewer role cannot reject (403)."""
        pid = _create_low_risk_proposal()
        r = viewer_client.post(f"/api/mcp/proposals/{pid}/reject")
        assert r.status_code == 403

    def test_reject_operator_blocked(self, operator_client):
        """Operator role cannot reject (403)."""
        pid = _create_low_risk_proposal()
        r = operator_client.post(f"/api/mcp/proposals/{pid}/reject")
        assert r.status_code == 403

    def test_reject_audited(self, client):
        """Reject action is recorded in the audit log."""
        pid = _create_low_risk_proposal()
        client.post(f"/api/mcp/proposals/{pid}/reject")
        from state import mcp_adapter

        actions = [e["action"] for e in mcp_adapter.audit.entries()]
        assert "reject" in actions


# ---------------------------------------------------------------------------
# 4. Adversarial tests — fail-closed semantics
# ---------------------------------------------------------------------------


class TestAdversarial:
    """Adversarial cases that must fail closed (D5, D6.2)."""

    def test_interlock_engage_revokes_approval_before_dispatch(self, client):
        """Engaging interlock between approve and dispatch blocks dispatch (D5)."""
        pid = _create_low_risk_proposal()
        r1 = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        aid = r1.json()["approval_id"]

        # Engage interlock — revokes all active approvals
        client.post("/api/supervision/interlock/engage", json={"reason": "emergency"})

        # Dispatch should fail — approval was revoked
        r2 = client.post(f"/api/mcp/approvals/{aid}/dispatch")
        assert r2.status_code == 409

    def test_dispatch_expired_approval_fails(self, client):
        """Dispatching an expired approval fails (409)."""
        pid = _create_low_risk_proposal()
        r1 = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        aid = r1.json()["approval_id"]
        assert r1.status_code == 200

        # Manually expire the approval in the DB
        import aiosqlite
        from stores import DB_PATH

        async def _expire():
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE approvals SET status='expired' WHERE approval_id=?",
                    (aid,),
                )
                await db.commit()

        asyncio.run(_expire())

        r2 = client.post(f"/api/mcp/approvals/{aid}/dispatch")
        assert r2.status_code == 409

    def test_approve_expired_proposal_fails(self, client):
        """Approving an expired proposal fails (410)."""
        pid = _create_low_risk_proposal()

        # Manually expire the proposal in the DB
        import aiosqlite
        from stores import DB_PATH

        async def _expire():
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE proposals SET status='expired' WHERE proposal_id=?",
                    (pid,),
                )
                await db.commit()

        asyncio.run(_expire())

        r = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        assert r.status_code == 410

    def test_totp_replay_rejected(self, client):
        """A replayed TOTP code is rejected (replay protection, D6.5).

        The first successful verify() grants a 5-minute elevated session.
        To test code replay specifically, we revoke the elevated session
        between calls so the second call must re-verify the same code.
        """
        _set_totp_secret("admin")
        pid = _create_high_risk_proposal()
        code = _generate_totp()

        # First use of the code — should succeed and grant elevation
        r1 = client.post("/api/mcp/approvals", json={"proposal_id": pid, "step_up_code": code})
        assert r1.status_code == 200

        # Revoke the elevated session so the second call must re-verify
        from step_up import get_step_up_verifier

        get_step_up_verifier().revoke_elevation("admin")

        # Create another HIGH-risk proposal and try to reuse the same code
        pid2 = _create_high_risk_proposal()
        r2 = client.post("/api/mcp/approvals", json={"proposal_id": pid2, "step_up_code": code})
        assert r2.status_code == 403

    def test_service_principal_cannot_approve(self, client):
        """A service-principal JWT cannot hit the approve endpoint (403)."""
        from auth_jwt import create_access_token

        service_token = create_access_token(
            {
                "sub": "service:supervisor",
                "role": "operator",
                "principal_type": "service",
            }
        )
        pid = _create_low_risk_proposal()
        r = client.post(
            "/api/mcp/approvals",
            json={"proposal_id": pid},
            headers={"Authorization": f"Bearer {service_token}"},
        )
        assert r.status_code == 403

    def test_service_principal_cannot_dispatch(self, client):
        """A service-principal JWT cannot hit the dispatch endpoint (403)."""
        from auth_jwt import create_access_token

        service_token = create_access_token(
            {
                "sub": "service:supervisor",
                "role": "operator",
                "principal_type": "service",
            }
        )
        r = client.post(
            "/api/mcp/approvals/APR-WHATEVER/dispatch",
            headers={"Authorization": f"Bearer {service_token}"},
        )
        assert r.status_code == 403

    def test_service_principal_cannot_reject(self, client):
        """A service-principal JWT cannot hit the reject endpoint (403)."""
        from auth_jwt import create_access_token

        service_token = create_access_token(
            {
                "sub": "service:supervisor",
                "role": "operator",
                "principal_type": "service",
            }
        )
        pid = _create_low_risk_proposal()
        r = client.post(
            f"/api/mcp/proposals/{pid}/reject",
            headers={"Authorization": f"Bearer {service_token}"},
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# 5. Audit visibility — all actions recorded
# ---------------------------------------------------------------------------


class TestAuditVisibility:
    """Every approve/dispatch/reject call is visible in the audit log."""

    def test_full_approve_dispatch_flow_audited(self, client):
        """Approve + dispatch both appear in the audit log."""
        pid = _create_low_risk_proposal()
        r1 = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        aid = r1.json()["approval_id"]
        client.post(f"/api/mcp/approvals/{aid}/dispatch")

        from state import mcp_adapter

        actions = [e["action"] for e in mcp_adapter.audit.entries()]
        assert "approve" in actions
        assert "dispatch" in actions

    def test_reject_audited_with_reason(self, client):
        """Reject audit entry includes the reason."""
        pid = _create_low_risk_proposal()
        client.post(f"/api/mcp/proposals/{pid}/reject", json={"reason": "test reason"})

        from state import mcp_adapter

        reject_entries = [e for e in mcp_adapter.audit.entries() if e["action"] == "reject"]
        assert len(reject_entries) == 1
        assert reject_entries[0]["detail"].get("reason") == "test reason"
