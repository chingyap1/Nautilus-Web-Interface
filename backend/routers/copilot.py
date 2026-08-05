"""Authenticated, non-executing Strategy Copilot workspace API (Phase O1a)."""

import json

import copilot_store
import database
from auth_jwt import get_current_user
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from state import manager

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
    kind: str = Field(pattern="^(specification|strategy_draft)$")
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=50_000)

    @field_validator("title", "content")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
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


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    _title = field_validator("title")(
        lambda value: value.strip() or (_ for _ in ()).throw(ValueError("title must not be blank"))
    )


class TaskEventCreate(BaseModel):
    status: str = Field(pattern="^(running|succeeded|failed|cancelled)$")
    progress: int = Field(ge=0, le=100)
    message: str = Field(min_length=1, max_length=2_000)
    _message = field_validator("message")(
        lambda value: (
            value.strip() or (_ for _ in ()).throw(ValueError("message must not be blank"))
        )
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


async def _owned_task(task_id: str, owner_id: str) -> dict:
    task = await copilot_store.get_task(task_id, owner_id)
    if not task:
        raise HTTPException(status_code=404, detail="Copilot task not found")
    return task


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
        details=json.dumps({"strategy_id": body.strategy_id}),
    )
    return {"workspace": workspace}


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
    user: dict = Depends(get_current_user),
):
    owner_id = _owner(user)
    await _owned_conversation(conversation_id, owner_id)
    message, acknowledgement = await copilot_store.append_user_message(
        conversation_id, body.content
    )
    await database.log_action(
        "copilot_message_created",
        user_id=owner_id,
        resource=f"copilot_conversation:{conversation_id}",
        details=json.dumps({"message_id": message["id"], "status": message["status"]}),
    )
    return {"message": message, "acknowledgement": acknowledgement}


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


@router.get("/workspaces/{workspace_id}/tasks")
async def list_tasks(workspace_id: str, user: dict = Depends(get_current_user)):
    await _owned_workspace(workspace_id, _owner(user))
    tasks = await copilot_store.list_tasks(workspace_id)
    return {"tasks": tasks, "count": len(tasks)}


@router.post("/workspaces/{workspace_id}/tasks", status_code=201)
async def create_task(workspace_id: str, body: TaskCreate, user: dict = Depends(get_current_user)):
    owner_id = _owner(user)
    await _owned_workspace(workspace_id, owner_id)
    task, event = await copilot_store.create_task(workspace_id, body.title, owner_id)
    await database.log_action(
        "copilot_task_created",
        owner_id,
        f"copilot_task:{task['id']}",
        json.dumps({"workspace_id": workspace_id, "event_id": event["id"]}),
    )
    return {"task": task, "event": event}


@router.get("/tasks/{task_id}/events")
async def list_task_events(task_id: str, user: dict = Depends(get_current_user)):
    task = await _owned_task(task_id, _owner(user))
    events = await copilot_store.list_task_events(task["id"])
    return {"events": events, "count": len(events)}


@router.post("/tasks/{task_id}/events", status_code=201)
async def create_task_event(
    task_id: str, body: TaskEventCreate, user: dict = Depends(get_current_user)
):
    owner_id = _owner(user)
    task = await _owned_task(task_id, owner_id)
    try:
        task, event = await copilot_store.append_task_event(
            task["id"], body.status, body.progress, body.message, owner_id
        )
    except copilot_store.TaskProgressConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    message = {
        "type": "copilot_task_progress",
        "workspace_id": task["workspace_id"],
        "task": task,
        "event": event,
    }
    await manager.send_to(owner_id, message)
    await database.log_action(
        "copilot_task_progressed",
        owner_id,
        f"copilot_task:{task_id}",
        json.dumps({"event_id": event["id"], "status": body.status, "progress": body.progress}),
    )
    return {"task": task, "event": event}


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
        raise HTTPException(status_code=409, detail=eligibility["reason"])
    updated = await _owned_workspace(workspace_id, owner_id)
    await database.log_action(
        "copilot_lifecycle_advanced",
        owner_id,
        f"copilot_workspace:{workspace_id}",
        json.dumps({"transition_id": transition["id"], "to": transition["to_lifecycle"]}),
    )
    return {"workspace": updated, "transition": transition}
