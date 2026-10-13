"""Recognize CE verification-only contracts without granting artifact writes."""

from collections.abc import Mapping
from typing import Any

from ..public.contracts import ProjectCompletionContractV1


def is_verification_only_blueprint(payload: Mapping[str, Any]) -> bool:
    """Require exact CE input and command bindings for an artifactless task."""
    if payload.get("planning_contract_version") != "pm.task_intent.v1":
        return False
    if payload.get("verification_authority") != "chief_engineer" or payload.get("target_files"):
        return False
    required_writes = payload.get("required_write_targets")
    if not isinstance(required_writes, (list, tuple)) or required_writes:
        return False
    task_id = str(payload.get("task_id") or "")
    raw_completion = payload.get("project_completion_contract")
    interface = payload.get("project_interface_contract")
    if not isinstance(raw_completion, Mapping) or not isinstance(interface, Mapping):
        return False
    try:
        completion = ProjectCompletionContractV1.from_dict(raw_completion)
    except (TypeError, ValueError):
        return False
    if task_id not in completion.covered_task_ids or str(payload.get("run_id") or "") != completion.run_id:
        return False
    if any(item.owner_task_id == task_id and item.applicability != "not_applicable"
           for item in completion.obligations.artifacts):
        return False
    inputs_by_task = interface.get("task_read_context")
    if not isinstance(inputs_by_task, Mapping):
        return False
    inputs = inputs_by_task.get(task_id)
    if not isinstance(inputs, (list, tuple)) or not inputs or list(inputs) != list(payload.get("context_files") or ()):
        return False
    required_hashes = {item.command_authority_hash for item in completion.obligations.verification
                       if item.owner_task_id == task_id and item.applicability == "required"}
    commands = {item.command for item in completion.verification_command_authority
                if item.task_id == task_id and item.authority_hash in required_hashes and item.modality != "entrypoint"}
    return bool(commands) and commands == set(payload.get("verification_commands") or ())
