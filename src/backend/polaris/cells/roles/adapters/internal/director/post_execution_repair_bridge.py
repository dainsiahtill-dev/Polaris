"""Post-execution deterministic repair bridge for Director adapter.

This module is the migration-time boundary between legacy language-specific
repair functions and the Director runtime repair kernel receipt model.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from polaris.cells.director.runtime.public.service import (
    DirectorRepairPostExecutionStepV1,
    ProjectDirectorRepairKernelSummaryV1,
    QueryDirectorRepairAdvisoryValidationV1,
    QueryDirectorRepairPostExecutionScheduleV1,
    project_director_repair_kernel_summary,
    query_director_repair_post_execution_schedule,
    validate_director_repair_advisory,
)

StepRunner = Callable[[Any, Path, str], list[dict[str, Any]]]


_POST_EXECUTION_REPAIR_RUNNERS: dict[str, StepRunner] = {
    "go.module_import": lambda adapter, workspace, task_id: _run_go_post_repairs(adapter, task_id=task_id),
    "rust.post_execution_convergence": lambda adapter, workspace, task_id: _run_rust_post_repairs(workspace),
    "cpp.post_execution": lambda adapter, workspace, task_id: run_cpp_post_repairs_as_tool_results(workspace),
    "java.post_execution": lambda adapter, workspace, task_id: _run_java_post_repairs(workspace),
}


def run_post_execution_language_repairs(
    adapter: Any,
    *,
    task_id: str,
    resident_agi_repair_advisory_overlay: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Run post-execution language repairs and return normalized tool results."""

    workspace = Path(str(getattr(adapter, "workspace", "") or ""))
    tool_results: list[dict[str, Any]] = []
    ordered_steps = _ordered_post_execution_steps()
    for step in ordered_steps:
        runner = _runner_for_post_execution_step(step)
        step_results = runner(adapter, workspace, task_id)
        for result in step_results:
            _annotate_bridge_step(result, step)
        tool_results.extend(step_results)
    if not tool_results:
        return [], None
    repair_kernel = dict(
        project_director_repair_kernel_summary(
            ProjectDirectorRepairKernelSummaryV1(
                stage="post_execution_language_repairs",
                tool_results=tuple(tool_results),
                artifact_quality_errors=(),
                mode="commit",
            )
        ).summary
    )
    agi_advisory_overlay = _normalize_resident_agi_repair_advisory_overlay(
        resident_agi_repair_advisory_overlay,
    )
    repair_kernel["agi_advisory"] = {
        **dict(repair_kernel.get("agi_advisory") or {}),
        **agi_advisory_overlay,
    }
    scheduler_bridge = _build_scheduler_bridge_summary(
        tool_results,
        repair_kernel=repair_kernel,
        ordered_steps=ordered_steps,
        resident_agi_repair_advisory_overlay=agi_advisory_overlay,
    )
    return tool_results, {
        "schema_version": "director.post_execution_repair_kernel.v1",
        "repair_kernel": repair_kernel,
        "scheduler_bridge": scheduler_bridge,
        "resident_agi_repair_advisory_overlay": agi_advisory_overlay,
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


def _normalize_resident_agi_repair_advisory_overlay(
    overlay: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = overlay if isinstance(overlay, dict) else {}
    advisor_notes_raw = payload.get("advisor_notes")
    raw_advisor_notes = (
        [item for item in advisor_notes_raw if isinstance(item, dict)] if isinstance(advisor_notes_raw, list) else []
    )
    advisor_notes, validation_errors = _validate_resident_agi_advisor_notes(raw_advisor_notes)
    suggested_rule_count = sum(
        len(note.get("suggested_rules") or [])
        for note in advisor_notes
        if isinstance(note.get("suggested_rules"), list)
    )

    ready = str(payload.get("status") or "").strip() == "ready"
    eligible = bool(payload.get("eligible_for_director_injection"))
    advisory_only = bool(payload.get("advisory_only", True))
    authoritative = bool(payload.get("authoritative"))
    agi_execution_authority = bool(payload.get("agi_execution_authority"))
    active = ready and eligible and advisory_only and not authoritative and not agi_execution_authority
    return {
        "schema_version": "director.post_execution_resident_agi_advisory_overlay.v1",
        "source": payload.get("source") or "resident.autonomy.public.build_resident_agi_repair_advisory_overlay",
        "status": payload.get("status") or "not_provided",
        "supported": True,
        "active": active,
        "eligible_for_director_injection": eligible,
        "authoritative": False,
        "advisory_only": True,
        "writes_allowed": False,
        "agi_execution_authority": False,
        "advisor_note_count": len(advisor_notes),
        "suggested_rule_count": suggested_rule_count,
        "advisor_notes": advisor_notes if active else [],
        "validation_error_count": len(validation_errors),
        "validation_errors": validation_errors,
        "reason": payload.get("reason") or payload.get("error") or "",
        "director_runtime_contract": payload.get("director_runtime_contract") or "director.repair_advisory_policy.v1",
        "injection_policy": "director_runtime_advisory_only_no_writes_no_registration",
    }


def _validate_resident_agi_advisor_notes(advisor_notes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    normalized_notes: list[dict[str, Any]] = []
    validation_errors: list[str] = []
    for index, note in enumerate(advisor_notes):
        suggested_rules = note.get("suggested_rules")
        result = validate_director_repair_advisory(
            QueryDirectorRepairAdvisoryValidationV1(
                advisor_source=str(note.get("advisor_source") or note.get("source") or "resident_agi"),
                message=str(note.get("message") or ""),
                confidence=float(note.get("confidence") or 0.0),
                suggested_rules=tuple(item for item in suggested_rules if isinstance(item, dict))
                if isinstance(suggested_rules, list)
                else (),
                metadata=dict(note.get("metadata") or {}),
            )
        )
        if result.ok and result.normalized_advisory is not None:
            normalized_notes.append(dict(result.normalized_advisory))
            continue
        errors = list(result.errors or ("advisory validation rejected note",))
        validation_errors.extend(f"advisor_notes[{index}]: {error}" for error in errors)
    return normalized_notes, validation_errors


def _build_scheduler_bridge_summary(
    tool_results: list[dict[str, Any]],
    *,
    repair_kernel: dict[str, Any],
    ordered_steps: tuple[DirectorRepairPostExecutionStepV1, ...],
    resident_agi_repair_advisory_overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payloads = [_result_payload(item) for item in tool_results]
    receipts = repair_kernel.get("receipts")
    receipt_payloads = receipts if isinstance(receipts, list) else []
    active_step_ids = _sorted_unique(str(payload.get("bridge_step_id") or "") for payload in payloads)
    agi_overlay = resident_agi_repair_advisory_overlay or {}
    return {
        "schema_version": "director.post_execution_scheduler_bridge.v1",
        "mode": "legacy_callback_bridge",
        "target_scheduler": "director.runtime.repair_kernel.scheduler",
        "schedule_source": "director.runtime.public.query_director_repair_post_execution_schedule",
        "runner_binding_owner": "roles.adapters",
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
        "resident_agi_advisory_active": bool(agi_overlay.get("active")),
        "resident_agi_advisory_note_count": int(agi_overlay.get("advisor_note_count") or 0),
        "resident_agi_suggested_rule_count": int(agi_overlay.get("suggested_rule_count") or 0),
    }


def _ordered_post_execution_steps() -> tuple[DirectorRepairPostExecutionStepV1, ...]:
    schedule = query_director_repair_post_execution_schedule(QueryDirectorRepairPostExecutionScheduleV1())
    ordered_steps = tuple(schedule.items)
    scheduled_step_ids = {step.step_id for step in ordered_steps}
    missing_runner_step_ids = sorted(scheduled_step_ids - set(_POST_EXECUTION_REPAIR_RUNNERS))
    if missing_runner_step_ids:
        raise RuntimeError(f"post-execution repair schedule has no runner binding: {missing_runner_step_ids}")
    extra_runner_step_ids = sorted(set(_POST_EXECUTION_REPAIR_RUNNERS) - scheduled_step_ids)
    if extra_runner_step_ids:
        raise RuntimeError(f"post-execution repair runner is not declared in runtime schedule: {extra_runner_step_ids}")
    return ordered_steps


def _runner_for_post_execution_step(step: DirectorRepairPostExecutionStepV1) -> StepRunner:
    runner = _POST_EXECUTION_REPAIR_RUNNERS.get(step.step_id)
    if runner is None:
        raise RuntimeError(f"post-execution repair schedule has no runner binding: {step.step_id}")
    return runner


def _annotate_bridge_step(tool_result: dict[str, Any], step: DirectorRepairPostExecutionStepV1) -> None:
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
