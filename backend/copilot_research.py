"""S2 — allowlisted Copilot research tools (backtest / compare / Optuna).

Runs in-process against framework library seams (D9-style). Never writes
``COMMAND_DIR``, never touches exchange credentials. Bars are loaded via an
injectable loader so CI uses synthetic data (no network).
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import pandas as pd

# ---------------------------------------------------------------------------
# Budgets (fail closed)
# ---------------------------------------------------------------------------

MAX_BARS = 500
MAX_WALK_FORWARD_SPLITS = 4
MAX_OPTUNA_TRIALS = 20
MAX_COMPARE_STRATEGIES = 5
DEFAULT_TOOL_TIMEOUT_SECONDS = float(os.getenv("COPILOT_RESEARCH_TIMEOUT", "120"))
MAX_VALIDATION_STEPS = 3
ALLOWED_VALIDATION_STEPS = frozenset({"ruff", "mypy", "pytest"})
DEFAULT_VALIDATION_STEPS = ("ruff", "mypy")
VALIDATION_STEP_TIMEOUT_SECONDS = float(os.getenv("COPILOT_VALIDATION_STEP_TIMEOUT", "90"))


def _default_framework_root() -> Path:
    env = os.getenv("FRAMEWORK_ROOT")
    if env:
        return Path(env).resolve()
    # backend → nautilus-web-interface → backtest_interface → repo root
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "strategies" / "ma_cross.py").is_file():
            return parent
    return Path("/workspace").resolve()


FRAMEWORK_ROOT = _default_framework_root()

ARTIFACT_KIND_BY_TOOL = {
    "run_backtest": "experiment_result",
    "run_walk_forward": "experiment_result",
    "compare_strategies": "comparison_table",
    "optimise_params": "optuna_summary",
    "registry_status": "experiment_result",
    # S4 — propose-only; apply is a separate human-gated endpoint.
    "propose_registry_patch": "strategy_draft",
    # S5 / fuller O3 — allowlisted strategy-code draft; apply is human-gated.
    "propose_strategy_patch": "strategy_draft",
    # S6 — mid-gate validation (inplace pipeline; worktree remains CLI).
    "run_validation": "validation_report",
}

ALLOWED_TOOLS = frozenset(ARTIFACT_KIND_BY_TOOL)

RESEARCH_ARTIFACT_KINDS = frozenset(
    {"experiment_result", "comparison_table", "optuna_summary"}
)
ALL_ARTIFACT_KINDS = RESEARCH_ARTIFACT_KINDS | {
    "specification",
    "strategy_draft",
    "validation_report",
    "candidate_bundle",
}
REGISTRY_PATCH_KIND = "registry_patch"
STRATEGY_CODE_PATCH_KIND = "strategy_code_patch"
VALIDATION_REPORT_KIND = "validation_report"
CANDIDATE_BUNDLE_KIND = "candidate_bundle"

BarLoader = Callable[[str, str, int], pd.DataFrame]
_bar_loader: BarLoader | None = None
_tool_override: Callable[[str, dict[str, Any]], "ResearchResult"] | None = None


class ResearchBudgetError(Exception):
    """Raised when a tool request exceeds hard budgets."""


class ResearchToolError(Exception):
    """Raised when a research tool fails for a non-budget reason."""


@dataclass(frozen=True)
class ResearchResult:
    tool: str
    artifact_kind: str
    title: str
    content: str
    summary: str
    metrics: dict[str, Any]


def set_bar_loader(loader: BarLoader | None) -> None:
    """Inject OHLCV loader (tests). ``None`` restores Kraken download."""
    global _bar_loader
    _bar_loader = loader


def set_tool_override(
    override: Callable[[str, dict[str, Any]], ResearchResult] | None,
) -> None:
    """Replace tool execution entirely (unit tests)."""
    global _tool_override
    _tool_override = override


def tool_schemas() -> list[dict[str, Any]]:
    """OpenAI-style tool definitions for Supervisor chat (S2)."""
    return [
        {
            "type": "function",
            "function": {
                "name": "run_backtest",
                "description": "Run a single-strategy paper backtest and store metrics.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "strategy": {"type": "string"},
                        "pair": {"type": "string", "default": "XBTUSD"},
                        "interval": {"type": "string", "default": "1d"},
                        "limit": {"type": "integer", "default": 200, "maximum": MAX_BARS},
                    },
                    "required": ["strategy"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_walk_forward",
                "description": "Walk-forward OOS evaluation for a strategy.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "strategy": {"type": "string"},
                        "pair": {"type": "string", "default": "XBTUSD"},
                        "interval": {"type": "string", "default": "1d"},
                        "limit": {"type": "integer", "default": 300, "maximum": MAX_BARS},
                        "n_splits": {
                            "type": "integer",
                            "default": 4,
                            "maximum": MAX_WALK_FORWARD_SPLITS,
                        },
                    },
                    "required": ["strategy"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "compare_strategies",
                "description": "Compare multiple strategies on the same bars.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "strategies": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": MAX_COMPARE_STRATEGIES,
                        },
                        "pair": {"type": "string", "default": "XBTUSD"},
                        "interval": {"type": "string", "default": "1d"},
                        "limit": {"type": "integer", "default": 200, "maximum": MAX_BARS},
                    },
                    "required": ["strategies"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "optimise_params",
                "description": "Bounded Optuna sweep scored by walk-forward OOS Sharpe.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "strategy": {"type": "string"},
                        "pair": {"type": "string", "default": "XBTUSD"},
                        "interval": {"type": "string", "default": "1h"},
                        "limit": {"type": "integer", "default": 300, "maximum": MAX_BARS},
                        "n_trials": {
                            "type": "integer",
                            "default": 10,
                            "maximum": MAX_OPTUNA_TRIALS,
                        },
                    },
                    "required": ["strategy"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "registry_status",
                "description": "Read-only check for strategy registry drift.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "propose_registry_patch",
                "description": (
                    "Propose three-registry sync patches for review (S4). "
                    "Does not write files; operator must approve then apply."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "strategies": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional strategy module names. "
                                "Omit to plan all missing registrations."
                            ),
                        }
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "propose_strategy_patch",
                "description": (
                    "Propose a constrained strategy-code draft from an "
                    "allowlisted template (S5). Does not write files; "
                    "operator must approve then apply. Never free-form edits."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "strategy_key": {
                            "type": "string",
                            "description": "New snake_case module name, e.g. sma_momentum",
                        },
                        "template": {
                            "type": "string",
                            "default": "risk_long_only",
                            "description": "Allowlisted template id",
                        },
                        "include_test": {
                            "type": "boolean",
                            "default": True,
                        },
                    },
                    "required": ["strategy_key"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_validation",
                "description": (
                    "Run allowlisted engineering validation steps (ruff/mypy/pytest) "
                    "on the framework root and store a validation_report (S6). "
                    "Does not git push. Isolated worktrees remain CLI-only."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "steps": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Subset of ruff, mypy, pytest (default ruff,mypy)",
                        }
                    },
                },
            },
        },
    ]


def execute_tool(
    tool: str,
    params: dict[str, Any] | None = None,
    *,
    timeout_seconds: float | None = None,
) -> ResearchResult:
    """Run an allowlisted research tool under hard budgets."""
    if tool not in ALLOWED_TOOLS:
        raise ResearchToolError(f"tool not allowlisted: {tool}")
    params = dict(params or {})

    timeout = timeout_seconds if timeout_seconds is not None else DEFAULT_TOOL_TIMEOUT_SECONDS
    if timeout <= 0:
        raise ResearchBudgetError("timeout budget exhausted")

    def _run() -> ResearchResult:
        if _tool_override is not None:
            return _tool_override(tool, params)
        return _dispatch(tool, params)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run)
        try:
            result = future.result(timeout=timeout)
        except FuturesTimeout as exc:
            raise ResearchBudgetError(
                f"tool '{tool}' exceeded timeout budget ({timeout}s)"
            ) from exc
    # Ensure API/json responses never include NaN/Inf.
    return ResearchResult(
        tool=result.tool,
        artifact_kind=result.artifact_kind,
        title=result.title,
        content=result.content,
        summary=result.summary,
        metrics=_json_safe(result.metrics),
    )


def _dispatch(tool: str, params: dict[str, Any]) -> ResearchResult:
    if tool == "run_backtest":
        return _run_backtest(params)
    if tool == "run_walk_forward":
        return _run_walk_forward(params)
    if tool == "compare_strategies":
        return _compare_strategies(params)
    if tool == "optimise_params":
        return _optimise_params(params)
    if tool == "registry_status":
        return _registry_status()
    if tool == "propose_registry_patch":
        return _propose_registry_patch(params)
    if tool == "propose_strategy_patch":
        return _propose_strategy_patch(params)
    if tool == "run_validation":
        return _run_validation(params)
    raise ResearchToolError(f"unhandled tool: {tool}")


def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int, label: str) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError) as exc:
        raise ResearchToolError(f"invalid {label}: {value!r}") from exc
    if parsed < minimum:
        raise ResearchBudgetError(f"{label} must be >= {minimum}")
    if parsed > maximum:
        raise ResearchBudgetError(f"{label} exceeds budget (max {maximum})")
    return parsed


# NWI backend has a local ``strategies/`` package that can shadow the
# framework package on ``sys.path``. Load strategy modules by file path.
_STRATEGY_CLASSES: dict[str, tuple[str, str]] = {
    "ma_cross": ("MACrossStrategy", "MACrossConfig"),
    "rsi": ("RSIStrategy", "RSIConfig"),
    "breakout": ("BreakoutStrategy", "BreakoutConfig"),
    "bb_reversion": ("BBReversionStrategy", "BBReversionConfig"),
}


def _load_strategy_module(name: str) -> Any:
    import importlib.util
    import sys

    file_path = FRAMEWORK_ROOT / "strategies" / f"{name}.py"
    if not file_path.is_file():
        raise ResearchToolError(f"strategy module not found: {file_path}")
    mod_name = f"_copilot_fw_strategies_{name}"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    # Ensure framework root imports (strategies.* deps) resolve first.
    root = str(FRAMEWORK_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    spec = importlib.util.spec_from_file_location(mod_name, file_path)
    if spec is None or spec.loader is None:
        raise ResearchToolError(f"cannot load strategy module: {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def _resolve_strategy(name: str) -> tuple[str, type, type]:
    if name not in _STRATEGY_CLASSES:
        raise ResearchToolError(
            f"unknown strategy '{name}'. Known: {sorted(_STRATEGY_CLASSES)}"
        )
    strategy_name, config_name = _STRATEGY_CLASSES[name]
    module = _load_strategy_module(name)
    try:
        strategy_cls = getattr(module, strategy_name)
        config_cls = getattr(module, config_name)
    except AttributeError as exc:
        raise ResearchToolError(f"strategy '{name}' missing classes") from exc
    return name, strategy_cls, config_cls


def _load_bars(pair: str, interval: str, limit: int) -> pd.DataFrame:
    if _bar_loader is not None:
        df = _bar_loader(pair, interval, limit)
    else:
        from marketdata.kraken_data import download_ohlcv

        df = download_ohlcv(pair=pair, interval=interval, limit=limit)
    if df is None or len(df) < 30:
        raise ResearchToolError("insufficient bars for research tool")
    if len(df) > MAX_BARS:
        df = df.iloc[-MAX_BARS:]
    return df


def _json_safe(value: Any) -> Any:
    """Replace NaN/Inf so FastAPI/json responses stay compliant."""
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):  # noqa: PLR0124
            return None
        return value
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    # numpy scalars
    try:
        import numpy as np

        if isinstance(value, np.generic):
            return _json_safe(value.item())
    except ImportError:
        pass
    return value


def _json_content(payload: dict[str, Any]) -> str:
    return json.dumps(_json_safe(payload), indent=2, default=str)


def _run_backtest(params: dict[str, Any]) -> ResearchResult:
    from backtest.runner import run_backtest

    strategy = str(params.get("strategy") or "").strip()
    pair = str(params.get("pair") or "XBTUSD")
    interval = str(params.get("interval") or "1d")
    limit = _clamp_int(
        params.get("limit"), default=200, minimum=50, maximum=MAX_BARS, label="limit"
    )
    name, strategy_cls, config_cls = _resolve_strategy(strategy)
    df = _load_bars(pair, interval, limit)
    started = time.perf_counter()
    result = run_backtest(
        strategy_cls,
        config_cls,
        df,
        kraken_pair=pair,
        interval=interval,
    )
    duration = round(time.perf_counter() - started, 3)
    metrics = {
        "strategy": name,
        "pair": pair,
        "interval": interval,
        "bars": len(df),
        "total_return_pct": result.get("total_return_pct"),
        "sharpe_ratio": result.get("sharpe_ratio"),
        "sortino_ratio": result.get("sortino_ratio"),
        "max_drawdown_pct": result.get("max_drawdown_pct"),
        "profit_factor": result.get("profit_factor"),
        "win_rate_pct": result.get("win_rate_pct"),
        "num_fills": result.get("num_fills"),
        "duration_seconds": duration,
    }
    summary = (
        f"{name} on {pair}/{interval}: Sharpe={metrics['sharpe_ratio']}, "
        f"return={metrics['total_return_pct']}%, "
        f"maxDD={metrics['max_drawdown_pct']}%, fills={metrics['num_fills']}"
    )
    content = _json_content({"tool": "run_backtest", "metrics": metrics})
    return ResearchResult(
        tool="run_backtest",
        artifact_kind="experiment_result",
        title=f"Backtest {name} {pair}",
        content=content,
        summary=summary,
        metrics=metrics,
    )


def _run_walk_forward(params: dict[str, Any]) -> ResearchResult:
    from backtest.walkforward import run_walk_forward

    strategy = str(params.get("strategy") or "").strip()
    pair = str(params.get("pair") or "XBTUSD")
    interval = str(params.get("interval") or "1d")
    limit = _clamp_int(
        params.get("limit"), default=300, minimum=80, maximum=MAX_BARS, label="limit"
    )
    n_splits = _clamp_int(
        params.get("n_splits"),
        default=4,
        minimum=2,
        maximum=MAX_WALK_FORWARD_SPLITS,
        label="n_splits",
    )
    name, strategy_cls, config_cls = _resolve_strategy(strategy)
    df = _load_bars(pair, interval, limit)
    table = run_walk_forward(
        strategy_cls,
        config_cls,
        df,
        n_splits=n_splits,
        symbol=pair,
        interval=interval,
    )
    records = table.reset_index(drop=True).to_dict(orient="records")
    mean_row = next((r for r in records if str(r.get("fold")) == "MEAN"), records[-1])
    metrics = {
        "strategy": name,
        "pair": pair,
        "interval": interval,
        "n_splits": n_splits,
        "bars": len(df),
        "oos_sharpe": mean_row.get("sharpe_ratio"),
        "oos_return_pct": mean_row.get("total_return_pct"),
        "oos_max_drawdown_pct": mean_row.get("max_drawdown_pct"),
        "folds": records,
    }
    summary = (
        f"Walk-forward {name} ({n_splits} folds): "
        f"OOS Sharpe={metrics['oos_sharpe']}, "
        f"return={metrics['oos_return_pct']}%"
    )
    return ResearchResult(
        tool="run_walk_forward",
        artifact_kind="experiment_result",
        title=f"Walk-forward {name} {pair}",
        content=_json_content({"tool": "run_walk_forward", "metrics": metrics}),
        summary=summary,
        metrics=metrics,
    )


def _compare_strategies(params: dict[str, Any]) -> ResearchResult:
    from backtest.runner import compare_strategies

    raw = params.get("strategies") or []
    if isinstance(raw, str):
        names = [s.strip() for s in raw.split(",") if s.strip()]
    else:
        names = [str(s).strip() for s in raw if str(s).strip()]
    if not names:
        raise ResearchToolError("strategies list is required")
    if len(names) > MAX_COMPARE_STRATEGIES:
        raise ResearchBudgetError(
            f"strategies exceeds budget (max {MAX_COMPARE_STRATEGIES})"
        )
    pair = str(params.get("pair") or "XBTUSD")
    interval = str(params.get("interval") or "1d")
    limit = _clamp_int(
        params.get("limit"), default=200, minimum=50, maximum=MAX_BARS, label="limit"
    )
    resolved = []
    for name in names:
        label, strategy_cls, config_cls = _resolve_strategy(name)
        resolved.append((label, strategy_cls, config_cls))
    df = _load_bars(pair, interval, limit)
    table = compare_strategies(resolved, df, kraken_pair=pair, interval=interval)
    rows = table.reset_index().to_dict(orient="records")
    top = rows[0] if rows else {}
    metrics = {
        "pair": pair,
        "interval": interval,
        "bars": len(df),
        "strategies": names,
        "ranking": rows,
    }
    summary = (
        f"Compare on {pair}/{interval}: top={top.get('strategy')} "
        f"Sharpe={top.get('sharpe')} return={top.get('return_pct')}%"
    )
    return ResearchResult(
        tool="compare_strategies",
        artifact_kind="comparison_table",
        title=f"Compare {','.join(names)} {pair}",
        content=_json_content({"tool": "compare_strategies", "metrics": metrics}),
        summary=summary,
        metrics=metrics,
    )


def _optimise_params(params: dict[str, Any]) -> ResearchResult:
    """Bounded Optuna sweep. Uses a tiny search when synthetic bars are injected."""
    strategy = str(params.get("strategy") or "").strip()
    pair = str(params.get("pair") or "XBTUSD")
    interval = str(params.get("interval") or "1h")
    limit = _clamp_int(
        params.get("limit"), default=300, minimum=80, maximum=MAX_BARS, label="limit"
    )
    n_trials = _clamp_int(
        params.get("n_trials"),
        default=10,
        minimum=1,
        maximum=MAX_OPTUNA_TRIALS,
        label="n_trials",
    )
    name, strategy_cls, config_cls = _resolve_strategy(strategy)
    if name == "ml_signal":
        raise ResearchToolError("optimise_params does not support ml_signal in S2")
    df = _load_bars(pair, interval, limit)

    try:
        import optuna
        from backtest.walkforward import run_walk_forward
    except ImportError as exc:
        raise ResearchToolError("optuna is not installed") from exc

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(trial: optuna.Trial) -> float:
        if name == "ma_cross":
            fast = trial.suggest_int("fast_period", 5, 20)
            slow = trial.suggest_int("slow_period", 21, 60)
            if fast >= slow:
                raise optuna.TrialPruned()
            strategy_params = {"fast_period": fast, "slow_period": slow}
        elif name == "rsi":
            strategy_params = {
                "period": trial.suggest_int("period", 7, 21),
                "oversold": trial.suggest_int("oversold", 20, 35),
                "overbought": trial.suggest_int("overbought", 65, 80),
            }
        elif name == "breakout":
            strategy_params = {"entry_period": trial.suggest_int("entry_period", 10, 40)}
        elif name == "bb_reversion":
            strategy_params = {
                "period": trial.suggest_int("period", 10, 30),
                "std_dev": trial.suggest_float("std_dev", 1.5, 3.0),
            }
        else:
            raise ResearchToolError(f"no Optuna space for {name}")
        table = run_walk_forward(
            strategy_cls,
            config_cls,
            df,
            n_splits=min(3, MAX_WALK_FORWARD_SPLITS),
            symbol=pair,
            interval=interval,
            strategy_params=strategy_params,
            trade_size=Decimal("0.10"),
        )
        mean = table[table["fold"].astype(str) == "MEAN"]
        if mean.empty:
            return float("-inf")
        sharpe = mean.iloc[0]["sharpe_ratio"]
        return float(sharpe) if sharpe is not None else float("-inf")

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, catch=(Exception,))
    best = study.best_trial if study.best_trial else None
    metrics = {
        "strategy": name,
        "pair": pair,
        "interval": interval,
        "n_trials": n_trials,
        "bars": len(df),
        "best_value": best.value if best else None,
        "best_params": best.params if best else {},
    }
    summary = (
        f"Optuna {name}: best OOS Sharpe={metrics['best_value']} "
        f"params={metrics['best_params']}"
    )
    return ResearchResult(
        tool="optimise_params",
        artifact_kind="optuna_summary",
        title=f"Optuna {name} {pair}",
        content=_json_content({"tool": "optimise_params", "metrics": metrics}),
        summary=summary,
        metrics=metrics,
    )


def _registry_status() -> ResearchResult:
    from engineering.registry import registry_patch

    missing = registry_patch(FRAMEWORK_ROOT)
    metrics = {
        "framework_root": str(FRAMEWORK_ROOT),
        "missing": missing,
        "in_sync": not bool(missing),
    }
    summary = (
        "Strategy registries in sync"
        if metrics["in_sync"]
        else f"Registry drift: {missing}"
    )
    return ResearchResult(
        tool="registry_status",
        artifact_kind="experiment_result",
        title="Registry status",
        content=_json_content({"tool": "registry_status", "metrics": metrics}),
        summary=summary,
        metrics=metrics,
    )


def _propose_registry_patch(params: dict[str, Any]) -> ResearchResult:
    from engineering.registry import propose_registry_patch

    raw = params.get("strategies")
    names: list[str] | None = None
    if raw is not None:
        if not isinstance(raw, list):
            raise ResearchToolError("strategies must be a list of names")
        names = [str(item) for item in raw]
        if len(names) > MAX_COMPARE_STRATEGIES:
            raise ResearchBudgetError(
                f"strategies exceeds budget (max {MAX_COMPARE_STRATEGIES})"
            )
    plan = propose_registry_patch(FRAMEWORK_ROOT, strategy_names=names)
    metrics = {
        "framework_root": plan["framework_root"],
        "strategies": plan["strategies"],
        "missing": plan["missing"],
        "in_sync": plan["in_sync"],
        "apply_requires_human": True,
        "git_push": False,
    }
    if plan["strategies"]:
        summary = (
            f"Proposed registry patch for {', '.join(plan['strategies'])} "
            "(approve artifact, then Apply — no git push)"
        )
    else:
        summary = "No registry patch needed — three registries already in sync"
    return ResearchResult(
        tool="propose_registry_patch",
        artifact_kind="strategy_draft",
        title="Registry patch proposal",
        content=_json_content(plan),
        summary=summary,
        metrics=metrics,
    )


def parse_registry_patch_content(content: str) -> dict[str, Any]:
    """Validate a strategy_draft revision body as an S4 registry patch plan."""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ResearchToolError("registry patch content must be JSON") from exc
    if not isinstance(payload, dict) or payload.get("kind") != REGISTRY_PATCH_KIND:
        raise ResearchToolError(
            f"artifact is not a {REGISTRY_PATCH_KIND} plan (got kind={payload.get('kind')!r})"
            if isinstance(payload, dict)
            else "artifact is not a registry_patch plan"
        )
    strategies = payload.get("strategies")
    if strategies is not None and not isinstance(strategies, list):
        raise ResearchToolError("registry patch strategies must be a list")
    return payload


def apply_approved_registry_patch(
    content: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply an approved registry_patch plan to FRAMEWORK_ROOT (no git push)."""
    from engineering.registry import RegistryError, apply_registry_patch

    plan = parse_registry_patch_content(content)
    names = [str(n) for n in (plan.get("strategies") or [])]
    try:
        result = apply_registry_patch(
            FRAMEWORK_ROOT,
            strategy_names=names or None,
            dry_run=dry_run,
        )
    except RegistryError as exc:
        raise ResearchToolError(str(exc)) from exc
    result["framework_root"] = str(FRAMEWORK_ROOT)
    return result


