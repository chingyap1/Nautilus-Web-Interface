"""
Durable command tables and idempotent command processing for the NWI backend.

This module adds a `commands` table that stores all trading commands before
they are sent to the Nautilus execution agent. Each command receives a unique
`command_id` that is used throughout its lifecycle.

## Command states

```
PENDING -> VALIDATED -> SUBMITTED -> [ACCEPTED | REJECTED]
                                  -> [FILLED | PARTIALLY_FILLED]
                                  -> [CANCELLED | EXPIRED | CANCELLING]
```

## Idempotency

Commands include an `idempotency_key` (derived from command_id by default).
Duplicate commands with the same key that have already been processed
(VALIDATED or later) will return the existing result instead of creating
a new command. This prevents duplicate orders from webhook retries or
network retransmissions.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

import aiosqlite

from database import DB_PATH


# ---------------------------------------------------------------------------
# Command types and statuses
# ---------------------------------------------------------------------------


class CommandType(str, Enum):
    SUBMIT_ORDER = "submit_order"
    CANCEL_ORDER = "cancel_order"
    FLATTEN = "flatten"
    START_STRATEGY = "start_strategy"
    STOP_STRATEGY = "stop_strategy"
    KILL_SWITCH = "kill_switch"


class CommandStatus(str, Enum):
    PENDING = "PENDING"
    VALIDATED = "VALIDATED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class CommandSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


async def init_commands_db() -> None:
    """Add command/event tables if they don't exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS commands (
                command_id      TEXT PRIMARY KEY,
                command_type    TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'PENDING',
                instrument      TEXT,
                side            TEXT,
                order_type      TEXT,
                quantity        REAL,
                price           REAL,
                strategy_id     TEXT,
                account         TEXT,
                idempotency_key TEXT,
                client_order_id TEXT,
                venue_order_id  TEXT,
                error_message   TEXT,
                submitted_at    TEXT,
                completed_at    TEXT,
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                event_id    TEXT PRIMARY KEY,
                command_id  TEXT NOT NULL,
                event_type  TEXT NOT NULL,
                data        TEXT DEFAULT '{}',
                created_at  TEXT NOT NULL,
                FOREIGN KEY (command_id) REFERENCES commands(command_id)
            );

            CREATE INDEX IF NOT EXISTS idx_commands_status  ON commands(status);
            CREATE INDEX IF NOT EXISTS idx_commands_created ON commands(created_at);
            CREATE INDEX IF NOT EXISTS idx_events_command   ON events(command_id);
            CREATE INDEX IF NOT EXISTS idx_events_created   ON events(created_at);
            CREATE INDEX IF NOT EXISTS idx_commands_idem    ON commands(idempotency_key);
            """
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Command creation
# ---------------------------------------------------------------------------


async def create_command(
    command_type: CommandType,
    instrument: Optional[str] = None,
    side: Optional[CommandSide] = None,
    order_type: Optional[OrderType] = None,
    quantity: Optional[float] = None,
    price: Optional[float] = None,
    strategy_id: Optional[str] = None,
    account: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new durable command record with PENDING status."""
    command_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    command = {
        "command_id": command_id,
        "command_type": command_type.value,
        "status": CommandStatus.PENDING.value,
        "instrument": instrument,
        "side": side.value if side else None,
        "order_type": order_type.value if order_type else None,
        "quantity": quantity,
        "price": price,
        "strategy_id": strategy_id,
        "account": account,
        "idempotency_key": idempotency_key or command_id,
        "client_order_id": None,
        "venue_order_id": None,
        "error_message": None,
        "submitted_at": None,
        "completed_at": None,
        "created_at": now,
    }

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO commands
                (command_id, command_type, status, instrument, side, order_type,
                 quantity, price, strategy_id, account, idempotency_key,
                 client_order_id, venue_order_id, error_message,
                 submitted_at, completed_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                command["command_id"], command["command_type"], command["status"],
                command["instrument"], command["side"], command["order_type"],
                command["quantity"], command["price"], command["strategy_id"],
                command["account"], command["idempotency_key"],
                command["client_order_id"], command["venue_order_id"],
                command["error_message"], command["submitted_at"],
                command["completed_at"], command["created_at"],
            ),
        )
        await db.commit()

    # Emit initial event
    await _append_event(command_id, "COMMAND_CREATED", {})

    return command


