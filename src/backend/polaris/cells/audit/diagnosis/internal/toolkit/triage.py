"""Triage bundle building functions.

CRITICAL: 所有文本文件 I/O 必须使用 UTF-8 编码。
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from polaris.kernelone.storage import resolve_runtime_path

from .query import query_by_run_id, query_by_task_id, query_by_trace_id

logger = logging.getLogger(__name__)


def build_triage_bundle(
    workspace: str,
    run_id: str | None = None,
    task_id: str | None = None,
    trace_id: str | None = None,
    runtime_root: Path | None = None,
) -> dict[str, Any]:
    """Build complete triage bundle.

    Args:
        workspace: 工作空间路径
        run_id: Run ID (optional)
        task_id: Task ID (optional)
        trace_id: Trace ID (optional)
        runtime_root: Runtime 根目录 (optional)

    Returns:
        Complete triage bundle dictionary
    """
    if runtime_root is None:
        runtime_root = Path(resolve_runtime_path(workspace, "runtime"))

    # Query related events
    events = []
    if run_id:
        events = query_by_run_id(str(runtime_root), run_id, limit=10000)
    elif task_id:
        events = query_by_task_id(str(runtime_root), task_id, limit=10000)
    elif trace_id:
        events = query_by_trace_id(str(runtime_root), trace_id, limit=10000)

    resolved_run_id = run_id or _infer_run_id(events)

    # Build bundle
    bundle = {
        "status": "success" if events else "not_found",
        "run_id": resolved_run_id,
        "task_id": task_id,
        "trace_id": trace_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pm_quality_history": _extract_pm_history(events),
        "leakage_findings": _extract_leakage_findings(events),
        "director_tool_audit": _extract_tool_audit(events),
        "issues_fixed": _extract_fixed_issues(events),
        "acceptance_results": _extract_acceptance(events),
        "evidence_paths": _collect_evidence_paths(events, runtime_root),
        "next_risks": _identify_risks(events),
        "failure_hops": _load_failure_hops(resolved_run_id, runtime_root),
    }

    return bundle


def _extract_pm_history(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract PM quality history."""
    history: list[dict[str, Any]] = []
    for event in events:
        source = event.get("source", {})
        action = event.get("action", {})

        if isinstance(source, dict) and source.get("role") == "pm":
            history.append(
                {
                    "timestamp": event.get("timestamp"),
                    "event_type": event.get("event_type"),
                    "action": action.get("name") if isinstance(action, dict) else "",
                    "result": action.get("result") if isinstance(action, dict) else "",
                }
            )
    return history


