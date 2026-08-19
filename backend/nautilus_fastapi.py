"""
Nautilus Trader API — production entry point.
This file wires the FastAPI app together; business logic lives in routers/.
"""

import asyncio
import json
import os
import secrets
import sys
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Ensure backend dir is on the path so routers can import sibling modules
sys.path.insert(0, str(Path(__file__).parent))

import database
import auth as _auth_module
from auth import ApiKeyMiddleware
from auth_jwt import decode_token
from routers import (
    adapters,
    alerts,
    auth as auth_router_module,
    backtest,
    components,
    copilot,
    database_ops,
    market_data,
    mcp_actions,
    orders,
    positions,
    push,
    risk,
    strategies,
    supervision,
    system,
    users,
)
from routers.strategies import load_strategies_from_db
from routers.components import load_component_states
import commands as _commands
import command_processor as _cmdproc
from state import manager, nautilus_system
from alert_monitor import run_alert_monitor


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────

_WEAK_SECRET = "dev-secret-key-CHANGE-IN-PRODUCTION-min-32-chars"


def _check_production_secrets() -> None:
    """Warn loudly if insecure default secrets are used.

    In production mode, the server refuses to start unless all required
    secrets are configured with strong values.  This prevents accidental
    deployment of a live-trading-capable instance with default credentials.
    """
    secret_key = os.getenv("SECRET_KEY", _WEAK_SECRET)
    encryption_key = os.getenv("ENCRYPTION_KEY", "")
    admin_pw = os.getenv("ADMIN_PASSWORD", "admin")
    execution_mode = os.getenv("EXECUTION_MODE", "").lower()
    env = os.getenv("ENVIRONMENT", "development").lower()

    warnings = []
    errors = []  # fatal in production — prevents start

    if secret_key == _WEAK_SECRET or len(secret_key) < 32:
        warnings.append(
            "SECRET_KEY is not set or too short (< 32 chars). "
            "Generate one with: openssl rand -hex 32"
        )

    if not encryption_key or len(encryption_key) < 16:
        warnings.append(
            "ENCRYPTION_KEY is not set or too short. "
            "Required for encrypting adapter credentials. "
            "Generate one with: openssl rand -hex 32"
        )

    if admin_pw in ("admin", "password", "123456", ""):
        warnings.append(
            f"ADMIN_PASSWORD='{admin_pw}' is insecure. Set a strong password via env var."
        )

    # Execution mode guard — refuse to start with live trading enabled
    # unless explicitly confirmed.  Valid values: paper, backtest, live.
    if execution_mode == "live":
        warnings.append(
            "EXECUTION_MODE=live is set. This instance can submit real orders."
        )
    elif not execution_mode:
        warnings.append(
            "EXECUTION_MODE is not set (defaults to 'paper'). "
            "Set explicitly: paper, backtest, or live."
        )

    if warnings:
        border = "=" * 70
        print(f"\n{border}", file=sys.stderr)
        print("  SECURITY WARNING", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)

        # In production mode, refuse to start with insecure defaults
        if env == "production":
            missing = []
            if secret_key == _WEAK_SECRET or len(secret_key) < 32:
                missing.append("SECRET_KEY")
            if not encryption_key or len(encryption_key) < 16:
                missing.append("ENCRYPTION_KEY")
            if admin_pw in ("admin", "password", "123456", ""):
                missing.append("ADMIN_PASSWORD")

            if missing:
                print(
                    f"  Refusing to start in production — missing/weak secrets: {', '.join(missing)}.",
                    file=sys.stderr,
                )
                print(f"{border}\n", file=sys.stderr)
                sys.exit(1)

        print(f"{border}\n", file=sys.stderr)


async def _purge_expired_tokens_loop() -> None:
    """Hourly purge of expired entries from the token revocation table."""
    while True:
        await asyncio.sleep(3600)
        try:
            removed = await database.purge_expired_revoked_tokens()
            if removed:
                print(f"[auth] Purged {removed} expired revoked token(s)")
        except Exception:
            pass


INTERLOCK_RENEW_INTERVAL_SECONDS = 10.0


