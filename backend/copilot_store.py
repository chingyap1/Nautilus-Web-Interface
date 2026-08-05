"""Durable, owner-scoped persistence for Strategy Copilot O1 workspaces."""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import aiosqlite

import database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex.upper()}"


async def list_workspaces(owner_id: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(database.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM copilot_workspaces
               WHERE owner_id = ? ORDER BY updated_at DESC""",
            (owner_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_workspace(workspace_id: str, owner_id: str) -> Optional[dict[str, Any]]:
    async with aiosqlite.connect(database.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM copilot_workspaces WHERE id = ? AND owner_id = ?",
            (workspace_id, owner_id),
        ) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row else None


async def create_workspace(
    owner_id: str,
    title: str,
    strategy_id: Optional[str] = None,
) -> dict[str, Any]:
    now = _now()
    workspace = {
        "id": _id("CWS"),
        "owner_id": owner_id,
        "title": title,
        "strategy_id": strategy_id,
        "lifecycle": "IDEA",
        "created_at": now,
        "updated_at": now,
    }
    async with aiosqlite.connect(database.DB_PATH) as db:
        await db.execute(
            """INSERT INTO copilot_workspaces
               (id, owner_id, title, strategy_id, lifecycle, created_at, updated_at)
               VALUES (:id, :owner_id, :title, :strategy_id, :lifecycle, :created_at, :updated_at)""",
            workspace,
        )
        await db.commit()
    return workspace


async def list_conversations(workspace_id: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(database.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM copilot_conversations
               WHERE workspace_id = ? ORDER BY updated_at DESC""",
            (workspace_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def create_conversation(workspace_id: str, title: str) -> dict[str, Any]:
    now = _now()
    conversation = {
        "id": _id("CCV"),
        "workspace_id": workspace_id,
        "title": title,
        "created_at": now,
        "updated_at": now,
    }
    async with aiosqlite.connect(database.DB_PATH) as db:
        await db.execute(
            """INSERT INTO copilot_conversations
               (id, workspace_id, title, created_at, updated_at)
               VALUES (:id, :workspace_id, :title, :created_at, :updated_at)""",
            conversation,
        )
        await db.execute(
            "UPDATE copilot_workspaces SET updated_at = ? WHERE id = ?",
            (now, workspace_id),
        )
        await db.commit()
    return conversation


async def get_owned_conversation(
    conversation_id: str,
    owner_id: str,
) -> Optional[dict[str, Any]]:
    async with aiosqlite.connect(database.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT c.* FROM copilot_conversations c
               JOIN copilot_workspaces w ON w.id = c.workspace_id
               WHERE c.id = ? AND w.owner_id = ?""",
            (conversation_id, owner_id),
        ) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row else None


async def list_messages(conversation_id: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(database.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM copilot_messages
               WHERE conversation_id = ? ORDER BY created_at ASC, sequence ASC""",
            (conversation_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def append_user_message(
    conversation_id: str,
    content: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    now = _now()
    async with aiosqlite.connect(database.DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT COALESCE(MAX(sequence), -1) FROM copilot_messages WHERE conversation_id = ?",
            (conversation_id,),
        ) as cursor:
            next_sequence = (await cursor.fetchone())[0] + 1
        user_message = {
            "id": _id("MSG"),
            "conversation_id": conversation_id,
            "role": "user",
            "content": content,
            "status": "saved",
            "sequence": next_sequence,
            "created_at": now,
        }
        acknowledgement = {
            "id": _id("MSG"),
            "conversation_id": conversation_id,
            "role": "system",
            "content": "Saved for Supervisor review. Model execution is not enabled in this O1 workspace yet.",
            "status": "queued_for_supervisor",
            "sequence": next_sequence + 1,
            "created_at": now,
        }
        ordered_messages = (user_message, acknowledgement)
        for message in ordered_messages:
            await db.execute(
                """INSERT INTO copilot_messages
                   (id, conversation_id, role, content, status, sequence, created_at)
                   VALUES (:id, :conversation_id, :role, :content, :status, :sequence, :created_at)""",
                message,
            )
        await db.execute(
            "UPDATE copilot_conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
        await db.execute(
            """UPDATE copilot_workspaces SET updated_at = ?
               WHERE id = (SELECT workspace_id FROM copilot_conversations WHERE id = ?)""",
            (now, conversation_id),
        )
        await db.commit()
    return user_message, acknowledgement