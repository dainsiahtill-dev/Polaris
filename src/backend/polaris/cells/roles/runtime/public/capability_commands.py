"""Stateless module-level command handlers for the ``roles.runtime`` cell.

This module is a lossless extraction of the stateless capability-command
subsystem from :mod:`polaris.cells.roles.runtime.public.service`. It hosts the
public command entrypoints (``instantiate_role_runtime_object``,
``execute_role_task_market_lifecycle``, ``commit_role_state``,
``rehydrate_role_handoff``, ``assemble_role_runtime_chain`` and
``execute_role_capability_invocation``) together with their private ref/payload
normalization helpers. The :class:`RoleRuntimeService` singleton and its
``_kernel_lock`` deliberately remain in ``service.py``; nothing here holds
process-level state.

The shared payload/ref helpers (``_payload_string``, ``_payload_mapping``,
``_unique_string_tuple`` and ``_merge_refs``) live here as their single
definition; ``service.py`` re-exports them so the public import surface stays
byte-identical.
"""

from __future__ import annotations

import concurrent.futures as futures
import dataclasses
import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.runtime.public.contracts import (
    AssembleRoleRuntimeChainCommandV1,
    ExecuteRoleCapabilityInvocationCommandV1,
    ExecuteRoleTaskMarketLifecycleCommandV1,
    InstantiateRoleRuntimeObjectCommandV1,
    RehydrateRoleHandoffCommandV1,
    RoleCapabilityDescriptor,
    RoleCapabilityInvocationResultV1,
    RoleHandoffRehydrationResultV1,
    RoleIdentity,
    RoleLedgerBinding,
    RoleProfileBinding,
    RoleRuntimeChainAssemblyResultV1,
    RoleRuntimeChainEnvelope,
    RoleRuntimeObject,
    RoleRuntimeObjectResultV1,
    RoleStateCommitReceipt,
    RoleStateCommitRequest,
    RoleTaskMarketLifecycleResultV1,
    get_builtin_role_runtime_spec,
)

if TYPE_CHECKING:
    from polaris.cells.roles.runtime.internal.capability import (
        ArchitectDesignPort,
        BlueprintPort,
        BudgetGuardPort,
        CapabilityHandlerRegistry,
        CodeIntelligencePort,
        DirectorExecutionPort,
        LlmControlPlanePort,
        PermissionPort,
        QaAuditPort,
        RuntimeProjectionPort,
        TaskMarketPort,
        VerificationGuardPort,
        WorkspaceGuardPort,
    )

FutureTimeoutError = futures.TimeoutError


def _capability_invocation_failure(
    command: ExecuteRoleCapabilityInvocationCommandV1,
    *,
    error_code: str,
    error_message: str,
    capability_available: bool = False,
    owner_cell: str = "",
    evidence_refs: tuple[str, ...] = (),
    metadata: Mapping[str, Any] | None = None,
) -> RoleCapabilityInvocationResultV1:
    invocation = command.invocation
    failure_metadata: Mapping[str, Any] = metadata or {}
    if capability_available:
        failure_metadata = _capability_available_metadata(invocation.capability_id, failure_metadata)
    return RoleCapabilityInvocationResultV1(
        ok=False,
        invocation_id=invocation.invocation_id,
        role_id=command.runtime_object.identity.role_id,
        capability_id=invocation.capability_id,
        command_contract=invocation.command_contract,
        allowed=False,
        owner_cell=owner_cell,
        payload_ref=invocation.payload_ref,
        evidence_refs=evidence_refs,
        metadata=failure_metadata,
        error_code=error_code,
        error_message=error_message,
    )


def _payload_string(payload: Mapping[str, Any], key: str, default: str = "") -> str:
    return str(payload.get(key) or default).strip()


def _normalize_model_capability(value: Any, default: str = "image_input") -> str:
    token = str(value or default).strip().lower().replace("-", "_")
    return token or default


