"""S6 — Copilot mid-gates DRAFT → VALIDATING → CANDIDATE."""

from __future__ import annotations

import json

import copilot_research


def _advance_to_draft(client):
    workspace = client.post(
        "/api/copilot/workspaces", json={"title": "S6 mid-gates"}
    ).json()["workspace"]
    # specification → IDEA→SPECIFICATION
    spec = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/artifacts",
        json={
            "kind": "specification",
            "title": "Spec",
            "content": "Hypothesis and risk constraints for mid-gate test.",
        },
    ).json()
    client.post(
        f"/api/copilot/artifacts/{spec['artifact']['id']}/revisions/"
        f"{spec['revision']['id']}/approval",
        json={"decision": "approved", "reason": "Spec looks complete enough."},
    )
    client.post(f"/api/copilot/workspaces/{workspace['id']}/lifecycle/advance")
    # strategy_draft → SPECIFICATION→DRAFT
    draft = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/artifacts",
        json={
            "kind": "strategy_draft",
            "title": "Draft",
            "content": "Template draft narrative for validation mid-gates.",
        },
    ).json()
    client.post(
        f"/api/copilot/artifacts/{draft['artifact']['id']}/revisions/"
        f"{draft['revision']['id']}/approval",
        json={"decision": "approved", "reason": "Draft ready for validation."},
    )
    advanced = client.post(f"/api/copilot/workspaces/{workspace['id']}/lifecycle/advance")
    assert advanced.status_code == 200, advanced.text
    assert advanced.json()["workspace"]["lifecycle"] == "DRAFT"
    return advanced.json()["workspace"]


def test_run_validation_tool_returns_report():
    result = copilot_research.execute_tool("run_validation", {"steps": ["ruff"]})
    assert result.tool == "run_validation"
    assert result.artifact_kind == "validation_report"
    payload = json.loads(result.content)
    assert payload["kind"] == "validation_report"
    assert payload["git_push"] is False
    assert payload["worktree"] is False
    assert "ruff" in payload["steps"]


def test_draft_to_candidate_happy_path(client):
    workspace = _advance_to_draft(client)

    # Not eligible until validation_report approved + passed.
    life = client.get(f"/api/copilot/workspaces/{workspace['id']}/lifecycle").json()
    assert life["eligibility"]["required_artifact_kind"] == "validation_report"
    assert life["eligibility"]["eligible"] is False

    created = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/experiments",
        json={"tool": "run_validation", "params": {"steps": ["ruff"]}},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["artifact"]["kind"] == "validation_report"
    assert body["metrics"]["passed"] is True

    client.post(
        f"/api/copilot/artifacts/{body['artifact']['id']}/revisions/"
        f"{body['revision']['id']}/approval",
        json={"decision": "approved", "reason": "Ruff validation passed."},
    )
    to_validating = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/lifecycle/advance"
    )
    assert to_validating.status_code == 200, to_validating.text
    assert to_validating.json()["workspace"]["lifecycle"] == "VALIDATING"

    bundled = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/bundle",
        json={"base_ref": "HEAD", "include_untracked": True},
    )
    assert bundled.status_code == 200, bundled.text
    bundle_body = bundled.json()
    assert bundle_body["artifact"]["kind"] == "candidate_bundle"
    assert bundle_body["bundle"]["payload_hash"]
    assert bundle_body["bundle"]["git_push"] is False
    assert bundle_body["bundle"]["paper_deploy"] is False

    client.post(
        f"/api/copilot/artifacts/{bundle_body['artifact']['id']}/revisions/"
        f"{bundle_body['revision']['id']}/approval",
        json={"decision": "approved", "reason": "Bundle hash reviewed."},
    )
    to_candidate = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/lifecycle/advance"
    )
    assert to_candidate.status_code == 200, to_candidate.text
    assert to_candidate.json()["workspace"]["lifecycle"] == "CANDIDATE"

    # Paper deploy not available from Copilot.
    life = client.get(
        f"/api/copilot/workspaces/{to_candidate.json()['workspace']['id']}/lifecycle"
    ).json()
    assert life["eligibility"]["eligible"] is False
    assert life["eligibility"]["target"] is None


def test_failed_validation_blocks_advance(client, monkeypatch):
    workspace = _advance_to_draft(client)

    def _fail(_tool: str, _params: dict):
        payload = {
            "kind": "validation_report",
            "passed": False,
            "steps": ["ruff"],
            "commands": [{"step": "ruff", "passed": False, "returncode": 1}],
            "git_push": False,
            "worktree": False,
        }
        return copilot_research.ResearchResult(
            tool="run_validation",
            artifact_kind="validation_report",
            title="Validation report",
            content=json.dumps(payload),
            summary="Validation FAILED",
            metrics={"passed": False, "steps": ["ruff"]},
        )

    monkeypatch.setattr(copilot_research, "_tool_override", _fail)
    created = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/experiments",
        json={"tool": "run_validation", "params": {}},
    )
    assert created.status_code == 201
    art = created.json()["artifact"]
    rev = created.json()["revision"]
    client.post(
        f"/api/copilot/artifacts/{art['id']}/revisions/{rev['id']}/approval",
        json={"decision": "approved", "reason": "Approving a failing report."},
    )
    life = client.get(f"/api/copilot/workspaces/{workspace['id']}/lifecycle").json()
    assert life["eligibility"]["eligible"] is False
    assert "did not pass" in life["eligibility"]["reason"].lower()
    blocked = client.post(f"/api/copilot/workspaces/{workspace['id']}/lifecycle/advance")
    assert blocked.status_code == 409


def test_reject_lifecycle(client):
    workspace = _advance_to_draft(client)
    rejected = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/lifecycle/reject",
        json={"reason": "Idea is not worth validating further."},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["workspace"]["lifecycle"] == "REJECTED"
