"""Durable, owner-scoped persistence for Strategy Copilot O1 workspaces.

Lifecycle authority is ``promotion.Promotion`` (D13). The ``lifecycle`` column
is a cached projection of ``Promotion.state`` for list/display; advances go
through ``copilot_promotion`` → ``promotion.state_machine.advance``.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import aiosqlite
import copilot_promotion
import database
from promotion.state_machine import PromotionError

LIFECYCLES = (
    "IDEA",
    "SPECIFICATION",
    "DRAFT",
    "VALIDATING",
    "CANDIDATE",
    "APPROVED_FOR_PAPER",
    "PAPER_OBSERVATION",
    "ELIGIBLE_FOR_LIVE",
    "REJECTED",
)
# Kept for display/docs only — do not grow as a second FSM (D13).
# Authoritative UI edges live in ``copilot_promotion.TRANSITIONS_UI``.
TRANSITIONS = {
    "IDEA": ("SPECIFICATION", "specification"),
    "SPECIFICATION": ("DRAFT", "strategy_draft"),
    "DRAFT": ("VALIDATING", "validation_report"),
    "VALIDATING": ("CANDIDATE", "candidate_bundle"),
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
    if not row:
        return None
    return await ensure_promotion_binding(dict(row))


async def create_workspace(
    owner_id: str,
    title: str,
    strategy_id: str | None = None,
    *,
    description: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a workspace bound to a new ``Promotion`` at IDEA (D13)."""
    now = _now()
    meta = {"workspace_title": title, "owner_id": owner_id, **(metadata or {})}
    if strategy_id:
        meta["strategy_id"] = strategy_id
    promotion = copilot_promotion.create_promotion(
        strategy_name=strategy_id or meta.get("framework_strategy") or "unlinked",
        description=description or title,
        metadata=meta,
    )
    workspace = {
        "id": _id("CWS"),
        "owner_id": owner_id,
        "title": title,
        "strategy_id": strategy_id,
        "lifecycle": copilot_promotion.project_lifecycle(promotion),
        "promotion_id": promotion.id,
        "created_at": now,
        "updated_at": now,
    }
    async with aiosqlite.connect(database.DB_PATH) as db:
        await db.execute(
            """INSERT INTO copilot_workspaces
               (id, owner_id, title, strategy_id, lifecycle, promotion_id, created_at, updated_at)
               VALUES (:id, :owner_id, :title, :strategy_id, :lifecycle, :promotion_id,
                       :created_at, :updated_at)""",
            workspace,
        )
        await db.commit()
    return workspace


async def ensure_promotion_binding(workspace: dict[str, Any]) -> dict[str, Any]:
    """Lazily bind a Promotion for pre-S3 rows missing ``promotion_id``."""
    if workspace.get("promotion_id"):
        return await _sync_lifecycle_projection(workspace)
    promotion = copilot_promotion.create_promotion(
        strategy_name=workspace.get("strategy_id") or "unlinked",
        description=workspace.get("title") or "legacy workspace",
        metadata={
            "workspace_id": workspace["id"],
            "owner_id": workspace.get("owner_id"),
            "migrated_from_lifecycle": workspace.get("lifecycle"),
            "source": "legacy_bind",
        },
    )
    # Preserve projected lifecycle if the row was already advanced before binding.
    desired = workspace.get("lifecycle") or "IDEA"
    if desired != promotion.state.value and desired in LIFECYCLES:
        # Do not silently invent approvals — reset projection to IDEA authority.
        desired = promotion.state.value
    now = _now()
    async with aiosqlite.connect(database.DB_PATH) as db:
        await db.execute(
            """UPDATE copilot_workspaces
               SET promotion_id = ?, lifecycle = ?, updated_at = ?
               WHERE id = ? AND (promotion_id IS NULL OR promotion_id = '')""",
            (promotion.id, desired, now, workspace["id"]),
        )
        await db.commit()
    workspace = {**workspace, "promotion_id": promotion.id, "lifecycle": desired, "updated_at": now}
    return workspace


