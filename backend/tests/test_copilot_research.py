"""S2 Copilot research tools — budgets, synthetic bars, artifact persistence."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
import copilot_research
from copilot_research import ResearchBudgetError, execute_tool, set_bar_loader, set_tool_override


def _synthetic_bars(pair: str, interval: str, limit: int) -> pd.DataFrame:
    n = max(limit, 80)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    ramp = np.concatenate([np.linspace(100, 200, n // 2), np.linspace(200, 100, n - n // 2)])
    noise = np.sin(np.linspace(0, 30, n)) * 2
    close = ramp + noise
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + 1.0
    low = np.minimum(open_, close) - 1.0
    volume = np.full(n, 10.0)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    ).iloc[-limit:]


@pytest.fixture(autouse=True)
def _synthetic_research():
    set_bar_loader(_synthetic_bars)
    set_tool_override(None)
    yield
    set_bar_loader(None)
    set_tool_override(None)


def test_run_backtest_returns_experiment_metrics():
    result = execute_tool(
        "run_backtest",
        {"strategy": "ma_cross", "pair": "XBTUSD", "interval": "1h", "limit": 120},
    )
    assert result.artifact_kind == "experiment_result"
    assert result.tool == "run_backtest"
    assert "sharpe_ratio" in result.metrics
    payload = json.loads(result.content)
    assert payload["metrics"]["strategy"] == "ma_cross"


def test_compare_strategies_ranks_rows():
    result = execute_tool(
        "compare_strategies",
        {
            "strategies": ["ma_cross", "rsi"],
            "pair": "XBTUSD",
            "interval": "1h",
            "limit": 120,
        },
    )
    assert result.artifact_kind == "comparison_table"
    ranking = result.metrics["ranking"]
    assert len(ranking) == 2
    assert "sharpe" in ranking[0]


def test_budget_exceeded_limit_raises():
    with pytest.raises(ResearchBudgetError, match="exceeds budget"):
        execute_tool(
            "run_backtest",
            {"strategy": "ma_cross", "limit": copilot_research.MAX_BARS + 1},
        )


def test_budget_timeout_raises_no_result():
    def slow(tool, params):
        import time

        time.sleep(2)
        raise AssertionError("should not finish")

    set_tool_override(slow)
    with pytest.raises(ResearchBudgetError, match="timeout"):
        execute_tool("run_backtest", {"strategy": "ma_cross"}, timeout_seconds=0.1)


def test_experiment_endpoint_persists_artifact(client):
    workspace = client.post(
        "/api/copilot/workspaces", json={"title": "S2 research"}
    ).json()["workspace"]
    response = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/experiments",
        json={
            "tool": "run_backtest",
            "params": {
                "strategy": "ma_cross",
                "pair": "XBTUSD",
                "interval": "1h",
                "limit": 100,
            },
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["tool"] == "run_backtest"
    assert body["artifact"]["kind"] == "experiment_result"
    assert "Sharpe" in body["summary"] or "sharpe" in body["summary"].lower()

    listed = client.get(f"/api/copilot/workspaces/{workspace['id']}/artifacts").json()
    assert any(a["kind"] == "experiment_result" for a in listed["artifacts"])


def test_experiment_budget_failure_creates_no_artifact(client):
    workspace = client.post(
        "/api/copilot/workspaces", json={"title": "Budget fail"}
    ).json()["workspace"]
    response = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/experiments",
        json={"tool": "run_backtest", "params": {"strategy": "ma_cross", "limit": 9999}},
    )
    assert response.status_code == 422
    listed = client.get(f"/api/copilot/workspaces/{workspace['id']}/artifacts").json()
    assert listed["artifacts"] == []


def test_import_optuna_summary(client):
    workspace = client.post(
        "/api/copilot/workspaces", json={"title": "Import"}
    ).json()["workspace"]
    content = json.dumps(
        {
            "tool": "optimise_params",
            "metrics": {"best_value": 1.2, "best_params": {"fast_period": 8}},
        }
    )
    response = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/artifacts/import",
        json={
            "kind": "optuna_summary",
            "title": "CLI Optuna BTC",
            "content": content,
        },
    )
    assert response.status_code == 201
    assert response.json()["artifact"]["kind"] == "optuna_summary"


def test_registry_status_tool():
    result = execute_tool("registry_status", {})
    assert result.tool == "registry_status"
    assert "in_sync" in result.metrics
