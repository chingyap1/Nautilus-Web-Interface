"""Authenticated, non-executing Strategy Copilot workspace API (Phase O1a)."""

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

import copilot_store
import database
from auth_jwt import get_current_user

router = APIRouter(prefix="/api/copilot", tags=["strategy-copilot"])


class WorkspaceCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    strategy_id: Optional[str] = Field(default=None, max_length=120)

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