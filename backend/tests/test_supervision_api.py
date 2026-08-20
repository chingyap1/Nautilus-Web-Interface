"""B1 — NWI supervision API gate tests (D7, D9, D3–D6).

Tests the 5 FastAPI endpoints in routers/supervision.py:

1. POST /api/supervision/inspect — inspect an agent, optionally create proposal
2. GET /api/supervision/interlock — return interlock state
3. POST /api/supervision/interlock/engage — engage interlock (operator+)
4. POST /api/supervision/interlock/resume — resume interlock (admin only)
5. GET /api/supervision/proposals — list supervision-originated proposals

Gate criteria:
- All 5 endpoints return correct status codes and JSON shapes.
- InterlockPausedError → HTTP 409.
- Role enforcement: viewer can inspect but not engage/resume; operator can
  engage but not resume; admin can resume.
- No endpoint calls dispatch() or approve() — audit log contains no dispatch
  or approve actions after inspect calls.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from routers.supervision import _resume_precondition_failure as _real_resume_preconditions  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def _make_log_dir(tmp_path: Path) -> Path:
    """Create a minimal log directory with a healthy heartbeat for XBTUSD."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    hb = {
        "agent_id": "agent-btc",
        "pair": "XBTUSD",
        "strategy": "ma_cross",
        "interval": "1h",
        "started_at": "2026-01-15T10:00:00Z",
        "last_heartbeat": NOW.isoformat().replace("+00:00", "Z"),
        "status": "running",
        "execution_mode": "paper",
        "num_fills": 0,
        "balance_usd": 100_000.0,
        "unrealised_pnl": 0.0,
        "open_positions": 0,
    }
    (log_dir / "heartbeat_XBTUSD.json").write_text(json.dumps(hb))
    return log_dir