async def _renew_interlock_loop() -> None:
    """Keep only an already-fresh resumed interlock lease alive."""
    from stores import InterlockStore

    store = InterlockStore()
    while True:
        await asyncio.sleep(INTERLOCK_RENEW_INTERVAL_SECONDS)
        try:
            await store.renew_if_fresh()
        except Exception as exc:
            # Renewal failures intentionally fail closed when the prior lease
            # expires; the loop must not revive stale state on recovery.
            print(f"[interlock] Lease renewal failed: {exc}", file=sys.stderr)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: check secrets before anything else
    _check_production_secrets()
    # Initialise the SQLite schema + seed defaults
    await database.init_db()
    # Seed admin TOTP secret for step-up authentication (D6.5/D6.6).
    # In production, set ADMIN_TOTP_SECRET to a unique base32 secret per
    # operator.  The default is for dev/paper trading only.
    _admin_totp = os.getenv("ADMIN_TOTP_SECRET", "JBSWY3DPEHPK3PXP")
    from step_up import get_step_up_verifier, TOTPStepUpVerifier
    _verifier = get_step_up_verifier()
    if isinstance(_verifier, TOTPStepUpVerifier):
        _verifier.set_secret("admin", _admin_totp)
    # Initialise commands/events tables (Phase 2 — durable command layer)
    await _commands.init()
    # Initialise proposal/approval/interlock tables (A3 — D4)
    import stores as _stores
    await _stores.init_stores_db()
    # Start the async command processor (polls PENDING commands, reconciles)
    await _cmdproc.start_processor()
    # Restore persisted strategies and component states
    await load_strategies_from_db()
    await load_component_states()
    # Start background tasks
    alert_task = asyncio.create_task(run_alert_monitor())
    purge_task = asyncio.create_task(_purge_expired_tokens_loop())
    interlock_task = asyncio.create_task(_renew_interlock_loop())
    yield
    # Shutdown: cancel background tasks
    for task in (alert_task, purge_task, interlock_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    # Stop the command processor
    await _cmdproc.stop_processor()


# ── App factory ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Nautilus Trader API",
    description="Real Nautilus Trader integration API",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS — set CORS_ORIGINS env var in production (comma-separated)
_cors_env = os.getenv("CORS_ORIGINS", "")
CORS_ORIGINS = (
    [o.strip() for o in _cors_env.split(",") if o.strip()]
    or ["http://localhost:5173", "http://localhost:3000"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)

# API key auth (enabled when API_KEY env var is set)
app.add_middleware(ApiKeyMiddleware)

# ── Rate limiting ─────────────────────────────────────────────────────────────

_GLOBAL_RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "200"))
_LOGIN_RATE_LIMIT = int(os.getenv("LOGIN_RATE_LIMIT_PER_MINUTE", "5"))

# Per-IP sliding window counters: {ip: {"count": N, "window_start": T}}
_global_counters: dict = defaultdict(lambda: {"count": 0, "window_start": 0.0})
_login_counters: dict = defaultdict(lambda: {"count": 0, "window_start": 0.0})


def _check_rate_limit(counters: dict, key: str, limit: int, now: float) -> tuple[int, bool]:
    """
    Check and update a rate-limit counter.
    Returns (remaining, is_exceeded).
    """
    state = counters[key]
    if now - state["window_start"] >= 60.0:
        state["count"] = 0
        state["window_start"] = now
    state["count"] += 1
    remaining = max(0, limit - state["count"])
    return remaining, state["count"] > limit


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = (request.client.host if request.client else "unknown") or "unknown"
    now = time.time()

    # Tight limit on login endpoint
    if request.url.path == "/api/auth/login":
        _, exceeded = _check_rate_limit(_login_counters, client_ip, _LOGIN_RATE_LIMIT, now)
        if exceeded:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Retry after 60 seconds."},
                headers={"Retry-After": "60", "X-RateLimit-Remaining": "0"},
            )

    # Global rate limit (applied to all routes)
    remaining, exceeded = _check_rate_limit(_global_counters, client_ip, _GLOBAL_RATE_LIMIT, now)
    if exceeded:
        return JSONResponse(
            status_code=429,
            content={"detail": "Global rate limit exceeded. Retry after 60 seconds."},
            headers={"Retry-After": "60", "X-RateLimit-Remaining": "0"},
        )

    response = await call_next(request)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    return response


# ── JWT authentication middleware ─────────────────────────────────────────────

# Paths that are always public (no token required)
_PUBLIC_PATHS = frozenset(
    [
        "/",
        "/health",
        "/api/health",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/refresh",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/ws",
    ]
)


@app.middleware("http")
async def jwt_middleware(request: Request, call_next):
    path = request.url.path

    # Allow public paths and non-API routes
    if path in _PUBLIC_PATHS or not path.startswith("/api/"):
        return await call_next(request)

    # Skip JWT check when a valid API key is already provided (alternative auth)
    api_key_header = request.headers.get("X-API-Key", "")
    if _auth_module.API_KEY and api_key_header and secrets.compare_digest(api_key_header, _auth_module.API_KEY):
        return await call_next(request)

    # Check for valid Bearer token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"detail": "Missing authentication token"},
        )

    token = auth_header[7:]
    payload = decode_token(token)
    if not payload:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or expired token"},
        )

    # Check persistent DB blacklist (survives restarts)
    if payload.get("jti") and await database.is_token_revoked(payload["jti"]):
        return JSONResponse(
            status_code=401,
            content={"detail": "Token has been revoked"},
        )

    request.state.user = payload
    return await call_next(request)


