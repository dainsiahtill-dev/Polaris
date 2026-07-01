"""Public service exports for `roles.runtime` cell."""

from __future__ import annotations

# ── Standard library imports ─────────────────────────────────────────────────
import argparse

# ── Standard-library re-exports (preserved public surface) ───────────────────
# These names are no longer referenced by ``service.py`` itself after the
# lossless split, but they remain part of this module's historical public
# surface, so they are re-exported via the explicit ``X as X`` idiom.
import dataclasses as dataclasses
import hashlib as hashlib
import importlib as importlib
import json as json
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Callable as Callable, Iterable as Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor as ThreadPoolExecutor
from threading import Lock
from types import SimpleNamespace as SimpleNamespace
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
    ContextGatewayConfig as ContextGatewayConfig,
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

# Lossless split: stateless aggregate-chat planning subsystem extracted to aggregate_chat.py (re-exported to preserve the public import surface).
from polaris.cells.roles.runtime.public.aggregate_chat import (
    _AGGREGATE_FAILURE_EVIDENCE_KEYS as _AGGREGATE_FAILURE_EVIDENCE_KEYS,
    _AGGREGATE_FAILURE_SIGNAL_ALIASES as _AGGREGATE_FAILURE_SIGNAL_ALIASES,
    _AGGREGATE_LOBE_SPECS as _AGGREGATE_LOBE_SPECS,
    _AGGREGATE_MODEL_ID as _AGGREGATE_MODEL_ID,
    _AGGREGATE_RUNTIME_INTEGRATION_SPECS as _AGGREGATE_RUNTIME_INTEGRATION_SPECS,
    _BACKEND_ROOT as _BACKEND_ROOT,
    _DEFAULT_AGGREGATE_ROLE_IDS as _DEFAULT_AGGREGATE_ROLE_IDS,
    _ENTRYPOINT_MODULE_ALIASES as _ENTRYPOINT_MODULE_ALIASES,
    _SESSION_PUBLIC_EXPORTS as _SESSION_PUBLIC_EXPORTS,
    _aggregate_execution_context as _aggregate_execution_context,
    _aggregate_execution_metadata as _aggregate_execution_metadata,
    _aggregate_handoff_from_result as _aggregate_handoff_from_result,
    _aggregate_history_from_messages as _aggregate_history_from_messages,
    _aggregate_max_lobe_turns as _aggregate_max_lobe_turns,
    _aggregate_memory_current_facts as _aggregate_memory_current_facts,
    _aggregate_memory_recall_limit as _aggregate_memory_recall_limit,
    _aggregate_memory_recall_query as _aggregate_memory_recall_query,
    _aggregate_memory_recall_triggers as _aggregate_memory_recall_triggers,
    _aggregate_objective_from_messages as _aggregate_objective_from_messages,
    _aggregate_phase_for_contextos as _aggregate_phase_for_contextos,
    _aggregate_suspected_files_from_failure_evidence as _aggregate_suspected_files_from_failure_evidence,
    _attribute_check as _attribute_check,
    _build_aggregate_attention_candidates as _build_aggregate_attention_candidates,
    _build_aggregate_context_governance_pack as _build_aggregate_context_governance_pack,
    _build_aggregate_contextos_attention_budget_pack as _build_aggregate_contextos_attention_budget_pack,
    _build_aggregate_distilled_knowledge_pack as _build_aggregate_distilled_knowledge_pack,
    _build_aggregate_lobe as _build_aggregate_lobe,
    _build_aggregate_lobe_directive as _build_aggregate_lobe_directive,
    _build_aggregate_lobe_turn_envelope as _build_aggregate_lobe_turn_envelope,
    _build_aggregate_memory_recall_pack as _build_aggregate_memory_recall_pack,
    _build_aggregate_task_market_projection_pack as _build_aggregate_task_market_projection_pack,
    _build_cognitive_ledger as _build_cognitive_ledger,
    _build_compute_policy as _build_compute_policy,
    _build_runtime_audit_result as _build_runtime_audit_result,
    _build_runtime_integrations as _build_runtime_integrations,
    _build_takeover_directive as _build_takeover_directive,
    _build_takeover_evidence_status as _build_takeover_evidence_status,
    _check_aggregate_entrypoint as _check_aggregate_entrypoint,
    _dedupe_tokens as _dedupe_tokens,
    _distill_aggregate_lobe_result as _distill_aggregate_lobe_result,
    _estimate_aggregate_text_tokens as _estimate_aggregate_text_tokens,
    _extract_failure_evidence as _extract_failure_evidence,
    _extract_failure_signals as _extract_failure_signals,
    _factory_cognitive_runtime_check as _factory_cognitive_runtime_check,
    _file_check as _file_check,
    _generated_pack_check as _generated_pack_check,
    _graph_cell_check as _graph_cell_check,
    _load_session_public_symbol as _load_session_public_symbol,
    _lobe_by_id as _lobe_by_id,
    _lobe_has_current_role as _lobe_has_current_role,
    _module_check as _module_check,
    _module_exists as _module_exists,
    _normalize_failure_signal as _normalize_failure_signal,
    _public_context_adapter_check as _public_context_adapter_check,
    _read_aggregate_generated_pack_summary as _read_aggregate_generated_pack_summary,
    _render_aggregate_chain_content as _render_aggregate_chain_content,
    _render_aggregate_execution_content as _render_aggregate_execution_content,
    _render_aggregate_plan_content as _render_aggregate_plan_content,
    _roles_kernel_public_check as _roles_kernel_public_check,
    _roles_runtime_check as _roles_runtime_check,
    _route_check as _route_check,
    _select_aggregate_execution_lobe as _select_aggregate_execution_lobe,
    _select_aggregate_execution_role as _select_aggregate_execution_role,
    _select_aggregate_lobe_chain as _select_aggregate_lobe_chain,
    _select_aggregate_role_ids as _select_aggregate_role_ids,
    _selected_message_index as _selected_message_index,
    _serialize_context_budget as _serialize_context_budget,
    _serialize_distilled_knowledge_unit as _serialize_distilled_knowledge_unit,
    _stable_completion_id as _stable_completion_id,
    _summarize_aggregate_memory_pack as _summarize_aggregate_memory_pack,
    _workspace_runtime_path_check as _workspace_runtime_path_check,
)

