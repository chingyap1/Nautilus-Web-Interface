"""Step-up authentication for high-risk actions (D6.5, D6.6).

Provides a ``StepUpVerifier`` protocol so the mechanism is swappable without
touching callers. For v1, implements **TOTP** (RFC 6238):

- Offline, works with any standard authenticator app.
- Deterministically testable (inject a fixed clock and shared secret).
- Replay protection: reject a code already consumed within its validity window.
- A successful step-up grants a **5-minute elevated session window**, not a
  per-action code — requiring a fresh code for every approval tap is
  impractical on a phone.

The protocol is designed so a ``WebAuthnStepUpVerifier`` can be added later
without changing the approval/resume call sites — e.g. ``verify(principal,
factor) -> bool`` with no TOTP-specific fields leaking into the interface.
"""

from __future__ import annotations

import hashlib
import hmac
import struct
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

# D6.5: 5-minute elevated session window
ELEVATED_WINDOW = timedelta(minutes=5)
# TOTP parameters (RFC 6238)
TOTP_PERIOD = 30  # seconds
TOTP_DIGITS = 6


@runtime_checkable
class StepUpVerifier(Protocol):
    """Protocol for step-up authentication (D6.5).

    A successful verification grants an elevated session for 5 minutes.
    The protocol is mechanism-agnostic — TOTP, WebAuthn, etc.
    """

    def verify(self, principal: str, factor: str) -> bool:
        """Verify the step-up factor for *principal*.

        Returns True if the factor is valid and a fresh elevated session
        is granted. Returns False if the factor is invalid, expired, or
        replayed.
        """
        ...

    def is_elevated(self, principal: str) -> bool:
        """Check whether *principal* currently has an active elevated session."""
        ...

    def revoke_elevation(self, principal: str) -> None:
        """Revoke the elevated session for *principal* (e.g. on interlock engage)."""
        ...


def _totp_code(secret: str, timestamp: int, period: int = TOTP_PERIOD, digits: int = TOTP_DIGITS) -> str:
    """Generate a TOTP code from a base32-encoded secret and timestamp (RFC 6238)."""
    import base64

    key = base64.b32decode(secret, casefold=True)
    counter = timestamp // period
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_bytes = digest[offset : offset + 4]
    code_int = struct.unpack(">I", code_bytes)[0] & 0x7FFFFFFF
    code = code_int % (10**digits)
    return str(code).zfill(digits)


class TOTPStepUpVerifier:
    """TOTP-based step-up verifier (D6.5).

    Uses a fixed clock for deterministic testing. In production, the clock
    is ``time.time()``.

    Replay protection: a consumed TOTP code cannot be reused within its
    30-second validity window. A successful verification grants a 5-minute
    elevated session.
    """

    def __init__(
        self,
        *,
        clock: callable | None = None,
        secrets: dict[str, str] | None = None,
    ) -> None:
        """Initialize the verifier.

        Args:
            clock: A callable returning the current Unix timestamp (float).
                   Defaults to ``time.time``.
            secrets: A mapping of principal -> base32-encoded TOTP secret.
                     In production, secrets are stored encrypted and loaded
                     from the database.
        """
        self._clock = clock or time.time
        self._secrets = secrets or {}
        self._elevated_until: dict[str, float] = {}
        self._consumed_codes: dict[str, float] = {}  # code -> consumed_at_timestamp

    def set_secret(self, principal: str, secret: str) -> None:
        """Set the TOTP shared secret for a principal."""
        self._secrets[principal] = secret

    def verify(self, principal: str, factor: str) -> bool:
        """Verify a TOTP code and grant an elevated session if valid.

        Returns False if:
        - The principal has no registered secret.
        - The code is incorrect for the current time window.
        - The code has already been consumed (replay protection).
        """
        secret = self._secrets.get(principal)
        if not secret:
            return False

        now = int(self._clock())

        # Check current and adjacent windows (±1 period for clock skew)
        for offset in (-1, 0, 1):
            window = now + offset * TOTP_PERIOD
            expected = _totp_code(secret, window)
            if hmac.compare_digest(factor, expected):
                # Replay protection: check if this code was already consumed
                replay_key = f"{principal}:{factor}"
                consumed_at = self._consumed_codes.get(replay_key)
                if consumed_at is not None:
                    # The code was already used — reject replay
                    return False
                # Mark code as consumed
                self._consumed_codes[replay_key] = float(now)
                # Grant elevated session
                self._elevated_until[principal] = float(now) + ELEVATED_WINDOW.total_seconds()
                # Clean up old consumed codes (older than 90 seconds)
                cutoff = float(now) - 90
                stale_keys = [k for k, v in self._consumed_codes.items() if v < cutoff]
                for k in stale_keys:
                    del self._consumed_codes[k]
                return True
        return False

    def is_elevated(self, principal: str) -> bool:
        """Check whether *principal* has an active elevated session."""
        until = self._elevated_until.get(principal)
        if until is None:
            return False
        if self._clock() >= until:
            del self._elevated_until[principal]
            return False
        return True

    def revoke_elevation(self, principal: str) -> None:
        """Revoke the elevated session for *principal*."""
        self._elevated_until.pop(principal, None)

    def revoke_all_elevations(self) -> None:
        """Revoke all elevated sessions (e.g. on interlock engage, D5)."""
        self._elevated_until.clear()


# Singleton instance for the NWI backend
_verifier: StepUpVerifier | None = None


def get_step_up_verifier() -> StepUpVerifier:
    """Return the global StepUpVerifier instance."""
    global _verifier
    if _verifier is None:
        _verifier = TOTPStepUpVerifier()
    return _verifier


def set_step_up_verifier(verifier: StepUpVerifier) -> None:
    """Set the global StepUpVerifier instance (for testing)."""
    global _verifier
    _verifier = verifier
