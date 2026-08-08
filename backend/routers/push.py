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

import base64
import json
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import database
from auth_jwt import get_current_user, require_operator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/push", tags=["push"])

_DEV_VAPID: dict[str, str] | None = None


class PushKeys(BaseModel):
    p256dh: str = Field(..., min_length=8)
    auth: str = Field(..., min_length=8)


class SubscribeRequest(BaseModel):
    endpoint: str = Field(..., min_length=8)
    keys: PushKeys


class UnsubscribeRequest(BaseModel):
    endpoint: str = Field(..., min_length=8)


def _urlsafe_b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _generate_dev_vapid() -> dict[str, str]:
    """Ephemeral VAPID pair for local/dev when env keys are unset."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization

    private_key = ec.generate_private_key(ec.SECP256R1())
    private_numbers = private_key.private_numbers()
    public_numbers = private_numbers.public_numbers

    # Uncompressed EC point (0x04 || x || y) — Push API applicationServerKey format
    x = public_numbers.x.to_bytes(32, "big")
    y = public_numbers.y.to_bytes(32, "big")
    public_key = _urlsafe_b64(b"\x04" + x + y)

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")

    return {"public_key": public_key, "private_pem": private_pem}


def get_vapid_public_key() -> str:
    """Return configured VAPID public key, or a process-local dev key."""
    configured = (os.getenv("VAPID_PUBLIC_KEY") or "").strip()
    if configured:
        return configured

    env = (os.getenv("ENVIRONMENT") or "development").strip().lower()
    if env == "production":
        raise HTTPException(
            status_code=503,
            detail="Web Push is not configured (set VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY)",
        )

    global _DEV_VAPID
    if _DEV_VAPID is None:
        _DEV_VAPID = _generate_dev_vapid()
        logger.warning(
            "VAPID_PUBLIC_KEY unset — using ephemeral in-process keys (dev only)"
        )
    return _DEV_VAPID["public_key"]


def _vapid_private_pem() -> str | None:
    pem = (os.getenv("VAPID_PRIVATE_KEY") or "").strip()
    if pem:
        # Allow escaped newlines in .env
        return pem.replace("\\n", "\n")
    if _DEV_VAPID is not None:
        return _DEV_VAPID["private_pem"]
    return None


def _vapid_claims() -> dict[str, str]:
    contact = (os.getenv("VAPID_CONTACT") or "mailto:ops@localhost").strip()
    return {"sub": contact}


@router.get("/vapid-public-key")
async def vapid_public_key(_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Return the application server key for PushManager.subscribe."""
    return {
        "public_key": get_vapid_public_key(),
        "configured": bool((os.getenv("VAPID_PUBLIC_KEY") or "").strip()),
    }


@router.get("/status")
async def push_status(_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Return whether the caller has any registered push endpoints."""
    username = _user.get("sub") or _user.get("username") or ""
    count = await database.count_push_subscriptions(username)
    try:
        get_vapid_public_key()
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
    get_vapid_public_key()

    username = _user.get("sub") or _user.get("username") or ""
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
        details=json.dumps({"endpoint_host": body.endpoint.split("/")[2] if "://" in body.endpoint else ""}),
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
    username = _user.get("sub") or _user.get("username") or ""
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
    username = _user.get("sub") or _user.get("username") or ""
    subs = await database.list_push_subscriptions(username)
    if not subs:
        raise HTTPException(status_code=404, detail="No push subscriptions for this user")

    private_pem = _vapid_private_pem()
    if not private_pem:
        raise HTTPException(
            status_code=503,
            detail="VAPID private key not available — cannot send test push",
        )

    try:
        from pywebpush import webpush, WebPushException
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="pywebpush is not installed on this server",
        ) from exc

    payload = json.dumps(
        {
            "title": "Mobile Ops",
            "body": "Test attention ping — push registration works.",
            "url": "/m/status",
        }
    )
    sent = 0
    errors: list[str] = []
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                },
                data=payload,
                vapid_private_key=private_pem,
                vapid_claims=_vapid_claims(),
            )
            sent += 1
        except WebPushException as exc:
            errors.append(str(exc))
            # Drop gone subscriptions
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in (404, 410):
                await database.delete_push_subscription(username, sub["endpoint"])

    return {"success": sent > 0, "sent": sent, "errors": errors[:5]}
