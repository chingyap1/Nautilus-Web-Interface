"""Authenticated Strategy Copilot workspace API (O1 + S1–S3)."""

import json

import copilot_research
import copilot_store
import database
import supervisor_client
from auth_jwt import get_current_user
from copilot_research import ResearchBudgetError, ResearchToolError
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from supervisor_client import SupervisorError

router = APIRouter(prefix="/api/copilot", tags=["strategy-copilot"])


class WorkspaceCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    strategy_id: str | None = Field(default=None, max_length=120)

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


class WorkspaceFromSupervision(BaseModel):
    """S3 — open a Copilot workspace from a supervision EXPERIMENT recommendation."""

    pair: str = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=1, max_length=2_000)
    strategy: str | None = Field(default=None, max_length=120)
    recommendation_kind: str = Field(default="experiment", max_length=32)
    parameters: dict = Field(default_factory=dict)

    @field_validator("pair", "reason")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


class ConversationCreate(BaseModel):
    title: str = Field(default="Strategy discussion", min_length=1, max_length=120)

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content must not be blank")
        return value


class ArtifactCreate(BaseModel):
    kind: str = Field(
        pattern="^(specification|strategy_draft|experiment_result|comparison_table|optuna_summary)$"
    )
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=50_000)

    @field_validator("title", "content")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


class ExperimentRun(BaseModel):
    """Human-triggered research tool (same implementation Supervisor may call)."""

    tool: str = Field(
        pattern="^(run_backtest|run_walk_forward|compare_strategies|optimise_params|registry_status)$"
    )
    params: dict = Field(default_factory=dict)


