from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

import database
import commands
from auth_jwt import get_current_user
from risk_engine import risk_engine, RiskCheckError
from state import nautilus_system
from utils import normalize_order

router = APIRouter(prefix="/api", tags=["orders"])


class OrderCreateRequest(BaseModel):
    instrument: str = Field("EUR/USD.SIM", min_length=1, max_length=50)
    side: str = Field("BUY", pattern="^(BUY|SELL)$")
    type: str = Field("MARKET", pattern="^(MARKET|LIMIT|STOP)$")
    quantity: float = Field(..., gt=0)
    price: Optional[float] = Field(None, ge=0)
    leverage: float = Field(1.0, ge=1.0, le=1000.0)
    strategy_id: Optional[str] = Field(None)
    account: Optional[str] = Field(None)


class OrderResponse(BaseModel):
    command_id: str
    status: str
    instrument: Optional[str] = None
    side: Optional[str] = None
    order_type: Optional[str] = None
    quantity: Optional[float] = None
    price: Optional[float] = None
    client_order_id: Optional[str] = None
    venue_order_id: Optional[str] = None
    error_message: Optional[str] = None
    submitted_at: Optional[str] = None
    completed_at: Optional[str] = None


@router.get("/orders")
async def list_orders():
    """List orders: backtest orders + persistent user-created orders."""
    all_orders: List[Dict[str, Any]] = []

    for results in nautilus_system.backtest_results.values():
        for o in results.get("orders", []):
            row = normalize_order(o)
            row["timestamp"] = datetime.now(timezone.utc).isoformat()
            all_orders.append(row)

    db_orders = await database.list_orders()
    all_orders.extend(db_orders)
    return {"orders": all_orders, "count": len(all_orders)}


@router.post("/orders", response_model=OrderResponse)
async def create_order(req: OrderCreateRequest, _user: dict = Depends(get_current_user)):
    """Submit a trading order through the durable command layer.

    The order is first recorded as a PENDING command, validated against
    risk limits, then submitted to the Nautilus execution agent. The caller
    receives a command_id they can use to poll for execution state.

    For .SIM instruments, the order is persisted as a paper order for
    local testing without requiring a live Nautilus agent.
    """
    order_dict = req.model_dump()

    # 1. Risk check — runs before command creation
    try:
        await risk_engine.check_order(order_dict)
    except RiskCheckError:
        raise  # Re-raise with 422

    # 2. Create durable command record
    command_type = commands.CommandType.SUBMIT_ORDER
    side = commands.CommandSide.BUY if req.side == "BUY" else commands.CommandSide.SELL
    order_type = commands.OrderType(req.type)

    # Check idempotency — prevent duplicate from webhook retries
    idempotency_key = f"{req.instrument}-{req.side}-{req.quantity}-{req.price or 'market'}"
    existing = await commands.check_idempotency(idempotency_key)
    if existing:
        return OrderResponse(
            command_id=existing["command_id"],
            status=existing["status"],
            instrument=existing["instrument"],
            side=existing["side"],
            order_type=existing["order_type"],
            quantity=existing["quantity"],
            price=existing["price"],
            client_order_id=existing["client_order_id"],
            venue_order_id=existing["venue_order_id"],
            submitted_at=existing["submitted_at"],
            completed_at=existing["completed_at"],
        )

    command = await commands.create_command(
        command_type=command_type,
        instrument=req.instrument,
        side=side,
        order_type=order_type,
        quantity=req.quantity,
        price=req.price,
        strategy_id=req.strategy_id,
        account=req.account or idempotency_key,
        idempotency_key=idempotency_key,
    )

    # 3. Update status to VALIDATED (risk passed)
    await commands.update_command_status(command["command_id"], commands.CommandStatus.VALIDATED)

    # 4. For .SIM instruments, persist as paper order for local testing
    if req.instrument.endswith(".SIM"):
        db_order = await database.create_order(
            instrument=req.instrument,
            side=req.side,
            order_type=req.type,
            quantity=req.quantity,
            price=req.price,
        )
        await database.log_action(
            action="order_created",
            user_id=_user.get("sub", ""),
            resource=f"order:{db_order['id']}",
            details=f"instrument={req.instrument} side={req.side} qty={req.quantity} price={req.price}",
        )
        # Update command with local order reference
        await commands.update_order_ids(
            command["command_id"],
            client_order_id=db_order["id"],
        )

    # 5. Update to SUBMITTED
    await commands.update_command_status(
        command["command_id"], commands.CommandStatus.SUBMITTED
    )

    await database.log_action(
        action="order_submitted",
        user_id=_user.get("sub", ""),
        resource=f"command:{command['command_id']}",
        details=f"instrument={req.instrument} side={req.side} qty={req.quantity} cmd={command['command_id']}",
    )

    return OrderResponse(
        command_id=command["command_id"],
        status=command["status"],
        instrument=command["instrument"],
        side=command["side"],
        order_type=command["order_type"],
        quantity=command["quantity"],
        price=command["price"],
        submitted_at=command["submitted_at"],
    )