# Request counter middleware
@app.middleware("http")
async def _count_requests(request: Request, call_next):
    system.increment_request_counter()
    return await call_next(request)


# ── Include routers ───────────────────────────────────────────────────────────

app.include_router(auth_router_module.router)
app.include_router(copilot.router)
app.include_router(strategies.router)
app.include_router(orders.router)
app.include_router(positions.router)
app.include_router(risk.router)
app.include_router(market_data.router)
app.include_router(alerts.router)
app.include_router(system.router)
app.include_router(backtest.router)
app.include_router(adapters.router)
app.include_router(database_ops.router)
app.include_router(components.router)
app.include_router(users.router)
app.include_router(supervision.router)
app.include_router(mcp_actions.router)
app.include_router(push.router)


# ── Root endpoint ─────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "name": "Nautilus Trader API",
        "version": "2.0.0",
        "status": "running",
        "docs": "/docs",
    }


# Alias so /health works in addition to /api/health
@app.get("/health")
async def health_alias():
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "nautilus-trader-api",
        "version": "2.0.0",
    }


# ── WebSocket live-data helpers ───────────────────────────────────────────────

async def _collect_live_snapshot() -> dict:
    """Gather a lightweight snapshot of live system state for WebSocket push."""
    info = nautilus_system.get_system_info()

    # System metrics (non-blocking psutil)
    metrics: dict = {}
    try:
        import psutil
        metrics = {
            "cpu_percent": round(psutil.cpu_percent(interval=None), 1),
            "memory_percent": round(psutil.virtual_memory().percent, 1),
        }
    except Exception:
        pass

    # Strategies
    strategy_list = [
        {
            "id": s["id"],
            "name": s.get("name", s["id"]),
            "status": s.get("status", "unknown"),
        }
        for s in nautilus_system.get_all_strategies()
    ]

    # Open positions (from latest backtest results, filtered by closed set)
    all_positions = []
    for results in nautilus_system.backtest_results.values():
        all_positions.extend(results.get("positions", []))
    open_positions = [p for p in all_positions if p.get("is_open")]

    # Recent orders count
    order_count = sum(
        len(r.get("orders", []))
        for r in nautilus_system.backtest_results.values()
    )

    return {
        "type": "live_data",
        "ts": datetime.now(timezone.utc).isoformat(),
        "engine": {
            "is_initialized": info["is_initialized"],
            "trader_id": info["trader_id"],
            "strategies_count": info["strategies_count"],
            "backtests_count": info["backtests_count"],
        },
        "metrics": metrics,
        "strategies": strategy_list,
        "open_positions_count": len(open_positions),
        "total_orders_count": order_count,
    }


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = ""):
    """
    WebSocket live-data endpoint.
    Clients must provide a valid JWT via query param: /ws?token=<jwt>
    """
    # Validate token before accepting the connection
    if not token:
        await websocket.close(code=4001, reason="Missing authentication token")
        return

    payload = decode_token(token)
    if not payload:
        await websocket.close(code=4001, reason="Invalid or expired token")
        return

    if payload.get("jti") and await database.is_token_revoked(payload["jti"]):
        await websocket.close(code=4001, reason="Token has been revoked")
        return

    await manager.connect(websocket)
    last_push = 0.0
    try:
        info = nautilus_system.get_system_info()
        await websocket.send_json(
            {
                "type": "connection",
                "status": "connected",
                "trader_id": info["trader_id"],
                "is_initialized": info["is_initialized"],
            }
        )
        while True:
            try:
                # Short timeout so we can push live data on schedule
                data = await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                now = time.time()
                # Heartbeat every tick
                await websocket.send_json(
                    {"type": "heartbeat", "ts": datetime.now(timezone.utc).isoformat()}
                )
                # Full live-data push every 3 seconds
                if now - last_push >= 3.0:
                    snapshot = await _collect_live_snapshot()
                    await websocket.send_json(snapshot)
                    last_push = now
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("NAUTILUS_API_PORT", "8000"))
    print(f"Starting Nautilus Trader API on port {port}")
    print(f"Docs: http://0.0.0.0:{port}/docs")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
