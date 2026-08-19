"""Publish control-plane commands and reconcile Nautilus-agent results."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import commands

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live.command_channel import FileCommandChannel
from mcp_gateway.interlock import evaluate_interlock
from mcp_gateway.models import InterlockState
from stores import InterlockStore


class CommandProcessor:
    """Transport coordinator; it never executes orders or connects to a venue."""

    def __init__(
        self,
        poll_interval: float = 0.5,
        channel: FileCommandChannel | None = None,
    ) -> None:
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
            try:
                await self.process_once()
            except Exception as exc:
                # A malformed artifact or transient database failure must not
                # permanently stop command routing while the API stays healthy.
                print(f"[commands] Reconciliation failed: {exc}", file=sys.stderr)
            await asyncio.sleep(self.poll_interval)

    async def process_once(self) -> None:
        validated = await commands.list_commands(status="VALIDATED", limit=100)
        supervisor_enabled = True
        if any(command.get("origin") == "supervisor" for command in validated):
            interlock = await InterlockStore().get()
            supervisor_enabled = (
                evaluate_interlock(interlock, now=datetime.now(UTC))
                == InterlockState.RESUMED
            )
        completed: set[str] = set()
        for command_id in self.channel.result_ids():
            result = self.channel.read_result(command_id)
            command = await commands.get_command(command_id)
            if result is None or command is None:
                continue
            if command["status"] in {"VALIDATED", "SUBMITTED"}:
                await self._apply_result(command, self._validated_result(command_id, result))
            self.channel.read_result(command_id, consume=True)
            self.channel.clear_processing(command_id)
            self._published.discard(command_id)
            completed.add(command_id)

        for command in reversed(validated):
            command_id = command["command_id"]
            if command_id in completed:
                continue
            if command.get("origin") == "supervisor" and not supervisor_enabled:
                continue
            if self.channel.has_in_flight(command_id):
                self._published.add(command_id)
                continue
            # Agent completion writes the result before removing processing/.
            # Checking in this order closes the completion-versus-republish race.
            result = self.channel.read_result(command_id)
            if result is not None:
                await self._apply_result(command, self._validated_result(command_id, result))
                self.channel.read_result(command_id, consume=True)
                self.channel.clear_processing(command_id)
                self._published.discard(command_id)
            elif command_id not in self._published:
                self.channel.publish(command)
                self._published.add(command_id)

    async def cancel_unclaimed_supervisor_commands(self) -> list[str]:
        """Cancel queued Supervisor commands without touching agent-claimed work."""
        validated = await commands.list_commands(status="VALIDATED", limit=1000)
        cancelled: list[str] = []
        for command in validated:
            if command.get("origin") != "supervisor":
                continue
            command_id = command["command_id"]
            if self.channel.read_result(command_id) is not None:
                continue
            if not self.channel.cancel_pending(command_id):
                continue
            await commands.update_command_status(
                command_id,
                commands.CommandStatus.CANCELLED,
                error_message="Cancelled because Supervisor interlock was engaged",
            )
            self._published.discard(command_id)
            cancelled.append(command_id)
        return cancelled

    @staticmethod
    def _validated_result(command_id: str, result: dict) -> dict:
        """Convert malformed or mismatched agent output to fail-closed state."""
        try:
            if str(result["command_id"]) != command_id:
                raise ValueError("result command_id does not match artifact name")
            status = commands.CommandStatus(result["status"])
            if status in {commands.CommandStatus.PENDING, commands.CommandStatus.VALIDATED}:
                raise ValueError(f"agent result cannot have status {status.value}")
        except (KeyError, TypeError, ValueError) as exc:
            return {
                "command_id": command_id,
                "status": commands.CommandStatus.RECONCILIATION_REQUIRED.value,
                "error_message": f"Malformed command result: {exc}",
            }
        return result

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
