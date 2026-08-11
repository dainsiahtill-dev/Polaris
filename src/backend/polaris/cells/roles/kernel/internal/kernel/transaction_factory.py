"""TransactionKernel assembly for RoleExecutionKernel.

This module is the canonical factory for kernel-backed TransactionKernel
instances. `RoleExecutionKernel` and execution helpers call this free function
directly so transaction assembly has one implementation surface.

Design notes (FROZEN behavior — do NOT change):
- The nested ``_LLMProvider`` / ``_ToolRuntime`` / ``_LLMProviderStream``
  classes capture a ``weakref`` to the kernel so they never keep it alive, and
  tool execution enters through ``kernel.tool_runtime_executor.execute_single_tool``
  and explicit turn-boundary resets enter through the same runtime owner.
- ``_LLMProvider``/``_ToolRuntime``/``_LLMProviderStream`` use ``__slots__ = ()``
  so they remain zero-attribute callables, identical to the in-class version.
- The five helper closures (history dedup, context-override assembly, model
  override) capture ``provider_request`` / ``provider_profile`` / ``llm_invoker``
  by lexical scope exactly as before.
"""

from __future__ import annotations

import copy
import dataclasses
import inspect
import weakref
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from polaris.cells.director.runtime.public.directed_effect_policy_contracts import (
    DirectorEffectPolicySnapshotPortV1,
)
from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
from polaris.cells.roles.kernel.internal.directed_effect_lifecycle import (
    _refresh_directed_effect_attempt,
)
from polaris.cells.roles.kernel.internal.directed_effect_policy_guard import (
    DirectedEffectPolicyGuard,
)
from polaris.cells.roles.kernel.internal.exploration_workflow import ExplorationWorkflowRuntime
from polaris.cells.roles.kernel.internal.kernel.llm_invoker_provider import get_llm_invoker
from polaris.cells.roles.kernel.internal.kernel.tool_executor import (
    derive_role_turn_capability_scope,
    derive_role_turn_capability_token,
)
from polaris.cells.roles.kernel.internal.kernel.tool_runtime_executor import (
    execute_single_tool,
    reset_cached_tool_gateway_turn_boundary,
    resolve_authorized_tool_gateway,
)
from polaris.cells.roles.kernel.internal.kernel.transaction_turn_id import (
    TransactionIdentityError,
    _require_bound_transaction_attempt,
)
from polaris.cells.roles.kernel.internal.llm_caller.helpers import resolve_context_output_budget_tokens
from polaris.cells.roles.kernel.internal.llm_caller.request_facts import project_role_request_facts
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import native_tool_calls_from_response
from polaris.cells.roles.kernel.internal.structured_output_transport import (
    StructuredOutputStreamNormalizer,
    normalize_structured_output_response,
    resolve_structured_output_transport,
)
from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
from polaris.cells.roles.kernel.internal.transaction.recon_policy import resolve_recon_required
from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel
from polaris.cells.roles.kernel.public.directed_effect_contracts import (
    DirectedEffectRuntimeDependenciesV1,
)
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptAuthorityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.kernelone.storage import resolve_workspace_runtime_identity

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
    from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _truthy_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _first_non_empty(*values: Any) -> str:
    for value in values:
        token = str(value or "").strip()
        if token:
            return token
    return ""


def _resolve_turn_transition_id(request: RoleTurnRequest) -> str:
    """Return the stable execution transition identity bound before kernel creation."""

    metadata = getattr(request, "metadata", None)
    metadata_map = metadata if isinstance(metadata, dict) else {}
    attempt_transition = str(metadata_map.get("transaction_attempt_id") or "").strip()
    compatibility_transition = str(metadata_map.get("turn_transition_id") or "").strip()
    if not attempt_transition:
        if compatibility_transition:
            raise TransactionIdentityError(
                "turn_transition_id has no bound transaction attempt producer",
                code="transaction_identity_unbound",
            )
        return ""
    identity = _require_bound_transaction_attempt(request)
    if compatibility_transition and compatibility_transition != identity.transition_id:
        raise TransactionIdentityError(
            "turn_transition_id cannot override transaction_attempt_id",
            code="transaction_identity_mismatch",
        )
    return identity.transition_id


