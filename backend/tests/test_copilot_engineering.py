"""S4 — Copilot registry patch propose / human apply."""

from __future__ import annotations

import json

import copilot_research


def test_propose_registry_patch_tool_returns_strategy_draft():
    result = copilot_research.execute_tool("propose_registry_patch", {})
    assert result.tool == "propose_registry_patch"
    assert result.artifact_kind == "strategy_draft"
    payload = json.loads(result.content)
    assert payload["kind"] == "registry_patch"
    assert payload["apply_requires_human"] is True
    assert payload["git_push"] is False


def test_propose_endpoint_persists_draft(client):
    workspace = client.post(
        "/api/copilot/workspaces", json={"title": "S4 registry"}
    ).json()["workspace"]
    response = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/experiments",
        json={"tool": "propose_registry_patch", "params": {}},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["artifact"]["kind"] == "strategy_draft"
    assert "registry" in body["summary"].lower() or body["metrics"].get("in_sync") is not None


def test_apply_requires_approval(client, tmp_path, monkeypatch):
    root = tmp_path / "fw"
    strategies = root / "strategies"
    strategies.mkdir(parents=True)
    (strategies / "gap.py").write_text(
        "from nautilus_trader.trading.strategy import Strategy\n"
        "from nautilus_trader.trading.config import StrategyConfig\n"
        "class GapConfig(StrategyConfig): pass\n"
        "class GapStrategy(Strategy): pass\n"
    )
    (strategies / "__init__.py").write_text(
        '"""pkg"""\n\nfrom strategies.ma_cross import MACrossConfig, MACrossStrategy\n\n'
        '__all__ = [\n    "MACrossConfig",\n    "MACrossStrategy",\n]\n'
    )
    (root / "run_backtest.py").write_text(
        "from strategies import (\n    MACrossConfig,\n    MACrossStrategy,\n)\n\n"
        'STRATEGIES = {\n    "ma_cross": (MACrossStrategy, MACrossConfig),\n}\n'
    )
    (root / "live").mkdir()
    (root / "live" / "kraken_node.py").write_text(
        "from strategies import (\n    MACrossConfig,\n    MACrossStrategy,\n)\n\n"
        'STRATEGIES = {\n    "ma_cross": (MACrossStrategy, MACrossConfig),\n}\n'
    )
    monkeypatch.setattr(copilot_research, "FRAMEWORK_ROOT", root)

    workspace = client.post(
        "/api/copilot/workspaces", json={"title": "Apply gate"}
    ).json()["workspace"]
    created = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/experiments",
        json={"tool": "propose_registry_patch", "params": {"strategies": ["gap"]}},
    )
    assert created.status_code == 201, created.text
    artifact = created.json()["artifact"]
    revision = created.json()["revision"]

    blocked = client.post(f"/api/copilot/artifacts/{artifact['id']}/apply-registry-patch")
    assert blocked.status_code == 409

    client.post(
        f"/api/copilot/artifacts/{artifact['id']}/revisions/{revision['id']}/approval",
        json={"decision": "approved", "reason": "Three-registry sync looks correct."},
    )
    applied = client.post(
        f"/api/copilot/artifacts/{artifact['id']}/apply-registry-patch",
        json={"dry_run": False},
    )
    assert applied.status_code == 200, applied.text
    result = applied.json()["result"]
    assert "strategies/__init__.py" in result["applied"]
    assert '"gap": (GapStrategy, GapConfig)' in (root / "run_backtest.py").read_text()
    assert result["git_push"] is False


def test_apply_rejects_non_registry_draft(client):
    workspace = client.post(
        "/api/copilot/workspaces", json={"title": "Wrong kind"}
    ).json()["workspace"]
    created = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/artifacts",
        json={
            "kind": "strategy_draft",
            "title": "Plain draft",
            "content": "not a registry patch json",
        },
    ).json()
    artifact = created["artifact"]
    revision = created["revision"]
    client.post(
        f"/api/copilot/artifacts/{artifact['id']}/revisions/{revision['id']}/approval",
        json={"decision": "approved", "reason": "Looks fine as a narrative draft."},
    )
    response = client.post(f"/api/copilot/artifacts/{artifact['id']}/apply-registry-patch")
    assert response.status_code == 422
