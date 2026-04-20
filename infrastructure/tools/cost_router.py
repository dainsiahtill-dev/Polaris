import json
import os
import sys
from typing import Any, Dict, List

from .utils import Result, error_result, normalize_args


def _load_sniper_module():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    core_dir = os.path.join(root, "backend", "core", "polaris_loop")
    if core_dir not in sys.path:
        sys.path.insert(0, core_dir)
    try:
        from sniper_mode import resolve_cost_class, route_by_cost_model  # type: ignore
    except Exception:
        return None, None
    return resolve_cost_class, route_by_cost_model


def cost_router(args: List[str], cwd: str, timeout: int) -> Result:
    _ = cwd
    _ = timeout
    args = normalize_args(args)
    cost_model = ""
    role = "director"
    output_format = "json"
    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--cost-model", "--cost", "-c") and i + 1 < len(args):
            cost_model = args[i + 1]
            i += 2
            continue
        if token in ("--role", "-r") and i + 1 < len(args):
            role = args[i + 1]
            i += 2
            continue
        if token in ("--format", "-f") and i + 1 < len(args):
            output_format = args[i + 1].strip().lower()
            i += 2
            continue
        i += 1

    resolve_cost_class, route_by_cost_model = _load_sniper_module()
    if resolve_cost_class is None or route_by_cost_model is None:
        return error_result("cost_router", "sniper_mode backend unavailable")

    cost_class = resolve_cost_class(cost_model or None)
    strategy = route_by_cost_model(cost_class, role)
    payload: Dict[str, Any] = {
        "cost_class": strategy.cost_class,
        "strategy": strategy.name,
        "budget": strategy.budget,
        "policy": strategy.policy,
        "sources_enabled": strategy.sources_enabled,
    }

    if output_format == "text":
        stdout = (
            f"cost_class: {payload['cost_class']}\n"
            f"strategy: {payload['strategy']}\n"
            f"budget: {payload['budget']}\n"
            f"sources: {', '.join(payload['sources_enabled'])}\n"
        )
    else:
        stdout = json.dumps(payload, ensure_ascii=False, indent=2)

    return {
        "ok": True,
        "tool": "cost_router",
        "exit_code": 0,
        "stdout": stdout,
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["cost_router"],
    }