def _resolve_durable_workspace(request: RoleTurnRequest, kernel: RoleExecutionKernel) -> str:
    """Resolve one durable workspace and reject request/kernel identity drift.

    A role request must never redirect a durable fact to a canonical project
    cache when the role kernel is bound to a distinct fresh workspace.
    """

    request_workspace = str(getattr(request, "workspace", "") or "").strip()
    kernel_workspace = str(getattr(kernel, "workspace", "") or "").strip()
    if not request_workspace:
        return kernel_workspace
    if not kernel_workspace:
        return request_workspace
    request_identity = resolve_workspace_runtime_identity(request_workspace)
    kernel_identity = resolve_workspace_runtime_identity(kernel_workspace)
    if request_identity.workspace_abs != kernel_identity.workspace_abs:
        raise RuntimeError(
            "durable turn workspace identity mismatch "
            f"request={request_identity.workspace_abs!r} kernel={kernel_identity.workspace_abs!r}"
        )
    return request_identity.workspace_abs


def _resolve_existing_output_budget_tokens(context_override: dict[str, Any]) -> int | None:
    """Delegate to the ONE llm_caller key scan (budget_policy blueprint Phase 1).

    This module previously mirrored the helpers key scan; the shared
    implementation (clamped to the hard output-token limit) is behavior
    preserving here — the result only feeds ``max(floor, existing or 0)``
    below, and downstream ``resolve_max_tokens`` applies the same clamp.
    """
    return resolve_context_output_budget_tokens(context_override)


def _assert_task_runtime_guard_allows_tool(request: Any) -> None:
    context_override = _as_mapping(getattr(request, "context_override", None))
    metadata = _as_mapping(getattr(request, "metadata", None))
    guard_enabled = _truthy_flag(
        _first_non_empty(
            context_override.get("task_runtime_guard"),
            metadata.get("task_runtime_guard"),
        )
    )
    if not guard_enabled:
        return

    authority = context_override.get("task_runtime_execution_attempt_authority")
    if not isinstance(authority, TaskRuntimeExecutionAttemptAuthorityV1):
        raise RuntimeError(
            "director_tool_execution_guard_misconfigured: missing task_runtime_execution_attempt_authority"
        )

    refresh, upstream_code = _refresh_directed_effect_attempt(
        authority=authority,
        expected_execution_attempt=None,
        context_summary="transaction_kernel_tool_guard",
    )
    if refresh.status != "fresh":
        raise RuntimeError(f"director_tool_execution_guard_heartbeat_rejected:{upstream_code}")


def _resolve_directed_effect_composition(
    *,
    kernel: RoleExecutionKernel,
    request: RoleTurnRequest,
    directed_effect_runtime: DirectedEffectRuntimeDependenciesV1 | None,
    directed_effect_required: bool | None,
) -> tuple[
    DirectedEffectRuntimeDependenciesV1 | None,
    bool,
    TaskRuntimeExecutionAttemptIdentityV1 | None,
    TaskRuntimeExecutionAttemptAuthorityV1 | None,
]:
    """Resolve one explicit DEO bundle and fresh public attempt for a turn."""

    kernel_runtime = kernel.directed_effect_runtime
    kernel_required = kernel.directed_effect_required
    if directed_effect_runtime is not None and type(directed_effect_runtime) is not DirectedEffectRuntimeDependenciesV1:
        raise TypeError("directed_effect_runtime must be exactly DirectedEffectRuntimeDependenciesV1")
    if (
        kernel_runtime is not None
        and directed_effect_runtime is not None
        and directed_effect_runtime is not kernel_runtime
    ):
        raise RuntimeError("directed_effect_runtime identity mismatch")
    runtime = directed_effect_runtime if directed_effect_runtime is not None else kernel_runtime
    required = kernel_required if directed_effect_required is None else bool(directed_effect_required)
    if required != kernel_required and kernel_runtime is not None:
        raise RuntimeError("directed_effect_required drift")
    if required and runtime is None:
        raise RuntimeError("directed_effect_runtime_required")
    if runtime is None:
        return None, required, None, None

    context_override = _as_mapping(getattr(request, "context_override", None))
    authority = context_override.get("task_runtime_execution_attempt_authority")
    if not isinstance(authority, TaskRuntimeExecutionAttemptAuthorityV1):
        if required:
            raise RuntimeError("directed_effect_execution_attempt_authority_required")
        return runtime, required, None, None
    refresh, upstream_code = _refresh_directed_effect_attempt(
        authority=authority,
        expected_execution_attempt=None,
        context_summary="directed_effect_transaction_kernel_create",
    )
    if refresh.status != "fresh" or refresh.execution_attempt is None:
        if required:
            raise RuntimeError(f"directed_effect_execution_attempt_refresh_failed:{upstream_code}")
        return runtime, required, None, None
    return runtime, required, refresh.execution_attempt, authority


