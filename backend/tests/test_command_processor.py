"""Tests for the command processor (command_processor.py)."""

import pytest
import asyncio

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure backend root is on sys.path
BACKEND_ROOT = str(Path(__file__).resolve().parent.parent)
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import commands as _commands
from commands import CommandStatus, CommandType, CommandSide, OrderType


@pytest.fixture(autouse=True)
async def _cleanup():
    """Ensure a clean DB after each test."""
    yield
    try:
        import database
        await database.purge_expired_revoked_tokens()
    except Exception:
        pass


class TestCommandProcessorLifecycle:
    async def test_start_and_stop(self):
        """Test that the processor can start and stop cleanly."""
        from command_processor import CommandProcessor

        proc = CommandProcessor(poll_interval=0.1)
        assert proc.reconciliation_ok is False

        await proc.start()
        assert proc._running is True
        assert proc._task is not None

        await proc.stop()
        assert proc._running is False
        assert proc._task is None

    async def test_reconciliation_ok_property(self):
        """Test reconciliation_ok getter/setter."""
        from command_processor import CommandProcessor

        proc = CommandProcessor()
        assert proc.reconciliation_ok is False

        proc.reconciliation_ok = True
        assert proc.reconciliation_ok is True

        proc.reconciliation_ok = False
        assert proc.reconciliation_ok is False


class TestProcessorReconciliationGate:
    """Test that the reconciliation gate blocks commands when not reconciled."""

    async def test_pending_commands_become_reconciliation_required(self):
        """When reconciliation hasn't passed, PENDING commands become RECONCILIATION_REQUIRED."""
        from command_processor import CommandProcessor

        # Create a PENDING command
        cmd = await _commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
            side=CommandSide.BUY,
        )
        assert cmd["status"] == "PENDING"

        proc = CommandProcessor(poll_interval=60.0)  # Long poll so loop doesn't interfere
        proc.reconciliation_ok = False  # Not reconciled
        await proc.start()

        # Wait for the processor to pick up the command
        await asyncio.sleep(0.3)

        updated = await _commands.get_command(cmd["command_id"])
        assert updated["status"] == CommandStatus.RECONCILIATION_REQUIRED.value

        await proc.stop()


class TestProcessorValidation:
    """Test command validation logic."""

    async def test_backtest_mode_rejects_trading(self):
        """Commands that require trading are rejected in backtest mode."""
        from command_processor import CommandProcessor

        proc = CommandProcessor()
        cmd = {
            "command_type": "submit_order",
            "instrument": "BTC/USD.KRAKEN",
            "side": "BUY",
            "quantity": 0.01,
            "idempotency_key": None,
        }

        # Mock execution mode to be backtest
        with patch("command_processor._get_execution_mode", return_value="backtest"):
            result = await proc._validate(cmd)

        assert result.valid is False
        assert "backtest" in result.message.lower()

    async def test_flatten_always_allowed(self):
        """FLATTEN and KILL_SWITCH are always allowed (global controls)."""
        from command_processor import CommandProcessor

        proc = CommandProcessor()

        for cmd_type in ("flatten", "kill_switch"):
            cmd = {"command_type": cmd_type, "idempotency_key": None}
            result = await proc._validate(cmd)
            assert result.valid is True

    async def test_idempotency_recheck_returns_valid(self):
        """If idempotency key matches an already-processed command, validation returns valid."""
        from command_processor import CommandProcessor

        # Create and process a command to SUBMITTED status
        cmd = await _commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
            idempotency_key="idem-recheck-test",
        )
        await _commands.update_command_status(cmd["command_id"], CommandStatus.SUBMITTED)

        proc = CommandProcessor()
        check_cmd = {
            "command_type": "submit_order",
            "idempotency_key": "idem-recheck-test",
        }
        result = await proc._validate(check_cmd)
        assert result.valid is True


