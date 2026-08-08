"""HTTP client from NWI to the Supervisor LLM gateway (Phase O2 / S1).

The browser never talks to the model. Copilot message handlers forward the
operator's NWI JWT to Supervisor ``/v1/chat/completions`` using the shared
``SECRET_KEY``. Failures are fail-closed: callers must not invent a success
reply when the Supervisor is unreachable or rejects auth.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

import httpx

DEFAULT_SUPERVISOR_URL = "http://127.0.0.1:8123"
DEFAULT_MODEL = "fake"
DEFAULT_TIMEOUT_SECONDS = 30.0


class SupervisorError(Exception):
    """Raised when the Supervisor call fails closed."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ChatCompleter(Protocol):
    async def __call__(
        self,
        messages: list[dict[str, str]],
        *,
        authorization: str,
        model: str | None = None,
    ) -> str: ...


_completer_override: ChatCompleter | None = None


def set_chat_completer(completer: ChatCompleter | None) -> None:
    """Replace the HTTP completer (tests). Pass ``None`` to restore default."""
    global _completer_override
    _completer_override = completer


def supervisor_base_url() -> str:
    return os.getenv("SUPERVISOR_URL", DEFAULT_SUPERVISOR_URL).rstrip("/")


def supervisor_model() -> str:
    return os.getenv("SUPERVISOR_MODEL", DEFAULT_MODEL)


async def complete_chat(
    messages: list[dict[str, str]],
    *,
    authorization: str,
    model: str | None = None,
) -> str:
    """Return assistant text from Supervisor chat completions.

    ``authorization`` must be the full ``Bearer <jwt>`` header value from the
    operator's NWI request.
    """
    if _completer_override is not None:
        return await _completer_override(
            messages, authorization=authorization, model=model
        )

    if not authorization or not authorization.lower().startswith("bearer "):
        raise SupervisorError("Missing bearer token for Supervisor", status_code=401)

    url = f"{supervisor_base_url()}/v1/chat/completions"
    payload = {
        "model": model or supervisor_model(),
        "messages": messages,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": authorization,
                    "Content-Type": "application/json",
                },
            )
    except httpx.TimeoutException as exc:
        raise SupervisorError("Supervisor request timed out", status_code=504) from exc
    except httpx.HTTPError as exc:
        raise SupervisorError(
            f"Supervisor unreachable: {exc}", status_code=503
        ) from exc

    if response.status_code in (401, 403):
        raise SupervisorError(
            "Supervisor rejected authentication",
            status_code=response.status_code,
        )
    if response.status_code >= 400:
        detail = _safe_detail(response)
        raise SupervisorError(
            f"Supervisor error ({response.status_code}): {detail}",
            status_code=502,
        )

    try:
        data: dict[str, Any] = response.json()
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise SupervisorError(
            "Supervisor returned an invalid chat completion payload",
            status_code=502,
        ) from exc

    if not isinstance(content, str) or not content.strip():
        raise SupervisorError(
            "Supervisor returned an empty assistant message",
            status_code=502,
        )
    return content


def _safe_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict):
            detail = body.get("detail") or body.get("error") or body
            return str(detail)[:500]
    except ValueError:
        pass
    return (response.text or "unknown error")[:500]
