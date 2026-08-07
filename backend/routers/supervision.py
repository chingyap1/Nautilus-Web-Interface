"""B1 — NWI supervision API endpoints (D7, D9, D3–D6).

Exposes the ``SupervisionBridge`` (A7) over HTTP so the NWI frontend can
trigger supervision inspections and display their results.

**No direct dispatch:** these endpoints never call ``adapter.dispatch()`` or
``adapter.approve()``. They only create proposals — approval and dispatch
remain separate human-in-the-loop actions through the existing MCP adapter
flow.
"""

from __future__ import annotations

import os
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from auth_jwt import get_current_user, require_admin, require_operator
from mcp_adapter import InterlockPausedError, UnknownCommandError
from state import mcp_adapter
from supervision.bridge import SupervisionBridge
from supervision.health import AgentOffline

router = APIRouter(prefix="/api/supervision", tags=["supervision"])

# Shared bridge instance — uses the singleton MCPAdapter from state.py.
_bridge = SupervisionBridge(mcp_adapter)

# Default log directory: env var or framework-root logs/
# backend/routers/supervision.py → parents[0]=routers, [1]=backend,
# [2]=nautilus-web-interface, [3]=backtest_interface, [4]=framework root
_DEFAULT_LOG_DIR = str(
    Path(__file__).resolve().parents[4] / "logs"
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class InspectRequest(BaseModel):
    pair: str
    log_dir: str | None = None
    requester: str = "supervision"


class InterlockEngageRequest(BaseModel):
    reason: str = "Manual engage via NWI"


class InterlockResumeRequest(BaseModel):
    reason: str = "Manual resume via NWI"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dataclass_to_dict(obj: Any) -> Any:
    """Recursively convert a dataclass (with enums) to a JSON-safe dict."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _dataclass_to_dict(getattr(obj, f.name)) for f in fields(obj)}
    if hasattr(obj, "value"):
        return obj.value
    if isinstance(obj, dict):
        return {k: _dataclass_to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_dataclass_to_dict(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/inspect")
async def inspect(
    body: InspectRequest = Body(...),
    _user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Inspect an agent and create a proposal if the recommendation warrants one.

    Requires ``viewer`` role or higher (any authenticated user).

    Returns the ``SupervisionResult`` as JSON: health, metrics, recommendation,
    and optional proposal.
    """
    log_dir = body.log_dir or os.getenv("SUPERVISION_LOG_DIR", _DEFAULT_LOG_DIR)

    try:
        result = _bridge.inspect_and_propose(
            log_dir=log_dir,
            pair=body.pair,
            requester=body.requester,
        )
    except AgentOffline as exc:
        raise HTTPException(status_code=404, detail=f"Agent offline: {exc}")
    except InterlockPausedError:
        raise HTTPException(
            status_code=409,
            detail="Supervisor interlock is PAUSED — cannot propose",
        )
    except UnknownCommandError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    response = _dataclass_to_dict(result)
    # Flatten: proposal is a CommandProposal dataclass or None
    return response


@router.get("/interlock")
async def get_interlock(
    _user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the current interlock state and metadata (PAUSED or RESUMED)."""
    state = mcp_adapter.interlock_state()
    record = mcp_adapter.interlock_record()
    resp: dict[str, Any] = {"state": state.value}
    if record is not None:
        resp["actor"] = record.actor
        resp["reason"] = record.reason
        resp["updated_at"] = record.updated_at
        resp["lease_seconds"] = record.lease_seconds
    return resp


@router.post("/interlock/engage")
async def engage_interlock(
    body: InterlockEngageRequest = Body(default=InterlockEngageRequest()),
    _user: dict = Depends(require_operator),
) -> dict[str, Any]:
    """Engage the interlock (fail-closed). Requires operator role or higher."""
    record = mcp_adapter.engage_interlock(
        actor=_user.get("sub", _user.get("username", "unknown")),
        reason=body.reason,
    )
    return {
        "state": record.state.value,
        "actor": record.actor,
        "reason": record.reason,
        "updated_at": record.updated_at,
    }


@router.post("/interlock/resume")
async def resume_interlock(
    body: InterlockResumeRequest = Body(default=InterlockResumeRequest()),
    _user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """Resume the interlock. Requires admin role."""
    record = mcp_adapter.resume_interlock(
        actor=_user.get("sub", _user.get("username", "unknown")),
        reason=body.reason,
    )
    return {
        "state": record.state.value,
        "actor": record.actor,
        "reason": record.reason,
        "updated_at": record.updated_at,
    }


@router.get("/proposals")
async def list_proposals(
    _user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """List pending proposals created by supervision.

    Filters the audit log for ``supervision_propose`` actions and returns
    the corresponding proposals.
    """
    audit_entries = mcp_adapter.audit.entries()
    supervision_proposal_ids = [
        e["detail"].get("proposal_id")
        for e in audit_entries
        if e["action"] == "supervision_propose" and e["detail"].get("proposal_id")
    ]

    # Fetch pending proposals from the store
    from stores import ProposalStore

    store = ProposalStore()
    pending = await store.list_pending()

    # Filter to supervision-originated proposals
    supervision_proposals = [
        p for p in pending if p.proposal_id in supervision_proposal_ids
    ]

    return {
        "proposals": [
            {
                "proposal_id": p.proposal_id,
                "command_name": p.command_name,
                "target_agent_id": p.target_agent_id,
                "requester": p.requester,
                "payload": p.payload,
                "status": p.status.value,
                "created_at": p.created_at,
                "expires_at": p.expires_at,
            }
            for p in supervision_proposals
        ],
        "count": len(supervision_proposals),
    }


@router.get("/audit")
async def get_audit_log(
    _user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Return recent audit log entries (read-only activity feed)."""
    entries = mcp_adapter.audit.entries()
    return {
        "entries": entries,
        "count": len(entries),
    }
