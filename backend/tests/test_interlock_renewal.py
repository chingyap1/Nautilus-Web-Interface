from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from mcp_gateway.models import InterlockState
from stores import InterlockStore, init_stores_db


@pytest.fixture
def store(tmp_path, monkeypatch) -> InterlockStore:
    import stores

    monkeypatch.setattr(stores, "DB_PATH", tmp_path / "interlock.db")
    asyncio.run(init_stores_db())
    return InterlockStore()


def test_fresh_resumed_lease_is_renewed(store: InterlockStore) -> None:
    resumed = asyncio.run(store.resume(actor="admin", reason="ready"))
    renewed_at = datetime.fromisoformat(resumed.updated_at) + timedelta(seconds=20)

    renewed = asyncio.run(store.renew_if_fresh(now=renewed_at))

    assert renewed is not None
    assert renewed.state == InterlockState.RESUMED
    assert renewed.updated_at == renewed_at.isoformat()


def test_expired_resumed_lease_is_not_revived(store: InterlockStore) -> None:
    resumed = asyncio.run(store.resume(actor="admin", reason="ready"))
    expired_at = datetime.fromisoformat(resumed.updated_at) + timedelta(seconds=31)

    assert asyncio.run(store.renew_if_fresh(now=expired_at)) is None
    assert asyncio.run(store.get()).updated_at == resumed.updated_at


def test_paused_and_missing_state_are_not_renewed(store: InterlockStore) -> None:
    assert asyncio.run(store.renew_if_fresh(now=datetime.now(UTC))) is None
    asyncio.run(store.engage(actor="operator", reason="stop"))

    assert asyncio.run(store.renew_if_fresh(now=datetime.now(UTC))) is None
    assert asyncio.run(store.get()).state == InterlockState.PAUSED
