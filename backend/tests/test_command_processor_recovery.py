from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import command_processor
from live.command_channel import FileCommandChannel


def test_existing_result_is_applied_before_validated_command_can_be_republished(
    tmp_path, monkeypatch
) -> None:
    channel = FileCommandChannel(tmp_path)
    command = {"command_id": "cmd-result", "status": "VALIDATED"}
    channel._write_atomic(
        channel.results / "cmd-result.json",
        {"command_id": "cmd-result", "status": "RECONCILIATION_REQUIRED"},
    )
    processor = command_processor.CommandProcessor(channel=channel)
    monkeypatch.setattr(
        command_processor.commands,
        "list_commands",
        AsyncMock(return_value=[command]),
    )
    monkeypatch.setattr(
        command_processor.commands,
        "get_command",
        AsyncMock(return_value=command),
    )
    apply_result = AsyncMock()
    monkeypatch.setattr(processor, "_apply_result", apply_result)

    asyncio.run(processor.process_once())

    apply_result.assert_awaited_once()
    assert not channel.has_in_flight("cmd-result")


def test_claimed_validated_command_is_not_republished(tmp_path, monkeypatch) -> None:
    channel = FileCommandChannel(tmp_path)
    command = {"command_id": "cmd-claimed", "status": "VALIDATED"}
    channel.publish(command)
    assert channel.claim_next() is not None
    processor = command_processor.CommandProcessor(channel=channel)
    monkeypatch.setattr(
        command_processor.commands,
        "list_commands",
        AsyncMock(return_value=[command]),
    )
    monkeypatch.setattr(command_processor.commands, "get_command", AsyncMock())

    asyncio.run(processor.process_once())

    assert not (channel.pending / "cmd-claimed.json").exists()
    assert (channel.processing / "cmd-claimed.json").exists()


def test_terminal_command_acknowledges_stranded_result_without_regression(
    tmp_path, monkeypatch
) -> None:
    channel = FileCommandChannel(tmp_path)
    command = {"command_id": "cmd-applied", "status": "FAILED"}
    channel._write_atomic(
        channel.results / "cmd-applied.json",
        {"command_id": "cmd-applied", "status": "FAILED"},
    )
    processor = command_processor.CommandProcessor(channel=channel)
    monkeypatch.setattr(
        command_processor.commands,
        "list_commands",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        command_processor.commands,
        "get_command",
        AsyncMock(return_value=command),
    )
    apply_result = AsyncMock()
    monkeypatch.setattr(processor, "_apply_result", apply_result)

    asyncio.run(processor.process_once())

    apply_result.assert_not_awaited()
    assert channel.read_result("cmd-applied") is None


def test_completion_between_result_scan_and_publish_is_not_republished(
    tmp_path, monkeypatch
) -> None:
    channel = FileCommandChannel(tmp_path)
    command = {"command_id": "cmd-race", "status": "VALIDATED"}
    processor = command_processor.CommandProcessor(channel=channel)
    monkeypatch.setattr(
        command_processor.commands,
        "list_commands",
        AsyncMock(return_value=[command]),
    )
    monkeypatch.setattr(command_processor.commands, "get_command", AsyncMock())
    apply_result = AsyncMock()
    monkeypatch.setattr(processor, "_apply_result", apply_result)

    def complete_during_check(command_id: str) -> bool:
        channel._write_atomic(
            channel.results / f"{command_id}.json",
            {"command_id": command_id, "status": "FAILED"},
        )
        return False

    monkeypatch.setattr(channel, "has_in_flight", complete_during_check)

    asyncio.run(processor.process_once())

    apply_result.assert_awaited_once()
    assert not (channel.pending / "cmd-race.json").exists()
    assert channel.read_result("cmd-race") is None


def test_malformed_result_becomes_reconciliation_required(tmp_path, monkeypatch) -> None:
    channel = FileCommandChannel(tmp_path)
    command = {"command_id": "cmd-malformed", "status": "VALIDATED"}
    channel._write_atomic(
        channel.results / "cmd-malformed.json",
        {"command_id": "wrong", "status": "NOT_A_STATUS"},
    )
    processor = command_processor.CommandProcessor(channel=channel)
    monkeypatch.setattr(
        command_processor.commands,
        "list_commands",
        AsyncMock(return_value=[command]),
    )
    monkeypatch.setattr(
        command_processor.commands,
        "get_command",
        AsyncMock(return_value=command),
    )
    update_status = AsyncMock()
    monkeypatch.setattr(command_processor.commands, "update_command_status", update_status)

    asyncio.run(processor.process_once())

    update_status.assert_awaited_once()
    assert (
        update_status.await_args.args[1]
        == command_processor.commands.CommandStatus.RECONCILIATION_REQUIRED
    )
    assert channel.read_result("cmd-malformed") is None


def test_malformed_json_result_is_not_treated_as_missing(tmp_path, monkeypatch) -> None:
    channel = FileCommandChannel(tmp_path)
    command = {"command_id": "cmd-broken-json", "status": "VALIDATED"}
    (channel.results / "cmd-broken-json.json").write_text("not-json", encoding="utf-8")
    processor = command_processor.CommandProcessor(channel=channel)
    monkeypatch.setattr(
        command_processor.commands,
        "list_commands",
        AsyncMock(return_value=[command]),
    )
    monkeypatch.setattr(
        command_processor.commands,
        "get_command",
        AsyncMock(return_value=command),
    )
    update_status = AsyncMock()
    monkeypatch.setattr(command_processor.commands, "update_command_status", update_status)

    asyncio.run(processor.process_once())

    assert (
        update_status.await_args.args[1]
        == command_processor.commands.CommandStatus.RECONCILIATION_REQUIRED
    )
    assert not (channel.pending / "cmd-broken-json.json").exists()


def test_engage_cancels_unclaimed_supervisor_command(tmp_path, monkeypatch) -> None:
    channel = FileCommandChannel(tmp_path)
    command = {
        "command_id": "cmd-cancel",
        "status": "VALIDATED",
        "origin": "supervisor",
    }
    channel.publish(command)
    processor = command_processor.CommandProcessor(channel=channel)
    monkeypatch.setattr(
        command_processor.commands,
        "list_commands",
        AsyncMock(return_value=[command]),
    )
    update_status = AsyncMock()
    monkeypatch.setattr(command_processor.commands, "update_command_status", update_status)

    cancelled = asyncio.run(processor.cancel_unclaimed_supervisor_commands())

    assert cancelled == ["cmd-cancel"]
    assert not channel.has_in_flight("cmd-cancel")
    assert update_status.await_args.args[1] == command_processor.commands.CommandStatus.CANCELLED


def test_engage_does_not_cancel_agent_claimed_command(tmp_path, monkeypatch) -> None:
    channel = FileCommandChannel(tmp_path)
    command = {
        "command_id": "cmd-claimed",
        "status": "VALIDATED",
        "origin": "supervisor",
    }
    channel.publish(command)
    assert channel.claim_next() is not None
    processor = command_processor.CommandProcessor(channel=channel)
    monkeypatch.setattr(
        command_processor.commands,
        "list_commands",
        AsyncMock(return_value=[command]),
    )
    update_status = AsyncMock()
    monkeypatch.setattr(command_processor.commands, "update_command_status", update_status)

    cancelled = asyncio.run(processor.cancel_unclaimed_supervisor_commands())

    assert cancelled == []
    update_status.assert_not_awaited()