def _propose_strategy_patch(params: dict[str, Any]) -> ResearchResult:
    from engineering.patch import PatchError, propose_strategy_patch

    raw_key = params.get("strategy_key")
    if raw_key is None or not str(raw_key).strip():
        raise ResearchToolError("strategy_key is required")
    template = str(params.get("template") or "risk_long_only")
    include_test = params.get("include_test", True)
    if not isinstance(include_test, bool):
        raise ResearchToolError("include_test must be a boolean")
    try:
        plan = propose_strategy_patch(
            FRAMEWORK_ROOT,
            str(raw_key),
            template=template,
            include_test=include_test,
        )
    except PatchError as exc:
        raise ResearchToolError(str(exc)) from exc

    metrics = {
        "framework_root": plan["framework_root"],
        "strategy_key": plan["strategy_key"],
        "template": plan["template"],
        "files": sorted(plan["files"].keys()),
        "writable_apply_root": plan["writable_apply_root"],
        "apply_requires_human": True,
        "git_push": False,
    }
    summary = (
        f"Proposed {plan['template']} strategy code patch for "
        f"{plan['strategy_key']} ({len(plan['files'])} files + registry). "
        "Approve artifact, then Apply — no git push."
    )
    return ResearchResult(
        tool="propose_strategy_patch",
        artifact_kind="strategy_draft",
        title=f"Strategy code patch: {plan['strategy_key']}",
        content=_json_content(plan),
        summary=summary,
        metrics=metrics,
    )


