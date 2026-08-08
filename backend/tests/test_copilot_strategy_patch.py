"""S5 — Copilot strategy-code patch propose / human apply."""

from __future__ import annotations

import json

import copilot_research


def test_propose_strategy_patch_tool_returns_strategy_draft():
    result = copilot_research.execute_tool(
        "propose_strategy_patch",
        {"strategy_key": "sma_momentum", "template": "risk_long_only"},
    )
    assert result.tool == "propose_strategy_patch"
    assert result.artifact_kind == "strategy_draft"
    payload = json.loads(result.content)
    assert payload["kind"] == "strategy_code_patch"
    assert payload["strategy_key"] == "sma_momentum"
    assert payload["apply_requires_human"] is True
    assert payload["git_push"] is False
    assert "strategies/sma_momentum.py" in payload["files"]


def test_propose_endpoint_persists_draft(client):
    workspace = client.post(
        "/api/copilot/workspaces", json={"title": "S5 strategy code"}
    ).json()["workspace"]
    response = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/experiments",
        json={
            "tool": "propose_strategy_patch",
            "params": {"strategy_key": "draft_alpha"},
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["artifact"]["kind"] == "strategy_draft"
    assert "strategy" in body["summary"].lower() or body["metrics"].get("strategy_key")


def test_apply_requires_approval(client, tmp_path, monkeypatch):
    root = tmp_path / "fw"
    strategies = root / "strategies"
    strategies.mkdir(parents=True)
    (strategies / "risk.py").write_text(
        "from nautilus_trader.config import StrategyConfig\n"
        "class RiskConfig(StrategyConfig, frozen=True): pass\n"
    )
    (strategies / "ma_cross.py").write_text(
        "from nautilus_trader.trading.strategy import Strategy\n"
        "from nautilus_trader.trading.config import StrategyConfig\n"
        "class MACrossConfig(StrategyConfig): pass\n"
        "class MACrossStrategy(Strategy): pass\n"
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
    (root / "tests").mkdir()
    monkeypatch.setattr(copilot_research, "FRAMEWORK_ROOT", root)
    monkeypatch.setenv("ENGINEERING_APPLY_ROOT", str(root))

    workspace = client.post(
        "/api/copilot/workspaces", json={"title": "Apply strategy gate"}
    ).json()["workspace"]
    created = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/experiments",
        json={
            "tool": "propose_strategy_patch",
            "params": {"strategy_key": "sma_momentum"},
        },
    )
    assert created.status_code == 201, created.text
    artifact = created.json()["artifact"]
    revision = created.json()["revision"]

    blocked = client.post(f"/api/copilot/artifacts/{artifact['id']}/apply-strategy-patch")
    assert blocked.status_code == 409

    client.post(
        f"/api/copilot/artifacts/{artifact['id']}/revisions/{revision['id']}/approval",
        json={"decision": "approved", "reason": "Template RiskMixin draft looks correct."},
    )
    applied = client.post(
        f"/api/copilot/artifacts/{artifact['id']}/apply-strategy-patch",
        json={"dry_run": False, "also_registry": True},
    )
    assert applied.status_code == 200, applied.text
    result = applied.json()["result"]
    assert "strategies/sma_momentum.py" in result["written"]
    assert (root / "strategies" / "sma_momentum.py").exists()
    assert '"sma_momentum": (SmaMomentumStrategy, SmaMomentumConfig)' in (
        root / "run_backtest.py"
    ).read_text()
    assert result["git_push"] is False


def test_apply_rejects_registry_draft(client):
    workspace = client.post(
        "/api/copilot/workspaces", json={"title": "Wrong kind S5"}
    ).json()["workspace"]
    created = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/artifacts",
        json={
            "kind": "strategy_draft",
            "title": "Registry-shaped draft",
            "content": json.dumps(
                {
                    "kind": "registry_patch",
                    "strategies": [],
                    "apply_requires_human": True,
                    "git_push": False,
                }
            ),
        },
    ).json()
    artifact = created["artifact"]
    revision = created["revision"]
    client.post(
        f"/api/copilot/artifacts/{artifact['id']}/revisions/{revision['id']}/approval",
        json={"decision": "approved", "reason": "Looks fine as a registry plan."},
    )
    response = client.post(f"/api/copilot/artifacts/{artifact['id']}/apply-strategy-patch")
    assert response.status_code == 422
