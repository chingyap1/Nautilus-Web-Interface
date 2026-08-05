"""
JWT authentication utilities — Sprint 1 (S1-02).

Provides token creation, validation, and user authentication.
Uses bcrypt directly (avoids passlib 1.7.x / bcrypt 5.x incompatibility).
Users are persisted in SQLite (users table) seeded at startup.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

# ── Configuration ────────────────────────────────────────────────────────────

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-CHANGE-IN-PRODUCTION-min-32-chars")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "8"))

_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """Hash a password using bcrypt directly."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


async def authenticate_user(username: str, password: str) -> Optional[dict]:
    """Return user dict if credentials are valid, else None (DB-backed)."""
    import database
    user = await database.get_user(username)
    if user and verify_password(password, user["hashed_password"]):
        return user
    return None


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a signed JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    )
    to_encode.update({"exp": expire, "jti": str(uuid.uuid4())})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """Decode and verify a JWT token. Returns payload dict or None."""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


# ── FastAPI dependency helpers ────────────────────────────────────────────────

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    """Dependency: verify JWT and return payload. Raises 401 on failure."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing token")
    payload = decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    # Check persistent DB blacklist (survives restarts)
    jti = payload.get("jti")
    if jti:
        import database
        if await database.is_token_revoked(jti):
            raise HTTPException(status_code=401, detail="Token has been revoked")
    return payload


async def require_admin(payload: dict = Depends(get_current_user)) -> dict:
    """Dependency: verify JWT and require admin role (human principal only).

    Raises 403 if the caller is not admin or is a service principal.
    """
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    if payload.get("principal_type") == "service":
        raise HTTPException(status_code=403, detail="Service principals cannot hold admin role")
    return payload


async def require_approver(payload: dict = Depends(get_current_user)) -> dict:
    """Dependency: verify JWT and require approver role (human principal only).

    Service principals are structurally barred from approver (D6.4).
    Raises 403 if the caller is not an approver or is a service principal.
    """
    if payload.get("principal_type") == "service":
        raise HTTPException(status_code=403, detail="Service principals cannot approve")
    if payload.get("role") != "approver":
        raise HTTPException(status_code=403, detail="Approver role required")
    return payload


async def require_operator(payload: dict = Depends(get_current_user)) -> dict:
    """Dependency: verify JWT and require operator role or higher.

    operator, approver, and admin are all permitted.
    Service principals with operator role are permitted (they can create
    proposals) but cannot hold approver/admin (enforced at creation).
    """
    role = payload.get("role")
    if role not in ("operator", "approver", "admin"):
        raise HTTPException(status_code=403, detail="Operator role required")
    return payload
