"""
Adapters router — DEPRECATED for live execution.

Supports listing all known adapters and managing connection credentials /
status via SQLite persistence (adapter_configs table).  Real exchange
connections must be managed through the Nautilus execution agent
(live/kraken_node.py), not direct HTTP calls from this router.

The connect/disconnect endpoints now only persist credential metadata
in the database and emit deprecation warnings — they no longer create
live exchange connections.

.. deprecated:: 2026-01-01
   Use ``live/kraken_node.py`` as the sole execution authority.
"""

from datetime import datetime, timezone
from typing import Optional

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import database
from auth_jwt import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["adapters"])

# ── Static adapter catalogue ──────────────────────────────────────────────────

_ADAPTERS: list[dict] = [
    {
        "id": "betfair", "name": "Betfair", "type": "Betting Exchange",
        "category": "Betting",
        "description": "Sports betting exchange adapter for Betfair markets",
        "docs_url": "https://nautilustrader.io/docs/nightly/integrations/betfair",
        "supports_live": True, "supports_backtest": True,
        "credential_fields": ["api_key", "api_secret"],
    },
    {
        "id": "binance", "name": "Binance", "type": "Crypto Exchange",
        "category": "Crypto",
        "description": "World's largest crypto exchange – spot and margin trading",
        "docs_url": "https://nautilustrader.io/docs/nightly/integrations/binance",
        "supports_live": True, "supports_backtest": True,
        "credential_fields": ["api_key", "api_secret"],
    },
    {
        "id": "binance_futures", "name": "Binance Futures", "type": "Crypto Futures",
        "category": "Crypto",
        "description": "Binance USD-M and COIN-M perpetual & dated futures",
        "docs_url": "https://nautilustrader.io/docs/nightly/integrations/binance",
        "supports_live": True, "supports_backtest": True,
        "credential_fields": ["api_key", "api_secret"],
    },
    {
        "id": "bybit", "name": "Bybit", "type": "Crypto Exchange",
        "category": "Crypto",
        "description": "Bybit spot, perpetuals, and options trading",
        "docs_url": "https://nautilustrader.io/docs/nightly/integrations/bybit",
        "supports_live": True, "supports_backtest": True,
        "credential_fields": ["api_key", "api_secret"],
    },
    {
        "id": "coinbase_advanced_trade", "name": "Coinbase Advanced Trade",
        "type": "Crypto Exchange", "category": "Crypto",
        "description": "Coinbase Advanced Trade API for professional trading",
        "docs_url": "https://nautilustrader.io/docs/nightly/integrations/coinbase",
        "supports_live": True, "supports_backtest": False,
        "credential_fields": ["api_key", "api_secret"],
    },
    {
        "id": "databento", "name": "Databento", "type": "Data Provider",
        "category": "Data",
        "description": "Historical and live institutional-grade market data",
        "docs_url": "https://nautilustrader.io/docs/nightly/integrations/databento",
        "supports_live": True, "supports_backtest": True,
        "credential_fields": ["api_key"],
    },
    {
        "id": "dydx", "name": "dYdX", "type": "DeFi Exchange",
        "category": "DeFi",
        "description": "Decentralized perpetuals exchange on Ethereum L2",
        "docs_url": "https://nautilustrader.io/docs/nightly/integrations/dydx",
        "supports_live": True, "supports_backtest": False,
        "credential_fields": ["api_key", "api_secret"],
    },
    {
        "id": "interactive_brokers", "name": "Interactive Brokers",
        "type": "Traditional Broker", "category": "Stocks & Futures",
        "description": "Multi-asset brokerage – stocks, futures, forex, options",
        "docs_url": "https://nautilustrader.io/docs/nightly/integrations/ib",
        "supports_live": True, "supports_backtest": False,
        "credential_fields": ["api_key"],
    },
    {
        "id": "okx", "name": "OKX", "type": "Crypto Exchange",
        "category": "Crypto",
        "description": "OKX spot, futures, options and DeFi trading",
        "docs_url": "https://nautilustrader.io/docs/nightly/integrations/okx",
        "supports_live": True, "supports_backtest": True,
        "credential_fields": ["api_key", "api_secret"],
    },
    {
        "id": "polymarket", "name": "Polymarket", "type": "Prediction Market",
        "category": "DeFi",
        "description": "On-chain decentralized prediction markets",
        "docs_url": "https://nautilustrader.io/docs/nightly/integrations/polymarket",
        "supports_live": True, "supports_backtest": False,
        "credential_fields": ["api_key"],
    },
    {
        "id": "tardis", "name": "Tardis", "type": "Data Provider",
        "category": "Data",
        "description": "Tick-level historical crypto market data replay",
        "docs_url": "https://nautilustrader.io/docs/nightly/integrations/tardis",
        "supports_live": False, "supports_backtest": True,
        "credential_fields": ["api_key"],
    },
]

_ADAPTER_BY_ID = {a["id"]: a for a in _ADAPTERS}


# ── Pydantic models ───────────────────────────────────────────────────────────

