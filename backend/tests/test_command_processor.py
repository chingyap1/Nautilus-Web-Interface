"""Tests for command publishing and execution-agent result reconciliation."""

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = str(Path(__file__).resolve().parent.parent)
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import commands
from command_processor import CommandProcessor
from commands import CommandSide, CommandStatus, CommandType, OrderType


class FakeChannel:
    def __init__(self):
        self.published: list[dict] = []
        self.results: dict[str, dict] = {}

    def publish(self, command: dict) -> None:
        self.published.append(command)

    def read_result(self, command_id: str, *, consume: bool = False):
        result = self.results.get(command_id)
        if result and consume:
            del self.results[command_id]
        return result


@pytest.fixture
async def validated_command():
    command = await commands.create_command(
        command_type=CommandType.SUBMIT_ORDER,
        instrument="BTC/USD.KRAKEN",
        side=CommandSide.BUY,
        order_type=OrderType.MARKET,
        quantity=0.01,
    )
    await commands.update_command_status(command["command_id"], CommandStatus.VALIDATED)
    return command


async def test_start_and_stop():
    processor = CommandProcessor(poll_interval=60, channel=FakeChannel())
    await processor.start()
    assert processor._running is True
    await processor.stop()
    assert processor._running is False
    assert processor._task is None


async def test_validated_command_is_published_once(validated_command):
    channel = FakeChannel()
    processor = CommandProcessor(channel=channel)
    await processor.process_once()
    await processor.process_once()
    matching = [
        item
        for item in channel.published
        if item["command_id"] == validated_command["command_id"]
    ]
    assert len(matching) == 1
    assert matching[0]["status"] == "VALIDATED"


async def test_agent_result_sets_submitted_and_order_ids(validated_command):
    channel = FakeChannel()
    channel.results[validated_command["command_id"]] = {
        "status": "SUBMITTED",
        "client_order_id": "O-19700101-000000-001-001-1",
    }
    processor = CommandProcessor(channel=channel)
    await processor.process_once()
    updated = await commands.get_command(validated_command["command_id"])
    assert updated["status"] == "SUBMITTED"
    assert updated["client_order_id"] == "O-19700101-000000-001-001-1"
    assert updated["submitted_at"] is not None


async def test_agent_rejection_is_terminal(validated_command):
    channel = FakeChannel()
    channel.results[validated_command["command_id"]] = {
        "status": "REJECTED",
        "error_message": "Account reconciliation not yet complete",
    }
    processor = CommandProcessor(channel=channel)
    await processor.process_once()
    updated = await commands.get_command(validated_command["command_id"])
    assert updated["status"] == "REJECTED"
    assert updated["completed_at"] is not None
    assert "reconciliation" in updated["error_message"].lower()


async def test_singleton_lifecycle():
    import command_processor

    command_processor._processor = CommandProcessor(poll_interval=60, channel=FakeChannel())
    await command_processor.start_processor()
    processor = command_processor.get_processor()
    assert processor._running is True
    await command_processor.stop_processor()
    assert processor._running is False
    assert command_processor._processor is None