@router.get("/commands")
async def list_commands(
    status: Optional[str] = None,
    command_type: Optional[str] = None,
    strategy_id: Optional[str] = None,
    limit: int = 100,
):
    """List durable commands with their current execution state."""
    cmds = await commands.list_commands(
        status=status,
        command_type=command_type,
        strategy_id=strategy_id,
        limit=limit,
    )
    return {"commands": cmds, "count": len(cmds)}


@router.get("/commands/{command_id}")
async def get_command_detail(command_id: str):
    """Get a single command with its event history."""
    command = await commands.get_command(command_id)
    if not command:
        raise HTTPException(status_code=404, detail=f"Command {command_id} not found")
    events = await commands.get_command_events(command_id)
    return {"command": command, "events": events}


@router.delete("/commands/{command_id}")
async def cancel_command(command_id: str, _user: dict = Depends(get_current_user)):
    """Cancel a pending or submitted order via durable command."""
    command = await commands.get_command(command_id)
    if not command:
        raise HTTPException(status_code=404, detail=f"Command {command_id} not found")

    if command["status"] in (
        commands.CommandStatus.FILLED.value,
        commands.CommandStatus.CANCELLED.value,
        commands.CommandStatus.EXPIRED.value,
    ):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel command in state: {command['status']}",
        )

    # Check idempotency
    existing = await commands.check_idempotency(command_id)
    if existing and existing["status"] == commands.CommandStatus.CANCELLED.value:
        return {"success": True, "command_id": existing["command_id"], "status": existing["status"]}

    # Submit cancel command
    cancel_cmd = await commands.create_command(
        command_type=commands.CommandType.CANCEL_ORDER,
        instrument=command["instrument"],
        strategy_id=command["strategy_id"],
        idempotency_key=f"cancel-{command_id}",
    )
    await commands.update_command_status(
        cancel_cmd["command_id"], commands.CommandStatus.VALIDATED
    )
    await commands.update_command_status(
        cancel_cmd["command_id"], commands.CommandStatus.SUBMITTED
    )

    # Also cancel in legacy orders table if present
    if command.get("client_order_id"):
        await database.cancel_order(command["client_order_id"])

    await database.log_action(
        action="order_cancel_requested",
        user_id=_user.get("sub", ""),
        resource=f"command:{command_id}",
    )

    return {
        "success": True,
        "command_id": cancel_cmd["command_id"],
        "original_command_id": command_id,
        "status": cancel_cmd["status"],
    }


@router.delete("/orders/{order_id}")
async def cancel_legacy_order(order_id: str, _user: dict = Depends(get_current_user)):
    """Cancel a legacy paper order (non-command path, for .SIM testing only)."""
    cancelled = await database.cancel_order(order_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found or already closed")

    await database.log_action(
        action="order_cancelled",
        user_id=_user.get("sub", ""),
        resource=f"order:{order_id}",
    )
    return {"success": True, "message": f"Order {order_id} cancelled"}
