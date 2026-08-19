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


def test_resume_if_unchanged_rejects_newer_engage(store: InterlockStore) -> None:
    observed = asyncio.run(store.engage(actor="operator", reason="initial"))
    asyncio.run(store.engage(actor="operator", reason="new emergency"))

    resumed = asyncio.run(
        store.resume_if_unchanged(
            actor="admin",
            reason="stale resume",
            expected_updated_at=observed.updated_at,
        )
    )

    assert resumed is None
    current = asyncio.run(store.get())
    assert current is not None
    assert current.state == InterlockState.PAUSED
    assert current.reason == "new emergency"


def test_engage_and_resume_restore_canonical_lease(store: InterlockStore) -> None:
    import aiosqlite
    import stores

    asyncio.run(store.engage(actor="operator", reason="initial"))

    async def corrupt_lease() -> None:
        async with aiosqlite.connect(stores.DB_PATH) as db:
            await db.execute("UPDATE interlock SET lease_seconds=999 WHERE id=1")
            await db.commit()

    asyncio.run(corrupt_lease())
    paused = asyncio.run(store.engage(actor="operator", reason="reset"))
    assert paused.lease_seconds == 30.0
    assert asyncio.run(store.get()).lease_seconds == 30.0

    asyncio.run(corrupt_lease())
    resumed = asyncio.run(store.resume(actor="admin", reason="reset"))
    assert resumed.lease_seconds == 30.0
    assert asyncio.run(store.get()).lease_seconds == 30.0