def parse_strategy_code_patch_content(content: str) -> dict[str, Any]:
    """Validate a strategy_draft revision body as an S5 strategy_code_patch."""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ResearchToolError("strategy code patch content must be JSON") from exc
    if not isinstance(payload, dict) or payload.get("kind") != STRATEGY_CODE_PATCH_KIND:
        raise ResearchToolError(
            f"artifact is not a {STRATEGY_CODE_PATCH_KIND} plan "
            f"(got kind={payload.get('kind')!r})"
            if isinstance(payload, dict)
            else "artifact is not a strategy_code_patch plan"
        )
    if not payload.get("strategy_key"):
        raise ResearchToolError("strategy_code_patch missing strategy_key")
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        raise ResearchToolError("strategy_code_patch files must be a non-empty object")
    return payload


def apply_approved_strategy_patch(
    content: str,
    *,
    dry_run: bool = False,
    also_registry: bool = True,
) -> dict[str, Any]:
    """Apply an approved strategy_code_patch to FRAMEWORK_ROOT (no git push)."""
    from engineering.patch import PatchError, apply_strategy_patch

    plan = parse_strategy_code_patch_content(content)
    try:
        result = apply_strategy_patch(
            FRAMEWORK_ROOT,
            plan,
            dry_run=dry_run,
            also_registry=also_registry,
        )
    except PatchError as exc:
        raise ResearchToolError(str(exc)) from exc
    result["framework_root"] = str(FRAMEWORK_ROOT)
    return result


