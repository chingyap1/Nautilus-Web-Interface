"""Durable storage for proposals, approvals, and interlock state (D4, D5, D6.2).

Repository interfaces backed by SQLite with ``BEGIN IMMEDIATE`` transactions
for atomic compare-and-set semantics. The SQL lives behind these interfaces
so a future swap to Postgres is a new implementation, not a rewrite.

All timestamps are tz-aware UTC ISO-8601, stamped by NWI's server clock
(D6.2 — client-supplied timestamps are rejected at the API boundary).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

import aiosqlite
from database import DB_PATH

from mcp_gateway.approvals import hash_payload
from mcp_gateway.interlock import evaluate_interlock
from mcp_gateway.models import (
    ApprovalStatus,
    CommandApproval,
    CommandProposal,
    InterlockRecord,
    InterlockState,
    ProposalStatus,
)

# D6.2 TTLs
PROPOSAL_TTL = timedelta(minutes=15)
APPROVAL_TTL = timedelta(minutes=10)
# D5 interlock lease
INTERLOCK_LEASE_SECONDS = 30.0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    proposal_id     TEXT PRIMARY KEY,
    command_name    TEXT NOT NULL,
    command_version INTEGER NOT NULL,
    target_agent_id TEXT NOT NULL,
    requester       TEXT NOT NULL,
    payload         TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id     TEXT PRIMARY KEY,
    proposal_id     TEXT NOT NULL,
    payload_hash    TEXT NOT NULL,
    target_agent_id TEXT NOT NULL,
    requester       TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    approver        TEXT NOT NULL,
    approved_at     TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    FOREIGN KEY (proposal_id) REFERENCES proposals(proposal_id)
);

CREATE TABLE IF NOT EXISTS interlock (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    state           TEXT NOT NULL DEFAULT 'paused',
    actor           TEXT NOT NULL,
    reason          TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    lease_seconds   REAL NOT NULL DEFAULT 30.0
);

CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);
CREATE INDEX IF NOT EXISTS idx_approvals_proposal ON approvals(proposal_id);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
"""