async def _sync_lifecycle_projection(workspace: dict[str, Any]) -> dict[str, Any]:
    """Refresh cached lifecycle from the bound Promotion when they diverge."""
    promotion_id = workspace.get("promotion_id")
    if not promotion_id:
        return workspace
    try:
        promotion = copilot_promotion.load_promotion(promotion_id)
    except Exception:
        return workspace
    projected = copilot_promotion.project_lifecycle(promotion)
    if projected == workspace.get("lifecycle"):
        return workspace
    now = _now()
    async with aiosqlite.connect(database.DB_PATH) as db:
        await db.execute(
            "UPDATE copilot_workspaces SET lifecycle = ?, updated_at = ? WHERE id = ?",
            (projected, now, workspace["id"]),
        )
        await db.commit()
    return {**workspace, "lifecycle": projected, "updated_at": now}


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
        "interlock, or modify live agent config. "
        "Research tools (run_backtest, run_walk_forward, compare_strategies, "
        "optimise_params, registry_status, propose_registry_patch, "
        "propose_strategy_patch, run_validation) may be suggested; the "
        "operator can also run them via workspace controls. Registry and "
        "strategy-code patches require human approve+apply and never git "
        "push. Mid-gates DRAFT→VALIDATING→CANDIDATE need approved "
        "validation_report then candidate_bundle; paper deploy stays on the "
        "Supervision path. Never claim a paper deploy occurred.\n\n"
        f"Workspace title: {workspace.get('title', '')}\n"
        f"Lifecycle: {workspace.get('lifecycle', 'IDEA')}\n"
        f"Promotion id: {workspace.get('promotion_id') or 'none'}\n"
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
    workspace = await ensure_promotion_binding(workspace)
    promotion_id = workspace.get("promotion_id")
    if not promotion_id:
        return {
            "eligible": False,
            "target": None,
            "required_artifact_kind": None,
            "reason": "Workspace is not bound to a Promotion (D13).",
            "promotion_id": None,
            "promotion_state": None,
        }
    promotion = copilot_promotion.load_promotion(promotion_id)
    rule = copilot_promotion.TRANSITIONS_UI.get(promotion.state)
    kind = rule[1] if rule else None
    async with aiosqlite.connect(database.DB_PATH) as db:
        approved = bool(kind and await _has_current_approval(db, workspace["id"], kind))
        evidence_ok, evidence_reason = await _midgate_evidence_ok(
            db, workspace["id"], kind, promotion
        )
    return copilot_promotion.transition_eligibility(
        promotion,
        artifact_approved=approved,
        evidence_ok=evidence_ok,
        evidence_reason=evidence_reason,
    )