def _parse_validation_steps(params: dict[str, Any]) -> list[str]:
    raw = params.get("steps")
    if raw is None:
        steps = list(DEFAULT_VALIDATION_STEPS)
    elif not isinstance(raw, list) or not raw:
        raise ResearchToolError("steps must be a non-empty list of step names")
    else:
        steps = [str(item).strip().lower() for item in raw]
    if len(steps) > MAX_VALIDATION_STEPS:
        raise ResearchBudgetError(
            f"steps exceeds budget (max {MAX_VALIDATION_STEPS})"
        )
    unknown = [s for s in steps if s not in ALLOWED_VALIDATION_STEPS]
    if unknown:
        raise ResearchToolError(
            f"validation steps not allowlisted: {unknown} "
            f"(allowed: {sorted(ALLOWED_VALIDATION_STEPS)})"
        )
    return steps


def _run_validation(params: dict[str, Any]) -> ResearchResult:
    """Run allowlisted lint/type/test steps in-place on FRAMEWORK_ROOT (S6).

    Uses ``engineering.runner.run_pipeline`` (no git worktree). Isolated
    worktree jobs remain available via ``engineering.worker.run_steps`` / CLI.
    """
    from engineering.models import ValidationStep
    from engineering.runner import CommandRunnerError, run_pipeline

    steps = _parse_validation_steps(params)
    started = time.monotonic()
    try:
        results = run_pipeline(
            cwd=FRAMEWORK_ROOT,
            steps=[ValidationStep(s) for s in steps],
            default_timeout=VALIDATION_STEP_TIMEOUT_SECONDS,
        )
    except CommandRunnerError as exc:
        raise ResearchToolError(str(exc)) from exc

    commands = [
        {
            "step": c.step.value,
            "returncode": c.returncode,
            "passed": c.passed,
            "duration_seconds": c.duration_seconds,
            "stdout_tail": (c.stdout or "")[-800:],
            "stderr_tail": (c.stderr or "")[-800:],
        }
        for c in results
    ]
    passed = bool(results) and all(c.passed for c in results)
    payload = {
        "kind": VALIDATION_REPORT_KIND,
        "framework_root": str(FRAMEWORK_ROOT),
        "mode": "inplace_pipeline",
        "steps": steps,
        "passed": passed,
        "commands": commands,
        "duration_seconds": round(time.monotonic() - started, 3),
        "git_push": False,
        "worktree": False,
    }
    metrics = {
        "passed": passed,
        "steps": steps,
        "failed_steps": [c["step"] for c in commands if not c["passed"]],
        "mode": "inplace_pipeline",
        "git_push": False,
    }
    summary = (
        f"Validation {'PASSED' if passed else 'FAILED'} "
        f"({', '.join(steps)}) on framework root — no git push"
    )
    return ResearchResult(
        tool="run_validation",
        artifact_kind=VALIDATION_REPORT_KIND,
        title="Validation report",
        content=_json_content(payload),
        summary=summary,
        metrics=metrics,
    )


