"""Web Push fan-out for operator-attention events."""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def clean_dedupe():
    import push_notify

    push_notify.reset_dedupe()
    yield
    push_notify.reset_dedupe()


async def _add_user(username: str, role: str, principal_type: str = "human") -> None:
    import database

    await database.create_user(username, "x" * 20, role=role, principal_type=principal_type)


async def _add_subscription(username: str, endpoint: str) -> None:
    import database

    await database.upsert_push_subscription(
        username=username,
        endpoint=endpoint,
        p256dh="B" + "x" * 80,
        auth="t" + "y" * 20,
    )


def test_recipients_filter_by_role_and_principal(client):
    import database
    import push_notify

    async def scenario():
        await _add_user("alice", "approver")
        await _add_user("bob", "operator")
        await _add_user("svc", "operator", principal_type="service")
        gone = await database.create_user("carol", "x" * 20, role="approver")
        await database.delete_user(gone["id"])

        return await push_notify.recipients_for_roles(("approver", "admin"))

    recipients = asyncio.run(scenario())
    assert "alice" in recipients
    assert "bob" not in recipients
    assert "svc" not in recipients
    assert "carol" not in recipients


def test_send_prunes_gone_endpoints(client, monkeypatch):
    import database
    import push_notify
    import push_vapid

    monkeypatch.setattr(push_vapid, "get_private_pem", lambda: "-----FAKE-----")
    monkeypatch.setitem(__import__("sys").modules, "pywebpush", _fake_pywebpush())

    statuses = {"https://push.example.com/gone": 410}
    monkeypatch.setattr(
        push_notify,
        "_send_one",
        lambda sub, payload, pem: statuses.get(sub["endpoint"]),
    )

    async def scenario():
        await _add_user("alice", "approver")
        await _add_subscription("alice", "https://push.example.com/live")
        await _add_subscription("alice", "https://push.example.com/gone")

        result = await push_notify.send_to_users(["alice"], title="t", body="b")
        remaining = await database.list_push_subscriptions("alice")
        return result, remaining

    result, remaining = asyncio.run(scenario())
    assert result == {"sent": 1, "failed": 1, "pruned": 1}
    assert [s["endpoint"] for s in remaining] == ["https://push.example.com/live"]


def test_notify_roles_dedupes_repeat_events(client, monkeypatch):
    import push_notify

    calls: list[list[str]] = []

    async def fake_send(usernames, **kwargs):
        calls.append(list(usernames))
        return {"sent": 1, "failed": 0, "pruned": 0}

    monkeypatch.setattr(push_notify, "send_to_users", fake_send)

    async def scenario():
        await _add_user("alice", "approver")
        first = await push_notify.notify_roles(
            ("approver",), title="t", body="b", dedupe_key="proposal:PRP-1"
        )
        second = await push_notify.notify_roles(
            ("approver",), title="t", body="b", dedupe_key="proposal:PRP-1"
        )
        third = await push_notify.notify_roles(
            ("approver",), title="t", body="b", dedupe_key="proposal:PRP-2"
        )
        return first, second, third

    first, second, third = asyncio.run(scenario())
    assert first["sent"] == 1
    assert second.get("skipped") == 1
    assert third["sent"] == 1
    assert calls == [["alice"], ["alice"]]


def test_notify_roles_never_raises_without_vapid(client, monkeypatch):
    import push_notify
    import push_vapid

    monkeypatch.setattr(push_vapid, "get_private_pem", lambda: None)

    async def scenario():
        await _add_user("alice", "approver")
        await _add_subscription("alice", "https://push.example.com/live")
        return await push_notify.notify_roles(("approver",), title="t", body="b")

    assert asyncio.run(scenario()) == {"sent": 0, "failed": 0, "pruned": 0}


def test_interlock_engage_notifies_admins(client, monkeypatch):
    """Engaging the interlock pings admins — only they can resume it."""
    import push_notify

    sent: list[dict] = []

    async def fake_notify(roles, **kwargs):
        sent.append({"roles": tuple(roles), **kwargs})
        return {"sent": 0, "failed": 0, "pruned": 0}

    monkeypatch.setattr(push_notify, "notify_roles", fake_notify)

    r = client.post("/api/supervision/interlock/engage", json={"reason": "drill"})
    assert r.status_code == 200, r.text
    assert len(sent) == 1
    assert sent[0]["roles"] == ("admin",)
    assert sent[0]["url"] == "/m/controls"
    assert "drill" in sent[0]["body"]

    client.post("/api/supervision/interlock/resume", json={"reason": "all clear"})
    assert sent[-1]["roles"] == ("operator", "approver", "admin")


def _fake_pywebpush():
    """Minimal stand-in so `from pywebpush import ...` resolves in tests."""
    import types

    module = types.ModuleType("pywebpush")

    class WebPushException(Exception):
        pass

    module.WebPushException = WebPushException
    module.webpush = lambda **kwargs: None
    return module
