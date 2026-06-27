"""Public service exports for `director.execution` cell.

Backward-compatible facade. Implementation migrated to sub-Cells:
- director.planning  → already migrated (Phase 2)
- director.tasking   → TaskQueueConfig, TaskService, WorkerPoolConfig, WorkerService (Phase 3)
- director.runtime   → PatchApplyEngine, FileApplyService, ExistenceGate (Phase 4, pending)
- director.delivery  → director_cli (Phase 5, pending)
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Mapping
from threading import Thread
from typing import Any

from polaris.cells.director.execution.internal.director_agent import DirectorAgent
from polaris.cells.director.execution.logic import extract_defect_ticket, parse_acceptance, write_gate_check
from polaris.cells.director.execution.public.contracts import (
    DirectorExecutionError,
    DirectorExecutionResultV1,
    ExecuteDirectorTaskCommandV1,
)
from polaris.cells.director.execution.service import DirectorConfig, DirectorService, DirectorState
from polaris.cells.director.tasking.public import (
    ApplyIntegrity,
    EditType,
    TaskQueueConfig,
    TaskService,
    WorkerPoolConfig,
    WorkerService,
    parse_all_operations,
    parse_full_file_blocks,
    parse_search_replace_blocks,
    validate_before_apply,
)
from polaris.domain.entities import Task, TaskResult
from polaris.kernelone.constants import DEFAULT_OPERATION_TIMEOUT_SECONDS


def _run_director_awaitable(awaitable: Awaitable[TaskResult]) -> TaskResult:
    async def _await_result() -> TaskResult:
        return await awaitable

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await_result())

    result_holder: dict[str, TaskResult] = {}
    error_holder: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result_holder["result"] = asyncio.run(_await_result())
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - defensive thread bridge
            error_holder["error"] = exc

    thread = Thread(target=_runner, name="director-execution-public-service", daemon=True)
    thread.start()
    thread.join()
    if error := error_holder.get("error"):
        raise error
    return result_holder["result"]


def _coerce_timeout_seconds(metadata: dict[str, Any]) -> int:
    raw_timeout = metadata.get("timeout_seconds", DEFAULT_OPERATION_TIMEOUT_SECONDS)
    try:
        timeout_seconds = int(raw_timeout)
    except (TypeError, ValueError) as exc:
        raise DirectorExecutionError(
            "metadata.timeout_seconds must be an integer",
            code="invalid_director_timeout",
            details={"timeout_seconds": raw_timeout},
        ) from exc
    if timeout_seconds <= 0:
        raise DirectorExecutionError(
            "metadata.timeout_seconds must be > 0",
            code="invalid_director_timeout",
            details={"timeout_seconds": raw_timeout},
        )
    return timeout_seconds


def _mapping_value(metadata: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, Mapping):
            payload = dict(value)
            if payload:
                return payload
    return {}


def _string_value(metadata: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return ""


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        token = value.strip()
        return [token] if token else []
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    rows: list[str] = []
    seen: set[str] = set()
    for item in value:
        token = str(item or "").strip()
        if token and token not in seen:
            seen.add(token)
            rows.append(token)
    return rows


def _ref_hash(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    return str(value.get("hash") or "").strip()


def _is_missing_ref(value: str) -> bool:
    return not value or value.startswith("missing:")


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"true", "1", "yes", "allowed"}:
            return True
        if token in {"false", "0", "no", "denied"}:
            return False
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "strict", "required"}
    return False


def _execution_contract_refs_required(metadata: Mapping[str, Any]) -> bool:
    return any(
        _truthy(metadata.get(key))
        for key in (
            "require_execution_contract_refs",
            "execution_contract_refs_required",
            "execution_envelope_strict",
        )
    )


def _build_execution_contract_audit(metadata: Mapping[str, Any]) -> dict[str, Any]:
    envelope = _mapping_value(metadata, "director_execution_envelope", "task_execution_envelope", "execution_envelope")
    handoff = _mapping_value(metadata, "ce_handoff_decision", "handoff_decision")
    if not handoff:
        handoff = _mapping_value(envelope, "handoff_decision")

    execution_envelope_hash = (
        _string_value(
            metadata,
            "execution_envelope_hash",
            "director_execution_envelope_hash",
            "task_execution_envelope_hash",
        )
        or str(envelope.get("envelope_hash") or "").strip()
    )
    handoff_decision_hash = _string_value(
        metadata,
        "handoff_decision_hash",
        "ce_handoff_decision_hash",
    ) or _ref_hash(handoff)
    pm_contract_hash = _string_value(metadata, "pm_contract_hash", "contract_hash") or _ref_hash(
        envelope.get("pm_contract")
    )
    ce_blueprint_hash = _string_value(metadata, "ce_blueprint_hash", "blueprint_hash") or _ref_hash(
        envelope.get("ce_blueprint")
    )
    execution_profile_hash = _string_value(
        metadata,
        "execution_profile_hash",
        "task_execution_profile_hash",
        "director_execution_profile_hash",
    ) or _ref_hash(envelope.get("execution_profile"))
    handoff_allowed = _optional_bool(handoff.get("allowed")) if handoff else None
    has_handoff_decision = bool(handoff) and not _is_missing_ref(handoff_decision_hash)

    required_refs = {
        "execution_envelope_hash": execution_envelope_hash,
        "handoff_decision_hash": handoff_decision_hash,
        "pm_contract_hash": pm_contract_hash,
        "ce_blueprint_hash": ce_blueprint_hash,
        "execution_profile_hash": execution_profile_hash,
    }
    missing_required_refs = [name for name, value in required_refs.items() if _is_missing_ref(value)]

    return {
        "schema_version": "director.execution_contract_audit.v1",
        "source": "director.execution.public.service",
        "public_contract": "ExecuteDirectorTaskCommandV1",
        "has_execution_envelope": bool(envelope),
        "execution_envelope_hash": execution_envelope_hash,
        "has_ce_handoff_decision": has_handoff_decision,
        "ce_handoff_allowed": handoff_allowed,
        "handoff_decision_hash": handoff_decision_hash,
        "pm_contract_hash": pm_contract_hash,
        "ce_blueprint_hash": ce_blueprint_hash,
        "execution_profile_hash": execution_profile_hash,
        "missing_required_refs": missing_required_refs,
        "enforcement": "strict" if _execution_contract_refs_required(metadata) else "audit_only",
    }


def _has_execution_envelope(metadata: Mapping[str, Any]) -> bool:
    return bool(
        _mapping_value(metadata, "director_execution_envelope", "task_execution_envelope", "execution_envelope")
    )


def _ensure_execution_envelope_metadata(command: ExecuteDirectorTaskCommandV1, metadata: dict[str, Any]) -> None:
    if _has_execution_envelope(metadata):
        return

    from polaris.cells.director.tasking.internal.execution_profile import resolve_director_execution_profile
    from polaris.cells.director.tasking.internal.execution_strategy import (
        apply_execution_strategy_overrides,
        resolve_director_execution_strategy,
    )

    target_files = _string_list(metadata.get("target_files"))
    scope_paths = _string_list(metadata.get("scope_paths"))
    profile = resolve_director_execution_profile(
        subject=str(metadata.get("title") or metadata.get("subject") or command.instruction or ""),
        description=str(metadata.get("description") or metadata.get("objective") or command.instruction or ""),
        metadata=metadata,
        target_files=target_files,
        scope_paths=scope_paths,
        workspace=command.workspace,
    )
    strategy = resolve_director_execution_strategy(profile, metadata=metadata)
    context: dict[str, Any] = {
        "workspace": command.workspace,
        "task_id": command.task_id,
        "run_id": command.run_id or metadata.get("run_id") or "unknown-run",
    }
    apply_execution_strategy_overrides(
        context=context,
        metadata=metadata,
        profile=profile,
        strategy=strategy,
    )
    metadata.setdefault("task_execution_profile_source", "director.execution.public.service")


def _build_director_task(command: ExecuteDirectorTaskCommandV1) -> Task:
    metadata = dict(command.metadata)
    metadata.setdefault("role_capability_id", "execute_director_task")
    metadata.setdefault("public_contract", "ExecuteDirectorTaskCommandV1")
    metadata.setdefault("director_execution_attempt", command.attempt)
    if command.run_id:
        metadata.setdefault("run_id", command.run_id)
    _ensure_execution_envelope_metadata(command, metadata)
    metadata["execution_contract_audit"] = _build_execution_contract_audit(metadata)
    command_line = str(metadata.get("command") or "").strip() or None
    working_directory = str(metadata.get("working_directory") or command.workspace).strip()
    return Task(
        id=command.task_id,
        subject=command.instruction,
        description=command.instruction,
        owner="director",
        assignee="director",
        role="director",
        command=command_line,
        working_directory=working_directory,
        timeout_seconds=_coerce_timeout_seconds(metadata),
        metadata=metadata,
    )


def execute_director_task(
    command: ExecuteDirectorTaskCommandV1,
    *,
    director_service: Any | None = None,
) -> DirectorExecutionResultV1:
    """Execute a Director task through the `director.execution` public contract."""

    if not isinstance(command, ExecuteDirectorTaskCommandV1):
        raise TypeError("command must be ExecuteDirectorTaskCommandV1")

    try:
        task = _build_director_task(command)
        execution_contract_audit = dict(task.metadata.get("execution_contract_audit") or {})
        missing_required_refs = [
            str(item) for item in execution_contract_audit.get("missing_required_refs") or [] if str(item).strip()
        ]
        if execution_contract_audit.get("enforcement") == "strict" and missing_required_refs:
            return DirectorExecutionResultV1(
                ok=False,
                task_id=command.task_id,
                workspace=command.workspace,
                status="failed",
                run_id=command.run_id,
                error_code="director_execution_contract_refs_missing",
                error_message="Director execution contract is missing required refs: "
                + ", ".join(missing_required_refs),
                metadata={"execution_contract_audit": execution_contract_audit},
            )
        service = director_service or DirectorService(DirectorConfig(workspace=command.workspace))
        maybe_result = service._execute_task_work(task)
        task_result = _run_director_awaitable(maybe_result) if inspect.isawaitable(maybe_result) else maybe_result
        if not isinstance(task_result, TaskResult):
            raise DirectorExecutionError(
                "Director service returned a non-TaskResult value",
                code="invalid_director_task_result",
                details={"result_type": type(task_result).__name__},
            )
    except DirectorExecutionError as exc:
        return DirectorExecutionResultV1(
            ok=False,
            task_id=command.task_id,
            workspace=command.workspace,
            status="failed",
            run_id=command.run_id,
            error_code=exc.code,
            error_message=str(exc),
            metadata={"execution_contract_audit": _build_execution_contract_audit(command.metadata)},
        )
    except Exception as exc:  # noqa: BLE001 - public RPC boundary returns structured failure
        return DirectorExecutionResultV1(
            ok=False,
            task_id=command.task_id,
            workspace=command.workspace,
            status="failed",
            run_id=command.run_id,
            error_code="director_execution_failed",
            error_message=str(exc),
            metadata={"execution_contract_audit": _build_execution_contract_audit(command.metadata)},
        )

    evidence_paths = tuple(evidence.path for evidence in task_result.evidence if evidence.path)
    if task_result.success:
        return DirectorExecutionResultV1(
            ok=True,
            task_id=command.task_id,
            workspace=command.workspace,
            status="completed",
            run_id=command.run_id,
            evidence_paths=evidence_paths,
            output_summary=task_result.output,
            metadata={"execution_contract_audit": execution_contract_audit},
        )
    return DirectorExecutionResultV1(
        ok=False,
        task_id=command.task_id,
        workspace=command.workspace,
        status="failed",
        run_id=command.run_id,
        evidence_paths=evidence_paths,
        output_summary=task_result.output,
        error_code="director_task_failed",
        error_message=task_result.error or "director task failed",
        metadata={"execution_contract_audit": execution_contract_audit},
    )


__all__ = [
    "ApplyIntegrity",
    "DirectorAgent",
    "DirectorConfig",
    "DirectorExecutionError",
    "DirectorExecutionResultV1",
    "DirectorService",
    "DirectorState",
    "EditType",
    "ExecuteDirectorTaskCommandV1",
    "TaskQueueConfig",
    "TaskService",
    "WorkerPoolConfig",
    "WorkerService",
    "execute_director_task",
    "extract_defect_ticket",
    "parse_acceptance",
    "parse_all_operations",
    "parse_full_file_blocks",
    "parse_search_replace_blocks",
    "validate_before_apply",
    "write_gate_check",
]
