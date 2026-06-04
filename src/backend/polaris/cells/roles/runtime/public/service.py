"""Public service exports for `roles.runtime` cell."""

from __future__ import annotations

# ── Standard library imports ─────────────────────────────────────────────────
import argparse
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from threading import Lock
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
    ExecuteRoleSessionCommandV1,
    ExecuteRoleTaskCommandV1,
    GetRoleRuntimeStatusQueryV1,
    IRoleRuntime,
    RoleExecutionResultV1,
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

_SESSION_PUBLIC_EXPORTS = frozenset(
    {
        "PathSecurityError",
        "RoleDataStore",
        "RoleDataStoreError",
    }
)


def _load_session_public_symbol(name: str) -> object:
    from polaris.cells.roles.session import public as session_public

    value = getattr(session_public, name)
    globals()[name] = value
    return value


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
        metadata=_copy_result_metadata(result.metadata),
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
                receipt_result = service.record_runtime_receipt(
                    RecordRuntimeReceiptCommandV1(
                        workspace=workspace,
                        receipt_type="role_runtime_turn",
                        session_id=session_id,
                        run_id=run_id,
                        payload={
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
                        },
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
        metadata["session_id"] = command.session_id
        metadata["stream"] = bool(command.stream)
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
            raise RuntimeError("cognitive_runtime_mainline_unavailable")

        if bool(cognitive_context.get("blocked")):
            reason = str(cognitive_context.get("block_reason") or "blocked").strip()
            raise RuntimeError(f"cognitive_runtime_blocked:{reason}")

        guidance = _copy_cognitive_guidance(cognitive_context)
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
                        event_metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
                        event["metadata"] = {
                            **dict(event_metadata),
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
    "AsyncWorker",
    "AsyncWorkerConfig",
    "AsyncWorkerPool",
    "BaseEngine",
    "ContextRequest",
    "ContextResult",
    "EngineBudget",
    "EngineContext",
    "EngineRegistry",
    "EngineResult",
    "EngineStatus",
    "EngineStrategy",
    "ExecuteRoleSessionCommandV1",
    "ExecuteRoleTaskCommandV1",
    "FailureClass",
    "GetRoleRuntimeStatusQueryV1",
    "HybridEngine",
    "IRoleRuntime",
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
    "RoleRuntimeService",
    "RoleSkillManager",
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
    "execute_role_session_command",
    "execute_role_task_command",
    "get_engine",
    "get_engine_registry",
    "get_hybrid_engine",
    "get_seq_emitter",
    "get_task_classifier",
    "load_core_roles",
    "profile_from_dict",
    "profile_to_dict",
    "query_role_runtime_status",
    "register_engine",
    "registry",
    "reset_role_runtime_service",
    "run_tui",
    "should_enable_sequential",
    "stream_role_session_command",
]
