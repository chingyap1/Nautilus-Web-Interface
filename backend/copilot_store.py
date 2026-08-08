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


async def append_message(
    conversation_id: str,
    *,
    role: str,
    content: str,
    status: str,
) -> dict[str, Any]:
    """Persist one Copilot message and bump conversation/workspace timestamps."""
    now = _now()
    async with aiosqlite.connect(database.DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT COALESCE(MAX(sequence), -1) FROM copilot_messages WHERE conversation_id = ?",
            (conversation_id,),
        ) as cursor:
            next_sequence = (await cursor.fetchone())[0] + 1
        message = {
            "id": _id("MSG"),
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "status": status,
            "sequence": next_sequence,
            "created_at": now,
        }
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
    return message


async def append_user_message(
    conversation_id: str,
    content: str,
) -> dict[str, Any]:
    """Persist a user message (Supervisor reply is appended separately in S1)."""
    return await append_message(
        conversation_id, role="user", content=content, status="saved"
    )


async def append_assistant_message(
    conversation_id: str,
    content: str,
) -> dict[str, Any]:
    """Persist a Supervisor assistant reply."""
    return await append_message(
        conversation_id, role="assistant", content=content, status="completed"
    )


def build_supervisor_messages(
    workspace: dict[str, Any],
    artifacts: list[dict[str, Any]],
    history: list[dict[str, Any]],
    user_content: str,
) -> list[dict[str, str]]:
    """Build OpenAI-style messages with workspace context for the Supervisor."""
    artifact_lines: list[str] = []
    for artifact in artifacts[:8]:
        kind = artifact.get("kind", "artifact")
        title = artifact.get("title", "untitled")
        rev = artifact.get("current_revision", "?")
        artifact_lines.append(f"- [{kind}] {title} (rev {rev})")
    artifacts_block = "\n".join(artifact_lines) if artifact_lines else "- (none yet)"
    strategy_id = workspace.get("strategy_id") or "none"
    system = (
        "You are Strategy Copilot assisting an operator who reviews proposed "
        "strategy changes. Discuss ideas, specifications, and experiment plans. "
        "You cannot place trades, dispatch paper commands, resume the Supervisor "
        "interlock, or modify live agent config. Research tools "
        "(backtest/compare/Optuna) are not enabled in this turn.\n\n"
        f"Workspace title: {workspace.get('title', '')}\n"
        f"Lifecycle: {workspace.get('lifecycle', 'IDEA')}\n"
        f"Linked strategy id: {strategy_id}\n"
        f"Recent artifacts:\n{artifacts_block}"
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for row in history:
        role = row.get("role")
        content = row.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        if not content.strip():
            continue
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_content})
    return messages


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
    async with aiosqlite.connect(database.DB_PATH) as db:
        try:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT workspace_id, current_revision FROM copilot_artifacts WHERE id = ?",
                (artifact["id"],),
            ) as cursor:
                current = await cursor.fetchone()
            if not current:
                await db.rollback()
                raise ValueError("Copilot artifact no longer exists")
            number = current[1] + 1
            revision = {
                "id": _id("REV"),
                "artifact_id": artifact["id"],
                "revision": number,
                "content": content,
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                "created_by": owner_id,
                "created_at": now,
            }
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
                (now, current[0]),
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise
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


async def list_approvals(artifact_id: str) -> list[dict[str, Any]]:
    """Return append-only decisions for an artifact, newest first per revision."""
    async with aiosqlite.connect(database.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT p.* FROM copilot_approvals p
               JOIN copilot_artifact_revisions r ON r.id = p.artifact_revision_id
               WHERE r.artifact_id = ?
               ORDER BY r.revision DESC, p.decided_at DESC, p.rowid DESC""",
            (artifact_id,),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


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
        approved = await _has_current_approval(db, workspace["id"], kind)
    return {
        "eligible": approved,
        "target": target,
        "required_artifact_kind": kind,
        "reason": "" if approved else f"Approve the current {kind} artifact revision first.",
    }


async def advance_lifecycle(workspace: dict[str, Any], owner_id: str) -> dict[str, Any] | None:
    now = _now()
    async with aiosqlite.connect(database.DB_PATH) as db:
        try:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT lifecycle FROM copilot_workspaces WHERE id = ?",
                (workspace["id"],),
            ) as cursor:
                row = await cursor.fetchone()
            if not row or row[0] != workspace["lifecycle"]:
                await db.rollback()
                return None
            rule = TRANSITIONS.get(row[0])
            if not rule or not await _has_current_approval(db, workspace["id"], rule[1]):
                await db.rollback()
                return None
            target = rule[0]
            transition = {
                "id": _id("LCT"),
                "workspace_id": workspace["id"],
                "from_lifecycle": row[0],
                "to_lifecycle": target,
                "actor_id": owner_id,
                "created_at": now,
            }
            updated = await db.execute(
                "UPDATE copilot_workspaces SET lifecycle=?, updated_at=? WHERE id=? AND lifecycle=?",
                (target, now, workspace["id"], row[0]),
            )
            if updated.rowcount != 1:
                await db.rollback()
                return None
            await db.execute(
                "INSERT INTO copilot_lifecycle_transitions VALUES (:id, :workspace_id, :from_lifecycle, :to_lifecycle, :actor_id, :created_at)",
                transition,
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    return transition


async def _has_current_approval(db: aiosqlite.Connection, workspace_id: str, kind: str) -> bool:
    """A current revision is approved only when its latest decision is approval."""
    async with db.execute(
        """SELECT p.decision FROM copilot_artifacts a
           JOIN copilot_artifact_revisions r ON r.artifact_id = a.id AND r.revision = a.current_revision
           JOIN copilot_approvals p ON p.artifact_revision_id = r.id
           WHERE a.workspace_id = ? AND a.kind = ?
           ORDER BY p.decided_at DESC, p.rowid DESC LIMIT 1""",
        (workspace_id, kind),
    ) as cursor:
        decision = await cursor.fetchone()
    return decision is not None and decision[0] == "approved"


async def list_transitions(workspace_id: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(database.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM copilot_lifecycle_transitions WHERE workspace_id=? ORDER BY created_at DESC",
            (workspace_id,),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]
