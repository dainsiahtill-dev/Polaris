"""Director task-boundary verdict projection for Role Kernel execution."""

from __future__ import annotations

from typing import Any

_DOWNSTREAM_ARTIFACT_PATH_KEYS = (
    "downstream_pending_artifacts",
    "downstream_pending_target_files",
    "downstream_target_files",
    "pending_artifacts",
    "pending_target_files",
    "project_declared_target_files",
    "project_declared_source_targets",
    "project_declared_entrypoint_targets",
    "declared_project_target_files",
    "all_task_target_files",
)

_TASK_COLLECTION_KEYS = (
    "tasks",
    "plan_tasks",
    "project_tasks",
    "pm_tasks",
    "task_contracts",
)


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


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _extend_mapping_path_fields(
    paths: list[str],
    value: Any,
    keys: tuple[str, ...],
) -> None:
    if not isinstance(value, dict):
        return
    for key in keys:
        _extend_context_paths(paths, value.get(key))


def _extend_task_collection_target_paths(paths: list[str], value: Any) -> None:
    if not isinstance(value, (list, tuple, set)):
        return
    for item in value:
        if not isinstance(item, dict):
            continue
        _extend_context_paths(paths, item.get("target_files"))
        metadata = _mapping(item.get("metadata"))
        _extend_context_paths(paths, metadata.get("target_files"))


def _string_items(value: Any) -> list[str]:
    raw_values = value if isinstance(value, (list, tuple, set)) else [value]
    items: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        item = str(raw_value or "").strip()
        if item and item not in seen:
            items.append(item)
            seen.add(item)
    return items


def _extend_context_items(items: list[str], value: Any) -> None:
    for item in _string_items(value):
        if item not in items:
            items.append(item)


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


def director_task_boundary_downstream_pending_artifacts(context_override: Any) -> list[str]:
    """Extract declared project/downstream artifact paths from role context.

    These paths are not current-task write authority. They only tell the
    TaskBoundary gate that a missing manifest/script entrypoint has a declared
    downstream owner instead of being an incomplete current task.
    """

    if not isinstance(context_override, dict):
        return []
    paths: list[str] = []
    mappings: list[dict[str, Any]] = [context_override]
    for key in (
        "context",
        "task_boundary",
        "director_execution_profile",
        "task_execution_profile",
        "execution_profile",
        "run_ledger",
        "run_ledger_projection",
        "ledger_projection",
        "task",
        "current_task",
        "pm_task",
        "pm_task_contract",
    ):
        nested = _mapping(context_override.get(key))
        if nested:
            mappings.append(nested)
    for mapping in mappings:
        _extend_mapping_path_fields(paths, mapping, _DOWNSTREAM_ARTIFACT_PATH_KEYS)
        metadata = _mapping(mapping.get("metadata"))
        _extend_mapping_path_fields(paths, metadata, _DOWNSTREAM_ARTIFACT_PATH_KEYS)
        for collection_key in _TASK_COLLECTION_KEYS:
            _extend_task_collection_target_paths(paths, mapping.get(collection_key))
    return paths


def director_task_boundary_blocked_dependencies(context_override: Any) -> list[str]:
    """Extract explicit blocked dependency ids from the current context.

    Plain ``depends_on`` values are not treated as blockers. The caller must
    provide a blocked/unresolved dependency projection to prevent false blocks
    for normal task graphs.
    """

    if not isinstance(context_override, dict):
        return []
    dependencies: list[str] = []
    for key in ("blocked_dependencies", "unresolved_dependencies"):
        _extend_context_items(dependencies, context_override.get(key))
    task_boundary = _mapping(context_override.get("task_boundary"))
    for key in ("blocked_dependencies", "unresolved_dependencies"):
        _extend_context_items(dependencies, task_boundary.get(key))
    return dependencies


def director_task_boundary_evidence_policy(context_override: Any) -> dict[str, Any]:
    """Extract the current evidence policy projection from role context."""

    if not isinstance(context_override, dict):
        return {}
    direct_policy = _mapping(context_override.get("evidence_policy"))
    if direct_policy:
        return direct_policy
    for key in ("run_ledger", "run_ledger_projection", "ledger_projection"):
        nested = _mapping(context_override.get(key))
        policy = _mapping(nested.get("evidence_policy"))
        if policy:
            return policy
    return {}