def create_transaction_kernel(
    kernel: RoleExecutionKernel,
    role: str,
    profile: RoleProfile,
    request: RoleTurnRequest,
    *,
    directed_effect_runtime: DirectedEffectRuntimeDependenciesV1 | None = None,
    directed_effect_required: bool | None = None,
) -> TransactionKernel:
    """Create a TransactionKernel with kernel-backed LLM and tool adapters.

    Uses explicit parameter passing instead of closures to avoid circular
    reference issues between nested classes and the kernel instance.
    """
    (
        resolved_deo_runtime,
        resolved_deo_required,
        resolved_execution_attempt,
        resolved_execution_authority,
    ) = _resolve_directed_effect_composition(
        kernel=kernel,
        request=request,
        directed_effect_runtime=directed_effect_runtime,
        directed_effect_required=directed_effect_required,
    )
    # Resolve freshness before constructing any provider-side dependency.  A
    # missing or stale execution attempt therefore cannot reach LLM dispatch.
    llm_invoker = get_llm_invoker(kernel)

    def _normalize_user_text(value: Any) -> str:
        return str(value or "").replace("\ufeff", "").strip()

    def _build_history_without_current_user(
        messages: list[dict[str, Any]],
        current_message: str,
    ) -> list[tuple[str, str]]:
        history: list[tuple[str, str]] = []
        for msg in messages:
            role_label = str(msg.get("role", ""))
            content = str(msg.get("content", ""))
            if role_label in ("user", "assistant", "tool"):
                history.append((role_label, content))

        normalized_current = _normalize_user_text(current_message)
        if normalized_current:
            history = [
                (role_label, content)
                for role_label, content in history
                if not (role_label == "user" and _normalize_user_text(content) == normalized_current)
            ]

        return history

    def _build_context_override_with_prebuilt_messages(
        prebuilt_messages: list[dict[str, Any]],
        tool_definitions: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
        temperature_override: Any | None = None,
        max_tokens_floor: Any | None = None,
    ) -> dict[str, Any]:
        override: dict[str, Any]
        override = dict(role_request_fact_projection.context_override)
        incoming_choice_is_none = isinstance(tool_choice, str) and tool_choice.strip().lower() == "none"
        explicit_tool_disable = (
            isinstance(override.get("_transaction_kernel_forced_tool_definitions"), list)
            and not override.get("_transaction_kernel_forced_tool_definitions")
            and str(override.get("_transaction_kernel_forced_tool_choice") or "").strip().lower() == "none"
        )
        existing_forced_defs = override.get("_transaction_kernel_forced_tool_definitions")
        existing_forced_choice = override.get("_transaction_kernel_forced_tool_choice")
        existing_forced_scope = (isinstance(existing_forced_defs, list) and bool(existing_forced_defs)) or (
            existing_forced_choice is not None
            and not (isinstance(existing_forced_choice, str) and existing_forced_choice.strip().lower() in {"", "auto"})
        )
        incoming_choice_is_default = tool_choice is None or (
            isinstance(tool_choice, str) and tool_choice.strip().lower() == "auto"
        )
        preserve_existing_forced_scope = existing_forced_scope and incoming_choice_is_default
        override["_transaction_kernel_prebuilt_messages"] = [
            dict(item) for item in prebuilt_messages if isinstance(item, dict)
        ]
        if incoming_choice_is_none:
            override["_transaction_kernel_forced_tool_definitions"] = []
            override["_transaction_kernel_forced_tool_choice"] = "none"
        elif isinstance(tool_definitions, list) and not explicit_tool_disable and not preserve_existing_forced_scope:
            override["_transaction_kernel_forced_tool_definitions"] = [
                dict(item) for item in tool_definitions if isinstance(item, dict)
            ]
        if (
            tool_choice is not None
            and not incoming_choice_is_none
            and not explicit_tool_disable
            and not preserve_existing_forced_scope
        ):
            override["_transaction_kernel_forced_tool_choice"] = tool_choice
        # ADR-0090 W2.6: phase-aware low temperature rides the same channel.
        if temperature_override is not None:
            override["_transaction_kernel_temperature_override"] = temperature_override
        # I3-r22 (F10): the reserved output floor rides the same channel as an
        # output-budget lower bound. It must never shrink a larger execution
        # strategy budget; high-capacity Director runs should keep their declared
        # output contract instead of being capped at the retry floor.
        if max_tokens_floor is not None:
            try:
                floor_value = int(max_tokens_floor)
            except (TypeError, ValueError):
                floor_value = 0
            if floor_value > 0:
                existing_budget = _resolve_existing_output_budget_tokens(override)
                override["llm_max_tokens"] = max(floor_value, existing_budget or 0)
                override.pop("_transaction_kernel_retry_output_budget_bounded", None)
                override.pop("_transaction_kernel_retry_output_budget_reason", None)
        return override

    def _extract_model_override_from_request_payload(request_payload: dict[str, Any]) -> str | None:
        token = str(request_payload.get("model_override") or "").strip()
        if not token:
            return None
        return token

    def _build_effective_profile(request_payload: dict[str, Any]) -> Any:
        model_override = _extract_model_override_from_request_payload(request_payload)
        if not model_override:
            return provider_profile
        base_model = str(getattr(provider_profile, "model", "") or "").strip()
        if not model_override or model_override == base_model:
            return provider_profile
        if hasattr(provider_profile, "model_copy"):
            try:
                return provider_profile.model_copy(update={"model": model_override})  # type: ignore[union-attr]
            except (AttributeError, TypeError, ValueError):
                pass
        if dataclasses.is_dataclass(provider_profile) and not isinstance(provider_profile, type):
            try:
                return dataclasses.replace(provider_profile, model=model_override)  # type: ignore[type-var]
            except (TypeError, ValueError):
                pass
        try:
            cloned_profile = copy.copy(provider_profile)
            object.__setattr__(cloned_profile, "model", model_override)
            return cloned_profile
        except (AttributeError, TypeError):
            fallback_payload = {}
            if hasattr(provider_profile, "__dict__"):
                fallback_payload = dict(getattr(provider_profile, "__dict__", {}) or {})
            fallback_payload["model"] = model_override
            return SimpleNamespace(**fallback_payload)

    kernel_weakref = weakref.ref(kernel)
    provider_profile = profile
    provider_request = request
    role_request_fact_projection = project_role_request_facts(
        context_override=getattr(provider_request, "context_override", None),
        metadata=getattr(provider_request, "metadata", None),
    )
    structured_output_transport = resolve_structured_output_transport(role_request_fact_projection.context_override)

    class _LLMProvider:
        """Encapsulated LLM provider for TransactionKernel."""

        __slots__ = ()

        async def __call__(self, request_payload: dict[str, Any]) -> dict[str, Any]:
            effective_profile = _build_effective_profile(request_payload)
            raw_messages = list(request_payload.get("messages", []))
            messages = list(raw_messages)
            system_prompt = ""
            if messages and messages[0].get("role") == "system":
                system_prompt = str(messages[0].get("content", ""))
                messages = messages[1:]

            current_message = str(getattr(provider_request, "message", "") or "")
            history = _build_history_without_current_user(messages, current_message)

            context = ContextRequest(
                message=current_message,
                history=tuple(history),
                task_id=provider_request.task_id,
                context_override=_build_context_override_with_prebuilt_messages(
                    raw_messages,
                    cast("list[dict[str, Any]] | None", request_payload.get("tools")),
                    request_payload.get("tool_choice"),
                    request_payload.get("temperature_override"),
                    request_payload.get("max_tokens_floor"),
                ),
            )

            tool_choice = request_payload.get("tool_choice")
            tool_definitions = request_payload.get("tools")
            run_id = str(provider_request.run_id or "").strip() or None
            task_id_str = str(provider_request.task_id or "").strip() or None

            if tool_choice == "none":
                if hasattr(llm_invoker, "call_finalization") and inspect.iscoroutinefunction(
                    getattr(llm_invoker, "call_finalization", None)
                ):
                    finalization_response = await llm_invoker.call_finalization(
                        profile=effective_profile,
                        system_prompt=system_prompt,
                        context=context,
                        run_id=run_id,
                        task_id=task_id_str,
                        attempt=0,
                        turn_round=0,
                    )
                    return normalize_structured_output_response(
                        finalization_response,
                        structured_output_transport,
                    )
                response = await llm_invoker.call(
                    profile=effective_profile,
                    system_prompt=system_prompt,
                    context=context,
                    run_id=run_id,
                    task_id=task_id_str,
                    attempt=0,
                    turn_round=0,
                )
                if getattr(response, "error", None):
                    raise RuntimeError(str(response.error))
                native_tool_calls = native_tool_calls_from_response(response)
                normalized_response = {
                    "content": response.content,
                    "thinking": getattr(response, "thinking", None),
                    "tool_calls": native_tool_calls,
                    "native_tool_calls": native_tool_calls,
                    "model": str(getattr(response, "model", "unknown") or "unknown"),
                    "usage": dict(getattr(response, "metadata", {}) or {}),
                }
                return normalize_structured_output_response(
                    normalized_response,
                    structured_output_transport,
                )
            if hasattr(llm_invoker, "call_decision") and inspect.iscoroutinefunction(
                getattr(llm_invoker, "call_decision", None)
            ):
                decision_response = await llm_invoker.call_decision(
                    profile=effective_profile,
                    system_prompt=system_prompt,
                    context=context,
                    tool_definitions=tool_definitions if tool_definitions else None,
                    run_id=run_id,
                    task_id=task_id_str,
                    attempt=0,
                    turn_round=0,
                )
                return normalize_structured_output_response(
                    decision_response,
                    structured_output_transport,
                )
            response = await llm_invoker.call(
                profile=effective_profile,
                system_prompt=system_prompt,
                context=context,
                run_id=run_id,
                task_id=task_id_str,
                attempt=0,
                turn_round=0,
            )
            if getattr(response, "error", None):
                raise RuntimeError(str(response.error))
            native_tool_calls = native_tool_calls_from_response(response)
            normalized_response = {
                "content": response.content,
                "thinking": getattr(response, "thinking", None),
                "tool_calls": native_tool_calls,
                "native_tool_calls": native_tool_calls,
                "model": str(getattr(response, "model", "unknown") or "unknown"),
                "usage": dict(getattr(response, "metadata", {}) or {}),
            }
            return normalize_structured_output_response(
                normalized_response,
                structured_output_transport,
            )

    class _ToolRuntime:
        """Encapsulated tool runtime for TransactionKernel."""

        __slots__ = ()

        def reset_turn_boundary(self, turn_id: str) -> None:
            kernel = kernel_weakref()
            if kernel is None:
                return
            normalized_turn_id = str(turn_id or "").strip()
            if not normalized_turn_id:
                return
            cast(Any, provider_request).turn_id = normalized_turn_id
            reset_cached_tool_gateway_turn_boundary(kernel, normalized_turn_id)

        def directed_effect_policy_guard(
            self,
            policy_port: DirectorEffectPolicySnapshotPortV1,
        ) -> DirectedEffectPolicyGuard:
            kernel = kernel_weakref()
            if kernel is None:
                raise RuntimeError("Kernel instance no longer exists")
            gateway = resolve_authorized_tool_gateway(
                kernel,
                profile=provider_profile,
                request=provider_request,
            )
            if not isinstance(gateway, RoleToolGateway):
                raise RuntimeError("directed_effect_gateway_authority_unavailable")
            return DirectedEffectPolicyGuard(gateway, policy_port)

        async def __call__(self, tool_name: str, arguments: dict[str, Any]) -> Any:
            kernel = kernel_weakref()
            if kernel is None:
                raise RuntimeError("Kernel instance no longer exists")
            _assert_task_runtime_guard_allows_tool(provider_request)
            return await execute_single_tool(
                kernel,
                tool_name=tool_name,
                args=arguments,
                context={"profile": provider_profile, "request": provider_request},
            )

    class _LLMProviderStream:
        """Encapsulated streaming LLM provider for TransactionKernel."""

        __slots__ = ()

        async def __call__(self, request_payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
            if not hasattr(llm_invoker, "call_stream"):
                return

            effective_profile = _build_effective_profile(request_payload)
            raw_messages = list(request_payload.get("messages", []))
            messages = list(raw_messages)
            system_prompt = ""
            if messages and messages[0].get("role") == "system":
                system_prompt = str(messages[0].get("content", ""))
                messages = messages[1:]

            current_message = str(getattr(provider_request, "message", "") or "")
            history = _build_history_without_current_user(messages, current_message)

            context = ContextRequest(
                message=current_message,
                history=tuple(history),
                task_id=provider_request.task_id,
                context_override=_build_context_override_with_prebuilt_messages(
                    raw_messages,
                    cast("list[dict[str, Any]] | None", request_payload.get("tools")),
                    request_payload.get("tool_choice"),
                    request_payload.get("temperature_override"),
                    request_payload.get("max_tokens_floor"),
                ),
            )

            run_id = str(provider_request.run_id or "").strip() or None
            task_id_str = str(provider_request.task_id or "").strip() or None
            stream_normalizer = (
                StructuredOutputStreamNormalizer(structured_output_transport)
                if structured_output_transport is not None
                else None
            )

            async for chunk in llm_invoker.call_stream(
                profile=effective_profile,
                system_prompt=system_prompt,
                context=context,
                run_id=run_id,
                task_id=task_id_str,
                attempt=0,
            ):
                if stream_normalizer is None:
                    yield chunk
                    continue
                for projected in stream_normalizer.project(chunk):
                    yield projected

    llm_provider = _LLMProvider()
    tool_runtime = _ToolRuntime()
    llm_provider_stream = _LLMProviderStream() if hasattr(llm_invoker, "call_stream") else None

    workflow_runtime = ExplorationWorkflowRuntime(
        tool_executor=tool_runtime,
        synthesis_llm=None,
    )
    durable_commit_required = bool(str(request.run_id or "").strip() and str(request.task_id or "").strip())
    durable_workspace = (
        _resolve_durable_workspace(request, kernel) if durable_commit_required else str(request.workspace or "").strip()
    )
    capability_scope = derive_role_turn_capability_scope(request)
    capability_token = derive_role_turn_capability_token(request, capability_scope)
    execution_envelope_hash = str(capability_token.get("execution_envelope_hash") or "").strip()

    return TransactionKernel(
        llm_provider=llm_provider,
        tool_runtime=tool_runtime,
        config=TransactionConfig(
            domain="code" if role in {"director", "chief_engineer"} else "document",
            role_id=role,
            run_id=str(request.run_id or "").strip(),
            task_id=str(request.task_id or "").strip(),
            transition_id=_resolve_turn_transition_id(request),
            durable_commit_required=durable_commit_required,
            workspace=durable_workspace,
            mutation_guard_mode="strict" if role == "director" else "warn",
            recon_required=resolve_recon_required(role, provider_profile),
        ),
        workflow_runtime=workflow_runtime,
        llm_provider_stream=llm_provider_stream,
        directed_effect_runtime=resolved_deo_runtime,
        directed_effect_required=resolved_deo_required,
        directed_effect_execution_attempt=resolved_execution_attempt,
        directed_effect_execution_attempt_authority=resolved_execution_authority,
        capability_token=capability_token,
        execution_envelope_hash=execution_envelope_hash,
    )
