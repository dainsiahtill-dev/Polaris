import json
import os
import sys
import time
from typing import Any, Dict, List

from .utils import Result, error_result, normalize_args


def _load_context_manager():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    core_dir = os.path.join(root, "backend", "core", "polaris_loop")
    if core_dir not in sys.path:
        sys.path.insert(0, core_dir)
    try:
        from context_manager import build_context_window  # type: ignore
    except Exception:
        return None
    return build_context_window


def _load_policy(path: str) -> Dict[str, Any]:
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def context_manager(args: List[str], cwd: str, timeout: int) -> Result:
    _ = timeout
    args = normalize_args(args)
    role = "director"
    query = ""
    run_id = "manual"
    mode = "director.execution"
    step = 0
    events_path = ""
    cost_model = ""
    sources: List[str] = []
    policy_path = ""
    include_prompt = False
    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--role", "-r") and i + 1 < len(args):
            role = args[i + 1]
            i += 2
            continue
        if token in ("--query", "-q") and i + 1 < len(args):
            query = args[i + 1]
            i += 2
            continue
        if token in ("--run-id", "--run") and i + 1 < len(args):
            run_id = args[i + 1]
            i += 2
            continue
        if token in ("--mode", "-m") and i + 1 < len(args):
            mode = args[i + 1]
            i += 2
            continue
        if token in ("--step", "-s") and i + 1 < len(args):
            try:
                step = int(args[i + 1])
            except Exception:
                step = 0
            i += 2
            continue
        if token in ("--events", "--events-path") and i + 1 < len(args):
            events_path = args[i + 1]
            i += 2
            continue
        if token in ("--cost-model", "--cost", "-c") and i + 1 < len(args):
            cost_model = args[i + 1]
            i += 2
            continue
        if token in ("--sources", "--sources-enabled") and i + 1 < len(args):
            sources = [s.strip() for s in args[i + 1].split(",") if s.strip()]
            i += 2
            continue
        if token in ("--policy", "-p") and i + 1 < len(args):
            policy_path = args[i + 1]
            i += 2
            continue
        if token == "--include-prompt":
            include_prompt = True
            i += 1
            continue
        i += 1

    build_context_window = _load_context_manager()
    if build_context_window is None:
        return error_result("context_manager", "context_manager backend unavailable")

    policy = _load_policy(policy_path)
    start = time.time()
    pack, effective_policy, budget, sources_enabled = build_context_window(
        cwd,
        role,
        query,
        step,
        run_id,
        mode,
        events_path=events_path,
        cost_model=cost_model or None,
        sources_enabled=sources or None,
        policy=policy,
    )

    items_summary = []
    for item in pack.items:
        preview = item.content_or_pointer or ""
        if len(preview) > 120:
            preview = preview[:120] + "...[snip]"
        items_summary.append(
            {
                "id": item.id,
                "kind": item.kind,
                "provider": item.provider,
                "priority": item.priority,
                "size_est": item.size_est,
                "reason": item.reason,
                "refs": item.refs,
                "preview": preview,
            }
        )

    payload: Dict[str, Any] = {
        "context_hash": pack.request_hash,
        "total_tokens": pack.total_tokens,
        "total_chars": pack.total_chars,
        "items_count": len(pack.items),
        "items": items_summary,
        "budget": budget.model_dump(),
        "policy": effective_policy,
        "sources_enabled": sources_enabled,
    }
    if include_prompt:
        payload["rendered_prompt"] = pack.rendered_prompt

    stdout = json.dumps(payload, ensure_ascii=False, indent=2)
    return {
        "ok": True,
        "tool": "context_manager",
        "exit_code": 0,
        "stdout": stdout,
        "stderr": "",
        "duration": time.time() - start,
        "duration_ms": int((time.time() - start) * 1000),
        "truncated": False,
        "artifacts": [],
        "command": ["context_manager"],
        "context_hash": pack.request_hash,
    }
