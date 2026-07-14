"""
LiveTradingManager — DEPRECATED.

This module is preserved as a no-op stub only and must NOT be used for
execution in production.  All order routing, position management, and
account state are the sole responsibility of the Nautilus execution agent
(see ``live/kraken_node.py``).

The FastAPI backend should communicate with the Nautilus agent via
commands/events through a durable channel (PostgreSQL outbox or Redis
Streams), not direct exchange HTTP calls.

.. deprecated:: 2026-01-01
   Use ``live/kraken_node.py`` as the sole execution authority.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class AdapterConnection:
    """DEPRECATED: Kept for backward compatibility only."""
    adapter_id: str
    connection_id: str
    status: str = "disconnected"
    node: Any = None  # reserved for future TradingNode reference


class LiveTradingManager:
    """
    DEPRECATED: No-op stub.

    All exchange connections and order routing must go through the Nautilus
    execution agent (live/kraken_node.py).  This class exists only to avoid
    import errors in existing code that references ``live_manager``.
    """

    def __init__(self) -> None:
        self._connections: Dict[str, AdapterConnection] = {}
        self._is_active: bool = False
        self._lock: asyncio.Lock = asyncio.Lock()

    # ── Connection state (all no-op) ────────────────────────────────────────

    def is_connected(self, adapter_id: Optional[str] = None) -> bool:
        """DEPRECATED: Always returns False."""
        return False

    def get_status(self) -> Dict[str, Any]:
        """DEPRECATED: Returns minimal status."""
        return {
            "is_active": self._is_active,
            "connections": {},
            "note": "LiveTradingManager is deprecated. All execution goes through the Nautilus agent.",
        }

    # ── Adapter connections (all no-op) ─────────────────────────────────────

    async def connect_binance(self, api_key: str, api_secret: str) -> Dict[str, Any]:
        """DEPRECATED: No-op. Raises RuntimeError."""
        raise RuntimeError(
            "connect_binance() is DEPRECATED. "
            "All exchange connections must be managed through the Nautilus execution agent."
        )

    async def connect_bybit(self, api_key: str, api_secret: str) -> Dict[str, Any]:
        """DEPRECATED: No-op. Raises RuntimeError."""
        raise RuntimeError(
            "connect_bybit() is DEPRECATED. "
            "All exchange connections must be managed through the Nautilus execution agent."
        )

    async def disconnect(self, adapter_id: str) -> Dict[str, Any]:
        """DEPRECATED: No-op."""
        return {"success": True}

    # ── Order management (all no-op) ────────────────────────────────────────

    async def submit_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """DEPRECATED: No-op. Raises RuntimeError."""
        raise RuntimeError(
            "submit_order() is DEPRECATED. "
            "All orders must be submitted via the Nautilus execution agent."
        )

    async def cancel_order(self, order_id: str, symbol: str = "BTCUSDT") -> Dict[str, Any]:
        """DEPRECATED: No-op. Raises RuntimeError."""
        raise RuntimeError(
            "cancel_order() is DEPRECATED. "
            "All order cancellations must go through the Nautilus execution agent."
        )

    async def sync_positions(self) -> List[Dict[str, Any]]:
        """DEPRECATED: Returns empty list."""
        return []


async def process_order_update(update: Dict[str, Any]) -> None:
    """
    DEPRECATED: No-op stub.

    Order updates should be processed via Nautilus agent events (fill_event,
    order_event) published to the message bus or outbox table.
    """
    logger.warning(
        "process_order_update() is DEPRECATED. "
        "Order updates must come from the Nautilus execution agent."
    )