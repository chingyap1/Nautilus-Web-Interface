"""Lifecycle gates must approve and record the same artifact revision (D13)."""

import asyncio
import json

import aiosqlite
import copilot_promotion
import copilot_store
import database
import pytest
from promotion.models import PromotionState


@pytest.fixture
def approval_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "approval-binding.db")
    monkeypatch.setenv("COPILOT_PROMOTIONS_DIR", str(tmp_path / "promotions"))
    asyncio.run(database.init_db())
    return asyncio.run(copilot_store.create_workspace("admin", "Approval binding"))


GATES = [
    ("IDEA", "specification"),
    ("SPECIFICATION", "strategy_draft"),
    ("DRAFT", "validation_report"),
    ("VALIDATING", "candidate_bundle"),
]


async def _set_gate(workspace, state):
    promotion = copilot_promotion.load_promotion(workspace["promotion_id"])
    promotion.state = PromotionState(state)
    promotion.candidate_bundle = {"payload_hash": "bundle-hash"}
    copilot_promotion.get_store().save(promotion)
    async with aiosqlite.connect(database.DB_PATH) as db:
        await db.execute(
            "UPDATE copilot_workspaces SET lifecycle=? WHERE id=?", (state, workspace["id"])
        )
        await db.commit()
    return {**workspace, "lifecycle": state}


async def _artifact(workspace, kind, label, *, passed=True):
    return await copilot_store.create_artifact(
        workspace["id"], kind, label,
        json.dumps({"kind": kind, "label": label, "passed": passed, "payload_hash": "bundle-hash"}),
        "admin",
    )


async def _decide(revision, decision):
    return await copilot_store.decide_revision(revision["id"], decision, "Reviewed", "admin")


@pytest.mark.parametrize("state,kind", GATES)
@pytest.mark.parametrize("operation", ["eligibility", "advance"])
def test_older_artifact_approval_cannot_authorize_newer_artifact(
    approval_workspace, state, kind, operation
):
    async def scenario():
        workspace = await _set_gate(approval_workspace, state)
        _, old = await _artifact(workspace, kind, "old approved")
        await _decide(old, "approved")
        await _artifact(workspace, kind, "new unapproved")
        if operation == "eligibility":
            assert not (await copilot_store.transition_eligibility(workspace))["eligible"]
        else:
            assert await copilot_store.advance_lifecycle(workspace, "admin") is None
            promotion = copilot_promotion.load_promotion(workspace["promotion_id"])
            assert promotion.state.value == state
            assert promotion.approvals == []

    asyncio.run(scenario())


@pytest.mark.parametrize("state,kind", GATES)
def test_approved_selected_revision_supplies_recorded_hash(approval_workspace, state, kind):
    async def scenario():
        workspace = await _set_gate(approval_workspace, state)
        _, old = await _artifact(workspace, kind, "old")
        _, selected = await _artifact(workspace, kind, "selected")
        await _decide(selected, "approved")
        # A later decision on a different artifact cannot change this gate.
        await _decide(old, "rejected")
        assert (await copilot_store.transition_eligibility(workspace))["eligible"]
        assert await copilot_store.advance_lifecycle(workspace, "admin") is not None
        promotion = copilot_promotion.load_promotion(workspace["promotion_id"])
        assert promotion.approvals[-1].payload_hash == selected["content_hash"]

    asyncio.run(scenario())


def test_new_revision_requires_its_own_approval(approval_workspace):
    async def scenario():
        artifact, old = await _artifact(approval_workspace, "specification", "original")
        await _decide(old, "approved")
        await copilot_store.create_revision(artifact, "Changed hypothesis", "admin")
        assert not (await copilot_store.transition_eligibility(approval_workspace))["eligible"]
        assert await copilot_store.advance_lifecycle(approval_workspace, "admin") is None

    asyncio.run(scenario())


def test_latest_decision_on_selected_revision_wins(approval_workspace, monkeypatch):
    # Exercise deterministic row ordering when timestamps have identical precision.
    monkeypatch.setattr(copilot_store, "_now", lambda: "2026-09-28T00:00:00+00:00")

    async def scenario():
        _, revision = await _artifact(approval_workspace, "specification", "selected")
        await _decide(revision, "approved")
        await _decide(revision, "rejected")
        assert not (await copilot_store.transition_eligibility(approval_workspace))["eligible"]
        assert await copilot_store.advance_lifecycle(approval_workspace, "admin") is None
        await _decide(revision, "approved")
        assert await copilot_store.advance_lifecycle(approval_workspace, "admin") is not None

    asyncio.run(scenario())


def test_tied_artifact_timestamps_select_last_created(approval_workspace, monkeypatch):
    monkeypatch.setattr(copilot_store, "_now", lambda: "2026-09-28T00:00:00+00:00")

    async def scenario():
        _, old = await _artifact(approval_workspace, "specification", "old")
        await _decide(old, "approved")
        _, selected = await _artifact(approval_workspace, "specification", "selected")
        assert await copilot_store.advance_lifecycle(approval_workspace, "admin") is None
        await _decide(selected, "approved")
        assert await copilot_store.advance_lifecycle(approval_workspace, "admin") is not None
        promotion = copilot_promotion.load_promotion(approval_workspace["promotion_id"])
        assert promotion.approvals[-1].payload_hash == selected["content_hash"]

    asyncio.run(scenario())


def test_approved_failing_validation_cannot_advance(approval_workspace):
    async def scenario():
        workspace = await _set_gate(approval_workspace, "DRAFT")
        _, old = await _artifact(workspace, "validation_report", "passed")
        await _decide(old, "approved")
        _, selected = await _artifact(workspace, "validation_report", "failed", passed=False)
        await _decide(selected, "approved")
        assert not (await copilot_store.transition_eligibility(workspace))["eligible"]
        assert await copilot_store.advance_lifecycle(workspace, "admin") is None

    asyncio.run(scenario())


def test_concurrent_advance_records_one_exact_approval(approval_workspace):
    async def scenario():
        _, selected = await _artifact(approval_workspace, "specification", "selected")
        await _decide(selected, "approved")
        results = await asyncio.gather(
            copilot_store.advance_lifecycle(approval_workspace, "admin"),
            copilot_store.advance_lifecycle(approval_workspace, "admin"),
        )
        assert sum(result is not None for result in results) == 1
        promotion = copilot_promotion.load_promotion(approval_workspace["promotion_id"])
        assert len(promotion.approvals) == 1
        assert promotion.approvals[0].payload_hash == selected["content_hash"]

    asyncio.run(scenario())