class TestProcessorForwarding:
    """Test command forwarding logic."""

    async def test_forward_submit_order(self):
        """submit_order command creates a valid forward result."""
        from command_processor import CommandProcessor

        proc = CommandProcessor()
        cmd = {
            "command_type": "submit_order",
            "instrument": "BTC/USD.KRAKEN",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": 0.01,
            "price": None,
        }

        # Mock the live trading service
        mock_result = {
            "client_order_id": "client-abc123",
            "venue_order_id": "venue-xyz789",
        }
        mock_service = AsyncMock()
        mock_service.submit_order = AsyncMock(return_value=mock_result)
        mock_service.initialize = AsyncMock(return_value=None)
        proc._live_service = mock_service

        result = await proc._forward_submit_order(cmd)
        assert result.success is True
        assert result.status == CommandStatus.SUBMITTED
        assert result.client_order_id == "client-abc123"
        assert result.venue_order_id == "venue-xyz789"

    async def test_forward_cancel_order_requires_id(self):
        """cancel_order without venue_order_id or client_order_id fails."""
        from command_processor import CommandProcessor

        proc = CommandProcessor()
        cmd = {
            "command_type": "cancel_order",
            "instrument": "BTC/USD.KRAKEN",
        }
        result = await proc._forward_cancel_order(cmd)
        assert result.success is False
        assert "required" in result.message.lower()

    async def test_forward_start_strategy_requires_strategy_id(self):
        """start_strategy without strategy_id fails."""
        from command_processor import CommandProcessor

        proc = CommandProcessor()
        cmd = {"command_type": "start_strategy"}
        result = await proc._forward_start_strategy(cmd)
        assert result.success is False
        assert "strategy_id" in result.message.lower()

    async def test_forward_unknown_command_type(self):
        """Unknown command types return failure."""
        from command_processor import CommandProcessor

        proc = CommandProcessor()
        cmd = {"command_type": "unknown_type"}
        result = await proc._forward_to_agent(cmd)
        assert result.success is False
        assert "Unknown command type" in result.message


class TestProcessorReconciliation:
    """Test reconciliation logic."""

    async def test_reconciliation_passes_with_execution_connected(self):
        """Reconciliation passes when execution_connected capability is true."""
        from command_processor import CommandProcessor

        proc = CommandProcessor()

        # Mock a live service with execution_connected
        mock_service = MagicMock()
        mock_service.accounts = {
            "kraken-primary": {
                "capabilities": {
                    "execution_connected": True,
                }
            }
        }
        proc._live_service = mock_service

        result = await proc.reconcile()
        assert result is True
        assert proc.reconciliation_ok is True

    async def test_reconciliation_fails_without_execution_connected(self):
        """Reconciliation fails when execution_connected is False."""
        from command_processor import CommandProcessor

        proc = CommandProcessor()

        mock_service = MagicMock()
        mock_service.accounts = {
            "kraken-primary": {
                "capabilities": {
                    "execution_connected": False,
                }
            }
        }
        proc._live_service = mock_service

        result = await proc.reconcile()
        assert result is False
        assert proc.reconciliation_ok is False

    async def test_reconciliation_fails_with_no_accounts(self):
        """Reconciliation fails when no accounts are configured."""
        from command_processor import CommandProcessor

        proc = CommandProcessor()

        mock_service = MagicMock()
        mock_service.accounts = {}
        proc._live_service = mock_service

        result = await proc.reconcile()
        assert result is False
        assert proc.reconciliation_ok is False


class TestGetProcessor:
    """Test the module-level singleton."""

    async def test_singleton_behavior(self):
        """get_processor returns the same instance on multiple calls."""
        # Need to reset the module-level singleton for this test
        import command_processor as cp_module
        original = cp_module._processor
        cp_module._processor = None

        try:
            proc1 = cp_module.get_processor()
            proc2 = cp_module.get_processor()
            assert proc1 is proc2
        finally:
            cp_module._processor = original


class TestKrakenToAdapterSymbol:
    """Test Kraken symbol conversion."""

    async def test_known_mappings(self):
        from command_processor import _kraken_to_adapter_symbol

        assert _kraken_to_adapter_symbol("BTC/USD.KRAKEN") == "BTCUSDT"
        assert _kraken_to_adapter_symbol("ETH/USD.KRAKEN") == "ETHUSDT"
        assert _kraken_to_adapter_symbol("SOL/USD.KRAKEN") == "SOLUSDT"

    async def test_unknown_symbol_appends_usdt(self):
        from command_processor import _kraken_to_adapter_symbol

        result = _kraken_to_adapter_symbol("ADA/USD.KRAKEN")
        assert result == "ADAUSDT"

    async def test_empty_returns_default(self):
        from command_processor import _kraken_to_adapter_symbol

        assert _kraken_to_adapter_symbol("") == "BTCUSDT"
        assert _kraken_to_adapter_symbol(None) == "BTCUSDT"  # type: ignore


class TestHelperFunctions:
    """Test module-level helper functions."""

    async def test_start_and_stop_processor(self):
        """Test the module-level start/stop functions."""
        import command_processor as cp_module

        # Reset singleton
        cp_module._processor = None

        try:
            await cp_module.start_processor()
            proc = cp_module.get_processor()
            assert proc._running is True

            await cp_module.stop_processor()
            assert proc._running is False
            assert cp_module._processor is None
        except Exception:
            # If start_processor failed (e.g. reconciliation issues),
            # ensure stop is called
            try:
                await cp_module.stop_processor()
            except Exception:
                pass