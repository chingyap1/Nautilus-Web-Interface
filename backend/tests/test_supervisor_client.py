"""Unit tests for NWI → Supervisor chat client (S1)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import supervisor_client
from supervisor_client import SupervisorError, complete_chat, set_chat_completer


@pytest.fixture(autouse=True)
def _reset_completer():
    set_chat_completer(None)
    yield
    set_chat_completer(None)


def test_complete_chat_uses_override():
    async def fake(messages, *, authorization, model=None):
        assert authorization.startswith("Bearer ")
        assert messages[-1]["content"] == "hello"
        return "Fake reply to: hello"

    set_chat_completer(fake)
    text = asyncio.run(
        complete_chat(
            [{"role": "user", "content": "hello"}],
            authorization="Bearer test-token",
        )
    )
    assert text == "Fake reply to: hello"


def test_complete_chat_requires_bearer():
    with pytest.raises(SupervisorError, match="Missing bearer"):
        asyncio.run(
            complete_chat(
                [{"role": "user", "content": "hi"}],
                authorization="Basic nope",
            )
        )


def test_complete_chat_maps_http_401(monkeypatch):
    class FakeResponse:
        status_code = 401
        text = "unauthorized"

        def json(self):
            return {"detail": "Invalid or expired token"}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(supervisor_client.httpx, "AsyncClient", FakeClient)
    with pytest.raises(SupervisorError, match="rejected authentication") as exc:
        asyncio.run(
            complete_chat(
                [{"role": "user", "content": "hi"}],
                authorization="Bearer bad",
            )
        )
    assert exc.value.status_code == 401


def test_complete_chat_unreachable(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(supervisor_client.httpx, "AsyncClient", FakeClient)
    with pytest.raises(SupervisorError, match="unreachable") as exc:
        asyncio.run(
            complete_chat(
                [{"role": "user", "content": "hi"}],
                authorization="Bearer tok",
            )
        )
    assert exc.value.status_code == 503
