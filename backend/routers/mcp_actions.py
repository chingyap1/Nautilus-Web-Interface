"""B5 — MCP approve/dispatch/reject HTTP endpoints (D6.4–D6.6, D7, D3–D6).

This router is **separate** from ``routers/supervision.py`` by design:
``supervision.py``'s invariant "never calls ``dispatch()`` or ``approve()``"
is a tested guarantee today, and keeping that file free of these calls
makes the guarantee easy to assert by inspection.

All three endpoints require the ``approver`` role (or ``admin``).  Service
principals are structurally barred (D6.4).  HIGH/CRITICAL-risk commands
additionally require an active step-up session (D6.6).
"""

from __future__ import annotations

from typing import Any

from auth_jwt import require_approver
from dispatch_bridge import DispatchBridgeError
from dispatch_bridge import persist as persist_command
from fastapi import APIRouter, Body, Depends, HTTPException
from mcp_adapter import DispatchError, InterlockPausedError, UnknownCommandError
from pydantic import BaseModel
from state import mcp_adapter
from step_up import get_step_up_verifier
from stores import ApprovalStore, ProposalStore

from mcp_gateway.catalog import get_command
from mcp_gateway.models import ProposalStatus, RiskClass

router = APIRouter(prefix="/api/mcp", tags=["mcp-actions"])

# Shared store instances — reuse the same SQLite DB as the adapter
_proposal_store = ProposalStore()
_approval_store = ApprovalStore()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ApproveRequest(BaseModel):
    proposal_id: str
    step_up_code: str | None = None


class ProposeRequest(BaseModel):
    command_name: str
    target_agent_id: str
    requester: str
    payload: dict[str, Any] = {}
    idempotency_key: str | None = None


class RejectRequest(BaseModel):
    reason: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _principal(user: dict) -> str:
    return user.get("sub", user.get("username", "unknown"))


def _risk_class_for_proposal(command_name: str) -> RiskClass:
    """Look up the risk class from the catalog (never trust client input)."""
    try:
        definition = get_command(command_name)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Unknown command: {command_name}")
    return definition.risk_class


def _require_step_up(user: dict, risk_class: RiskClass, step_up_code: str | None) -> None:
    """Enforce the D6.6 step-up matrix for HIGH/CRITICAL risk commands.

    MEDIUM/LOW: plain ``approver`` role is sufficient (no step-up).
    HIGH/CRITICAL: requires an active elevated session.  If not elevated and
    a ``step_up_code`` is provided, attempt to verify it first.  If that
    fails or no code was provided, return 403 with ``reason: step_up_required``
    so the frontend can prompt inline.
    """
    if risk_class not in (RiskClass.HIGH, RiskClass.CRITICAL):
        return

    verifier = get_step_up_verifier()
    principal = _principal(user)

    if verifier.is_elevated(principal):
        return

    if step_up_code:
        if verifier.verify(principal, step_up_code):
            return
        raise HTTPException(
            status_code=403,
            detail={"reason": "step_up_required", "message": "Invalid or expired step-up code"},
        )

    raise HTTPException(
        status_code=403,
        detail={
            "reason": "step_up_required",
            "message": "Step-up authentication required for this action",
        },
    )


def _approval_to_dict(approval) -> dict[str, Any]:
    return {
        "approval_id": approval.approval_id,
        "proposal_id": approval.proposal_id,
        "payload_hash": approval.payload_hash,
        "target_agent_id": approval.target_agent_id,
        "requester": approval.requester,
        "idempotency_key": approval.idempotency_key,
        "approver": approval.approver,
        "approved_at": approval.approved_at,
        "expires_at": approval.expires_at,
        "status": approval.status.value,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/propose")
async def propose_command(
    body: ProposeRequest = Body(...),
    user: dict = Depends(require_approver),
) -> dict[str, Any]:
    """Create a command proposal for human approval.

    Requires ``approver`` role (or ``admin``).  The proposal is created
    in the ``PENDING`` state and must be separately approved and
    dispatched — this endpoint does not execute anything.

    Fails closed (409) if the interlock is PAUSED.
    """
    try:
        proposal = mcp_adapter.propose(
            command_name=body.command_name,
            target_agent_id=body.target_agent_id,
            requester=body.requester,
            payload=body.payload,
            idempotency_key=body.idempotency_key,
        )
    except InterlockPausedError:
        raise HTTPException(
            status_code=409,
            detail="Supervisor interlock is PAUSED — cannot propose",
        )
    except UnknownCommandError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "proposal_id": proposal.proposal_id,
        "command_name": proposal.command_name,
        "command_version": proposal.command_version,
        "target_agent_id": proposal.target_agent_id,
        "requester": proposal.requester,
        "payload": proposal.payload,
        "idempotency_key": proposal.idempotency_key,
        "created_at": proposal.created_at,
        "expires_at": proposal.expires_at,
        "status": proposal.status.value,
    }