async def advance_lifecycle(workspace: dict[str, Any], owner_id: str) -> dict[str, Any] | None:
    """Advance via ``promotion.state_machine`` then project state onto the workspace."""
    workspace = await ensure_promotion_binding(workspace)
    now = _now()
    async with aiosqlite.connect(database.DB_PATH) as db:
        try:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT lifecycle, promotion_id FROM copilot_workspaces WHERE id = ?",
                (workspace["id"],),
            ) as cursor:
                row = await cursor.fetchone()
            if not row or row[0] != workspace["lifecycle"] or not row[1]:
                await db.rollback()
                return None
            lifecycle, promotion_id = row[0], row[1]
            promotion = copilot_promotion.load_promotion(promotion_id)
            if promotion.state.value != lifecycle:
                # Repair projection under lock, then refuse this advance attempt.
                await db.execute(
                    "UPDATE copilot_workspaces SET lifecycle=?, updated_at=? WHERE id=?",
                    (promotion.state.value, now, workspace["id"]),
                )
                await db.commit()
                return None
            rule = copilot_promotion.TRANSITIONS_UI.get(promotion.state)
            if not rule:
                await db.rollback()
                return None
            target, kind = rule
            if not await _has_current_approval(db, workspace["id"], kind):
                await db.rollback()
                return None
            evidence_ok, _ = await _midgate_evidence_ok(
                db, workspace["id"], kind, promotion
            )
            if not evidence_ok:
                await db.rollback()
                return None
            payload_hash = await _current_artifact_hash(db, workspace["id"], kind)
            try:
                updated_promotion = copilot_promotion.advance_promotion(
                    promotion,
                    target=target,
                    approver=owner_id,
                    payload_hash=payload_hash,
                    notes=f"Advanced from Copilot workspace {workspace['id']}",
                )
            except PromotionError:
                await db.rollback()
                return None
            to_lifecycle = updated_promotion.state.value
            transition = {
                "id": _id("LCT"),
                "workspace_id": workspace["id"],
                "from_lifecycle": lifecycle,
                "to_lifecycle": to_lifecycle,
                "actor_id": owner_id,
                "created_at": now,
            }
            updated = await db.execute(
                "UPDATE copilot_workspaces SET lifecycle=?, updated_at=? WHERE id=? AND lifecycle=?",
                (to_lifecycle, now, workspace["id"], lifecycle),
            )
            if updated.rowcount != 1:
                await db.rollback()
                return None
            await db.execute(
                "INSERT INTO copilot_lifecycle_transitions VALUES "
                "(:id, :workspace_id, :from_lifecycle, :to_lifecycle, :actor_id, :created_at)",
                transition,
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    return transition


async def create_from_supervision(
    owner_id: str,
    *,
    pair: str,
    reason: str,
    strategy: str | None = None,
    recommendation_kind: str = "experiment",
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Spawn a Copilot workspace from a supervision EXPERIMENT recommendation."""
    params = parameters or {}
    title = f"Supervision experiment · {pair}"
    workspace = await create_workspace(
        owner_id,
        title,
        strategy_id=None,
        description=reason,
        metadata={
            "source": "supervision",
            "pair": pair,
            "recommendation_kind": recommendation_kind,
            "framework_strategy": strategy,
            "parameters": params,
        },
    )
    conversation = await create_conversation(workspace["id"], "Supervision ingress")
    seed = (
        f"Opened from supervision for {pair} "
        f"(kind={recommendation_kind}"
        f"{f', strategy={strategy}' if strategy else ''}).\n\n"
        f"{reason}"
    )
    if params:
        seed += f"\n\nSuggested parameters:\n{json.dumps(params, indent=2)}"
    await append_message(
        conversation["id"], role="system", content=seed, status="saved"
    )
    content = (
        f"# Experiment brief ({pair})\n\n{reason}\n\n"
        f"Framework strategy: {strategy or 'unspecified'}\n"
        f"Parameters: {json.dumps(params)}\n"
    )
    artifact, revision = await create_artifact(
        workspace["id"],
        "specification",
        f"Supervision brief · {pair}",
        content,
        owner_id,
    )
    return {
        "workspace": workspace,
        "conversation": conversation,
        "artifact": artifact,
        "revision": revision,
    }


async def _midgate_evidence_ok(
    db: aiosqlite.Connection,
    workspace_id: str,
    kind: str | None,
    promotion: Any,
) -> tuple[bool, str]:
    """Extra evidence checks for S6 mid-gates beyond artifact approval."""
    if kind == "validation_report":
        content = await _current_artifact_content(db, workspace_id, kind)
        if not content:
            return False, "Run validation to produce a validation_report first."
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return False, "validation_report content must be JSON."
        if not isinstance(payload, dict) or payload.get("kind") != "validation_report":
            return False, "Artifact is not a validation_report."
        if not payload.get("passed"):
            return False, "Validation did not pass — fix failures before advancing."
        return True, ""
    if kind == "candidate_bundle":
        if not promotion.candidate_bundle:
            return False, "Create a candidate bundle on this promotion first."
        content = await _current_artifact_content(db, workspace_id, kind)
        if not content:
            return False, "Candidate bundle artifact missing — create the bundle again."
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return False, "candidate_bundle content must be JSON."
        if not isinstance(payload, dict) or payload.get("kind") != "candidate_bundle":
            return False, "Artifact is not a candidate_bundle."
        expected = promotion.candidate_bundle.get("payload_hash")
        if expected and payload.get("payload_hash") != expected:
            return False, "Bundle artifact hash does not match the promotion record."
        return True, ""
    return True, ""


async def _current_artifact_content(
    db: aiosqlite.Connection, workspace_id: str, kind: str
) -> str | None:
    async with db.execute(
        """SELECT r.content FROM copilot_artifacts a
           JOIN copilot_artifact_revisions r
             ON r.artifact_id = a.id AND r.revision = a.current_revision
           WHERE a.workspace_id = ? AND a.kind = ?
           ORDER BY a.updated_at DESC LIMIT 1""",
        (workspace_id, kind),
    ) as cursor:
        row = await cursor.fetchone()
    return row[0] if row else None


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


async def _current_artifact_hash(
    db: aiosqlite.Connection, workspace_id: str, kind: str
) -> str | None:
    """Return content_hash of the current revision for an artifact kind."""
    async with db.execute(
        """SELECT r.content_hash FROM copilot_artifacts a
           JOIN copilot_artifact_revisions r
             ON r.artifact_id = a.id AND r.revision = a.current_revision
           WHERE a.workspace_id = ? AND a.kind = ?
           ORDER BY a.updated_at DESC LIMIT 1""",
        (workspace_id, kind),
    ) as cursor:
        row = await cursor.fetchone()
    return row[0] if row else None


async def list_transitions(workspace_id: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(database.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM copilot_lifecycle_transitions WHERE workspace_id=? ORDER BY created_at DESC",
            (workspace_id,),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]