class AdapterConnectRequest(BaseModel):
    api_key: Optional[str] = None
    api_secret: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _enrich(adapter: dict) -> dict:
    """Merge static catalogue entry with persisted DB status."""
    cfg = await database.get_adapter_config(adapter["id"])
    # Status is now metadata-only — no longer indicates live connection
    status = cfg["status"] if cfg else "disconnected"
    last_connected = cfg["last_connected"] if cfg else None
    has_credentials = bool(cfg and cfg.get("api_key"))

    # Masked key: show last 4 chars only — never expose plaintext
    api_key_masked = ""
    if cfg and cfg.get("api_key"):
        from credential_utils import decrypt_credential, mask_credential
        decrypted = decrypt_credential(cfg["api_key"])
        api_key_masked = mask_credential(decrypted) if decrypted else "****"

    return {
        **adapter,
        "status": status,
        "last_connected": last_connected,
        "has_credentials": has_credentials,
        "api_key_masked": api_key_masked,
        "connection_id": None,  # No longer managed by FastAPI backend
        "deprecated_note": "Adapter connections are deprecated. Use live/kraken_node.py.",
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/adapters")
async def list_adapters():
    enriched = [await _enrich(a) for a in _ADAPTERS]
    return {"adapters": enriched, "count": len(enriched)}


@router.get("/adapters/{adapter_id}")
async def get_adapter(adapter_id: str):
    if adapter_id not in _ADAPTER_BY_ID:
        raise HTTPException(status_code=404, detail=f"Adapter '{adapter_id}' not found")
    return await _enrich(_ADAPTER_BY_ID[adapter_id])


@router.post("/adapters/{adapter_id}/connect")
async def connect_adapter(adapter_id: str, req: AdapterConnectRequest, _admin: dict = Depends(require_admin)):
    """
    DEPRECATED: Store credentials in DB only.

    Real exchange connections must be managed through the Nautilus execution
    agent (live/kraken_node.py).  This endpoint no longer creates live
    connections — it only persists credential metadata for reference.
    """
    if adapter_id not in _ADAPTER_BY_ID:
        raise HTTPException(status_code=404, detail=f"Adapter '{adapter_id}' not found")

    meta = _ADAPTER_BY_ID[adapter_id]
    required = meta.get("credential_fields", [])

    # Validate required fields are present, non-empty, and no null bytes
    missing = [f for f in required if not getattr(req, f, None)]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Missing required credentials: {', '.join(missing)}",
        )

    import re as _re
    api_key = (req.api_key or "").strip().replace("\x00", "")
    api_secret = (req.api_secret or "").strip().replace("\x00", "")

    # Reject oversized credentials
    if len(api_key) > 512 or len(api_secret) > 512:
        raise HTTPException(status_code=400, detail="Credential too long (max 512 chars)")

    # Minimum-length guard (real exchange keys are always >= 8 chars)
    if api_key and len(api_key) < 8:
        raise HTTPException(status_code=400, detail="api_key too short (min 8 chars)")
    if api_secret and len(api_secret) < 8:
        raise HTTPException(status_code=400, detail="api_secret too short (min 8 chars)")

    # Allow only printable ASCII (no control characters beyond what was already stripped)
    _safe_re = _re.compile(r'^[\x20-\x7E]+$')
    if api_key and not _safe_re.match(api_key):
        raise HTTPException(status_code=400, detail="api_key contains invalid characters")
    if api_secret and not _safe_re.match(api_secret):
        raise HTTPException(status_code=400, detail="api_secret contains invalid characters")

    # Encrypt credentials before storing
    from credential_utils import encrypt_credential
    encrypted_key = encrypt_credential(api_key) if api_key else ""
    encrypted_secret = encrypt_credential(api_secret) if api_secret else ""

    logger.warning(
        "DEPRECATED: connect_adapter() for '%s' — credentials stored in DB only. "
        "Live execution must go through the Nautilus agent (live/kraken_node.py).",
        adapter_id,
    )

    await database.upsert_adapter_config(
        adapter_id=adapter_id,
        status="connected",  # metadata-only; no longer indicates live connection
        api_key=encrypted_key,
        api_secret=encrypted_secret,
        last_connected=datetime.now(timezone.utc).isoformat(),
    )

    return {
        "success": True,
        "adapter_id": adapter_id,
        "status": "connected",
        "connection_id": None,
        "message": f"Credentials stored for '{meta['name']}' (deprecated — no live connection created).",
        "last_connected": datetime.now(timezone.utc).isoformat(),
        "deprecated": True,
    }


@router.post("/adapters/{adapter_id}/disconnect")
async def disconnect_adapter(adapter_id: str, _admin: dict = Depends(require_admin)):
    """DEPRECATED: Clear adapter status from DB. No live disconnection occurs."""
    if adapter_id not in _ADAPTER_BY_ID:
        raise HTTPException(status_code=404, detail=f"Adapter '{adapter_id}' not found")

    logger.warning(
        "DEPRECATED: disconnect_adapter() for '%s' — status cleared from DB only.",
        adapter_id,
    )

    await database.upsert_adapter_config(
        adapter_id=adapter_id,
        status="disconnected",
    )

    return {
        "success": True,
        "adapter_id": adapter_id,
        "status": "disconnected",
        "message": f"Adapter '{_ADAPTER_BY_ID[adapter_id]['name']}' disconnected (deprecated).",
    }