async def init_stores_db() -> None:
    """Create proposal/approval/interlock tables if they don't exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_STORE_SCHEMA)
        await db.commit()


# ---------------------------------------------------------------------------
# ProposalStore
# ---------------------------------------------------------------------------


class ProposalStore:
    """Repository for command proposals (D4)."""

    async def create(
        self,
        *,
        command_name: str,
        command_version: int,
        target_agent_id: str,
        requester: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> CommandProposal:
        """Create a new proposal with server-stamped timestamps (D6.2).

        The proposal TTL is 15 minutes from now.
        """
        now = datetime.now(UTC)
        proposal = CommandProposal(
            proposal_id=f"PRP-{uuid.uuid4().hex[:12].upper()}",
            command_name=command_name,
            command_version=command_version,
            target_agent_id=target_agent_id,
            requester=requester,
            payload=payload,
            idempotency_key=idempotency_key,
            created_at=now.isoformat(),
            expires_at=(now + PROPOSAL_TTL).isoformat(),
            status=ProposalStatus.PENDING,
        )
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO proposals
                   (proposal_id, command_name, command_version, target_agent_id,
                    requester, payload, idempotency_key, created_at, expires_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    proposal.proposal_id,
                    proposal.command_name,
                    proposal.command_version,
                    proposal.target_agent_id,
                    proposal.requester,
                    json.dumps(proposal.payload, sort_keys=True),
                    proposal.idempotency_key,
                    proposal.created_at,
                    proposal.expires_at,
                    proposal.status.value,
                ),
            )
            await db.commit()
        return proposal

    async def get(self, proposal_id: str) -> Optional[CommandProposal]:
        """Fetch a proposal by ID."""
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM proposals WHERE proposal_id=?", (proposal_id,)
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        return self._row_to_proposal(row)

    async def list_pending(self) -> list[CommandProposal]:
        """Return all pending proposals."""
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM proposals WHERE status='pending' ORDER BY created_at"
            ) as cur:
                rows = await cur.fetchall()
        return [self._row_to_proposal(r) for r in rows]

    async def update_status(
        self, proposal_id: str, status: ProposalStatus
    ) -> Optional[CommandProposal]:
        """Atomically update a proposal's status."""
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                cur = await db.execute(
                    "UPDATE proposals SET status=? WHERE proposal_id=?",
                    (status.value, proposal_id),
                )
                await db.commit()
                if cur.rowcount == 0:
                    return None
            except Exception:
                await db.execute("ROLLBACK")
                raise
        return await self.get(proposal_id)

    async def expire_stale(self) -> int:
        """Mark proposals past their expires_at as expired. Returns count."""
        now_iso = _now_iso()
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                """UPDATE proposals SET status='expired'
                   WHERE status='pending' AND expires_at < ?""",
                (now_iso,),
            )
            await db.commit()
            return cur.rowcount

    @staticmethod
    def _row_to_proposal(row: aiosqlite.Row) -> CommandProposal:
        return CommandProposal(
            proposal_id=row["proposal_id"],
            command_name=row["command_name"],
            command_version=row["command_version"],
            target_agent_id=row["target_agent_id"],
            requester=row["requester"],
            payload=json.loads(row["payload"]),
            idempotency_key=row["idempotency_key"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            status=ProposalStatus(row["status"]),
        )


# ---------------------------------------------------------------------------
# ApprovalStore
# ---------------------------------------------------------------------------


class ApprovalStore:
    """Repository for command approvals (D4).

    Approval consumption uses ``BEGIN IMMEDIATE`` so the
    consume-approval-and-create-command sequence is atomic (D4).
    """

    async def create(
        self,
        *,
        proposal: CommandProposal,
        approver: str,
    ) -> CommandApproval:
        """Create a new approval with server-stamped timestamps (D6.2).

        The approval TTL is 10 minutes from now, and never later than the
        parent proposal's expires_at.
        """
        now = datetime.now(UTC)
        approval_expiry = now + APPROVAL_TTL
        proposal_expiry = datetime.fromisoformat(
            proposal.expires_at.replace("Z", "+00:00")
        )
        if approval_expiry > proposal_expiry:
            approval_expiry = proposal_expiry

        approval = CommandApproval(
            approval_id=f"APR-{uuid.uuid4().hex[:12].upper()}",
            proposal_id=proposal.proposal_id,
            payload_hash=hash_payload(proposal.payload),
            target_agent_id=proposal.target_agent_id,
            requester=proposal.requester,
            idempotency_key=proposal.idempotency_key,
            approver=approver,
            approved_at=now.isoformat(),
            expires_at=approval_expiry.isoformat(),
            status=ApprovalStatus.ACTIVE,
        )
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO approvals
                   (approval_id, proposal_id, payload_hash, target_agent_id,
                    requester, idempotency_key, approver, approved_at,
                    expires_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    approval.approval_id,
                    approval.proposal_id,
                    approval.payload_hash,
                    approval.target_agent_id,
                    approval.requester,
                    approval.idempotency_key,
                    approval.approver,
                    approval.approved_at,
                    approval.expires_at,
                    approval.status.value,
                ),
            )
            await db.commit()
        return approval

    async def get(self, approval_id: str) -> Optional[CommandApproval]:
        """Fetch an approval by ID."""
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM approvals WHERE approval_id=?", (approval_id,)
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        return self._row_to_approval(row)

    async def get_for_proposal(self, proposal_id: str) -> list[CommandApproval]:
        """Return all approvals for a given proposal."""
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM approvals WHERE proposal_id=? ORDER BY approved_at",
                (proposal_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [self._row_to_approval(r) for r in rows]

    async def consume(self, approval_id: str) -> Optional[CommandApproval]:
        """Atomically consume an active approval (single-use dispatch, D4).

        Uses ``BEGIN IMMEDIATE`` so consumption and command creation can be
        in one atomic transaction. Returns the consumed approval, or None
        if the approval was not found or not active.
        """
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM approvals WHERE approval_id=?", (approval_id,)
                ) as cur:
                    row = await cur.fetchone()
                if not row or row["status"] != ApprovalStatus.ACTIVE.value:
                    await db.execute("ROLLBACK")
                    return None
                await db.execute(
                    "UPDATE approvals SET status='consumed' WHERE approval_id=?",
                    (approval_id,),
                )
                await db.commit()
            except Exception:
                await db.execute("ROLLBACK")
                raise
        return self._row_to_approval(row, status=ApprovalStatus.CONSUMED)

    async def revoke(self, approval_id: str) -> Optional[CommandApproval]:
        """Revoke an active approval (e.g. when interlock is engaged, D5)."""
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM approvals WHERE approval_id=?", (approval_id,)
                ) as cur:
                    row = await cur.fetchone()
                if not row or row["status"] != ApprovalStatus.ACTIVE.value:
                    await db.execute("ROLLBACK")
                    return None
                await db.execute(
                    "UPDATE approvals SET status='revoked' WHERE approval_id=?",
                    (approval_id,),
                )
                await db.commit()
            except Exception:
                await db.execute("ROLLBACK")
                raise
        return self._row_to_approval(row, status=ApprovalStatus.REVOKED)

    async def revoke_all_active(self) -> int:
        """Revoke all active approvals (used when interlock is engaged, D5).

        Returns the count of revoked approvals.
        """
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                cur = await db.execute(
                    "UPDATE approvals SET status='revoked' WHERE status='active'"
                )
                await db.commit()
                return cur.rowcount
            except Exception:
                await db.execute("ROLLBACK")
                raise

    async def expire_stale(self) -> int:
        """Mark approvals past their expires_at as expired. Returns count."""
        now_iso = _now_iso()
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                """UPDATE approvals SET status='expired'
                   WHERE status='active' AND expires_at < ?""",
                (now_iso,),
            )
            await db.commit()
            return cur.rowcount

    @staticmethod
    def _row_to_approval(
        row: aiosqlite.Row, *, status: Optional[ApprovalStatus] = None
    ) -> CommandApproval:
        return CommandApproval(
            approval_id=row["approval_id"],
            proposal_id=row["proposal_id"],
            payload_hash=row["payload_hash"],
            target_agent_id=row["target_agent_id"],
            requester=row["requester"],
            idempotency_key=row["idempotency_key"],
            approver=row["approver"],
            approved_at=row["approved_at"],
            expires_at=row["expires_at"],
            status=status or ApprovalStatus(row["status"]),
        )