# Lossless split: aggregate-execution methods extracted to aggregate_execution.py
# as a mixin so RoleRuntimeService keeps every method as a real class attribute.
from polaris.cells.roles.runtime.public.aggregate_execution import (
    _AggregateExecutionMixin as _AggregateExecutionMixin,
)

# Lossless split: stateless capability-command handlers extracted to capability_commands.py (re-exported to preserve the public import surface).
from polaris.cells.roles.runtime.public.capability_commands import (
    _FULL_PHASE5_REQUIRED_EVIDENCE_ROLES as _FULL_PHASE5_REQUIRED_EVIDENCE_ROLES,
    _FULL_PHASE5_REQUIRED_HANDOFF_ROLES as _FULL_PHASE5_REQUIRED_HANDOFF_ROLES,
    _FULL_PHASE5_REQUIRED_RECEIPT_ROLES as _FULL_PHASE5_REQUIRED_RECEIPT_ROLES,
    _FULL_PHASE5_REQUIRED_ROLES as _FULL_PHASE5_REQUIRED_ROLES,
    _TASK_MARKET_LIFECYCLE_CONTRACT_ATTRS as _TASK_MARKET_LIFECYCLE_CONTRACT_ATTRS,
    FutureTimeoutError as FutureTimeoutError,
    _asset_mount_ref as _asset_mount_ref,
    _audit_evidence_refs as _audit_evidence_refs,
    _capability_available_metadata as _capability_available_metadata,
    _capability_invocation_failure as _capability_invocation_failure,
    _chain_invalid_ref_failure as _chain_invalid_ref_failure,
    _change_set_validation_ref as _change_set_validation_ref,
    _check_workspace_guard_paths as _check_workspace_guard_paths,
    _chief_engineer_asset_refs as _chief_engineer_asset_refs,
    _first_ref_outside_namespace as _first_ref_outside_namespace,
    _handoff_id_from_ref as _handoff_id_from_ref,
    _handoff_pack_ref as _handoff_pack_ref,
    _handoff_rehydration_ref as _handoff_rehydration_ref,
    _mapping_string_tuple as _mapping_string_tuple,
    _merge_refs as _merge_refs,
    _normalize_model_capability as _normalize_model_capability,
    _normalize_owner_ref as _normalize_owner_ref,
    _normalize_owner_refs as _normalize_owner_refs,
    _payload_mapping as _payload_mapping,
    _payload_string as _payload_string,
    _payload_string_tuple as _payload_string_tuple,
    _pm_asset_refs as _pm_asset_refs,
    _profile_policy_ref as _profile_policy_ref,
    _ref_has_namespace as _ref_has_namespace,
    _role_runtime_chain_ref as _role_runtime_chain_ref,
    _run_with_timeout as _run_with_timeout,
    _runtime_object_audit_metadata as _runtime_object_audit_metadata,
    _runtime_receipt_ref as _runtime_receipt_ref,
    _serialize_role_state_commit_envelope as _serialize_role_state_commit_envelope,
    _task_market_lifecycle_capability as _task_market_lifecycle_capability,
    _task_market_lifecycle_failure as _task_market_lifecycle_failure,
    _task_market_lifecycle_lease_ref as _task_market_lifecycle_lease_ref,
    _task_market_lifecycle_metadata as _task_market_lifecycle_metadata,
    _task_market_lifecycle_result_ref as _task_market_lifecycle_result_ref,
    _turn_context_payload_refs as _turn_context_payload_refs,
    _unique_string_tuple as _unique_string_tuple,
    _visual_audit_evidence_refs as _visual_audit_evidence_refs,
    assemble_role_runtime_chain as assemble_role_runtime_chain,
    commit_role_state as commit_role_state,
    execute_role_capability_invocation as execute_role_capability_invocation,
    execute_role_task_market_lifecycle as execute_role_task_market_lifecycle,
    instantiate_role_runtime_object as instantiate_role_runtime_object,
    rehydrate_role_handoff as rehydrate_role_handoff,
)

# Wave 3: CLI runner module extracted to public/cli_runner.py
from polaris.cells.roles.runtime.public.cli_runner import CliRunner

# Lossless split: cognitive-runtime methods extracted to
# cognitive_runtime_methods.py as a mixin (real class attributes preserved).
from polaris.cells.roles.runtime.public.cognitive_runtime_methods import (
    _CognitiveRuntimeMixin as _CognitiveRuntimeMixin,
)

# Lossless split: cognitive-runtime / strategy-override helpers extracted to
# cognitive_strategy.py (re-exported to preserve the public import surface).
from polaris.cells.roles.runtime.public.cognitive_strategy import (
    _apply_forced_transaction_tool_guidance as _apply_forced_transaction_tool_guidance,
    _build_cognitive_strategy_override as _build_cognitive_strategy_override,
    _cognitive_runtime_result_patch as _cognitive_runtime_result_patch,
    _copy_cognitive_guidance as _copy_cognitive_guidance,
    _copy_llm_provider_policy_into_context as _copy_llm_provider_policy_into_context,
    _copy_strategy_override as _copy_strategy_override,
    _copy_string_tuple as _copy_string_tuple,
    _deep_merge_strategy_overrides as _deep_merge_strategy_overrides,
    _enforce_required_context_os as _enforce_required_context_os,
    _has_forced_transaction_tool_choice as _has_forced_transaction_tool_choice,
    _metadata_flag_enabled as _metadata_flag_enabled,
    _resolve_cognitive_runtime_blocker_approval as _resolve_cognitive_runtime_blocker_approval,
    _safe_float as _safe_float,
)

# Wave 2: Context adapter module extracted to public/context_adapter.py
from polaris.cells.roles.runtime.public.context_adapter import (
    augment_context_with_handoff_rehydration as _augment_context_with_handoff_rehydration_impl,
    augment_context_with_repo_intelligence as _augment_context_with_repo_intelligence_impl,
    load_session_context_os_snapshot as _load_session_context_os_snapshot_impl,
)

# Lossless split: §8 context-gateway asset-reader wiring extracted to
# context_gateway_wiring.py (re-exported to preserve the public import surface).
# The reader functions remain patchable on THIS module's namespace because the
# config factory resolves them through this module at call time.
from polaris.cells.roles.runtime.public.context_gateway_wiring import (
    _build_context_gateway_config_for_role as _build_context_gateway_config_for_role,
    _read_blueprint_status_for_context as _read_blueprint_status_for_context,
    _read_qa_verdict_for_context as _read_qa_verdict_for_context,
)

# Contract re-exports (preserved public surface). After the lossless split these
# contract symbols are consumed inside ``capability_commands.py`` rather than by
# ``service.py`` itself, but they remain part of this module's historical public
# surface, so they are re-exported via the explicit ``X as X`` idiom.
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
    AssembleRoleRuntimeChainCommandV1 as AssembleRoleRuntimeChainCommandV1,
    AuditAggregateRuntimeIntegrationsQueryV1,
    BuildAggregateRolePlanQueryV1,
    ExecuteRoleCapabilityInvocationCommandV1,
    ExecuteRoleSessionCommandV1,
    ExecuteRoleTaskCommandV1,
    ExecuteRoleTaskMarketLifecycleCommandV1,
    GetRoleRuntimeStatusQueryV1,
    InstantiateRoleRuntimeObjectCommandV1,
    IRoleRuntime,
    RehydrateRoleHandoffCommandV1,
    RoleCapabilityDescriptor as RoleCapabilityDescriptor,
    RoleCapabilityInvocationResultV1,
    RoleExecutionResultV1,
    RoleHandoffRehydrationResultV1,
    RoleIdentity as RoleIdentity,
    RoleLedgerBinding as RoleLedgerBinding,
    RoleProfileBinding as RoleProfileBinding,
    RoleRuntimeChainAssemblyResultV1 as RoleRuntimeChainAssemblyResultV1,
    RoleRuntimeChainEnvelope as RoleRuntimeChainEnvelope,
    RoleRuntimeObject as RoleRuntimeObject,
    RoleRuntimeObjectResultV1,
    RoleStateCommitReceipt as RoleStateCommitReceipt,
    RoleStateCommitRequest as RoleStateCommitRequest,
    RoleTaskMarketLifecycleResultV1,
    get_builtin_role_runtime_spec as get_builtin_role_runtime_spec,
)

# Wave 1: Persistence module extracted to public/persistence.py
from polaris.cells.roles.runtime.public.persistence import (
    persist_session_turn_state as _persist_session_turn_state_impl,
    project_host_history as _project_host_history_impl,
)

# Lossless split: contract result-mapping helpers extracted to result_mapping.py
# (re-exported to preserve the public import surface).
from polaris.cells.roles.runtime.public.result_mapping import (
    _contract_result_metadata as _contract_result_metadata,
    _copy_batch_receipt_metadata as _copy_batch_receipt_metadata,
    _copy_result_metadata as _copy_result_metadata,
    _copy_tool_result_metadata as _copy_tool_result_metadata,
    _extract_artifacts as _extract_artifacts,
    _extract_tool_calls as _extract_tool_calls,
    _extract_turn_envelope_metadata as _extract_turn_envelope_metadata,
    _to_contract_result as _to_contract_result,
    _with_result_metadata_patch as _with_result_metadata_patch,
)

# Lossless split: strategy-resolution methods extracted to strategy_resolution.py
# as a mixin (real class attributes preserved).
from polaris.cells.roles.runtime.public.strategy_resolution import (
    _StrategyResolutionMixin as _StrategyResolutionMixin,
)
from polaris.kernelone.context.runtime_feature_flags import (
    resolve_context_os_enabled as resolve_context_os_enabled,
)

# Skill system: prefer KernelOne implementation, keep Cells layer for backward compat
from polaris.kernelone.telemetry.debug_stream import emit_debug_event

if TYPE_CHECKING:
    from polaris.cells.roles.session.public import (
        PathSecurityError,
        RoleDataStore,
        RoleDataStoreError,
    )

logger = logging.getLogger(__name__)


def get_role_system_prompt(*args, **kwargs) -> Any:
    """Lazy proxy to avoid control-plane import cycles at module import time."""
    from polaris.cells.llm.control_plane.public.service import (
        get_role_system_prompt as _get_role_system_prompt,
    )

    return _get_role_system_prompt(*args, **kwargs)


class WorkflowRoleResult(dict):
    """Compatibility marker type for workflow role execution results."""


class RoleRuntimeService(
    _AggregateExecutionMixin,
    _CognitiveRuntimeMixin,
    _StrategyResolutionMixin,
    IRoleRuntime,
):
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
                kernel = RoleExecutionKernel(
                    workspace=token,
                    registry=registry,
                    context_gateway_config_factory=_build_context_gateway_config_for_role,
                )
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
        from polaris.cells.roles.kernel.internal.kernel.transaction_factory import create_transaction_kernel

        return create_transaction_kernel(kernel, command.role, profile, request)

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
    "RehydrateRoleHandoffCommandV1",
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
    "RoleHandoffRehydrationResultV1",
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
    "rehydrate_role_handoff",
    "reset_role_runtime_service",
    "run_tui",
    "should_enable_sequential",
    "stream_role_session_command",
]
