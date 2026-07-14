"""Tests for the durable command layer (commands.py)."""

import pytest

import sys
from pathlib import Path

# Ensure backend root is on sys.path for `import commands` etc.
BACKEND_ROOT = str(Path(__file__).resolve().parent.parent)
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import commands
from commands import (
    CommandType,
    CommandStatus,
    CommandSide,
    OrderType,
)


@pytest.fixture(autouse=True)
async def _cleanup():
    """Ensure a clean DB after each test."""
    yield
    # Prune expired revoked tokens (side-effect of init_db)
    try:
        import database
        await database.purge_expired_revoked_tokens()
    except Exception:
        pass


class TestCreateCommand:
    async def test_creates_command_with_pending_status(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
            side=CommandSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.01,
            strategy_id="test-strategy",
        )
        assert cmd["command_id"] is not None
        assert cmd["command_type"] == "submit_order"
        assert cmd["status"] == "PENDING"
        assert cmd["instrument"] == "BTC/USD.KRAKEN"
        assert cmd["side"] == "BUY"
        assert cmd["quantity"] == 0.01
        assert cmd["strategy_id"] == "test-strategy"
        assert cmd["idempotency_key"] == cmd["command_id"]
        assert cmd["submitted_at"] is None
        assert cmd["completed_at"] is None
        assert cmd["created_at"] is not None

    async def test_custom_idempotency_key(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="ETH/USD.KRAKEN",
            side=CommandSide.SELL,
            idempotency_key="my-custom-key",
        )
        assert cmd["idempotency_key"] == "my-custom-key"

    async def test_optional_fields_default_to_none(self):
        cmd = await commands.create_command(
            command_type=CommandType.START_STRATEGY,
            strategy_id="str-1",
        )
        assert cmd["instrument"] is None
        assert cmd["side"] is None
        assert cmd["order_type"] is None
        assert cmd["price"] is None
        assert cmd["quantity"] is None
        assert cmd["client_order_id"] is None
        assert cmd["venue_order_id"] is None
        assert cmd["error_message"] is None


class TestIdempotency:
    async def test_returns_none_for_new_key(self):
        result = await commands.check_idempotency("nonexistent-key-12345")
        assert result is None

    async def test_returns_existing_command_after_status_change(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
            idempotency_key="idem-test-key",
        )
        # Initially pending — should NOT match
        result = await commands.check_idempotency("idem-test-key")
        assert result is None

        # After transitioning to SUBMITTED — should match
        await commands.update_command_status(
            cmd["command_id"], CommandStatus.SUBMITTED
        )
        result = await commands.check_idempotency("idem-test-key")
        assert result is not None
        assert result["command_id"] == cmd["command_id"]
        assert result["status"] == "SUBMITTED"


class TestUpdateCommandStatus:
    async def test_updates_to_validated(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
        )
        updated = await commands.update_command_status(
            cmd["command_id"], CommandStatus.VALIDATED
        )
        assert updated["status"] == "VALIDATED"

    async def test_updates_to_submitted_sets_submitted_at(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
        )
        updated = await commands.update_command_status(
            cmd["command_id"], CommandStatus.SUBMITTED
        )
        assert updated["status"] == "SUBMITTED"
        assert updated["submitted_at"] is not None

    async def test_updates_completed_at_for_terminal_states(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
        )
        for status in (
            CommandStatus.ACCEPTED,
            CommandStatus.FILLED,
            CommandStatus.CANCELLED,
            CommandStatus.EXPIRED,
            CommandStatus.FAILED,
        ):
            updated = await commands.update_command_status(
                cmd["command_id"], status
            )
            assert updated["completed_at"] is not None

    async def test_stores_error_message(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
        )
        updated = await commands.update_command_status(
            cmd["command_id"],
            CommandStatus.REJECTED,
            error_message="Insufficient margin",
        )
        assert updated["error_message"] == "Insufficient margin"


class TestOrderIds:
    async def test_updates_client_order_id(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
        )
        result = await commands.update_order_ids(
            cmd["command_id"],
            client_order_id="client-abc123",
        )
        assert result is True
        updated = await commands.get_command(cmd["command_id"])
        assert updated["client_order_id"] == "client-abc123"

    async def test_updates_venue_order_id(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
        )
        result = await commands.update_order_ids(
            cmd["command_id"],
            venue_order_id="venue-xyz789",
        )
        assert result is True
        updated = await commands.get_command(cmd["command_id"])
        assert updated["venue_order_id"] == "venue-xyz789"


class TestGetCommand:
    async def test_returns_none_for_missing(self):
        result = await commands.get_command("nonexistent-command-id")
        assert result is None

    async def test_returns_command(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
        )
        result = await commands.get_command(cmd["command_id"])
        assert result is not None
        assert result["command_id"] == cmd["command_id"]


class TestListCommands:
    async def test_returns_all_commands(self):
        for i in range(5):
            await commands.create_command(
                command_type=CommandType.SUBMIT_ORDER,
                instrument=f"BTC/USD.KRAKEN-{i}",
            )
        cmds = await commands.list_commands(limit=100)
        assert len(cmds) >= 5

    async def test_filters_by_status(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
        )
        await commands.update_command_status(
            cmd["command_id"], CommandStatus.VALIDATED
        )
        filtered = await commands.list_commands(status="VALIDATED", limit=100)
        assert any(c["command_id"] == cmd["command_id"] for c in filtered)

    async def test_filters_by_command_type(self):
        await commands.create_command(
            command_type=CommandType.START_STRATEGY,
            strategy_id="str-1",
        )
        filtered = await commands.list_commands(command_type="start_strategy", limit=100)
        assert any(c["command_type"] == "start_strategy" for c in filtered)

    async def test_filters_by_strategy_id(self):
        await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            strategy_id="my-strategy",
        )
        filtered = await commands.list_commands(strategy_id="my-strategy", limit=100)
        assert any(c["strategy_id"] == "my-strategy" for c in filtered)


class TestGetCommandEvents:
    async def test_returns_events(self):
        cmd = await commands.create_command(
            command_type=CommandType.SUBMIT_ORDER,
            instrument="BTC/USD.KRAKEN",
        )
        events = await commands.get_command_events(cmd["command_id"])
        # COMMAND_CREATED event is emitted on creation
        assert len(events) >= 1
        assert events[0]["event_type"] == "COMMAND_CREATED"
        assert events[0]["command_id"] == cmd["command_id"]