def _payload_mapping(payload: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    value = payload.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        return None
    return dict(value)


def _payload_string_tuple(payload: Mapping[str, Any], key: str) -> tuple[str, ...] | None:
    value = payload.get(key)
    if value is None:
        return ()
    if isinstance(value, str):
        token = value.strip()
        return (token,) if token else ()
    if isinstance(value, (list, tuple, set, frozenset)):
        rows: list[str] = []
        seen: set[str] = set()
        for item in value:
            token = str(item or "").strip()
            if token and token not in seen:
                rows.append(token)
                seen.add(token)
        return tuple(rows)
    return None


def _audit_evidence_refs(values: Iterable[Any]) -> tuple[str, ...]:
    refs: list[str] = []
    seen: set[str] = set()
    for value in values:
        ref = str(value or "").strip()
        if not ref:
            continue
        if ref == "audit.evidence" or ref.startswith("audit.evidence:"):
            evidence_ref = ref
        elif ref.startswith("runtime/evidence/"):
            evidence_ref = f"audit.evidence:path:{ref}"
        else:
            continue
        if evidence_ref not in seen:
            refs.append(evidence_ref)
            seen.add(evidence_ref)
    return tuple(refs)


def _visual_audit_evidence_refs(values: Iterable[Any]) -> tuple[str, ...]:
    refs: list[str] = []
    seen: set[str] = set()
    for value in values:
        ref = str(value or "").strip()
        if not ref:
            continue
        if ref == "audit.evidence" or ref.startswith("audit.evidence:"):
            evidence_ref = ref
        elif ref.startswith("runtime/evidence/"):
            evidence_ref = f"audit.evidence:path:{ref}"
        else:
            continue
        if evidence_ref not in seen:
            refs.append(evidence_ref)
            seen.add(evidence_ref)
    return tuple(refs)


def _mapping_string_tuple(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if value is None:
        return ()
    if isinstance(value, str):
        token = value.strip()
        return (token,) if token else ()
    if isinstance(value, (list, tuple, set, frozenset)):
        rows: list[str] = []
        seen: set[str] = set()
        for item in value:
            token = str(item or "").strip()
            if token and token not in seen:
                rows.append(token)
                seen.add(token)
        return tuple(rows)
    return ()


def _asset_mount_ref(runtime_object: RoleRuntimeObject, mount_name: str) -> str:
    try:
        return runtime_object.asset_mounts.get(mount_name).asset_ref.ref
    except KeyError:
        return ""


def _pm_asset_refs(runtime_object: RoleRuntimeObject) -> dict[str, str]:
    return {
        "project_function_index": _asset_mount_ref(runtime_object, "ProjectFunctionIndex"),
        "task_graph": _asset_mount_ref(runtime_object, "TaskGraph"),
        "runtime_projection_state": _asset_mount_ref(runtime_object, "RuntimeProjectionState"),
        "open_loop_registry": _asset_mount_ref(runtime_object, "OpenLoopRegistry"),
    }


def _chief_engineer_asset_refs(runtime_object: RoleRuntimeObject) -> dict[str, str]:
    return {
        "blueprint_database": _asset_mount_ref(runtime_object, "BlueprintDatabase"),
        "arch_constraint_memo": _asset_mount_ref(runtime_object, "ArchConstraintMemo"),
        "diff_map_archive": _asset_mount_ref(runtime_object, "DiffMapArchive"),
    }


def _capability_available_metadata(
    capability_id: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(metadata or {})
    payload["capability_available"] = True
    payload["capability_id"] = capability_id
    return payload


def _run_with_timeout(callable_obj: Callable[[], Any], timeout_seconds: float) -> Any:
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(callable_obj)
    try:
        return future.result(timeout=timeout_seconds)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _check_workspace_guard_paths(
    *,
    paths: tuple[str, ...],
    operation: str,
    workspace_guard_service: Any | None,
) -> tuple[bool, tuple[str, ...], str, str]:
    from polaris.cells.policy.workspace_guard.public.contracts import WorkspaceWriteGuardBatchQueryV1
    from polaris.cells.policy.workspace_guard.public.service import check_workspace_write_guard_batch

    if not paths:
        return True, (), "", ""

    query = WorkspaceWriteGuardBatchQueryV1(paths=paths, operation=operation)
    if workspace_guard_service is None:
        decision = check_workspace_write_guard_batch(query)
    else:
        decision = workspace_guard_service.check_workspace_write_guard_batch(query)
    checked_paths = tuple(decision.checked_paths)
    denied_path = str(decision.denied_path or "")
    return bool(decision.allowed), checked_paths, denied_path, str(decision.reason or "")


def _merge_refs(*groups: str | tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    refs: list[str] = []
    seen: set[str] = set()
    for group in groups:
        if group is None:
            continue
        values = (group,) if isinstance(group, str) else group
        for value in values:
            ref = str(value or "").strip()
            if ref and ref not in seen:
                refs.append(ref)
                seen.add(ref)
    return tuple(refs)


def _turn_context_payload_refs(runtime_object: RoleRuntimeObject) -> tuple[str, ...]:
    return _merge_refs(
        runtime_object.turn_context.typed_input_ref,
        runtime_object.turn_context.task_refs,
    )


def _runtime_receipt_ref(receipt_id: str) -> str:
    return f"factory.cognitive_runtime:receipt:{receipt_id}"


def _change_set_validation_ref(validation_id: str) -> str:
    return f"factory.cognitive_runtime:change-set-validation:{validation_id}"


def _handoff_pack_ref(handoff_id: str) -> str:
    return f"factory.cognitive_runtime:handoff:{handoff_id}"


def _handoff_rehydration_ref(rehydration_id: str) -> str:
    return f"factory.cognitive_runtime:rehydration:{rehydration_id}"


def _handoff_id_from_ref(handoff_ref: str) -> str:
    parts = str(handoff_ref or "").strip().split(":", 2)
    if len(parts) != 3 or parts[0] != "factory.cognitive_runtime" or parts[1] != "handoff":
        raise ValueError("handoff_ref must use factory.cognitive_runtime:handoff:<handoff_id>")
    handoff_id = parts[2].strip()
    if not handoff_id:
        raise ValueError("handoff_ref must include a handoff id")
    return handoff_id


def _normalize_owner_ref(ref: str, *, owner_cell: str, ref_kind: str) -> str:
    token = str(ref or "").strip()
    if not token:
        return ""
    if token.split(":", 1)[0] == owner_cell:
        return token
    return f"{owner_cell}:{ref_kind}:{token}"


def _normalize_owner_refs(refs: Iterable[Any], *, owner_cell: str, ref_kind: str) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        token = _normalize_owner_ref(str(ref or ""), owner_cell=owner_cell, ref_kind=ref_kind)
        if token and token not in seen:
            normalized.append(token)
            seen.add(token)
    return tuple(normalized)


def _profile_policy_ref(role_id: str, policy_name: str, profile_fingerprint: str) -> str:
    return f"roles.profile:{role_id}:{policy_name}:{profile_fingerprint}"


def _unique_string_tuple(values: Iterable[Any]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = str(value or "").strip()
        if token and token not in seen:
            result.append(token)
            seen.add(token)
    return tuple(result)


def _runtime_object_audit_metadata(runtime_object: RoleRuntimeObject) -> dict[str, Any]:
    """Build a refs-only audit index for an instantiated runtime object."""
    asset_mounts = runtime_object.asset_mounts.mounts
    capabilities = runtime_object.capability_ports.capabilities
    task_market_binding = runtime_object.task_market_binding
    capability_fingerprint = runtime_object.capability_fingerprint
    task_market_binding_refs = _unique_string_tuple(
        (
            task_market_binding.work_item_ref,
            task_market_binding.lease_token_ref,
        )
    )

    return {
        "asset_refs": _unique_string_tuple(mount.asset_ref.ref for mount in asset_mounts),
        "asset_owner_cells": _unique_string_tuple(mount.asset_ref.owner_cell for mount in asset_mounts),
        "capability_refs": _unique_string_tuple(
            f"{capability.owner_cell}:{capability.contract_name}" for capability in capabilities
        ),
        "capability_owner_cells": _unique_string_tuple(capability.owner_cell for capability in capabilities),
        "turn_ledger_ref": runtime_object.ledger_binding.turn_ledger_ref,
        "commit_receipt_ref": runtime_object.ledger_binding.commit_receipt_ref or "",
        "runtime_receipt_refs": tuple(runtime_object.ledger_binding.receipt_refs),
        "task_market_binding_refs": task_market_binding_refs,
        "turn_task_refs": tuple(runtime_object.turn_context.task_refs),
        "handoff_refs": tuple(runtime_object.turn_context.handoff_refs),
        "typed_input_ref": runtime_object.turn_context.typed_input_ref,
        "context_snapshot_ref": runtime_object.turn_context.context_snapshot_ref,
        "capability_fingerprint_ref": f"roles.runtime:capability-fingerprint:{capability_fingerprint.fingerprint}",
    }


def instantiate_role_runtime_object(
    command: InstantiateRoleRuntimeObjectCommandV1,
    *,
    profile_service: Any | None = None,
) -> RoleRuntimeObjectResultV1:
    """Instantiate a stateful role object from public profile and runtime contracts."""
    if not isinstance(command, InstantiateRoleRuntimeObjectCommandV1):
        raise TypeError("command must be an InstantiateRoleRuntimeObjectCommandV1")

    try:
        spec = get_builtin_role_runtime_spec(command.role_id)
    except KeyError:
        return RoleRuntimeObjectResultV1(
            ok=False,
            role_id=command.role_id,
            error_code="unknown_role_runtime_spec",
            error_message=f"role runtime spec {command.role_id!r} was not found",
        )

    try:
        from polaris.cells.roles.profile.public.contracts import GetRoleProfileQueryV1, RoleProfileResultV1
        from polaris.cells.roles.profile.public.service import get_profile as get_role_profile

        query = GetRoleProfileQueryV1(role_id=spec.role_id)
        profile_result = profile_service.get_profile(query) if profile_service is not None else get_role_profile(query)
        if not isinstance(profile_result, RoleProfileResultV1):
            raise TypeError("profile service returned non-RoleProfileResultV1")
    except Exception as exc:  # noqa: BLE001 - public facade returns structured failure
        return RoleRuntimeObjectResultV1(
            ok=False,
            role_id=spec.role_id,
            error_code="profile_binding_failed",
            error_message=str(exc),
        )

    if not profile_result.ok:
        return RoleRuntimeObjectResultV1(
            ok=False,
            role_id=spec.role_id,
            error_code=profile_result.error_code or "profile_not_available",
            error_message=profile_result.error_message or f"profile {spec.role_id!r} is not available",
        )

    profile_payload = dict(profile_result.payload)
    profile_fingerprint = str(profile_payload.get("profile_fingerprint") or "").strip()
    if not profile_fingerprint:
        payload_bytes = json.dumps(profile_payload, sort_keys=True, default=str).encode("utf-8")
        profile_fingerprint = hashlib.sha256(payload_bytes).hexdigest()[:16]
    profile_ref = str(profile_payload.get("profile_ref") or "").strip() or _profile_policy_ref(
        spec.role_id,
        "profile",
        profile_fingerprint,
    )
    profile_binding = RoleProfileBinding(
        role_id=spec.role_id,
        profile_ref=profile_ref,
        tool_policy_ref=_profile_policy_ref(spec.role_id, "tool_policy", profile_fingerprint),
        prompt_policy_ref=_profile_policy_ref(spec.role_id, "prompt_policy", profile_fingerprint),
        data_policy_ref=_profile_policy_ref(spec.role_id, "data_policy", profile_fingerprint),
        profile_fingerprint=profile_fingerprint,
    )

    try:
        command_metadata = dict(command.metadata)
        runtime_object = spec.instantiate(
            identity=RoleIdentity(
                role_id=spec.role_id,
                run_id=command.run_id,
                task_id=command.task_id,
                session_id=command.session_id,
                workspace=command.workspace,
                host_kind=command.host_kind,
            ),
            profile_binding=profile_binding,
            ledger_binding=RoleLedgerBinding(turn_ledger_ref=command.turn_ledger_ref),
            policy_fingerprint=command.policy_fingerprint,
            capability_id=command.capability_id,
            task_market_binding=command.task_market_binding,
            metadata={
                **command_metadata,
                "profile_ref": profile_ref,
                "profile_owner_cell": "roles.profile",
            },
        )
    except Exception as exc:  # noqa: BLE001 - public facade returns structured failure
        return RoleRuntimeObjectResultV1(
            ok=False,
            role_id=spec.role_id,
            error_code="runtime_object_instantiation_failed",
            error_message=str(exc),
        )

    audit_metadata = _runtime_object_audit_metadata(runtime_object)
    runtime_object_metadata = {
        **dict(runtime_object.metadata),
        **audit_metadata,
    }
    if runtime_object_metadata != dict(runtime_object.metadata):
        runtime_object = dataclasses.replace(runtime_object, metadata=runtime_object_metadata)

    return RoleRuntimeObjectResultV1(
        ok=True,
        role_id=spec.role_id,
        runtime_object=runtime_object,
        profile_ref=profile_ref,
        metadata={
            "profile_ref": profile_ref,
            "default_capability_id": spec.default_capability_id,
            "capability_id": runtime_object.capability_fingerprint.capability_id,
            **audit_metadata,
        },
    )


_TASK_MARKET_LIFECYCLE_CONTRACT_ATTRS: dict[str, str] = {
    "publish": "publish_contract",
    "claim": "claim_contract",
    "lease": "lease_contract",
    "renew": "lease_contract",
    "renew_lease": "lease_contract",
    "ack": "ack_contract",
    "acknowledge": "ack_contract",
    "fail": "fail_contract",
    "requeue": "requeue_contract",
    "dead_letter": "dead_letter_contract",
    "dlq": "dead_letter_contract",
    "move_to_dead_letter": "dead_letter_contract",
}


def _task_market_lifecycle_capability(
    runtime_object: RoleRuntimeObject,
    command_contract: str,
) -> RoleCapabilityDescriptor | None:
    for capability in runtime_object.capability_ports.capabilities:
        if capability.owner_cell == "runtime.task_market" and capability.contract_name == command_contract:
            return capability
    return None


def _task_market_lifecycle_result_ref(task_id: str) -> str:
    return f"runtime.task_market:task:{task_id}" if task_id else ""


def _task_market_lifecycle_lease_ref(lease_token: str) -> str:
    return f"runtime.task_market:lease:{lease_token}" if lease_token else ""


def _task_market_lifecycle_failure(
    command: ExecuteRoleTaskMarketLifecycleCommandV1,
    *,
    operation: str,
    command_contract: str = "",
    error_code: str,
    error_message: str,
    metadata: Mapping[str, Any] | None = None,
) -> RoleTaskMarketLifecycleResultV1:
    failure_metadata = {"owner_cell": "runtime.task_market"}
    failure_metadata.update(dict(metadata or {}))
    return RoleTaskMarketLifecycleResultV1(
        ok=False,
        role_id=command.runtime_object.identity.role_id,
        operation=operation or command.operation,
        command_contract=command_contract or "unknown",
        error_code=error_code,
        error_message=error_message,
        metadata=failure_metadata,
    )


def _task_market_lifecycle_metadata(command: ExecuteRoleTaskMarketLifecycleCommandV1) -> dict[str, Any]:
    payload_metadata = _payload_mapping(command.payload, "metadata")
    if payload_metadata is None:
        payload_metadata = {}
    runtime_object = command.runtime_object
    identity = runtime_object.identity
    payload_metadata.update(dict(command.metadata))
    payload_metadata.update(
        {
            "role_id": identity.role_id,
            "run_id": identity.run_id or "",
            "task_id": identity.task_id or "",
            "session_id": identity.session_id or "",
            "host_kind": identity.host_kind,
            "role_runtime_profile_ref": runtime_object.profile_binding.profile_ref,
        }
    )
    return payload_metadata


def execute_role_task_market_lifecycle(
    command: ExecuteRoleTaskMarketLifecycleCommandV1,
    *,
    task_market_service: Any | None = None,
) -> RoleTaskMarketLifecycleResultV1:
    """Execute claim/lease/ack/fail/requeue through the task-market public boundary."""
    if not isinstance(command, ExecuteRoleTaskMarketLifecycleCommandV1):
        raise TypeError("command must be an ExecuteRoleTaskMarketLifecycleCommandV1")

    operation = command.operation
    contract_attr = _TASK_MARKET_LIFECYCLE_CONTRACT_ATTRS.get(operation)
    if contract_attr is None:
        return _task_market_lifecycle_failure(
            command,
            operation=operation,
            error_code="unsupported_task_market_operation",
            error_message=f"unsupported task-market lifecycle operation {operation!r}",
        )
    if operation in {"renew", "renew_lease"}:
        operation = "lease"
    if operation == "acknowledge":
        operation = "ack"
    if operation in {"dlq", "move_to_dead_letter"}:
        operation = "dead_letter"

    binding = command.runtime_object.task_market_binding
    command_contract = str(getattr(binding, contract_attr))
    runtime_object = command.runtime_object
    lifecycle_capability = _task_market_lifecycle_capability(runtime_object, command_contract)
    if lifecycle_capability is None:
        return _task_market_lifecycle_failure(
            command,
            operation=operation,
            command_contract=command_contract,
            error_code="task_market_capability_not_mounted",
            error_message="task-market lifecycle operation requires a mounted runtime.task_market capability port",
            metadata={"command_contract": command_contract},
        )
    role_id = runtime_object.identity.role_id
    if role_id not in lifecycle_capability.allowed_roles:
        return _task_market_lifecycle_failure(
            command,
            operation=operation,
            command_contract=command_contract,
            error_code="task_market_capability_role_denied",
            error_message="task-market lifecycle capability is not allowed for this role",
            metadata={
                "capability_id": lifecycle_capability.capability_id,
                "allowed_roles": lifecycle_capability.allowed_roles,
                "role_id": role_id,
            },
        )
    capability_fingerprint = runtime_object.capability_fingerprint
    expected_tool = lifecycle_capability.endpoint_ref or ""
    if (
        capability_fingerprint.role_id != role_id
        or capability_fingerprint.capability_id != lifecycle_capability.capability_id
        or capability_fingerprint.effect != lifecycle_capability.effect
        or (expected_tool and capability_fingerprint.tool != expected_tool)
    ):
        return _task_market_lifecycle_failure(
            command,
            operation=operation,
            command_contract=command_contract,
            error_code="task_market_capability_fingerprint_mismatch",
            error_message="task-market lifecycle capability must match the current RoleCapabilityFingerprint",
            metadata={
                "expected_capability_id": lifecycle_capability.capability_id,
                "actual_capability_id": capability_fingerprint.capability_id,
                "expected_effect": lifecycle_capability.effect,
                "actual_effect": capability_fingerprint.effect,
                "expected_tool": expected_tool,
                "actual_tool": capability_fingerprint.tool,
            },
        )
    if operation in {"lease", "ack", "fail", "requeue", "dead_letter"}:
        task_id = _payload_string(command.payload, "task_id")
        task_ref = _task_market_lifecycle_result_ref(task_id)
        if not task_ref or task_ref not in runtime_object.turn_context.task_refs:
            return _task_market_lifecycle_failure(
                command,
                operation=operation,
                command_contract=command_contract,
                error_code="task_market_task_ref_outside_turn_context",
                error_message="task-market lifecycle task_id must match the current RoleTurnContext task refs",
                metadata={
                    "task_ref": task_ref,
                    "turn_task_refs": runtime_object.turn_context.task_refs,
                },
            )
    if operation in {"lease", "ack", "fail"}:
        lease_token_ref = _task_market_lifecycle_lease_ref(_payload_string(command.payload, "lease_token"))
        binding_lease_token_ref = runtime_object.task_market_binding.lease_token_ref or ""
        if not binding_lease_token_ref:
            return _task_market_lifecycle_failure(
                command,
                operation=operation,
                command_contract=command_contract,
                error_code="task_market_lease_ref_missing_from_binding",
                error_message="task-market lifecycle lease operations require the current RoleTaskMarketBinding lease ref",
                metadata={
                    "lease_token_ref": lease_token_ref,
                    "binding_lease_token_ref": binding_lease_token_ref,
                },
            )
        if lease_token_ref != binding_lease_token_ref:
            return _task_market_lifecycle_failure(
                command,
                operation=operation,
                command_contract=command_contract,
                error_code="task_market_lease_ref_outside_binding",
                error_message="task-market lifecycle lease_token must match the current RoleTaskMarketBinding lease ref",
                metadata={
                    "lease_token_ref": lease_token_ref,
                    "binding_lease_token_ref": binding_lease_token_ref,
                },
            )
    try:
        from polaris.cells.runtime.task_market.public.contracts import (
            AcknowledgeTaskStageCommandV1,
            ClaimTaskWorkItemCommandV1,
            FailTaskStageCommandV1,
            MoveTaskToDeadLetterCommandV1,
            PublishTaskWorkItemCommandV1,
            RenewTaskLeaseCommandV1,
            RequeueTaskCommandV1,
        )
        from polaris.cells.runtime.task_market.public.service import get_task_market_service

        service = task_market_service or get_task_market_service()
        identity = command.runtime_object.identity
        workspace = _payload_string(command.payload, "workspace", identity.workspace)
        metadata = _task_market_lifecycle_metadata(command)

        if operation == "publish":
            task_command = PublishTaskWorkItemCommandV1(
                workspace=workspace,
                trace_id=_payload_string(command.payload, "trace_id", identity.run_id or identity.task_id),
                run_id=_payload_string(command.payload, "run_id", identity.run_id or identity.task_id),
                task_id=_payload_string(command.payload, "task_id", identity.task_id),
                stage=_payload_string(
                    command.payload,
                    "stage",
                    str(lifecycle_capability.metadata.get("target_stage") or ""),
                ),
                source_role=identity.role_id,
                payload=_payload_mapping(command.payload, "payload") or {},
                priority=_payload_string(command.payload, "priority", "medium"),
                max_attempts=int(command.payload.get("max_attempts", 3)),
                metadata=metadata,
                plan_id=_payload_string(command.payload, "plan_id"),
                plan_revision_id=_payload_string(command.payload, "plan_revision_id"),
                root_task_id=_payload_string(command.payload, "root_task_id"),
                parent_task_id=_payload_string(command.payload, "parent_task_id"),
                is_leaf=bool(command.payload.get("is_leaf", True)),
                depends_on=tuple(command.payload.get("depends_on", ())),
                requirement_digest=_payload_string(command.payload, "requirement_digest"),
                constraint_digest=_payload_string(command.payload, "constraint_digest"),
                summary_ref=_payload_string(command.payload, "summary_ref"),
                superseded_by_revision=_payload_string(command.payload, "superseded_by_revision"),
                change_policy=_payload_string(command.payload, "change_policy", "strict"),
                compensation_group_id=_payload_string(command.payload, "compensation_group_id"),
            )
            result = service.publish_work_item(task_command)
        elif operation == "claim":
            task_command = ClaimTaskWorkItemCommandV1(
                workspace=workspace,
                stage=_payload_string(command.payload, "stage"),
                worker_id=_payload_string(
                    command.payload,
                    "worker_id",
                    identity.run_id or identity.session_id or identity.role_id,
                ),
                worker_role=_payload_string(command.payload, "worker_role", identity.role_id),
                visibility_timeout_seconds=int(command.payload.get("visibility_timeout_seconds", 900)),
                task_id=_payload_string(command.payload, "task_id") or None,
                trace_id=_payload_string(command.payload, "trace_id") or None,
            )
            result = service.claim_work_item(task_command)
        elif operation == "lease":
            task_command = RenewTaskLeaseCommandV1(
                workspace=workspace,
                task_id=_payload_string(command.payload, "task_id"),
                lease_token=_payload_string(command.payload, "lease_token"),
                visibility_timeout_seconds=int(command.payload.get("visibility_timeout_seconds", 900)),
            )
            result = service.renew_task_lease(task_command)
        elif operation == "ack":
            task_command = AcknowledgeTaskStageCommandV1(
                workspace=workspace,
                task_id=_payload_string(command.payload, "task_id"),
                lease_token=_payload_string(command.payload, "lease_token"),
                next_stage=_payload_string(command.payload, "next_stage") or None,
                terminal_status=_payload_string(command.payload, "terminal_status") or None,
                summary=_payload_string(command.payload, "summary"),
                metadata=metadata,
            )
            result = service.acknowledge_task_stage(task_command)
        elif operation == "fail":
            task_command = FailTaskStageCommandV1(
                workspace=workspace,
                task_id=_payload_string(command.payload, "task_id"),
                lease_token=_payload_string(command.payload, "lease_token"),
                error_code=_payload_string(command.payload, "error_code"),
                error_message=_payload_string(command.payload, "error_message"),
                requeue_stage=_payload_string(command.payload, "requeue_stage") or None,
                to_dead_letter=bool(command.payload.get("to_dead_letter", False)),
                metadata=metadata,
            )
            result = service.fail_task_stage(task_command)
        elif operation == "requeue":
            task_command = RequeueTaskCommandV1(
                workspace=workspace,
                task_id=_payload_string(command.payload, "task_id"),
                target_stage=_payload_string(command.payload, "target_stage"),
                reason=_payload_string(command.payload, "reason"),
                metadata=metadata,
            )
            result = service.requeue_task(task_command)
        else:
            task_command = MoveTaskToDeadLetterCommandV1(
                workspace=workspace,
                task_id=_payload_string(command.payload, "task_id"),
                reason=_payload_string(command.payload, "reason"),
                error_code=_payload_string(command.payload, "error_code") or None,
                metadata=metadata,
            )
            result = service.move_task_to_dead_letter(task_command)
    except Exception as exc:  # noqa: BLE001 - public facade returns structured failure
        return _task_market_lifecycle_failure(
            command,
            operation=operation,
            command_contract=command_contract,
            error_code="task_market_lifecycle_failed",
            error_message=str(exc),
        )

    ok = bool(getattr(result, "ok", False))
    task_id = str(getattr(result, "task_id", "") or "").strip()
    lease_token = str(getattr(result, "lease_token", "") or "").strip()
    status = str(getattr(result, "status", "") or "").strip() or ("lease_renewed" if operation == "lease" else "")
    result_ref = _task_market_lifecycle_result_ref(task_id)
    lease_token_ref = _task_market_lifecycle_lease_ref(lease_token)
    if ok and not result_ref:
        return _task_market_lifecycle_failure(
            command,
            operation=operation,
            command_contract=command_contract,
            error_code="task_market_lifecycle_missing_result_ref",
            error_message="successful task-market lifecycle result must include a task_id result ref",
            metadata={
                "version": getattr(result, "version", 0),
                "status": status,
                "stage": getattr(result, "stage", ""),
            },
        )
    if ok and operation in {"claim", "lease"} and not lease_token_ref:
        return _task_market_lifecycle_failure(
            command,
            operation=operation,
            command_contract=command_contract,
            error_code="task_market_lifecycle_missing_lease_ref",
            error_message="successful claim/lease task-market lifecycle result must include a lease token ref",
            metadata={
                "version": getattr(result, "version", 0),
                "status": status,
                "stage": getattr(result, "stage", ""),
                "result_ref": result_ref,
            },
        )
    return RoleTaskMarketLifecycleResultV1(
        ok=ok,
        role_id=command.runtime_object.identity.role_id,
        operation=operation,
        command_contract=command_contract,
        task_id=task_id,
        status=status,
        result_ref=result_ref,
        lease_token_ref=lease_token_ref,
        metadata={
            "owner_cell": "runtime.task_market",
            "version": getattr(result, "version", 0),
            "stage": getattr(result, "stage", ""),
            "reason": getattr(result, "reason", ""),
        },
        error_code=None if ok else "task_market_lifecycle_not_ok",
        error_message=None if ok else str(getattr(result, "reason", "") or "not ok"),
    )


def _serialize_role_state_commit_envelope(request: RoleStateCommitRequest) -> dict[str, Any]:
    envelope = request.envelope
    return {
        "identity": {
            "role_id": envelope.identity.role_id,
            "run_id": envelope.identity.run_id,
            "task_id": envelope.identity.task_id,
            "session_id": envelope.identity.session_id,
            "workspace": envelope.identity.workspace,
            "host_kind": envelope.identity.host_kind,
        },
        "profile_binding": {
            "role_id": envelope.profile_binding.role_id,
            "profile_ref": envelope.profile_binding.profile_ref,
            "tool_policy_ref": envelope.profile_binding.tool_policy_ref,
            "prompt_policy_ref": envelope.profile_binding.prompt_policy_ref,
            "data_policy_ref": envelope.profile_binding.data_policy_ref,
            "profile_fingerprint": envelope.profile_binding.profile_fingerprint,
            "owner_cell": envelope.profile_binding.owner_cell,
        },
        "turn_context": {
            "typed_input_ref": envelope.turn_context.typed_input_ref,
            "context_snapshot_ref": envelope.turn_context.context_snapshot_ref,
            "handoff_refs": envelope.turn_context.handoff_refs,
            "task_refs": envelope.turn_context.task_refs,
            "metadata": dict(envelope.turn_context.metadata),
        },
        "capability_invocations": tuple(
            {
                "invocation_id": invocation.invocation_id,
                "capability_id": invocation.capability_id,
                "role_id": invocation.role_id,
                "command_contract": invocation.command_contract,
                "payload_ref": invocation.payload_ref,
                "fingerprint_ref": invocation.fingerprint_ref,
                "metadata": dict(invocation.metadata),
            }
            for invocation in envelope.capability_invocations
        ),
        "ledger_binding": {
            "turn_ledger_ref": envelope.ledger_binding.turn_ledger_ref,
            "commit_contract": envelope.ledger_binding.commit_contract,
            "runtime_receipt_contract": envelope.ledger_binding.runtime_receipt_contract,
            "receipt_refs": envelope.ledger_binding.receipt_refs,
            "commit_receipt_ref": envelope.ledger_binding.commit_receipt_ref,
        },
        "task_market_binding": {
            "publish_contract": envelope.task_market_binding.publish_contract,
            "claim_contract": envelope.task_market_binding.claim_contract,
            "lease_contract": envelope.task_market_binding.lease_contract,
            "ack_contract": envelope.task_market_binding.ack_contract,
            "fail_contract": envelope.task_market_binding.fail_contract,
            "requeue_contract": envelope.task_market_binding.requeue_contract,
            "work_item_ref": envelope.task_market_binding.work_item_ref,
            "lease_token_ref": envelope.task_market_binding.lease_token_ref,
        },
        "metadata": dict(envelope.metadata),
    }


def commit_role_state(
    request: RoleStateCommitRequest,
    *,
    cognitive_runtime_service: Any | None = None,
) -> RoleStateCommitReceipt:
    """Commit role turn refs through kernel commit receipt and Cognitive Runtime receipts."""
    if not isinstance(request, RoleStateCommitRequest):
        raise TypeError("request must be a RoleStateCommitRequest")

    envelope = request.envelope
    identity = envelope.identity
    ledger = envelope.ledger_binding
    commit_receipt_ref = ledger.commit_receipt_ref
    if not commit_receipt_ref:
        return RoleStateCommitReceipt(
            request_id=request.request_id,
            ok=False,
            status="rejected",
            error_code="missing_commit_receipt_ref",
            error_message="Role state commit requires an existing roles.kernel CommitReceipt ref",
        )

    turn_envelope = _serialize_role_state_commit_envelope(request)
    payload = {
        "request_id": request.request_id,
        "role_id": identity.role_id,
        "task_id": identity.task_id,
        "session_id": identity.session_id,
        "run_id": identity.run_id,
        "changed_asset_refs": request.changed_asset_refs,
        "changed_files": request.changed_files,
        "allowed_scope_paths": request.allowed_scope_paths,
        "evidence_refs": request.evidence_refs,
        "reason": request.reason,
        "commit_receipt_ref": commit_receipt_ref,
        "turn_ledger_ref": ledger.turn_ledger_ref,
    }

    close_after = cognitive_runtime_service is None
    service = cognitive_runtime_service
    try:
        from polaris.cells.factory.cognitive_runtime.public.contracts import (
            ExportHandoffPackCommandV1,
            RecordRuntimeReceiptCommandV1,
            ValidateChangeSetCommandV1,
        )
        from polaris.cells.factory.cognitive_runtime.public.service import (
            get_cognitive_runtime_public_service,
        )

        if service is None:
            service = get_cognitive_runtime_public_service()

        validation_result = service.validate_change_set(
            ValidateChangeSetCommandV1(
                workspace=identity.workspace,
                changed_files=request.changed_files,
                allowed_scope_paths=request.allowed_scope_paths or ("runtime/", "workspace/"),
                evidence_refs=request.evidence_refs,
                require_change=request.require_change_validation or bool(request.changed_files),
            )
        )
        if not bool(getattr(validation_result, "ok", False)):
            error_message = str(getattr(validation_result, "error_message", "") or "").strip()
            error_code = str(getattr(validation_result, "error_code", "") or "").strip()
            return RoleStateCommitReceipt(
                request_id=request.request_id,
                ok=False,
                commit_receipt_ref=commit_receipt_ref,
                runtime_receipt_refs=ledger.receipt_refs,
                status="change_set_validation_failed",
                error_code=error_code or "change_set_validation_failed",
                error_message=error_message or "Cognitive Runtime change-set validation failed",
            )
        validation = getattr(validation_result, "validation", None)
        validation_id = str(getattr(validation, "validation_id", "") or "").strip()
        if not validation_id:
            return RoleStateCommitReceipt(
                request_id=request.request_id,
                ok=False,
                commit_receipt_ref=commit_receipt_ref,
                runtime_receipt_refs=ledger.receipt_refs,
                status="change_set_validation_failed",
                error_code="change_set_validation_missing_id",
                error_message="Cognitive Runtime change-set validation response did not include validation_id",
            )
        change_set_validation_ref = _change_set_validation_ref(validation_id)
        payload["change_set_validation_ref"] = change_set_validation_ref
        trace_refs = _merge_refs(
            commit_receipt_ref,
            ledger.turn_ledger_ref,
            ledger.receipt_refs,
            request.changed_asset_refs,
            request.changed_files,
            request.evidence_refs,
            envelope.turn_context.handoff_refs,
            envelope.turn_context.task_refs,
            change_set_validation_ref,
        )

        receipt_result = service.record_runtime_receipt(
            RecordRuntimeReceiptCommandV1(
                workspace=identity.workspace,
                receipt_type="role_state_commit",
                payload=payload,
                session_id=identity.session_id,
                run_id=identity.run_id,
                trace_refs=trace_refs,
                turn_envelope=turn_envelope,
            )
        )
        if not bool(getattr(receipt_result, "ok", False)):
            error_message = str(getattr(receipt_result, "error_message", "") or "").strip()
            error_code = str(getattr(receipt_result, "error_code", "") or "").strip()
            return RoleStateCommitReceipt(
                request_id=request.request_id,
                ok=False,
                commit_receipt_ref=commit_receipt_ref,
                change_set_validation_ref=change_set_validation_ref,
                runtime_receipt_refs=ledger.receipt_refs,
                status="receipt_failed",
                error_code=error_code or "runtime_receipt_failed",
                error_message=error_message or "Cognitive Runtime receipt recording failed",
            )
        runtime_receipt = getattr(receipt_result, "receipt", None)
        receipt_id = str(getattr(runtime_receipt, "receipt_id", "") or "").strip()
        if not receipt_id:
            return RoleStateCommitReceipt(
                request_id=request.request_id,
                ok=False,
                commit_receipt_ref=commit_receipt_ref,
                change_set_validation_ref=change_set_validation_ref,
                runtime_receipt_refs=ledger.receipt_refs,
                status="receipt_failed",
                error_code="runtime_receipt_missing_id",
                error_message="Cognitive Runtime receipt response did not include receipt_id",
            )

        runtime_receipt_refs = _merge_refs(ledger.receipt_refs, _runtime_receipt_ref(receipt_id))
        handoff_pack_refs: tuple[str, ...] = ()
        if identity.session_id:
            handoff_turn_envelope = dict(turn_envelope)
            handoff_turn_envelope["runtime_receipt_refs"] = runtime_receipt_refs
            handoff_result = service.export_handoff_pack(
                ExportHandoffPackCommandV1(
                    workspace=identity.workspace,
                    session_id=identity.session_id,
                    run_id=identity.run_id,
                    reason=request.reason or f"role_state_commit:{request.request_id}",
                    turn_envelope=handoff_turn_envelope,
                    metadata={
                        "request_id": request.request_id,
                        "commit_receipt_ref": commit_receipt_ref,
                    },
                )
            )
            if not bool(getattr(handoff_result, "ok", False)):
                error_message = str(getattr(handoff_result, "error_message", "") or "").strip()
                error_code = str(getattr(handoff_result, "error_code", "") or "").strip()
                return RoleStateCommitReceipt(
                    request_id=request.request_id,
                    ok=False,
                    commit_receipt_ref=commit_receipt_ref,
                    change_set_validation_ref=change_set_validation_ref,
                    runtime_receipt_refs=runtime_receipt_refs,
                    status="handoff_failed",
                    error_code=error_code or "handoff_export_failed",
                    error_message=error_message or "Cognitive Runtime handoff export failed",
                )
            handoff = getattr(handoff_result, "handoff", None)
            handoff_id = str(getattr(handoff, "handoff_id", "") or "").strip()
            if not handoff_id:
                return RoleStateCommitReceipt(
                    request_id=request.request_id,
                    ok=False,
                    commit_receipt_ref=commit_receipt_ref,
                    change_set_validation_ref=change_set_validation_ref,
                    runtime_receipt_refs=runtime_receipt_refs,
                    status="handoff_failed",
                    error_code="handoff_missing_id",
                    error_message="Cognitive Runtime handoff response did not include handoff_id",
                )
            handoff_pack_refs = (_handoff_pack_ref(handoff_id),)

        return RoleStateCommitReceipt(
            request_id=request.request_id,
            ok=True,
            commit_receipt_ref=commit_receipt_ref,
            change_set_validation_ref=change_set_validation_ref,
            runtime_receipt_refs=runtime_receipt_refs,
            handoff_pack_refs=handoff_pack_refs,
            turn_outcome_ref=str(envelope.metadata.get("turn_outcome_ref") or "").strip() or None,
            status="committed",
        )
    except (RuntimeError, ValueError) as exc:
        return RoleStateCommitReceipt(
            request_id=request.request_id,
            ok=False,
            commit_receipt_ref=commit_receipt_ref,
            runtime_receipt_refs=ledger.receipt_refs,
            status="failed",
            error_code="role_state_commit_failed",
            error_message=str(exc),
        )
    finally:
        if close_after and service is not None and hasattr(service, "close"):
            service.close()


def rehydrate_role_handoff(
    command: RehydrateRoleHandoffCommandV1,
    *,
    cognitive_runtime_service: Any | None = None,
) -> RoleHandoffRehydrationResultV1:
    """Rehydrate a Cognitive Runtime handoff pack without owning handoff state."""
    if not isinstance(command, RehydrateRoleHandoffCommandV1):
        raise TypeError("command must be a RehydrateRoleHandoffCommandV1")

    identity = command.identity
    turn_context = command.turn_context
    turn_envelope = {
        "identity": {
            "role_id": identity.role_id,
            "run_id": identity.run_id,
            "task_id": identity.task_id,
            "session_id": identity.session_id,
            "workspace": identity.workspace,
            "host_kind": identity.host_kind,
        },
        "turn_context": {
            "typed_input_ref": turn_context.typed_input_ref,
            "context_snapshot_ref": turn_context.context_snapshot_ref,
            "handoff_refs": turn_context.handoff_refs,
            "task_refs": turn_context.task_refs,
            "metadata": dict(turn_context.metadata),
        },
        "metadata": dict(command.metadata),
    }

    close_after = cognitive_runtime_service is None
    service = cognitive_runtime_service
    try:
        from polaris.cells.factory.cognitive_runtime.public.contracts import RehydrateHandoffPackCommandV1
        from polaris.cells.factory.cognitive_runtime.public.service import get_cognitive_runtime_public_service

        if service is None:
            service = get_cognitive_runtime_public_service()

        result = service.rehydrate_handoff_pack(
            RehydrateHandoffPackCommandV1(
                workspace=identity.workspace,
                handoff_id=_handoff_id_from_ref(command.handoff_ref),
                target_role=command.target_role,
                target_session_id=command.target_session_id,
                turn_envelope=turn_envelope,
                metadata={
                    **dict(command.metadata),
                    "handoff_ref": command.handoff_ref,
                    "role_payload_ref": turn_context.typed_input_ref,
                    "source_role": identity.role_id,
                },
            )
        )
        if not bool(getattr(result, "ok", False)):
            error_message = str(getattr(result, "error_message", "") or "").strip()
            error_code = str(getattr(result, "error_code", "") or "").strip()
            return RoleHandoffRehydrationResultV1(
                ok=False,
                handoff_ref=command.handoff_ref,
                target_role=command.target_role,
                target_session_id=command.target_session_id,
                status="rehydration_failed",
                error_code=error_code or "handoff_rehydration_failed",
                error_message=error_message or "Cognitive Runtime handoff rehydration failed",
            )

        rehydration = getattr(result, "rehydration", None)
        rehydration_id = str(getattr(rehydration, "rehydration_id", "") or "").strip()
        if not rehydration_id:
            return RoleHandoffRehydrationResultV1(
                ok=False,
                handoff_ref=command.handoff_ref,
                target_role=command.target_role,
                target_session_id=command.target_session_id,
                status="rehydration_failed",
                error_code="handoff_rehydration_missing_id",
                error_message="Cognitive Runtime handoff rehydration response did not include rehydration_id",
            )

        return RoleHandoffRehydrationResultV1(
            ok=True,
            handoff_ref=command.handoff_ref,
            target_role=command.target_role,
            target_session_id=command.target_session_id,
            rehydration_ref=_handoff_rehydration_ref(rehydration_id),
            context_override=dict(getattr(rehydration, "context_override", {}) or {}),
            metadata_patch=dict(getattr(rehydration, "metadata_patch", {}) or {}),
            runtime_receipt_refs=_normalize_owner_refs(
                getattr(rehydration, "receipt_refs", ()) or (),
                owner_cell="factory.cognitive_runtime",
                ref_kind="receipt",
            ),
            artifact_refs=_normalize_owner_refs(
                getattr(rehydration, "artifact_refs", ()) or (),
                owner_cell="roles.session",
                ref_kind="artifact",
            ),
            episode_refs=_normalize_owner_refs(
                getattr(rehydration, "episode_refs", ()) or (),
                owner_cell="roles.session",
                ref_kind="episode",
            ),
            source_spans=tuple(getattr(rehydration, "source_spans", ()) or ()),
            status="rehydrated",
        )
    except (RuntimeError, ValueError) as exc:
        return RoleHandoffRehydrationResultV1(
            ok=False,
            handoff_ref=command.handoff_ref,
            target_role=command.target_role,
            target_session_id=command.target_session_id,
            status="failed",
            error_code="role_handoff_rehydration_failed",
            error_message=str(exc),
        )
    finally:
        if close_after and service is not None and hasattr(service, "close"):
            service.close()


def _role_runtime_chain_ref(chain_id: str) -> str:
    return f"roles.runtime:chain:{chain_id}"


_FULL_PHASE5_REQUIRED_ROLES = ("pm", "chief_engineer", "director", "qa")
_FULL_PHASE5_REQUIRED_EVIDENCE_ROLES = ("director", "qa")
_FULL_PHASE5_REQUIRED_HANDOFF_ROLES = ("chief_engineer", "director")
_FULL_PHASE5_REQUIRED_RECEIPT_ROLES = ("chief_engineer", "director", "qa")


def _ref_has_namespace(ref: str, namespace: str) -> bool:
    return str(ref or "").strip().split(":", 1)[0] == namespace


def _first_ref_outside_namespace(refs: tuple[str, ...], namespace: str) -> str:
    for ref in refs:
        if not _ref_has_namespace(ref, namespace):
            return ref
    return ""


def _chain_invalid_ref_failure(
    *,
    chain_ref: str,
    error_code: str,
    error_message: str,
    required_owner_cell: str,
    invalid_ref: str,
) -> RoleRuntimeChainAssemblyResultV1:
    return RoleRuntimeChainAssemblyResultV1(
        ok=False,
        chain_ref=chain_ref,
        error_code=error_code,
        error_message=error_message,
        metadata={
            "required_owner_cell": required_owner_cell,
            "invalid_ref": invalid_ref,
        },
    )


def assemble_role_runtime_chain(
    command: AssembleRoleRuntimeChainCommandV1,
) -> RoleRuntimeChainAssemblyResultV1:
    """Assemble a refs-only Phase 5 role runtime chain envelope."""

    if not isinstance(command, AssembleRoleRuntimeChainCommandV1):
        raise TypeError("command must be an AssembleRoleRuntimeChainCommandV1")

    chain_ref = _role_runtime_chain_ref(command.chain_id)
    if not _ref_has_namespace(command.turn_ledger_ref, "roles.kernel"):
        return _chain_invalid_ref_failure(
            chain_ref=chain_ref,
            error_code="invalid_turn_ledger_ref",
            error_message="turn_ledger_ref must point to roles.kernel",
            required_owner_cell="roles.kernel",
            invalid_ref=command.turn_ledger_ref,
        )

    present_roles = {step.role_id for step in command.steps}
    missing_roles = tuple(role for role in command.required_roles if role not in present_roles)
    if missing_roles:
        return RoleRuntimeChainAssemblyResultV1(
            ok=False,
            chain_ref=chain_ref,
            missing_roles=missing_roles,
            error_code="missing_required_chain_roles",
            error_message="role runtime chain is missing required role step(s): " + ", ".join(missing_roles),
        )

    is_full_phase5_chain = all(role in present_roles for role in _FULL_PHASE5_REQUIRED_ROLES)
    if is_full_phase5_chain and command.required_roles != _FULL_PHASE5_REQUIRED_ROLES:
        return RoleRuntimeChainAssemblyResultV1(
            ok=False,
            chain_ref=chain_ref,
            error_code="required_roles_cannot_downgrade_full_phase5_chain",
            error_message="full Phase 5 role runtime chain cannot downgrade required_roles",
            metadata={
                "expected_required_roles": _FULL_PHASE5_REQUIRED_ROLES,
                "actual_required_roles": command.required_roles,
            },
        )

    required_role_positions = {role: index for index, role in enumerate(command.required_roles)}
    actual_required_order = tuple(step.role_id for step in command.steps if step.role_id in required_role_positions)
    last_required_position = -1
    for role_id in actual_required_order:
        required_position = required_role_positions[role_id]
        if required_position < last_required_position:
            return RoleRuntimeChainAssemblyResultV1(
                ok=False,
                chain_ref=chain_ref,
                error_code="chain_required_roles_out_of_order",
                error_message="role runtime chain required roles must follow declared required_roles order",
                metadata={
                    "expected_order": command.required_roles,
                    "actual_order": actual_required_order,
                },
            )
        last_required_position = required_position

    if is_full_phase5_chain and not command.runtime_projection_refs:
        return RoleRuntimeChainAssemblyResultV1(
            ok=False,
            chain_ref=chain_ref,
            error_code="missing_runtime_projection_ref",
            error_message="full Phase 5 role runtime chain requires at least one runtime.projection ref",
            metadata={
                "required_roles": command.required_roles,
                "required_owner_cell": "runtime.projection",
            },
        )
    invalid_runtime_projection_ref = _first_ref_outside_namespace(
        command.runtime_projection_refs,
        "runtime.projection",
    )
    if invalid_runtime_projection_ref:
        return _chain_invalid_ref_failure(
            chain_ref=chain_ref,
            error_code="invalid_runtime_projection_ref",
            error_message="runtime_projection_refs must point to runtime.projection",
            required_owner_cell="runtime.projection",
            invalid_ref=invalid_runtime_projection_ref,
        )

    task_market_refs = _merge_refs(
        tuple(step.task_ref or "" for step in command.steps),
        tuple(step.work_item_ref or "" for step in command.steps),
    )
    audit_evidence_refs = _merge_refs(
        command.audit_evidence_refs,
        *(step.evidence_refs for step in command.steps),
    )
    if is_full_phase5_chain and not audit_evidence_refs:
        return RoleRuntimeChainAssemblyResultV1(
            ok=False,
            chain_ref=chain_ref,
            error_code="missing_audit_evidence_ref",
            error_message="full Phase 5 role runtime chain requires at least one audit.evidence ref",
            metadata={
                "required_roles": command.required_roles,
                "required_owner_cell": "audit.evidence",
            },
        )
    invalid_audit_evidence_ref = _first_ref_outside_namespace(audit_evidence_refs, "audit.evidence")
    if invalid_audit_evidence_ref:
        return _chain_invalid_ref_failure(
            chain_ref=chain_ref,
            error_code="invalid_audit_evidence_ref",
            error_message="audit_evidence_refs must point to audit.evidence",
            required_owner_cell="audit.evidence",
            invalid_ref=invalid_audit_evidence_ref,
        )
    if is_full_phase5_chain:
        for role_id in _FULL_PHASE5_REQUIRED_EVIDENCE_ROLES:
            step = next(step for step in command.steps if step.role_id == role_id)
            if not step.evidence_refs:
                return RoleRuntimeChainAssemblyResultV1(
                    ok=False,
                    chain_ref=chain_ref,
                    error_code="missing_phase5_role_audit_evidence_ref",
                    error_message="full Phase 5 role runtime chain requires audit evidence refs for each audited role",
                    metadata={
                        "required_roles": command.required_roles,
                        "required_owner_cell": "audit.evidence",
                        "missing_role": role_id,
                        "required_evidence_roles": _FULL_PHASE5_REQUIRED_EVIDENCE_ROLES,
                    },
                )
    capability_fingerprint_refs = _merge_refs(tuple(step.capability_fingerprint_ref for step in command.steps))
    handoff_refs = _merge_refs(*(step.handoff_refs for step in command.steps))
    runtime_receipt_refs = _merge_refs(*(step.receipt_refs for step in command.steps))
    if is_full_phase5_chain and not handoff_refs:
        return RoleRuntimeChainAssemblyResultV1(
            ok=False,
            chain_ref=chain_ref,
            error_code="missing_handoff_ref",
            error_message="full Phase 5 role runtime chain requires at least one handoff ref",
            metadata={
                "required_roles": command.required_roles,
                "required_owner_cell": "factory.cognitive_runtime",
                "missing_ref": "handoff",
            },
        )
    if is_full_phase5_chain:
        for role_id in _FULL_PHASE5_REQUIRED_HANDOFF_ROLES:
            step = next(step for step in command.steps if step.role_id == role_id)
            if not step.handoff_refs:
                return RoleRuntimeChainAssemblyResultV1(
                    ok=False,
                    chain_ref=chain_ref,
                    error_code="missing_phase5_role_handoff_ref",
                    error_message="full Phase 5 role runtime chain requires typed handoff refs for each role transition",
                    metadata={
                        "required_roles": command.required_roles,
                        "required_owner_cell": "factory.cognitive_runtime",
                        "missing_role": role_id,
                        "required_handoff_roles": _FULL_PHASE5_REQUIRED_HANDOFF_ROLES,
                    },
                )
    if is_full_phase5_chain and not runtime_receipt_refs:
        return RoleRuntimeChainAssemblyResultV1(
            ok=False,
            chain_ref=chain_ref,
            error_code="missing_runtime_receipt_ref",
            error_message="full Phase 5 role runtime chain requires at least one runtime receipt ref",
            metadata={
                "required_roles": command.required_roles,
                "required_owner_cell": "factory.cognitive_runtime",
                "missing_ref": "runtime_receipt",
            },
        )
    if is_full_phase5_chain:
        for role_id in _FULL_PHASE5_REQUIRED_RECEIPT_ROLES:
            step = next(step for step in command.steps if step.role_id == role_id)
            if not step.receipt_refs:
                return RoleRuntimeChainAssemblyResultV1(
                    ok=False,
                    chain_ref=chain_ref,
                    error_code="missing_phase5_role_runtime_receipt_ref",
                    error_message="full Phase 5 role runtime chain requires runtime receipt refs for each executed role",
                    metadata={
                        "required_roles": command.required_roles,
                        "required_owner_cell": "factory.cognitive_runtime",
                        "missing_role": role_id,
                        "required_receipt_roles": _FULL_PHASE5_REQUIRED_RECEIPT_ROLES,
                    },
                )
    chain = RoleRuntimeChainEnvelope(
        chain_id=command.chain_id,
        workspace=command.workspace,
        run_id=command.run_id,
        task_id=command.task_id,
        steps=command.steps,
        turn_ledger_ref=command.turn_ledger_ref,
        task_market_refs=task_market_refs,
        audit_evidence_refs=audit_evidence_refs,
        runtime_projection_refs=command.runtime_projection_refs,
        capability_fingerprint_refs=capability_fingerprint_refs,
        handoff_refs=handoff_refs,
        runtime_receipt_refs=runtime_receipt_refs,
        metadata={
            **dict(command.metadata),
            "chain_ref": chain_ref,
            "required_roles": command.required_roles,
        },
    )
    return RoleRuntimeChainAssemblyResultV1(
        ok=True,
        chain_ref=chain_ref,
        chain=chain,
    )


def execute_role_capability_invocation(
    command: ExecuteRoleCapabilityInvocationCommandV1,
    *,
    task_market_service: TaskMarketPort | None = None,
    blueprint_service: BlueprintPort | None = None,
    code_intelligence_service: CodeIntelligencePort | None = None,
    verification_guard_service: VerificationGuardPort | None = None,
    qa_audit_service: QaAuditPort | None = None,
    runtime_projection_service: RuntimeProjectionPort | None = None,
    budget_guard_service: BudgetGuardPort | None = None,
    workspace_guard_service: WorkspaceGuardPort | None = None,
    permission_service: PermissionPort | None = None,
    architect_design_service: ArchitectDesignPort | None = None,
    llm_control_plane_service: LlmControlPlanePort | None = None,
    director_execution_service: DirectorExecutionPort | None = None,
    handlers: CapabilityHandlerRegistry | None = None,
) -> RoleCapabilityInvocationResultV1:
    """Execute a mounted role capability through its declared public contract.

    ``handlers`` is an optional typed :class:`CapabilityHandlerRegistry` seam for
    the in-progress decomposition of this dispatcher. Capability families that
    have been migrated onto a :class:`CapabilityHandler` are routed through the
    registry (the explicit ``handlers`` argument, else the process-wide
    :func:`default_capability_registry`); families not yet migrated fall through
    to the legacy ``if/elif`` ladder below. Behavior is byte-identical either way.

    The twelve ``*_service`` kwargs remain ``Any | None`` while the legacy ladder
    still consumes them directly; they are funnelled into the typed
    :class:`CapabilityDeps` at the dispatch seam so migrated handlers see a
    zero-``Any`` port surface. Phase 4 retypes the public kwargs once every family
    is migrated and the legacy ladder is gone.
    """
    if not isinstance(command, ExecuteRoleCapabilityInvocationCommandV1):
        raise TypeError("command must be an ExecuteRoleCapabilityInvocationCommandV1")

    runtime_object = command.runtime_object
    invocation = command.invocation
    role_id = runtime_object.identity.role_id
    if invocation.role_id != role_id:
        return _capability_invocation_failure(
            command,
            error_code="role_mismatch",
            error_message=f"invocation role {invocation.role_id!r} does not match runtime role {role_id!r}",
        )

    try:
        capability = runtime_object.capability_ports.get(invocation.capability_id)
    except KeyError:
        return _capability_invocation_failure(
            command,
            error_code="capability_not_mounted",
            error_message=f"capability {invocation.capability_id!r} is not mounted on role {role_id!r}",
        )

    if role_id not in capability.allowed_roles:
        return _capability_invocation_failure(
            command,
            error_code="capability_role_denied",
            error_message=f"role {role_id!r} is not allowed for capability {capability.capability_id!r}",
            owner_cell=capability.owner_cell,
        )

    if invocation.command_contract != capability.contract_name:
        return _capability_invocation_failure(
            command,
            error_code="capability_contract_mismatch",
            error_message=(
                f"invocation contract {invocation.command_contract!r} does not match "
                f"mounted contract {capability.contract_name!r}"
            ),
            owner_cell=capability.owner_cell,
        )

    is_qa_pytest_verification = (
        capability.capability_id == "invoke_container_pytest"
        and capability.owner_cell == "factory.verification_guard"
        and capability.contract_name == "VerifyCompletionCommandV1"
    )
    is_qa_visual_audit_verdict = (
        capability.capability_id == "issue_visual_audit_verdict"
        and capability.owner_cell == "qa.audit_verdict"
        and capability.contract_name == "RunVisualQaAuditCommandV1"
    )

    if is_qa_pytest_verification and role_id != "qa":
        return _capability_invocation_failure(
            command,
            capability_available=False,
            owner_cell=capability.owner_cell,
            error_code="qa_capability_role_denied",
            error_message="invoke_container_pytest requires the qa role runtime object",
            metadata=_capability_available_metadata(
                capability.capability_id,
                {
                    "required_role": "qa",
                    "actual_role": role_id,
                    "required_effect": "process.spawn:qa/pytest",
                },
            ),
        )

    if is_qa_visual_audit_verdict and role_id != "qa":
        return _capability_invocation_failure(
            command,
            capability_available=False,
            owner_cell=capability.owner_cell,
            error_code="qa_visual_capability_role_denied",
            error_message="issue_visual_audit_verdict requires the qa role runtime object",
            metadata=_capability_available_metadata(
                capability.capability_id,
                {
                    "required_role": "qa",
                    "actual_role": role_id,
                    "required_effect": "llm.invoke:vision",
                },
            ),
        )

    capability_fingerprint = runtime_object.capability_fingerprint
    expected_tool = capability.endpoint_ref or f"{capability.owner_cell}:{capability.contract_name}"
    if (
        capability_fingerprint.capability_id != capability.capability_id
        or capability_fingerprint.effect != capability.effect
        or capability_fingerprint.tool != expected_tool
        or invocation.fingerprint_ref != capability_fingerprint.fingerprint
    ):
        return _capability_invocation_failure(
            command,
            error_code="capability_fingerprint_mismatch",
            error_message=f"capability fingerprint does not unlock {capability.capability_id!r}",
            owner_cell=capability.owner_cell,
            metadata={
                "expected_capability_id": capability.capability_id,
                "actual_capability_id": capability_fingerprint.capability_id,
                "expected_effect": capability.effect,
                "actual_effect": capability_fingerprint.effect,
                "expected_tool": expected_tool,
                "actual_tool": capability_fingerprint.tool,
            },
        )

    allowed_payload_refs = _turn_context_payload_refs(runtime_object)
    if invocation.payload_ref not in allowed_payload_refs:
        return _capability_invocation_failure(
            command,
            capability_available=False,
            owner_cell=capability.owner_cell,
            error_code="payload_ref_outside_turn_context",
            error_message="capability invocation payload_ref must match the current RoleTurnContext typed input or task refs",
            metadata={
                "turn_typed_input_ref": runtime_object.turn_context.typed_input_ref,
                "turn_task_refs": runtime_object.turn_context.task_refs,
                "payload_ref": invocation.payload_ref,
            },
        )

    # --- Typed CapabilityHandler dispatch seam --------------------------------
    # After the verbatim cross-cutting prelude guards above, route migrated
    # capability families through the typed registry. ``deps`` bundles the twelve
    # optional service ports; the identity triple is exactly what the legacy
    # ``is_*`` flags reconstruct (capability_id / owner_cell / contract_name).
    # Families with no registered handler fall through to the legacy ``if/elif``
    # ladder below, so this is additive and independently revertible. This single
    # try/except is the reusable catch for EVERY migrated handler.
    from polaris.cells.roles.runtime.internal.capability import (
        CapabilityDeps,
        CapabilityInvocationError,
        default_capability_registry,
    )

    deps = CapabilityDeps(
        task_market_service=task_market_service,
        blueprint_service=blueprint_service,
        code_intelligence_service=code_intelligence_service,
        verification_guard_service=verification_guard_service,
        qa_audit_service=qa_audit_service,
        runtime_projection_service=runtime_projection_service,
        budget_guard_service=budget_guard_service,
        workspace_guard_service=workspace_guard_service,
        permission_service=permission_service,
        architect_design_service=architect_design_service,
        llm_control_plane_service=llm_control_plane_service,
        director_execution_service=director_execution_service,
    )
    capability_identity = (
        capability.capability_id,
        capability.owner_cell,
        capability.contract_name,
    )
    registry = handlers or default_capability_registry()
    handler = registry.lookup(*capability_identity)
    if handler is not None:
        try:
            handler.validate(command)
        except CapabilityInvocationError as exc:
            return _capability_invocation_failure(
                command,
                error_code=exc.code,
                error_message=str(exc),
                owner_cell=exc.owner_cell,
                capability_available=exc.capability_available,
                evidence_refs=exc.evidence_refs,
                metadata=exc.metadata,
            )
        try:
            raw_result = handler.invoke(command, deps)
        except CapabilityInvocationError as exc:
            return _capability_invocation_failure(
                command,
                error_code=exc.code,
                error_message=str(exc),
                owner_cell=exc.owner_cell,
                capability_available=exc.capability_available,
                evidence_refs=exc.evidence_refs,
                metadata=exc.metadata,
            )
        return handler.map_result(raw_result, command)

    # No registered handler for a capability that passed the prelude guards: this is
    # the single coded unknown-capability fallback that replaces the legacy if/elif
    # ladder (now fully migrated to internal/capability/handlers/*). It reproduces the
    # legacy "unsupported_capability_contract" terminal failure byte-for-byte.
    return _capability_invocation_failure(
        command,
        error_code="unsupported_capability_contract",
        error_message=(f"capability {capability.capability_id!r} has no latest-only public invocation adapter"),
        owner_cell=capability.owner_cell,
    )
