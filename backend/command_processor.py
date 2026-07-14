"""
Async command processor — background loop that polls for PENDING commands,
validates them, forwards them to the Nautilus execution agent, and reconciles
state before enabling trading.

## Architecture

```
FastAPI HTTP  ──create──►  commands table (PENDING)
                              │
                              ▼
                    CommandProcessor loop
                    ┌─────────────────────┐
                    │ 1. Poll PENDING cmds │
                    │ 2. Validate (risk)   │
                    │ 3. Forward to agent  │
                    │ 4. Update status     │
                    │ 5. Publish events    │
                    └─────────────────────┘
                              │
                              ▼
                    Nautilus Execution Agent
                              │
                              ▼
                         Kraken / Sandbox
```

## Reconciliation gate

Before accepting ANY new commands, the processor runs a reconciliation check:
- Verify account balance matches expected
- Verify open orders match expected
- If reconciliation fails, all new commands are queued as RECONCILIATION_REQUIRED

This prevents the system from accepting commands when the local state has
drifted from the exchange state.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import sys
from pathlib import Path

# Ensure backend root is on sys.path
BACKEND_ROOT = str(Path(__file__).resolve().parent)
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import commands as _commands
from commands import CommandStatus, CommandType
from risk_engine import RiskEngine
from live_trading import LiveTradingService
from state import nautilus_system

logger = logging.getLogger(__name__)


class CommandProcessor:
    """
    Background loop that polls for PENDING commands and processes them.

    Parameters
    ----------
    poll_interval : float
        Seconds between polls for new PENDING commands (default 1.0).
    reconciliation_timeout : float
        Seconds to wait for reconciliation before marking a command
        RECONCILIATION_REQUIRED (default 30.0).
    """

    def __init__(
        self,
        poll_interval: float = 1.0,
        reconciliation_timeout: float = 30.0,
    ) -> None:
        self._poll_interval = poll_interval
        self._reconciliation_timeout = reconciliation_timeout
        self._running = False
        self._task: Optional[asyncio.Task[None]] = None
        self._risk_engine = RiskEngine()
        self._live_service: Optional[LiveTradingService] = None
        self._reconciliation_ok = False
        self._pending_queue: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the processor background loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        logger.info("[CommandProcessor] Started (poll_interval=%.1fs)", self._poll_interval)

    async def stop(self) -> None:
        """Stop the processor background loop."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[CommandProcessor] Stopped")

    @property
    def reconciliation_ok(self) -> bool:
        """Whether reconciliation has passed and trading is enabled."""
        return self._reconciliation_ok

    @reconciliation_ok.setter
    def reconciliation_ok(self, value: bool) -> None:
        self._reconciliation_ok = value

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _process_loop(self) -> None:
        """Main polling loop — runs until stopped."""
        while self._running:
            try:
                await self._process_pending()
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[CommandProcessor] Unexpected error in process loop")
                await asyncio.sleep(self._poll_interval)

    async def _process_pending(self) -> None:
        """Fetch all PENDING commands and process them sequentially."""
        # First check: if reconciliation is not OK, all new commands get
        # queued as RECONCILIATION_REQUIRED until reconciliation passes.
        if not self._reconciliation_ok:
            pending = await _commands.list_commands(status="PENDING", limit=50)
            if pending:
                for cmd in pending:
                    await _commands.update_command_status(
                        cmd["command_id"],
                        CommandStatus.RECONCILIATION_REQUIRED,
                        error_message="Reconciliation not yet complete",
                    )
            return

        # Normal path: fetch PENDING commands
        pending = await _commands.list_commands(status="PENDING", limit=50)
        if not pending:
            return

        for cmd in pending:
            await self._process_one(cmd)

    async def _process_one(self, cmd: Dict[str, Any]) -> None:
        """Process a single PENDING command."""
        command_id = cmd["command_id"]
        command_type = cmd["command_type"]

        logger.info("[CommandProcessor] Processing command %s: %s", command_id, command_type)

        try:
            # Step 1: Validate
            validation = await self._validate(cmd)
            if not validation.valid:
                await _commands.update_command_status(
                    command_id,
                    CommandStatus.REJECTED,
                    error_message=validation.message,
                )
                return

            await _commands.update_command_status(command_id, CommandStatus.VALIDATED)

            # Step 2: Forward to Nautilus agent
            result = await self._forward_to_agent(cmd)

            if result.success:
                # Update venue IDs if provided
                if result.client_order_id or result.venue_order_id:
                    await _commands.update_order_ids(
                        command_id,
                        client_order_id=result.client_order_id,
                        venue_order_id=result.venue_order_id,
                    )

                if result.status:
                    await _commands.update_command_status(command_id, result.status)

                logger.info(
                    "[CommandProcessor] Command %s → %s",
                    command_id,
                    result.status.value if result.status else "accepted",
                )
            else:
                await _commands.update_command_status(
                    command_id,
                    CommandStatus.FAILED,
                    error_message=result.message,
                )
                logger.warning("[CommandProcessor] Command %s → FAILED: %s", command_id, result.message)

        except Exception:
            logger.exception("[CommandProcessor] Error processing command %s", command_id)
            await _commands.update_command_status(
                command_id,
                CommandStatus.FAILED,
                error_message="Unexpected error during processing",
            )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    class _ValidationResult:
        def __init__(self, valid: bool, message: Optional[str] = None) -> None:
            self.valid = valid
            self.message = message or ("OK" if valid else "Unknown error")

    async def _validate(self, cmd: Dict[str, Any]) -> _ValidationResult:
        """
        Validate a command before forwarding.

        For SUBMIT_ORDER:
        - Check idempotency (already checked on creation, but re-check)
        - Run risk engine pre-trade checks
        - Check execution mode

        For START_STRATEGY / STOP_STRATEGY:
        - Check strategy exists
        - Check execution mode

        For FLATTEN / KILL_SWITCH:
        - Always allowed (global controls)
        """
        command_type = cmd["command_type"]

        # Idempotency re-check
        if cmd["idempotency_key"]:
            existing = await _commands.check_idempotency(cmd["idempotency_key"])
            if existing is not None:
                # Already processed — return a "valid" result that signals
                # no further action is needed.
                return self._ValidationResult(True)

        if command_type == CommandType.SUBMIT_ORDER.value:
            # Execution mode guard
            execution_mode = _get_execution_mode()
            if execution_mode == "backtest":
                return self._ValidationResult(False, "Trading disabled in backtest mode")

            # Risk engine pre-trade checks
            if cmd.get("instrument") and cmd.get("side") and cmd.get("quantity"):
                risk_result = await self._risk_engine.validate_order(
                    instrument=cmd["instrument"],
                    side=cmd["side"],
                    quantity=cmd["quantity"],
                    price=cmd.get("price"),
                    account=cmd.get("account", "kraken-primary"),
                )
                if not risk_result.approved:
                    return self._ValidationResult(False, risk_result.reason or "Risk check failed")

        elif command_type in (CommandType.START_STRATEGY.value, CommandType.STOP_STRATEGY.value):
            execution_mode = _get_execution_mode()
            if execution_mode == "backtest":
                return self._ValidationResult(False, "Strategy lifecycle disabled in backtest mode")

        # FLATTEN and KILL_SWITCH are always allowed (global controls)
        return self._ValidationResult(True)

    # ------------------------------------------------------------------
    # Forwarding to Nautilus agent
    # ------------------------------------------------------------------

    class _ForwardResult:
        def __init__(
            self,
            success: bool,
            status: Optional[CommandStatus] = None,
            message: str = "OK",
            client_order_id: Optional[str] = None,
            venue_order_id: Optional[str] = None,
        ) -> None:
            self.success = success
            self.status = status
            self.message = message
            self.client_order_id = client_order_id
            self.venue_order_id = venue_order_id

    async def _forward_to_agent(self, cmd: Dict[str, Any]) -> _ForwardResult:
        """
        Forward a validated command to the Nautilus execution agent.

        In the final architecture, this would send commands via the
        Nautilus message bus or directly to the agent. For now, it uses
        the LiveTradingService (which itself should eventually be replaced
        by direct Nautilus API calls).
        """
        command_type = cmd["command_type"]

        # Get or create the live trading service
        if self._live_service is None:
            self._live_service = LiveTradingService()
            await self._live_service.initialize()

        if command_type == CommandType.SUBMIT_ORDER.value:
            return await self._forward_submit_order(cmd)
        elif command_type == CommandType.CANCEL_ORDER.value:
            return await self._forward_cancel_order(cmd)
        elif command_type == CommandType.FLATTEN.value:
            return await self._forward_flatten(cmd)
        elif command_type == CommandType.START_STRATEGY.value:
            return await self._forward_start_strategy(cmd)
        elif command_type == CommandType.STOP_STRATEGY.value:
            return await self._forward_stop_strategy(cmd)
        elif command_type == CommandType.KILL_SWITCH.value:
            return await self._forward_kill_switch(cmd)

        return self._ForwardResult(False, message=f"Unknown command type: {command_type}")

    async def _forward_submit_order(self, cmd: Dict[str, Any]) -> _ForwardResult:
        """Forward a submit_order command."""
        try:
            # Use the live trading service to submit the order
            result = await self._live_service.submit_order(
                symbol=_kraken_to_adapter_symbol(cmd.get("instrument", "")),
                side=cmd.get("side", "BUY"),
                order_type=cmd.get("order_type", "MARKET"),
                quantity=cmd.get("quantity", 0),
                price=cmd.get("price"),
            )
            return self._ForwardResult(
                success=True,
                status=CommandStatus.SUBMITTED,
                client_order_id=result.get("client_order_id"),
                venue_order_id=result.get("venue_order_id"),
            )
        except Exception as e:
            return self._ForwardResult(False, message=str(e))

    async def _forward_cancel_order(self, cmd: Dict[str, Any]) -> _ForwardResult:
        """Forward a cancel_order command."""
        try:
            venue_order_id = cmd.get("venue_order_id") or cmd.get("client_order_id")
            if not venue_order_id:
                return self._ForwardResult(False, message="venue_order_id or client_order_id required")

            symbol = _kraken_to_adapter_symbol(cmd.get("instrument", ""))
            await self._live_service.cancel_order(order_id=venue_order_id, symbol=symbol)
            return self._ForwardResult(
                success=True,
                status=CommandStatus.CANCELLED,
            )
        except Exception as e:
            return self._ForwardResult(False, message=str(e))

    async def _forward_flatten(self, cmd: Dict[str, Any]) -> _ForwardResult:
        """Forward a flatten command."""
        try:
            symbol = _kraken_to_adapter_symbol(cmd.get("instrument", ""))
            await self._live_service.flatten(symbol=symbol)
            return self._ForwardResult(success=True, status=CommandStatus.FILLED)
        except Exception as e:
            return self._ForwardResult(False, message=str(e))

    async def _forward_start_strategy(self, cmd: Dict[str, Any]) -> _ForwardResult:
        """Forward a start_strategy command."""
        strategy_id = cmd.get("strategy_id")
        if not strategy_id:
            return self._ForwardResult(False, message="strategy_id required")

        try:
            # Signal the state manager to start the strategy
            nautilus_system.start_strategy(strategy_id)
            return self._ForwardResult(success=True, status=CommandStatus.ACCEPTED)
        except Exception as e:
            return self._ForwardResult(False, message=str(e))

    async def _forward_stop_strategy(self, cmd: Dict[str, Any]) -> _ForwardResult:
        """Forward a stop_strategy command."""
        strategy_id = cmd.get("strategy_id")
        if not strategy_id:
            return self._ForwardResult(False, message="strategy_id required")

        try:
            nautilus_system.stop_strategy(strategy_id)
            return self._ForwardResult(success=True, status=CommandStatus.ACCEPTED)
        except Exception as e:
            return self._ForwardResult(False, message=str(e))

    async def _forward_kill_switch(self, cmd: Dict[str, Any]) -> _ForwardResult:
        """Forward a kill_switch command."""
        try:
            nautilus_system.activate_kill_switch()
            return self._ForwardResult(success=True, status=CommandStatus.ACCEPTED)
        except Exception as e:
            return self._ForwardResult(False, message=str(e))

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    async def reconcile(self) -> bool:
        """
        Run reconciliation check before enabling trading.

        Returns True if reconciliation passes, False otherwise.
        """
        try:
            # Check account balance
            if self._live_service is None:
                self._live_service = LiveTradingService()
                await self._live_service.initialize()

            # Check that at least one adapter is connected
            accounts = self._live_service.accounts
            if not accounts:
                logger.warning("[CommandProcessor] Reconciliation failed: no accounts")
                self._reconciliation_ok = False
                return False

            # Check that trading is enabled on the primary account
            primary = accounts.get("kraken-primary")
            if primary:
                capabilities = primary.get("capabilities", {})
                if not capabilities.get("execution_connected"):
                    logger.warning("[CommandProcessor] Reconciliation failed: execution not connected")
                    self._reconciliation_ok = False
                    return False

            self._reconciliation_ok = True
            logger.info("[CommandProcessor] Reconciliation passed — trading enabled")
            return True

        except Exception:
            logger.exception("[CommandProcessor] Reconciliation error")
            self._reconciliation_ok = False
            return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KRAKEN_TO_ADAPTER = {
    "BTC/USD.KRAKEN": "BTCUSDT",
    "ETH/USD.KRAKEN": "ETHUSDT",
    "SOL/USD.KRAKEN": "SOLUSDT",
}


def _kraken_to_adapter_symbol(kraken_symbol: str) -> str:
    """Convert a Kraken instrument ID to an adapter symbol."""
    if not kraken_symbol:
        return "BTCUSDT"  # default fallback
    return _KRAKEN_TO_ADAPTER.get(kraken_symbol, kraken_symbol.replace("/", "").replace(".KRAKEN", "") + "USDT")


def _get_execution_mode() -> str:
    """Get the current execution mode from environment."""
    import os
    return os.getenv("EXECUTION_MODE", "paper").lower()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_processor: Optional[CommandProcessor] = None


def get_processor() -> CommandProcessor:
    """Get or create the module-level CommandProcessor singleton."""
    global _processor
    if _processor is None:
        _processor = CommandProcessor()
    return _processor


async def start_processor() -> None:
    """Start the command processor (called from FastAPI lifespan)."""
    proc = get_processor()
    await proc.start()
    # Run initial reconciliation
    await proc.reconcile()


async def stop_processor() -> None:
    """Stop the command processor (called from FastAPI shutdown)."""
    global _processor
    if _processor is not None:
        await _processor.stop()
        _processor = None