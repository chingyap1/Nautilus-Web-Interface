"""
Positions router.

Open positions are read from the DB positions table (persisted after each
backtest run).  The UI-layer close action marks them as closed in the DB.
Current prices are enriched from market_data_service when the instrument
matches a supported crypto symbol (e.g. BTCUSDT).
"""

import asyncio
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

import database
from auth_jwt import get_current_user
import market_data_service as svc
from state import live_manager, nautilus_system

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["positions"])


async def _enrich_current_prices(positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Best-effort: add current_price and live unrealized_pnl to each position."""
    if not positions:
        return positions

    # Gather unique symbols that market_data_service knows about
    symbols_needed = {
        p["instrument"].upper()
        for p in positions
        if p.get("instrument", "").upper() in svc.SYMBOLS
    }

    if not symbols_needed:
        return positions

    # Fetch all needed prices concurrently
    price_map: Dict[str, float] = {}
    async def _fetch(sym: str) -> None:
        try:
            data = await svc.get_symbol_data(sym)
            price_map[sym] = data.get("price", 0.0)
        except Exception:
            pass  # keep whatever is already in DB

    await asyncio.gather(*[_fetch(s) for s in symbols_needed])

    enriched = []
    for pos in positions:
        p = dict(pos)
        sym = p.get("instrument", "").upper()
        if sym in price_map and price_map[sym] > 0:
            current = price_map[sym]
            p["current_price"] = current
            # Recompute unrealized PnL when entry_price is available
            entry = float(p.get("entry_price") or 0)
            qty = float(p.get("quantity") or 0)
            if entry > 0 and qty > 0:
                side = str(p.get("side", "")).upper()
                multiplier = 1.0 if "LONG" in side or "BUY" in side else -1.0
                p["pnl"] = round((current - entry) * qty * multiplier, 4)
        enriched.append(p)
    return enriched


@router.get("/positions")
async def list_positions():
    # Primary: DB-persisted positions (survive restarts, reflect latest backtest)
    db_positions = await database.list_db_positions(open_only=True)

    # Determine data source
    source = "live" if live_manager.is_connected() else "cached"

    if db_positions:
        enriched = await _enrich_current_prices(db_positions)
        for pos in enriched:
            pos["source"] = source
        return enriched

    # Fallback: in-memory backtest results (before first backtest persists to DB)
    all_positions = []
    for results in nautilus_system.backtest_results.values():
        all_positions.extend(results.get("positions", []))
    open_pos = [p for p in all_positions if p.get("is_open", False)]
    enriched = await _enrich_current_prices(open_pos)
    for pos in enriched:
        pos["source"] = source
    return enriched


@router.post("/positions/sync")
async def sync_positions(_user: dict = Depends(get_current_user)):
    """Sync open positions from the connected exchange.

    DEPRECATED: LiveTradingManager.sync_positions() is deprecated and returns
    an empty list.  Position data should come from the Nautilus execution agent
    via heartbeat events or the outbox table.
    """
    # live_manager.sync_positions() is deprecated — always returns []
    live_positions: List[Dict[str, Any]] = []

    # Persist synced positions to DB (no-op when empty)
    if live_positions:
        await database.save_positions(live_positions)

    return {
        "success": True,
        "synced_count": 0,
        "positions": [],
        "note": "Position sync is deprecated. Positions are managed by the Nautilus execution agent.",
    }


@router.post("/positions/{position_id}/close")
async def close_position(position_id: str, _user: dict = Depends(get_current_user)):
    """Close a position in the database.

    DEPRECATED: LiveTradingManager.submit_order() is deprecated and raises
    RuntimeError.  Position closing via exchange order must go through the
    Nautilus execution agent.  This endpoint only updates the local DB record.
    """
    # live_manager.is_connected() always returns False, so this block is skipped.
    # If it ever returns True in a future refactor, submit_order() would raise
    # RuntimeError because it's deprecated — keep the guard explicit.
    if live_manager.is_connected():
        try:
            positions = await database.list_db_positions(open_only=False)
            target = next((p for p in positions if p["id"] == position_id), None)
            if target:
                logger.warning(
                    "Position close via exchange is DEPRECATED. "
                    "Use the Nautilus execution agent to flatten or close positions."
                )
        except Exception as exc:
            logger.warning("Position lookup failed: %s", exc)

    closed = await database.close_db_position(position_id)
    return {
        "success": True,
        "closed_in_db": closed,
        "message": f"Position {position_id} marked as closed in local DB only. "
                   f"Exchange orders must be managed through the Nautilus execution agent.",
    }