def _infer_run_id(events: list[dict[str, Any]]) -> str | None:
    """Infer run_id from queried events for task_id/trace_id triage."""
    for event in events:
        task = event.get("task", {})
        if not isinstance(task, dict):
            continue
        candidate = task.get("run_id")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _extract_leakage_findings(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract leakage findings (simplified implementation)."""
    findings: list[dict[str, Any]] = []
    # TODO: 从专门的服务获取
    return findings


def _extract_tool_audit(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract Director tool audit.

    支持两种事件格式：
    1. Audit 事件格式: event_type="tool_execution", resource.path, action.result
    2. Journal 事件格式: kind="action"/"output", message="tool_call:xxx"/"tool_result:xxx", raw.tool
    """
    audit: dict[str, Any] = {"tools_used": [], "errors": [], "total": 0, "failed": 0}

    for event in events:
        # 格式1: Audit 事件格式
        if event.get("event_type") == "tool_execution":
            audit["total"] = cast("int", audit["total"]) + 1
            resource = event.get("resource", {})
            action = event.get("action", {})

            tool_info: dict[str, Any] = {
                "tool": resource.get("path") if isinstance(resource, dict) else "",
                "operation": resource.get("operation") if isinstance(resource, dict) else "",
                "result": action.get("result") if isinstance(action, dict) else "",
                "timestamp": event.get("timestamp"),
            }
            cast("list[dict[str, Any]]", audit["tools_used"]).append(tool_info)

            if isinstance(action, dict) and action.get("result") == "failure":
                audit["failed"] = cast("int", audit["failed"]) + 1
                data = event.get("data", {})
                cast("list[dict[str, Any]]", audit["errors"]).append(
                    {
                        "tool": tool_info["tool"],
                        "error": data.get("error") if isinstance(data, dict) else "",
                        "timestamp": event.get("timestamp"),
                    }
                )

        # 格式2: Journal 事件格式 - tool_call
        elif event.get("kind") == "action" and "tool_call" in event.get("message", ""):
            audit["total"] = cast("int", audit["total"]) + 1
            raw = event.get("raw", {})
            tool_name = raw.get("tool", "") if isinstance(raw, dict) else ""
            tool_args = raw.get("args", {}) if isinstance(raw, dict) else {}

            tool_info_v2: dict[str, Any] = {
                "tool": tool_name,
                "operation": next(iter(tool_args.keys())) if tool_args else "",
                "result": "pending",
                "timestamp": event.get("ts"),
            }
            cast("list[dict[str, Any]]", audit["tools_used"]).append(tool_info_v2)

        # 格式2: Journal 事件格式 - tool_result
        elif event.get("kind") == "output" and "tool_result" in event.get("message", ""):
            raw = event.get("raw", {})
            if isinstance(raw, dict):
                result_data = raw.get("result", {})
                tool_name = raw.get("tool", "")
                success = result_data.get("success", True) if isinstance(result_data, dict) else True
                error_msg = result_data.get("error") if isinstance(result_data, dict) else None

                # 更新对应的 tool_info
                for tool_info in cast("list[dict[str, Any]]", audit["tools_used"]):
                    if tool_info.get("tool") == tool_name and tool_info.get("result") == "pending":
                        tool_info["result"] = "success" if success else "failure"
                        break

                if not success or error_msg:
                    audit["failed"] = cast("int", audit["failed"]) + 1
                    cast("list[dict[str, Any]]", audit["errors"]).append(
                        {
                            "tool": tool_name,
                            "error": error_msg or "unknown error",
                            "timestamp": event.get("ts"),
                        }
                    )

    return audit


def _extract_fixed_issues(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract fixed issues."""
    fixed: list[dict[str, Any]] = []
    for event in events:
        action = event.get("action", {})
        if (
            event.get("event_type") == "task_complete"
            and isinstance(action, dict)
            and action.get("result") == "success"
        ):
            task = event.get("task", {})
            fixed.append(
                {
                    "task_id": task.get("task_id") if isinstance(task, dict) else "",
                    "timestamp": event.get("timestamp"),
                    "description": event.get("data", {}).get("description")
                    if isinstance(event.get("data"), dict)
                    else "",
                }
            )
    return fixed


def _extract_acceptance(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract acceptance results."""
    results: dict[str, Any] = {"passed": 0, "failed": 0, "inconclusive": 0, "details": []}

    for event in events:
        if event.get("event_type") == "audit_verdict":
            data = event.get("data", {})
            verdict = data.get("verdict") if isinstance(data, dict) else ""

            if verdict == "PASS":
                results["passed"] = cast("int", results["passed"]) + 1
            elif verdict == "FAIL":
                results["failed"] = cast("int", results["failed"]) + 1
            else:
                results["inconclusive"] = cast("int", results["inconclusive"]) + 1

            cast("list[dict[str, Any]]", results["details"]).append(
                {
                    "timestamp": event.get("timestamp"),
                    "verdict": verdict,
                    "findings": data.get("findings", []) if isinstance(data, dict) else [],
                }
            )

    return results


def _collect_evidence_paths(events: list[dict[str, Any]], runtime_root: Path) -> dict[str, list[str]]:
    """Collect evidence paths."""
    paths: dict[str, list[str]] = {
        "trajectory": [],
        "evidence": [],
        "tool_outputs": [],
        "failure_hops": [],
    }

    for event in events:
        task = event.get("task", {})

        if isinstance(task, dict):
            if task.get("trajectory_path"):
                paths["trajectory"].append(task["trajectory_path"])
            if task.get("evidence_path"):
                paths["evidence"].append(task["evidence_path"])

    # Deduplicate and verify existence
    for key in paths:
        unique = list(set(paths[key]))
        paths[key] = [p for p in unique if Path(p).exists() or (runtime_root / p).exists()]

    # Try to find failure_hops
    run_ids = set()
    for event in events:
        task = event.get("task", {})
        if isinstance(task, dict):
            run_id = task.get("run_id")
            if run_id:
                run_ids.add(run_id)

    for run_id in run_ids:
        hops_path = runtime_root / "artifacts" / "runs" / run_id / "failure_hops.json"
        if hops_path.exists():
            paths["failure_hops"].append(str(hops_path))

    return paths


def _identify_risks(events: list[dict[str, Any]]) -> list[str]:
    """Identify next risks (simplified implementation)."""
    risks: list[str] = []

    # Check failure patterns
    failure_count = sum(1 for e in events if e.get("event_type") == "task_failed")
    if failure_count > 3:
        risks.append(f"Multiple failures detected ({failure_count})")

    # Check tool errors
    tool_errors = sum(
        1 for e in events if e.get("event_type") == "tool_execution" and e.get("action", {}).get("result") == "failure"
    )
    if tool_errors > 0:
        risks.append(f"Tool execution failures ({tool_errors})")

    return risks


def _load_failure_hops(run_id: str | None, runtime_root: Path) -> dict[str, Any] | None:
    """Load failure_hops."""
    if not run_id:
        return None

    hops_path = runtime_root / "artifacts" / "runs" / run_id / "failure_hops.json"
    if not hops_path.exists():
        return None

    try:
        with open(hops_path, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return cast("dict[str, Any]", data)
            return None
    except (RuntimeError, ValueError) as exc:
        logger.debug("Failed to load failure hops from %s: %s", hops_path, exc)
        return None
