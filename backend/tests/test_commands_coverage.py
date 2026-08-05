"""B3a — Additional coverage tests for commands.py.

Extends the existing test_commands.py to cover:
- Full PENDING → VALIDATED → SUBMITTED → ACCEPTED → FILLED lifecycle
- validate_catalog_drift()
- update_order_ids with both IDs simultaneously
- update_order_ids with no IDs (returns False)
- init_commands_db() idempotency
- init() wrapper
- ACCEPTED status sets completed_at
- PARTIALLY_FILLED status
- CANCELLING status
- RECONCILIATION_REQUIRED status
- get_command_events after status transitions
- list_commands with all filters combined
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = str(Path(__file__).resolve().parent.parent)
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import commands
from commands import (
    CommandSide,
    CommandStatus,
    CommandType,
    OrderType,
)


class TestFullLifecycle:
    """Test the full PENDING → VALIDATED → SUBMITTED → ACCEPTED → FILLED lifecycle."""

    async def test_full_lifecycle_submit_order(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
            side=CommandSide.BUY,
            order_type=CommandType.MARKET,
            quantity=0.5,
            strategy_id="str-lifecycle",
        )
        assert cmd["status"] == "PENDING"

        # VALIDATED
        cmd = await commands.update_command_status(
            cmd["command_id"], CommandStatus.VALIDATED
        )
        assert cmd["status"] == "VALIDATED"
        assert cmd["completed_at"] is None

        # SUBMITTED
        cmd = await commands.update_command_status(
            cmd["command_id"], CommandStatus.SUBMITTED
        )
        assert cmd["status"] == "SUBMITTED"
        assert cmd["submitted_at"] is not None

        # ACCEPTED
        cmd = await commands.update_command_status(
            cmd["command_id"], CommandStatus.ACCEPTED
        )
        assert cmd["status"] == "ACCEPTED"
        assert cmd["completed_at"] is not None

        # FILLED
        cmd = await commands.update_command_status(
            cmd["command_id"], CommandStatus.FILLED
        )
        assert cmd["status"] == "FILLED"

    async def test_lifecycle_with_rejection(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="ETH/USD.KRAKEN",
            side=CommandSide.SELL,
        )
        await commands.update_command_status(cmd["command_id"], CommandStatus.VALIDATED)
        cmd = await commands.update_command_status(
            cmd["command_id"], CommandStatus.REJECTED, error_message="Bad order"
        )
        assert cmd["status"] == "REJECTED"
        assert cmd["error_message"] == "Bad order"
        assert cmd["completed_at"] is not None

    async def test_lifecycle_with_cancellation(self):
        cmd = await commands.create_command(
            command_type=CommandType.CANCEL_ORDER,
            instrument="BTC/USD.KRAKEN",
        )
        await commands.update_command_status(cmd["command_id"], CommandStatus.VALIDATED)
        await commands.update_command_status(cmd["command_id"], CommandStatus.SUBMITTED)
        await commands.update_command_status(cmd["command_id"], CommandStatus.CANCELLING)
        cmd = await commands.update_command_status(
            cmd["command_id"], CommandStatus.CANCELLED
        )
        assert cmd["status"] == "CANCELLED"
        assert cmd["completed_at"] is not None

    async def test_partially_filled_status(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
            side=CommandSide.BUY,
        )
        cmd = await commands.update_command_status(
            cmd["command_id"], CommandStatus.PARTIALLY_FILLED
        )
        assert cmd["status"] == "PARTIALLY_FILLED"
        assert cmd["completed_at"] is not None

    async def test_expired_status(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
        )
        cmd = await commands.update_command_status(
            cmd["command_id"], CommandStatus.EXPIRED
        )
        assert cmd["status"] == "EXPIRED"
        assert cmd["completed_at"] is not None

    async def test_failed_status(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
        )
        cmd = await commands.update_command_status(
            cmd["command_id"], CommandStatus.FAILED, error_message="Connection lost"
        )
        assert cmd["status"] == "FAILED"
        assert cmd["error_message"] == "Connection lost"

    async def test_reconciliation_required_status(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
        )
        cmd = await commands.update_command_status(
            cmd["command_id"], CommandStatus.RECONCILIATION_REQUIRED
        )
        assert cmd["status"] == "RECONCILIATION_REQUIRED"


class TestUpdateOrderIds:
    async def test_update_both_ids(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
        )
        result = await commands.update_order_ids(
            cmd["command_id"],
            client_order_id="client-001",
            venue_order_id="venue-001",
        )
        assert result is True
        updated = await commands.get_command(cmd["command_id"])
        assert updated["client_order_id"] == "client-001"
        assert updated["venue_order_id"] == "venue-001"

    async def test_update_no_ids_returns_false(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
        )
        result = await commands.update_order_ids(cmd["command_id"])
        assert result is False

    async def test_update_nonexistent_command(self):
        result = await commands.update_order_ids(
            "nonexistent-id", client_order_id="c-1"
        )
        assert result is False


class TestCommandEvents:
    async def test_events_after_status_transitions(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
        )
        await commands.update_command_status(cmd["command_id"], CommandStatus.VALIDATED)
        await commands.update_command_status(cmd["command_id"], CommandStatus.SUBMITTED)

        events = await commands.get_command_events(cmd["command_id"])
        assert len(events) >= 3  # CREATED + VALIDATED + SUBMITTED
        event_types = [e["event_type"] for e in events]
        assert "COMMAND_CREATED" in event_types
        assert "STATUS_VALIDATED" in event_types
        assert "STATUS_SUBMITTED" in event_types

    async def test_events_for_nonexistent_command(self):
        events = await commands.get_command_events("nonexistent-id")
        assert events == []


class TestListCommandsCombined:
    async def test_filter_by_status_and_type(self):
        await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
            strategy_id="str-combined",
        )
        cmd2 = await commands.create_command(
            command_type=CommandType.START_STRATEGY,
            strategy_id="str-combined",
        )
        await commands.update_command_status(cmd2["command_id"], CommandStatus.VALIDATED)

        filtered = await commands.list_commands(
            status="VALIDATED", command_type="start_strategy", strategy_id="str-combined"
        )
        assert any(c["command_id"] == cmd2["command_id"] for c in filtered)

    async def test_limit_parameter(self):
        for _ in range(5):
            await commands.create_command(
                command_type=CommandType.SUBMIT_ORDER,
                instrument="BTC/USD.KRAKEN",
            )
        cmds = await commands.list_commands(limit=2)
        assert len(cmds) <= 2


class TestInitCommandsDb:
    async def test_init_commands_db_idempotent(self):
        """init_commands_db can be called multiple times safely."""
        await commands.init_commands_db()
        await commands.init_commands_db()  # should not raise

    async def test_init_wrapper(self):
        await commands.init()  # should not raise


class TestValidateCatalogDrift:
    def test_validate_catalog_drift_passes(self):
        """Should not raise when CommandType matches catalog."""
        commands.validate_catalog_drift()

    def test_command_type_values(self):
        values = CommandType.values()
        assert "submit_order" in values
        assert "cancel_order" in values
        assert "flatten" in values
        assert "start_strategy" in values
        assert "stop_strategy" in values
        assert "kill_switch" in values


class TestUpdateCommandStatusEdgeCases:
    async def test_update_nonexistent_command_returns_none(self):
        result = await commands.update_command_status(
            "nonexistent-id", CommandStatus.VALIDATED
        )
        assert result is None

    async def test_update_accepted_sets_completed_at(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
        )
        updated = await commands.update_command_status(
            cmd["command_id"], CommandStatus.ACCEPTED
        )
        assert updated["completed_at"] is not None

    async def test_update_validated_does_not_set_completed_at(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
        )
        updated = await commands.update_command_status(
            cmd["command_id"], CommandStatus.VALIDATED
        )
        assert updated["completed_at"] is None
