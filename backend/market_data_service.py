"""
Market Data Service
===================
Fetches real-time market data from the Kraken public REST API (no API key
required).  A simple 5-second in-memory TTL cache sits in front of every
Kraken call so we stay well within rate limits.

Data quality indicators
-----------------------
 1. ``live``   -- fresh data from Kraken (< 5 s old)
 2. ``stale``  -- cached Kraken data (> 5 s old, but still valid during brief outages)
 3. ``unavail``-- no network and no cache; the frontend must display a clear
    "data unavailable" placeholder rather than silently using stale prices.

This intentionally avoids hard-coded fallback prices because mixing venues
(e.g. Binance prices for Kraken positions) creates incorrect P&L calculations.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supported symbols -- mapped to Kraken pair names
# ---------------------------------------------------------------------------
SYMBOLS: List[str] = [
    "XBTUSD",   # BTC / USD
    "ETHUSD",   # ETH / USD
    "SOLUSD",   # SOL / USD
]

# ---------------------------------------------------------------------------
# TTL cache
# ---------------------------------------------------------------------------
_CACHE_TTL_SECONDS = 5

# Per-symbol cache: symbol -> {"data": {...}, "quality": "live"|"stale", "fetched_at": float}
_symbol_cache: Dict[str, Dict[str, Any]] = {}
# All-symbols cache for the instruments list endpoint
_instruments_cache: Optional[Dict[str, Any]] = None

# A lock so concurrent requests don't all fire off to Kraken simultaneously
_fetch_lock = asyncio.Lock()

KRAKEN_BASE = "https://api.kraken.com"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_kraken_ticker(ticker_dict: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    """Convert a Kraken Ticker response dict to our internal format.

    Kraken returns arrays inside the result dict for each pair.  We normalise
    to the same schema used by all consumers of this module.
    """
    return {
        "symbol": symbol,
        "base": symbol[:-4] if symbol.endswith("USD") else symbol.split("USD")[0],
        "quote": "USD",
        "exchange": "KRAKEN",
        "price": float(ticker_dict.get("c", [0])[0]) if ticker_dict.get("c") else 0.0,
        "change_24h": float(ticker_dict.get("p", [0])[0]) if ticker_dict.get("p") else 0.0,
        "bid": float(ticker_dict.get("b", [0])[0]) if ticker_dict.get("b") else 0.0,
        "ask": float(ticker_dict.get("a", [0])[0]) if ticker_dict.get("a") else 0.0,
        "volume_24h": float(ticker_dict.get("v", [0])[0]) if ticker_dict.get("v") else 0.0,
        "quality": "live",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _cache_is_fresh(entry: Optional[Dict[str, Any]]) -> bool:
    return (
        entry is not None
        and (time.monotonic() - entry["fetched_at"]) < _CACHE_TTL_SECONDS
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_instruments() -> List[Dict[str, Any]]:
    """
    Return 24-hr ticker data for all supported symbols.

    Hits Kraken's public ticker endpoint.  Results are cached for CACHE_TTL
    seconds.  On complete failure the cache returns stale data with a
    ``quality`` tag so the frontend can display a clear staleness warning
    instead of silently using unknown prices.
    """
    global _instruments_cache

    async with _fetch_lock:
        if _cache_is_fresh(_instruments_cache):
            return _instruments_cache["data"]  # type: ignore[index]

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{KRAKEN_BASE}/api/1/Public/Ticker",
                    params={"pair": ",".join(SYMBOLS)},
                )
                resp.raise_for_status()
                result_dict = resp.json()

            # Kraken returns results in result_dict["result"] keyed by wrapped pair names
            if not isinstance(result_dict, dict):
                raise ValueError("Unexpected response format from Kraken")

            result: List[Dict[str, Any]] = []
            order = {s: i for i, s in enumerate(SYMBOLS)}

            for symbol in SYMBOLS:
                # Kraken wraps pairs with "X" prefix + quote currency, e.g. XXBTUSD -> X{pair}
                kraken_key = f"{symbol[0]}{symbol}"
                ticker_dict = result_dict.get("result", {}).get(kraken_key)
                if not ticker_dict or not isinstance(ticker_dict, dict):
                    continue

                parsed = _parse_kraken_ticker(ticker_dict, symbol)
                # Update per-symbol cache as a side-effect
                _symbol_cache[symbol] = {
                    "data": parsed,
                    "quality": "live",
                    "fetched_at": time.monotonic(),
                }
                result.append(parsed)

            result.sort(key=lambda x: order.get(x["symbol"], 99))

            _instruments_cache = {"data": result, "fetched_at": time.monotonic()}
            return result

        except Exception as exc:
            logger.warning("Market data fetch failed: %s", exc)
            # Return stale cache if available (will be tagged as stale below)
            cached_items = [
                _symbol_cache[s]["data"]
                for s in SYMBOLS
                if s in _symbol_cache
            ]
            if cached_items:
                # Mark all cached items as stale
                for item in cached_items:
                    item["quality"] = "stale"
                return cached_items

            # Cold start with no network -- mark all as unavailable
            return [
                {
                    "symbol": s,
                    "base": s[:-4] if s.endswith("USD") else s.split("USD")[0],
                    "quote": "USD",
                    "exchange": "KRAKEN",
                    "price": 0.0,
                    "change_24h": 0.0,
                    "bid": 0.0,
                    "ask": 0.0,
                    "volume_24h": 0.0,
                    "quality": "unavail",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                for s in SYMBOLS
            ]


async def get_symbol_data(symbol: str) -> Dict[str, Any]:
    """
    Return 24-hr ticker data for a single symbol.

    Uses the per-symbol cache; falls back to a fresh Kraken request if
    stale, then to the last known cached value.  On total failure returns
    an ``unavail`` marker -- never hard-coded prices.
    """
    upper = symbol.upper()

    async with _fetch_lock:
        entry = _symbol_cache.get(upper)
        if entry and _cache_is_fresh(entry):
            data = entry["data"]
            data["quality"] = "live"
            return data

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{KRAKEN_BASE}/api/1/Public/Ticker",
                    params={"pair": upper},
                )
                resp.raise_for_status()
                result_dict = resp.json()

            kraken_key = f"{upper[0]}{upper}"
            ticker_dict = result_dict.get("result", {}).get(kraken_key)
            if not ticker_dict or not isinstance(ticker_dict, dict):
                raise ValueError(f"No ticker data for {upper}")

            data = _parse_kraken_ticker(ticker_dict, upper)
            _symbol_cache[upper] = {"data": data, "quality": "live", "fetched_at": time.monotonic()}
            return data

        except Exception as exc:
            logger.warning("Market data fetch failed for %s: %s", upper, exc)
            # Return stale cache if available
            if upper in _symbol_cache:
                cached = _symbol_cache[upper]["data"].copy()
                cached["quality"] = "stale"
                return cached

            # Total failure -- mark unavailable (never hard-coded prices)
            return {
                "symbol": upper,
                "base": upper[:-4] if upper.endswith("USD") else upper.split("USD")[0],
                "quote": "USD",
                "exchange": "KRAKEN",
                "price": 0.0,
                "change_24h": 0.0,
                "bid": 0.0,
                "ask": 0.0,
                "volume_24h": 0.0,
                "quality": "unavail",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }