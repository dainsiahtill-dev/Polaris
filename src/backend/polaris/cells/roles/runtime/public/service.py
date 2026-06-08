"""Public service exports for `roles.runtime` cell."""

from __future__ import annotations

# ── Standard library imports ─────────────────────────────────────────────────
import argparse
import hashlib
import importlib
import importlib.util
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

# ── Third-party / internal imports (before stdlib per PEP 8 / E402) ──────────
from polaris.cells.roles.engine.public.service import (
    BaseEngine,
    EngineBudget,
    EngineContext,
    EngineRegistry,
    EngineResult,
    EngineStatus,
    EngineStrategy,
    HybridEngine,
    PlanSolveEngine,
    ReActEngine,
    TaskClassifier,
    ToTEngine,
    classify_task,
    create_engine_budget,
    get_engine,
    get_engine_registry,
    get_hybrid_engine,
    get_task_classifier,
    register_engine,
)
from polaris.cells.roles.kernel.public.service import (
    ContextRequest,
    ContextResult,
    RoleContextGateway,
    RoleExecutionKernel,
    RoleToolGateway,
    ToolAuthorizationError,
)
from polaris.cells.roles.profile.public.service import (
    PromptFingerprint,
    RoleContextPolicy,
    RoleDataPolicy,
    RoleExecutionMode,
    RoleLibraryPolicy,
    RoleProfile,
    RoleProfileRegistry,
    RolePromptPolicy,
    RoleToolPolicy,
    RoleTurnRequest,
    RoleTurnResult,
    SequentialConfig,
    SequentialMode,
    SequentialStatsResult,
    SequentialTraceLevel,
    load_core_roles,
    profile_from_dict,
    profile_to_dict,
    registry,
)
from polaris.cells.roles.runtime.internal.agent_runtime_base import (
    AgentMessage,
    AgentState,
    AgentStatus,
    MessageType,
    RoleAgent,
)
from polaris.cells.roles.runtime.internal.kernel_one_bus_port import (
    KernelOneMessageBusPort,
)
from polaris.cells.roles.runtime.internal.protocol_fsm import (
    ProtocolBus,
    ProtocolFSM,
    ProtocolType,
    create_protocol_bus,
    create_protocol_fsm,
)
from polaris.cells.roles.runtime.internal.role_domain_policy import RoleDomainPolicy
from polaris.cells.roles.runtime.internal.sequential_engine import (
    FailureClass,
    RetryHint,
    SeqEventType,
    SeqProgressDetector,
    SeqState,
    SequentialBudget,
    SequentialEngine,
    SequentialStateProxy,
    SequentialStats,
    StepDecision,
    StepResult,
    StepStatus,
    TerminationReason,
    create_sequential_budget,
    emit_seq_event,
    get_seq_emitter,
    should_enable_sequential,
)

# Re-export RoleSessionOrchestrator for Cell boundary compliance
from polaris.cells.roles.runtime.internal.session_orchestrator import (
    RoleSessionOrchestrator as RoleSessionOrchestrator,
)
from polaris.cells.roles.runtime.internal.skill_loader import (
    RoleSkillManager,
    SkillLoader,
    create_role_skill_manager,
    create_skill_loader,
)
from polaris.cells.roles.runtime.internal.worker_pool import (
    AsyncWorker,
    AsyncWorkerConfig,
    AsyncWorkerPool,
    Worker,
    WorkerConfig,
    WorkerPool,
    WorkerResult,
    WorkerState,
    WorkerTask,
    create_async_worker_pool,
    create_worker_pool,
)

# Wave 3: CLI runner module extracted to public/cli_runner.py
from polaris.cells.roles.runtime.public.cli_runner import CliRunner

# Wave 2: Context adapter module extracted to public/context_adapter.py
from polaris.cells.roles.runtime.public.context_adapter import (
    augment_context_with_handoff_rehydration as _augment_context_with_handoff_rehydration_impl,
    augment_context_with_repo_intelligence as _augment_context_with_repo_intelligence_impl,
    load_session_context_os_snapshot as _load_session_context_os_snapshot_impl,
)
from polaris.cells.roles.runtime.public.contracts import (
    AggregateChatChoiceV1,
    AggregateChatCompletionsCommandV1,
    AggregateChatCompletionsResultV1,
    AggregateChatMessageV1,
    AggregateCognitiveLedgerEntryV1,
    AggregateRoleLobeV1,
    AggregateRolePlanResultV1,
    AggregateRuntimeAuditResultV1,
    AggregateRuntimeEntrypointCheckV1,
    AggregateRuntimeIntegrationV1,
    AggregateTakeoverDirectiveV1,
    AssembleRoleRuntimeChainCommandV1,
    AuditAggregateRuntimeIntegrationsQueryV1,
    BuildAggregateRolePlanQueryV1,
    ExecuteRoleCapabilityInvocationCommandV1,
    ExecuteRoleSessionCommandV1,
    ExecuteRoleTaskCommandV1,
    ExecuteRoleTaskMarketLifecycleCommandV1,
    GetRoleRuntimeStatusQueryV1,
    InstantiateRoleRuntimeObjectCommandV1,
    IRoleRuntime,
    RoleCapabilityDescriptor,
    RoleCapabilityInvocationResultV1,
    RoleExecutionResultV1,
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

# Wave 1: Persistence module extracted to public/persistence.py
from polaris.cells.roles.runtime.public.persistence import (
    emit_strategy_receipt as _emit_strategy_receipt_impl,
    persist_session_turn_state as _persist_session_turn_state_impl,
    project_host_history as _project_host_history_impl,
    resolve_session_override as _resolve_session_override_impl,
)
from polaris.kernelone.context import (
    ContextBudget,
    ResolvedStrategy,
    StrategyRunContext,
    get_registry,
)
from polaris.kernelone.context.runtime_feature_flags import (
    CognitiveRuntimeMode,
    resolve_cognitive_runtime_mode,
    resolve_context_os_enabled,
)

# Skill system: prefer KernelOne implementation, keep Cells layer for backward compat
from polaris.kernelone.telemetry.debug_stream import emit_debug_event

if TYPE_CHECKING:
    from pathlib import Path

    from polaris.cells.roles.session.public import (
        PathSecurityError,
        RoleDataStore,
        RoleDataStoreError,
    )

logger = logging.getLogger(__name__)

_AGGREGATE_MODEL_ID = "polaris.aggregate_llm.v1"
_DEFAULT_AGGREGATE_ROLE_IDS: tuple[str, ...] = (
    "pm",
    "architect",
    "chief_engineer",
    "director",
    "qa",
)
_AGGREGATE_FAILURE_SIGNAL_ALIASES: dict[str, str] = {
    "compile": "compile_failure",
    "compiler_failure": "compile_failure",
    "compile_error": "compile_failure",
    "tsc_failure": "typecheck_failure",
    "type_error": "typecheck_failure",
    "typecheck": "typecheck_failure",
    "apply_failure": "failed_apply",
    "patch_apply_failure": "failed_apply",
    "repo_map_empty": "empty_repo_map",
    "localization": "localization_uncertain",
}
_AGGREGATE_FAILURE_EVIDENCE_KEYS: dict[str, tuple[str, ...]] = {
    "compile_failure": ("compiler_output", "changed_files", "test_command"),
    "typecheck_failure": ("typecheck_output", "tsconfig_path", "changed_files"),
    "failed_apply": ("patch_payload", "apply_error", "target_paths"),
    "localization_uncertain": ("repo_map", "mentioned_symbols", "candidate_files"),
    "degraded_signal": ("degraded_reason", "cognitive_runtime_preflight", "session_id"),
    "empty_repo_map": ("repo_intelligence_result", "workspace", "language_filter"),
    "graph_boundary_violation": ("cell_id", "target_paths", "graph_edge"),
}


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
        if not ref or ref in seen:
            continue
        if ref == "audit.evidence" or ref.startswith("audit.evidence:"):
            refs.append(ref)
            seen.add(ref)
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


def _profile_policy_ref(role_id: str, policy_name: str, profile_fingerprint: str) -> str:
    return f"roles.profile:{role_id}:{policy_name}:{profile_fingerprint}"


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
                **dict(command.metadata),
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

    return RoleRuntimeObjectResultV1(
        ok=True,
        role_id=spec.role_id,
        runtime_object=runtime_object,
        profile_ref=profile_ref,
        metadata={
            "profile_ref": profile_ref,
            "default_capability_id": spec.default_capability_id,
            "capability_id": runtime_object.capability_fingerprint.capability_id,
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
    if operation in {"lease", "ack", "fail", "requeue"}:
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
        else:
            task_command = RequeueTaskCommandV1(
                workspace=workspace,
                task_id=_payload_string(command.payload, "task_id"),
                target_stage=_payload_string(command.payload, "target_stage"),
                reason=_payload_string(command.payload, "reason"),
                metadata=metadata,
            )
            result = service.requeue_task(task_command)
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


def _role_runtime_chain_ref(chain_id: str) -> str:
    return f"roles.runtime:chain:{chain_id}"


_FULL_PHASE5_REQUIRED_ROLES = ("pm", "chief_engineer", "director", "qa")
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
    task_market_service: Any | None = None,
    blueprint_service: Any | None = None,
    code_intelligence_service: Any | None = None,
    verification_guard_service: Any | None = None,
    qa_audit_service: Any | None = None,
    runtime_projection_service: Any | None = None,
    budget_guard_service: Any | None = None,
    workspace_guard_service: Any | None = None,
    permission_service: Any | None = None,
    architect_design_service: Any | None = None,
    llm_control_plane_service: Any | None = None,
    director_execution_service: Any | None = None,
) -> RoleCapabilityInvocationResultV1:
    """Execute a mounted role capability through its declared public contract."""
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

    is_not_task_market_dispatch = (
        capability.capability_id != "dispatch_task_to_market"
        or capability.owner_cell != "runtime.task_market"
        or capability.contract_name != "PublishTaskWorkItemCommandV1"
    )
    is_pm_critical_path = (
        capability.capability_id == "evaluate_critical_path"
        and capability.owner_cell == "runtime.task_market"
        and capability.contract_name == "QueryTaskMarketStatusV1"
    )
    is_pm_runtime_projection = (
        capability.capability_id == "project_runtime_status"
        and capability.owner_cell == "runtime.projection"
        and capability.contract_name == "RuntimeProjectionQueryV1"
    )
    is_blueprint_generation = (
        capability.capability_id in {"generate_diff_specification", "record_arch_memo"}
        and capability.owner_cell == "chief_engineer.blueprint"
        and capability.contract_name == "GenerateTaskBlueprintCommandV1"
    )
    is_ce_ast_dependency = (
        capability.capability_id == "verify_ast_dependency"
        and capability.owner_cell == "code_intelligence.engine"
        and capability.contract_name == "VerifyAstDependencyQueryV1"
    )
    is_qa_audit_verdict = (
        capability.capability_id == "issue_audit_verdict"
        and capability.owner_cell == "qa.audit_verdict"
        and capability.contract_name == "RunQaAuditCommandV1"
    )
    is_qa_traceback_parse = (
        capability.capability_id == "parse_traceback_frames"
        and capability.owner_cell == "qa.audit_verdict"
        and capability.contract_name == "ParseTracebackFramesCommandV1"
    )
    is_architect_budget_reservation = (
        capability.capability_id == "allocate_context_token_budget"
        and capability.owner_cell == "finops.budget_guard"
        and capability.contract_name == "ReserveBudgetCommandV1"
    )
    is_architect_workspace_guard = (
        capability.capability_id == "intercept_illegal_mutations"
        and capability.owner_cell == "policy.workspace_guard"
        and capability.contract_name == "WorkspaceWriteGuardQueryV1"
    )
    is_architect_boundary_validation = (
        capability.capability_id == "validate_cell_boundary_change"
        and capability.owner_cell == "architect.design"
        and capability.contract_name == "GenerateArchitectureDesignCommandV1"
    )
    is_director_task_execution = (
        capability.capability_id == "execute_director_task"
        and capability.owner_cell == "director.execution"
        and capability.contract_name == "ExecuteDirectorTaskCommandV1"
    )

    if is_architect_budget_reservation:
        budget_metadata = _payload_mapping(command.payload, "metadata")
        if budget_metadata is None:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_budget_metadata",
                error_message="payload.metadata must be a mapping when provided",
            )
        budget_metadata.update(
            {
                "role_invocation_id": invocation.invocation_id,
                "role_payload_ref": invocation.payload_ref,
                "role_fingerprint_ref": invocation.fingerprint_ref,
                "role_capability_id": capability.capability_id,
            }
        )
        try:
            token_budget = int(command.payload.get("token_budget", command.payload.get("context_token_budget", 0)))
            from polaris.cells.finops.budget_guard.public.contracts import ReserveBudgetCommandV1
            from polaris.cells.finops.budget_guard.public.service import reserve_budget

            reserve_command = ReserveBudgetCommandV1(
                scope_id=_payload_string(command.payload, "scope_id", invocation.invocation_id),
                workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
                role=role_id,
                token_budget=token_budget,
                metadata=budget_metadata,
            )
        except (TypeError, ValueError) as exc:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_budget_command",
                error_message=str(exc),
            )

        try:
            if budget_guard_service is None:
                budget_result = reserve_budget(reserve_command)
            else:
                budget_result = budget_guard_service.reserve_budget(reserve_command)
        except Exception as exc:  # noqa: BLE001 - public RPC boundary returns structured failure
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="budget_guard_failed",
                error_message=str(exc),
            )

        result_ref = f"finops.budget_guard:budget:{reserve_command.scope_id}"
        metadata = _capability_available_metadata(
            capability.capability_id,
            {
                "budget_allowed": budget_result.allowed,
                "remaining_tokens": budget_result.remaining_tokens,
                "estimated_cost_usd": budget_result.estimated_cost_usd,
                "reason": budget_result.reason,
            },
        )
        if not budget_result.allowed:
            return RoleCapabilityInvocationResultV1(
                ok=False,
                invocation_id=invocation.invocation_id,
                role_id=role_id,
                capability_id=capability.capability_id,
                command_contract=capability.contract_name,
                allowed=False,
                owner_cell=capability.owner_cell,
                payload_ref=result_ref,
                result_ref=result_ref,
                task_id=runtime_object.identity.task_id or "",
                status="DENIED",
                metadata=metadata,
                error_code="budget_denied",
                error_message=budget_result.reason or "budget reservation denied",
            )
        return RoleCapabilityInvocationResultV1(
            ok=True,
            invocation_id=invocation.invocation_id,
            role_id=role_id,
            capability_id=capability.capability_id,
            command_contract=capability.contract_name,
            allowed=True,
            owner_cell=capability.owner_cell,
            payload_ref=result_ref,
            result_ref=result_ref,
            task_id=runtime_object.identity.task_id or "",
            status="RESERVED",
            metadata=metadata,
        )

    if is_architect_workspace_guard:
        target_path = _payload_string(command.payload, "path")
        operation = _payload_string(command.payload, "operation", "write")
        if not target_path:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_workspace_guard_path",
                error_message="payload.path must be a non-empty string",
            )
        try:
            from polaris.cells.policy.workspace_guard.public.contracts import WorkspaceWriteGuardQueryV1
            from polaris.cells.policy.workspace_guard.public.service import check_workspace_write_guard

            guard_query = WorkspaceWriteGuardQueryV1(
                path=target_path,
                operation=operation,
            )
        except (TypeError, ValueError) as exc:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_workspace_guard_query",
                error_message=str(exc),
            )

        try:
            if workspace_guard_service is None:
                guard_result = check_workspace_write_guard(guard_query)
            else:
                guard_result = workspace_guard_service.check_workspace_write_guard(guard_query)
        except Exception as exc:  # noqa: BLE001 - public RPC boundary returns structured failure
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="workspace_guard_failed",
                error_message=str(exc),
            )

        result_ref = f"policy.workspace_guard:decision:{invocation.invocation_id}"
        metadata = {
            "capability_available": True,
            "mutation_allowed": guard_result.allowed,
            "guard_reason": guard_result.reason,
            "path": guard_query.path,
            "operation": guard_query.operation,
        }
        if not guard_result.allowed:
            return RoleCapabilityInvocationResultV1(
                ok=False,
                invocation_id=invocation.invocation_id,
                role_id=role_id,
                capability_id=capability.capability_id,
                command_contract=capability.contract_name,
                allowed=False,
                owner_cell=capability.owner_cell,
                payload_ref=result_ref,
                result_ref=result_ref,
                task_id=runtime_object.identity.task_id or "",
                status="DENIED",
                metadata=metadata,
                error_code="workspace_guard_denied",
                error_message=guard_result.reason or "workspace guard denied mutation",
            )
        return RoleCapabilityInvocationResultV1(
            ok=True,
            invocation_id=invocation.invocation_id,
            role_id=role_id,
            capability_id=capability.capability_id,
            command_contract=capability.contract_name,
            allowed=True,
            owner_cell=capability.owner_cell,
            payload_ref=result_ref,
            result_ref=result_ref,
            task_id=runtime_object.identity.task_id or "",
            status="ALLOWED",
            metadata=metadata,
        )

    if is_architect_boundary_validation:
        boundary_context = _payload_mapping(command.payload, "context")
        if boundary_context is None:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_architect_boundary_context",
                error_message="payload.context must be a mapping when provided",
            )
        boundary_constraints = _payload_mapping(command.payload, "constraints")
        if boundary_constraints is None:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_architect_boundary_constraints",
                error_message="payload.constraints must be a mapping when provided",
            )
        changed_paths = _payload_string_tuple(command.payload, "changed_paths")
        if changed_paths is None:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_architect_boundary_changed_paths",
                error_message="payload.changed_paths must be a sequence of strings when provided",
            )
        if not changed_paths:
            return _capability_invocation_failure(
                command,
                capability_available=False,
                owner_cell=capability.owner_cell,
                error_code="invalid_architect_boundary_changed_paths",
                error_message="payload.changed_paths must include at least one changed path",
                metadata=_capability_available_metadata(
                    capability.capability_id,
                    {"required_field": "changed_paths"},
                ),
            )
        target_cell = _payload_string(command.payload, "target_cell")
        if not target_cell:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_architect_boundary_target_cell",
                error_message="payload.target_cell must be a non-empty string",
            )

        permission_context = {
            "resource_type": "api",
            "task_id": runtime_object.identity.task_id or "",
            "session_id": runtime_object.identity.session_id or "",
            "request_id": invocation.invocation_id,
            "capability_id": capability.capability_id,
            "target_cell": target_cell,
            "role_payload_ref": invocation.payload_ref,
            "role_fingerprint_ref": invocation.fingerprint_ref,
        }
        try:
            from polaris.cells.policy.permission.public.contracts import EvaluatePermissionCommandV1
            from polaris.cells.policy.permission.public.service import evaluate_permission

            permission_command = EvaluatePermissionCommandV1(
                role=role_id,
                action="execute",
                resource="architect.design:validate_cell_boundary_change",
                workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
                context=permission_context,
            )
        except (TypeError, ValueError) as exc:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_permission_command",
                error_message=str(exc),
            )

        try:
            if permission_service is None:
                permission_result = evaluate_permission(permission_command)
            else:
                permission_result = permission_service.evaluate_permission(permission_command)
        except Exception as exc:  # noqa: BLE001 - public RPC boundary returns structured failure
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="permission_evaluation_failed",
                error_message=str(exc),
            )

        permission_metadata = {
            "permission_allowed": permission_result.allowed,
            "permission_reason": permission_result.reason,
            "permission_matched_policy": permission_result.matched_policy or "",
        }
        if not permission_result.allowed:
            return _capability_invocation_failure(
                command,
                capability_available=False,
                owner_cell=capability.owner_cell,
                error_code="permission_denied",
                error_message=permission_result.reason or "permission denied",
                metadata=_capability_available_metadata(capability.capability_id, permission_metadata),
            )

        try:
            guard_allowed, checked_paths, denied_path, guard_reason = _check_workspace_guard_paths(
                paths=changed_paths,
                operation=_payload_string(command.payload, "operation", "write"),
                workspace_guard_service=workspace_guard_service,
            )
        except Exception as exc:  # noqa: BLE001 - public RPC boundary returns structured failure
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="workspace_guard_failed",
                error_message=str(exc),
            )
        guard_metadata = {
            **permission_metadata,
            "workspace_guard_allowed": guard_allowed,
            "checked_paths": checked_paths,
            "denied_path": denied_path,
            "guard_reason": guard_reason,
        }
        if not guard_allowed:
            return _capability_invocation_failure(
                command,
                capability_available=False,
                owner_cell=capability.owner_cell,
                error_code="workspace_guard_denied",
                error_message=guard_reason or "workspace guard denied mutation",
                metadata=_capability_available_metadata(capability.capability_id, guard_metadata),
            )

        boundary_context.update(
            {
                "target_cell": target_cell,
                "changed_paths": changed_paths,
                "role_invocation_id": invocation.invocation_id,
                "role_payload_ref": invocation.payload_ref,
                "role_fingerprint_ref": invocation.fingerprint_ref,
                "role_capability_id": capability.capability_id,
                "permission_ref": "policy.permission:decision",
                "workspace_guard_ref": "policy.workspace_guard:decision",
            }
        )
        try:
            from polaris.cells.architect.design.public.contracts import GenerateArchitectureDesignCommandV1
            from polaris.cells.architect.design.public.service import generate_architecture_design

            design_command = GenerateArchitectureDesignCommandV1(
                workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
                objective=_payload_string(command.payload, "objective"),
                constraints=boundary_constraints,
                context=boundary_context,
            )
        except (TypeError, ValueError) as exc:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_architect_design_command",
                error_message=str(exc),
            )

        timeout_seconds = float(command.payload.get("timeout_seconds", 30.0))
        try:
            if architect_design_service is None:
                design_result = _run_with_timeout(
                    lambda: generate_architecture_design(design_command),
                    timeout_seconds,
                )
            else:
                design_result = _run_with_timeout(
                    lambda: architect_design_service.generate_architecture_design(design_command),
                    timeout_seconds,
                )
        except FutureTimeoutError:
            return _capability_invocation_failure(
                command,
                capability_available=False,
                owner_cell=capability.owner_cell,
                error_code="architect_design_timeout",
                error_message=f"architect design timed out after {timeout_seconds:g}s",
                metadata=_capability_available_metadata(capability.capability_id, guard_metadata),
            )
        except Exception as exc:  # noqa: BLE001 - public RPC boundary returns structured failure
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="architect_design_failed",
                error_message=str(exc),
            )

        result_ref = f"architect.design:boundary-validation:{design_result.design_id}"
        metadata = {
            **guard_metadata,
            "design_id": design_result.design_id,
            "summary": design_result.summary,
            "recommendation_paths": tuple(design_result.recommendation_paths),
        }
        if not design_result.ok:
            return RoleCapabilityInvocationResultV1(
                ok=False,
                invocation_id=invocation.invocation_id,
                role_id=role_id,
                capability_id=capability.capability_id,
                command_contract=capability.contract_name,
                allowed=False,
                owner_cell=capability.owner_cell,
                payload_ref=result_ref,
                result_ref=result_ref,
                task_id=runtime_object.identity.task_id or "",
                status=design_result.status,
                metadata=metadata,
                error_code="architect_design_rejected",
                error_message=design_result.summary or "architect design rejected boundary change",
            )
        return RoleCapabilityInvocationResultV1(
            ok=True,
            invocation_id=invocation.invocation_id,
            role_id=role_id,
            capability_id=capability.capability_id,
            command_contract=capability.contract_name,
            allowed=True,
            owner_cell=capability.owner_cell,
            payload_ref=result_ref,
            result_ref=result_ref,
            task_id=runtime_object.identity.task_id or "",
            status=design_result.status,
            metadata=metadata,
        )

    if is_pm_critical_path:
        try:
            from polaris.cells.runtime.task_market.public import (
                QueryTaskMarketStatusV1,
                get_task_market_service,
            )

            query = QueryTaskMarketStatusV1(
                workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
                stage=_payload_string(command.payload, "stage") or None,
                status=_payload_string(command.payload, "status") or None,
                limit=int(command.payload.get("limit", 200)),
                include_payload=bool(command.payload.get("include_payload", True)),
            )
        except (TypeError, ValueError) as exc:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_task_market_status_query",
                error_message=str(exc),
            )

        service = task_market_service or get_task_market_service()
        try:
            status_result = service.query_status(query)
        except Exception as exc:  # noqa: BLE001 - public RPC boundary returns structured failure
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="task_market_status_query_failed",
                error_message=str(exc),
            )

        terminal_statuses = {"resolved", "completed", "acknowledged", "cancelled", "superseded"}
        blocked_statuses = {"failed", "dead_letter", "blocked", "cancel_requested", "needs_revalidation"}
        open_items = tuple(
            item for item in status_result.items if str(item.get("status") or "").lower() not in terminal_statuses
        )
        blocked_task_ids = tuple(
            str(item.get("task_id") or "").strip()
            for item in open_items
            if str(item.get("status") or "").lower() in blocked_statuses and str(item.get("task_id") or "").strip()
        )
        open_task_ids = tuple(
            str(item.get("task_id") or "").strip() for item in open_items if str(item.get("task_id") or "").strip()
        )
        dependency_edges = tuple(
            {"task_id": task_id, "depends_on": depends_on}
            for item in status_result.items
            if (task_id := str(item.get("task_id") or "").strip())
            if (depends_on := _mapping_string_tuple(item, "depends_on"))
        )
        failed_stages = tuple(
            {
                "task_id": task_id,
                "stage": failed_stage,
                "reason": str(item.get("failure_reason") or item.get("reason") or "").strip(),
            }
            for item in status_result.items
            if (task_id := str(item.get("task_id") or "").strip())
            if (failed_stage := str(item.get("failed_stage") or item.get("stage") or "").strip())
            if str(item.get("status") or "").lower() in blocked_statuses
        )
        projection_refs = tuple(
            ref
            for item in status_result.items
            if (ref := str(item.get("projection_ref") or item.get("runtime_projection_ref") or "").strip())
        )
        asset_refs = {
            "task_graph": _asset_mount_ref(runtime_object, "TaskGraph"),
            "runtime_projection_state": _asset_mount_ref(runtime_object, "RuntimeProjectionState"),
            "open_loop_registry": _asset_mount_ref(runtime_object, "OpenLoopRegistry"),
        }
        result_ref = f"runtime.task_market:critical-path:{invocation.invocation_id}"
        return RoleCapabilityInvocationResultV1(
            ok=True,
            invocation_id=invocation.invocation_id,
            role_id=role_id,
            capability_id=capability.capability_id,
            command_contract=capability.contract_name,
            allowed=True,
            owner_cell=capability.owner_cell,
            payload_ref=result_ref,
            result_ref=result_ref,
            task_id=runtime_object.identity.task_id or "",
            status="EVALUATED",
            metadata={
                "total_tasks": status_result.total,
                "counts": dict(status_result.counts),
                "open_task_ids": open_task_ids,
                "blocked_task_ids": blocked_task_ids,
                "open_task_count": len(open_task_ids),
                "blocked_task_count": len(blocked_task_ids),
                "dependency_edges": dependency_edges,
                "failed_stages": failed_stages,
                "projection_refs": projection_refs,
                "asset_refs": asset_refs,
            },
        )

    if is_pm_runtime_projection:
        try:
            from polaris.cells.runtime.projection.public.contracts import RuntimeProjectionQueryV1

            projection_query = RuntimeProjectionQueryV1(scope=_payload_string(command.payload, "scope", "runtime"))
        except (TypeError, ValueError) as exc:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_runtime_projection_query",
                error_message=str(exc),
            )

        if runtime_projection_service is None:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="runtime_projection_service_unavailable",
                error_message="runtime.projection query service must be injected by the host boundary",
            )
        try:
            projection_result = runtime_projection_service.query_runtime_projection(projection_query)
        except Exception as exc:  # noqa: BLE001 - public RPC boundary returns structured failure
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="runtime_projection_query_failed",
                error_message=str(exc),
            )

        result_ref = f"runtime.projection:{projection_query.scope}:{invocation.invocation_id}"
        return RoleCapabilityInvocationResultV1(
            ok=True,
            invocation_id=invocation.invocation_id,
            role_id=role_id,
            capability_id=capability.capability_id,
            command_contract=capability.contract_name,
            allowed=True,
            owner_cell=capability.owner_cell,
            payload_ref=result_ref,
            result_ref=result_ref,
            task_id=runtime_object.identity.task_id or "",
            status="PROJECTED",
            metadata={"projection": dict(projection_result.payload), "scope": projection_query.scope},
        )

    if is_ce_ast_dependency:
        ast_metadata = _payload_mapping(command.payload, "metadata")
        if ast_metadata is None:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_ast_dependency_metadata",
                error_message="payload.metadata must be a mapping when provided",
            )
        ast_metadata.update(
            {
                "role_invocation_id": invocation.invocation_id,
                "role_payload_ref": invocation.payload_ref,
                "role_fingerprint_ref": invocation.fingerprint_ref,
                "role_capability_id": capability.capability_id,
            }
        )
        try:
            from polaris.cells.code_intelligence.engine.public.contracts import VerifyAstDependencyQueryV1
            from polaris.cells.code_intelligence.engine.public.service import verify_ast_dependency

            ast_query = VerifyAstDependencyQueryV1(
                workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
                path=_payload_string(command.payload, "path") or _payload_string(command.payload, "file"),
                language=_payload_string(command.payload, "language"),
                symbol=_payload_string(command.payload, "symbol") or _payload_string(command.payload, "name"),
                kind=_payload_string(command.payload, "kind") or None,
                max_results=int(command.payload.get("max_results", 10)),
                context_radius=int(command.payload.get("context_radius", 5)),
                fuzzy=bool(command.payload.get("fuzzy", True)),
                metadata=ast_metadata,
            )
        except (TypeError, ValueError) as exc:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_ast_dependency_query",
                error_message=str(exc),
            )

        try:
            if code_intelligence_service is None:
                ast_result = verify_ast_dependency(ast_query)
            else:
                ast_result = code_intelligence_service.verify_ast_dependency(ast_query)
        except Exception as exc:  # noqa: BLE001 - public RPC boundary returns structured failure
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="ast_dependency_verification_failed",
                error_message=str(exc),
            )

        result_ref = f"code_intelligence.engine:ast-dependency:{invocation.invocation_id}"
        metadata = _capability_available_metadata(
            capability.capability_id,
            {
                "workspace": ast_result.workspace,
                "path": ast_result.path,
                "language": ast_result.language,
                "symbol": ast_result.symbol,
                "engine": ast_result.engine,
                "result_count": ast_result.result_count,
                "results": tuple(dict(item) for item in ast_result.results),
                "warnings": tuple(ast_result.warnings),
            },
        )
        if not ast_result.ok:
            return RoleCapabilityInvocationResultV1(
                ok=False,
                invocation_id=invocation.invocation_id,
                role_id=role_id,
                capability_id=capability.capability_id,
                command_contract=capability.contract_name,
                allowed=False,
                owner_cell=capability.owner_cell,
                payload_ref=result_ref,
                result_ref=result_ref,
                task_id=runtime_object.identity.task_id or "",
                status="FAILED",
                metadata=metadata,
                error_code="ast_dependency_verification_failed",
                error_message=ast_result.error or "AST dependency verification failed",
            )
        return RoleCapabilityInvocationResultV1(
            ok=True,
            invocation_id=invocation.invocation_id,
            role_id=role_id,
            capability_id=capability.capability_id,
            command_contract=capability.contract_name,
            allowed=True,
            owner_cell=capability.owner_cell,
            payload_ref=result_ref,
            result_ref=result_ref,
            task_id=runtime_object.identity.task_id or "",
            status="VERIFIED" if ast_result.result_count else "NO_MATCH",
            metadata=metadata,
        )

    if is_qa_traceback_parse:
        traceback_metadata = _payload_mapping(command.payload, "metadata")
        if traceback_metadata is None:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_traceback_metadata",
                error_message="payload.metadata must be a mapping when provided",
            )
        traceback_text = _payload_string(command.payload, "traceback_text")
        if not traceback_text:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_traceback_text",
                error_message="payload.traceback_text must be a non-empty string",
            )
        traceback_metadata.update(
            {
                "role_invocation_id": invocation.invocation_id,
                "role_payload_ref": invocation.payload_ref,
                "role_fingerprint_ref": invocation.fingerprint_ref,
                "role_capability_id": capability.capability_id,
            }
        )
        try:
            from polaris.cells.qa.audit_verdict.public.contracts import ParseTracebackFramesCommandV1
            from polaris.cells.qa.audit_verdict.public.service import parse_traceback_frames

            parse_command = ParseTracebackFramesCommandV1(
                task_id=_payload_string(command.payload, "task_id", runtime_object.identity.task_id or ""),
                workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
                traceback_text=traceback_text,
                run_id=_payload_string(command.payload, "run_id", runtime_object.identity.run_id or "") or None,
                metadata=traceback_metadata,
            )
        except (TypeError, ValueError) as exc:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_traceback_parse_command",
                error_message=str(exc),
            )

        try:
            if qa_audit_service is None:
                parse_result = parse_traceback_frames(parse_command)
            else:
                parse_result = qa_audit_service.parse_traceback_frames(parse_command)
        except Exception as exc:  # noqa: BLE001 - public RPC boundary returns structured failure
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="traceback_parse_failed",
                error_message=str(exc),
            )

        signal = parse_result.signal
        result_ref = f"qa.audit_verdict:failure-signal:{signal.signal_id}"
        metadata = _capability_available_metadata(
            capability.capability_id,
            {
                "signal_id": signal.signal_id,
                "signal_type": signal.signal_type,
                "summary": signal.summary,
                "severity": signal.severity,
                "source": signal.source,
                "frame_count": parse_result.frame_count,
                "frames": tuple(
                    {
                        "path": frame.path,
                        "line": frame.line,
                        "function": frame.function,
                        "code": frame.code,
                    }
                    for frame in signal.frames
                ),
            },
        )
        if not parse_result.ok:
            return RoleCapabilityInvocationResultV1(
                ok=False,
                invocation_id=invocation.invocation_id,
                role_id=role_id,
                capability_id=capability.capability_id,
                command_contract=capability.contract_name,
                allowed=False,
                owner_cell=capability.owner_cell,
                payload_ref=result_ref,
                result_ref=result_ref,
                task_id=parse_result.task_id,
                status="REJECTED",
                metadata=metadata,
                error_code="traceback_parse_rejected",
                error_message=signal.summary,
            )
        return RoleCapabilityInvocationResultV1(
            ok=True,
            invocation_id=invocation.invocation_id,
            role_id=role_id,
            capability_id=capability.capability_id,
            command_contract=capability.contract_name,
            allowed=True,
            owner_cell=capability.owner_cell,
            payload_ref=result_ref,
            result_ref=result_ref,
            task_id=parse_result.task_id,
            status="PARSED",
            metadata=metadata,
        )

    if is_qa_audit_verdict:
        audit_criteria = _payload_mapping(command.payload, "criteria")
        if audit_criteria is None:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_qa_audit_criteria",
                error_message="payload.criteria must be a mapping when provided",
            )
        evidence_paths = _payload_string_tuple(command.payload, "evidence_paths")
        if evidence_paths is None:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_qa_audit_evidence_paths",
                error_message="payload.evidence_paths must be a sequence of strings when provided",
            )
        audit_criteria.update(
            {
                "role_invocation_id": invocation.invocation_id,
                "role_payload_ref": invocation.payload_ref,
                "role_fingerprint_ref": invocation.fingerprint_ref,
                "role_capability_id": capability.capability_id,
            }
        )
        try:
            from polaris.cells.qa.audit_verdict.public.contracts import RunQaAuditCommandV1
            from polaris.cells.qa.audit_verdict.public.service import run_qa_audit

            audit_command = RunQaAuditCommandV1(
                task_id=_payload_string(command.payload, "task_id", runtime_object.identity.task_id or ""),
                workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
                run_id=_payload_string(command.payload, "run_id", runtime_object.identity.run_id or "") or None,
                criteria=audit_criteria,
                evidence_paths=evidence_paths,
            )
        except (TypeError, ValueError) as exc:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_qa_audit_command",
                error_message=str(exc),
            )

        try:
            if qa_audit_service is None:
                audit_result = run_qa_audit(audit_command)
            else:
                audit_result = qa_audit_service.run_qa_audit(audit_command)
        except Exception as exc:  # noqa: BLE001 - public RPC boundary returns structured failure
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="qa_audit_failed",
                error_message=str(exc),
            )

        result_ref = f"qa.audit_verdict:verdict:{audit_result.task_id}"
        audit_evidence_refs = _audit_evidence_refs(evidence_paths)
        metadata = _capability_available_metadata(
            capability.capability_id,
            {
                "verdict": audit_result.verdict,
                "score": audit_result.score,
                "findings": tuple(audit_result.findings),
                "suggestions": tuple(audit_result.suggestions),
                "evidence_paths": evidence_paths,
                "audit_evidence_refs": audit_evidence_refs,
            },
        )
        if not audit_result.ok:
            return RoleCapabilityInvocationResultV1(
                ok=False,
                invocation_id=invocation.invocation_id,
                role_id=role_id,
                capability_id=capability.capability_id,
                command_contract=capability.contract_name,
                allowed=False,
                owner_cell=capability.owner_cell,
                payload_ref=result_ref,
                result_ref=result_ref,
                task_id=audit_result.task_id,
                status=audit_result.verdict,
                evidence_refs=audit_evidence_refs,
                metadata=metadata,
                error_code="qa_audit_rejected",
                error_message="; ".join(audit_result.findings) or "QA audit rejected the task",
            )
        return RoleCapabilityInvocationResultV1(
            ok=True,
            invocation_id=invocation.invocation_id,
            role_id=role_id,
            capability_id=capability.capability_id,
            command_contract=capability.contract_name,
            allowed=True,
            owner_cell=capability.owner_cell,
            payload_ref=result_ref,
            result_ref=result_ref,
            task_id=audit_result.task_id,
            status=audit_result.verdict,
            evidence_refs=audit_evidence_refs,
            metadata=metadata,
        )

    if is_qa_visual_audit_verdict:
        image_refs = _payload_string_tuple(command.payload, "image_refs")
        if image_refs is None or not image_refs:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_visual_audit_image_refs",
                error_message="payload.image_refs must be a non-empty sequence of image evidence refs",
            )
        visual_criteria = _payload_mapping(command.payload, "criteria")
        if visual_criteria is None:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_visual_audit_criteria",
                error_message="payload.criteria must be a mapping when provided",
            )
        evidence_paths = _payload_string_tuple(command.payload, "evidence_paths")
        if evidence_paths is None:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_visual_audit_evidence_paths",
                error_message="payload.evidence_paths must be a sequence of strings when provided",
            )
        required_model_capability = _normalize_model_capability(
            capability.metadata.get("required_model_capability"),
            "image_input",
        )
        requested_model_capability = _payload_string(command.payload, "required_model_capability")
        normalized_requested_model_capability = _normalize_model_capability(requested_model_capability, "")
        if normalized_requested_model_capability and normalized_requested_model_capability != required_model_capability:
            return _capability_invocation_failure(
                command,
                capability_available=False,
                owner_cell="llm.control_plane",
                error_code="visual_model_capability_override_denied",
                error_message=(
                    "visual QA audit requires "
                    f"{required_model_capability!r}; payload requested "
                    f"{normalized_requested_model_capability!r}"
                ),
                metadata=_capability_available_metadata(
                    capability.capability_id,
                    {
                        "required_capability": required_model_capability,
                        "requested_capability": normalized_requested_model_capability,
                    },
                ),
            )
        try:
            from polaris.cells.llm.control_plane.public.contracts import CheckLlmModelCapabilityQueryV1
            from polaris.cells.llm.control_plane.public.service import check_llm_model_capability

            model_query = CheckLlmModelCapabilityQueryV1(
                workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
                role=role_id,
                capability=required_model_capability,
                model=_payload_string(command.payload, "model") or None,
                metadata={
                    "role_invocation_id": invocation.invocation_id,
                    "role_payload_ref": invocation.payload_ref,
                    "role_fingerprint_ref": invocation.fingerprint_ref,
                    "role_capability_id": capability.capability_id,
                    "payload_llm_role": _payload_string(command.payload, "llm_role"),
                },
            )
        except (TypeError, ValueError) as exc:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_visual_model_capability_query",
                error_message=str(exc),
            )

        try:
            if llm_control_plane_service is None:
                model_capability = check_llm_model_capability(model_query)
            else:
                model_capability = llm_control_plane_service.check_model_capability(model_query)
        except Exception as exc:  # noqa: BLE001 - public RPC boundary returns structured failure
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell="llm.control_plane",
                error_code="visual_model_capability_check_failed",
                error_message=str(exc),
            )

        model_metadata = {
            "model_capability_supported": bool(getattr(model_capability, "supported", False)),
            "required_capability": model_query.capability,
            "model_capability_ref": getattr(model_capability, "capability_ref", ""),
            "model_provider_id": getattr(model_capability, "provider_id", ""),
            "model": getattr(model_capability, "model", ""),
            "model_reason": getattr(model_capability, "reason", ""),
        }
        if not bool(getattr(model_capability, "ok", False)) or not bool(getattr(model_capability, "supported", False)):
            return _capability_invocation_failure(
                command,
                capability_available=False,
                owner_cell="llm.control_plane",
                error_code="visual_model_capability_missing",
                error_message=getattr(model_capability, "reason", "")
                or "configured model does not support image_input",
                metadata=_capability_available_metadata(capability.capability_id, model_metadata),
            )

        visual_criteria.update(
            {
                "role_invocation_id": invocation.invocation_id,
                "role_payload_ref": invocation.payload_ref,
                "role_fingerprint_ref": invocation.fingerprint_ref,
                "role_capability_id": capability.capability_id,
                "model_provider_id": getattr(model_capability, "provider_id", ""),
                "model": getattr(model_capability, "model", ""),
            }
        )
        try:
            from polaris.cells.qa.audit_verdict.public.contracts import RunVisualQaAuditCommandV1
            from polaris.cells.qa.audit_verdict.public.service import run_visual_qa_audit

            visual_command = RunVisualQaAuditCommandV1(
                task_id=_payload_string(command.payload, "task_id", runtime_object.identity.task_id or ""),
                workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
                run_id=_payload_string(command.payload, "run_id", runtime_object.identity.run_id or "") or None,
                image_refs=image_refs,
                model_capability_ref=str(getattr(model_capability, "capability_ref", "")),
                criteria=visual_criteria,
                evidence_paths=evidence_paths,
            )
        except (TypeError, ValueError) as exc:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_visual_qa_audit_command",
                error_message=str(exc),
            )

        try:
            if qa_audit_service is None:
                visual_result = run_visual_qa_audit(visual_command)
            else:
                visual_result = qa_audit_service.run_visual_qa_audit(visual_command)
        except Exception as exc:  # noqa: BLE001 - public RPC boundary returns structured failure
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="visual_qa_audit_failed",
                error_message=str(exc),
            )

        result_ref = f"qa.audit_verdict:visual-verdict:{visual_result.task_id}"
        target_evidence_refs = tuple(getattr(visual_result, "evidence_refs", ()) or ())
        audit_evidence_refs = _visual_audit_evidence_refs(target_evidence_refs)
        metadata = _capability_available_metadata(
            capability.capability_id,
            {
                **model_metadata,
                "verdict": visual_result.verdict,
                "score": visual_result.score,
                "image_refs": tuple(visual_result.image_refs),
                "finding_count": len(visual_result.findings),
                "findings": tuple(finding.summary for finding in visual_result.findings),
                "evidence_refs": target_evidence_refs,
                "audit_evidence_refs": audit_evidence_refs,
            },
        )
        if visual_result.ok and not audit_evidence_refs:
            return RoleCapabilityInvocationResultV1(
                ok=False,
                invocation_id=invocation.invocation_id,
                role_id=role_id,
                capability_id=capability.capability_id,
                command_contract=capability.contract_name,
                allowed=False,
                owner_cell=capability.owner_cell,
                payload_ref=result_ref,
                result_ref=result_ref,
                task_id=visual_result.task_id,
                status="EVIDENCE_MISSING",
                metadata={
                    **metadata,
                    "owner_cell": capability.owner_cell,
                    "evidence_owner_cell": "audit.evidence",
                },
                error_code="visual_qa_audit_missing_evidence_ref",
                error_message="visual QA audit success must include an audit.evidence evidence ref",
            )
        if not visual_result.ok:
            return RoleCapabilityInvocationResultV1(
                ok=False,
                invocation_id=invocation.invocation_id,
                role_id=role_id,
                capability_id=capability.capability_id,
                command_contract=capability.contract_name,
                allowed=False,
                owner_cell=capability.owner_cell,
                payload_ref=result_ref,
                result_ref=result_ref,
                task_id=visual_result.task_id,
                status=visual_result.verdict,
                evidence_refs=audit_evidence_refs,
                metadata=metadata,
                error_code="visual_qa_audit_rejected",
                error_message="; ".join(finding.summary for finding in visual_result.findings)
                or "visual QA audit rejected the task",
            )
        return RoleCapabilityInvocationResultV1(
            ok=True,
            invocation_id=invocation.invocation_id,
            role_id=role_id,
            capability_id=capability.capability_id,
            command_contract=capability.contract_name,
            allowed=True,
            owner_cell=capability.owner_cell,
            payload_ref=result_ref,
            result_ref=result_ref,
            task_id=visual_result.task_id,
            status=visual_result.verdict,
            evidence_refs=audit_evidence_refs,
            metadata=metadata,
        )

    if is_qa_pytest_verification:
        verification_commands = _payload_string_tuple(command.payload, "verification_commands")
        if verification_commands is None or not verification_commands:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_verification_commands",
                error_message="payload.verification_commands must be a non-empty sequence of strings",
            )
        evidence_paths = _payload_string_tuple(command.payload, "evidence_paths")
        if evidence_paths is None:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_verification_evidence_paths",
                error_message="payload.evidence_paths must be a sequence of strings when provided",
            )
        allowed_commands = _payload_string_tuple(command.payload, "allowed_commands")
        if allowed_commands is None:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_verification_allowed_commands",
                error_message="payload.allowed_commands must be a sequence of strings when provided",
            )
        claim_metadata = _payload_mapping(command.payload, "metadata")
        if claim_metadata is None:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_verification_metadata",
                error_message="payload.metadata must be a mapping when provided",
            )
        claim_metadata.update(
            {
                "role_invocation_id": invocation.invocation_id,
                "role_payload_ref": invocation.payload_ref,
                "role_fingerprint_ref": invocation.fingerprint_ref,
                "role_capability_id": capability.capability_id,
            }
        )
        try:
            from polaris.cells.factory.verification_guard.public.contracts import (
                VerificationClaim,
                VerificationStatus,
                VerifyCompletionCommandV1,
            )
            from polaris.cells.factory.verification_guard.public.service import (
                verify_completion,
            )

            verification_command = VerifyCompletionCommandV1(
                workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
                claim=VerificationClaim(
                    claim_id=_payload_string(command.payload, "claim_id", invocation.invocation_id),
                    claimed_outcome=_payload_string(command.payload, "claimed_outcome", "pytest verification"),
                    verification_commands=verification_commands,
                    evidence_paths=evidence_paths,
                    timeout_seconds=int(command.payload.get("timeout_seconds", 120)),
                    metadata=claim_metadata,
                ),
                strict_mode=bool(command.payload.get("strict_mode", True)),
                allowed_commands=allowed_commands or None,
            )
        except (TypeError, ValueError) as exc:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_verification_command",
                error_message=str(exc),
            )

        try:
            if verification_guard_service is None:
                verification_result = verify_completion(verification_command)
            else:
                verification_result = verification_guard_service.verify_completion(verification_command)
        except Exception as exc:  # noqa: BLE001 - public RPC boundary returns structured failure
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="verification_guard_failed",
                error_message=str(exc),
            )

        report = verification_result.report
        status = report.status.name if report is not None else "ERROR"
        result_ref = f"factory.verification_guard:report:{verification_command.claim.claim_id}"
        metadata = {
            "verification_ok": verification_result.ok,
            "verification_status": status,
            "execution_summary": report.execution_summary if report is not None else "",
            "command_count": len(report.command_results) if report is not None else 0,
        }
        if report is not None:
            metadata["evidence_collected"] = tuple(report.evidence_collected)
            metadata["evidence_missing"] = tuple(report.evidence_missing)
            metadata["mismatch_details"] = tuple(report.mismatch_details)
        if not verification_result.ok or report is None or report.status != VerificationStatus.PASS:
            return RoleCapabilityInvocationResultV1(
                ok=False,
                invocation_id=invocation.invocation_id,
                role_id=role_id,
                capability_id=capability.capability_id,
                command_contract=capability.contract_name,
                allowed=False,
                owner_cell=capability.owner_cell,
                payload_ref=result_ref,
                result_ref=result_ref,
                task_id=runtime_object.identity.task_id or "",
                status=status,
                metadata=metadata,
                error_code="verification_failed",
                error_message=verification_result.error_message
                or (report.execution_summary if report is not None else "verification failed"),
            )
        return RoleCapabilityInvocationResultV1(
            ok=True,
            invocation_id=invocation.invocation_id,
            role_id=role_id,
            capability_id=capability.capability_id,
            command_contract=capability.contract_name,
            allowed=True,
            owner_cell=capability.owner_cell,
            payload_ref=result_ref,
            result_ref=result_ref,
            task_id=runtime_object.identity.task_id or "",
            status=status,
            metadata=metadata,
        )

    if is_blueprint_generation:
        blueprint_context = _payload_mapping(command.payload, "context")
        if blueprint_context is None:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_blueprint_context",
                error_message="payload.context must be a mapping when provided",
            )
        blueprint_constraints = _payload_mapping(command.payload, "constraints")
        if blueprint_constraints is None:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_blueprint_constraints",
                error_message="payload.constraints must be a mapping when provided",
            )
        ce_asset_refs = _chief_engineer_asset_refs(runtime_object)
        target_asset_mount = str(capability.metadata.get("asset_mount") or "").strip()
        target_asset_ref = _asset_mount_ref(runtime_object, target_asset_mount) if target_asset_mount else ""
        blueprint_context.update(
            {
                "role_invocation_id": invocation.invocation_id,
                "role_payload_ref": invocation.payload_ref,
                "role_fingerprint_ref": invocation.fingerprint_ref,
                "role_capability_id": capability.capability_id,
                "asset_refs": ce_asset_refs,
                "diff_map_archive_requires_blueprint_ref": True,
            }
        )
        if target_asset_mount:
            blueprint_context["target_asset_mount"] = target_asset_mount
            blueprint_context["target_asset_ref"] = target_asset_ref
        try:
            from polaris.cells.chief_engineer.blueprint.public.contracts import GenerateTaskBlueprintCommandV1
            from polaris.cells.chief_engineer.blueprint.public.service import generate_task_blueprint

            blueprint_command = GenerateTaskBlueprintCommandV1(
                task_id=_payload_string(command.payload, "task_id", runtime_object.identity.task_id or ""),
                workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
                objective=_payload_string(command.payload, "objective"),
                run_id=_payload_string(command.payload, "run_id", runtime_object.identity.run_id or "") or None,
                constraints=blueprint_constraints,
                context=blueprint_context,
            )
        except (TypeError, ValueError) as exc:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_blueprint_command",
                error_message=str(exc),
            )

        try:
            if blueprint_service is None:
                blueprint_result = generate_task_blueprint(blueprint_command)
            else:
                blueprint_result = blueprint_service.generate_task_blueprint(blueprint_command)
        except Exception as exc:  # noqa: BLE001 - public RPC boundary returns structured failure
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="blueprint_generation_failed",
                error_message=str(exc),
            )

        blueprint_ref_id = blueprint_result.blueprint_id or blueprint_result.task_id
        blueprint_ref = f"chief_engineer.blueprint:blueprint:{blueprint_ref_id}"
        metadata = {
            "blueprint_id": blueprint_result.blueprint_id or "",
            "blueprint_path": blueprint_result.blueprint_path or "",
            "summary": blueprint_result.summary,
            "recommendations": tuple(blueprint_result.recommendations),
            "risks": tuple(blueprint_result.risks),
            "asset_refs": ce_asset_refs,
            "diff_map_archive_ref": f"{ce_asset_refs['diff_map_archive']}:{blueprint_ref_id}"
            if ce_asset_refs["diff_map_archive"]
            else "",
            "arch_memo_ref": f"{ce_asset_refs['arch_constraint_memo']}:{blueprint_ref_id}"
            if ce_asset_refs["arch_constraint_memo"]
            else "",
        }
        if target_asset_mount:
            metadata["target_asset_mount"] = target_asset_mount
            metadata["target_asset_ref"] = target_asset_ref
        if not blueprint_result.ok:
            return RoleCapabilityInvocationResultV1(
                ok=False,
                invocation_id=invocation.invocation_id,
                role_id=role_id,
                capability_id=capability.capability_id,
                command_contract=capability.contract_name,
                allowed=False,
                owner_cell=capability.owner_cell,
                payload_ref=blueprint_ref,
                result_ref=blueprint_ref,
                task_id=blueprint_result.task_id,
                status=blueprint_result.status,
                metadata=metadata,
                error_code="blueprint_generation_rejected",
                error_message=blueprint_result.summary or "blueprint generation was rejected",
            )
        return RoleCapabilityInvocationResultV1(
            ok=True,
            invocation_id=invocation.invocation_id,
            role_id=role_id,
            capability_id=capability.capability_id,
            command_contract=capability.contract_name,
            allowed=True,
            owner_cell=capability.owner_cell,
            payload_ref=blueprint_ref,
            result_ref=blueprint_ref,
            task_id=blueprint_result.task_id,
            status=blueprint_result.status,
            metadata=metadata,
        )

    if is_director_task_execution:
        director_metadata = _payload_mapping(command.payload, "metadata")
        if director_metadata is None:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_director_execution_metadata",
                error_message="payload.metadata must be a mapping when provided",
            )
        director_asset_refs = {
            "execution_task": _asset_mount_ref(runtime_object, "ExecutionTask"),
            "director_execution_state": _asset_mount_ref(runtime_object, "DirectorExecutionState"),
            "director_evidence_trail": _asset_mount_ref(runtime_object, "DirectorEvidenceTrail"),
        }
        director_metadata.update(
            {
                "role_invocation_id": invocation.invocation_id,
                "role_payload_ref": invocation.payload_ref,
                "role_fingerprint_ref": invocation.fingerprint_ref,
                "role_capability_id": capability.capability_id,
                "asset_refs": director_asset_refs,
            }
        )
        instruction = (
            _payload_string(command.payload, "instruction")
            or _payload_string(command.payload, "objective")
            or _payload_string(command.payload, "summary")
        )
        try:
            from polaris.cells.director.execution.public.contracts import ExecuteDirectorTaskCommandV1
            from polaris.cells.director.execution.public.service import execute_director_task

            director_command = ExecuteDirectorTaskCommandV1(
                task_id=_payload_string(command.payload, "task_id", runtime_object.identity.task_id or ""),
                workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
                instruction=instruction,
                run_id=_payload_string(command.payload, "run_id", runtime_object.identity.run_id or "") or None,
                attempt=int(command.payload.get("attempt", 1)),
                metadata=director_metadata,
            )
        except (TypeError, ValueError) as exc:
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_director_execution_command",
                error_message=str(exc),
            )

        try:
            if director_execution_service is None:
                director_result = execute_director_task(director_command)
            elif callable(director_execution_service):
                director_result = director_execution_service(director_command)
            else:
                director_result = director_execution_service.execute_director_task(director_command)
        except Exception as exc:  # noqa: BLE001 - public RPC boundary returns structured failure
            return _capability_invocation_failure(
                command,
                capability_available=True,
                owner_cell=capability.owner_cell,
                error_code="director_execution_failed",
                error_message=str(exc),
            )

        result_ref = f"director.execution:task:{director_result.task_id}"
        evidence_paths = tuple(director_result.evidence_paths)
        audit_evidence_refs = _audit_evidence_refs(evidence_paths)
        metadata = _capability_available_metadata(
            capability.capability_id,
            {
                "director_status": director_result.status,
                "output_summary": director_result.output_summary,
                "evidence_paths": evidence_paths,
                "audit_evidence_refs": audit_evidence_refs,
                "asset_refs": director_asset_refs,
            },
        )
        if not director_result.ok:
            return RoleCapabilityInvocationResultV1(
                ok=False,
                invocation_id=invocation.invocation_id,
                role_id=role_id,
                capability_id=capability.capability_id,
                command_contract=capability.contract_name,
                allowed=False,
                owner_cell=capability.owner_cell,
                payload_ref=result_ref,
                result_ref=result_ref,
                task_id=director_result.task_id,
                status=director_result.status,
                evidence_refs=audit_evidence_refs,
                metadata=metadata,
                error_code=director_result.error_code or "director_execution_rejected",
                error_message=director_result.error_message or "director execution rejected the task",
            )
        return RoleCapabilityInvocationResultV1(
            ok=True,
            invocation_id=invocation.invocation_id,
            role_id=role_id,
            capability_id=capability.capability_id,
            command_contract=capability.contract_name,
            allowed=True,
            owner_cell=capability.owner_cell,
            payload_ref=result_ref,
            result_ref=result_ref,
            task_id=director_result.task_id,
            status=director_result.status,
            evidence_refs=audit_evidence_refs,
            metadata=metadata,
        )

    if is_not_task_market_dispatch:
        return _capability_invocation_failure(
            command,
            error_code="unsupported_capability_contract",
            error_message=(f"capability {capability.capability_id!r} has no latest-only public invocation adapter"),
            owner_cell=capability.owner_cell,
        )

    task_payload = _payload_mapping(command.payload, "payload")
    if task_payload is None or not task_payload:
        return _capability_invocation_failure(
            command,
            capability_available=True,
            owner_cell=capability.owner_cell,
            error_code="invalid_task_market_payload",
            error_message="payload.payload must be a non-empty mapping",
        )
    task_metadata = _payload_mapping(command.payload, "metadata")
    if task_metadata is None:
        return _capability_invocation_failure(
            command,
            capability_available=True,
            owner_cell=capability.owner_cell,
            error_code="invalid_task_market_metadata",
            error_message="payload.metadata must be a mapping when provided",
        )

    task_metadata.update(
        {
            "role_invocation_id": invocation.invocation_id,
            "role_payload_ref": invocation.payload_ref,
            "role_fingerprint_ref": invocation.fingerprint_ref,
            "role_capability_id": capability.capability_id,
        }
    )

    try:
        from polaris.cells.runtime.task_market.public import (
            PublishTaskWorkItemCommandV1,
            get_task_market_service,
        )

        publish_command = PublishTaskWorkItemCommandV1(
            workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
            trace_id=_payload_string(
                command.payload,
                "trace_id",
                runtime_object.identity.run_id or invocation.invocation_id,
            ),
            run_id=_payload_string(
                command.payload,
                "run_id",
                runtime_object.identity.run_id or invocation.invocation_id,
            ),
            task_id=_payload_string(command.payload, "task_id", runtime_object.identity.task_id or ""),
            stage=_payload_string(command.payload, "stage", str(capability.metadata.get("target_stage") or "")),
            source_role=role_id,
            payload=task_payload,
            priority=_payload_string(command.payload, "priority", "medium"),
            max_attempts=int(command.payload.get("max_attempts", 3)),
            metadata=task_metadata,
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
    except (TypeError, ValueError) as exc:
        return _capability_invocation_failure(
            command,
            capability_available=True,
            owner_cell=capability.owner_cell,
            error_code="invalid_task_market_command",
            error_message=str(exc),
        )

    service = task_market_service or get_task_market_service()
    try:
        task_result = service.publish_work_item(publish_command)
    except Exception as exc:  # noqa: BLE001 - public RPC boundary returns structured failure
        return _capability_invocation_failure(
            command,
            capability_available=True,
            owner_cell=capability.owner_cell,
            error_code="task_market_publish_failed",
            error_message=str(exc),
        )

    task_ref = f"runtime.task_market:work-item:{task_result.task_id}"
    if not task_result.ok:
        return RoleCapabilityInvocationResultV1(
            ok=False,
            invocation_id=invocation.invocation_id,
            role_id=role_id,
            capability_id=capability.capability_id,
            command_contract=capability.contract_name,
            allowed=False,
            owner_cell=capability.owner_cell,
            payload_ref=task_ref,
            result_ref=task_ref,
            task_id=task_result.task_id,
            status=task_result.status,
            metadata={"task_market_version": task_result.version, "task_market_reason": task_result.reason},
            error_code="task_market_publish_rejected",
            error_message=task_result.reason or "task market publish was rejected",
        )

    return RoleCapabilityInvocationResultV1(
        ok=True,
        invocation_id=invocation.invocation_id,
        role_id=role_id,
        capability_id=capability.capability_id,
        command_contract=capability.contract_name,
        allowed=True,
        owner_cell=capability.owner_cell,
        payload_ref=task_ref,
        result_ref=task_ref,
        task_id=task_result.task_id,
        status=task_result.status,
        metadata={"task_market_version": task_result.version},
    )


_AGGREGATE_LOBE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "lobe_id": "constraint_boundary_generator",
        "title": "Architect + QA constraint boundary generator",
        "phase": "preflight",
        "role_ids": ("architect", "qa"),
        "virtual_role_ids": (),
        "capability_refs": (
            "docs.graph.catalog.cells",
            "docs.graph.subgraphs.context_plane",
            "polaris.kernelone.context.context_os.StateFirstContextOS",
            "polaris.cells.factory.cognitive_runtime",
        ),
        "attention_masks": (
            "graph_boundary",
            "forbid_prompt_leakage",
            "verification_before_write",
        ),
        "memory_triggers": (),
        "compute_tier": "cloud_control",
        "handoff_keys": (
            "constraint_topology",
            "negative_prompt_masks",
            "graph_boundary_verdict",
        ),
        "takeover_triggers": (
            "graph_boundary_violation",
            "prompt_leakage_risk",
            "unverified_write_plan",
        ),
        "output_contract": "constraint_topology_v1",
    },
    {
        "lobe_id": "dialectic_self_heal_loop",
        "title": "Chief Engineer + adversarial critic reasoning loop",
        "phase": "blueprint_refinement",
        "role_ids": ("chief_engineer", "qa"),
        "virtual_role_ids": ("adversarial_critic",),
        "capability_refs": (
            "polaris.cells.chief_engineer.blueprint",
            "polaris.kernelone.cognitive.governance.verification",
            "polaris.kernelone.tool_execution.code_validator",
            "polaris.kernelone.cognitive.execution.rollback_manager",
        ),
        "attention_masks": (
            "edge_case_attack",
            "rollback_required",
            "test_gate_required",
        ),
        "memory_triggers": ("verification_failure", "compiler_failure"),
        "compute_tier": "cloud_critic_local_retry",
        "handoff_keys": (
            "self_healed_blueprint",
            "edge_case_matrix",
            "rollback_plan",
        ),
        "takeover_triggers": (
            "blueprint_incomplete",
            "compile_failure",
            "typecheck_failure",
        ),
        "output_contract": "self_healed_blueprint_v1",
    },
    {
        "lobe_id": "hippocampus_controller",
        "title": "Director + ContextOS/Akashic memory router",
        "phase": "execution_context_projection",
        "role_ids": ("director",),
        "virtual_role_ids": (),
        "capability_refs": (
            "polaris.kernelone.context.context_os.StateFirstContextOS",
            "polaris.kernelone.akashic.semantic_memory.AkashicSemanticMemory",
            "polaris.kernelone.context.repo_intelligence.RepoIntelligenceFacade",
            "polaris.cells.roles.session.public.contracts",
        ),
        "attention_masks": (
            "active_window",
            "stable_facts",
            "open_loops",
        ),
        "memory_triggers": ("degraded_signal", "localization_uncertain", "long_session"),
        "compute_tier": "local_memory_router",
        "handoff_keys": (
            "context_projection",
            "akashic_recall_pack",
            "repo_localization_candidates",
        ),
        "takeover_triggers": (
            "degraded_signal",
            "empty_repo_map",
            "localization_uncertain",
        ),
        "output_contract": "context_projection_v1",
    },
    {
        "lobe_id": "tool_commit_guard",
        "title": "Director + QA governed commit guard",
        "phase": "apply_and_verify",
        "role_ids": ("director", "qa"),
        "virtual_role_ids": (),
        "capability_refs": (
            "polaris.cells.roles.kernel.transaction.turn_ledger",
            "polaris.kernelone.llm.toolkit.tool_normalization",
            "polaris.kernelone.tool_execution.code_validator",
            "polaris.cells.factory.cognitive_runtime.handoff",
        ),
        "attention_masks": (
            "single_commit",
            "tool_policy",
            "change_set_validation",
        ),
        "memory_triggers": ("rollback", "failed_apply", "integration_qa_failure"),
        "compute_tier": "local_execution_guard",
        "handoff_keys": (
            "validated_change_set",
            "tool_receipt",
            "qa_gate_result",
        ),
        "takeover_triggers": (
            "unsafe_tool_call",
            "change_set_out_of_scope",
            "integration_qa_failure",
        ),
        "output_contract": "verified_patch_v1",
    },
    {
        "lobe_id": "task_market_allocator",
        "title": "PM + Director + QA runtime task allocator",
        "phase": "stage_handoff",
        "role_ids": ("pm", "director", "qa"),
        "virtual_role_ids": (),
        "capability_refs": (
            "polaris.cells.runtime.task_market",
            "polaris.cells.runtime.projection",
            "polaris.cells.orchestration.pm_dispatch",
            "polaris.cells.factory.cognitive_runtime.receipt",
        ),
        "attention_masks": (
            "task_contract_quality",
            "stage_owner",
            "receipt_required",
        ),
        "memory_triggers": ("task_requeue", "dead_letter", "handoff_gap"),
        "compute_tier": "control_plane",
        "handoff_keys": (
            "task_contract",
            "stage_owner_matrix",
            "runtime_receipt_ids",
        ),
        "takeover_triggers": (
            "task_quality_below_gate",
            "handoff_gap",
            "dead_letter",
        ),
        "output_contract": "runtime_handoff_matrix_v1",
    },
)
_AGGREGATE_RUNTIME_INTEGRATION_SPECS: tuple[dict[str, Any], ...] = (
    {
        "tech_id": "acga_graph_cell_governance",
        "title": "ACGA 2.0 Graph/Cell governance",
        "status": "wired",
        "priority": "p1",
        "production_entrypoints": (
            "docs/graph/catalog/cells.yaml",
            "roles.runtime.build_aggregate_role_plan",
            "delivery.http./v1/chat/completions",
        ),
        "trigger_keys": ("workspace", "domain", "role_ids"),
        "evidence_keys": ("graph_catalog_cell", "required_capability_refs", "graph_boundary_verdict"),
        "runtime_effects": ("constrain role/capability selection before aggregate execution",),
        "benefit": "Keeps aggregate-model planning inside graph-owned architecture boundaries.",
        "capability_refs": ("docs.graph.catalog.cells",),
    },
    {
        "tech_id": "kernelone_agent_os",
        "title": "KernelOne Agent OS foundation",
        "status": "wired",
        "priority": "p1",
        "production_entrypoints": (
            "roles.runtime.stream_chat_turn",
            "roles.kernel.RoleExecutionKernel",
            "kernelone.context",
        ),
        "trigger_keys": ("execution_mode=single_turn",),
        "evidence_keys": ("strategy_fingerprint", "context_override", "runtime_receipts"),
        "runtime_effects": ("execute selected concrete role through KernelOne-backed role runtime",),
        "benefit": "Turns role lobes into a shared runtime substrate instead of prompt-only agents.",
        "capability_refs": ("polaris.kernelone", "polaris.cells.roles.kernel"),
    },
    {
        "tech_id": "turn_transaction_kernel_ledger",
        "title": "Turn Transaction Kernel / Turn Ledger",
        "status": "wired",
        "priority": "p0",
        "production_entrypoints": (
            "roles.runtime.stream_chat_turn",
            "roles.kernel.internal.turn_engine",
            "roles.kernel.internal.transaction.ledger",
        ),
        "trigger_keys": ("execution_mode=single_turn", "tool_call"),
        "evidence_keys": ("tool_calls", "turn_events_metadata", "receipt_ids"),
        "runtime_effects": ("commit or reject tool effects through the normal turn transaction flow",),
        "benefit": "Prevents aggregate execution from becoming an unaudited multi-agent chat transcript.",
        "capability_refs": ("polaris.cells.roles.kernel.transaction.turn_ledger",),
    },
    {
        "tech_id": "context_plane_isolation",
        "title": "Context Plane isolation",
        "status": "wired",
        "priority": "p0",
        "production_entrypoints": (
            "roles.runtime._prepare_session_request",
            "roles.runtime._enforce_required_context_os",
            "kernelone.context.context_os",
        ),
        "trigger_keys": ("context_os_expected", "state_first_context_os_enabled"),
        "evidence_keys": ("context_os_preflight", "context_os_audit", "context_os_snapshot"),
        "runtime_effects": ("separate control metadata from model-visible context projection",),
        "benefit": "Reduces prompt pollution while preserving auditable control-plane decisions.",
        "capability_refs": ("polaris.kernelone.context.context_os.StateFirstContextOS",),
    },
    {
        "tech_id": "descriptor_context_verify_packs",
        "title": "Descriptor / Context Pack / Verify Pack split",
        "status": "wired",
        "priority": "p2",
        "production_entrypoints": (
            "roles.runtime._build_aggregate_context_governance_pack",
            "cells.context.catalog",
            "cells.context.engine",
            "generated/context.pack.json",
        ),
        "trigger_keys": ("context_request", "verification_scope"),
        "evidence_keys": ("descriptor_pack", "context_pack", "verify_pack"),
        "runtime_effects": ("inject descriptor, working context, and verification summaries into aggregate turns",),
        "benefit": "Keeps retrieval, work, and verification attention layers distinct.",
        "capability_refs": ("polaris.cells.context.catalog", "polaris.cells.context.engine"),
    },
    {
        "tech_id": "strategy_profile_overlay_fingerprint",
        "title": "Strategy Profile + Role Overlay + Fingerprint",
        "status": "wired",
        "priority": "p0",
        "production_entrypoints": (
            "roles.runtime.stream_chat_turn",
            "roles.runtime.create_strategy_run",
            "runtime/strategy_runs",
        ),
        "trigger_keys": ("execution_mode=single_turn", "domain", "role"),
        "evidence_keys": ("profile_id", "profile_hash", "bundle_id", "strategy_receipt_path"),
        "runtime_effects": ("emit per-turn strategy identity before role execution",),
        "benefit": "Makes each brain-lobe activation traceable to a concrete context/tool policy.",
        "capability_refs": ("polaris.kernelone.context.strategy_profiles",),
    },
    {
        "tech_id": "cognitive_runtime_receipt_handoff",
        "title": "Cognitive Runtime Receipt / Handoff Pack",
        "status": "wired",
        "priority": "p0",
        "production_entrypoints": (
            "roles.runtime._emit_cognitive_runtime_shadow_artifacts",
            "factory.cognitive_runtime.record_runtime_receipt",
            "factory.cognitive_runtime.export_handoff_pack",
        ),
        "trigger_keys": ("cognitive_runtime_required", "session_id"),
        "evidence_keys": ("cognitive_runtime_evidence", "receipt_id", "handoff_id"),
        "runtime_effects": ("record completed role turns and export cross-lobe handoff packs",),
        "benefit": "Provides hard evidence for memory transfer instead of relying on chat history.",
        "capability_refs": ("polaris.cells.factory.cognitive_runtime",),
    },
    {
        "tech_id": "session_continuity_engine",
        "title": "Session Continuity Engine",
        "status": "wired",
        "priority": "p1",
        "production_entrypoints": (
            "roles.runtime._project_host_history",
            "roles.runtime._persist_session_turn_state",
            "context.session_continuity",
        ),
        "trigger_keys": ("session_id", "history", "include_session_snapshot"),
        "evidence_keys": ("stable_facts", "open_loops", "recent_window", "session_turn_events"),
        "runtime_effects": ("persist and reload structured session state across aggregate calls",),
        "benefit": "Turns long-running aggregate chats into stateful memory projection, not raw history piles.",
        "capability_refs": ("polaris.kernelone.context.session_continuity",),
    },
    {
        "tech_id": "context_catalog_graph_semantic_retrieval",
        "title": "Context Catalog / Graph-constrained semantic retrieval",
        "status": "wired",
        "priority": "p1",
        "production_entrypoints": (
            "roles.runtime._build_aggregate_context_governance_pack",
            "cells.context.catalog",
            "cells.context.engine.internal.search_gateway",
        ),
        "trigger_keys": ("context_query", "cell_scope", "graph_boundary"),
        "evidence_keys": ("catalog_descriptor", "graph_scope", "retrieval_candidates"),
        "runtime_effects": ("run catalog search gateway before aggregate role execution",),
        "benefit": "Prevents semantic memory recall from crossing Cell ownership boundaries.",
        "capability_refs": ("polaris.cells.context.catalog",),
    },
    {
        "tech_id": "repo_intelligence_localizer",
        "title": "Repo Intelligence / Repo Map Localizer",
        "status": "wired",
        "priority": "p1",
        "production_entrypoints": (
            "roles.runtime._build_session_request",
            "roles.runtime.public.context_adapter.augment_context_with_repo_intelligence",
            "kernelone.context.repo_intelligence",
        ),
        "trigger_keys": ("domain=code", "localization_uncertain", "empty_repo_map"),
        "evidence_keys": ("repo_intelligence", "candidate_files", "symbol_tags"),
        "runtime_effects": ("project code-localization candidates into role turn context",),
        "benefit": "Improves local-model coding accuracy by aiming attention at likely files first.",
        "capability_refs": ("polaris.kernelone.context.repo_intelligence.RepoIntelligenceFacade",),
    },
    {
        "tech_id": "akashic_knowledge_pipeline",
        "title": "Akashic Knowledge Pipeline",
        "status": "wired",
        "priority": "p1",
        "production_entrypoints": (
            "roles.runtime._build_aggregate_memory_recall_pack",
            "kernelone.memory",
            "kernelone.context.context_os.memory",
            "kernelone.context.context_os.memory_search",
        ),
        "trigger_keys": ("long_session", "memory_search", "akashic_recall_pack"),
        "evidence_keys": ("memory_candidates", "semantic_chunks", "vector_store_refs"),
        "runtime_effects": ("call ContextOS MemoryManager during aggregate memory fallback turns",),
        "benefit": "Allows repeated execution experience and documents to become reusable recall material.",
        "capability_refs": ("polaris.kernelone.akashic.semantic_memory.AkashicSemanticMemory",),
    },
    {
        "tech_id": "tool_normalization_edit_blocks",
        "title": "Tool Normalization / Edit Blocks Normalizer",
        "status": "wired",
        "priority": "p1",
        "production_entrypoints": (
            "roles.kernel.internal.tool_call_protocol",
            "roles.kernel.internal.output.action_parser",
            "roles.kernel.internal.transaction.modification_contract",
        ),
        "trigger_keys": ("tool_call", "edit_block", "failed_apply"),
        "evidence_keys": ("normalized_tool_call", "modification_contract", "apply_error"),
        "runtime_effects": ("normalize unstable model tool output before execution or repair",),
        "benefit": "Reduces format drift when aggregate execution delegates coding to weaker local models.",
        "capability_refs": ("polaris.kernelone.llm.toolkit.tool_normalization",),
    },
    {
        "tech_id": "change_set_validation_rollback",
        "title": "Change-set Validation + Rollback Manager",
        "status": "wired",
        "priority": "p1",
        "production_entrypoints": (
            "factory.cognitive_runtime.validate_change_set",
            "roles.kernel.internal.transaction.write_authority",
            "kernelone.cognitive.execution.rollback_manager",
        ),
        "trigger_keys": ("write_tool", "change_set", "rollback"),
        "evidence_keys": ("validated_change_set", "rollback_snapshot", "scope_lease"),
        "runtime_effects": ("guard physical writes with validation and rollback receipts",),
        "benefit": "Adds a write fuse between aggregate reasoning and repository mutation.",
        "capability_refs": ("polaris.kernelone.cognitive.execution.rollback_manager",),
    },
    {
        "tech_id": "task_market_runtime_projection",
        "title": "Task Market / Runtime Projection",
        "status": "wired",
        "priority": "p2",
        "production_entrypoints": (
            "roles.runtime._build_aggregate_task_market_projection_pack",
            "cells.runtime.task_market",
            "cells.runtime.projection",
            "cells.orchestration.pm_dispatch",
        ),
        "trigger_keys": ("pm_task_id", "stage_owner", "runtime_projection"),
        "evidence_keys": ("task_contract", "stage_owner_matrix", "projection_snapshot"),
        "runtime_effects": ("snapshot task-market projection during aggregate role execution",),
        "benefit": "Makes multi-role delivery a governed workflow rather than ad hoc cross-calls.",
        "capability_refs": ("polaris.cells.runtime.task_market", "polaris.cells.runtime.projection"),
    },
    {
        "tech_id": "cognitive_knowledge_distiller",
        "title": "Cognitive Knowledge Distiller",
        "status": "wired",
        "priority": "p2",
        "production_entrypoints": (
            "roles.runtime._build_aggregate_distilled_knowledge_pack",
            "roles.runtime._distill_aggregate_lobe_result",
            "docs.graph.catalog.cells:cognitive.knowledge_distiller",
            "polaris.cells.cognitive.knowledge_distiller.public.service",
        ),
        "trigger_keys": ("receipt_id", "post_run_distillation"),
        "evidence_keys": ("distilled_lesson", "source_receipts", "replay_case"),
        "runtime_effects": ("retrieve distilled knowledge before execution and distill lobe results after execution",),
        "benefit": "Converts one-off aggregate runs into reusable operational knowledge.",
        "capability_refs": ("cognitive.knowledge_distiller",),
    },
    {
        "tech_id": "contextos_attention_phase_budgeting",
        "title": "ContextOS Attention / Phase-aware Budgeting / Predictive Compression",
        "status": "wired",
        "priority": "p1",
        "production_entrypoints": (
            "roles.runtime._build_aggregate_contextos_attention_budget_pack",
            "kernelone.context.context_os.attention",
            "kernelone.context.context_os.phase_budget_planner",
            "kernelone.context.context_os.predictive",
        ),
        "trigger_keys": ("phase", "budget_plan", "attention_masks"),
        "evidence_keys": ("attention_scores", "phase_budget", "compression_decisions"),
        "runtime_effects": ("inject phase budget, attention scores, and predictive compression signals per lobe",),
        "benefit": "Supports different context budgets for planning, localization, execution, and QA lobes.",
        "capability_refs": ("polaris.kernelone.context.context_os.phase_budget_planner",),
    },
)

_SESSION_PUBLIC_EXPORTS = frozenset(
    {
        "PathSecurityError",
        "RoleDataStore",
        "RoleDataStoreError",
    }
)
_BACKEND_ROOT = Path(__file__).resolve().parents[5]
_ENTRYPOINT_MODULE_ALIASES: dict[str, str] = {
    "cells.context.catalog": "polaris.cells.context.catalog",
    "cells.context.engine": "polaris.cells.context.engine",
    "cells.context.engine.internal.search_gateway": "polaris.cells.context.engine.internal.search_gateway",
    "cells.orchestration.pm_dispatch": "polaris.cells.orchestration.pm_dispatch",
    "cells.runtime.projection": "polaris.cells.runtime.projection",
    "cells.runtime.task_market": "polaris.cells.runtime.task_market",
    "factory.cognitive_runtime.receipts": "polaris.cells.factory.cognitive_runtime.public.service",
    "kernelone.context": "polaris.kernelone.context",
    "kernelone.context.context_os": "polaris.kernelone.context.context_os",
    "kernelone.context.context_os.attention": "polaris.kernelone.context.context_os.attention",
    "kernelone.context.context_os.memory": "polaris.kernelone.context.context_os.memory",
    "kernelone.context.context_os.memory_search": "polaris.kernelone.context.context_os.memory_search",
    "kernelone.context.context_os.phase_budget_planner": "polaris.kernelone.context.context_os.phase_budget_planner",
    "kernelone.context.context_os.predictive": "polaris.kernelone.context.context_os.predictive",
    "kernelone.context.repo_intelligence": "polaris.kernelone.context.repo_intelligence",
    "kernelone.cognitive.execution.rollback_manager": "polaris.kernelone.cognitive.execution.rollback_manager",
    "kernelone.memory": "polaris.kernelone.memory",
    "context.session_continuity": "polaris.kernelone.context.session_continuity",
    "roles.kernel.internal.output.action_parser": "polaris.cells.roles.kernel.internal.output.action_parser",
    "roles.kernel.internal.tool_call_protocol": "polaris.cells.roles.kernel.internal.tool_call_protocol",
    "roles.kernel.internal.transaction.ledger": "polaris.cells.roles.kernel.internal.transaction.ledger",
    "roles.kernel.internal.transaction.modification_contract": (
        "polaris.cells.roles.kernel.internal.transaction.modification_contract"
    ),
    "roles.kernel.internal.transaction.write_authority": (
        "polaris.cells.roles.kernel.internal.transaction.write_authority"
    ),
    "roles.kernel.internal.turn_engine": "polaris.cells.roles.kernel.internal.turn_engine",
}


def _load_session_public_symbol(name: str) -> object:
    from polaris.cells.roles.session import public as session_public

    value = getattr(session_public, name)
    globals()[name] = value
    return value


def _dedupe_tokens(values: Any) -> tuple[str, ...]:
    tokens: list[str] = []
    seen: set[str] = set()
    try:
        iterator = iter(values)
    except TypeError:
        return ()
    for item in iterator:
        token = str(item or "").strip()
        if token and token not in seen:
            tokens.append(token)
            seen.add(token)
    return tuple(tokens)


