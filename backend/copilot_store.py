"""Durable, owner-scoped persistence for Strategy Copilot O1 workspaces."""

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

import aiosqlite
import database

LIFECYCLES = (
    "IDEA",
    "SPECIFICATION",
    "DRAFT",
    "VALIDATING",
    "CANDIDATE",
    "APPROVED_FOR_PAPER",
    "PAPER_OBSERVATION",
    "ELIGIBLE_FOR_LIVE",
)
TRANSITIONS = {
    "IDEA": ("SPECIFICATION", "specification"),
    "SPECIFICATION": ("DRAFT", "strategy_draft"),
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


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


async def get_workspace(workspace_id: str, owner_id: str) -> dict[str, Any] | None:
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
    strategy_id: str | None = None,
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
) -> dict[str, Any] | None:
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


async def list_artifacts(workspace_id: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(database.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM copilot_artifacts WHERE workspace_id = ? ORDER BY updated_at DESC",
            (workspace_id,),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def get_artifact(artifact_id: str, owner_id: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(database.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT a.* FROM copilot_artifacts a JOIN copilot_workspaces w ON w.id = a.workspace_id
            WHERE a.id = ? AND w.owner_id = ?""",
            (artifact_id, owner_id),
        ) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row else None


async def create_artifact(
    workspace_id: str, kind: str, title: str, content: str, owner_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    now = _now()
    artifact = {
        "id": _id("ART"),
        "workspace_id": workspace_id,
        "kind": kind,
        "title": title,
        "current_revision": 1,
        "created_at": now,
        "updated_at": now,
    }
    revision = {
        "id": _id("REV"),
        "artifact_id": artifact["id"],
        "revision": 1,
        "content": content,
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        "created_by": owner_id,
        "created_at": now,
    }
    async with aiosqlite.connect(database.DB_PATH) as db:
        await db.execute(
            "INSERT INTO copilot_artifacts VALUES (:id, :workspace_id, :kind, :title, :current_revision, :created_at, :updated_at)",
            artifact,
        )
        await db.execute(
            "INSERT INTO copilot_artifact_revisions VALUES (:id, :artifact_id, :revision, :content, :content_hash, :created_by, :created_at)",
            revision,
        )
        await db.execute(
            "UPDATE copilot_workspaces SET updated_at = ? WHERE id = ?", (now, workspace_id)
        )
        await db.commit()
    return artifact, revision


async def list_revisions(artifact_id: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(database.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM copilot_artifact_revisions WHERE artifact_id = ? ORDER BY revision DESC",
            (artifact_id,),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def create_revision(artifact: dict[str, Any], content: str, owner_id: str) -> dict[str, Any]:
    now = _now()
    number = artifact["current_revision"] + 1
    revision = {
        "id": _id("REV"),
        "artifact_id": artifact["id"],
        "revision": number,
        "content": content,
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        "created_by": owner_id,
        "created_at": now,
    }
    async with aiosqlite.connect(database.DB_PATH) as db:
        await db.execute(
            "INSERT INTO copilot_artifact_revisions VALUES (:id, :artifact_id, :revision, :content, :content_hash, :created_by, :created_at)",
            revision,
        )
        await db.execute(
            "UPDATE copilot_artifacts SET current_revision = ?, updated_at = ? WHERE id = ?",
            (number, now, artifact["id"]),
        )
        await db.execute(
            "UPDATE copilot_workspaces SET updated_at = ? WHERE id = ?",
            (now, artifact["workspace_id"]),
        )
        await db.commit()
    return revision


async def decide_revision(
    revision_id: str, decision: str, reason: str, owner_id: str
) -> dict[str, Any]:
    approval = {
        "id": _id("APR"),
        "artifact_revision_id": revision_id,
        "decision": decision,
        "reason": reason,
        "decided_by": owner_id,
        "decided_at": _now(),
    }
    async with aiosqlite.connect(database.DB_PATH) as db:
        await db.execute(
            "INSERT INTO copilot_approvals VALUES (:id, :artifact_revision_id, :decision, :reason, :decided_by, :decided_at)",
            approval,
        )
        await db.commit()
    return approval


async def transition_eligibility(workspace: dict[str, Any]) -> dict[str, Any]:
    rule = TRANSITIONS.get(workspace["lifecycle"])
    if not rule:
        return {
            "eligible": False,
            "target": None,
            "reason": "This lifecycle transition is not available in O1b.",
        }
    target, kind = rule
    async with aiosqlite.connect(database.DB_PATH) as db:
        async with db.execute(
            """SELECT 1 FROM copilot_artifacts a JOIN copilot_artifact_revisions r ON r.artifact_id=a.id AND r.revision=a.current_revision
            JOIN copilot_approvals p ON p.artifact_revision_id=r.id WHERE a.workspace_id=? AND a.kind=? AND p.decision='approved' LIMIT 1""",
            (workspace["id"], kind),
        ) as cursor:
            approved = await cursor.fetchone() is not None
    return {
        "eligible": approved,
        "target": target,
        "required_artifact_kind": kind,
        "reason": "" if approved else f"Approve the current {kind} artifact revision first.",
    }


async def advance_lifecycle(workspace: dict[str, Any], owner_id: str) -> dict[str, Any] | None:
    eligibility = await transition_eligibility(workspace)
    if not eligibility["eligible"]:
        return None
    now = _now()
    target = eligibility["target"]
    transition = {
        "id": _id("LCT"),
        "workspace_id": workspace["id"],
        "from_lifecycle": workspace["lifecycle"],
        "to_lifecycle": target,
        "actor_id": owner_id,
        "created_at": now,
    }
    async with aiosqlite.connect(database.DB_PATH) as db:
        await db.execute(
            "UPDATE copilot_workspaces SET lifecycle=?, updated_at=? WHERE id=? AND lifecycle=?",
            (target, now, workspace["id"], workspace["lifecycle"]),
        )
        await db.execute(
            "INSERT INTO copilot_lifecycle_transitions VALUES (:id, :workspace_id, :from_lifecycle, :to_lifecycle, :actor_id, :created_at)",
            transition,
        )
        await db.commit()
    return transition


async def list_transitions(workspace_id: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(database.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM copilot_lifecycle_transitions WHERE workspace_id=? ORDER BY created_at DESC",
            (workspace_id,),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]
