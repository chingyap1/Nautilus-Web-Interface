"""Publish control-plane commands and reconcile Nautilus-agent results."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import commands

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live.command_channel import FileCommandChannel


class CommandProcessor:
    """Transport coordinator; it never executes orders or connects to a venue."""

    def __init__(self, poll_interval: float = 0.5, channel: FileCommandChannel | None = None) -> None:
        self.poll_interval = poll_interval
        self.channel = channel or FileCommandChannel()
        self._running = False
        self._task: asyncio.Task | None = None
        self._published: set[str] = set()

    async def start(self) -> None:
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._run(), name="command-reconciler")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while self._running:
            await self.process_once()
            await asyncio.sleep(self.poll_interval)

    async def process_once(self) -> None:
        validated = await commands.list_commands(status="VALIDATED", limit=100)
        for command in reversed(validated):
            if command["command_id"] not in self._published:
                self.channel.publish(command)
                self._published.add(command["command_id"])

        submitted = await commands.list_commands(status="SUBMITTED", limit=100)
        for command in validated + submitted:
            result = self.channel.read_result(command["command_id"], consume=True)
            if result is not None:
                await self._apply_result(command, result)
                self._published.discard(command["command_id"])

    async def _apply_result(self, command: dict, result: dict) -> None:
        if result.get("client_order_id") or result.get("venue_order_id"):
            await commands.update_order_ids(
                command["command_id"],
                client_order_id=result.get("client_order_id"),
                venue_order_id=result.get("venue_order_id"),
            )
        await commands.update_command_status(
            command["command_id"],
            commands.CommandStatus(result["status"]),
            error_message=result.get("error_message"),
        )
        if result.get("strategy_status") and command.get("strategy_id"):
            import database

            await database.update_strategy_status(
                command["strategy_id"], result["strategy_status"].lower()
            )


_processor: CommandProcessor | None = None


def get_processor() -> CommandProcessor:
    global _processor
    if _processor is None:
        _processor = CommandProcessor()
    return _processor


async def start_processor() -> None:
    await get_processor().start()


async def stop_processor() -> None:
    global _processor
    if _processor:
        await _processor.stop()
        _processor = None
