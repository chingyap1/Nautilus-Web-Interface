"""Web Push fan-out for operator-attention events.

Push is **attention only** — never authority. A notification tells an operator
to open Mobile Ops; every mutation still goes through NWI's authenticated,
role-gated, two-step approve → dispatch path.

Delivery is best-effort by design: ``notify_roles`` never raises, so a dead
push service or missing VAPID key can't block a proposal, an interlock engage,
or any other trading-plane action.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Iterable, Sequence

import database
import push_vapid

logger = logging.getLogger(__name__)

# Endpoints that answer with these are permanently gone — drop the row.
_GONE_STATUS = (404, 410)

# Suppress repeat notifications for the same event key within this window.
DEDUPE_TTL_SECONDS = 300.0

_recent_sends: dict[str, float] = {}


class PushUnavailable(RuntimeError):
    """No usable VAPID key or push library on this server."""


def reset_dedupe() -> None:
    """Clear the in-process dedupe window (tests)."""
    _recent_sends.clear()


def _dedupe_hit(key: str | None) -> bool:
    """Return True when this key was already notified inside the TTL."""
    if not key:
        return False
    now = time.monotonic()
    for stale in [k for k, seen in _recent_sends.items() if now - seen > DEDUPE_TTL_SECONDS]:
        _recent_sends.pop(stale, None)
    if key in _recent_sends:
        return True
    _recent_sends[key] = now
    return False


async def recipients_for_roles(roles: Sequence[str]) -> list[str]:
    """Active human usernames holding any of ``roles``."""
    wanted = {r.lower() for r in roles}
    users = await database.list_users()
    return [
        u["username"]
        for u in users
        if u.get("is_active")
        and (u.get("role") or "").lower() in wanted
        and (u.get("principal_type") or "human") == "human"
    ]


def _send_one(subscription: dict[str, Any], payload: str, private_pem: str) -> int | None:
    """Blocking single send. Returns the HTTP status on failure, None on success."""
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info={
                "endpoint": subscription["endpoint"],
                "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]},
            },
            data=payload,
            vapid_private_key=private_pem,
            vapid_claims=push_vapid.get_claims(),
        )
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        logger.warning("Web Push send failed (status=%s): %s", status, exc)
        return status or 0
    return None


async def send_to_users(
    usernames: Iterable[str],
    *,
    title: str,
    body: str,
    url: str = "/m/status",
) -> dict[str, int]:
    """Deliver one notification to every subscription of ``usernames``.

    Raises ``PushUnavailable`` when the server cannot send at all. Individual
    endpoint failures are counted, not raised; gone endpoints are pruned.
    """
    private_pem = push_vapid.get_private_pem()
    if not private_pem:
        raise PushUnavailable("VAPID private key not available")
    try:
        from pywebpush import webpush  # noqa: F401
    except ImportError as exc:
        raise PushUnavailable("pywebpush is not installed on this server") from exc

    payload = json.dumps({"title": title, "body": body, "url": url})
    sent = 0
    failed = 0
    pruned = 0

    for username in usernames:
        for subscription in await database.list_push_subscriptions(username):
            status = await asyncio.to_thread(_send_one, subscription, payload, private_pem)
            if status is None:
                sent += 1
                continue
            failed += 1
            if status in _GONE_STATUS:
                await database.delete_push_subscription(username, subscription["endpoint"])
                pruned += 1

    return {"sent": sent, "failed": failed, "pruned": pruned}


async def notify_roles(
    roles: Sequence[str],
    *,
    title: str,
    body: str,
    url: str = "/m/status",
    dedupe_key: str | None = None,
) -> dict[str, int]:
    """Best-effort fan-out to every active human holding one of ``roles``.

    Never raises — attention delivery must not affect the trading plane.
    """
    if _dedupe_hit(dedupe_key):
        return {"sent": 0, "failed": 0, "pruned": 0, "skipped": 1}
    try:
        usernames = await recipients_for_roles(roles)
        if not usernames:
            return {"sent": 0, "failed": 0, "pruned": 0}
        return await send_to_users(usernames, title=title, body=body, url=url)
    except PushUnavailable as exc:
        logger.info("Skipping push notification: %s", exc)
    except Exception:  # pragma: no cover - defensive; push must never break callers
        logger.exception("Unexpected error while sending push notifications")
    return {"sent": 0, "failed": 0, "pruned": 0}
