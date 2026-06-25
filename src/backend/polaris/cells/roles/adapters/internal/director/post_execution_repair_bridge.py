"""Post-execution deterministic repair bridge for Director adapter.

This module is the migration-time boundary between legacy language-specific
repair functions and the Director runtime repair kernel receipt model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polaris.cells.director.runtime.public.service import build_director_repair_kernel_summary

StepRunner = Callable[[Any, Path, str], list[dict[str, Any]]]


@dataclass(frozen=True)
class PostExecutionRepairStep:
    """Declarative migration step for one post-execution language repair group."""

    step_id: str
    language: str
    phase: str
    priority: int
    source_tool: str
    runner: StepRunner
    depends_on: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "language": self.language,
            "phase": self.phase,
            "priority": self.priority,
            "source_tool": self.source_tool,
            "depends_on": list(self.depends_on),
        }


_POST_EXECUTION_REPAIR_STEPS: tuple[PostExecutionRepairStep, ...] = (
    PostExecutionRepairStep(
        step_id="go.module_import",
        language="go",
        phase="dependency_resolution",
        priority=0,
        source_tool="deterministic_go_module_import_repair",
        runner=lambda adapter, workspace, task_id: _run_go_post_repairs(adapter, task_id=task_id),
    ),
    PostExecutionRepairStep(
        step_id="rust.post_execution_convergence",
        language="rust",
        phase="multi_phase_convergence",
        priority=0,
        source_tool="deterministic_rust_post_repair",
        runner=lambda adapter, workspace, task_id: _run_rust_post_repairs(workspace),
    ),
    PostExecutionRepairStep(
        step_id="cpp.post_execution",
        language="cpp",
        phase="post_execution",
        priority=1,
        source_tool="deterministic_cpp_post_repair",
        runner=lambda adapter, workspace, task_id: run_cpp_post_repairs_as_tool_results(workspace),
    ),
    PostExecutionRepairStep(
        step_id="java.post_execution",
        language="java",
        phase="post_execution",
        priority=1,
        source_tool="deterministic_java_post_repair",
        runner=lambda adapter, workspace, task_id: _run_java_post_repairs(workspace),
    ),
)


def run_post_execution_language_repairs(
    adapter: Any,
    *,
    task_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Run post-execution language repairs and return normalized tool results."""

    workspace = Path(str(getattr(adapter, "workspace", "") or ""))
    tool_results: list[dict[str, Any]] = []
    ordered_steps = _ordered_post_execution_steps()
    for step in ordered_steps:
        step_results = step.runner(adapter, workspace, task_id)
        for result in step_results:
            _annotate_bridge_step(result, step)
        tool_results.extend(step_results)
    if not tool_results:
        return [], None
    repair_kernel = build_director_repair_kernel_summary(
        stage="post_execution_language_repairs",
        tool_results=tool_results,
        artifact_quality_errors=[],
        mode="commit",
    )
    return tool_results, {
        "schema_version": "director.post_execution_repair_kernel.v1",
        "repair_kernel": repair_kernel,
        "scheduler_bridge": _build_scheduler_bridge_summary(
            tool_results,
            repair_kernel=repair_kernel,
            ordered_steps=ordered_steps,
        ),
    }


def run_cpp_post_repairs_as_tool_results(workspace: str | Path) -> list[dict[str, Any]]:
    """Run C++ post repairs and normalize them as write-tool results."""

    workspace_path = Path(workspace)
    if not _looks_like_cpp_workspace(workspace_path):
        return []
    from .deterministic_repairs.cpp_repairs import run_all_cpp_post_repairs

    return [
        _record_to_tool_result(
            record,
            source_tool="deterministic_cpp_post_repair",
            default_action="cpp_post_repair",
        )
        for record in run_all_cpp_post_repairs(workspace_path)
    ]


def _run_go_post_repairs(adapter: Any, *, task_id: str) -> list[dict[str, Any]]:
    from .deterministic_repairs.generic_repairs import _apply_deterministic_go_module_import_repair

    return list(_apply_deterministic_go_module_import_repair(adapter, task_id=task_id))


def _run_rust_post_repairs(workspace: Path) -> list[dict[str, Any]]:
    if not (workspace / "Cargo.toml").is_file():
        return []
    from .deterministic_repairs.rust_repairs import run_all_rust_post_repairs

    return [_rust_record_to_tool_result(record) for record in run_all_rust_post_repairs(workspace)]


def _run_java_post_repairs(workspace: Path) -> list[dict[str, Any]]:
    if not any(workspace.rglob("*.java")):
        return []
    from .deterministic_repairs.java_repairs import run_all_java_post_repairs

    return [
        _record_to_tool_result(
            record,
            source_tool="deterministic_java_post_repair",
            default_action="java_post_repair",
        )
        for record in run_all_java_post_repairs(workspace)
    ]