def _make_critical_log_dir(tmp_path: Path) -> Path:
    """Create a log directory with critical drawdown to trigger FLATTEN."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    hb = {
        "agent_id": "agent-btc",
        "pair": "XBTUSD",
        "strategy": "ma_cross",
        "interval": "1h",
        "started_at": "2026-01-15T10:00:00Z",
        "last_heartbeat": NOW.isoformat().replace("+00:00", "Z"),
        "status": "running",
        "execution_mode": "paper",
        "num_fills": 12,
        "balance_usd": 100_000.0,
        "unrealised_pnl": -30000.0,
        "open_positions": 1,
    }
    (log_dir / "heartbeat_XBTUSD.json").write_text(json.dumps(hb))
    (log_dir / "pnl_XBTUSD_20260115_100000.csv").write_text(
        "timestamp,balance_usd,unrealised_pnl,open_positions\n"
        "2026-01-15T10:00:00+00:00,100000.00,0.00,0\n"
        "2026-01-15T11:00:00+00:00,100000.00,-30000.00,1\n"
        "2026-01-15T12:00:00+00:00,100000.00,-30000.00,1\n"
    )
    (log_dir / "fills_XBTUSD_20260115_100000.csv").write_text(
        "timestamp,side,quantity,price,commission_currency,commission\n"
        "2026-01-15T10:00:00+00:00,BUY,0.001,95000.0,USD,0.13\n"
        "2026-01-15T11:00:00+00:00,SELL,0.001,96000.0,USD,0.13\n"
    )
    return log_dir


@pytest.mark.parametrize(
    ("agents", "expected"),
    [
        ([], "agent_unavailable"),
        (
            [
                {
                    "agent_id": "other-agent",
                    "freshness": "online",
                    "reconciled": True,
                    "kill_switch": False,
                }
            ],
            "agent_unavailable",
        ),
        ([{"agent_id": "agent-btc", "freshness": "stale"}], "agent_offline"),
        (
            [
                {
                    "agent_id": "agent-btc",
                    "freshness": "online",
                    "execution_mode": "paper",
                    "reconciled": False,
                }
            ],
            "account_not_reconciled",
        ),
        (
            [
                {
                    "agent_id": "agent-btc",
                    "freshness": "online",
                    "execution_mode": "live",
                    "reconciled": True,
                    "kill_switch": False,
                }
            ],
            "execution_mode_not_paper",
        ),
        (
            [
                {
                    "agent_id": "agent-btc",
                    "freshness": "online",
                    "execution_mode": "paper",
                    "reconciled": True,
                }
            ],
            "kill_switch_active",
        ),
        (
            [
                {
                    "agent_id": "agent-btc",
                    "freshness": "online",
                    "execution_mode": "paper",
                    "reconciled": True,
                    "kill_switch": False,
                }
            ],
            None,
        ),
    ],
)
def test_resume_preconditions_fail_closed(agents, expected, monkeypatch):
    from routers import system

    monkeypatch.setattr(system, "_load_agent_heartbeats", lambda: agents)

    assert _real_resume_preconditions() == expected


# ---------------------------------------------------------------------------
# Fixtures
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
        # Create a viewer user
        r = c.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert r.status_code == 200
        admin_token = r.json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {admin_token}"})
        c.post("/api/users", json={"username": "viewer1", "password": "secret123", "role": "viewer"})

        # Login as viewer
        r = c.post("/api/auth/login", json={"username": "viewer1", "password": "secret123"})
        assert r.status_code == 200, f"Viewer login failed: {r.text}"
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
        assert r.status_code == 200, f"Operator login failed: {r.text}"
        op_token = r.json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {op_token}"})
        yield c


@pytest.fixture(autouse=True)
def reset_rate_limit_counters():
    """Clear in-memory rate-limit state before every test."""
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
def reset_step_up():
    """Reset the step-up verifier and seed admin TOTP secret before each test."""
    from step_up import TOTPStepUpVerifier, set_step_up_verifier
    verifier = TOTPStepUpVerifier()
    verifier.set_secret("admin", "JBSWY3DPEHPK3PXP")
    set_step_up_verifier(verifier)
    yield


@pytest.fixture(autouse=True)
def healthy_resume_preconditions(monkeypatch):
    from routers import supervision

    monkeypatch.setattr(supervision, "_resume_precondition_failure", lambda: None)


@pytest.fixture(autouse=True)
def reset_adapter_state():
    """Clear the shared MCP adapter audit log and reset interlock before each test."""
    try:
        from state import mcp_adapter
        mcp_adapter.audit.clear()
        # Reset interlock to RESUMED for test isolation (direct adapter call,
        # bypasses HTTP step-up enforcement — this is test setup, not a user action)
        mcp_adapter.resume_interlock(actor="test-setup", reason="test reset")
    except (ImportError, AttributeError):
        pass
    yield


# ---------------------------------------------------------------------------
# 1. POST /api/supervision/inspect
# ---------------------------------------------------------------------------


class TestInspect:
    """POST /api/supervision/inspect — inspect an agent."""

    def test_inspect_healthy_agent_returns_200(self, client, tmp_path):
        """Inspecting a healthy agent returns health, metrics, recommendation."""
        log_dir = _make_log_dir(tmp_path)
        with mock.patch("supervision.health.datetime") as m:
            m.now.return_value = NOW
            m.fromisoformat = datetime.fromisoformat
            r = client.post("/api/supervision/inspect", json={
                "pair": "XBTUSD",
                "log_dir": str(log_dir),
            })

        assert r.status_code == 200
        body = r.json()
        assert "health" in body
        assert "metrics" in body
        assert "recommendation" in body
        assert body["recommendation"]["kind"] == "none"
        assert body.get("proposal") is None

    def test_inspect_critical_agent_creates_proposal(self, client, tmp_path):
        """Inspecting an agent with critical drawdown creates a FLATTEN proposal."""
        log_dir = _make_critical_log_dir(tmp_path)
        with mock.patch("supervision.health.datetime") as m:
            m.now.return_value = NOW
            m.fromisoformat = datetime.fromisoformat
            r = client.post("/api/supervision/inspect", json={
                "pair": "XBTUSD",
                "log_dir": str(log_dir),
            })

        assert r.status_code == 200
        body = r.json()
        assert body["recommendation"]["kind"] == "flatten"
        assert body.get("proposal") is not None
        assert body["proposal"]["command_name"] == "flatten"

    def test_inspect_agent_offline_returns_404(self, client, tmp_path):
        """Inspecting a non-existent agent (no heartbeat) returns 404."""
        empty_dir = tmp_path / "empty_logs"
        empty_dir.mkdir()
        r = client.post("/api/supervision/inspect", json={
            "pair": "XBTUSD",
            "log_dir": str(empty_dir),
        })
        assert r.status_code == 404
        assert "offline" in r.json()["detail"].lower()

    def test_inspect_with_interlock_paused_returns_409(self, client, tmp_path):
        """Inspecting when interlock is PAUSED and recommendation is actionable → 409."""
        log_dir = _make_critical_log_dir(tmp_path)

        # Engage interlock first
        client.post("/api/supervision/interlock/engage", json={"reason": "test"})

        with mock.patch("supervision.health.datetime") as m:
            m.now.return_value = NOW
            m.fromisoformat = datetime.fromisoformat
            r = client.post("/api/supervision/inspect", json={
                "pair": "XBTUSD",
                "log_dir": str(log_dir),
            })

        assert r.status_code == 409
        assert "paused" in r.json()["detail"].lower()

    def test_inspect_advisory_works_with_interlock_paused(self, client, tmp_path):
        """Advisory recommendation (NONE) works even when interlock is paused."""
        log_dir = _make_log_dir(tmp_path)

        # Engage interlock
        client.post("/api/supervision/interlock/engage", json={"reason": "test"})

        with mock.patch("supervision.health.datetime") as m:
            m.now.return_value = NOW
            m.fromisoformat = datetime.fromisoformat
            r = client.post("/api/supervision/inspect", json={
                "pair": "XBTUSD",
                "log_dir": str(log_dir),
            })

        assert r.status_code == 200
        body = r.json()
        assert body["recommendation"]["kind"] == "none"
        assert body.get("proposal") is None

    def test_inspect_no_dispatch_in_audit(self, client, tmp_path):
        """After inspect, audit log contains no dispatch or approve actions."""
        log_dir = _make_critical_log_dir(tmp_path)
        with mock.patch("supervision.health.datetime") as m:
            m.now.return_value = NOW
            m.fromisoformat = datetime.fromisoformat
            client.post("/api/supervision/inspect", json={
                "pair": "XBTUSD",
                "log_dir": str(log_dir),
            })

        from state import mcp_adapter
        actions = [e["action"] for e in mcp_adapter.audit.entries()]
        assert "dispatch" not in actions
        assert "approve" not in actions
        assert "supervision_propose" in actions

    def test_inspect_viewer_allowed(self, viewer_client, tmp_path):
        """Viewer role can call inspect (requires viewer or higher)."""
        log_dir = _make_log_dir(tmp_path)
        with mock.patch("supervision.health.datetime") as m:
            m.now.return_value = NOW
            m.fromisoformat = datetime.fromisoformat
            r = viewer_client.post("/api/supervision/inspect", json={
                "pair": "XBTUSD",
                "log_dir": str(log_dir),
            })

        assert r.status_code == 200
        assert r.json()["recommendation"]["kind"] == "none"


# ---------------------------------------------------------------------------
# 2. GET /api/supervision/interlock
# ---------------------------------------------------------------------------


class TestInterlockGet:
    """GET /api/supervision/interlock — return interlock state."""

    def test_get_interlock_returns_state(self, client):
        r = client.get("/api/supervision/interlock")
        assert r.status_code == 200
        body = r.json()
        assert "state" in body
        assert body["state"] in ("paused", "resumed")

    def test_get_interlock_after_engage_returns_paused(self, client):
        client.post("/api/supervision/interlock/engage", json={"reason": "test"})
        r = client.get("/api/supervision/interlock")
        assert r.status_code == 200
        assert r.json()["state"] == "paused"

    def test_get_interlock_returns_extended_fields_after_engage(self, client):
        """Extended response includes actor, reason, updated_at, lease_seconds."""
        client.post("/api/supervision/interlock/engage", json={"reason": "emergency stop"})
        r = client.get("/api/supervision/interlock")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "paused"
        assert body["reason"] == "emergency stop"
        assert "actor" in body
        assert "updated_at" in body
        assert "lease_seconds" in body
        assert isinstance(body["lease_seconds"], (int, float))

    def test_get_interlock_viewer_allowed(self, viewer_client):
        r = viewer_client.get("/api/supervision/interlock")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# 3. POST /api/supervision/interlock/engage
# ---------------------------------------------------------------------------


class TestInterlockEngage:
    """POST /api/supervision/interlock/engage — engage interlock (operator+)."""

    def test_engage_as_admin_returns_200(self, client):
        r = client.post("/api/supervision/interlock/engage", json={"reason": "emergency"})
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "paused"
        assert body["reason"] == "emergency"

    def test_engage_as_operator_returns_200(self, operator_client):
        r = operator_client.post("/api/supervision/interlock/engage", json={"reason": "op stop"})
        assert r.status_code == 200
        assert r.json()["state"] == "paused"

    def test_engage_as_viewer_returns_403(self, viewer_client):
        r = viewer_client.post("/api/supervision/interlock/engage", json={"reason": "test"})
        assert r.status_code == 403

    def test_engage_default_reason(self, client):
        """Engage with no body uses default reason."""
        r = client.post("/api/supervision/interlock/engage")
        assert r.status_code == 200
        assert r.json()["state"] == "paused"


# ---------------------------------------------------------------------------
# 4. POST /api/supervision/interlock/resume
# ---------------------------------------------------------------------------


class TestInterlockResume:
    """POST /api/supervision/interlock/resume — resume interlock (admin only)."""

    def test_resume_as_admin_returns_200(self, client):
        from step_up import _totp_code
        # First engage
        client.post("/api/supervision/interlock/engage", json={"reason": "stop"})
        # Then resume with step-up code
        code = _totp_code("JBSWY3DPEHPK3PXP", int(time.time()))
        r = client.post("/api/supervision/interlock/resume", json={"reason": "all clear", "step_up_code": code})
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "resumed"
        assert body["reason"] == "all clear"

    def test_resume_as_operator_returns_403(self, operator_client):
        r = operator_client.post("/api/supervision/interlock/resume", json={"reason": "resume"})
        assert r.status_code == 403

    def test_resume_as_viewer_returns_403(self, viewer_client):
        r = viewer_client.post("/api/supervision/interlock/resume", json={"reason": "resume"})
        assert r.status_code == 403

    def test_resume_without_step_up_returns_403(self, client):
        """Resume without step-up code returns 403 (D6.6)."""
        client.post("/api/supervision/interlock/engage", json={"reason": "stop"})
        r = client.post("/api/supervision/interlock/resume", json={"reason": "all clear"})
        assert r.status_code == 403
        detail = r.json()["detail"]
        if isinstance(detail, dict):
            assert detail["reason"] == "step_up_required"
        else:
            assert "step_up_required" in str(detail)

    def test_resume_default_reason_with_step_up(self, client):
        from step_up import _totp_code
        client.post("/api/supervision/interlock/engage", json={"reason": "stop"})
        code = _totp_code("JBSWY3DPEHPK3PXP", int(time.time()))
        r = client.post("/api/supervision/interlock/resume", json={"step_up_code": code})
        assert r.status_code == 200
        assert r.json()["state"] == "resumed"

    def test_resume_rejects_failed_agent_precondition(self, client, monkeypatch):
        from routers import supervision
        from step_up import _totp_code

        client.post("/api/supervision/interlock/engage", json={"reason": "stop"})
        monkeypatch.setattr(
            supervision,
            "_resume_precondition_failure",
            lambda: "account_not_reconciled",
        )
        code = _totp_code("JBSWY3DPEHPK3PXP", int(time.time()))

        r = client.post(
            "/api/supervision/interlock/resume",
            json={"reason": "all clear", "step_up_code": code},
        )

        assert r.status_code == 409
        assert r.json()["detail"] == {
            "reason": "resume_precondition_failed",
            "check": "account_not_reconciled",
        }

    def test_concurrent_engage_wins_over_in_flight_resume(self, client, monkeypatch):
        from routers import supervision
        from state import mcp_adapter
        from step_up import _totp_code

        client.post("/api/supervision/interlock/engage", json={"reason": "initial pause"})

        def engage_during_checks():
            mcp_adapter.engage_interlock(actor="operator", reason="new emergency")
            return None

        monkeypatch.setattr(
            supervision,
            "_resume_precondition_failure",
            engage_during_checks,
        )
        code = _totp_code("JBSWY3DPEHPK3PXP", int(time.time()))

        r = client.post(
            "/api/supervision/interlock/resume",
            json={"reason": "older resume", "step_up_code": code},
        )

        assert r.status_code == 409
        assert r.json()["detail"]["reason"] == "interlock_changed"
        record = mcp_adapter.interlock_record()
        assert record is not None
        assert record.state.value == "paused"
        assert record.reason == "new emergency"

    def test_resume_requires_step_up_even_when_elevated(self, client):
        """Resume must always call verify() regardless of active elevation
        window.  Prior elevation from another action (e.g. approving a proposal)
        does **not** shortcut step-up enforcement (D6.6)."""
        from step_up import TOTP_PERIOD, _totp_code, get_step_up_verifier

        # Engage interlock so the subsequent resume is meaningful
        client.post("/api/supervision/interlock/engage", json={"reason": "stop"})

        # Simulate an admin that already has a 5-minute elevation from a prior
        # step-up action (e.g. approving a HIGH-risk proposal).
        now = int(time.time())
        code = _totp_code("JBSWY3DPEHPK3PXP", now)
        r_first = client.post(
            "/api/supervision/interlock/resume",
            json={"reason": "elevating first", "step_up_code": code},
        )
        assert r_first.status_code == 200

        verifier = get_step_up_verifier()
        assert verifier.is_elevated("admin")

        # Resume *without* step-up while elevated → must still require code.
        r_no_code = client.post("/api/supervision/interlock/resume", json={"reason": "no code"})
        assert r_no_code.status_code == 403
        assert r_no_code.json()["detail"]["reason"] == "step_up_required"

        # Resume *with* step-up → succeeds and refreshes the elevation window.
        # Use a code from the next TOTP window so it differs from `code` above —
        # replay protection would otherwise reject a reused code, and both calls
        # can land in the same 30s window when the test runs fast.
        code2 = _totp_code("JBSWY3DPEHPK3PXP", now + TOTP_PERIOD)
        r_with_code = client.post(
            "/api/supervision/interlock/resume",
            json={"reason": "second resume with step-up", "step_up_code": code2},
        )
        assert r_with_code.status_code == 200
        assert r_with_code.json()["state"] == "resumed"


# ---------------------------------------------------------------------------
# 5. GET /api/supervision/proposals
# ---------------------------------------------------------------------------


class TestListProposals:
    """GET /api/supervision/proposals — list supervision-originated proposals."""

    def test_list_proposals_empty(self, client):
        r = client.get("/api/supervision/proposals")
        assert r.status_code == 200
        body = r.json()
        assert body["proposals"] == []
        assert body["count"] == 0

    def test_list_proposals_after_inspect(self, client, tmp_path):
        """After an inspect that creates a proposal, it appears in the list."""
        log_dir = _make_critical_log_dir(tmp_path)
        with mock.patch("supervision.health.datetime") as m:
            m.now.return_value = NOW
            m.fromisoformat = datetime.fromisoformat
            client.post("/api/supervision/inspect", json={
                "pair": "XBTUSD",
                "log_dir": str(log_dir),
            })

        r = client.get("/api/supervision/proposals")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 1
        assert body["proposals"][0]["command_name"] == "flatten"
        assert body["proposals"][0]["status"] == "pending"

    def test_list_proposals_viewer_allowed(self, viewer_client):
        r = viewer_client.get("/api/supervision/proposals")
        assert r.status_code == 200

    def test_list_proposals_excludes_non_supervision(self, client):
        """Proposals created directly via adapter (not supervision) are excluded."""
        from state import mcp_adapter
        mcp_adapter.propose(
            command_name="flatten",
            target_agent_id="agent-btc",
            requester="manual",
            payload={"instrument": "BTC/USD.KRAKEN"},
        )

        r = client.get("/api/supervision/proposals")
        assert r.status_code == 200
        assert r.json()["count"] == 0


# ---------------------------------------------------------------------------
# 6. Gate: no dispatch or approve from any supervision endpoint
# ---------------------------------------------------------------------------


class TestNoDispatchOrApprove:
    """No supervision endpoint calls dispatch() or approve()."""

    def test_inspect_no_dispatch_approve_in_audit(self, client, tmp_path):
        """Full inspect cycle: audit has supervision_propose but never dispatch/approve."""
        log_dir = _make_critical_log_dir(tmp_path)
        with mock.patch("supervision.health.datetime") as m:
            m.now.return_value = NOW
            m.fromisoformat = datetime.fromisoformat
            client.post("/api/supervision/inspect", json={
                "pair": "XBTUSD",
                "log_dir": str(log_dir),
            })

        from state import mcp_adapter
        actions = [e["action"] for e in mcp_adapter.audit.entries()]
        assert "dispatch" not in actions
        assert "approve" not in actions

    def test_engage_resume_no_dispatch_approve(self, client):
        """Engage and resume interlock: no dispatch or approve in audit."""
        from step_up import _totp_code
        client.post("/api/supervision/interlock/engage", json={"reason": "test"})
        code = _totp_code("JBSWY3DPEHPK3PXP", int(time.time()))
        client.post("/api/supervision/interlock/resume", json={"reason": "test", "step_up_code": code})

        from state import mcp_adapter
        actions = [e["action"] for e in mcp_adapter.audit.entries()]
        assert "dispatch" not in actions
        assert "approve" not in actions


# ---------------------------------------------------------------------------
# 7. GET /api/supervision/audit
# ---------------------------------------------------------------------------


class TestAuditLog:
    """GET /api/supervision/audit — return audit log entries."""

    def test_get_audit_returns_entries(self, client):
        """Audit endpoint returns entries after engage/resume."""
        from step_up import _totp_code
        client.post("/api/supervision/interlock/engage", json={"reason": "test"})
        code = _totp_code("JBSWY3DPEHPK3PXP", int(time.time()))
        client.post("/api/supervision/interlock/resume", json={"reason": "done", "step_up_code": code})

        r = client.get("/api/supervision/audit")
        assert r.status_code == 200
        body = r.json()
        assert "entries" in body
        assert body["count"] >= 2
        actions = [e["action"] for e in body["entries"]]
        assert "interlock_engage" in actions
        assert "interlock_resume" in actions

    def test_get_audit_entry_shape(self, client):
        """Each audit entry has audit_id, timestamp, action, actor, detail."""
        client.post("/api/supervision/interlock/engage", json={"reason": "test"})

        r = client.get("/api/supervision/audit")
        assert r.status_code == 200
        entries = r.json()["entries"]
        assert len(entries) >= 1
        entry = entries[-1]
        assert "audit_id" in entry
        assert "timestamp" in entry
        assert "action" in entry
        assert "actor" in entry
        assert "detail" in entry

    def test_get_audit_viewer_allowed(self, viewer_client):
        """Viewer role can read the audit log (read-only)."""
        r = viewer_client.get("/api/supervision/audit")
        assert r.status_code == 200

    def test_get_audit_empty_structure(self, viewer_client):
        """Audit endpoint returns valid structure even when no user actions taken."""
        r = viewer_client.get("/api/supervision/audit")
        assert r.status_code == 200
        body = r.json()
        assert "entries" in body
        assert "count" in body
        assert isinstance(body["entries"], list)
        assert body["count"] == len(body["entries"])