@router.post("/approvals")
async def approve_proposal(
    body: ApproveRequest = Body(...),
    user: dict = Depends(require_approver),
) -> dict[str, Any]:
    """Approve a pending command proposal.

    Requires ``approver`` role (or ``admin``).  HIGH/CRITICAL-risk commands
    additionally require an active step-up session (D6.6).

    Returns the resulting ``CommandApproval`` as JSON.
    """
    proposal = await _proposal_store.get(body.proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.status != ProposalStatus.PENDING:
        raise HTTPException(
            status_code=410,
            detail=f"Proposal is no longer pending (status: {proposal.status.value})",
        )

    risk_class = _risk_class_for_proposal(proposal.command_name)
    _require_step_up(user, risk_class, body.step_up_code)

    approval = mcp_adapter.approve(proposal=proposal, approver=_principal(user))
    return _approval_to_dict(approval)


@router.post("/approvals/{approval_id}/dispatch")
async def dispatch_approval(
    approval_id: str,
    user: dict = Depends(require_approver),
) -> dict[str, Any]:
    """Dispatch an approved command.

    This is a **second, distinct, explicitly-clicked action** — never
    auto-dispatched inside the approve endpoint.  D6.2's short approval TTL
    and D5's interlock-revoke behavior exist precisely so a window remains
    between approve and dispatch for revocation.

    The same role/step-up gate as the approve endpoint applies, keyed off
    the proposal's ``risk_class`` (re-derived from the catalog).
    """
    approval = await _approval_store.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")

    proposal = await _proposal_store.get(approval.proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found for this approval")

    risk_class = _risk_class_for_proposal(proposal.command_name)
    _require_step_up(user, risk_class, None)

    try:
        result = mcp_adapter.dispatch(proposal=proposal, approval=approval)
    except DispatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # F1 — bridge the dispatch into a durable command record.
    # The adapter has consumed the approval and set DISPATCHED; now persist
    # the command so the CommandProcessor can publish it to the agent.
    try:
        command = await persist_command(
            proposal=proposal,
            approval=approval,
            dispatch_id=result["dispatch_id"],
        )
    except DispatchBridgeError as exc:
        mcp_adapter.audit.record(
            "dispatch_failed",
            actor=_principal(user),
            proposal_id=proposal.proposal_id,
            reason=exc.reason,
        )
        raise HTTPException(
            status_code=409,
            detail={"reason": exc.reason, "proposal_id": proposal.proposal_id},
        ) from exc

    result["command_id"] = command["command_id"]
    return result


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: str,
    body: RejectRequest = Body(default=RejectRequest()),
    user: dict = Depends(require_approver),
) -> dict[str, Any]:
    """Reject a pending command proposal.

    Requires ``approver`` role (or ``admin``).  No step-up — rejecting is
    the safe direction (D6.6).

    Returns 404 if the proposal doesn't exist, 409 if it's not pending.
    """
    proposal = await _proposal_store.get(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.status != ProposalStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"Proposal is not pending (status: {proposal.status.value})",
        )

    updated = await _proposal_store.update_status(proposal_id, ProposalStatus.REJECTED)
    if updated is None:
        raise HTTPException(status_code=409, detail="Proposal could not be rejected")

    mcp_adapter.audit.record(
        "reject",
        actor=_principal(user),
        proposal_id=proposal_id,
        reason=body.reason or "Rejected via NWI",
    )

    return {
        "proposal_id": proposal_id,
        "status": "rejected",
        "reason": body.reason or "Rejected via NWI",
    }