def _looks_like_cpp_workspace(workspace: Path) -> bool:
    return (workspace / "CMakeLists.txt").exists() or any(workspace.rglob("*.cpp"))


def _rust_record_to_tool_result(record: dict[str, Any]) -> dict[str, Any]:
    result = _record_payload(
        record,
        source_tool=str(record.get("source_tool") or "deterministic_rust_post_repair"),
        default_action=str(record.get("symbols") or "rust_post_repair"),
    )
    result["phase"] = record.get("phase", "")
    result["priority"] = record.get("priority")
    result["round_number"] = record.get("round_number")
    result["revalidation"] = record.get("revalidation", {})
    return _write_tool_result(result)


def _record_to_tool_result(
    record: dict[str, Any],
    *,
    source_tool: str,
    default_action: str,
) -> dict[str, Any]:
    return _write_tool_result(_record_payload(record, source_tool=source_tool, default_action=default_action))


def _record_payload(record: dict[str, Any], *, source_tool: str, default_action: str) -> dict[str, Any]:
    return {
        "ok": True,
        "source_tool": source_tool,
        "file": str(record.get("file") or ""),
        "action": str(record.get("action") or default_action),
        "operation": "modify",
    }


def _write_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool": "write_file",
        "tool_name": "write_file",
        "success": True,
        "result": result,
    }


def _build_scheduler_bridge_summary(
    tool_results: list[dict[str, Any]],
    *,
    repair_kernel: dict[str, Any],
    ordered_steps: tuple[PostExecutionRepairStep, ...],
) -> dict[str, Any]:
    payloads = [_result_payload(item) for item in tool_results]
    receipts = repair_kernel.get("receipts")
    receipt_payloads = receipts if isinstance(receipts, list) else []
    active_step_ids = _sorted_unique(str(payload.get("bridge_step_id") or "") for payload in payloads)
    return {
        "schema_version": "director.post_execution_scheduler_bridge.v1",
        "mode": "legacy_callback_bridge",
        "target_scheduler": "director.runtime.repair_kernel.scheduler",
        "step_order": [step.to_dict() for step in ordered_steps],
        "active_step_ids": active_step_ids,
        "observed_max_round": _max_int(payloads, "round_number"),
        "configured_max_rounds": _max_revalidation_int(payloads, "max_rounds"),
        "tool_result_count": len(tool_results),
        "source_tools": _sorted_unique(str(payload.get("source_tool") or "") for payload in payloads),
        "phases": _count_by_payload_key(payloads, "phase", default="post_execution"),
        "priorities": _count_by_payload_key(payloads, "priority", default="1"),
        "rounds": _count_by_payload_key(payloads, "round_number", default="0"),
        "receipt_count": len(receipt_payloads),
        "receipts_with_revalidation": sum(1 for receipt in receipt_payloads if receipt.get("revalidation_evidence")),
        "authoritative": bool(repair_kernel.get("authoritative")),
    }


def _ordered_post_execution_steps() -> tuple[PostExecutionRepairStep, ...]:
    completed: set[str] = set()
    pending = list(_POST_EXECUTION_REPAIR_STEPS)
    ordered: list[PostExecutionRepairStep] = []
    while pending:
        ready = [step for step in pending if all(depends_on in completed for depends_on in step.depends_on)]
        if not ready:
            raise RuntimeError("post-execution repair step dependency cycle detected")
        ready.sort(key=lambda step: (step.priority, step.step_id))
        for step in ready:
            ordered.append(step)
            completed.add(step.step_id)
            pending.remove(step)
    return tuple(ordered)


def _annotate_bridge_step(tool_result: dict[str, Any], step: PostExecutionRepairStep) -> None:
    payload = _result_payload(tool_result)
    if not payload:
        return
    payload.setdefault("bridge_step_id", step.step_id)
    payload.setdefault("language", step.language)
    payload.setdefault("phase", step.phase)
    payload.setdefault("priority", step.priority)


def _result_payload(tool_result: dict[str, Any]) -> dict[str, Any]:
    result = tool_result.get("result")
    return result if isinstance(result, dict) else {}


def _count_by_payload_key(
    payloads: list[dict[str, Any]],
    key: str,
    *,
    default: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for payload in payloads:
        value = str(payload.get(key) if payload.get(key) is not None else default).strip() or default
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _sorted_unique(values: Any) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value or "").strip()})


def _max_int(payloads: list[dict[str, Any]], key: str) -> int:
    maximum = 0
    for payload in payloads:
        try:
            maximum = max(maximum, int(payload.get(key) or 0))
        except (TypeError, ValueError):
            continue
    return maximum


def _max_revalidation_int(payloads: list[dict[str, Any]], key: str) -> int:
    maximum = 0
    for payload in payloads:
        revalidation = payload.get("revalidation")
        if not isinstance(revalidation, dict):
            continue
        try:
            maximum = max(maximum, int(revalidation.get(key) or 0))
        except (TypeError, ValueError):
            continue
    return maximum
