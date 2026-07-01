"""Director task-boundary verdict projection for Role Kernel execution."""

from __future__ import annotations

from typing import Any


def clean_relative_paths(values: Any) -> list[str]:
    """Return stable repository-relative paths from a scalar or collection."""
    raw_values = values if isinstance(values, (list, tuple, set)) else [values]
    paths: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        path = str(value or "").strip().replace("\\", "/")
        if not path or path.startswith("/") or path.startswith("../") or "/../" in path:
            continue
        if "*" in path or "," in path:
            continue
        normalized = path[2:] if path.startswith("./") else path
        if normalized and normalized not in seen:
            seen.add(normalized)
            paths.append(normalized)
    return paths


def _extend_context_paths(paths: list[str], value: Any) -> None:
    for path in clean_relative_paths(value):
        if path not in paths:
            paths.append(path)


def director_task_boundary_target_files(context_override: Any) -> list[str]:
    """Extract Director target files from the current role-turn context."""
    if not isinstance(context_override, dict):
        return []
    paths: list[str] = []
    for key in ("target_files", "repair_target_files"):
        _extend_context_paths(paths, context_override.get(key))
    for key in ("director_execution_profile", "task_execution_profile", "execution_profile"):
        profile = context_override.get(key)
        if isinstance(profile, dict):
            _extend_context_paths(paths, profile.get("target_files"))
    construction_step = context_override.get("construction_step")
    if isinstance(construction_step, dict):
        _extend_context_paths(paths, construction_step.get("target_file"))
        _extend_context_paths(paths, construction_step.get("target_files"))
    for key in ("task", "current_task", "pm_task_contract"):
        task = context_override.get(key)
        if isinstance(task, dict):
            _extend_context_paths(paths, task.get("target_files"))
    return paths


def completed_artifacts_from_tool_results(tool_results: list[dict[str, Any]]) -> list[str]:
    """Project changed artifact paths from successful tool results."""
    artifacts: list[str] = []
    for item in tool_results:
        if not isinstance(item, dict) or item.get("success") is False:
            continue
        candidates: list[Any] = [item]
        for key in ("result", "effect_receipt", "raw_result"):
            value = item.get(key)
            if isinstance(value, dict):
                candidates.append(value)
        for candidate in candidates:
            for key in ("file", "path", "target_file"):
                _extend_context_paths(artifacts, candidate.get(key))
            for key in ("files_changed", "changed_files"):
                _extend_context_paths(artifacts, candidate.get(key))
    return artifacts


def build_director_task_boundary_verdict(
    *,
    role: str,
    workspace: str,
    task_id: str,
    run_id: str,
    context_override: Any,
    tool_results: list[dict[str, Any]],
    tool_dispatch: dict[str, Any] | None = None,
    evidence_refs: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    """Build a control-plane task-boundary verdict for Director turns."""
    if str(role or "").strip().lower() != "director":
        return None
    target_files = director_task_boundary_target_files(context_override)
    completed_artifacts = completed_artifacts_from_tool_results(tool_results)
    dispatch = dict(tool_dispatch or {})
    if not target_files and not completed_artifacts and not bool(dispatch.get("dropped")):
        return None
    from polaris.cells.control_plane.run_ledger.public import evaluate_task_boundary_verdict

    return evaluate_task_boundary_verdict(
        workspace=workspace,
        task_id=task_id,
        run_id=run_id,
        target_files=target_files,
        completed_artifacts=completed_artifacts,
        tool_dispatch=dispatch,
        evidence_refs=evidence_refs,
    ).to_dict()


def _append_task_boundary_verdict_event(
    *,
    workspace: str,
    task_id: str,
    run_id: str,
    verdict: dict[str, Any],
) -> None:
    """Append a task-boundary verdict event using the canonical Run Ledger shape."""
    from polaris.cells.control_plane.run_ledger.public import AppendRunLedgerEventCommandV1, append_run_ledger_event

    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=workspace,
            run_id=run_id,
            event={
                "event_type": "task_boundary_verdict",
                "stage": "task_boundary",
                "task_id": task_id,
                "run_id": run_id,
                "task_boundary_verdict": verdict,
                "job_token": {
                    "run_id": run_id,
                    "task_id": task_id,
                    "project_id": task_id or "unknown",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
            },
        )
    )


def append_director_task_boundary_verdict(
    *,
    role: str,
    workspace: str,
    task_id: str,
    run_id: str,
    context_override: Any,
    tool_results: list[dict[str, Any]],
    tool_dispatch: dict[str, Any] | None = None,
    evidence_refs: list[str] | tuple[str, ...] | None = None,
) -> None:
    """Append a Director task-boundary verdict to the Run Ledger when applicable."""
    verdict = build_director_task_boundary_verdict(
        role=role,
        workspace=workspace,
        task_id=task_id,
        run_id=run_id,
        context_override=context_override,
        tool_results=tool_results,
        tool_dispatch=tool_dispatch,
        evidence_refs=evidence_refs,
    )
    if verdict is None:
        return
    _append_task_boundary_verdict_event(
        workspace=workspace,
        task_id=task_id,
        run_id=run_id,
        verdict=verdict,
    )


def append_deferred_followup_task_boundary_verdict(
    *,
    workspace: str,
    task_id: str,
    run_id: str,
    reason: str,
    evidence_refs: list[str] | tuple[str, ...] | None = None,
) -> None:
    """Append the task-boundary verdict for turns deferred to a governed follow-up."""
    from polaris.cells.control_plane.run_ledger.public import build_deferred_followup_task_boundary_verdict

    verdict = build_deferred_followup_task_boundary_verdict(
        task_id=task_id,
        run_id=run_id,
        reason=reason,
        evidence_refs=evidence_refs,
    ).to_dict()
    _append_task_boundary_verdict_event(
        workspace=workspace,
        task_id=task_id,
        run_id=run_id,
        verdict=verdict,
    )
