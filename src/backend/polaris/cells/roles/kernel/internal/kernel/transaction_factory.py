"""TransactionKernel assembly for RoleExecutionKernel.

This module is the canonical factory for kernel-backed TransactionKernel
instances. `RoleExecutionKernel` and execution helpers call this free function
directly so transaction assembly has one implementation surface.

Design notes (FROZEN behavior — do NOT change):
- The nested ``_LLMProvider`` / ``_ToolRuntime`` / ``_LLMProviderStream``
  classes capture a ``weakref`` to the kernel so they never keep it alive, and
  tool execution enters through ``kernel.tool_runtime_executor.execute_single_tool``
  while explicit turn-boundary resets remain on the kernel public boundary.
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

from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
from polaris.cells.roles.kernel.internal.exploration_workflow import ExplorationWorkflowRuntime
from polaris.cells.roles.kernel.internal.kernel.llm_invoker_provider import get_llm_invoker
from polaris.cells.roles.kernel.internal.kernel.tool_runtime_executor import execute_single_tool
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
from polaris.cells.roles.kernel.internal.transaction.recon_policy import resolve_recon_required
from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel

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


def _coerce_positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _mapping_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _resolve_existing_output_budget_tokens(context_override: dict[str, Any]) -> int | None:
    for key in ("llm_max_tokens", "max_output_tokens", "max_tokens"):
        parsed = _coerce_positive_int(context_override.get(key))
        if parsed is not None:
            return parsed
    for payload_key in (
        "task_execution_contract",
        "director_execution_contract",
        "task_execution_strategy",
        "director_execution_strategy",
        "execution_strategy",
    ):
        payload = _mapping_value(context_override.get(payload_key))
        context_budget = _mapping_value(payload.get("context_budget"))
        for nested_key in ("output_budget_tokens", "llm_max_tokens", "max_output_tokens", "max_tokens"):
            parsed = _coerce_positive_int(payload.get(nested_key))
            if parsed is None:
                parsed = _coerce_positive_int(context_budget.get(nested_key))
            if parsed is not None:
                return parsed
    return None


def _assert_task_runtime_guard_allows_tool(request: Any) -> None:
    context_override = _as_mapping(getattr(request, "context_override", None))
    metadata = _as_mapping(getattr(request, "metadata", None))
    guard_enabled = _truthy_flag(
        _first_non_empty(
            context_override.get("task_runtime_guard"),
            metadata.get("task_runtime_guard"),
        )
    )
    session_id = _first_non_empty(
        context_override.get("task_runtime_session_id"),
        metadata.get("task_runtime_session_id"),
        context_override.get("session_id"),
        metadata.get("session_id"),
    )
    if not guard_enabled and not session_id:
        return

    workspace = _first_non_empty(
        context_override.get("workspace"),
        metadata.get("workspace"),
        getattr(request, "workspace", ""),
    )
    task_id = _first_non_empty(
        context_override.get("task_id"),
        context_override.get("pm_task_id"),
        context_override.get("target_task_id"),
        metadata.get("task_id"),
        metadata.get("pm_task_id"),
        metadata.get("target_task_id"),
        getattr(request, "task_id", ""),
    )
    missing = [
        name
        for name, value in (
            ("workspace", workspace),
            ("task_id", task_id),
            ("task_runtime_session_id", session_id),
        )
        if not value
    ]
    if missing:
        raise RuntimeError("director_tool_execution_guard_misconfigured: missing " + ",".join(missing))

    from polaris.cells.runtime.task_runtime.public import TaskRuntimeService

    result = TaskRuntimeService(workspace).heartbeat_execution(
        task_id,
        session_id=session_id,
        lease_ttl_seconds=120,
        context_summary="transaction_kernel_tool_guard",
    )
    if result.get("success") is True:
        return
    reason = str(result.get("reason") or "task_runtime_guard_blocked").strip()
    raise RuntimeError(
        "director_tool_execution_cancelled: task_runtime_guard_blocked "
        f"reason={reason} task_id={task_id} session_id={session_id}"
    )


def create_transaction_kernel(
    kernel: RoleExecutionKernel,
    role: str,
    profile: RoleProfile,
    request: RoleTurnRequest,
) -> TransactionKernel:
    """Create a TransactionKernel with kernel-backed LLM and tool adapters.

    Uses explicit parameter passing instead of closures to avoid circular
    reference issues between nested classes and the kernel instance.
    """
    # Keep the canonical invoker entrypoint so TransactionKernel context
    # overrides (forced tool definitions/tool_choice) are preserved end-to-end.
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
        if isinstance(getattr(provider_request, "context_override", None), dict):
            override = dict(provider_request.context_override or {})
        else:
            override = {}
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
                forced_tool_choice = tool_choice is not None and not (
                    isinstance(tool_choice, str) and tool_choice.strip().lower() in {"", "auto"}
                )
                if forced_tool_choice:
                    override["llm_max_tokens"] = floor_value
                    override["_transaction_kernel_retry_output_budget_bounded"] = True
                    override["_transaction_kernel_retry_output_budget_reason"] = (
                        "forced_tool_retry_must_not_inherit_full_execution_budget"
                    )
                else:
                    override["llm_max_tokens"] = max(floor_value, existing_budget or 0)
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
                    return await llm_invoker.call_finalization(
                        profile=effective_profile,
                        system_prompt=system_prompt,
                        context=context,
                        run_id=run_id,
                        task_id=task_id_str,
                        attempt=0,
                        turn_round=0,
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
                return {
                    "content": response.content,
                    "thinking": getattr(response, "thinking", None),
                    "tool_calls": getattr(response, "tool_calls", []) or [],
                    "model": str(getattr(response, "model", "unknown") or "unknown"),
                    "usage": dict(getattr(response, "metadata", {}) or {}),
                }
            if hasattr(llm_invoker, "call_decision") and inspect.iscoroutinefunction(
                getattr(llm_invoker, "call_decision", None)
            ):
                return await llm_invoker.call_decision(
                    profile=effective_profile,
                    system_prompt=system_prompt,
                    context=context,
                    tool_definitions=tool_definitions if tool_definitions else None,
                    run_id=run_id,
                    task_id=task_id_str,
                    attempt=0,
                    turn_round=0,
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
            return {
                "content": response.content,
                "thinking": getattr(response, "thinking", None),
                "tool_calls": getattr(response, "tool_calls", []) or [],
                "model": str(getattr(response, "model", "unknown") or "unknown"),
                "usage": dict(getattr(response, "metadata", {}) or {}),
            }

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
            kernel.reset_tool_gateway_turn_boundary(normalized_turn_id)

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

            async for chunk in llm_invoker.call_stream(
                profile=effective_profile,
                system_prompt=system_prompt,
                context=context,
                run_id=run_id,
                task_id=task_id_str,
                attempt=0,
            ):
                yield chunk

    llm_provider = _LLMProvider()
    tool_runtime = _ToolRuntime()
    llm_provider_stream = _LLMProviderStream() if hasattr(llm_invoker, "call_stream") else None

    workflow_runtime = ExplorationWorkflowRuntime(
        tool_executor=tool_runtime,
        synthesis_llm=None,
    )

    return TransactionKernel(
        llm_provider=llm_provider,
        tool_runtime=tool_runtime,
        config=TransactionConfig(
            domain="code" if role in {"director", "chief_engineer"} else "document",
            role_id=role,
            workspace=str(request.workspace or "").strip(),
            mutation_guard_mode="strict" if role == "director" else "warn",
            recon_required=resolve_recon_required(role, provider_profile),
        ),
        workflow_runtime=workflow_runtime,
        llm_provider_stream=llm_provider_stream,
    )
