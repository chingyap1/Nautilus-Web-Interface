"""VAPID application-server key management for Web Push.

Keys come from ``VAPID_PUBLIC_KEY`` / ``VAPID_PRIVATE_KEY``. When unset outside
production an ephemeral in-process pair is generated so local dev works without
setup; production fails closed instead, because ephemeral keys would silently
invalidate every stored subscription on restart.
"""

from __future__ import annotations

import base64
import logging
import os

from fastapi import HTTPException

logger = logging.getLogger(__name__)

_DEV_VAPID: dict[str, str] | None = None


def _urlsafe_b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _generate_dev_vapid() -> dict[str, str]:
    """Ephemeral VAPID pair for local/dev when env keys are unset."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_numbers = private_key.private_numbers().public_numbers

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


def reset_dev_keys() -> None:
    """Drop the cached ephemeral pair (tests, key rotation)."""
    global _DEV_VAPID
    _DEV_VAPID = None


def is_configured() -> bool:
    return bool((os.getenv("VAPID_PUBLIC_KEY") or "").strip())


def get_public_key() -> str:
    """Return the configured VAPID public key, or a process-local dev key."""
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
        logger.warning("VAPID_PUBLIC_KEY unset — using ephemeral in-process keys (dev only)")
    return _DEV_VAPID["public_key"]


def get_private_pem() -> str | None:
    """Return the private key PEM, or None when no key is available."""
    pem = (os.getenv("VAPID_PRIVATE_KEY") or "").strip()
    if pem:
        # Allow escaped newlines in .env
        return pem.replace("\\n", "\n")
    if _DEV_VAPID is not None:
        return _DEV_VAPID["private_pem"]
    return None


def get_claims() -> dict[str, str]:
    contact = (os.getenv("VAPID_CONTACT") or "mailto:ops@localhost").strip()
    return {"sub": contact}
