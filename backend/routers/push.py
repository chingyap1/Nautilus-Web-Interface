"""
Web Push registration — Mobile Ops Account opt-in.

Endpoints:
  GET  /api/push/vapid-public-key — application server public key
  GET  /api/push/status           — caller's subscription count
  POST /api/push/subscribe        — upsert browser PushSubscription
  POST /api/push/unsubscribe      — remove by endpoint
  POST /api/push/test             — send a dry-run notification (operator+)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import database
import push_notify
import push_vapid
from auth_jwt import get_current_user, require_operator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/push", tags=["push"])


class PushKeys(BaseModel):
    p256dh: str = Field(..., min_length=8)
    auth: str = Field(..., min_length=8)


class SubscribeRequest(BaseModel):
    endpoint: str = Field(..., min_length=8)
    keys: PushKeys


class UnsubscribeRequest(BaseModel):
    endpoint: str = Field(..., min_length=8)


def _subject(user: dict) -> str:
    return user.get("sub") or user.get("username") or ""


@router.get("/vapid-public-key")
async def vapid_public_key(_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Return the application server key for PushManager.subscribe."""
    return {
        "public_key": push_vapid.get_public_key(),
        "configured": push_vapid.is_configured(),
    }


@router.get("/status")
async def push_status(_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Return whether the caller has any registered push endpoints."""
    count = await database.count_push_subscriptions(_subject(_user))
    try:
        push_vapid.get_public_key()
        available = True
        reason = None
    except HTTPException as exc:
        available = False
        reason = str(exc.detail)
    return {
        "available": available,
        "reason": reason,
        "subscribed": count > 0,
        "subscription_count": count,
    }


@router.post("/subscribe")
async def subscribe(
    body: SubscribeRequest,
    request: Request,
    _user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Upsert a browser PushSubscription for the authenticated user."""
    # Ensure VAPID is available (fail closed in production)
    push_vapid.get_public_key()

    username = _subject(_user)
    if not username:
        raise HTTPException(status_code=401, detail="Missing subject")

    user_agent = (request.headers.get("user-agent") or "")[:512]
    row = await database.upsert_push_subscription(
        username=username,
        endpoint=body.endpoint,
        p256dh=body.keys.p256dh,
        auth=body.keys.auth,
        user_agent=user_agent,
    )
    await database.log_action(
        action="push_subscribe",
        user_id=username,
        resource=f"push:{row.get('id', '')}",
        details=json.dumps(
            {"endpoint_host": body.endpoint.split("/")[2] if "://" in body.endpoint else ""}
        ),
    )
    return {
        "success": True,
        "subscription_id": row.get("id"),
        "subscribed": True,
    }


@router.post("/unsubscribe")
async def unsubscribe(
    body: UnsubscribeRequest,
    _user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Remove a push subscription owned by the caller."""
    username = _subject(_user)
    removed = await database.delete_push_subscription(username, body.endpoint)
    if removed:
        await database.log_action(
            action="push_unsubscribe",
            user_id=username,
            resource="push",
        )
    return {"success": True, "removed": removed, "subscribed": False}


@router.post("/test")
async def push_test(_user: dict = Depends(require_operator)) -> dict[str, Any]:
    """Send a dry-run Mobile Ops attention ping to the caller's subscriptions."""
    username = _subject(_user)
    if not await database.count_push_subscriptions(username):
        raise HTTPException(status_code=404, detail="No push subscriptions for this user")

    try:
        result = await push_notify.send_to_users(
            [username],
            title="Mobile Ops",
            body="Test attention ping — push registration works.",
            url="/m/status",
        )
    except push_notify.PushUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {"success": result["sent"] > 0, **result}
