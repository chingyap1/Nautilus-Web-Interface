"""Web Push registration API — Mobile Ops Account opt-in."""

from __future__ import annotations


def test_vapid_public_key_available_in_dev(client, monkeypatch):
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    # Reset cached ephemeral keys between tests
    import routers.push as push_mod

    push_mod._DEV_VAPID = None

    r = client.get("/api/push/vapid-public-key")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["public_key"]
    assert body["configured"] is False


def test_subscribe_status_unsubscribe(client, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    import routers.push as push_mod

    push_mod._DEV_VAPID = None

    status = client.get("/api/push/status")
    assert status.status_code == 200
    assert status.json()["subscribed"] is False
    assert status.json()["available"] is True

    sub = {
        "endpoint": "https://push.example.com/sub/abc123",
        "keys": {"p256dh": "BNcRd8" + "x" * 80, "auth": "tBHItJ" + "y" * 16},
    }
    r = client.post("/api/push/subscribe", json=sub)
    assert r.status_code == 200, r.text
    assert r.json()["subscribed"] is True
    assert r.json()["subscription_id"]

    status = client.get("/api/push/status")
    assert status.json()["subscribed"] is True
    assert status.json()["subscription_count"] == 1

    # Upsert same endpoint
    r2 = client.post("/api/push/subscribe", json=sub)
    assert r2.status_code == 200
    status = client.get("/api/push/status")
    assert status.json()["subscription_count"] == 1

    un = client.post("/api/push/unsubscribe", json={"endpoint": sub["endpoint"]})
    assert un.status_code == 200
    assert un.json()["removed"] is True
    status = client.get("/api/push/status")
    assert status.json()["subscribed"] is False


def test_production_requires_vapid_env(client, monkeypatch):
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    import routers.push as push_mod

    push_mod._DEV_VAPID = None

    r = client.get("/api/push/vapid-public-key")
    assert r.status_code == 503


def test_unsubscribe_other_users_endpoint_is_noop(client, monkeypatch, tmp_path):
    """Deleting an endpoint not owned by the caller returns removed=false."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    import routers.push as push_mod

    push_mod._DEV_VAPID = None

    r = client.post(
        "/api/push/unsubscribe",
        json={"endpoint": "https://push.example.com/not-mine"},
    )
    assert r.status_code == 200
    assert r.json()["removed"] is False