class ArtifactImport(BaseModel):
    """Import a CLI/Optuna/notebook JSON summary into the workspace."""

    kind: str = Field(pattern="^(experiment_result|comparison_table|optuna_summary)$")
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=2, max_length=50_000)

    @field_validator("title", "content")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("content")
    @classmethod
    def content_must_be_json_object(cls, value: str) -> str:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("content must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("content must be a JSON object")
        return value


class RevisionCreate(BaseModel):
    content: str = Field(min_length=1, max_length=50_000)
    _content = field_validator("content")(
        lambda value: (
            value.strip() or (_ for _ in ()).throw(ValueError("content must not be blank"))
        )
    )


class ApprovalCreate(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    reason: str = Field(min_length=3, max_length=2_000)
    _reason = field_validator("reason")(
        lambda value: value.strip() or (_ for _ in ()).throw(ValueError("reason must not be blank"))
    )


def _owner(user: dict) -> str:
    owner_id = user.get("sub")
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise HTTPException(status_code=401, detail="Token subject is required")
    return owner_id


async def _owned_workspace(workspace_id: str, owner_id: str) -> dict:
    workspace = await copilot_store.get_workspace(workspace_id, owner_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Copilot workspace not found")
    return workspace


async def _owned_conversation(conversation_id: str, owner_id: str) -> dict:
    conversation = await copilot_store.get_owned_conversation(conversation_id, owner_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Copilot conversation not found")
    return conversation


async def _owned_artifact(artifact_id: str, owner_id: str) -> dict:
    artifact = await copilot_store.get_artifact(artifact_id, owner_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Copilot artifact not found")
    return artifact


@router.get("/workspaces")
async def list_workspaces(user: dict = Depends(get_current_user)):
    workspaces = await copilot_store.list_workspaces(_owner(user))
    return {"workspaces": workspaces, "count": len(workspaces)}


@router.post("/workspaces", status_code=201)
async def create_workspace(body: WorkspaceCreate, user: dict = Depends(get_current_user)):
    if body.strategy_id:
        strategies = await database.list_strategies()
        if not any(row["id"] == body.strategy_id for row in strategies):
            raise HTTPException(status_code=422, detail="Linked strategy does not exist")
    owner_id = _owner(user)
    workspace = await copilot_store.create_workspace(owner_id, body.title, body.strategy_id)
    await database.log_action(
        "copilot_workspace_created",
        user_id=owner_id,
        resource=f"copilot_workspace:{workspace['id']}",
        details=json.dumps(
            {
                "strategy_id": body.strategy_id,
                "promotion_id": workspace.get("promotion_id"),
            }
        ),
    )
    return {"workspace": workspace}


@router.post("/workspaces/from-supervision", status_code=201)
async def create_workspace_from_supervision(
    body: WorkspaceFromSupervision,
    user: dict = Depends(get_current_user),
):
    """S3 / D13 — supervision EXPERIMENT ingress into a bound Copilot workspace."""
    if body.recommendation_kind != "experiment":
        raise HTTPException(
            status_code=422,
            detail="Only experiment recommendations can open a Copilot workspace",
        )
    owner_id = _owner(user)
    created = await copilot_store.create_from_supervision(
        owner_id,
        pair=body.pair.upper(),
        reason=body.reason,
        strategy=body.strategy,
        recommendation_kind=body.recommendation_kind,
        parameters=body.parameters,
    )
    await database.log_action(
        "copilot_workspace_from_supervision",
        user_id=owner_id,
        resource=f"copilot_workspace:{created['workspace']['id']}",
        details=json.dumps(
            {
                "pair": body.pair.upper(),
                "promotion_id": created["workspace"].get("promotion_id"),
                "recommendation_kind": body.recommendation_kind,
            }
        ),
    )
    return created


@router.get("/workspaces/{workspace_id}")
async def get_workspace(workspace_id: str, user: dict = Depends(get_current_user)):
    return {"workspace": await _owned_workspace(workspace_id, _owner(user))}


@router.get("/workspaces/{workspace_id}/conversations")
async def list_conversations(workspace_id: str, user: dict = Depends(get_current_user)):
    await _owned_workspace(workspace_id, _owner(user))
    conversations = await copilot_store.list_conversations(workspace_id)
    return {"conversations": conversations, "count": len(conversations)}


@router.post("/workspaces/{workspace_id}/conversations", status_code=201)
async def create_conversation(
    workspace_id: str,
    body: ConversationCreate,
    user: dict = Depends(get_current_user),
):
    owner_id = _owner(user)
    await _owned_workspace(workspace_id, owner_id)
    conversation = await copilot_store.create_conversation(workspace_id, body.title)
    await database.log_action(
        "copilot_conversation_created",
        user_id=owner_id,
        resource=f"copilot_conversation:{conversation['id']}",
    )
    return {"conversation": conversation}


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(conversation_id: str, user: dict = Depends(get_current_user)):
    await _owned_conversation(conversation_id, _owner(user))
    messages = await copilot_store.list_messages(conversation_id)
    return {"messages": messages, "count": len(messages)}


@router.post("/conversations/{conversation_id}/messages", status_code=201)
async def create_message(
    conversation_id: str,
    body: MessageCreate,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Persist a user message and return a Supervisor assistant reply (S1).

    Fail-closed: if Supervisor auth fails or the gateway is unreachable, the
    user message is still durable but no fabricated assistant success is
    returned — the API responds with 5xx/401 and omits a fake acknowledgement.
    """
    owner_id = _owner(user)
    conversation = await _owned_conversation(conversation_id, owner_id)
    workspace = await _owned_workspace(conversation["workspace_id"], owner_id)
    history = await copilot_store.list_messages(conversation_id)
    artifacts = await copilot_store.list_artifacts(workspace["id"])
    supervisor_messages = copilot_store.build_supervisor_messages(
        workspace, artifacts, history, body.content
    )

    authorization = request.headers.get("Authorization", "")
    try:
        assistant_text = await supervisor_client.complete_chat(
            supervisor_messages,
            authorization=authorization,
        )
    except SupervisorError as exc:
        # Persist the user turn so the operator does not lose the prompt, but
        # do not invent an assistant success reply (fail-closed).
        message = await copilot_store.append_user_message(conversation_id, body.content)
        await database.log_action(
            "copilot_supervisor_failed",
            user_id=owner_id,
            resource=f"copilot_conversation:{conversation_id}",
            details=json.dumps(
                {
                    "message_id": message["id"],
                    "error": str(exc),
                    "supervisor_status": exc.status_code,
                }
            ),
        )
        # Map Supervisor auth failures to 502 so the browser does not treat a
        # valid NWI session as logged-out (fail-closed without session wipe).
        status = exc.status_code or 502
        if status in (401, 403) or status < 400:
            status = 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc

    message = await copilot_store.append_user_message(conversation_id, body.content)
    acknowledgement = await copilot_store.append_assistant_message(
        conversation_id, assistant_text
    )
    await database.log_action(
        "copilot_message_created",
        user_id=owner_id,
        resource=f"copilot_conversation:{conversation_id}",
        details=json.dumps(
            {
                "message_id": message["id"],
                "assistant_message_id": acknowledgement["id"],
                "status": acknowledgement["status"],
            }
        ),
    )
    return {"message": message, "acknowledgement": acknowledgement}


@router.get("/workspaces/{workspace_id}/research/tools")
async def list_research_tools(workspace_id: str, user: dict = Depends(get_current_user)):
    await _owned_workspace(workspace_id, _owner(user))
    return {"tools": copilot_research.tool_schemas()}


@router.post("/workspaces/{workspace_id}/experiments", status_code=201)
async def run_experiment(
    workspace_id: str,
    body: ExperimentRun,
    user: dict = Depends(get_current_user),
):
    """Run an allowlisted research tool and persist the result as an artifact."""
    owner_id = _owner(user)
    await _owned_workspace(workspace_id, owner_id)
    try:
        result = copilot_research.execute_tool(body.tool, body.params)
    except ResearchBudgetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ResearchToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    artifact, revision = await copilot_store.create_artifact(
        workspace_id,
        result.artifact_kind,
        result.title[:120],
        result.content,
        owner_id,
    )
    await database.log_action(
        "copilot_experiment_ran",
        owner_id,
        f"copilot_workspace:{workspace_id}",
        json.dumps(
            {
                "tool": result.tool,
                "artifact_id": artifact["id"],
                "revision_id": revision["id"],
                "actor": "human",
            }
        ),
    )
    return {
        "artifact": artifact,
        "revision": revision,
        "summary": result.summary,
        "metrics": result.metrics,
        "tool": result.tool,
    }


@router.post("/workspaces/{workspace_id}/artifacts/import", status_code=201)
async def import_research_artifact(
    workspace_id: str,
    body: ArtifactImport,
    user: dict = Depends(get_current_user),
):
    """Import an external research JSON summary (Optuna CLI, notebook, etc.)."""
    owner_id = _owner(user)
    await _owned_workspace(workspace_id, owner_id)
    artifact, revision = await copilot_store.create_artifact(
        workspace_id, body.kind, body.title, body.content, owner_id
    )
    await database.log_action(
        "copilot_artifact_imported",
        owner_id,
        f"copilot_artifact:{artifact['id']}",
        json.dumps({"kind": body.kind, "revision_id": revision["id"]}),
    )
    return {"artifact": artifact, "revision": revision}


@router.get("/workspaces/{workspace_id}/artifacts")
async def list_artifacts(workspace_id: str, user: dict = Depends(get_current_user)):
    await _owned_workspace(workspace_id, _owner(user))
    artifacts = await copilot_store.list_artifacts(workspace_id)
    return {"artifacts": artifacts, "count": len(artifacts)}


@router.post("/workspaces/{workspace_id}/artifacts", status_code=201)
async def create_artifact(
    workspace_id: str, body: ArtifactCreate, user: dict = Depends(get_current_user)
):
    owner_id = _owner(user)
    await _owned_workspace(workspace_id, owner_id)
    artifact, revision = await copilot_store.create_artifact(
        workspace_id, body.kind, body.title, body.content, owner_id
    )
    await database.log_action(
        "copilot_artifact_created",
        owner_id,
        f"copilot_artifact:{artifact['id']}",
        json.dumps(
            {
                "kind": body.kind,
                "revision_id": revision["id"],
                "content_hash": revision["content_hash"],
            }
        ),
    )
    return {"artifact": artifact, "revision": revision}


@router.get("/artifacts/{artifact_id}/revisions")
async def list_revisions(artifact_id: str, user: dict = Depends(get_current_user)):
    artifact = await _owned_artifact(artifact_id, _owner(user))
    revisions = await copilot_store.list_revisions(artifact["id"])
    return {"revisions": revisions, "count": len(revisions)}


@router.post("/artifacts/{artifact_id}/revisions", status_code=201)
async def create_revision(
    artifact_id: str, body: RevisionCreate, user: dict = Depends(get_current_user)
):
    owner_id = _owner(user)
    artifact = await _owned_artifact(artifact_id, owner_id)
    revision = await copilot_store.create_revision(artifact, body.content, owner_id)
    await database.log_action(
        "copilot_artifact_revised",
        owner_id,
        f"copilot_artifact:{artifact_id}",
        json.dumps({"revision_id": revision["id"], "content_hash": revision["content_hash"]}),
    )
    return {"revision": revision}


@router.get("/artifacts/{artifact_id}/approvals")
async def list_approvals(artifact_id: str, user: dict = Depends(get_current_user)):
    artifact = await _owned_artifact(artifact_id, _owner(user))
    approvals = await copilot_store.list_approvals(artifact["id"])
    return {"approvals": approvals, "count": len(approvals)}


@router.post("/artifacts/{artifact_id}/revisions/{revision_id}/approval", status_code=201)
async def decide_revision(
    artifact_id: str, revision_id: str, body: ApprovalCreate, user: dict = Depends(get_current_user)
):
    owner_id = _owner(user)
    artifact = await _owned_artifact(artifact_id, owner_id)
    revisions = await copilot_store.list_revisions(artifact["id"])
    if not any(revision["id"] == revision_id for revision in revisions):
        raise HTTPException(status_code=404, detail="Copilot artifact revision not found")
    approval = await copilot_store.decide_revision(
        revision_id, body.decision, body.reason, owner_id
    )
    await database.log_action(
        "copilot_artifact_decided",
        owner_id,
        f"copilot_artifact_revision:{revision_id}",
        json.dumps({"decision": body.decision, "approval_id": approval["id"]}),
    )
    return {"approval": approval}


@router.get("/workspaces/{workspace_id}/lifecycle")
async def lifecycle_status(workspace_id: str, user: dict = Depends(get_current_user)):
    workspace = await _owned_workspace(workspace_id, _owner(user))
    return {
        "workspace": workspace,
        "eligibility": await copilot_store.transition_eligibility(workspace),
        "transitions": await copilot_store.list_transitions(workspace_id),
    }


@router.post("/workspaces/{workspace_id}/lifecycle/advance")
async def advance_lifecycle(workspace_id: str, user: dict = Depends(get_current_user)):
    owner_id = _owner(user)
    workspace = await _owned_workspace(workspace_id, owner_id)
    transition = await copilot_store.advance_lifecycle(workspace, owner_id)
    if not transition:
        eligibility = await copilot_store.transition_eligibility(workspace)
        raise HTTPException(
            status_code=409,
            detail=eligibility.get("reason") or "Lifecycle advance refused",
        )
    updated = await _owned_workspace(workspace_id, owner_id)
    await database.log_action(
        "copilot_lifecycle_advanced",
        owner_id,
        f"copilot_workspace:{workspace_id}",
        json.dumps(
            {
                "transition_id": transition["id"],
                "to": transition["to_lifecycle"],
                "promotion_id": updated.get("promotion_id"),
            }
        ),
    )
    return {"workspace": updated, "transition": transition}
