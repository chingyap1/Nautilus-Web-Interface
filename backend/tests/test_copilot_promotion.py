"""S3 / D13 — Copilot workspace ↔ Promotion binding and supervision ingress."""

from __future__ import annotations

import asyncio

import copilot_promotion
import copilot_store


def _create_workspace(client, **overrides):
    payload = {"title": "BTC momentum research", **overrides}
    return client.post("/api/copilot/workspaces", json=payload)


def test_create_workspace_binds_promotion(client, tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_PROMOTIONS_DIR", str(tmp_path / "promotions"))
    response = _create_workspace(client)
    assert response.status_code == 201
    workspace = response.json()["workspace"]
    assert workspace["lifecycle"] == "IDEA"
    assert workspace["promotion_id"]
    assert workspace["promotion_id"].startswith("PROM-")

    promotion = copilot_promotion.load_promotion(workspace["promotion_id"])
    assert promotion.state.value == "IDEA"
    assert promotion.description == "BTC momentum research"
    assert (tmp_path / "promotions" / f"{workspace['promotion_id']}.json").is_file()


def test_lifecycle_advance_records_promotion_approval(client, tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_PROMOTIONS_DIR", str(tmp_path / "promotions"))
    workspace = _create_workspace(client).json()["workspace"]
    promotion_id = workspace["promotion_id"]

    created = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/artifacts",
        json={
            "kind": "specification",
            "title": "Spec",
            "content": "Entry/exit/risk for review.",
        },
    ).json()
    client.post(
        f"/api/copilot/artifacts/{created['artifact']['id']}/revisions/{created['revision']['id']}/approval",
        json={"decision": "approved", "reason": "Ready for SPECIFICATION."},
    )
    advanced = client.post(f"/api/copilot/workspaces/{workspace['id']}/lifecycle/advance")
    assert advanced.status_code == 200
    updated = advanced.json()["workspace"]
    assert updated["lifecycle"] == "SPECIFICATION"
    assert updated["promotion_id"] == promotion_id

    promotion = copilot_promotion.load_promotion(promotion_id)
    assert promotion.state.value == "SPECIFICATION"
    assert len(promotion.approvals) == 1
    assert promotion.approvals[0].type.value == "specification"
    assert promotion.approvals[0].payload_hash == created["revision"]["content_hash"]

    eligibility = client.get(f"/api/copilot/workspaces/{workspace['id']}/lifecycle").json()[
        "eligibility"
    ]
    assert eligibility["promotion_id"] == promotion_id
    assert eligibility["promotion_state"] == "SPECIFICATION"
    assert not eligibility["eligible"]


def test_from_supervision_creates_bound_workspace_with_brief(client, tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_PROMOTIONS_DIR", str(tmp_path / "promotions"))
    response = client.post(
        "/api/copilot/workspaces/from-supervision",
        json={
            "pair": "xbtusd",
            "reason": "Low return after many fills; try a bounded MA experiment.",
            "strategy": "ma_cross",
            "recommendation_kind": "experiment",
            "parameters": {"fast": 12, "slow": 48},
        },
    )
    assert response.status_code == 201
    body = response.json()
    workspace = body["workspace"]
    assert workspace["promotion_id"]
    assert "XBTUSD" in workspace["title"]
    assert body["conversation"]["workspace_id"] == workspace["id"]
    assert body["artifact"]["kind"] == "specification"

    messages = client.get(
        f"/api/copilot/conversations/{body['conversation']['id']}/messages"
    ).json()["messages"]
    assert any(m["role"] == "system" and "supervision" in m["content"].lower() for m in messages)

    promotion = copilot_promotion.load_promotion(workspace["promotion_id"])
    assert promotion.metadata.get("source") == "supervision"
    assert promotion.metadata.get("pair") == "XBTUSD"


def test_from_supervision_rejects_non_experiment(client):
    response = client.post(
        "/api/copilot/workspaces/from-supervision",
        json={
            "pair": "XBTUSD",
            "reason": "Halt trading",
            "recommendation_kind": "halt",
        },
    )
    assert response.status_code == 422


def test_advance_still_atomic_with_promotion(client, tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_PROMOTIONS_DIR", str(tmp_path / "promotions"))
    workspace = _create_workspace(client).json()["workspace"]
    created = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/artifacts",
        json={"kind": "specification", "title": "Spec", "content": "Ready."},
    ).json()
    client.post(
        f"/api/copilot/artifacts/{created['artifact']['id']}/revisions/{created['revision']['id']}/approval",
        json={"decision": "approved", "reason": "Ready to advance."},
    )

    async def advance_twice():
        return await asyncio.gather(
            copilot_store.advance_lifecycle(workspace, "admin"),
            copilot_store.advance_lifecycle(workspace, "admin"),
        )

    results = asyncio.run(advance_twice())
    assert sum(result is not None for result in results) == 1
    promotion = copilot_promotion.load_promotion(workspace["promotion_id"])
    assert promotion.state.value == "SPECIFICATION"
    assert len(promotion.approvals) == 1


def test_lifecycle_eligibility_requires_artifact_even_with_promotion(client, tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_PROMOTIONS_DIR", str(tmp_path / "promotions"))
    workspace = _create_workspace(client).json()["workspace"]
    eligibility = client.get(f"/api/copilot/workspaces/{workspace['id']}/lifecycle").json()[
        "eligibility"
    ]
    assert not eligibility["eligible"]
    assert eligibility["required_artifact_kind"] == "specification"
    assert "Approve" in eligibility["reason"]
