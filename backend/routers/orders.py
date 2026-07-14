from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

import database
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


@router.post("/orders")
async def create_order(req: OrderCreateRequest, _user: dict = Depends(get_current_user)):
    order_dict = req.model_dump()

    # 1. Risk check — runs before anything else
    try:
        await risk_engine.check_order(order_dict)
    except RiskCheckError:
        raise  # Re-raise with 422

    # 2. Live execution is handled by the Nautilus agent (live/kraken_node.py).
    # The FastAPI backend no longer routes orders directly to exchanges.
    # For .SIM instruments, persist as paper orders for local testing.
    # For live instruments, orders must be submitted via the Nautilus agent
    # command interface (not yet implemented — see Phase 2 refactor).
    if not req.instrument.endswith(".SIM"):
        raise HTTPException(
            status_code=501,
            detail=(
                "Live instrument orders are no longer accepted through this endpoint. "
                "Use the Nautilus execution agent command interface (Phase 2 refactor in progress). "
                "For paper trading on live instruments, configure risk limits and use .SIM suffix."
            ),
        )

    # 3. Persist paper order to DB
    order = await database.create_order(
        instrument=req.instrument,
        side=req.side,
        order_type=req.type,
        quantity=req.quantity,
        price=req.price,
    )
    await database.log_action(
        action="order_created",
        user_id=_user.get("sub", ""),
        resource=f"order:{order['id']}",
        details=f"instrument={req.instrument} side={req.side} qty={req.quantity} price={req.price}",
    )
    return {"success": True, "order": order, "mode": "paper"}


@router.delete("/orders/{order_id}")
async def cancel_order(order_id: str, _user: dict = Depends(get_current_user)):
    # Live execution is handled by the Nautilus agent.  Only cancel paper orders here.
    cancelled = await database.cancel_order(order_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found or already closed")

    await database.log_action(
        action="order_cancelled",
        user_id=_user.get("sub", ""),
        resource=f"order:{order_id}",
    )
    return {"success": True, "message": f"Order {order_id} cancelled"}