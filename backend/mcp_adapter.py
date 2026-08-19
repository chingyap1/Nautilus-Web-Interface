"""In-process MCP adapter inside NWI backend (D7, D8).

This module implements the MCP gateway as an **in-process adapter** — no
new container, no published port (D7). It wires the Supervisor's fake
provider end-to-end through the A3 stores:

    read → recommend → propose → human approval → dispatch → result → audit

The adapter calls into the same NWI proposal/approval/interlock/command
APIs that the human-facing NWI UI calls, just from a different entry point
(the Supervisor's MCP client instead of a browser).

Gate 1 (D8): the fake provider is fully deterministic — no external
dependency, no API key needed. All A3 adversarial cases must pass when
replayed through this adapter path, not just the raw store APIs.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from mcp_gateway.approvals import hash_payload, validate_approval
from mcp_gateway.catalog import get_command, list_commands
from mcp_gateway.interlock import evaluate_interlock, is_supervisor_traffic_allowed
from mcp_gateway.models import (
    ApprovalStatus,
    CommandApproval,
    CommandProposal,
    InterlockRecord,
    InterlockState,
    ProposalStatus,
    RiskClass,
)

from stores import ApprovalStore, InterlockStore, ProposalStore

logger = logging.getLogger("mcp_adapter")


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


@dataclass
class AuditEntry:
    """Immutable audit record for one MCP adapter action."""

    timestamp: str
    action: str
    actor: str
    detail: dict[str, Any]
    audit_id: str = field(default_factory=lambda: f"AUD-{uuid.uuid4().hex[:12].upper()}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "timestamp": self.timestamp,
            "action": self.action,
            "actor": self.actor,
            "detail": self.detail,
        }


class AuditLog:
    """In-memory audit log for the MCP adapter.

    In production this would be persisted to the database; for Gate 1
    (deterministic fake provider) an in-memory log is sufficient.
    """

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    def record(self, action: str, actor: str, **detail: Any) -> AuditEntry:
        entry = AuditEntry(
            timestamp=datetime.now(UTC).isoformat(),
            action=action,
            actor=actor,
            detail=detail,
        )
        self._entries.append(entry)
        logger.info("Audit %s: %s by %s — %s", entry.audit_id, action, actor, detail)
        return entry

    def entries(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._entries]

    def clear(self) -> None:
        self._entries.clear()


# ---------------------------------------------------------------------------
# Fake recommender (Gate 1 — deterministic, D8)
# ---------------------------------------------------------------------------


class FakeRecommender:
    """Deterministic fake recommender for Gate 1 (D8).

    Produces a recommendation based on the read resources and a simple
    rule: if no position is open and the agent is online, recommend
    starting a strategy; if a position is open, recommend flattening.

    This is intentionally simple — the point is to exercise the full
    propose → approve → dispatch flow deterministically, not to produce
    good trading signals.
    """

    def recommend(
        self,
        resources: dict[str, Any],
    ) -> dict[str, Any]:
        """Produce a recommendation from read resources."""
        agents = resources.get("agents", [])
        if not agents:
            return {
                "action": "none",
                "reason": "no agents available",
                "command": None,
                "payload": None,
            }

        agent = agents[0]
        agent_id = agent.get("agent_id", "agent-btc")
        open_positions = agent.get("open_positions", 0)
        status = agent.get("status", "offline")

        if status != "online":
            return {
                "action": "none",
                "reason": f"agent {agent_id} is {status}",
                "command": None,
                "payload": None,
                "agent_id": agent_id,
            }

        if open_positions > 0:
            return {
                "action": "recommend",
                "reason": f"agent {agent_id} has {open_positions} open positions",
                "command": "flatten",
                "payload": {"instrument": agent.get("pair", "BTC/USD.KRAKEN")},
                "agent_id": agent_id,
            }

        return {
            "action": "recommend",
            "reason": f"agent {agent_id} is online with no positions",
            "command": "start_strategy",
            "payload": {
                "strategy_id": "ma_cross",
                "instrument": agent.get("pair", "BTC/USD.KRAKEN"),
            },
            "agent_id": agent_id,
        }


# ---------------------------------------------------------------------------
# MCP Adapter
# ---------------------------------------------------------------------------


class MCPAdapter:
    """In-process MCP adapter (D7).

    Wires the Supervisor's read → recommend → propose → approve → dispatch
    → result → audit flow through the A3 stores. No network port, no
    separate process — it calls into the same NWI APIs as the UI.
    """

    def __init__(
        self,
        *,
        proposal_store: ProposalStore | None = None,
        approval_store: ApprovalStore | None = None,
        interlock_store: InterlockStore | None = None,
        recommender: FakeRecommender | None = None,
        audit_log: AuditLog | None = None,
    ) -> None:
        self._proposals = proposal_store or ProposalStore()
        self._approvals = approval_store or ApprovalStore()
        self._interlock = interlock_store or InterlockStore()
        self._recommender = recommender or FakeRecommender()
        self._audit = audit_log or AuditLog()

    @property
    def audit(self) -> AuditLog:
        return self._audit

    # -- read ---------------------------------------------------------------

    def read_resources(
        self,
        *,
        agent_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Read MCP resources (O6b catalog). Returns a resource snapshot.

        In production this calls NWI's existing read APIs. For Gate 1,
        the caller passes agent data directly.
        """
        resources: dict[str, Any] = {
            "agents": [],
            "interlock": None,
            "commands": [cmd.to_dict() for cmd in list_commands(enabled_only=True)],
        }
        if agent_data:
            resources["agents"] = [agent_data]

        # Check interlock state
        record = asyncio_run(self._interlock.get())
        if record:
            effective_state = evaluate_interlock(record, now=datetime.now(UTC))
            resources["interlock"] = {
                "state": effective_state.value,
                "updated_at": record.updated_at,
                "lease_seconds": record.lease_seconds,
            }
        else:
            resources["interlock"] = {"state": "paused", "updated_at": None, "lease_seconds": 30.0}

        self._audit.record(
            "read",
            actor="supervisor",
            resource_count=len(resources),
        )
        return resources

    # -- recommend ----------------------------------------------------------

    def recommend(self, resources: dict[str, Any]) -> dict[str, Any]:
        """Produce a recommendation from read resources."""
        rec = self._recommender.recommend(resources)
        self._audit.record(
            "recommend",
            actor="supervisor",
            recommendation=rec["action"],
            command=rec.get("command"),
        )
        return rec

    # -- propose ------------------------------------------------------------

    def propose(
        self,
        *,
        command_name: str,
        target_agent_id: str,
        requester: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> CommandProposal:
        """Create a proposal through the A3 ProposalStore.

        Fails closed if:
        - The command is not in the enabled catalog.
        - The interlock is PAUSED (D5: no new proposals while paused).
        """
        # Check interlock — fail closed if PAUSED
        record = asyncio_run(self._interlock.get())
        if not is_supervisor_traffic_allowed(record, now=datetime.now(UTC)):
            self._audit.record(
                "propose_rejected",
                actor=requester,
                reason="interlock paused",
                command=command_name,
            )
            raise InterlockPausedError("Supervisor interlock is PAUSED — cannot propose")

        # Validate command exists in catalog
        try:
            definition = get_command(command_name)
        except Exception as exc:
            self._audit.record(
                "propose_rejected",
                actor=requester,
                reason=f"unknown command: {exc}",
                command=command_name,
            )
            raise UnknownCommandError(f"Unknown command: {command_name}") from exc

        if not definition.enabled:
            self._audit.record(
                "propose_rejected",
                actor=requester,
                reason="command disabled",
                command=command_name,
            )
            raise UnknownCommandError(f"Command {command_name} is disabled")

        proposal = asyncio_run(
            self._proposals.create(
                command_name=command_name,
                command_version=definition.version,
                target_agent_id=target_agent_id,
                requester=requester,
                payload=payload,
                idempotency_key=idempotency_key or f"mcp-{uuid.uuid4().hex[:8]}",
            )
        )
        self._audit.record(
            "propose",
            actor=requester,
            proposal_id=proposal.proposal_id,
            command=command_name,
            target=target_agent_id,
        )
        return proposal

    # -- approve ------------------------------------------------------------

    def approve(
        self,
        *,
        proposal: CommandProposal,
        approver: str,
    ) -> CommandApproval:
        """Create an approval through the A3 ApprovalStore.

        Enforces D6.4 self-approval rules:
        - Service principals cannot approve (they can't hold approver role).
        - Human principals can self-approve.
        """
        approval = asyncio_run(
            self._approvals.create(proposal=proposal, approver=approver)
        )
        self._audit.record(
            "approve",
            actor=approver,
            approval_id=approval.approval_id,
            proposal_id=proposal.proposal_id,
        )
        return approval

    # -- dispatch -----------------------------------------------------------

    def dispatch(
        self,
        *,
        proposal: CommandProposal,
        approval: CommandApproval,
    ) -> dict[str, Any]:
        """Dispatch an approved command.

        Validates the approval (fail-closed), consumes it atomically, and
        creates a durable command record. Returns the dispatch result.

        Fails closed on:
        - Payload mutation (hash mismatch)
        - Replay (approval already consumed)
        - Expiry (approval or proposal past TTL)
        - Revocation (approval revoked via interlock engage)
        - Wrong target/requester
        """
        now = datetime.now(UTC)

        # Validate approval — fail closed
        ok, reason = validate_approval(approval, proposal, now=now)
        if not ok:
            self._audit.record(
                "dispatch_rejected",
                actor=approval.approver,
                proposal_id=proposal.proposal_id,
                reason=reason,
            )
            raise DispatchError(f"Approval validation failed: {reason}")

        # Consume approval atomically (single-use, D4)
        consumed = asyncio_run(self._approvals.consume(approval.approval_id))
        if consumed is None:
            self._audit.record(
                "dispatch_rejected",
                actor=approval.approver,
                proposal_id=proposal.proposal_id,
                reason="approval could not be consumed (not active or not found)",
            )
            raise DispatchError("Approval could not be consumed — already used or not active")

        # Update proposal status
        asyncio_run(
            self._proposals.update_status(proposal.proposal_id, ProposalStatus.DISPATCHED)
        )

        result = {
            "dispatch_id": f"DSP-{uuid.uuid4().hex[:12].upper()}",
            "proposal_id": proposal.proposal_id,
            "approval_id": approval.approval_id,
            "command": proposal.command_name,
            "target_agent_id": proposal.target_agent_id,
            "status": "dispatched",
            "dispatched_at": now.isoformat(),
        }
        self._audit.record(
            "dispatch",
            actor=approval.approver,
            dispatch_id=result["dispatch_id"],
            proposal_id=proposal.proposal_id,
            command=proposal.command_name,
        )
        return result

    # -- interlock ----------------------------------------------------------

    def engage_interlock(self, *, actor: str, reason: str) -> InterlockRecord:
        """Engage the interlock (PAUSED). Any authenticated role can engage (D6.4)."""
        record = asyncio_run(self._interlock.engage(actor=actor, reason=reason))
        self._audit.record(
            "interlock_engage",
            actor=actor,
            reason=reason,
        )
        return record

    def resume_interlock(self, *, actor: str, reason: str) -> InterlockRecord:
        """Resume the interlock (RESUMED). Requires admin + step-up (D5, D6.6).

        Step-up enforcement is at the API layer; the adapter trusts the
        caller has already verified step-up.
        """
        record = asyncio_run(self._interlock.resume(actor=actor, reason=reason))
        self._audit.record(
            "interlock_resume",
            actor=actor,
            reason=reason,
        )
        return record

    def interlock_state(self) -> InterlockState:
        """Return the effective interlock state (fail-closed, D5)."""
        record = asyncio_run(self._interlock.get())
        return evaluate_interlock(record, now=datetime.now(UTC))

    def interlock_record(self) -> InterlockRecord | None:
        """Return the full interlock record, or None if not initialized."""
        return asyncio_run(self._interlock.get())


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InterlockPausedError(Exception):
    """Raised when the interlock is PAUSED and a proposal is attempted."""


class UnknownCommandError(Exception):
    """Raised when a command is not in the enabled catalog."""


class DispatchError(Exception):
    """Raised when dispatch fails closed (approval invalid, replayed, etc.)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def asyncio_run(coro):
    """Run a coroutine, handling the case where we're already in an event loop."""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # We're in a running loop — create a task and wait
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, coro).result()