def _module_exists(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _file_check(entrypoint: str, path: Path) -> AggregateRuntimeEntrypointCheckV1:
    ok = path.exists()
    return AggregateRuntimeEntrypointCheckV1(
        entrypoint=entrypoint,
        check_type="file",
        ok=ok,
        evidence=str(path),
        reason="exists" if ok else "missing",
    )


def _module_check(entrypoint: str, module_name: str) -> AggregateRuntimeEntrypointCheckV1:
    ok = _module_exists(module_name)
    return AggregateRuntimeEntrypointCheckV1(
        entrypoint=entrypoint,
        check_type="module",
        ok=ok,
        evidence=module_name,
        reason="import_spec_found" if ok else "import_spec_missing",
    )


def _attribute_check(entrypoint: str, *, module_name: str, attribute: str) -> AggregateRuntimeEntrypointCheckV1:
    reason = "attribute_found"
    ok = False
    try:
        module = importlib.import_module(module_name)
        target: Any = module
        for part in attribute.split("."):
            target = getattr(target, part)
        ok = True
    except (AttributeError, ImportError, ValueError) as exc:
        reason = f"attribute_missing:{exc.__class__.__name__}"
    return AggregateRuntimeEntrypointCheckV1(
        entrypoint=entrypoint,
        check_type="attribute",
        ok=ok,
        evidence=f"{module_name}:{attribute}",
        reason=reason,
    )


def _route_check(entrypoint: str) -> AggregateRuntimeEntrypointCheckV1:
    route_path = _BACKEND_ROOT / "polaris" / "delivery" / "http" / "routers" / "aggregate_chat.py"
    ok = False
    reason = "missing"
    if route_path.exists():
        with open(route_path, encoding="utf-8") as handle:
            source = handle.read()
        ok = '@router.post("/v1/chat/completions"' in source
        reason = "route_declared" if ok else "route_missing"
    return AggregateRuntimeEntrypointCheckV1(
        entrypoint=entrypoint,
        check_type="http_route",
        ok=ok,
        evidence=str(route_path),
        reason=reason,
    )


def _graph_cell_check(entrypoint: str) -> AggregateRuntimeEntrypointCheckV1:
    graph_path = _BACKEND_ROOT / "docs" / "graph" / "catalog" / "cells.yaml"
    cell_id = entrypoint.split(":", 1)[1].strip() if ":" in entrypoint else ""
    ok = False
    reason = "missing"
    if graph_path.exists() and cell_id:
        with open(graph_path, encoding="utf-8") as handle:
            source = handle.read()
        ok = cell_id in source
        reason = "cell_id_declared" if ok else "cell_id_missing"
    return AggregateRuntimeEntrypointCheckV1(
        entrypoint=entrypoint,
        check_type="graph_cell",
        ok=ok,
        evidence=str(graph_path),
        reason=reason,
    )


def _generated_pack_check(entrypoint: str) -> AggregateRuntimeEntrypointCheckV1:
    matches = tuple((_BACKEND_ROOT / "polaris" / "cells").glob(f"**/{entrypoint}"))
    ok = bool(matches)
    return AggregateRuntimeEntrypointCheckV1(
        entrypoint=entrypoint,
        check_type="generated_pack",
        ok=ok,
        evidence=str(matches[0]) if matches else str(_BACKEND_ROOT / "polaris" / "cells" / "**" / entrypoint),
        reason="pack_found" if ok else "pack_missing",
    )


def _workspace_runtime_path_check(workspace: str, entrypoint: str) -> AggregateRuntimeEntrypointCheckV1:
    workspace_path = Path(workspace).expanduser()
    ok = workspace_path.exists()
    return AggregateRuntimeEntrypointCheckV1(
        entrypoint=entrypoint,
        check_type="workspace_runtime_path",
        ok=ok,
        evidence=str(workspace_path / entrypoint),
        reason="workspace_root_exists_path_created_on_demand" if ok else "workspace_root_missing",
    )


def _roles_runtime_check(entrypoint: str) -> AggregateRuntimeEntrypointCheckV1:
    attribute = entrypoint.removeprefix("roles.runtime.")
    class_check = _attribute_check(
        entrypoint,
        module_name="polaris.cells.roles.runtime.public.service",
        attribute=f"RoleRuntimeService.{attribute}",
    )
    if class_check.ok:
        return class_check
    module_check = _attribute_check(
        entrypoint,
        module_name="polaris.cells.roles.runtime.public.service",
        attribute=attribute,
    )
    if module_check.ok:
        return module_check
    return class_check


def _roles_kernel_public_check(entrypoint: str) -> AggregateRuntimeEntrypointCheckV1:
    attribute = entrypoint.removeprefix("roles.kernel.")
    return _attribute_check(
        entrypoint,
        module_name="polaris.cells.roles.kernel.public.service",
        attribute=attribute,
    )


def _factory_cognitive_runtime_check(entrypoint: str) -> AggregateRuntimeEntrypointCheckV1:
    attribute = entrypoint.removeprefix("factory.cognitive_runtime.")
    return _attribute_check(
        entrypoint,
        module_name="polaris.cells.factory.cognitive_runtime.public.service",
        attribute=f"CognitiveRuntimePublicService.{attribute}",
    )


def _public_context_adapter_check(entrypoint: str) -> AggregateRuntimeEntrypointCheckV1:
    attribute = entrypoint.removeprefix("roles.runtime.public.context_adapter.")
    return _attribute_check(
        entrypoint,
        module_name="polaris.cells.roles.runtime.public.context_adapter",
        attribute=attribute,
    )


def _check_aggregate_entrypoint(workspace: str, entrypoint: str) -> AggregateRuntimeEntrypointCheckV1:
    token = str(entrypoint or "").strip()
    if token.startswith("docs.graph.catalog.cells:"):
        return _graph_cell_check(token)
    if token.startswith("docs/"):
        return _file_check(token, _BACKEND_ROOT / token)
    if token == "delivery.http./v1/chat/completions":
        return _route_check(token)
    if token.startswith("runtime/"):
        return _workspace_runtime_path_check(workspace, token)
    if token.startswith("generated/"):
        return _generated_pack_check(token)
    if token.startswith("roles.runtime.public.context_adapter."):
        return _public_context_adapter_check(token)
    if token.startswith("roles.runtime."):
        return _roles_runtime_check(token)
    if token.startswith("roles.kernel.RoleExecutionKernel"):
        return _roles_kernel_public_check(token)
    if token.startswith("factory.cognitive_runtime.") and token != "factory.cognitive_runtime.receipts":
        return _factory_cognitive_runtime_check(token)
    module_name = _ENTRYPOINT_MODULE_ALIASES.get(token)
    if module_name is None and token.startswith("polaris."):
        module_name = token
    if module_name is not None:
        return _module_check(token, module_name)
    return AggregateRuntimeEntrypointCheckV1(
        entrypoint=token,
        check_type="unresolved",
        ok=False,
        evidence=token,
        reason="no_entrypoint_resolver",
    )


def _select_aggregate_role_ids(
    requested_role_ids: tuple[str, ...],
    available_role_ids: set[str],
) -> tuple[str, ...]:
    if requested_role_ids:
        return _dedupe_tokens(role_id for role_id in requested_role_ids if role_id in available_role_ids)
    selected = tuple(role_id for role_id in _DEFAULT_AGGREGATE_ROLE_IDS if role_id in available_role_ids)
    if selected:
        return selected
    return tuple(sorted(available_role_ids))


def _build_aggregate_lobe(
    spec: Mapping[str, Any],
    *,
    selected_role_ids: set[str],
    available_role_ids: set[str],
    include_virtual_lobes: bool,
) -> AggregateRoleLobeV1:
    role_ids = _dedupe_tokens(spec.get("role_ids") or ())
    virtual_role_ids = _dedupe_tokens(spec.get("virtual_role_ids") or ()) if include_virtual_lobes else ()
    missing_role_ids = tuple(
        role_id for role_id in role_ids if role_id not in selected_role_ids or role_id not in available_role_ids
    )
    status = "active" if not missing_role_ids else "partial"
    return AggregateRoleLobeV1(
        lobe_id=str(spec.get("lobe_id") or ""),
        title=str(spec.get("title") or ""),
        phase=str(spec.get("phase") or ""),
        role_ids=role_ids,
        virtual_role_ids=virtual_role_ids,
        capability_refs=_dedupe_tokens(spec.get("capability_refs") or ()),
        attention_masks=_dedupe_tokens(spec.get("attention_masks") or ()),
        memory_triggers=_dedupe_tokens(spec.get("memory_triggers") or ()),
        compute_tier=str(spec.get("compute_tier") or "unspecified"),
        handoff_keys=_dedupe_tokens(spec.get("handoff_keys") or ()),
        takeover_triggers=_dedupe_tokens(spec.get("takeover_triggers") or ()),
        output_contract=str(spec.get("output_contract") or ""),
        status=status,
        missing_role_ids=missing_role_ids,
        metadata={
            "truthful_migration": (
                "virtual_role_ids are aggregate lobes or critics, not current roles.profile entries"
                if virtual_role_ids
                else "all role_ids are current roles.profile entries"
            ),
            "stateful": False,
        },
    )


def _build_cognitive_ledger(lobes: tuple[AggregateRoleLobeV1, ...]) -> tuple[AggregateCognitiveLedgerEntryV1, ...]:
    entries: list[AggregateCognitiveLedgerEntryV1] = []
    for index, lobe in enumerate(lobes):
        next_lobe = lobes[index + 1].lobe_id if index + 1 < len(lobes) else ""
        entries.append(
            AggregateCognitiveLedgerEntryV1(
                sequence=index,
                lobe_id=lobe.lobe_id,
                phase=lobe.phase,
                compute_tier=lobe.compute_tier,
                reads=(*lobe.capability_refs, *lobe.memory_triggers),
                writes=(*lobe.handoff_keys, lobe.output_contract),
                handoff_to=(next_lobe,) if next_lobe else (),
                takeover_triggers=lobe.takeover_triggers,
            )
        )
    return tuple(entries)


def _build_compute_policy(lobes: tuple[AggregateRoleLobeV1, ...]) -> dict[str, Any]:
    tier_order = _dedupe_tokens(lobe.compute_tier for lobe in lobes)
    return {
        "policy_id": "aggregate_compute_swap.v1",
        "tier_order": tier_order,
        "default_priority": "local_self_heal_first_after_compiler_feedback",
        "cloud_priority_conditions": (
            "architectural_ambiguity",
            "graph_boundary_violation",
            "high_blast_radius",
        ),
        "local_priority_conditions": (
            "compile_failure",
            "typecheck_failure",
            "failed_apply",
            "localization_uncertain",
        ),
        "rationale": (
            "Use cloud critique for high-ambiguity boundary decisions, but route "
            "compiler/test failures to local self-heal loops first because they are "
            "measurable and produce reusable Cognitive Runtime receipts."
        ),
    }


def _build_runtime_integrations(workspace: str) -> tuple[AggregateRuntimeIntegrationV1, ...]:
    integrations: list[AggregateRuntimeIntegrationV1] = []
    for spec in _AGGREGATE_RUNTIME_INTEGRATION_SPECS:
        production_entrypoints = _dedupe_tokens(spec.get("production_entrypoints") or ())
        entrypoint_checks = tuple(
            _check_aggregate_entrypoint(workspace, entrypoint) for entrypoint in production_entrypoints
        )
        missing_entrypoints = tuple(check.entrypoint for check in entrypoint_checks if not check.ok)
        integrations.append(
            AggregateRuntimeIntegrationV1(
                tech_id=str(spec.get("tech_id") or ""),
                title=str(spec.get("title") or ""),
                status=str(spec.get("status") or ""),
                priority=str(spec.get("priority") or ""),
                production_entrypoints=production_entrypoints,
                trigger_keys=_dedupe_tokens(spec.get("trigger_keys") or ()),
                evidence_keys=_dedupe_tokens(spec.get("evidence_keys") or ()),
                runtime_effects=_dedupe_tokens(spec.get("runtime_effects") or ()),
                benefit=str(spec.get("benefit") or ""),
                capability_refs=_dedupe_tokens(spec.get("capability_refs") or ()),
                entrypoint_checks=entrypoint_checks,
                entrypoints_verified=bool(entrypoint_checks) and not missing_entrypoints,
                missing_entrypoints=missing_entrypoints,
            )
        )
    return tuple(integrations)


def _build_runtime_audit_result(
    *,
    workspace: str,
    integrations: tuple[AggregateRuntimeIntegrationV1, ...],
    metadata: Mapping[str, Any] | None = None,
) -> AggregateRuntimeAuditResultV1:
    wired = tuple(item for item in integrations if item.status == "wired" and item.entrypoints_verified)
    available = tuple(item for item in integrations if item.status == "available" and item.entrypoints_verified)
    planned_bridge = tuple(item for item in integrations if item.status == "planned_bridge")
    missing_checks = tuple(
        check for integration in integrations for check in integration.entrypoint_checks if not check.ok
    )
    verified_checks = tuple(
        check for integration in integrations for check in integration.entrypoint_checks if check.ok
    )
    priority_wired = tuple(item.tech_id for item in wired if item.priority == "p0")
    warnings: list[str] = []
    if planned_bridge:
        warnings.append(f"planned_bridge:{','.join(item.tech_id for item in planned_bridge)}")
    if missing_checks:
        warnings.append(f"missing_entrypoints:{','.join(check.entrypoint for check in missing_checks)}")
    return AggregateRuntimeAuditResultV1(
        ok=bool(integrations) and len(priority_wired) >= 4 and not missing_checks,
        workspace=workspace,
        aggregate_model_id=_AGGREGATE_MODEL_ID,
        integrations=integrations,
        wired_count=len(wired),
        available_count=len(available),
        planned_bridge_count=len(planned_bridge),
        verified_entrypoint_count=len(verified_checks),
        missing_entrypoint_count=len(missing_checks),
        priority_wired=priority_wired,
        warnings=tuple(warnings),
        metadata={
            "audit_scope": "aggregate_llm_unique_technology_runtime_integrations",
            "status_semantics": {
                "wired": "current aggregate runtime path has a production entrypoint",
                "available": "implemented capability exists but aggregate path does not force it yet",
                "planned_bridge": "known architecture asset still needs an aggregate bridge",
            },
            **dict(metadata or {}),
        },
    )


def _normalize_failure_signal(value: str) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _AGGREGATE_FAILURE_SIGNAL_ALIASES.get(token, token)


def _extract_failure_signals(query: BuildAggregateRolePlanQueryV1) -> tuple[str, ...]:
    signals: list[str] = []
    signals.extend(query.failure_signals)
    for source in (query.context, query.metadata):
        for key in ("failure_signal", "degraded_signal", "error_signal"):
            raw_value = source.get(key) if isinstance(source, Mapping) else None
            if isinstance(raw_value, str) and raw_value.strip():
                signals.append(raw_value)
        raw_values = source.get("failure_signals") if isinstance(source, Mapping) else None
        if isinstance(raw_values, list | tuple):
            signals.extend(str(item or "") for item in raw_values)
    return _dedupe_tokens(_normalize_failure_signal(signal) for signal in signals if str(signal or "").strip())


def _extract_failure_evidence(query: BuildAggregateRolePlanQueryV1) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for source in (query.context, query.metadata):
        raw_evidence = source.get("failure_evidence") if isinstance(source, Mapping) else None
        if isinstance(raw_evidence, Mapping):
            evidence.update(dict(raw_evidence))
    evidence.update(dict(query.failure_evidence))
    return evidence


def _build_takeover_evidence_status(
    *,
    takeover_directive: AggregateTakeoverDirectiveV1 | None,
    failure_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    if takeover_directive is None:
        return {
            "required_keys": (),
            "present_keys": tuple(sorted(str(key) for key in failure_evidence)),
            "missing_keys": (),
            "complete": True,
        }
    required_keys = takeover_directive.evidence_keys
    present_keys = tuple(
        key for key in required_keys if key in failure_evidence and failure_evidence.get(key) is not None
    )
    missing_keys = tuple(key for key in required_keys if key not in present_keys)
    return {
        "required_keys": required_keys,
        "present_keys": present_keys,
        "missing_keys": missing_keys,
        "complete": not missing_keys,
    }


def _build_takeover_directive(
    *,
    lobes: tuple[AggregateRoleLobeV1, ...],
    cognitive_ledger: tuple[AggregateCognitiveLedgerEntryV1, ...],
    failure_signals: tuple[str, ...],
) -> AggregateTakeoverDirectiveV1 | None:
    if not failure_signals:
        return None
    ledger_by_lobe = {entry.lobe_id: entry for entry in cognitive_ledger}
    for signal in failure_signals:
        for lobe in lobes:
            if signal not in lobe.takeover_triggers and signal not in lobe.memory_triggers:
                continue
            entry = ledger_by_lobe.get(lobe.lobe_id)
            next_lobes = entry.handoff_to if entry is not None else ()
            return AggregateTakeoverDirectiveV1(
                trigger=signal,
                lobe_id=lobe.lobe_id,
                compute_tier=lobe.compute_tier,
                reason=f"{signal} activates {lobe.lobe_id} through aggregate takeover triggers",
                evidence_keys=_AGGREGATE_FAILURE_EVIDENCE_KEYS.get(signal, ("failure_signal",)),
                action_contract=lobe.output_contract,
                next_lobes=next_lobes,
            )
    return None


def _lobe_by_id(plan: AggregateRolePlanResultV1, lobe_id: str | None) -> AggregateRoleLobeV1 | None:
    token = str(lobe_id or "").strip()
    if not token:
        return None
    for lobe in plan.lobes:
        if lobe.lobe_id == token:
            return lobe
    return None


def _select_aggregate_execution_lobe(plan: AggregateRolePlanResultV1) -> AggregateRoleLobeV1:
    takeover_lobe = _lobe_by_id(
        plan,
        plan.takeover_directive.lobe_id if plan.takeover_directive is not None else None,
    )
    if takeover_lobe is not None:
        return takeover_lobe
    for lobe in plan.lobes:
        if lobe.status == "active":
            return lobe
    return plan.lobes[0]


def _lobe_has_current_role(lobe: AggregateRoleLobeV1, plan: AggregateRolePlanResultV1) -> bool:
    current_roles = set(plan.current_role_ids)
    return any(role_id in current_roles for role_id in lobe.role_ids)


def _aggregate_max_lobe_turns(command: AggregateChatCompletionsCommandV1) -> int:
    for source in (command.metadata, command.context):
        raw_value = source.get("max_lobe_turns") if isinstance(source, Mapping) else None
        if raw_value is None and isinstance(source, Mapping):
            raw_value = source.get("aggregate_max_lobe_turns")
        if raw_value is None:
            continue
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            continue
        return min(max(parsed, 1), 5)
    return 3


def _aggregate_memory_recall_limit(command: AggregateChatCompletionsCommandV1) -> int:
    for source in (command.metadata, command.context):
        raw_value = source.get("memory_recall_limit") if isinstance(source, Mapping) else None
        if raw_value is None and isinstance(source, Mapping):
            raw_value = source.get("akashic_recall_limit")
        if raw_value is None:
            continue
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            continue
        return min(max(parsed, 0), 10)
    return 5


def _aggregate_memory_recall_triggers(
    *,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
) -> tuple[str, ...]:
    failure_signals = tuple(str(item) for item in plan.metadata.get("failure_signals") or ())
    triggers = [
        signal
        for signal in failure_signals
        if signal in selected_lobe.memory_triggers
        or signal in {"localization_uncertain", "degraded_signal", "empty_repo_map", "long_session"}
    ]
    if selected_lobe.lobe_id == "hippocampus_controller":
        triggers.append("hippocampus_controller")
    return _dedupe_tokens(triggers)


def _aggregate_memory_recall_query(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
) -> str:
    evidence = plan.metadata.get("failure_evidence")
    evidence_text = " ".join(str(value) for value in dict(evidence).values()) if isinstance(evidence, Mapping) else ""
    return " ".join(
        item
        for item in (
            _aggregate_objective_from_messages(command.messages),
            selected_lobe.lobe_id,
            " ".join(str(item) for item in plan.metadata.get("failure_signals") or ()),
            evidence_text,
        )
        if item
    ).strip()


def _aggregate_memory_current_facts(
    *,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    prior_handoffs: tuple[Mapping[str, Any], ...],
) -> list[str]:
    facts = [
        f"aggregate_model_id={plan.aggregate_model_id}",
        f"selected_lobe_id={selected_lobe.lobe_id}",
        f"selected_lobe_phase={selected_lobe.phase}",
    ]
    for signal in plan.metadata.get("failure_signals") or ():
        facts.append(f"failure_signal={signal}")
    evidence = plan.metadata.get("failure_evidence")
    if isinstance(evidence, Mapping):
        facts.extend(f"failure_evidence.{key}={value}" for key, value in evidence.items())
    for handoff in prior_handoffs:
        facts.append(
            "prior_handoff="
            + json.dumps(
                {
                    "lobe_id": handoff.get("lobe_id"),
                    "role_id": handoff.get("role_id"),
                    "status": handoff.get("status"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return facts


def _build_aggregate_memory_recall_pack(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    prior_handoffs: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    triggers = _aggregate_memory_recall_triggers(plan=plan, selected_lobe=selected_lobe)
    limit = _aggregate_memory_recall_limit(command)
    if not triggers or limit <= 0:
        return {
            "enabled": False,
            "provider": "ContextOS.MemoryManager",
            "triggers": triggers,
            "reason": "no_memory_trigger" if not triggers else "limit_zero",
        }
    query = _aggregate_memory_recall_query(command=command, plan=plan, selected_lobe=selected_lobe)
    current_facts = _aggregate_memory_current_facts(
        plan=plan,
        selected_lobe=selected_lobe,
        prior_handoffs=prior_handoffs,
    )
    try:
        from polaris.kernelone.context.context_os.memory import MemoryManager

        manager = MemoryManager(workspace=command.workspace)
        projections = manager.process(query=query, current_facts=current_facts, limit=limit)
        projection_payloads = [projection.to_dict() for projection in projections[:limit]]
        return {
            "enabled": True,
            "provider": "ContextOS.MemoryManager",
            "status": "ok",
            "triggers": triggers,
            "query": query,
            "current_facts": current_facts,
            "projection_count": len(projection_payloads),
            "injection_allowed_count": sum(1 for item in projection_payloads if bool(item.get("injection_allowed"))),
            "projections": projection_payloads,
            "truthful_migration": "Memory is supplementary and never overrides current failure evidence.",
        }
    except (RuntimeError, ValueError, TypeError, OSError) as exc:
        return {
            "enabled": True,
            "provider": "ContextOS.MemoryManager",
            "status": "degraded",
            "triggers": triggers,
            "query": query,
            "current_facts": current_facts,
            "projection_count": 0,
            "injection_allowed_count": 0,
            "projections": [],
            "error_message": str(exc),
        }


def _aggregate_phase_for_contextos(
    *,
    selected_lobe: AggregateRoleLobeV1,
    plan: AggregateRolePlanResultV1,
) -> str:
    if plan.takeover_directive is not None and plan.takeover_directive.trigger in {
        "compile_failure",
        "typecheck_failure",
        "failed_apply",
    }:
        return "debugging"
    return {
        "preflight": "planning",
        "blueprint_refinement": "planning",
        "execution_context_projection": "exploration",
        "apply_and_verify": "verification",
        "stage_handoff": "review",
    }.get(selected_lobe.phase, "planning")


def _estimate_aggregate_text_tokens(value: Any) -> int:
    try:
        payload = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        payload = str(value)
    return max(1, len(payload) // 4) if payload else 0


def _build_aggregate_attention_candidates(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    prior_handoffs: tuple[Mapping[str, Any], ...],
) -> tuple[Any, ...]:
    objective = _aggregate_objective_from_messages(command.messages)
    candidates: list[Any] = [
        SimpleNamespace(
            event_id="aggregate.objective",
            content=objective,
            role="user",
            kind="user_turn",
            sequence=0,
            metadata={"is_pinned": True, "candidate_kind": "objective"},
            created_at="",
        )
    ]
    failure_evidence = dict(plan.metadata.get("failure_evidence") or {})
    if failure_evidence:
        candidates.append(
            SimpleNamespace(
                event_id="aggregate.failure_evidence",
                content=json.dumps(failure_evidence, ensure_ascii=False, sort_keys=True),
                role="system",
                kind="error",
                sequence=1,
                metadata={"contains_error": True, "candidate_kind": "failure_evidence"},
                created_at="",
            )
        )
    if prior_handoffs:
        candidates.append(
            SimpleNamespace(
                event_id="aggregate.prior_handoffs",
                content=json.dumps([dict(item) for item in prior_handoffs], ensure_ascii=False, sort_keys=True),
                role="assistant",
                kind="tool_result",
                sequence=2,
                metadata={"contains_tool_result": True, "candidate_kind": "prior_handoffs"},
                created_at="",
            )
        )
    candidates.append(
        SimpleNamespace(
            event_id=f"aggregate.lobe.{selected_lobe.lobe_id}",
            content=" ".join((selected_lobe.title, selected_lobe.phase, " ".join(selected_lobe.attention_masks))),
            role="system",
            kind="system",
            sequence=3,
            metadata={"candidate_kind": "lobe_directive"},
            created_at="",
        )
    )
    return tuple(candidates)


def _build_aggregate_contextos_attention_budget_pack(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    prior_handoffs: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    phase_name = _aggregate_phase_for_contextos(selected_lobe=selected_lobe, plan=plan)
    transcript_tokens = sum(_estimate_aggregate_text_tokens(message.content) for message in command.messages)
    artifact_tokens = _estimate_aggregate_text_tokens(plan.metadata.get("failure_evidence") or {})
    artifact_tokens += _estimate_aggregate_text_tokens([dict(item) for item in prior_handoffs]) if prior_handoffs else 0
    try:
        from polaris.kernelone.context.context_os.attention import AttentionScorer
        from polaris.kernelone.context.context_os.attention.scorer import ScoringContext
        from polaris.kernelone.context.context_os.phase_budget_planner import PhaseAwareBudgetPlanner
        from polaris.kernelone.context.context_os.phase_detection import TaskPhase
        from polaris.kernelone.context.context_os.predictive import PredictiveCompressor

        task_phase = TaskPhase(phase_name)
        budget_plan = PhaseAwareBudgetPlanner().plan_budget(
            phase=task_phase,
            transcript_tokens=transcript_tokens,
            artifact_tokens=artifact_tokens,
        )
        prediction = PredictiveCompressor().predict(current_phase=task_phase.value, recent_events=())
        scorer = AttentionScorer(use_embeddings=False)
        scoring_context = ScoringContext(
            current_intent=_aggregate_objective_from_messages(command.messages),
            current_goal=selected_lobe.output_contract,
            hard_constraints=tuple(selected_lobe.attention_masks),
            current_task_id=command.run_id or command.session_id or "",
            current_phase=task_phase,
        )
        attention_scores = [
            {
                "event_id": str(getattr(candidate, "event_id", "")),
                "kind": str(getattr(candidate, "kind", "")),
                "score": scorer.score_candidate(candidate, scoring_context).to_dict(),
            }
            for candidate in _build_aggregate_attention_candidates(
                command=command,
                plan=plan,
                selected_lobe=selected_lobe,
                prior_handoffs=prior_handoffs,
            )
        ]
        return {
            "enabled": True,
            "provider": "ContextOS.Attention+PhaseBudget+PredictiveCompression",
            "status": "ok",
            "phase": task_phase.value,
            "attention_masks": list(selected_lobe.attention_masks),
            "phase_budget": budget_plan.to_dict(),
            "attention_scores": attention_scores,
            "predictive_compression": prediction.to_dict(),
        }
    except (ImportError, RuntimeError, ValueError, TypeError, OSError) as exc:
        return {
            "enabled": True,
            "provider": "ContextOS.Attention+PhaseBudget+PredictiveCompression",
            "status": "degraded",
            "phase": phase_name,
            "attention_masks": list(selected_lobe.attention_masks),
            "phase_budget": {},
            "attention_scores": [],
            "predictive_compression": {},
            "error_message": str(exc),
        }


def _build_aggregate_task_market_projection_pack(
    *,
    command: AggregateChatCompletionsCommandV1,
) -> dict[str, Any]:
    try:
        from polaris.cells.runtime.projection.task_market_projection import TaskMarketProjection

        summary = TaskMarketProjection(workspace=command.workspace).get_dashboard_summary()
        active_items = summary.get("active_items")
        if isinstance(active_items, list):
            summary = dict(summary)
            summary["active_items"] = active_items[:5]
            summary["active_items_truncated"] = len(active_items) > 5
        return {
            "enabled": True,
            "provider": "TaskMarketProjection",
            "status": "ok",
            "summary": summary,
        }
    except (ImportError, RuntimeError, ValueError, TypeError, OSError) as exc:
        return {
            "enabled": True,
            "provider": "TaskMarketProjection",
            "status": "degraded",
            "summary": {},
            "error_message": str(exc),
        }


def _read_aggregate_generated_pack_summary(pack_name: str, path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "pack": pack_name,
            "status": "missing",
            "path": path.relative_to(_BACKEND_ROOT).as_posix() if path.is_relative_to(_BACKEND_ROOT) else str(path),
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return {
            "pack": pack_name,
            "status": "degraded",
            "path": path.relative_to(_BACKEND_ROOT).as_posix() if path.is_relative_to(_BACKEND_ROOT) else str(path),
            "error_message": str(exc),
        }
    descriptors = payload.get("descriptors")
    capabilities = payload.get("capabilities")
    items = payload.get("items")
    return {
        "pack": pack_name,
        "status": "ok",
        "path": path.relative_to(_BACKEND_ROOT).as_posix() if path.is_relative_to(_BACKEND_ROOT) else str(path),
        "version": payload.get("version"),
        "cell_id": payload.get("cell_id"),
        "descriptor_count": len(descriptors) if isinstance(descriptors, list) else None,
        "capability_count": len(capabilities) if isinstance(capabilities, list) else None,
        "item_count": len(items) if isinstance(items, list) else None,
        "source_hash": payload.get("source_hash"),
        "snapshot_hash": payload.get("snapshot_hash"),
    }


def _serialize_context_budget(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    if hasattr(value, "dict"):
        dumped = value.dict()
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _build_aggregate_context_governance_pack(
    *,
    command: AggregateChatCompletionsCommandV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    chain_turn_index: int,
) -> dict[str, Any]:
    query = _aggregate_objective_from_messages(command.messages)
    descriptor_summaries = [
        _read_aggregate_generated_pack_summary(
            "context.catalog.descriptor",
            _BACKEND_ROOT / "polaris/cells/context/catalog/generated/descriptor.pack.json",
        ),
        _read_aggregate_generated_pack_summary(
            "context.engine.descriptor",
            _BACKEND_ROOT / "polaris/cells/context/engine/generated/descriptor.pack.json",
        ),
    ]
    generated_context_summaries = [
        _read_aggregate_generated_pack_summary(
            "context.catalog.context",
            _BACKEND_ROOT / "polaris/cells/context/catalog/generated/context.pack.json",
        ),
        _read_aggregate_generated_pack_summary(
            "context.engine.context",
            _BACKEND_ROOT / "polaris/cells/context/engine/generated/context.pack.json",
        ),
    ]
    try:
        from polaris.cells.context.engine.public.service import build_context_window, get_search_service

        retrieval_candidates = get_search_service().search(query, limit=5)
        pack, policy, budget, sources_enabled = build_context_window(
            project_root=command.workspace,
            role=selected_role,
            query=query,
            step=chain_turn_index + 1,
            run_id=command.run_id or command.session_id or _AGGREGATE_MODEL_ID,
            mode=selected_lobe.phase,
            sources_enabled=[],
            policy={"max_tokens": 4096, "max_chars": 16384, "cost_class": "LOCAL"},
            context_override={"state_first_context_os_enabled": True},
            session_id=command.session_id or "",
        )
        context_pack = {
            "provider": "context.engine.build_context_window",
            "request_hash": pack.request_hash,
            "item_count": len(pack.items),
            "total_tokens": pack.total_tokens,
            "total_chars": pack.total_chars,
            "snapshot_path": pack.snapshot_path,
            "snapshot_hash": pack.snapshot_hash,
            "sources_enabled": list(sources_enabled),
        }
        verify_pack = {
            "budget": _serialize_context_budget(budget),
            "policy_cost_class": policy.get("cost_class"),
            "descriptor_pack_count": len(descriptor_summaries),
            "generated_context_pack_count": len(generated_context_summaries),
            "retrieval_candidate_count": len(retrieval_candidates),
        }
        return {
            "enabled": True,
            "provider": "ContextCatalog+ContextEngine",
            "status": "ok",
            "query": query,
            "descriptor_pack": descriptor_summaries,
            "generated_context_pack": generated_context_summaries,
            "context_pack": context_pack,
            "verify_pack": verify_pack,
            "graph_constrained_retrieval": {
                "provider": "context.engine.search_gateway",
                "candidate_count": len(retrieval_candidates),
                "candidates": retrieval_candidates,
            },
        }
    except (ImportError, RuntimeError, ValueError, TypeError, OSError) as exc:
        return {
            "enabled": True,
            "provider": "ContextCatalog+ContextEngine",
            "status": "degraded",
            "query": query,
            "descriptor_pack": descriptor_summaries,
            "generated_context_pack": generated_context_summaries,
            "context_pack": {},
            "verify_pack": {},
            "graph_constrained_retrieval": {"candidate_count": 0, "candidates": []},
            "error_message": str(exc),
        }


def _serialize_distilled_knowledge_unit(unit: Any) -> dict[str, Any]:
    return {
        "knowledge_id": str(getattr(unit, "knowledge_id", "") or ""),
        "knowledge_type": str(getattr(unit, "knowledge_type", "") or ""),
        "pattern_summary": str(getattr(unit, "pattern_summary", "") or ""),
        "confidence": float(getattr(unit, "confidence", 0.0) or 0.0),
        "occurrence_count": int(getattr(unit, "occurrence_count", 0) or 0),
        "prevention_hint": getattr(unit, "prevention_hint", None),
        "metadata": dict(getattr(unit, "metadata", {}) or {}),
    }


def _build_aggregate_distilled_knowledge_pack(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
) -> dict[str, Any]:
    query = " ".join(
        token
        for token in (
            _aggregate_objective_from_messages(command.messages),
            selected_lobe.output_contract,
            " ".join(str(signal) for signal in plan.metadata.get("failure_signals") or ()),
        )
        if token
    )
    try:
        from polaris.cells.cognitive.knowledge_distiller.public.contracts import RetrieveKnowledgeQueryV1
        from polaris.cells.cognitive.knowledge_distiller.public.service import KnowledgeDistillerService

        result = KnowledgeDistillerService(workspace=command.workspace).retrieve_knowledge(
            RetrieveKnowledgeQueryV1(
                workspace=command.workspace,
                query=query,
                top_k=5,
                role_filter=selected_role,
                min_confidence=0.3,
            )
        )
        return {
            "enabled": True,
            "provider": "KnowledgeDistillerService.retrieve_knowledge",
            "status": "ok",
            "query": result.query,
            "total_available": result.total_available,
            "knowledge_units": [_serialize_distilled_knowledge_unit(unit) for unit in result.knowledge_units],
        }
    except (ImportError, RuntimeError, ValueError, TypeError, OSError) as exc:
        return {
            "enabled": True,
            "provider": "KnowledgeDistillerService.retrieve_knowledge",
            "status": "degraded",
            "query": query,
            "total_available": 0,
            "knowledge_units": [],
            "error_message": str(exc),
        }


def _aggregate_suspected_files_from_failure_evidence(failure_evidence: Mapping[str, Any]) -> list[str]:
    files: list[str] = []
    for key in ("changed_files", "target_paths", "candidate_files"):
        value = failure_evidence.get(key)
        if isinstance(value, str) and value.strip():
            files.append(value.strip())
        elif isinstance(value, (list, tuple)):
            files.extend(str(item).strip() for item in value if str(item or "").strip())
    return files[:20]


def _distill_aggregate_lobe_result(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    chain_turn_index: int,
    result: RoleExecutionResultV1,
) -> dict[str, Any]:
    failure_evidence = dict(plan.metadata.get("failure_evidence") or {})
    error_summary = (
        result.error_message
        or failure_evidence.get("compiler_output")
        or failure_evidence.get("typecheck_output")
        or failure_evidence.get("apply_error")
        or ""
    )
    structured_findings: dict[str, Any] = {
        "task_progress": selected_lobe.phase,
        "action_taken": selected_lobe.output_contract,
        "verified_results": [result.status] if result.ok else [],
        "patched_files": [],
        "suspected_files": _aggregate_suspected_files_from_failure_evidence(failure_evidence),
        "_findings_trajectory": [
            {
                "lobe_id": selected_lobe.lobe_id,
                "role_id": selected_role,
                "status": result.status,
                "chain_turn_index": chain_turn_index,
            }
        ],
    }
    if error_summary:
        structured_findings["error_summary"] = str(error_summary)
    try:
        from polaris.cells.cognitive.knowledge_distiller.public.contracts import DistillSessionCommandV1
        from polaris.cells.cognitive.knowledge_distiller.public.service import KnowledgeDistillerService

        distill_session_id = (
            result.session_id
            or command.session_id
            or result.task_id
            or result.run_id
            or command.run_id
            or f"aggregate:{selected_lobe.lobe_id}:{chain_turn_index}"
        )
        distillation = KnowledgeDistillerService(workspace=command.workspace).distill_session(
            DistillSessionCommandV1(
                workspace=command.workspace,
                session_id=distill_session_id,
                run_id=result.run_id or command.run_id,
                structured_findings=structured_findings,
                task_progress=selected_lobe.phase,
                outcome="completed" if result.ok else "failed",
                metadata={
                    "role": selected_role,
                    "lobe_id": selected_lobe.lobe_id,
                    "aggregate_model_id": plan.aggregate_model_id,
                    "chain_turn_index": chain_turn_index,
                },
            )
        )
        return {
            "enabled": True,
            "provider": "KnowledgeDistillerService.distill_session",
            "status": "ok",
            "knowledge_units_created": distillation.knowledge_units_created,
            "patterns_extracted": list(distillation.patterns_extracted),
            "knowledge_ids": list(distillation.knowledge_ids),
        }
    except (ImportError, RuntimeError, ValueError, TypeError, OSError) as exc:
        return {
            "enabled": True,
            "provider": "KnowledgeDistillerService.distill_session",
            "status": "degraded",
            "knowledge_units_created": 0,
            "patterns_extracted": [],
            "knowledge_ids": [],
            "error_message": str(exc),
        }


def _select_aggregate_lobe_chain(
    *,
    plan: AggregateRolePlanResultV1,
    command: AggregateChatCompletionsCommandV1,
) -> tuple[AggregateRoleLobeV1, ...]:
    max_lobes = _aggregate_max_lobe_turns(command)
    start_lobe = _select_aggregate_execution_lobe(plan)
    ordered_lobes = list(plan.lobes)
    try:
        start_index = ordered_lobes.index(start_lobe)
    except ValueError:
        start_index = 0
    candidates = ordered_lobes[start_index:]
    selected = [lobe for lobe in candidates if _lobe_has_current_role(lobe, plan)]
    if not selected and _lobe_has_current_role(start_lobe, plan):
        selected = [start_lobe]
    return tuple(selected[:max_lobes])


def _select_aggregate_execution_role(
    *,
    plan: AggregateRolePlanResultV1,
    command: AggregateChatCompletionsCommandV1,
    selected_lobe: AggregateRoleLobeV1,
) -> str:
    current_roles = set(plan.current_role_ids)
    for source in (command.metadata, command.context):
        raw_role = source.get("aggregate_execution_role") if isinstance(source, Mapping) else None
        if raw_role is None and isinstance(source, Mapping):
            raw_role = source.get("execution_role")
        role = str(raw_role or "").strip()
        if role and role in current_roles:
            return role
    preferred_by_lobe: dict[str, tuple[str, ...]] = {
        "constraint_boundary_generator": ("architect", "qa"),
        "dialectic_self_heal_loop": ("chief_engineer", "qa"),
        "hippocampus_controller": ("director",),
        "tool_commit_guard": ("director", "qa"),
        "task_market_allocator": ("pm", "director", "qa"),
    }
    for role in preferred_by_lobe.get(selected_lobe.lobe_id, selected_lobe.role_ids):
        if role in current_roles and role in selected_lobe.role_ids:
            return role
    for role in selected_lobe.role_ids:
        if role in current_roles:
            return role
    for role in ("director", "chief_engineer", "architect", "pm", "qa"):
        if role in current_roles:
            return role
    if plan.current_role_ids:
        return plan.current_role_ids[0]
    raise ValueError("aggregate single_turn requires at least one concrete role")


def _selected_message_index(messages: tuple[AggregateChatMessageV1, ...]) -> int:
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role.lower() == "user":
            return index
    return len(messages) - 1


def _aggregate_history_from_messages(messages: tuple[AggregateChatMessageV1, ...]) -> tuple[tuple[str, str], ...]:
    selected_index = _selected_message_index(messages)
    return tuple((message.role, message.content) for index, message in enumerate(messages) if index != selected_index)


def _build_aggregate_lobe_directive(
    *,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    chain_turn_index: int,
) -> dict[str, Any]:
    return {
        "schema": "aggregate_lobe_directive.v1",
        "aggregate_model_id": plan.aggregate_model_id,
        "chain_turn_index": chain_turn_index,
        "lobe_id": selected_lobe.lobe_id,
        "phase": selected_lobe.phase,
        "role_id": selected_role,
        "role_ids": list(selected_lobe.role_ids),
        "virtual_role_ids": list(selected_lobe.virtual_role_ids),
        "compute_tier": selected_lobe.compute_tier,
        "capability_refs": list(selected_lobe.capability_refs),
        "attention_masks": list(selected_lobe.attention_masks),
        "memory_triggers": list(selected_lobe.memory_triggers),
        "handoff_keys": list(selected_lobe.handoff_keys),
        "takeover_triggers": list(selected_lobe.takeover_triggers),
        "output_contract": selected_lobe.output_contract,
        "truthful_migration": "Only role_id is executed; virtual_role_ids are planning constructs.",
    }


def _summarize_aggregate_memory_pack(memory_recall_pack: Mapping[str, Any]) -> dict[str, Any]:
    projections = memory_recall_pack.get("projections")
    projection_summaries: list[dict[str, Any]] = []
    if isinstance(projections, list):
        for item in projections[:5]:
            if not isinstance(item, Mapping):
                continue
            memory = item.get("memory")
            memory_payload = dict(memory) if isinstance(memory, Mapping) else {}
            projection_summaries.append(
                {
                    "memory_id": memory_payload.get("memory_id"),
                    "content": memory_payload.get("content"),
                    "injection_allowed": bool(item.get("injection_allowed")),
                    "injection_reason": item.get("injection_reason"),
                }
            )
    return {
        "enabled": bool(memory_recall_pack.get("enabled")),
        "provider": memory_recall_pack.get("provider"),
        "status": memory_recall_pack.get("status") or "skipped",
        "triggers": list(memory_recall_pack.get("triggers") or ()),
        "query": memory_recall_pack.get("query"),
        "projection_count": memory_recall_pack.get("projection_count", 0),
        "injection_allowed_count": memory_recall_pack.get("injection_allowed_count", 0),
        "projections": projection_summaries,
    }


def _build_aggregate_lobe_turn_envelope(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    chain_turn_index: int,
    prior_handoffs: tuple[Mapping[str, Any], ...],
    memory_recall_pack: Mapping[str, Any],
    contextos_attention_budget_pack: Mapping[str, Any],
    task_market_projection_pack: Mapping[str, Any],
    context_governance_pack: Mapping[str, Any],
    distilled_knowledge_pack: Mapping[str, Any],
) -> str:
    payload: dict[str, Any] = {
        "schema": "polaris.aggregate_lobe_turn.v1",
        "original_objective": _aggregate_objective_from_messages(command.messages),
        "execution_mode": command.execution_mode,
        "lobe_directive": _build_aggregate_lobe_directive(
            plan=plan,
            selected_lobe=selected_lobe,
            selected_role=selected_role,
            chain_turn_index=chain_turn_index,
        ),
        "failure": {
            "signals": list(plan.metadata.get("failure_signals") or ()),
            "evidence": dict(plan.metadata.get("failure_evidence") or {}),
            "takeover_evidence_status": dict(plan.metadata.get("takeover_evidence_status") or {}),
            "takeover_directive": (
                {
                    "trigger": plan.takeover_directive.trigger,
                    "lobe_id": plan.takeover_directive.lobe_id,
                    "action_contract": plan.takeover_directive.action_contract,
                }
                if plan.takeover_directive is not None
                else None
            ),
        },
        "prior_handoffs": [dict(item) for item in prior_handoffs],
        "memory_recall": _summarize_aggregate_memory_pack(memory_recall_pack),
        "contextos_attention_budget": dict(contextos_attention_budget_pack),
        "context_governance": dict(context_governance_pack),
        "distilled_knowledge": dict(distilled_knowledge_pack),
        "runtime_projection": {
            "provider": task_market_projection_pack.get("provider"),
            "status": task_market_projection_pack.get("status"),
            "summary": task_market_projection_pack.get("summary") or {},
        },
        "response_contract": selected_lobe.output_contract,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _aggregate_execution_context(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    chain_turn_index: int = 0,
    prior_handoffs: tuple[Mapping[str, Any], ...] = (),
    memory_recall_pack: Mapping[str, Any] | None = None,
    contextos_attention_budget_pack: Mapping[str, Any] | None = None,
    task_market_projection_pack: Mapping[str, Any] | None = None,
    context_governance_pack: Mapping[str, Any] | None = None,
    distilled_knowledge_pack: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    context = dict(command.context)
    recall_pack = dict(memory_recall_pack or {})
    attention_budget_pack = dict(contextos_attention_budget_pack or {})
    task_projection_pack = dict(task_market_projection_pack or {})
    governance_pack = dict(context_governance_pack or {})
    knowledge_pack = dict(distilled_knowledge_pack or {})
    lobe_directive = _build_aggregate_lobe_directive(
        plan=plan,
        selected_lobe=selected_lobe,
        selected_role=selected_role,
        chain_turn_index=chain_turn_index,
    )
    context.setdefault("state_first_context_os_enabled", True)
    context["aggregate_runtime_context"] = {
        "aggregate_model_id": plan.aggregate_model_id,
        "execution_mode": command.execution_mode,
        "selected_lobe_id": selected_lobe.lobe_id,
        "selected_role_id": selected_role,
        "chain_turn_index": chain_turn_index,
        "execution_order": list(plan.execution_order),
        "prior_handoffs": [dict(item) for item in prior_handoffs],
        "lobe_directive": lobe_directive,
        "failure_signals": list(plan.metadata.get("failure_signals") or ()),
        "failure_evidence": dict(plan.metadata.get("failure_evidence") or {}),
        "takeover_evidence_status": dict(plan.metadata.get("takeover_evidence_status") or {}),
        "akashic_recall_pack": recall_pack,
        "contextos_attention_budget_pack": attention_budget_pack,
        "task_market_projection_pack": task_projection_pack,
        "context_governance_pack": governance_pack,
        "distilled_knowledge_pack": knowledge_pack,
        "takeover_directive": (
            {
                "trigger": plan.takeover_directive.trigger,
                "lobe_id": plan.takeover_directive.lobe_id,
                "action_contract": plan.takeover_directive.action_contract,
            }
            if plan.takeover_directive is not None
            else None
        ),
    }
    return context


def _aggregate_execution_metadata(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    chain_turn_index: int = 0,
    prior_handoffs: tuple[Mapping[str, Any], ...] = (),
    memory_recall_pack: Mapping[str, Any] | None = None,
    contextos_attention_budget_pack: Mapping[str, Any] | None = None,
    task_market_projection_pack: Mapping[str, Any] | None = None,
    context_governance_pack: Mapping[str, Any] | None = None,
    distilled_knowledge_pack: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(command.metadata)
    recall_pack = dict(memory_recall_pack or {})
    attention_budget_pack = dict(contextos_attention_budget_pack or {})
    task_projection_pack = dict(task_market_projection_pack or {})
    governance_pack = dict(context_governance_pack or {})
    knowledge_pack = dict(distilled_knowledge_pack or {})
    lobe_directive = _build_aggregate_lobe_directive(
        plan=plan,
        selected_lobe=selected_lobe,
        selected_role=selected_role,
        chain_turn_index=chain_turn_index,
    )
    metadata.setdefault("context_os_expected", True)
    metadata.setdefault("cognitive_runtime_required", True)
    metadata.setdefault("cognitive_runtime_mode", "mainline")
    metadata["aggregate_execution"] = {
        "planner": "roles.runtime.aggregate_chat_completions.v1",
        "aggregate_model_id": plan.aggregate_model_id,
        "execution_mode": command.execution_mode,
        "selected_lobe_id": selected_lobe.lobe_id,
        "selected_role_id": selected_role,
        "chain_turn_index": chain_turn_index,
        "selected_lobe_phase": selected_lobe.phase,
        "selected_lobe_compute_tier": selected_lobe.compute_tier,
        "lobe_directive": lobe_directive,
        "prior_handoff_count": len(prior_handoffs),
        "takeover_evidence_status": dict(plan.metadata.get("takeover_evidence_status") or {}),
        "akashic_recall_status": {
            "enabled": bool(recall_pack.get("enabled")),
            "status": recall_pack.get("status") or "skipped",
            "projection_count": recall_pack.get("projection_count", 0),
            "injection_allowed_count": recall_pack.get("injection_allowed_count", 0),
        },
        "contextos_attention_budget_status": {
            "enabled": bool(attention_budget_pack.get("enabled")),
            "status": attention_budget_pack.get("status") or "skipped",
            "phase": attention_budget_pack.get("phase"),
            "attention_score_count": len(attention_budget_pack.get("attention_scores") or ()),
        },
        "task_market_projection_status": {
            "enabled": bool(task_projection_pack.get("enabled")),
            "status": task_projection_pack.get("status") or "skipped",
            "total_active": (task_projection_pack.get("summary") or {}).get("total_active", 0),
            "dead_letter_count": (task_projection_pack.get("summary") or {}).get("dead_letter_count", 0),
        },
        "context_governance_status": {
            "enabled": bool(governance_pack.get("enabled")),
            "status": governance_pack.get("status") or "skipped",
            "retrieval_candidate_count": (governance_pack.get("graph_constrained_retrieval") or {}).get(
                "candidate_count", 0
            ),
        },
        "distilled_knowledge_status": {
            "enabled": bool(knowledge_pack.get("enabled")),
            "status": knowledge_pack.get("status") or "skipped",
            "total_available": knowledge_pack.get("total_available", 0),
            "knowledge_unit_count": len(knowledge_pack.get("knowledge_units") or ()),
        },
        "p0_runtime_integrations": [
            item.tech_id for item in plan.runtime_integrations if item.priority == "p0" and item.status == "wired"
        ],
    }
    return metadata


def _aggregate_handoff_from_result(
    *,
    sequence: int,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    result: RoleExecutionResultV1,
) -> dict[str, Any]:
    runtime_evidence = result.metadata.get("cognitive_runtime_evidence")
    receipt_id = runtime_evidence.get("receipt_id") if isinstance(runtime_evidence, Mapping) else None
    handoff_id = runtime_evidence.get("handoff_id") if isinstance(runtime_evidence, Mapping) else None
    return {
        "sequence": sequence,
        "lobe_id": selected_lobe.lobe_id,
        "role_id": selected_role,
        "status": result.status,
        "ok": result.ok,
        "output_contract": selected_lobe.output_contract,
        "output": result.output,
        "tool_calls": list(result.tool_calls),
        "receipt_id": receipt_id,
        "handoff_id": handoff_id,
    }


def _render_aggregate_execution_content(
    *,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    execution_result: RoleExecutionResultV1,
) -> str:
    payload: dict[str, Any] = {
        "aggregate_model_id": plan.aggregate_model_id,
        "execution_mode": "single_turn",
        "selected_lobe_id": selected_lobe.lobe_id,
        "selected_role_id": selected_role,
        "status": execution_result.status,
        "ok": execution_result.ok,
        "output": execution_result.output,
        "tool_calls": list(execution_result.tool_calls),
        "runtime_evidence": {
            "strategy_fingerprint": execution_result.metadata.get("strategy_fingerprint"),
            "context_os_preflight": execution_result.metadata.get("context_os_preflight"),
            "cognitive_runtime_preflight": execution_result.metadata.get("cognitive_runtime_preflight"),
            "cognitive_runtime_evidence": execution_result.metadata.get("cognitive_runtime_evidence"),
        },
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _render_aggregate_chain_content(
    *,
    plan: AggregateRolePlanResultV1,
    chain_lobes: tuple[AggregateRoleLobeV1, ...],
    chain_roles: tuple[str, ...],
    execution_results: tuple[RoleExecutionResultV1, ...],
    handoffs: tuple[Mapping[str, Any], ...],
) -> str:
    payload: dict[str, Any] = {
        "aggregate_model_id": plan.aggregate_model_id,
        "execution_mode": "lobe_chain",
        "executed_lobes": [lobe.lobe_id for lobe in chain_lobes],
        "executed_roles": list(chain_roles),
        "status": "ok" if all(result.ok for result in execution_results) else "failed",
        "ok": all(result.ok for result in execution_results),
        "handoffs": [dict(item) for item in handoffs],
        "results": [
            {
                "sequence": index,
                "lobe_id": chain_lobes[index].lobe_id if index < len(chain_lobes) else "",
                "role_id": result.role,
                "status": result.status,
                "ok": result.ok,
                "output": result.output,
                "tool_calls": list(result.tool_calls),
                "runtime_evidence": {
                    "strategy_fingerprint": result.metadata.get("strategy_fingerprint"),
                    "context_os_preflight": result.metadata.get("context_os_preflight"),
                    "cognitive_runtime_preflight": result.metadata.get("cognitive_runtime_preflight"),
                    "cognitive_runtime_evidence": result.metadata.get("cognitive_runtime_evidence"),
                },
            }
            for index, result in enumerate(execution_results)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _aggregate_objective_from_messages(messages: tuple[AggregateChatMessageV1, ...]) -> str:
    user_messages = [message.content for message in messages if message.role.lower() == "user"]
    if user_messages:
        return user_messages[-1]
    return messages[-1].content


def _stable_completion_id(command: AggregateChatCompletionsCommandV1, objective: str) -> str:
    seed = {
        "workspace": command.workspace,
        "model": command.model,
        "objective": objective,
        "messages": [
            {
                "role": message.role,
                "content": message.content,
                "name": message.name,
            }
            for message in command.messages
        ],
        "role_ids": list(command.role_ids),
        "failure_signals": list(command.failure_signals),
        "failure_evidence": dict(command.failure_evidence),
        "domain": command.domain,
        "execution_mode": command.execution_mode,
        "session_id": command.session_id,
        "run_id": command.run_id,
    }
    digest = hashlib.sha256(json.dumps(seed, ensure_ascii=True, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return f"aggcmpl-{digest}"


def _render_aggregate_plan_content(plan: AggregateRolePlanResultV1) -> str:
    payload: dict[str, Any] = {
        "aggregate_model_id": plan.aggregate_model_id,
        "execution_mode": "plan_only",
        "current_role_ids": list(plan.current_role_ids),
        "execution_order": list(plan.execution_order),
        "required_capability_refs": list(plan.required_capability_refs),
        "failure_signals": list(plan.metadata.get("failure_signals") or ()),
        "failure_evidence": dict(plan.metadata.get("failure_evidence") or {}),
        "takeover_evidence_status": dict(plan.metadata.get("takeover_evidence_status") or {}),
        "runtime_integrations": [
            {
                "tech_id": integration.tech_id,
                "title": integration.title,
                "status": integration.status,
                "priority": integration.priority,
                "production_entrypoints": list(integration.production_entrypoints),
                "entrypoints_verified": integration.entrypoints_verified,
                "missing_entrypoints": list(integration.missing_entrypoints),
                "entrypoint_checks": [
                    {
                        "entrypoint": check.entrypoint,
                        "check_type": check.check_type,
                        "ok": check.ok,
                        "evidence": check.evidence,
                        "reason": check.reason,
                    }
                    for check in integration.entrypoint_checks
                ],
                "evidence_keys": list(integration.evidence_keys),
                "runtime_effects": list(integration.runtime_effects),
                "benefit": integration.benefit,
            }
            for integration in plan.runtime_integrations
        ],
        "compute_policy": dict(plan.compute_policy),
        "takeover_directive": (
            {
                "trigger": plan.takeover_directive.trigger,
                "lobe_id": plan.takeover_directive.lobe_id,
                "compute_tier": plan.takeover_directive.compute_tier,
                "reason": plan.takeover_directive.reason,
                "evidence_keys": list(plan.takeover_directive.evidence_keys),
                "action_contract": plan.takeover_directive.action_contract,
                "next_lobes": list(plan.takeover_directive.next_lobes),
                "status": plan.takeover_directive.status,
            }
            if plan.takeover_directive is not None
            else None
        ),
        "cognitive_ledger": [
            {
                "sequence": item.sequence,
                "lobe_id": item.lobe_id,
                "phase": item.phase,
                "compute_tier": item.compute_tier,
                "reads": list(item.reads),
                "writes": list(item.writes),
                "handoff_to": list(item.handoff_to),
                "takeover_triggers": list(item.takeover_triggers),
            }
            for item in plan.cognitive_ledger
        ],
        "warnings": list(plan.warnings),
        "truthful_migration": str(plan.metadata.get("truthful_migration") or ""),
        "lobes": [
            {
                "lobe_id": lobe.lobe_id,
                "phase": lobe.phase,
                "role_ids": list(lobe.role_ids),
                "virtual_role_ids": list(lobe.virtual_role_ids),
                "capability_refs": list(lobe.capability_refs),
                "attention_masks": list(lobe.attention_masks),
                "memory_triggers": list(lobe.memory_triggers),
                "compute_tier": lobe.compute_tier,
                "handoff_keys": list(lobe.handoff_keys),
                "takeover_triggers": list(lobe.takeover_triggers),
                "output_contract": lobe.output_contract,
                "status": lobe.status,
                "missing_role_ids": list(lobe.missing_role_ids),
            }
            for lobe in plan.lobes
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class WorkflowRoleAdapter:
    """Lazy proxy to avoid adapter-side effects at import time."""

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        from polaris.cells.roles.adapters.public.service import (
            WorkflowRoleAdapter as _WorkflowRoleAdapter,
        )

        return _WorkflowRoleAdapter(*args, **kwargs)


def execute_workflow_role(*args, **kwargs) -> Any:
    """Forward workflow role execution to `roles.adapters` public service."""
    from polaris.cells.roles.adapters.public.service import (
        execute_workflow_role as _execute_workflow_role,
    )

    return _execute_workflow_role(*args, **kwargs)


def get_role_system_prompt(*args, **kwargs) -> Any:
    """Lazy proxy to avoid control-plane import cycles at module import time."""
    from polaris.cells.llm.control_plane.public.service import (
        get_role_system_prompt as _get_role_system_prompt,
    )

    return _get_role_system_prompt(*args, **kwargs)


class WorkflowRoleResult(dict):
    """Compatibility marker type for workflow role execution results."""


def _extract_tool_calls(result: RoleTurnResult) -> tuple[str, ...]:
    names: list[str] = []
    for item in list(result.tool_calls or []):
        if not isinstance(item, dict):
            continue
        token = str(item.get("name") or item.get("tool") or "").strip()
        if token:
            names.append(token)
    return tuple(names)


def _extract_artifacts(result: RoleTurnResult) -> tuple[str, ...]:
    payload = result.structured_output if isinstance(result.structured_output, dict) else {}
    values = payload.get("artifacts")
    if not isinstance(values, list):
        return ()
    return tuple(str(item).strip() for item in values if str(item).strip())


def _copy_result_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(metadata or {})


def _copy_tool_result_metadata(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    copied: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, Mapping):
            copied.append(dict(item))
    return copied


def _copy_batch_receipt_metadata(receipt: Any) -> dict[str, Any] | None:
    if isinstance(receipt, Mapping):
        return dict(receipt)
    model_dump = getattr(receipt, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dict(dumped)
    return None


def _contract_result_metadata(result: RoleTurnResult) -> dict[str, Any]:
    metadata = _copy_result_metadata(result.metadata)
    tool_results = _copy_tool_result_metadata(result.tool_results)
    if tool_results and "tool_results" not in metadata:
        metadata["tool_results"] = tool_results
    batch_receipt = _copy_batch_receipt_metadata(result.batch_receipt)
    if batch_receipt and "batch_receipt" not in metadata:
        metadata["batch_receipt"] = batch_receipt
    return metadata


def _metadata_flag_enabled(*payloads: Mapping[str, Any] | None, key: str) -> bool:
    for payload in payloads:
        if not isinstance(payload, Mapping) or key not in payload:
            continue
        value = payload.get(key)
        if isinstance(value, bool):
            return value
        token = str(value or "").strip().lower()
        if token in {"1", "true", "yes", "on", "required"}:
            return True
        if token in {"0", "false", "no", "off", "optional", "disabled"}:
            return False
    return False


def _enforce_required_context_os(request: RoleTurnRequest) -> RoleTurnRequest:
    context_override = dict(request.context_override or {})
    metadata = dict(request.metadata or {})
    expected = _metadata_flag_enabled(
        context_override,
        metadata,
        key="context_os_expected",
    )
    if not expected:
        return request

    enabled = resolve_context_os_enabled(
        incoming_context=context_override,
        session_context_config=metadata,
        default=True,
    )
    if not enabled:
        raise RuntimeError("context_os_expected_but_disabled")

    metadata["context_os_preflight"] = {
        "expected": True,
        "enabled": True,
    }
    request.metadata = metadata
    return request


def _with_result_metadata_patch(
    result: RoleExecutionResultV1,
    patch: Mapping[str, Any],
) -> RoleExecutionResultV1:
    metadata = _copy_result_metadata(result.metadata)
    metadata.update(dict(patch))
    return RoleExecutionResultV1(
        ok=result.ok,
        status=result.status,
        role=result.role,
        workspace=result.workspace,
        task_id=result.task_id,
        session_id=result.session_id,
        run_id=result.run_id,
        output=result.output,
        thinking=result.thinking,
        tool_calls=result.tool_calls,
        artifacts=result.artifacts,
        usage=result.usage,
        metadata=metadata,
        error_code=result.error_code,
        error_message=result.error_message,
        turn_history=list(result.turn_history),
    )


def _cognitive_runtime_result_patch(
    *,
    evidence: Mapping[str, Any] | None,
    request_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    evidence_payload = (
        dict(evidence)
        if isinstance(evidence, Mapping)
        else {"available": False, "error_code": "invalid_cognitive_runtime_evidence"}
    )
    patch: dict[str, Any] = {"cognitive_runtime_evidence": evidence_payload}
    preflight = request_metadata.get("cognitive_runtime_preflight")
    if isinstance(preflight, Mapping):
        patch["cognitive_runtime_preflight"] = dict(preflight)
    context_os_preflight = request_metadata.get("context_os_preflight")
    if isinstance(context_os_preflight, Mapping):
        patch["context_os_preflight"] = dict(context_os_preflight)
    return patch


def _copy_llm_provider_policy_into_context(
    *,
    context_override: dict[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Expose role-runtime provider policy metadata to the LLM executor path."""
    result = dict(context_override)
    for key in (
        "allowed_provider_types",
        "allow_provider_types",
        "blocked_provider_types",
        "provider_type_policy",
    ):
        value = metadata.get(key)
        if value is not None:
            result[key] = value
    policy = metadata.get("llm_provider_policy")
    if isinstance(policy, Mapping):
        result["llm_provider_policy"] = dict(policy)
    return result


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _copy_cognitive_guidance(cognitive_context: Mapping[str, Any]) -> dict[str, Any]:
    analysis = cognitive_context.get("cognitive_analysis")
    analysis_payload = dict(analysis) if isinstance(analysis, Mapping) else {}
    actions = analysis_payload.get("actions_taken")
    action_values = tuple(str(item) for item in actions[:8]) if isinstance(actions, (list, tuple)) else ()
    blocked_tools = _copy_string_tuple(cognitive_context.get("blocked_tools"), limit=24)
    return {
        "intent_type": str(cognitive_context.get("intent_type") or "unknown"),
        "confidence": _safe_float(cognitive_context.get("confidence")),
        "uncertainty_score": _safe_float(cognitive_context.get("uncertainty_score")),
        "execution_path": str(cognitive_context.get("execution_path") or "unknown"),
        "clarity_level": str(analysis_payload.get("clarity_level") or "unknown"),
        "verification_needed": bool(analysis_payload.get("verification_needed")),
        "actions_taken": action_values,
        "blocked_tools": blocked_tools,
    }


def _resolve_cognitive_runtime_blocker_approval(
    *,
    context: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, str] | None:
    approval_raw = metadata.get("cognitive_runtime_approval")
    if not isinstance(approval_raw, Mapping):
        approval_raw = context.get("cognitive_runtime_approval")
    if not isinstance(approval_raw, Mapping):
        return None

    mode = (
        str(
            approval_raw.get("mode")
            or metadata.get("cognitive_runtime_approval_mode")
            or context.get("cognitive_runtime_approval_mode")
            or ""
        )
        .strip()
        .lower()
    )
    if mode != "auto_accept":
        return None

    scope = str(
        approval_raw.get("scope")
        or metadata.get("cognitive_runtime_approval_scope")
        or context.get("cognitive_runtime_approval_scope")
        or ""
    ).strip()
    if not scope:
        return None

    return {
        "mode": mode,
        "source": str(approval_raw.get("source") or "unknown").strip() or "unknown",
        "scope": scope,
        "approved_by": str(approval_raw.get("approved_by") or "unknown").strip() or "unknown",
    }


def _copy_string_tuple(raw_value: Any, *, limit: int) -> tuple[str, ...]:
    if not isinstance(raw_value, (list, tuple, set, frozenset)):
        return ()
    values: list[str] = []
    seen: set[str] = set()
    for item in raw_value:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        values.append(token)
        if len(values) >= limit:
            break
    return tuple(values)


def _deep_merge_strategy_overrides(
    base: Mapping[str, Any] | None,
    override: Mapping[str, Any] | None,
) -> dict[str, Any]:
    result = dict(base or {})
    if not isinstance(override, Mapping):
        return result
    for key, value in override.items():
        key_token = str(key or "").strip()
        if not key_token:
            continue
        existing = result.get(key_token)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            result[key_token] = _deep_merge_strategy_overrides(existing, value)
        elif isinstance(value, Mapping):
            result[key_token] = dict(value)
        elif isinstance(value, (list, tuple, set, frozenset)):
            result[key_token] = tuple(value)
        else:
            result[key_token] = value
    return result


def _copy_strategy_override(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return _deep_merge_strategy_overrides({}, value)


def _build_cognitive_strategy_override(guidance: Mapping[str, Any]) -> dict[str, Any]:
    execution_path = str(guidance.get("execution_path") or "").strip().lower()
    intent_type = str(guidance.get("intent_type") or "").strip().lower()
    uncertainty = _safe_float(guidance.get("uncertainty_score"))
    verification_needed = bool(guidance.get("verification_needed"))
    requires_deeper_context = (
        verification_needed
        or uncertainty >= 0.45
        or any(
            marker in execution_path
            for marker in (
                "full",
                "verify",
                "write",
                "plan",
                "refactor",
                "architect",
            )
        )
        or intent_type in {"code_generation", "architecture", "debugging", "root_cause"}
    )
    if not requires_deeper_context:
        return {}

    depth = 5 if uncertainty >= 0.65 else 4
    read_threshold_kb = 500 if uncertainty >= 0.65 else 350
    return {
        "exploration": {
            "map_first": True,
            "search_before_read": True,
            "max_expansion_depth": depth,
            "neighbor_expansion_aggressive": verification_needed or uncertainty >= 0.45,
        },
        "read_escalation": {
            "full_read_allowed": True,
            "full_read_threshold_kb": read_threshold_kb,
            "range_first_default": True,
            "range_first_threshold_kb": 20,
        },
        "compaction": {
            "trigger_at_budget_pct": 0.90,
            "receipt_micro_compact": True,
            "receipt_compact_threshold": 5,
        },
        "cognitive_runtime": {
            "source": "cognitive_runtime_mainline",
            "applied": True,
            "execution_path": execution_path or "unknown",
            "intent_type": intent_type or "unknown",
            "verification_needed": verification_needed,
            "uncertainty_score": round(uncertainty, 3),
        },
    }


def _extract_turn_envelope_metadata(result: RoleExecutionResultV1) -> dict[str, Any]:
    metadata = _copy_result_metadata(result.metadata)
    envelope = metadata.get("turn_envelope")
    if isinstance(envelope, Mapping):
        return dict(envelope)
    turn_id = str(metadata.get("turn_id") or "").strip()
    if not turn_id:
        return {}
    return {
        "turn_id": turn_id,
        "session_id": str(result.session_id or "").strip() or None,
        "run_id": str(result.run_id or "").strip() or None,
        "role": str(result.role or "").strip() or None,
        "task_id": str(result.task_id or "").strip() or None,
    }


def _to_contract_result(
    *,
    role: str,
    workspace: str,
    task_id: str | None,
    session_id: str | None,
    run_id: str | None,
    result: RoleTurnResult,
) -> RoleExecutionResultV1:
    error_message = str(result.error or result.tool_execution_error or "").strip()
    ok = not bool(error_message)
    status = "ok" if ok else "failed"
    if not result.is_complete and ok:
        status = "in_progress"
    return RoleExecutionResultV1(
        ok=ok,
        status=status,
        role=role,
        workspace=workspace,
        task_id=task_id,
        session_id=session_id,
        run_id=run_id,
        output=str(result.content or ""),
        thinking=result.thinking,
        tool_calls=_extract_tool_calls(result),
        artifacts=_extract_artifacts(result),
        usage=dict(result.execution_stats or {}),
        metadata=_contract_result_metadata(result),
        error_code=None if ok else "role_runtime_error",
        error_message=None if ok else (error_message or "unknown runtime error"),
        turn_history=list(result.turn_history) if result.turn_history else [],
    )


class RoleRuntimeService(IRoleRuntime):
    """Contract-first service facade for `roles.runtime`."""

    _DOMAIN_ALIASES = RoleDomainPolicy.DOMAIN_ALIASES
    _DEFAULT_EXECUTION_DOMAIN = RoleDomainPolicy.DEFAULT_EXECUTION_DOMAIN

    def __init__(self) -> None:
        self._kernels: dict[str, RoleExecutionKernel] = {}
        self._kernel_lock = Lock()
        self._turn_indices: dict[str, int] = {}  # session_id -> turn_index

    def _get_kernel(self, workspace: str) -> RoleExecutionKernel:
        token = str(workspace or "").strip()
        if not token:
            token = "."
        with self._kernel_lock:
            kernel = self._kernels.get(token)
            if kernel is None:
                if not registry.list_roles():
                    load_core_roles()
                kernel = RoleExecutionKernel(workspace=token, registry=registry)
                self._kernels[token] = kernel
        return kernel

    @classmethod
    def _normalize_execution_domain(cls, domain: str | None) -> str | None:
        return RoleDomainPolicy.normalize_domain(domain)

    @classmethod
    def _strategy_domain_from_execution(cls, execution_domain: str) -> str:
        return RoleDomainPolicy.strategy_domain_from_execution(execution_domain)

    @classmethod
    def _resolve_execution_domain(
        cls,
        command_domain: str | None = None,
        context: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        role: str | None = None,
    ) -> tuple[str, bool]:
        resolved = RoleDomainPolicy.resolve(
            command_domain=command_domain,
            context=context,
            metadata=metadata,
            role=role,
        )
        return resolved.execution_domain, resolved.explicit

    # ── Strategy resolution (WS2) ───────────────────────────────────────────

    def _next_turn_index(self, session_id: str | None) -> int:
        """Return and increment the turn counter for a session."""
        if not session_id:
            return 0
        idx = self._turn_indices.get(session_id, 0)
        self._turn_indices[session_id] = idx + 1
        return idx

    def _resolve_session_override(self, session_id: str) -> dict[str, Any] | None:
        """Read session strategy override from roles.session source-of-truth.

        Delegates to extracted persistence module for actual implementation.
        """
        return _resolve_session_override_impl(session_id)

    def resolve_strategy_profile(
        self,
        domain: str | None = None,
        role: str | None = None,
        session_override: dict[str, Any] | None = None,
        current_turn_override: Mapping[str, Any] | None = None,
        prefer_domain_default: bool = False,
    ) -> ResolvedStrategy:
        """Resolve the effective strategy profile for a run.

        Resolution order (StrategyRegistry.resolve):
            1. Explicit session_override (highest priority)
            2. Domain-specific default
            3. canonical_balanced fallback

        Args:
            domain: Target domain ("code", "document", "research", "general").
            role: Role name ("director", "pm", etc.).
            session_override: Session-level strategy override dict.
            prefer_domain_default: When True, domain default takes precedence
                over role default for the base profile selection.

        Returns:
            ResolvedStrategy with profile, bundle, and hash.
        """
        execution_domain, _ = self._resolve_execution_domain(
            command_domain=domain,
            role=role,
        )
        strategy_domain = self._strategy_domain_from_execution(execution_domain)
        registry = get_registry()
        merged_override = _deep_merge_strategy_overrides(session_override, current_turn_override)
        return registry.resolve(
            domain=strategy_domain,
            role=None if prefer_domain_default else role,
            override=merged_override or None,
        )

    def create_strategy_run(
        self,
        domain: str,
        role: str | None,
        session_id: str | None,
        budget: ContextBudget | None,
        workspace: str,
        domain_explicit: bool = False,
        include_session_override: bool = False,
        current_turn_override: Mapping[str, Any] | None = None,
    ) -> StrategyRunContext:
        """Create a per-turn StrategyRunContext with resolved strategy identity.

        This is the canonical constructor for a strategy run. Call before each
        LLM turn; emit the receipt after the turn completes.

        Args:
            domain: Target execution domain.
            role: Role name.
            session_id: Session ID (None for task/oneshot runs).
            budget: Current context budget snapshot.
            workspace: Workspace directory path.
            domain_explicit: Whether the caller explicitly requested a domain.
            include_session_override: When True, attempt to load session-level
                strategy override from roles.session source-of-truth.

        Returns:
            StrategyRunContext carrying strategy identity and mutable accumulators.
        """
        # Pull session-level override from roles.session if session_id is available.
        session_override: dict[str, Any] | None = None
        if include_session_override and session_id:
            session_override = self._resolve_session_override(session_id)

        execution_domain, _ = self._resolve_execution_domain(
            command_domain=domain,
            role=role,
        )
        resolved = self.resolve_strategy_profile(
            domain=execution_domain,
            role=role,
            session_override=session_override,
            current_turn_override=current_turn_override,
            prefer_domain_default=domain_explicit,
        )
        turn_index = self._next_turn_index(session_id)
        return StrategyRunContext.from_resolved(
            resolved,
            turn_index=turn_index,
            session_id=session_id or "",
            workspace=workspace,
            role=role,
            domain=execution_domain,
            budget=budget,
        )

    @staticmethod
    def emit_strategy_receipt(
        run_ctx: StrategyRunContext,
        workspace: str,
    ) -> Path:
        """Persist a strategy run's receipt to `<metadata_dir>/runtime/strategy_runs/`.

        Delegates to extracted persistence module for actual implementation.
        """
        return _emit_strategy_receipt_impl(run_ctx, workspace)

    # ── History projection ──────────────────────────────────────────────────

    @staticmethod
    async def _project_host_history(
        *,
        session_id: str,
        role: str,
        workspace: str,
        history: tuple[tuple[str, str], ...] | list[tuple[str, str]] | None,
        context: Mapping[str, Any] | None,
        session_context_config: Mapping[str, Any] | None = None,
        history_limit: int = 10,
        session_title: str = "",
    ) -> tuple[tuple[tuple[str, str], ...], dict[str, Any], dict[str, Any]]:
        """Project host history for session continuity.

        Delegates to extracted persistence module for actual implementation.
        """
        return await _project_host_history_impl(
            session_id=session_id,
            role=role,
            workspace=workspace,
            history=history,
            context=context,
            session_context_config=session_context_config,
            history_limit=history_limit,
            session_title=session_title,
        )

    @staticmethod
    async def _persist_session_turn_state(
        command: ExecuteRoleSessionCommandV1,
        *,
        turn_history: list[tuple[str, str]],
        turn_events_metadata: list[dict[str, Any]] | None = None,
    ) -> None:
        """Persist role session turn state to roles.session source-of-truth.

        Delegates to extracted persistence module for actual implementation.
        """
        await _persist_session_turn_state_impl(
            command,
            turn_history=turn_history,
            turn_events_metadata=turn_events_metadata,
        )

    def _emit_cognitive_runtime_shadow_artifacts(
        self,
        *,
        source: str,
        workspace: str,
        role: str,
        task_id: str | None,
        session_id: str | None,
        run_id: str | None,
        result: RoleExecutionResultV1,
        metadata: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        required = _metadata_flag_enabled(context, metadata, key="cognitive_runtime_required")
        mode = resolve_cognitive_runtime_mode(context=context, metadata=metadata)
        evidence: dict[str, Any] = {
            "required": required,
            "source": source,
            "cognitive_runtime_mode": mode.value,
            "receipt_recorded": False,
            "handoff_exported": False,
        }
        if mode is CognitiveRuntimeMode.OFF:
            if required:
                raise RuntimeError("cognitive_runtime_required_but_off")
            return evidence
        try:
            from polaris.cells.factory.cognitive_runtime.public.contracts import (
                ExportHandoffPackCommandV1,
                RecordRuntimeReceiptCommandV1,
            )
            from polaris.cells.factory.cognitive_runtime.public.service import (
                get_cognitive_runtime_public_service,
            )

            service = get_cognitive_runtime_public_service()
            try:
                turn_envelope = _extract_turn_envelope_metadata(result)
                result_metadata = _copy_result_metadata(result.metadata)
                context_os_audit = result_metadata.get("context_os_audit")
                receipt_payload: dict[str, Any] = {
                    "source": source,
                    "role": role,
                    "task_id": task_id,
                    "status": result.status,
                    "ok": result.ok,
                    "tool_calls": list(result.tool_calls),
                    "artifacts": list(result.artifacts),
                    "output_length": len(str(result.output or "")),
                    "has_thinking": bool(str(result.thinking or "").strip()),
                    "error_code": result.error_code,
                    "error_message": result.error_message,
                    "cognitive_runtime_mode": mode.value,
                }
                if isinstance(context_os_audit, Mapping):
                    receipt_payload["context_os_audit"] = dict(context_os_audit)
                    evidence["context_os_audit_recorded"] = True
                receipt_result = service.record_runtime_receipt(
                    RecordRuntimeReceiptCommandV1(
                        workspace=workspace,
                        receipt_type="role_runtime_turn",
                        session_id=session_id,
                        run_id=run_id,
                        payload=receipt_payload,
                        turn_envelope=turn_envelope,
                    )
                )
                if not bool(getattr(receipt_result, "ok", False)):
                    error_message = str(getattr(receipt_result, "error_message", "") or "").strip()
                    error_code = str(getattr(receipt_result, "error_code", "") or "").strip()
                    raise RuntimeError(error_message or error_code or "runtime_receipt_failed")
                receipt = getattr(receipt_result, "receipt", None)
                receipt_id = str(getattr(receipt, "receipt_id", "") or "").strip()
                if required and not receipt_id:
                    raise RuntimeError("runtime_receipt_missing_id")
                if receipt_id:
                    evidence["receipt_id"] = receipt_id
                evidence["receipt_recorded"] = True
                if session_id:
                    handoff_turn_envelope = dict(turn_envelope)
                    if receipt_id:
                        receipt_ids = list(handoff_turn_envelope.get("receipt_ids") or [])
                        if receipt_id not in receipt_ids:
                            receipt_ids.append(receipt_id)
                        handoff_turn_envelope["receipt_ids"] = receipt_ids
                    handoff_result = service.export_handoff_pack(
                        ExportHandoffPackCommandV1(
                            workspace=workspace,
                            session_id=session_id,
                            run_id=run_id,
                            reason=f"{source}:{result.status}",
                            turn_envelope=handoff_turn_envelope,
                        )
                    )
                    if not bool(getattr(handoff_result, "ok", False)):
                        error_message = str(getattr(handoff_result, "error_message", "") or "").strip()
                        error_code = str(getattr(handoff_result, "error_code", "") or "").strip()
                        raise RuntimeError(error_message or error_code or "handoff_export_failed")
                    handoff = getattr(handoff_result, "handoff", None)
                    handoff_id = str(getattr(handoff, "handoff_id", "") or "").strip()
                    if required and not handoff_id:
                        raise RuntimeError("handoff_missing_id")
                    if handoff_id:
                        evidence["handoff_id"] = handoff_id
                    evidence["handoff_exported"] = True
                else:
                    evidence["handoff_skipped_reason"] = "no_session_id"
            finally:
                service.close()
        except (RuntimeError, ValueError) as exc:
            evidence["error_message"] = str(exc)
            if required:
                raise
            logger.warning(
                "Failed to emit Cognitive Runtime shadow artifacts for role=%s session=%s run=%s",
                role,
                session_id,
                run_id,
                exc_info=True,
            )
        return evidence

    @staticmethod
    def resolve_strategy(
        domain: str | None = None,
        role: str | None = None,
        overlay_id: str | None = None,
        session_override: dict[str, Any] | None = None,
    ) -> ResolvedStrategy:
        """Resolve the effective strategy for a role execution.

        Resolution cascade (highest → lowest priority):
            1. explicit session_override (caller-supplied overrides)
            2. role overlay (matched by role + target domain + parent profile)
            3. role-default profile (from StrategyRegistry._ROLE_DEFAULTS)
            4. domain-default profile (from StrategyRegistry._DOMAIN_DEFAULTS)
            5. canonical_balanced fallback

        Args:
            domain: Target domain (e.g. ``"code"``, ``"document"``).
            role: Role name (e.g. ``"director"``, ``"architect"``, ``"qa"``).
            overlay_id: Specific overlay to apply
                (e.g. ``"director.execution"``, ``"architect.analysis"``).
                If None, the RoleOverlayRegistry selects the best matching
                overlay for the resolved role-default profile.
            session_override: Caller-supplied overrides merged last.

        Returns:
            ResolvedStrategy with the fully resolved profile, bundle, and hash.
            When an overlay is applied, the returned profile_id is the overlay_id
            (e.g. ``"director.execution"``), not the parent profile id.

        Raises:
            KeyError: If the resolved profile or overlay is not found.
        """
        from polaris.kernelone.context import (
            ResolvedStrategy,
            StrategyProfile,
            StrategyRegistry,
            get_overlay_registry,
        )

        execution_domain, domain_explicit = RoleRuntimeService._resolve_execution_domain(
            command_domain=domain,
            role=role,
        )
        strategy_domain = RoleRuntimeService._strategy_domain_from_execution(
            execution_domain,
        )

        # Step 1: resolve the base profile via StrategyRegistry
        registry = StrategyRegistry.get_instance()
        parent_strategy = registry.resolve(
            domain=strategy_domain,
            role=None if domain_explicit else role,
            override=None,
        )

        # Step 2: apply role overlay if available
        if role is not None:
            overlay_reg = get_overlay_registry()
            try:
                # Determine parent profile id from the resolved base strategy
                parent_profile_id = parent_strategy.profile.profile_id
                if overlay_id:
                    # Explicit overlay requested: look it up directly
                    overlay = overlay_reg.get(overlay_id)
                    # Verify it matches the requested role
                    if overlay.role != role:
                        raise KeyError(f"overlay {overlay_id!r} is for role {overlay.role!r}, not {role!r}")
                    # Merge overlay + session overrides on top of parent's effective overrides
                    from polaris.kernelone.context.strategy_overlay_registry import _deep_merge

                    merged_overrides: dict[str, Any] = _deep_merge(
                        parent_strategy.overrides_applied,
                        overlay.overrides_by_strategy(),
                    )
                    if session_override:
                        merged_overrides = _deep_merge(merged_overrides, session_override)

                    # Build the effective profile with overlay's overlay_id
                    effective_profile = StrategyProfile(
                        profile_id=overlay.overlay_id,
                        profile_version="overlay.1",
                        bundle_id=parent_strategy.bundle.bundle_id,
                        overrides=merged_overrides,
                        metadata=parent_strategy.profile.metadata,
                    )
                    new_hash = registry.resolve_profile_hash(effective_profile)
                    return ResolvedStrategy(
                        profile=effective_profile,
                        bundle=parent_strategy.bundle,
                        profile_hash=new_hash,
                        overrides_applied=merged_overrides,
                    )
                else:
                    # Auto-select: let the overlay registry find the best match
                    resolved = overlay_reg.resolve(
                        role=role,
                        parent_profile_id=parent_profile_id,
                        domain=execution_domain,
                        parent_overrides=parent_strategy.overrides_applied,
                        explicit_override=session_override,
                    )
                    # Build effective profile with overlay's overlay_id
                    effective_profile = StrategyProfile(
                        profile_id=resolved.profile_id,
                        profile_version="overlay.1",
                        bundle_id=parent_strategy.bundle.bundle_id,
                        overrides=resolved.effective_overrides,
                        metadata=parent_strategy.profile.metadata,
                    )
                    new_hash = registry.resolve_profile_hash(effective_profile)
                    return ResolvedStrategy(
                        profile=effective_profile,
                        bundle=parent_strategy.bundle,
                        profile_hash=new_hash,
                        overrides_applied=resolved.effective_overrides,
                    )
            except KeyError:
                # No overlay registered for this role; fall through to base profile
                pass

        # Step 3: no overlay found — return base profile with session override
        if session_override:
            return registry.resolve(
                domain=strategy_domain,
                role=None if domain_explicit else role,
                override=session_override,
            )
        return parent_strategy

    @staticmethod
    def _build_task_request(command: ExecuteRoleTaskCommandV1) -> RoleTurnRequest:
        metadata = dict(command.metadata)
        if command.timeout_seconds is not None:
            metadata["timeout_seconds"] = int(command.timeout_seconds)
        context_override, metadata = _augment_context_with_handoff_rehydration_impl(
            workspace=command.workspace,
            role=command.role,
            session_id=command.session_id,
            context=command.context,
            metadata=metadata,
        )
        execution_domain, _ = RoleRuntimeService._resolve_execution_domain(
            command_domain=command.domain,
            context=context_override,
            metadata=metadata,
            role=command.role,
        )
        metadata["domain"] = execution_domain
        context_override = _augment_context_with_repo_intelligence_impl(
            workspace=command.workspace,
            domain=execution_domain,
            context=context_override,
            metadata=metadata,
        )
        context_override = _copy_llm_provider_policy_into_context(
            context_override=context_override,
            metadata=metadata,
        )
        if "repo_intelligence" in context_override:
            metadata["repo_intelligence_enabled"] = True
        validate_output = bool(metadata.get("validate_output", True))
        max_retries = int(metadata.get("max_retries", 1))
        return RoleTurnRequest(
            mode=RoleExecutionMode.WORKFLOW,
            workspace=command.workspace,
            message=command.objective,
            domain=execution_domain,
            context_override=context_override,
            task_id=command.task_id,
            run_id=command.run_id,
            validate_output=validate_output,
            max_retries=max(0, max_retries),
            metadata=metadata,
        )

    @staticmethod
    def _build_session_request(
        command: ExecuteRoleSessionCommandV1,
        *,
        include_session_snapshot: bool = False,
    ) -> RoleTurnRequest:
        metadata = dict(command.metadata)
        context = dict(command.context)
        if command.timeout_seconds is not None:
            timeout_seconds = int(command.timeout_seconds)
            metadata["timeout_seconds"] = timeout_seconds
            context.setdefault("llm_call_timeout_seconds", timeout_seconds)
            context.setdefault("request_timeout_seconds", timeout_seconds)
            context.setdefault("timeout_seconds", timeout_seconds)
        metadata["session_id"] = command.session_id
        metadata["stream"] = bool(command.stream)
        context_override, metadata = _augment_context_with_handoff_rehydration_impl(
            workspace=command.workspace,
            role=command.role,
            session_id=command.session_id,
            context=context,
            metadata=metadata,
        )
        execution_domain, _ = RoleRuntimeService._resolve_execution_domain(
            command_domain=command.domain,
            context=context_override,
            metadata=metadata,
            role=command.role,
        )
        metadata["domain"] = execution_domain
        context_override = _augment_context_with_repo_intelligence_impl(
            workspace=command.workspace,
            domain=execution_domain,
            context=context_override,
            metadata=metadata,
        )
        context_override = _copy_llm_provider_policy_into_context(
            context_override=context_override,
            metadata=metadata,
        )
        if include_session_snapshot:
            # Wave 2: Load ContextOS snapshot via extracted context_adapter module.
            context_override = _load_session_context_os_snapshot_impl(
                session_id=str(command.session_id or "").strip(),
                workspace=command.workspace,
                role=command.role,
                context_override=context_override,
            )
            # SSOT Fix: Also update command.context directly so that
            # _persist_session_turn_state (which uses command.context) has
            # access to session_turn_events on the NEXT turn.
            # command.context is a mutable dict (frozen dataclass only prevents
            # attribute reassignment, not dict modification in place).
            session_turn_events = context_override.get("session_turn_events")
            context_os_snapshot = context_override.get("context_os_snapshot")
            if isinstance(command.context, dict):
                if isinstance(session_turn_events, list) and session_turn_events:
                    command.context["session_turn_events"] = session_turn_events
                if isinstance(context_os_snapshot, dict) and context_os_snapshot:
                    command.context["context_os_snapshot"] = context_os_snapshot

        if "repo_intelligence" in context_override:
            metadata["repo_intelligence_enabled"] = True
        prompt_appendix = str(metadata.pop("prompt_appendix", "") or "").strip() or None
        validate_output = bool(metadata.get("validate_output", True))
        max_retries = int(metadata.get("max_retries", 1))
        return RoleTurnRequest(
            mode=RoleExecutionMode.CHAT,
            workspace=command.workspace,
            message=command.user_message,
            domain=execution_domain,
            history=list(command.history),
            prompt_appendix=prompt_appendix,
            context_override=context_override,
            task_id=command.task_id,
            run_id=command.run_id,
            validate_output=validate_output,
            max_retries=max(0, max_retries),
            metadata=metadata,
        )

    @staticmethod
    async def _apply_cognitive_runtime_preflight(
        *,
        request: RoleTurnRequest,
        role: str,
        workspace: str,
        session_id: str | None,
    ) -> RoleTurnRequest:
        context_override = dict(request.context_override or {})
        metadata = dict(request.metadata or {})
        mode = resolve_cognitive_runtime_mode(context=context_override, metadata=metadata)
        required = _metadata_flag_enabled(
            context_override,
            metadata,
            key="cognitive_runtime_required",
        )
        if mode is CognitiveRuntimeMode.OFF:
            if required:
                raise RuntimeError("cognitive_runtime_required_but_off")
            metadata["cognitive_runtime_preflight"] = {
                "mode": mode.value,
                "applied": False,
                "reason": "off",
            }
            request.metadata = metadata
            return request

        if mode is not CognitiveRuntimeMode.MAINLINE:
            metadata["cognitive_runtime_preflight"] = {
                "mode": mode.value,
                "applied": False,
                "reason": "shadow_mode",
            }
            request.metadata = metadata
            return request

        from polaris.kernelone.cognitive.middleware import CognitiveMiddleware

        middleware = CognitiveMiddleware(workspace=workspace, enabled=True)
        cognitive_context = await middleware.process(
            message=str(request.message or ""),
            role_id=role,
            session_id=session_id,
        )
        if not bool(cognitive_context.get("enabled")):
            # Telemetry refactor: the middleware degrades to enabled=False on an infra
            # failure; carry its degraded_reason into the breadcrumb and the raised error
            # so the actionable cause is not absorbed into a generic "unavailable".
            degraded_reason = str(cognitive_context.get("degraded_reason") or "").strip()
            metadata["cognitive_runtime_preflight"] = {
                "mode": mode.value,
                "applied": False,
                "reason": f"mainline_unavailable:{degraded_reason}" if degraded_reason else "mainline_unavailable",
            }
            request.metadata = metadata
            raise RuntimeError(
                f"cognitive_runtime_mainline_unavailable:{degraded_reason}"
                if degraded_reason
                else "cognitive_runtime_mainline_unavailable"
            )

        approved_blocker: dict[str, str] | None = None
        block_reason = ""
        if bool(cognitive_context.get("blocked")):
            reason = str(cognitive_context.get("block_reason") or "blocked").strip()
            approved_blocker = _resolve_cognitive_runtime_blocker_approval(
                context=context_override,
                metadata=metadata,
            )
            if approved_blocker is None:
                raise RuntimeError(f"cognitive_runtime_blocked:{reason}")
            block_reason = reason

        guidance = _copy_cognitive_guidance(cognitive_context)
        if approved_blocker is not None:
            guidance["approved_blocker"] = True
            guidance["block_reason"] = block_reason
        context_override["cognitive_guidance"] = guidance
        blocked_tools = tuple(guidance.get("blocked_tools") or ())
        if blocked_tools:
            metadata["cognitive_tool_policy"] = {
                "source": "cognitive_runtime_mainline",
                "blocked_tools": blocked_tools,
            }
        strategy_override = _build_cognitive_strategy_override(guidance)
        if strategy_override:
            metadata["cognitive_strategy_override"] = strategy_override
        metadata["cognitive_runtime_preflight"] = {
            "mode": mode.value,
            "applied": True,
            "blocked": False,
            "intent_type": guidance["intent_type"],
            "execution_path": guidance["execution_path"],
            "verification_needed": guidance["verification_needed"],
            "blocked_tools": blocked_tools,
            "tool_policy_applied": bool(blocked_tools),
            "strategy_override_applied": bool(strategy_override),
        }
        if approved_blocker is not None:
            metadata["cognitive_runtime_preflight"].update(
                {
                    "approved_blocker": True,
                    "original_blocked": True,
                    "block_reason": block_reason,
                    "approval_mode": approved_blocker["mode"],
                    "approval_source": approved_blocker["source"],
                    "approval_scope": approved_blocker["scope"],
                    "approved_by": approved_blocker["approved_by"],
                }
            )
        request.context_override = context_override
        request.metadata = metadata
        return request

    async def _prepare_task_request(self, command: ExecuteRoleTaskCommandV1) -> RoleTurnRequest:
        request = self._build_task_request(command)
        request = _enforce_required_context_os(request)
        return await self._apply_cognitive_runtime_preflight(
            request=request,
            role=command.role,
            workspace=command.workspace,
            session_id=command.session_id,
        )

    async def _prepare_session_request(
        self,
        command: ExecuteRoleSessionCommandV1,
        *,
        include_session_snapshot: bool = False,
    ) -> RoleTurnRequest:
        request = self._build_session_request(
            command,
            include_session_snapshot=include_session_snapshot,
        )
        request = _enforce_required_context_os(request)
        return await self._apply_cognitive_runtime_preflight(
            request=request,
            role=command.role,
            workspace=command.workspace,
            session_id=command.session_id,
        )

    async def create_transaction_controller(
        self,
        command: ExecuteRoleSessionCommandV1,
    ) -> Any:
        """Create a TurnTransactionController for session orchestrator integration.

        Resolves strategy profile and delegates to the kernel's transaction
        kernel factory. This is the canonical entrypoint for RoleSessionOrchestrator
        to obtain a kernel-backed turn controller.
        """
        _execution_domain, _ = self._resolve_execution_domain(
            command_domain=command.domain,
            context=command.context,
            metadata=command.metadata,
            role=command.role,
        )
        kernel = self._get_kernel(command.workspace)
        request = await self._prepare_session_request(command, include_session_snapshot=True)
        from polaris.cells.roles.profile.public.service import registry as _registry

        if not _registry.list_roles():
            load_core_roles()
        profile = _registry.get_profile(command.role)
        if profile is None:
            raise ValueError(f"Role profile not found: {command.role}")
        return kernel._create_transaction_kernel(command.role, profile, request)

    async def execute_role_task(
        self,
        command: ExecuteRoleTaskCommandV1,
    ) -> RoleExecutionResultV1:
        kernel = self._get_kernel(command.workspace)
        request = await self._prepare_task_request(command)
        result = await kernel.run(command.role, request)
        contract_result = _to_contract_result(
            role=command.role,
            workspace=command.workspace,
            task_id=command.task_id,
            session_id=command.session_id,
            run_id=command.run_id,
            result=result,
        )
        evidence = self._emit_cognitive_runtime_shadow_artifacts(
            source="roles.runtime.execute_role_task",
            workspace=command.workspace,
            role=command.role,
            task_id=command.task_id,
            session_id=command.session_id,
            run_id=command.run_id,
            result=contract_result,
            metadata=request.metadata,
            context=request.context_override,
        )
        return _with_result_metadata_patch(
            contract_result,
            _cognitive_runtime_result_patch(
                evidence=evidence,
                request_metadata=request.metadata,
            ),
        )

    async def execute_role_session(
        self,
        command: ExecuteRoleSessionCommandV1,
    ) -> RoleExecutionResultV1:
        if command.stream:
            # Collect streaming events and assemble the final result.
            kernel = self._get_kernel(command.workspace)
            full_content: list[str] = []
            thinking: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            error_message: str | None = None
            final_result: RoleTurnResult | None = None
            # SSOT Fix: Track accumulated turn_events_metadata from each complete event
            # so we can persist events even when complete is not the final event.
            accumulated_turn_events_metadata: list[dict[str, Any]] = []
            request: RoleTurnRequest | None = None

            try:
                request = await self._prepare_session_request(
                    command,
                    include_session_snapshot=True,
                )
                async for event in kernel.run_stream(
                    command.role,
                    request,
                ):
                    event_type = str(event.get("type") or "")
                    if event_type == "content_chunk":
                        full_content.append(str(event.get("content", "")))
                    elif event_type == "thinking_chunk":
                        thinking.append(str(event.get("content", "")))
                    elif event_type == "tool_call":
                        tool_calls.append(
                            {
                                "name": str(event.get("tool", "")),
                                "args": event.get("args") or {},
                            }
                        )
                    elif event_type == "complete":
                        maybe_result = event.get("result")
                        if isinstance(maybe_result, RoleTurnResult):
                            final_result = maybe_result
                            # SSOT Fix: Accumulate turn_events_metadata from each complete event.
                            # This ensures events are persisted even if the stream doesn't end
                            # with the typical early_return/complete pattern.
                            if maybe_result.turn_events_metadata:
                                accumulated_turn_events_metadata.extend(list(maybe_result.turn_events_metadata))
                    elif event_type == "error":
                        error_message = str(event.get("error", "stream error"))
            except (RuntimeError, ValueError) as e:
                error_message = str(e)

            # SSOT Fix: Use accumulated turn_events_metadata if final_result doesn't have them.
            # This handles the case where complete was never received or had empty metadata.
            turn_events_to_persist = (
                list(final_result.turn_events_metadata)
                if final_result and final_result.turn_events_metadata
                else accumulated_turn_events_metadata
                if accumulated_turn_events_metadata
                else None
            )
            turn_history_to_persist = (
                list(final_result.turn_history) if final_result and final_result.turn_history else []
            )

            if final_result is not None:
                assert request is not None
                await self._persist_session_turn_state(
                    command,
                    turn_history=turn_history_to_persist,
                    turn_events_metadata=turn_events_to_persist,
                )
                contract_result = _to_contract_result(
                    role=command.role,
                    workspace=command.workspace,
                    task_id=command.task_id,
                    session_id=command.session_id,
                    run_id=command.run_id,
                    result=final_result,
                )
                evidence = self._emit_cognitive_runtime_shadow_artifacts(
                    source="roles.runtime.execute_role_session.stream",
                    workspace=command.workspace,
                    role=command.role,
                    task_id=command.task_id,
                    session_id=command.session_id,
                    run_id=command.run_id,
                    result=contract_result,
                    metadata=request.metadata,
                    context=request.context_override,
                )
                return _with_result_metadata_patch(
                    contract_result,
                    _cognitive_runtime_result_patch(
                        evidence=evidence,
                        request_metadata=request.metadata,
                    ),
                )

            full_text = "".join(full_content)
            thinking_text = "".join(thinking)
            error_msg = error_message or ""
            ok = not bool(error_msg)

            # NOTE: In the error case, final_result may never have been set.
            # Persist only the turn state collected before the failure.
            await self._persist_session_turn_state(
                command,
                turn_history=turn_history_to_persist,
                turn_events_metadata=turn_events_to_persist,
            )

            contract_result = RoleExecutionResultV1(
                ok=ok,
                status="ok" if ok else "failed",
                role=command.role,
                workspace=command.workspace,
                task_id=command.task_id,
                session_id=command.session_id,
                run_id=command.run_id,
                output=full_text,
                thinking=thinking_text if thinking_text else None,
                tool_calls=tuple(str(t.get("name") or "") for t in tool_calls if isinstance(t, dict)),
                artifacts=(),
                usage={"stream": True, "tool_calls_count": len(tool_calls)},
                metadata={},
                error_code=None if ok else "role_runtime_error",
                error_message=error_msg or None,
            )
            evidence = self._emit_cognitive_runtime_shadow_artifacts(
                source="roles.runtime.execute_role_session.stream_fallback",
                workspace=command.workspace,
                role=command.role,
                task_id=command.task_id,
                session_id=command.session_id,
                run_id=command.run_id,
                result=contract_result,
                metadata=request.metadata if request is not None else command.metadata,
                context=request.context_override if request is not None else command.context,
            )
            return _with_result_metadata_patch(
                contract_result,
                _cognitive_runtime_result_patch(
                    evidence=evidence,
                    request_metadata=request.metadata if request is not None else command.metadata,
                ),
            )

        kernel = self._get_kernel(command.workspace)
        request = await self._prepare_session_request(command, include_session_snapshot=True)
        result = await kernel.run(command.role, request)
        await self._persist_session_turn_state(
            command,
            turn_history=list(result.turn_history) if result.turn_history else [],
            turn_events_metadata=list(result.turn_events_metadata) if result.turn_events_metadata else None,
        )
        contract_result = _to_contract_result(
            role=command.role,
            workspace=command.workspace,
            task_id=command.task_id,
            session_id=command.session_id,
            run_id=command.run_id,
            result=result,
        )
        evidence = self._emit_cognitive_runtime_shadow_artifacts(
            source="roles.runtime.execute_role_session",
            workspace=command.workspace,
            role=command.role,
            task_id=command.task_id,
            session_id=command.session_id,
            run_id=command.run_id,
            result=contract_result,
            metadata=request.metadata,
            context=request.context_override,
        )
        return _with_result_metadata_patch(
            contract_result,
            _cognitive_runtime_result_patch(
                evidence=evidence,
                request_metadata=request.metadata,
            ),
        )

    async def get_runtime_status(
        self,
        query: GetRoleRuntimeStatusQueryV1,
    ) -> Mapping[str, Any]:
        if not registry.list_roles():
            load_core_roles()
        roles = sorted(registry.list_roles())
        role_token = str(query.role or "").strip()
        role_exists = role_token in roles if role_token else True
        status_payload: dict[str, Any] = {
            "workspace": query.workspace,
            "role": role_token or None,
            "ready": bool(roles) and role_exists,
            "role_exists": role_exists,
            "role_count": len(roles),
            "roles": roles,
        }
        if query.include_agent_health:
            status_payload["agent_health"] = {"status": "ready" if role_exists else "degraded"}
        if query.include_queue:
            status_payload["queue"] = {"pending": 0, "running": 0}
        if query.include_tools:
            status_payload["tools"] = {"available": True}
        return status_payload

    async def build_aggregate_role_plan(
        self,
        query: BuildAggregateRolePlanQueryV1,
    ) -> AggregateRolePlanResultV1:
        """Build a query-only plan for treating role composition as one model.

        The result is intentionally structural: it names current role profiles,
        virtual lobes, KernelOne/ContextOS/Akashic capabilities, and phase order.
        It does not execute role turns or mutate runtime state.
        """
        if not registry.list_roles():
            load_core_roles()
        available_role_ids = {str(role_id).strip() for role_id in registry.list_roles() if str(role_id).strip()}
        current_role_ids = _select_aggregate_role_ids(query.role_ids, available_role_ids)
        selected_role_ids = set(current_role_ids)
        lobes = tuple(
            _build_aggregate_lobe(
                spec,
                selected_role_ids=selected_role_ids,
                available_role_ids=available_role_ids,
                include_virtual_lobes=query.include_virtual_lobes,
            )
            for spec in _AGGREGATE_LOBE_SPECS
        )
        required_capability_refs = _dedupe_tokens(
            capability_ref for lobe in lobes for capability_ref in lobe.capability_refs
        )
        runtime_integrations = _build_runtime_integrations(query.workspace)
        cognitive_ledger = _build_cognitive_ledger(lobes)
        compute_policy = _build_compute_policy(lobes)
        failure_signals = _extract_failure_signals(query)
        takeover_directive = _build_takeover_directive(
            lobes=lobes,
            cognitive_ledger=cognitive_ledger,
            failure_signals=failure_signals,
        )
        warnings: list[str] = []
        failure_evidence = _extract_failure_evidence(query)
        takeover_evidence_status = _build_takeover_evidence_status(
            takeover_directive=takeover_directive,
            failure_evidence=failure_evidence,
        )
        if takeover_directive is not None and takeover_evidence_status.get("missing_keys"):
            warnings.append(
                "missing_takeover_evidence:"
                + ",".join(str(key) for key in takeover_evidence_status.get("missing_keys") or ())
            )
        unknown_requested_roles = tuple(role_id for role_id in query.role_ids if role_id not in available_role_ids)
        if unknown_requested_roles:
            warnings.append(f"unknown_role_ids:{','.join(unknown_requested_roles)}")
        if query.include_virtual_lobes and any(lobe.virtual_role_ids for lobe in lobes):
            warnings.append("virtual_role_ids_are_not_current_role_profiles")
        partial_lobes = tuple(lobe.lobe_id for lobe in lobes if lobe.status != "active")
        if partial_lobes:
            warnings.append(f"partial_lobes:{','.join(partial_lobes)}")
        metadata: dict[str, Any] = {
            "planner": "roles.runtime.aggregate_role_plan.v1",
            "domain": query.domain,
            "failure_signals": failure_signals,
            "failure_evidence": failure_evidence,
            "takeover_evidence_status": takeover_evidence_status,
            "external_interface": "chat_completions_compatible_wrapper",
            "stateful": False,
            "current_fact_scope": "roles.profile entries plus KernelOne capability references",
            "truthful_migration": (
                "This result is a composition plan. It does not claim virtual lobes are "
                "standalone role profiles or that aggregate execution has already run."
            ),
        }
        if query.context:
            metadata["context_keys"] = tuple(sorted(str(key) for key in query.context))
        if query.metadata:
            metadata["metadata_keys"] = tuple(sorted(str(key) for key in query.metadata))
        return AggregateRolePlanResultV1(
            ok=bool(lobes),
            workspace=query.workspace,
            objective=query.objective,
            aggregate_model_id=_AGGREGATE_MODEL_ID,
            lobes=lobes,
            execution_order=tuple(lobe.lobe_id for lobe in lobes),
            current_role_ids=current_role_ids,
            required_capability_refs=required_capability_refs,
            runtime_integrations=runtime_integrations,
            cognitive_ledger=cognitive_ledger,
            compute_policy=compute_policy,
            takeover_directive=takeover_directive,
            warnings=tuple(warnings),
            metadata=metadata,
        )

    async def audit_aggregate_runtime_integrations(
        self,
        query: AuditAggregateRuntimeIntegrationsQueryV1,
    ) -> AggregateRuntimeAuditResultV1:
        """Return a machine-readable audit of aggregate runtime integrations."""
        integrations = _build_runtime_integrations(query.workspace)
        return _build_runtime_audit_result(
            workspace=query.workspace,
            integrations=integrations,
            metadata={
                "role_ids": query.role_ids,
                "include_virtual_lobes": query.include_virtual_lobes,
                "context_keys": tuple(sorted(str(key) for key in query.context)),
                "metadata_keys": tuple(sorted(str(key) for key in query.metadata)),
            },
        )

    async def _execute_aggregate_lobe_turn(
        self,
        *,
        command: AggregateChatCompletionsCommandV1,
        plan: AggregateRolePlanResultV1,
        completion_id: str,
        objective: str,
        selected_lobe: AggregateRoleLobeV1,
        chain_turn_index: int = 0,
        prior_handoffs: tuple[Mapping[str, Any], ...] = (),
    ) -> tuple[RoleExecutionResultV1, str]:
        selected_role = _select_aggregate_execution_role(
            plan=plan,
            command=command,
            selected_lobe=selected_lobe,
        )
        session_id = command.session_id or f"{completion_id}-session"
        run_id = command.run_id or completion_id
        task_suffix = "single_turn" if command.execution_mode == "single_turn" else f"lobe_chain:{chain_turn_index}"
        memory_recall_pack = _build_aggregate_memory_recall_pack(
            command=command,
            plan=plan,
            selected_lobe=selected_lobe,
            prior_handoffs=prior_handoffs,
        )
        contextos_attention_budget_pack = _build_aggregate_contextos_attention_budget_pack(
            command=command,
            plan=plan,
            selected_lobe=selected_lobe,
            prior_handoffs=prior_handoffs,
        )
        task_market_projection_pack = _build_aggregate_task_market_projection_pack(command=command)
        context_governance_pack = _build_aggregate_context_governance_pack(
            command=command,
            selected_lobe=selected_lobe,
            selected_role=selected_role,
            chain_turn_index=chain_turn_index,
        )
        distilled_knowledge_pack = _build_aggregate_distilled_knowledge_pack(
            command=command,
            plan=plan,
            selected_lobe=selected_lobe,
            selected_role=selected_role,
        )
        lobe_turn_envelope = _build_aggregate_lobe_turn_envelope(
            command=command,
            plan=plan,
            selected_lobe=selected_lobe,
            selected_role=selected_role,
            chain_turn_index=chain_turn_index,
            prior_handoffs=prior_handoffs,
            memory_recall_pack=memory_recall_pack,
            contextos_attention_budget_pack=contextos_attention_budget_pack,
            task_market_projection_pack=task_market_projection_pack,
            context_governance_pack=context_governance_pack,
            distilled_knowledge_pack=distilled_knowledge_pack,
        )
        role_command = ExecuteRoleSessionCommandV1(
            role=selected_role,
            session_id=session_id,
            workspace=command.workspace,
            user_message=lobe_turn_envelope,
            run_id=run_id,
            task_id=f"{completion_id}:{task_suffix}",
            domain=command.domain,
            history=_aggregate_history_from_messages(command.messages),
            context=_aggregate_execution_context(
                command=command,
                plan=plan,
                selected_lobe=selected_lobe,
                selected_role=selected_role,
                chain_turn_index=chain_turn_index,
                prior_handoffs=prior_handoffs,
                memory_recall_pack=memory_recall_pack,
                contextos_attention_budget_pack=contextos_attention_budget_pack,
                task_market_projection_pack=task_market_projection_pack,
                context_governance_pack=context_governance_pack,
                distilled_knowledge_pack=distilled_knowledge_pack,
            ),
            metadata=_aggregate_execution_metadata(
                command=command,
                plan=plan,
                selected_lobe=selected_lobe,
                selected_role=selected_role,
                chain_turn_index=chain_turn_index,
                prior_handoffs=prior_handoffs,
                memory_recall_pack=memory_recall_pack,
                contextos_attention_budget_pack=contextos_attention_budget_pack,
                task_market_projection_pack=task_market_projection_pack,
                context_governance_pack=context_governance_pack,
                distilled_knowledge_pack=distilled_knowledge_pack,
            ),
            stream=True,
        )
        content_chunks: list[str] = []
        thinking_chunks: list[str] = []
        tool_calls: list[str] = []
        error_messages: list[str] = []
        fingerprint: dict[str, Any] | None = None
        final_result: RoleExecutionResultV1 | None = None
        async for event in self.stream_chat_turn(role_command):
            event_type = str(event.get("type") or "")
            if event_type == "fingerprint":
                fingerprint = {
                    "profile_id": event.get("profile_id"),
                    "profile_hash": event.get("profile_hash"),
                    "bundle_id": event.get("bundle_id"),
                    "bundle_version": event.get("bundle_version"),
                    "run_id": event.get("run_id"),
                    "turn_index": event.get("turn_index"),
                    "cognitive_strategy_override_applied": bool(event.get("cognitive_strategy_override_applied")),
                }
            elif event_type == "content_chunk":
                content_chunks.append(str(event.get("content") or ""))
            elif event_type == "thinking_chunk":
                thinking_chunks.append(str(event.get("content") or ""))
            elif event_type == "tool_call":
                tool_name = str(event.get("tool") or "").strip()
                if tool_name:
                    tool_calls.append(tool_name)
            elif event_type == "error":
                error_messages.append(str(event.get("error") or "role runtime stream error"))
            elif event_type == "complete":
                maybe_result = event.get("result")
                if isinstance(maybe_result, RoleTurnResult):
                    final_result = _to_contract_result(
                        role=selected_role,
                        workspace=command.workspace,
                        task_id=f"{completion_id}:{task_suffix}",
                        session_id=session_id,
                        run_id=run_id,
                        result=maybe_result,
                    )
                    event_metadata = event.get("metadata")
                    if isinstance(event_metadata, Mapping):
                        final_result = _with_result_metadata_patch(final_result, dict(event_metadata))

        aggregate_patch = {
            "aggregate_runtime": {
                "aggregate_model_id": plan.aggregate_model_id,
                "execution_mode": command.execution_mode,
                "selected_lobe_id": selected_lobe.lobe_id,
                "selected_role_id": selected_role,
                "chain_turn_index": chain_turn_index,
                "runtime_integrations_wired": [
                    item.tech_id for item in plan.runtime_integrations if item.status == "wired"
                ],
                "context_governance_status": context_governance_pack.get("status") or "skipped",
                "distilled_knowledge_status": distilled_knowledge_pack.get("status") or "skipped",
            },
            "context_governance": {
                "status": context_governance_pack.get("status") or "skipped",
                "retrieval_candidate_count": (context_governance_pack.get("graph_constrained_retrieval") or {}).get(
                    "candidate_count", 0
                ),
            },
            "distilled_knowledge": {
                "status": distilled_knowledge_pack.get("status") or "skipped",
                "total_available": distilled_knowledge_pack.get("total_available", 0),
                "knowledge_unit_count": len(distilled_knowledge_pack.get("knowledge_units") or ()),
            },
        }
        if fingerprint is not None:
            aggregate_patch["strategy_fingerprint"] = fingerprint
        if final_result is not None:
            distillation_pack = _distill_aggregate_lobe_result(
                command=command,
                plan=plan,
                selected_lobe=selected_lobe,
                selected_role=selected_role,
                chain_turn_index=chain_turn_index,
                result=final_result,
            )
            aggregate_patch["knowledge_distillation"] = distillation_pack
            aggregate_patch["aggregate_runtime"]["knowledge_distillation_status"] = (
                distillation_pack.get("status") or "skipped"
            )
            return _with_result_metadata_patch(final_result, aggregate_patch), selected_role

        error_text = "; ".join(message for message in error_messages if message)
        ok = not error_text
        fallback_result = RoleExecutionResultV1(
            ok=ok,
            status="ok" if ok else "failed",
            role=selected_role,
            workspace=command.workspace,
            task_id=f"{completion_id}:{task_suffix}",
            session_id=session_id,
            run_id=run_id,
            output="".join(content_chunks),
            thinking="".join(thinking_chunks) or None,
            tool_calls=tuple(tool_calls),
            usage={"stream_collected": True, "tool_calls_count": len(tool_calls)},
            metadata={},
            error_code=None if ok else "aggregate_single_turn_failed",
            error_message=error_text or None,
        )
        distillation_pack = _distill_aggregate_lobe_result(
            command=command,
            plan=plan,
            selected_lobe=selected_lobe,
            selected_role=selected_role,
            chain_turn_index=chain_turn_index,
            result=fallback_result,
        )
        aggregate_patch["knowledge_distillation"] = distillation_pack
        aggregate_patch["aggregate_runtime"]["knowledge_distillation_status"] = (
            distillation_pack.get("status") or "skipped"
        )
        return _with_result_metadata_patch(fallback_result, aggregate_patch), selected_role

    async def _execute_aggregate_single_turn(
        self,
        *,
        command: AggregateChatCompletionsCommandV1,
        plan: AggregateRolePlanResultV1,
        completion_id: str,
        objective: str,
    ) -> tuple[RoleExecutionResultV1, AggregateRoleLobeV1, str]:
        selected_lobe = _select_aggregate_execution_lobe(plan)
        execution_result, selected_role = await self._execute_aggregate_lobe_turn(
            command=command,
            plan=plan,
            completion_id=completion_id,
            objective=objective,
            selected_lobe=selected_lobe,
        )
        return execution_result, selected_lobe, selected_role

    async def _execute_aggregate_lobe_chain(
        self,
        *,
        command: AggregateChatCompletionsCommandV1,
        plan: AggregateRolePlanResultV1,
        completion_id: str,
        objective: str,
    ) -> tuple[
        tuple[RoleExecutionResultV1, ...],
        tuple[AggregateRoleLobeV1, ...],
        tuple[str, ...],
        tuple[Mapping[str, Any], ...],
    ]:
        chain_lobes = _select_aggregate_lobe_chain(plan=plan, command=command)
        if not chain_lobes:
            raise ValueError("aggregate lobe_chain requires at least one executable lobe")
        results: list[RoleExecutionResultV1] = []
        roles: list[str] = []
        handoffs: list[Mapping[str, Any]] = []
        for index, lobe in enumerate(chain_lobes):
            result, role = await self._execute_aggregate_lobe_turn(
                command=command,
                plan=plan,
                completion_id=completion_id,
                objective=objective,
                selected_lobe=lobe,
                chain_turn_index=index,
                prior_handoffs=tuple(handoffs),
            )
            results.append(result)
            roles.append(role)
            handoffs.append(
                _aggregate_handoff_from_result(
                    sequence=index,
                    selected_lobe=lobe,
                    selected_role=role,
                    result=result,
                )
            )
            if not result.ok:
                break
        return tuple(results), chain_lobes[: len(results)], tuple(roles), tuple(handoffs)

    async def chat_completions(
        self,
        command: AggregateChatCompletionsCommandV1,
    ) -> AggregateChatCompletionsResultV1:
        """Expose Polaris role composition behind a model-shaped interface.

        `plan_only` returns a deterministic lobe plan. `single_turn` selects one
        concrete role lobe. `lobe_chain` executes a bounded sequence of concrete
        roles through the normal streamed role runtime so strategy fingerprints,
        ContextOS preflight, Turn Ledger, and Cognitive Runtime receipts remain
        active.
        """
        objective = _aggregate_objective_from_messages(command.messages)
        plan = await self.build_aggregate_role_plan(
            BuildAggregateRolePlanQueryV1(
                workspace=command.workspace,
                objective=objective,
                role_ids=command.role_ids,
                failure_signals=command.failure_signals,
                failure_evidence=command.failure_evidence,
                domain=command.domain,
                include_virtual_lobes=command.include_virtual_lobes,
                context=command.context,
                metadata=command.metadata,
            )
        )
        completion_id = _stable_completion_id(command, objective)
        execution_result: RoleExecutionResultV1 | None = None
        execution_results: tuple[RoleExecutionResultV1, ...] = ()
        selected_lobe: AggregateRoleLobeV1 | None = None
        selected_role: str | None = None
        selected_lobes: tuple[AggregateRoleLobeV1, ...] = ()
        selected_roles: tuple[str, ...] = ()
        handoffs: tuple[Mapping[str, Any], ...] = ()
        if command.execution_mode == "single_turn":
            execution_result, selected_lobe, selected_role = await self._execute_aggregate_single_turn(
                command=command,
                plan=plan,
                completion_id=completion_id,
                objective=objective,
            )
            content = _render_aggregate_execution_content(
                plan=plan,
                selected_lobe=selected_lobe,
                selected_role=selected_role,
                execution_result=execution_result,
            )
            execution_results = (execution_result,)
        elif command.execution_mode == "lobe_chain":
            execution_results, selected_lobes, selected_roles, handoffs = await self._execute_aggregate_lobe_chain(
                command=command,
                plan=plan,
                completion_id=completion_id,
                objective=objective,
            )
            execution_result = execution_results[-1] if execution_results else None
            selected_lobe = selected_lobes[-1] if selected_lobes else None
            selected_role = selected_roles[-1] if selected_roles else None
            content = _render_aggregate_chain_content(
                plan=plan,
                chain_lobes=selected_lobes,
                chain_roles=selected_roles,
                execution_results=execution_results,
                handoffs=handoffs,
            )
        else:
            content = _render_aggregate_plan_content(plan)
        metadata: dict[str, Any] = {
            "planner": "roles.runtime.aggregate_chat_completions.v1",
            "execution_mode": command.execution_mode,
            "session_id": command.session_id,
            "run_id": command.run_id,
            "aggregate_plan_model_id": plan.aggregate_model_id,
            "stateful": command.execution_mode != "plan_only",
            "runtime_integrations_wired": [
                item.tech_id for item in plan.runtime_integrations if item.status == "wired"
            ],
        }
        if selected_lobe is not None and selected_role is not None:
            metadata["selected_lobe_id"] = selected_lobe.lobe_id
            metadata["selected_role_id"] = selected_role
        if selected_lobes:
            metadata["selected_lobe_ids"] = tuple(lobe.lobe_id for lobe in selected_lobes)
        if selected_roles:
            metadata["selected_role_ids"] = selected_roles
        if handoffs:
            metadata["handoff_count"] = len(handoffs)
        if command.execution_mode == "plan_only":
            metadata["truthful_migration"] = (
                "plan_only chat_completions builds the aggregate lobe plan but does "
                "not claim multi-role execution has run."
            )
        elif command.execution_mode == "lobe_chain":
            metadata["truthful_migration"] = (
                "lobe_chain executes a bounded sequence of concrete current roles selected "
                "from the aggregate plan; virtual lobes remain planning constructs."
            )
        else:
            metadata["truthful_migration"] = (
                "single_turn executes one concrete current role selected from the aggregate "
                "lobe plan; virtual lobes remain planning constructs."
            )
        return AggregateChatCompletionsResultV1(
            id=completion_id,
            object="chat.completion",
            model=command.model,
            choices=(
                AggregateChatChoiceV1(
                    index=0,
                    message=AggregateChatMessageV1(
                        role="assistant",
                        content=content,
                    ),
                    finish_reason="stop",
                ),
            ),
            usage={
                "input_messages": len(command.messages),
                "output_lobes": len(plan.lobes),
                "role_count": len(plan.current_role_ids),
                "execution_mode": command.execution_mode,
                "runtime_integrations": len(plan.runtime_integrations),
                "executed": execution_result is not None,
                "tool_calls_count": len(execution_result.tool_calls) if execution_result is not None else 0,
                "executed_turns": len(execution_results),
                "handoff_count": len(handoffs),
            },
            aggregate_plan=plan,
            execution_result=execution_result,
            execution_results=execution_results,
            metadata=metadata,
        )

    async def stream_chat_turn(
        self,
        command: ExecuteRoleSessionCommandV1,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream role chat turns as an async iterator of events.

        Yields dict events with keys: type, content, thinking, tool, args,
        result, error, fingerprint.

        Before the first turn event, yields a fingerprint event containing
        strategy identity (profile_id, profile_hash, bundle_id, run_id).
        After the stream completes, persists a StrategyReceipt to
        `<metadata_dir>/runtime/strategy_runs/`.
        """
        try:
            request = await self._prepare_session_request(
                command,
                include_session_snapshot=True,
            )
        except (RuntimeError, ValueError) as exc:
            yield {
                "type": "error",
                "error": str(exc),
                "source": "roles.runtime.cognitive_preflight",
            }
            return

        execution_domain, domain_explicit = self._resolve_execution_domain(
            command_domain=request.domain or command.domain,
            context=request.context_override,
            metadata=request.metadata,
            role=command.role,
        )
        cognitive_strategy_override = _copy_strategy_override(
            request.metadata.get("cognitive_strategy_override") if isinstance(request.metadata, Mapping) else None
        )
        # WS2: Create strategy run context before the turn.
        run_ctx = self.create_strategy_run(
            domain=execution_domain,
            role=command.role,
            session_id=command.session_id,
            budget=None,  # Budget snapshot taken during context assembly
            workspace=command.workspace,
            domain_explicit=domain_explicit,
            include_session_override=True,
            current_turn_override=cognitive_strategy_override,
        )
        emit_debug_event(
            category="strategy",
            label="resolved",
            source="roles.runtime",
            payload={
                "workspace": command.workspace,
                "role": command.role,
                "domain": execution_domain,
                "session_id": command.session_id,
                "run_id": run_ctx.run_id,
                "turn_index": run_ctx.turn_index,
                "bundle_id": run_ctx.bundle_id,
                "bundle_version": run_ctx.bundle_version,
                "profile_id": run_ctx.profile_id,
                "profile_hash": run_ctx.profile_hash,
                "resolved_overrides": dict(run_ctx.resolved_overrides),
                "cognitive_strategy_override_applied": bool(cognitive_strategy_override),
            },
        )
        # Emit strategy fingerprint as the first event.
        fingerprint_event = {
            "type": "fingerprint",
            "profile_id": run_ctx.profile_id,
            "profile_hash": run_ctx.profile_hash,
            "bundle_id": run_ctx.bundle_id,
            "bundle_version": run_ctx.bundle_version,
            "run_id": run_ctx.run_id,
            "turn_index": run_ctx.turn_index,
            "cognitive_strategy_override_applied": bool(cognitive_strategy_override),
        }
        yield fingerprint_event

        kernel = self._get_kernel(command.workspace)
        streamed_content: list[str] = []
        streamed_thinking: list[str] = []
        streamed_tool_calls: list[str] = []
        final_stream_result: RoleTurnResult | None = None
        try:
            async for event in kernel.run_stream(
                command.role,
                request,
            ):
                # WS2: Accumulate tool calls into the strategy run context.
                event_type = str(event.get("type") or "")
                if event_type == "tool_call":
                    tool_name = str(event.get("tool") or "")
                    if tool_name:
                        run_ctx = run_ctx.with_tool_call(tool_name)
                        streamed_tool_calls.append(tool_name)
                elif event_type == "content_chunk":
                    streamed_content.append(str(event.get("content") or ""))
                elif event_type == "thinking_chunk":
                    streamed_thinking.append(str(event.get("content") or ""))
                elif event_type == "complete":
                    maybe_result = event.get("result")
                    if isinstance(maybe_result, RoleTurnResult):
                        contract_result = _to_contract_result(
                            role=command.role,
                            workspace=command.workspace,
                            task_id=command.task_id,
                            session_id=command.session_id,
                            run_id=command.run_id,
                            result=maybe_result,
                        )
                        evidence = self._emit_cognitive_runtime_shadow_artifacts(
                            source="roles.runtime.stream_chat_turn",
                            workspace=command.workspace,
                            role=command.role,
                            task_id=command.task_id,
                            session_id=command.session_id,
                            run_id=command.run_id,
                            result=contract_result,
                            metadata=request.metadata,
                            context=request.context_override,
                        )
                        evidence_patch = _cognitive_runtime_result_patch(
                            evidence=evidence,
                            request_metadata=request.metadata,
                        )
                        maybe_result.metadata.update(evidence_patch)
                        maybe_result.execution_stats["cognitive_runtime_evidence_emitted"] = True
                        event["cognitive_runtime_evidence"] = dict(evidence_patch["cognitive_runtime_evidence"])
                        raw_event_metadata = event.get("metadata")
                        event_metadata: dict[str, Any] = (
                            dict(raw_event_metadata) if isinstance(raw_event_metadata, dict) else {}
                        )
                        result_metadata = _copy_result_metadata(maybe_result.metadata)
                        event["metadata"] = {
                            **result_metadata,
                            **event_metadata,
                            **evidence_patch,
                        }
                        final_stream_result = maybe_result

                yield dict(event)
        finally:
            usage_payload: dict[str, Any] = {"stream": True}
            if final_stream_result is not None:
                usage_payload.update(dict(final_stream_result.execution_stats or {}))
            await self._persist_session_turn_state(
                command,
                turn_history=list(final_stream_result.turn_history)
                if final_stream_result and final_stream_result.turn_history
                else [],
                turn_events_metadata=list(final_stream_result.turn_events_metadata)
                if final_stream_result and final_stream_result.turn_events_metadata
                else None,
            )
            # WS2: Mark run ended and emit receipt.
            run_ctx = run_ctx.mark_ended()
            try:
                receipt_path = self.emit_strategy_receipt(run_ctx, command.workspace)
                emit_debug_event(
                    category="strategy",
                    label="receipt_emitted",
                    source="roles.runtime",
                    payload={
                        "run_id": run_ctx.run_id,
                        "turn_index": run_ctx.turn_index,
                        "profile_id": run_ctx.profile_id,
                        "profile_hash": run_ctx.profile_hash,
                        "receipt_path": str(receipt_path),
                        "tool_sequence": list(run_ctx.tool_sequence),
                        "ended_at": run_ctx.ended_at,
                    },
                )
            except (RuntimeError, ValueError):
                logger.warning("Failed to emit strategy receipt for run %s", run_ctx.run_id)

    # ── CLI helper methods (Wave 3: delegated to CliRunner) ────────────────────
    # These methods forward to CliRunner for backward compatibility.
    # New code should use CliRunner directly.

    def _get_cli_runner(self) -> CliRunner:
        """Get CliRunner instance for CLI method forwarding."""
        return CliRunner(self)

    async def run_interactive(
        self,
        role: str,
        workspace: str,
        welcome_message: str = "",
    ) -> None:
        """Interactive REPL loop for a role. Delegates to CliRunner."""
        await self._get_cli_runner().run_interactive(
            role=role,
            workspace=workspace,
            welcome_message=welcome_message,
        )

    async def run_oneshot(
        self,
        role: str,
        workspace: str,
        goal: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a single role goal. Delegates to CliRunner."""
        return await self._get_cli_runner().run_oneshot(
            role=role,
            workspace=workspace,
            goal=goal,
            context=context,
        )

    async def run_autonomous(
        self,
        role: str,
        workspace: str,
        goal: str,
        max_iterations: int = 10,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Plan-and-execute loop for a role. Delegates to CliRunner."""
        return await self._get_cli_runner().run_autonomous(
            role=role,
            workspace=workspace,
            goal=goal,
            max_iterations=max_iterations,
            context=context,
        )

    async def run_server(
        self,
        role: str,
        workspace: str,
        host: str = "127.0.0.1",
        port: int = 50000,
    ) -> None:
        """Run a FastAPI server for programmatic role access. Delegates to CliRunner."""
        await self._get_cli_runner().run_server(
            role=role,
            workspace=workspace,
            host=host,
            port=port,
        )

    async def execute_role(
        self,
        role_id: str,
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Execute a role task or session. Delegates to CliRunner."""
        return await self._get_cli_runner().execute_role(
            role_id=role_id,
            context=context,
        )


_DEFAULT_ROLE_RUNTIME_SERVICE = RoleRuntimeService()


def reset_role_runtime_service() -> None:
    """Reset the singleton RoleRuntimeService for test isolation.

    This function clears the internal kernels and turn indices caches
    to prevent state leakage between tests.
    """
    _DEFAULT_ROLE_RUNTIME_SERVICE._kernels.clear()
    _DEFAULT_ROLE_RUNTIME_SERVICE._turn_indices.clear()


async def execute_role_task_command(command: ExecuteRoleTaskCommandV1) -> RoleExecutionResultV1:
    """Execute role task command via singleton runtime service."""
    return await _DEFAULT_ROLE_RUNTIME_SERVICE.execute_role_task(command)


async def execute_role_session_command(command: ExecuteRoleSessionCommandV1) -> RoleExecutionResultV1:
    """Execute role session command via singleton runtime service."""
    return await _DEFAULT_ROLE_RUNTIME_SERVICE.execute_role_session(command)


async def stream_role_session_command(
    command: ExecuteRoleSessionCommandV1,
) -> AsyncIterator[dict[str, Any]]:
    """Stream role session events via singleton runtime service."""
    async for event in _DEFAULT_ROLE_RUNTIME_SERVICE.stream_chat_turn(command):
        yield event


async def query_role_runtime_status(query: GetRoleRuntimeStatusQueryV1) -> Mapping[str, Any]:
    """Query role runtime status via singleton runtime service."""
    return await _DEFAULT_ROLE_RUNTIME_SERVICE.get_runtime_status(query)


async def query_aggregate_role_plan(query: BuildAggregateRolePlanQueryV1) -> AggregateRolePlanResultV1:
    """Query aggregate role/lobe composition via singleton runtime service."""
    return await _DEFAULT_ROLE_RUNTIME_SERVICE.build_aggregate_role_plan(query)


async def audit_aggregate_runtime_integrations(
    query: AuditAggregateRuntimeIntegrationsQueryV1,
) -> AggregateRuntimeAuditResultV1:
    """Audit aggregate runtime integrations via singleton runtime service."""
    return await _DEFAULT_ROLE_RUNTIME_SERVICE.audit_aggregate_runtime_integrations(query)


async def aggregate_chat_completions(
    command: AggregateChatCompletionsCommandV1,
) -> AggregateChatCompletionsResultV1:
    """Return aggregate chat completions via singleton runtime service."""
    return await _DEFAULT_ROLE_RUNTIME_SERVICE.chat_completions(command)


def create_role_cli_parser(role: str) -> argparse.ArgumentParser:
    """Create a standard CLI argument parser for role agents.

    This is the canonical parser for all role CLI entry points. It is
    independent of StandaloneRoleAgent and can be used without triggering
    deprecation warnings.

    Args:
        role: Role name used in the help text (e.g. 'architect', 'director').

    Returns:
        Configured ArgumentParser with --workspace, --mode, --goal, --host,
        --port, --max-iterations, --model arguments.
    """
    parser = argparse.ArgumentParser(
        description=f"{role.title()} Role Agent",
    )
    parser.add_argument(
        "--workspace",
        type=str,
        default=".",
        help="Workspace directory",
    )
    parser.add_argument(
        "--mode",
        choices=["interactive", "oneshot", "autonomous", "server", "tui"],
        default="interactive",
        help="Operation mode",
    )
    parser.add_argument(
        "--goal",
        type=str,
        help="Goal for oneshot/autonomous mode",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Server host (for server mode)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=50000,
        help="Server port (for server mode)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=10,
        help="Max iterations (for autonomous mode)",
    )
    parser.add_argument(
        "--model",
        type=str,
        help="LLM model to use",
    )
    return parser


def run_tui(role: str, workspace: str, session_id: str | None = None, debug: bool = False) -> int:
    """Run the Textual TUI console for a role.

    This is a convenience wrapper that forwards to the textual_console module.

    Args:
        role: Role name (e.g. 'architect', 'director', 'pm').
        workspace: Workspace directory path.
        session_id: Optional session ID for session continuity.
        debug: Enable debug mode.

    Returns:
        Exit code from the TUI application.
    """
    try:
        from polaris.delivery.cli.textual_console import run_claude_tui

        return run_claude_tui(
            workspace=workspace,
            role=role,
            session_id=session_id,
            debug=debug,
        )
    except ImportError as e:
        print("Error: TUI mode requires textual and rich packages")
        print("Install: pip install textual rich")
        print(f"Details: {e}")
        return 1


def __getattr__(name: str) -> object:
    if name in _SESSION_PUBLIC_EXPORTS:
        return _load_session_public_symbol(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AgentMessage",
    "AgentState",
    "AgentStatus",
    "AggregateChatChoiceV1",
    "AggregateChatCompletionsCommandV1",
    "AggregateChatCompletionsResultV1",
    "AggregateChatMessageV1",
    "AggregateCognitiveLedgerEntryV1",
    "AggregateRoleLobeV1",
    "AggregateRolePlanResultV1",
    "AggregateRuntimeAuditResultV1",
    "AggregateRuntimeEntrypointCheckV1",
    "AggregateRuntimeIntegrationV1",
    "AggregateTakeoverDirectiveV1",
    "AsyncWorker",
    "AsyncWorkerConfig",
    "AsyncWorkerPool",
    "AuditAggregateRuntimeIntegrationsQueryV1",
    "BaseEngine",
    "BuildAggregateRolePlanQueryV1",
    "ContextRequest",
    "ContextResult",
    "EngineBudget",
    "EngineContext",
    "EngineRegistry",
    "EngineResult",
    "EngineStatus",
    "EngineStrategy",
    "ExecuteRoleCapabilityInvocationCommandV1",
    "ExecuteRoleSessionCommandV1",
    "ExecuteRoleTaskCommandV1",
    "ExecuteRoleTaskMarketLifecycleCommandV1",
    "FailureClass",
    "GetRoleRuntimeStatusQueryV1",
    "HybridEngine",
    "IRoleRuntime",
    "InstantiateRoleRuntimeObjectCommandV1",
    "KernelOneMessageBusPort",
    "MessageType",
    "PathSecurityError",
    "PlanSolveEngine",
    "PromptFingerprint",
    "ProtocolBus",
    "ProtocolFSM",
    "ProtocolType",
    "ReActEngine",
    "RetryHint",
    "RoleAgent",
    "RoleCapabilityInvocationResultV1",
    "RoleContextGateway",
    "RoleContextPolicy",
    "RoleDataPolicy",
    "RoleDataStore",
    "RoleDataStoreError",
    "RoleExecutionKernel",
    "RoleExecutionMode",
    "RoleExecutionResultV1",
    "RoleLibraryPolicy",
    "RoleProfile",
    "RoleProfileRegistry",
    "RolePromptPolicy",
    "RoleRuntimeObjectResultV1",
    "RoleRuntimeService",
    "RoleSkillManager",
    "RoleTaskMarketLifecycleResultV1",
    "RoleToolGateway",
    "RoleToolPolicy",
    "RoleTurnRequest",
    "RoleTurnResult",
    "SeqEventType",
    "SeqProgressDetector",
    "SeqState",
    "SequentialBudget",
    "SequentialConfig",
    "SequentialEngine",
    "SequentialMode",
    "SequentialStateProxy",
    "SequentialStats",
    "SequentialStatsResult",
    "SequentialTraceLevel",
    "SkillLoader",
    "StepDecision",
    "StepResult",
    "StepStatus",
    "TaskClassifier",
    "TerminationReason",
    "ToTEngine",
    "ToolAuthorizationError",
    "Worker",
    "WorkerConfig",
    "WorkerPool",
    "WorkerResult",
    "WorkerState",
    "WorkerTask",
    "aggregate_chat_completions",
    "assemble_role_runtime_chain",
    "audit_aggregate_runtime_integrations",
    "classify_task",
    "create_async_worker_pool",
    "create_engine_budget",
    "create_protocol_bus",
    "create_protocol_fsm",
    "create_role_cli_parser",
    "create_role_skill_manager",
    "create_sequential_budget",
    "create_skill_loader",
    "create_worker_pool",
    "emit_seq_event",
    "execute_role_capability_invocation",
    "execute_role_session_command",
    "execute_role_task_command",
    "execute_role_task_market_lifecycle",
    "get_engine",
    "get_engine_registry",
    "get_hybrid_engine",
    "get_seq_emitter",
    "get_task_classifier",
    "instantiate_role_runtime_object",
    "load_core_roles",
    "profile_from_dict",
    "profile_to_dict",
    "query_aggregate_role_plan",
    "query_role_runtime_status",
    "register_engine",
    "registry",
    "reset_role_runtime_service",
    "run_tui",
    "should_enable_sequential",
    "stream_role_session_command",
]
