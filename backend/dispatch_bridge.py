"""F1 — MCP dispatch → durable command bridge.

This module is the single place where an approved MCP-origin proposal becomes
a durable NWI command record.  It is deliberately separate from
``mcp_adapter.py`` (D7: the MCP adapter never writes the command channel) and
from ``routers/orders.py`` / ``routers/strategies.py`` (human-origin paths).

The bridge revalidates at persist time because NWI is the authority, not the
caller.  See the revalidation table in ``docs/mcp_dispatch_bridge_plan.md``.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import commands
from stores import InterlockStore

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live.command_channel import FileCommandChannel
from mcp_gateway.approvals import hash_payload
from mcp_gateway.catalog import get_command
from mcp_gateway.interlock import evaluate_interlock
from mcp_gateway.models import CommandApproval, CommandProposal

logger = logging.getLogger("dispatch_bridge")


class DispatchBridgeError(Exception):
    """Raised when the bridge rejects a dispatch (fail-closed)."""

    def __init__(self, reason: str, proposal_id: str | None = None) -> None:
        self.reason = reason
        self.proposal_id = proposal_id
        super().__init__(reason)


async def persist(
    *,
    proposal: CommandProposal,
    approval: CommandApproval,
    dispatch_id: str,
    interlock_store: InterlockStore | None = None,
    channel: FileCommandChannel | None = None,
) -> dict[str, Any]:
    """Revalidate and create a durable command record for an approved proposal.

    Called **after** ``mcp_adapter.dispatch()`` has consumed the approval and
    set the proposal to ``DISPATCHED``.  If this function rejects, the approval
    is already spent — the caller must return HTTP 409 and require a fresh
    proposal + approval to retry.

    Publishes the command to the ``FileCommandChannel`` synchronously, in the
    same request, rather than leaving it to ``CommandProcessor``'s background
    poll loop. That loop runs inside this same process — if NWI crashes
    between the DB write and the loop's next tick, a command that was never
    published is invisible to the agent forever, even though the API already
    returned 200. Publishing here closes that window; the poll loop's own
    ``publish()`` call becomes a no-op retry (it checks ``has_in_flight``
    first) rather than the only path.

    Returns the created command dict (with ``command_id``).
    Raises ``DispatchBridgeError`` on any revalidation failure.
    """
    store = interlock_store or InterlockStore()

    # 1. Interlock effective state must be RESUMED
    record = await store.get()
    effective = evaluate_interlock(record, now=datetime.now(UTC))
    if effective.value != "resumed":
        raise DispatchBridgeError("interlock_paused", proposal_id=proposal.proposal_id)

    # 2. Command must be enabled in the catalog
    try:
        definition = get_command(proposal.command_name)
    except Exception as exc:
        raise DispatchBridgeError(
            "unknown_command", proposal_id=proposal.proposal_id
        ) from exc

    if not definition.enabled:
        raise DispatchBridgeError(
            "unknown_command", proposal_id=proposal.proposal_id
        )
    if definition.paper_only and os.environ.get("EXECUTION_MODE", "paper").lower() != "paper":
        raise DispatchBridgeError("paper_only", proposal_id=proposal.proposal_id)

    # 3. Payload must validate against the catalog schema (lightweight check)
    _validate_payload(definition.request_schema, proposal.payload)

    # 4. Payload hash must still match the approval's hash
    current_hash = hash_payload(proposal.payload)
    if current_hash != approval.payload_hash:
        raise DispatchBridgeError(
            "payload_mutated", proposal_id=proposal.proposal_id
        )

    # 5. Idempotency: check for an existing command with the same key
    idempotency_key = proposal.proposal_id
    existing = await commands.check_idempotency(idempotency_key)
    if existing is not None:
        logger.info(
            "Dispatch bridge: idempotent hit for proposal %s → command %s",
            proposal.proposal_id,
            existing["command_id"],
        )
        return existing

    # 6. Map proposal → CommandType and create the durable command
    command_type = commands.CommandType(proposal.command_name)
    payload = proposal.payload

    command = await commands.create_command(
        command_type=command_type,
        instrument=payload.get("instrument"),
        side=commands.CommandSide(payload["side"]) if "side" in payload else None,
        order_type=commands.OrderType(payload["order_type"]) if "order_type" in payload else None,
        quantity=payload.get("quantity"),
        price=payload.get("price"),
        strategy_id=payload.get("strategy_id"),
        idempotency_key=idempotency_key,
        origin="supervisor",
        proposal_id=proposal.proposal_id,
        approval_id=approval.approval_id,
        client_order_id=payload.get("client_order_id"),
        target_agent_id=proposal.target_agent_id,
    )

    # Advance to VALIDATED, then publish to the file channel immediately —
    # don't rely solely on CommandProcessor's next poll tick (see docstring).
    await commands.update_command_status(
        command["command_id"], commands.CommandStatus.VALIDATED
    )
    command["status"] = commands.CommandStatus.VALIDATED.value
    (channel or FileCommandChannel()).publish(command)

    logger.info(
        "Dispatch bridge: created command %s for proposal %s (origin=supervisor)",
        command["command_id"],
        proposal.proposal_id,
    )
    return command


def _validate_payload(schema: dict[str, Any], payload: dict[str, Any]) -> None:
    """Lightweight JSON-Schema-ish validation (shape + required fields).

    The catalog schemas are intentionally simple — this covers ``required``,
    ``type``, and ``additionalProperties: false``.  Anything more complex would
    require a schema library the codebase doesn't otherwise depend on.
    """
    if schema.get("type") == "object" and not isinstance(payload, dict):
        raise DispatchBridgeError("schema_invalid")

    for field in schema.get("required", []):
        if field not in payload:
            raise DispatchBridgeError("schema_invalid")

    if schema.get("additionalProperties") is False:
        allowed = set(schema.get("properties", {}).keys())
        extra = set(payload.keys()) - allowed
        if extra:
            raise DispatchBridgeError("schema_invalid")

    # Type checks for declared properties
    type_map = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
    }
    for field, spec in schema.get("properties", {}).items():
        if field not in payload:
            continue
        expected_type = type_map.get(spec.get("type"))
        if expected_type and not isinstance(payload[field], expected_type):
            raise DispatchBridgeError("schema_invalid")
        # enum check
        if "enum" in spec and payload[field] not in spec["enum"]:
            raise DispatchBridgeError("schema_invalid")