def build_candidate_bundle(
    *,
    base_ref: str = "HEAD",
    include_untracked: bool = True,
) -> dict[str, Any]:
    """Build a review-only candidate bundle against FRAMEWORK_ROOT (S6)."""
    from promotion.bundler import BundleError, create_candidate_bundle

    try:
        bundle = create_candidate_bundle(
            FRAMEWORK_ROOT,
            base_ref=base_ref,
            include_untracked=include_untracked,
        )
    except BundleError as exc:
        raise ResearchToolError(str(exc)) from exc

    # Truncate huge diffs for artifact storage (full hash still covers content).
    diff = bundle.get("diff") or ""
    diff_truncated = False
    if len(diff) > 40_000:
        bundle = {**bundle, "diff": diff[:40_000] + "\n…[truncated]"}
        diff_truncated = True

    payload = {
        "kind": CANDIDATE_BUNDLE_KIND,
        "framework_root": str(FRAMEWORK_ROOT),
        "base_ref": bundle["base_ref"],
        "base_sha": bundle["base_sha"],
        "commit_sha": bundle["commit_sha"],
        "payload_hash": bundle["payload_hash"],
        "diff": bundle["diff"],
        "untracked_files": bundle.get("untracked_files") or [],
        "diff_truncated": diff_truncated,
        "git_push": False,
        "paper_deploy": False,
    }
    return payload
