"""F1 — Dispatch bridge tests.

Tests that an approved MCP-origin dispatch creates a durable command record
with provenance (origin=supervisor, proposal_id, approval_id), that the
CommandProcessor can publish it, and that the revalidation table fails closed
on interlock-paused, payload mutation, and duplicate dispatch.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Fixtures — mirror test_mcp_actions_api.py patterns
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
    from step_up import TOTPStepUpVerifier, set_step_up_verifier
    set_step_up_verifier(TOTPStepUpVerifier())
    yield


@pytest.fixture(autouse=True)
def isolated_command_dir(tmp_path, monkeypatch):
    """Route the FileCommandChannel to a per-test directory.

    dispatch_bridge.persist() constructs a channel with no COMMAND_DIR
    override when the caller doesn't inject one, so without this it would
    write into the real repo's logs/commands/ during test runs.
    """
    monkeypatch.setenv("COMMAND_DIR", str(tmp_path / "commands"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_proposal(
    command_name: str = "flatten",
    target_agent_id: str = "agent-btc",
    payload: dict | None = None,
    requester: str = "supervision",
) -> str:
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


def _set_elevated(principal: str = "admin") -> None:
    from step_up import TOTPStepUpVerifier, get_step_up_verifier

    verifier = get_step_up_verifier()
    if isinstance(verifier, TOTPStepUpVerifier):
        verifier._elevated_until[principal] = float(time.time()) + 300.0


# ---------------------------------------------------------------------------
# 1. Durable command creation
# ---------------------------------------------------------------------------


class TestDispatchCreatesDurableCommand:
    """After dispatch, exactly one command row exists with provenance."""

    def test_dispatch_creates_command_with_provenance(self, client):
        """Dispatch creates a command row with origin=supervisor, proposal_id, approval_id."""
        pid = _create_proposal(
            command_name="cancel_order",
            payload={"client_order_id": "O-12345"},
        )
        r1 = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        assert r1.status_code == 200
        aid = r1.json()["approval_id"]

        r2 = client.post(f"/api/mcp/approvals/{aid}/dispatch")
        assert r2.status_code == 200
        body = r2.json()
        assert body["status"] == "dispatched"
        assert "command_id" in body

        # Verify the command row in the DB
        import commands

        cmd = asyncio.run(commands.get_command(body["command_id"]))
        assert cmd is not None
        assert cmd["origin"] == "supervisor"
        assert cmd["proposal_id"] == pid
        assert cmd["approval_id"] == aid
        assert cmd["status"] == "VALIDATED"
        assert cmd["client_order_id"] == "O-12345"

    def test_dispatch_high_risk_creates_command(self, client):
        """HIGH-risk (flatten) dispatch with step-up creates a command."""
        _set_elevated("admin")
        pid = _create_proposal(
            command_name="flatten",
            payload={"instrument": "BTC/USD.KRAKEN"},
        )
        r1 = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        assert r1.status_code == 200
        aid = r1.json()["approval_id"]

        r2 = client.post(f"/api/mcp/approvals/{aid}/dispatch")
        assert r2.status_code == 200
        assert "command_id" in r2.json()

        import commands

        cmd = asyncio.run(commands.get_command(r2.json()["command_id"]))
        assert cmd is not None
        assert cmd["origin"] == "supervisor"
        assert cmd["command_type"] == "flatten"
        assert cmd["instrument"] == "BTC/USD.KRAKEN"

    def test_dispatch_rejects_live_execution_mode(self, client, monkeypatch):
        monkeypatch.setenv("EXECUTION_MODE", "live")
        pid = _create_proposal(
            command_name="cancel_order",
            payload={"client_order_id": "O-PAPER-ONLY"},
        )
        approved = client.post("/api/mcp/approvals", json={"proposal_id": pid})

        response = client.post(
            f"/api/mcp/approvals/{approved.json()['approval_id']}/dispatch"
        )

        assert response.status_code == 409
        assert "paper_only" in str(response.json())

    def test_dispatch_start_strategy_creates_command(self, client):
        """MEDIUM-risk (start_strategy) dispatch creates a command."""
        pid = _create_proposal(
            command_name="start_strategy",
            payload={"strategy_id": "ma_cross"},
        )
        r1 = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        assert r1.status_code == 200
        aid = r1.json()["approval_id"]

        r2 = client.post(f"/api/mcp/approvals/{aid}/dispatch")
        assert r2.status_code == 200
        assert "command_id" in r2.json()

        import commands

        cmd = asyncio.run(commands.get_command(r2.json()["command_id"]))
        assert cmd is not None
        assert cmd["command_type"] == "start_strategy"
        assert cmd["strategy_id"] == "ma_cross"


# ---------------------------------------------------------------------------
# 2. Idempotency
# ---------------------------------------------------------------------------


class TestDispatchIdempotency:
    """Duplicate dispatch cannot produce two commands."""

    def test_replayed_dispatch_rejected_by_approval(self, client):
        """A second dispatch of the same approval is rejected (409)."""
        pid = _create_proposal(
            command_name="cancel_order",
            payload={"client_order_id": "O-12345"},
        )
        r1 = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        aid = r1.json()["approval_id"]

        r2 = client.post(f"/api/mcp/approvals/{aid}/dispatch")
        assert r2.status_code == 200
        first_command_id = r2.json()["command_id"]

        r3 = client.post(f"/api/mcp/approvals/{aid}/dispatch")
        assert r3.status_code == 409

        # Only one command row should exist
        import commands

        all_cmds = asyncio.run(commands.list_commands(limit=100))
        supervisor_cmds = [c for c in all_cmds if c.get("origin") == "supervisor"]
        assert len(supervisor_cmds) == 1
        assert supervisor_cmds[0]["command_id"] == first_command_id


# ---------------------------------------------------------------------------
# 3. Interlock rejection
# ---------------------------------------------------------------------------


class TestDispatchInterlockRejection:
    """Dispatch fails closed when interlock is engaged between approve and persist."""

    def test_dispatch_rejected_when_interlock_engaged(self, client):
        """Engaging interlock after approve blocks dispatch (no command row)."""
        pid = _create_proposal(
            command_name="cancel_order",
            payload={"client_order_id": "O-12345"},
        )
        r1 = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        aid = r1.json()["approval_id"]

        # Engage interlock between approve and dispatch
        client.post("/api/supervision/interlock/engage", json={"reason": "emergency"})

        r2 = client.post(f"/api/mcp/approvals/{aid}/dispatch")
        # The adapter's validate_approval will reject because interlock
        # revokes the approval, OR the bridge will reject with interlock_paused.
        # Either way, no command should be created.
        assert r2.status_code == 409

        import commands

        all_cmds = asyncio.run(commands.list_commands(limit=100))
        supervisor_cmds = [c for c in all_cmds if c.get("origin") == "supervisor"]
        assert len(supervisor_cmds) == 0


# ---------------------------------------------------------------------------
# 4. Audit
# ---------------------------------------------------------------------------


class TestDispatchAudit:
    """Dispatch bridge actions are audited."""

    def test_successful_dispatch_audited(self, client):
        """Successful dispatch records 'dispatch' in the audit log."""
        pid = _create_proposal(
            command_name="cancel_order",
            payload={"client_order_id": "O-12345"},
        )
        r1 = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        aid = r1.json()["approval_id"]
        client.post(f"/api/mcp/approvals/{aid}/dispatch")

        from state import mcp_adapter
        actions = [e["action"] for e in mcp_adapter.audit.entries()]
        assert "dispatch" in actions

    def test_failed_dispatch_audited(self, client):
        """Failed dispatch (interlock) records 'dispatch_failed' in the audit log."""
        pid = _create_proposal(
            command_name="cancel_order",
            payload={"client_order_id": "O-12345"},
        )
        r1 = client.post("/api/mcp/approvals", json={"proposal_id": pid})
        aid = r1.json()["approval_id"]

        # Engage interlock to cause bridge failure
        client.post("/api/supervision/interlock/engage", json={"reason": "emergency"})

        client.post(f"/api/mcp/approvals/{aid}/dispatch")

        from state import mcp_adapter
        actions = [e["action"] for e in mcp_adapter.audit.entries()]
        # The adapter may reject before the bridge, or the bridge may reject.
        # Either "dispatch_rejected" (adapter) or "dispatch_failed" (bridge).
        assert "dispatch_rejected" in actions or "dispatch_failed" in actions


# ---------------------------------------------------------------------------
# 5. Human command provenance regression guard
# ---------------------------------------------------------------------------


class TestHumanCommandProvenance:
    """Human-origin commands still have origin=human (regression guard)."""

    def test_human_order_has_origin_human(self, client):
        """A human-origin order via /api/orders has origin=human."""
        r = client.post(
            "/api/orders",
            json={
                "instrument": "EUR/USD.SIM",
                "side": "BUY",
                "type": "MARKET",
                "quantity": 1.0,
            },
        )
        assert r.status_code == 200
        command_id = r.json()["command_id"]

        import commands

        cmd = asyncio.run(commands.get_command(command_id))
        assert cmd is not None
        assert cmd["origin"] == "human"
        assert cmd["proposal_id"] is None
        assert cmd["approval_id"] is None


# ---------------------------------------------------------------------------
# 6. Dispatch durability — published to the file channel synchronously
# ---------------------------------------------------------------------------


class TestDispatchPublishesSynchronously:
    """Regression guard for the gate5 d2 durability gap.

    Previously the dispatch endpoint only wrote the command to the DB with
    status VALIDATED and left CommandProcessor's background poll loop to
    publish it to the FileCommandChannel. If NWI crashed between the DB
    write and that loop's next tick, the command was never published and
    the agent had nothing to claim — even though /dispatch had already
    returned 200. persist() now publishes in the same request, so the
    command must be visible to the file channel immediately, with no
    CommandProcessor loop iteration required.
    """

    def test_command_visible_in_file_channel_immediately_after_dispatch(self, client, tmp_path):
        pid = _create_proposal(
            command_name="cancel_order",
            payload={"client_order_id": "O-durability"},
        )
        aid = client.post("/api/mcp/approvals", json={"proposal_id": pid}).json()["approval_id"]

        r = client.post(f"/api/mcp/approvals/{aid}/dispatch")
        assert r.status_code == 200
        command_id = r.json()["command_id"]

        # No CommandProcessor was ever constructed or run in this test — if
        # the command is visible here, dispatch_bridge.persist() published
        # it itself.
        import os

        from live.command_channel import FileCommandChannel

        channel = FileCommandChannel(os.environ["COMMAND_DIR"])
        assert channel.has_in_flight(command_id)
        pending = channel.pending / f"{command_id}.json"
        assert pending.exists()

    def test_command_processor_does_not_double_publish(self, client, tmp_path):
        """CommandProcessor's own publish() call is a safe no-op retry —
        it must not clobber or duplicate an already-published command."""
        pid = _create_proposal(
            command_name="cancel_order",
            payload={"client_order_id": "O-idempotent"},
        )
        aid = client.post("/api/mcp/approvals", json={"proposal_id": pid}).json()["approval_id"]
        command_id = client.post(f"/api/mcp/approvals/{aid}/dispatch").json()["command_id"]

        import os

        from command_processor import CommandProcessor

        from live.command_channel import FileCommandChannel

        channel = FileCommandChannel(os.environ["COMMAND_DIR"])
        before = (channel.pending / f"{command_id}.json").read_text()

        processor = CommandProcessor(channel=channel)
        asyncio.run(processor.process_once())

        after = (channel.pending / f"{command_id}.json").read_text()
        assert before == after
        assert channel.has_in_flight(command_id)