# ---------------------------------------------------------------------------
# Idempotency check
# ---------------------------------------------------------------------------


async def check_idempotency(idempotency_key: str) -> Optional[Dict[str, Any]]:
    """
    Check if a command with this idempotency_key has already been validated.
    Returns the existing command if found, None otherwise.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM commands WHERE idempotency_key=? AND status NOT IN ('PENDING', 'VALIDATED')",
            (idempotency_key,),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Command status updates
# ---------------------------------------------------------------------------


async def update_command_status(
    command_id: str,
    status: CommandStatus,
    error_message: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Update a command's status and append an event record."""
    now = datetime.now(timezone.utc).isoformat()

    updates: List[str] = [f"status='{status.value}'"]
    params: List[Any] = [status.value, command_id]

    if error_message is not None:
        updates.append("error_message=?")
        params.append(error_message)

    if status in (
        CommandStatus.SUBMITTED,
        CommandStatus.ACCEPTED,
        CommandStatus.FILLED,
        CommandStatus.PARTIALLY_FILLED,
        CommandStatus.CANCELLED,
        CommandStatus.EXPIRED,
        CommandStatus.FAILED,
        CommandStatus.RECONCILIATION_REQUIRED,
    ):
        updates.append("completed_at=?")
        params.append(now)

    if status == CommandStatus.SUBMITTED and not any("submitted_at" in u for u in updates):
        updates.append("submitted_at=?")
        params.append(now)

    sql = f"UPDATE commands SET {', '.join(updates)} WHERE command_id=?"
    params.append(command_id)

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(sql, params)
        await db.commit()
        updated = cur.rowcount > 0

    if updated:
        await _append_event(command_id, f"STATUS_{status.value}", {})

    # Return updated command
    return await get_command(command_id)


# ---------------------------------------------------------------------------
# Client/Venue order ID tracking
# ---------------------------------------------------------------------------


async def update_order_ids(
    command_id: str,
    client_order_id: Optional[str] = None,
    venue_order_id: Optional[str] = None,
) -> bool:
    """Record the Nautilus client_order_id and/or venue_order_id."""
    parts: List[str] = []
    params: List[Any] = []

    if client_order_id is not None:
        parts.append("client_order_id=?")
        params.append(client_order_id)
    if venue_order_id is not None:
        parts.append("venue_order_id=?")
        params.append(venue_order_id)

    if not parts:
        return False

    params.append(command_id)
    sql = f"UPDATE commands SET {', '.join(parts)} WHERE command_id=?"

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(sql, params)
        await db.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Command queries
# ---------------------------------------------------------------------------


async def get_command(command_id: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM commands WHERE command_id=?", (command_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def list_commands(
    status: Optional[str] = None,
    command_type: Optional[str] = None,
    strategy_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    conditions: List[str] = []
    params: List[Any] = []

    if status:
        conditions.append("status=?")
        params.append(status)
    if command_type:
        conditions.append("command_type=?")
        params.append(command_type)
    if strategy_id:
        conditions.append("strategy_id=?")
        params.append(strategy_id)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT * FROM commands {where} ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Event tracking
# ---------------------------------------------------------------------------


async def _append_event(command_id: str, event_type: str, data: Dict[str, Any]) -> None:
    """Append an event record for a command."""
    event_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO events (event_id, command_id, event_type, data, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event_id, command_id, event_type, str(data), now),
        )
        await db.commit()


async def get_command_events(command_id: str) -> List[Dict[str, Any]]:
    """Return all events for a command, ordered chronologically."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM events WHERE command_id=? ORDER BY created_at ASC",
            (command_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


async def init() -> None:
    """Ensure command/event tables exist."""
    await init_commands_db()