# ---------------------------------------------------------------------------
# InterlockStore
# ---------------------------------------------------------------------------


class InterlockStore:
    """Repository for the Supervisor command interlock (D4, D5).

    The interlock is a singleton row (id=1). State transitions use
    ``BEGIN IMMEDIATE`` so engage/resume cannot race a dispatch check.
    """

    async def get(self) -> Optional[InterlockRecord]:
        """Return the current interlock record, or None if not initialized."""
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM interlock WHERE id=1") as cur:
                row = await cur.fetchone()
        if not row:
            return None
        return InterlockRecord(
            state=InterlockState(row["state"]),
            actor=row["actor"],
            reason=row["reason"],
            updated_at=row["updated_at"],
            lease_seconds=row["lease_seconds"],
        )

    async def engage(self, *, actor: str, reason: str) -> InterlockRecord:
        """Atomically transition the interlock to PAUSED (D5).

        Also revokes all active approvals (D5 engage semantics).
        Returns the new record.
        """
        now_iso = _now_iso()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(
                    """INSERT INTO interlock (id, state, actor, reason, updated_at, lease_seconds)
                       VALUES (1, 'paused', ?, ?, ?, 30.0)
                       ON CONFLICT(id) DO UPDATE SET
                           state='paused', actor=excluded.actor,
                           reason=excluded.reason, updated_at=excluded.updated_at,
                           lease_seconds=excluded.lease_seconds""",
                    (actor, reason, now_iso),
                )
                await db.execute(
                    "UPDATE approvals SET status='revoked' WHERE status='active'"
                )
                await db.commit()
            except Exception:
                await db.execute("ROLLBACK")
                raise
        return InterlockRecord(
            state=InterlockState.PAUSED,
            actor=actor,
            reason=reason,
            updated_at=now_iso,
            lease_seconds=INTERLOCK_LEASE_SECONDS,
        )

    async def resume(self, *, actor: str, reason: str) -> InterlockRecord:
        """Atomically transition the interlock to RESUMED (D5).

        Only admin + step-up may call this (enforced at the API layer).
        Returns the new record.
        """
        now_iso = _now_iso()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(
                    """INSERT INTO interlock (id, state, actor, reason, updated_at, lease_seconds)
                       VALUES (1, 'resumed', ?, ?, ?, 30.0)
                       ON CONFLICT(id) DO UPDATE SET
                           state='resumed', actor=excluded.actor,
                           reason=excluded.reason, updated_at=excluded.updated_at,
                           lease_seconds=excluded.lease_seconds""",
                    (actor, reason, now_iso),
                )
                await db.commit()
            except Exception:
                await db.execute("ROLLBACK")
                raise
        return InterlockRecord(
            state=InterlockState.RESUMED,
            actor=actor,
            reason=reason,
            updated_at=now_iso,
            lease_seconds=INTERLOCK_LEASE_SECONDS,
        )

    async def resume_if_unchanged(
        self,
        *,
        actor: str,
        reason: str,
        expected_updated_at: str | None,
    ) -> InterlockRecord | None:
        """Resume only if no engage/renewal changed the observed durable row."""
        now_iso = _now_iso()
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute("SELECT updated_at FROM interlock WHERE id=1") as cur:
                    row = await cur.fetchone()
                current_updated_at = row["updated_at"] if row else None
                if current_updated_at != expected_updated_at:
                    await db.commit()
                    return None
                if row:
                    await db.execute(
                        """UPDATE interlock
                           SET state='resumed', actor=?, reason=?, updated_at=?, lease_seconds=?
                           WHERE id=1 AND updated_at=?""",
                        (
                            actor,
                            reason,
                            now_iso,
                            INTERLOCK_LEASE_SECONDS,
                            expected_updated_at,
                        ),
                    )
                else:
                    await db.execute(
                        """INSERT INTO interlock
                           (id, state, actor, reason, updated_at, lease_seconds)
                           VALUES (1, 'resumed', ?, ?, ?, ?)""",
                        (actor, reason, now_iso, INTERLOCK_LEASE_SECONDS),
                    )
                await db.commit()
            except Exception:
                await db.execute("ROLLBACK")
                raise
        return InterlockRecord(
            state=InterlockState.RESUMED,
            actor=actor,
            reason=reason,
            updated_at=now_iso,
            lease_seconds=INTERLOCK_LEASE_SECONDS,
        )

    async def renew_if_fresh(
        self, *, now: datetime | None = None
    ) -> InterlockRecord | None:
        """Renew an already-valid RESUMED lease without reviving stale state.

        NWI owns lease freshness. The immediate transaction serializes this
        check-and-update with engage/resume so an operator pause always wins.
        """
        now_ = now or datetime.now(UTC)
        now_iso = now_.isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute("SELECT * FROM interlock WHERE id=1") as cur:
                    row = await cur.fetchone()
                if not row:
                    await db.commit()
                    return None

                try:
                    record = InterlockRecord(
                        state=InterlockState(row["state"]),
                        actor=row["actor"],
                        reason=row["reason"],
                        updated_at=row["updated_at"],
                        lease_seconds=row["lease_seconds"],
                    )
                    effective_state = evaluate_interlock(record, now=now_)
                except (TypeError, ValueError):
                    effective_state = InterlockState.PAUSED
                if effective_state != InterlockState.RESUMED:
                    await db.commit()
                    return None

                await db.execute(
                    "UPDATE interlock SET updated_at=? WHERE id=1 AND state='resumed'",
                    (now_iso,),
                )
                await db.commit()
            except Exception:
                await db.execute("ROLLBACK")
                raise

        return InterlockRecord(
            state=InterlockState.RESUMED,
            actor=record.actor,
            reason=record.reason,
            updated_at=now_iso,
            lease_seconds=record.lease_seconds,
        )