def director_task_boundary_verifier_policy(context_override: Any) -> dict[str, list[str]]:
    """Extract required/completed/failed verifier names from role context."""

    if not isinstance(context_override, dict):
        return {"required": [], "completed": [], "missing": [], "failed": []}
    verifier_policy = _mapping(context_override.get("verifier_policy"))
    task_boundary = _mapping(context_override.get("task_boundary"))
    return {
        "required": [
            *_string_items(context_override.get("required_verifiers")),
            *_string_items(verifier_policy.get("required_verifiers")),
            *_string_items(task_boundary.get("required_verifiers")),
        ],
        "completed": [
            *_string_items(context_override.get("completed_verifiers")),
            *_string_items(verifier_policy.get("completed_verifiers")),
            *_string_items(task_boundary.get("completed_verifiers")),
        ],
        "missing": [
            *_string_items(context_override.get("missing_required_verifiers")),
            *_string_items(verifier_policy.get("missing_required_verifiers")),
            *_string_items(task_boundary.get("missing_required_verifiers")),
        ],
        "failed": [
            *_string_items(context_override.get("failed_required_verifiers")),
            *_string_items(verifier_policy.get("failed_required_verifiers")),
            *_string_items(task_boundary.get("failed_required_verifiers")),
        ],
    }


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
    completed_set = set(completed_artifacts)
    target_set = set(target_files)
    downstream_pending_artifacts = [
        path
        for path in director_task_boundary_downstream_pending_artifacts(context_override)
        if path not in target_set and path not in completed_set
    ]
    dispatch = dict(tool_dispatch or {})
    blocked_dependencies = director_task_boundary_blocked_dependencies(context_override)
    evidence_policy = director_task_boundary_evidence_policy(context_override)
    verifier_policy = director_task_boundary_verifier_policy(context_override)
    has_boundary_obligation = bool(
        target_files
        or completed_artifacts
        or downstream_pending_artifacts
        or dispatch.get("dropped")
        or dispatch.get("failure_class")
        or blocked_dependencies
        or evidence_policy
        or any(verifier_policy.values())
    )
    if not has_boundary_obligation:
        return None
    from polaris.cells.control_plane.run_ledger.public import evaluate_task_boundary_verdict

    return evaluate_task_boundary_verdict(
        workspace=workspace,
        task_id=task_id,
        run_id=run_id,
        target_files=target_files,
        completed_artifacts=completed_artifacts,
        downstream_pending_artifacts=downstream_pending_artifacts,
        blocked_dependencies=blocked_dependencies,
        required_evidence_modalities=evidence_policy.get("required_evidence_modalities")
        or evidence_policy.get("required_modalities"),
        present_evidence_modalities=evidence_policy.get("present_evidence_modalities")
        or evidence_policy.get("present_modalities"),
        missing_required_evidence_modalities=evidence_policy.get("missing_required_evidence_modalities")
        or evidence_policy.get("missing_required_modalities"),
        failed_required_evidence_modalities=evidence_policy.get("failed_required_evidence_modalities")
        or evidence_policy.get("failed_required_modalities"),
        required_verifiers=verifier_policy["required"],
        completed_verifiers=verifier_policy["completed"],
        missing_required_verifiers=verifier_policy["missing"],
        failed_required_verifiers=verifier_policy["failed"],
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
) -> dict[str, Any] | None:
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
        return None
    _append_task_boundary_verdict_event(
        workspace=workspace,
        task_id=task_id,
        run_id=run_id,
        verdict=verdict,
    )
    return verdict


def append_deferred_followup_task_boundary_verdict(
    *,
    workspace: str,
    task_id: str,
    run_id: str,
    reason: str,
    evidence_refs: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
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
    return verdict


def append_role_turn_task_boundary_verdict(
    *,
    role: str,
    workspace: str,
    task_id: str,
    run_id: str,
    context_override: Any,
    tool_results: list[dict[str, Any]],
    tool_dispatch: dict[str, Any] | None = None,
    needs_followup_workflow: bool,
    workflow_reason: str | None = None,
    error_message: str | None = None,
    evidence_refs: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    """Append the task-boundary verdict implied by a completed role turn.

    Normal Director turns are evaluated against target-file completion. Turns
    intentionally handed to a governed follow-up record a deferred verdict so
    the control plane does not mistake the turn for a successful completion.
    """
    if needs_followup_workflow:
        return append_deferred_followup_task_boundary_verdict(
            workspace=workspace,
            task_id=task_id,
            run_id=run_id,
            reason=str(workflow_reason or error_message or "needs_followup_workflow"),
            evidence_refs=evidence_refs,
        )

    return append_director_task_boundary_verdict(
        role=role,
        workspace=workspace,
        task_id=task_id,
        run_id=run_id,
        context_override=context_override,
        tool_results=tool_results,
        tool_dispatch=tool_dispatch,
        evidence_refs=evidence_refs,
    )


def task_boundary_evidence_refs_from_metadata(metadata: dict[str, Any]) -> list[str]:
    """Project canonical context and execution-commit references for QA."""

    candidates = [str(metadata.get("context_snapshot_ref") or "").strip()]
    commit_receipt = metadata.get("turn_commit_receipt")
    if isinstance(commit_receipt, dict):
        candidates.append(str(commit_receipt.get("fact_event_id") or "").strip())
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))
