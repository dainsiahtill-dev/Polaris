"""TurnEngine - Facade over TransactionKernel.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Slice B (2026-04-16): TurnEngine has been cut over to a pure facade.
- All while-True loops, tool scheduling, and continuation logic removed.
- run() / run_stream() delegate directly to TransactionKernel.execute() / execute_stream().
- Public signatures preserved for backward compatibility.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
import warnings
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.kernel.tool_policy import _apply_forced_transaction_tool_definitions
from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel
from polaris.cells.roles.kernel.internal.turn_engine.turn_materializer import TurnMaterializer
from polaris.cells.roles.kernel.internal.turn_transaction_controller import TransactionConfig
from polaris.cells.roles.profile.public.service import RoleTurnResult
from polaris.kernelone.audit.context_os_prompt import summarize_context_os_audit_from_ledger
from polaris.kernelone.context.contracts import (
    TurnEngineContextRequest as ContextRequest,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from polaris.cells.roles.profile.public.service import RoleTurnRequest

logger = logging.getLogger(__name__)


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


class TurnEngine:
    """Deprecated turn-engine API reduced to a TransactionKernel facade.

    Deprecated compatibility shim. New execution behavior must land in
    TransactionKernel / RoleExecutionKernel, while this facade only preserves
    the minimum compatibility result shape still consumed by tests and adapters.
    """

    _DEPRECATION_WARNING = (
        "TurnEngine is deprecated and frozen as a TransactionKernel compatibility "
        "facade. New execution behavior must not be added here."
    )
    _deprecation_warning_emitted = False

    def __init__(
        self,
        kernel: Any,
        config: Any | None = None,
        llm_caller: Any | None = None,
        output_parser: Any | None = None,
        prompt_builder: Any | None = None,
        policy_layer: Any | None = None,
        cognitive_pipeline: Any | None = None,
    ) -> None:
        """Initialize facade (compatibility collaborators ignored)."""
        self._kernel = kernel
        self._llm_invoker = llm_caller if llm_caller is not None else kernel._get_llm_invoker()
        self._prompt_builder = prompt_builder if prompt_builder is not None else kernel._get_prompt_builder()
        self._output_parser = output_parser if output_parser is not None else getattr(kernel, "_output_parser", None)
        self._materializer = TurnMaterializer(output_parser=self._output_parser)
        if not type(self)._deprecation_warning_emitted:
            warnings.warn(self._DEPRECATION_WARNING, DeprecationWarning, stacklevel=2)
            type(self)._deprecation_warning_emitted = True

    @staticmethod
    def _request_metadata(request: RoleTurnRequest) -> dict[str, Any]:
        raw_metadata = getattr(request, "metadata", None)
        return dict(raw_metadata) if isinstance(raw_metadata, dict) else {}

    @staticmethod
    def _assert_task_runtime_guard_allows_tool(request: RoleTurnRequest) -> None:
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

    @staticmethod
    def _normalize_receipt_ids(receipt_ids: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
        normalized: list[str] = []
        for receipt_id in receipt_ids or ():
            token = str(receipt_id or "").strip()
            if token and token not in normalized:
                normalized.append(token)
        return tuple(normalized)

    def _derive_projection_version(self, request: RoleTurnRequest) -> str | None:
        request_metadata = self._request_metadata(request)
        raw_projection_version = request_metadata.get("projection_version")
        if raw_projection_version in (None, ""):
            context_override = getattr(request, "context_override", None)
            safe_context_override = dict(context_override) if isinstance(context_override, dict) else {}
            context_os_snapshot = safe_context_override.get("context_os_snapshot")
            if isinstance(context_os_snapshot, dict):
                version = context_os_snapshot.get("version")
                if version not in (None, ""):
                    raw_projection_version = f"state_first_context_os.v{version}"
        projection_version = str(raw_projection_version or "").strip()
        return projection_version or None

    def _build_turn_envelope(
        self,
        *,
        request: RoleTurnRequest,
        role: str,
        turn_id: str,
        receipt_ids: list[str] | tuple[str, ...] | None = None,
    ) -> Any:
        from polaris.domain.cognitive_runtime.models import TurnEnvelope

        request_metadata = self._request_metadata(request)
        session_id = str(request_metadata.get("session_id") or "").strip() or None
        run_id = str(getattr(request, "run_id", "") or "").strip() or None
        task_id = str(getattr(request, "task_id", "") or "").strip() or None
        return TurnEnvelope(
            turn_id=turn_id,
            projection_version=self._derive_projection_version(request),
            lease_id=str(request_metadata.get("lease_id") or "").strip() or None,
            validation_id=str(request_metadata.get("validation_id") or "").strip() or None,
            receipt_ids=self._normalize_receipt_ids(receipt_ids),
            session_id=session_id,
            run_id=run_id,
            role=str(role or "").strip() or None,
            task_id=task_id,
        )

    def _build_result_metadata(
        self,
        *,
        request: RoleTurnRequest,
        role: str,
        turn_id: str,
        receipt_ids: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        turn_envelope = self._build_turn_envelope(
            request=request,
            role=role,
            turn_id=turn_id,
            receipt_ids=receipt_ids,
        )
        return {
            "turn_id": turn_envelope.turn_id,
            "turn_envelope": turn_envelope.to_dict(),
        }

    @staticmethod
    def _receipt_refs_from_batch_receipt(batch_receipt: dict[str, Any] | None) -> list[str]:
        if not isinstance(batch_receipt, dict):
            return []
        batch_id = str(batch_receipt.get("batch_id", "")).strip()
        return [batch_id] if batch_id else []

    @staticmethod
    def _build_turn_transcript(
        *,
        turn_id: str,
        user_message: str,
        assistant_content: str,
        tool_results: list[dict[str, Any]],
    ) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
        """Build (turn_history, turn_events_metadata) for ContextOS persistence.

        Single source of truth shared by ``run()`` (non-streaming) and
        ``_build_stream_result()`` (streaming) so both paths emit byte-identical
        transcript shapes. Self-contained — depends only on its arguments, never
        on the kernel, so stub/mock kernels in tests keep working.
        """
        turn_history: list[tuple[str, str]] = []
        turn_events_metadata: list[dict[str, Any]] = []

        normalized_user = str(user_message or "").strip()
        if normalized_user:
            turn_history.append(("user", normalized_user))
            turn_events_metadata.append(
                {
                    "role": "user",
                    "content": normalized_user,
                    "event_id": f"user_{turn_id}",
                    "kind": "user_turn",
                }
            )

        normalized_assistant = str(assistant_content or "").strip()
        if normalized_assistant:
            turn_history.append(("assistant", normalized_assistant))
            turn_events_metadata.append(
                {
                    "role": "assistant",
                    "content": normalized_assistant,
                    "event_id": f"assistant_{turn_id}",
                    "kind": "assistant_turn",
                }
            )

        for tr in tool_results:
            if not isinstance(tr, dict):
                continue
            tool_name = str(tr.get("tool") or "tool").strip() or "tool"
            result_value = tr.get("result")
            if result_value is not None:
                result_text = json.dumps(result_value, ensure_ascii=False)
            else:
                error_text = str(tr.get("error") or "").strip()
                result_text = f"Error: {error_text}" if error_text else ""
            if result_text:
                turn_history.append(("tool", result_text))
                turn_events_metadata.append(
                    {
                        "role": "tool",
                        "content": result_text,
                        "event_id": f"tool_{tr.get('call_id', turn_id)}",
                        "kind": "tool_result",
                        "tool": tool_name,
                    }
                )

        return turn_history, turn_events_metadata

    def _build_stream_result(
        self,
        *,
        request: RoleTurnRequest,
        role: str,
        profile: Any,
        fingerprint: Any,
        turn_id: str,
        status: str,
        content: str,
        thinking: str | None,
        tool_calls: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
        execution_stats: dict[str, Any],
        batch_receipt: dict[str, Any] | None = None,
        receipt_ids: list[str] | tuple[str, ...] | None = None,
        response_model: type | None = None,
    ) -> RoleTurnResult:
        structured_output: dict[str, Any] | None = None
        if response_model is not None and content:
            try:
                parser = self._kernel._get_output_parser()
                candidate = parser.extract_json(content)
                if candidate is not None:
                    validated = response_model(**candidate)
                    structured_output = validated.model_dump()
            except (RuntimeError, ValueError):
                structured_output = None

        metadata = self._build_result_metadata(
            request=request,
            role=role,
            turn_id=turn_id,
            receipt_ids=receipt_ids,
        )
        monitoring = execution_stats.get("monitoring")
        if isinstance(monitoring, dict) and isinstance(monitoring.get("context_os_audit"), dict):
            metadata["context_os_audit"] = dict(monitoring["context_os_audit"])
        if status == "handoff":
            metadata["transaction_kind"] = "handoff_workflow"

        # Build turn history and events metadata for ContextOS persistence
        turn_history, turn_events_metadata = self._build_turn_transcript(
            turn_id=turn_id,
            user_message=str(getattr(request, "message", "") or ""),
            assistant_content=content,
            tool_results=tool_results,
        )

        return RoleTurnResult(
            content=content,
            thinking=thinking,
            structured_output=structured_output,
            tool_calls=list(tool_calls),
            tool_results=list(tool_results),
            batch_receipt=dict(batch_receipt) if isinstance(batch_receipt, dict) else None,
            profile_version=profile.version,
            prompt_fingerprint=fingerprint,
            tool_policy_id=profile.tool_policy.policy_id,
            error=None,
            is_complete=status != "failed",
            execution_stats=dict(execution_stats),
            turn_history=turn_history,
            turn_events_metadata=turn_events_metadata,
            metadata=metadata,
        )

    def _create_transaction_kernel(
        self,
        role: str,
        profile: Any,
        request: RoleTurnRequest,
    ) -> TransactionKernel:
        """Create a TransactionKernel with kernel-backed LLM and tool adapters."""
        import inspect

        # Prefer the canonical invoker so context_override-based forced tool scope
        # is honored during contract-retry paths.
        llm_invoker = self._llm_invoker
        if not inspect.iscoroutinefunction(getattr(llm_invoker, "call", None)):
            llm_invoker = (
                self._llm_invoker._get_invoker() if hasattr(self._llm_invoker, "_get_invoker") else self._llm_invoker
            )

        def _preserve_existing_forced_tool_scope(context_override: dict[str, Any], tool_choice: Any) -> bool:
            existing_forced_defs = context_override.get("_transaction_kernel_forced_tool_definitions")
            existing_forced_choice = context_override.get("_transaction_kernel_forced_tool_choice")
            existing_forced_scope = (isinstance(existing_forced_defs, list) and bool(existing_forced_defs)) or (
                existing_forced_choice is not None
                and not (
                    isinstance(existing_forced_choice, str) and existing_forced_choice.strip().lower() in {"", "auto"}
                )
            )
            incoming_choice_is_default = tool_choice is None or (
                isinstance(tool_choice, str) and tool_choice.strip().lower() == "auto"
            )
            return existing_forced_scope and incoming_choice_is_default

        async def llm_provider(request_payload: dict[str, Any]) -> dict[str, Any]:
            raw_messages = list(request_payload.get("messages", []))
            messages = list(raw_messages)
            system_prompt = ""
            if messages and messages[0].get("role") == "system":
                system_prompt = str(messages[0].get("content", ""))
                messages = messages[1:]

            history: list[tuple[str, str]] = []
            for msg in messages:
                role_label = msg.get("role", "")
                content = msg.get("content", "")
                if role_label in ("user", "assistant", "tool"):
                    history.append((role_label, content))

            context_override = (
                dict(request.context_override or {}) if isinstance(request.context_override, dict) else {}
            )
            explicit_tool_disable = (
                isinstance(context_override.get("_transaction_kernel_forced_tool_definitions"), list)
                and not context_override.get("_transaction_kernel_forced_tool_definitions")
                and str(context_override.get("_transaction_kernel_forced_tool_choice") or "").strip().lower() == "none"
            )
            preserve_existing_forced_scope = _preserve_existing_forced_tool_scope(
                context_override,
                request_payload.get("tool_choice"),
            )
            context_override["_transaction_kernel_prebuilt_messages"] = [
                dict(item) for item in raw_messages if isinstance(item, dict)
            ]
            if (
                isinstance(request_payload.get("tools"), list)
                and not explicit_tool_disable
                and not preserve_existing_forced_scope
            ):
                context_override["_transaction_kernel_forced_tool_definitions"] = [
                    dict(item) for item in request_payload["tools"] if isinstance(item, dict)
                ]
            if (
                request_payload.get("tool_choice") is not None
                and not explicit_tool_disable
                and not preserve_existing_forced_scope
            ):
                context_override["_transaction_kernel_forced_tool_choice"] = request_payload.get("tool_choice")
            # ADR-0090 W2.6: phase-aware decoding — escalated mutation retries
            # carry a low-temperature override down the same channel.
            if request_payload.get("temperature_override") is not None:
                context_override["_transaction_kernel_temperature_override"] = request_payload.get(
                    "temperature_override"
                )
            max_tokens_floor = request_payload.get("max_tokens_floor")
            if max_tokens_floor is not None:
                try:
                    floor_value = int(max_tokens_floor)
                except (TypeError, ValueError):
                    floor_value = 0
                if floor_value > 0:
                    tool_choice_value = request_payload.get("tool_choice")
                    forced_tool_choice = tool_choice_value is not None and not (
                        isinstance(tool_choice_value, str) and tool_choice_value.strip().lower() in {"", "auto"}
                    )
                    if forced_tool_choice:
                        context_override["llm_max_tokens"] = floor_value
                        context_override["_transaction_kernel_retry_output_budget_bounded"] = True
                        context_override["_transaction_kernel_retry_output_budget_reason"] = (
                            "forced_tool_retry_must_not_inherit_full_execution_budget"
                        )
                    else:
                        existing_budget = 0
                        for budget_key in ("llm_max_tokens", "max_output_tokens", "max_tokens"):
                            try:
                                existing_budget = max(existing_budget, int(context_override.get(budget_key) or 0))
                            except (TypeError, ValueError):
                                continue
                        context_override["llm_max_tokens"] = max(floor_value, existing_budget or 0)

            context = ContextRequest(
                message=getattr(request, "message", "") or "",
                history=tuple(history),
                task_id=request.task_id,
                context_override=context_override,
            )

            tool_choice = request_payload.get("tool_choice")
            tool_definitions = request_payload.get("tools")
            run_id = str(request.run_id or "").strip() or None
            task_id_str = str(request.task_id or "").strip() or None

            import asyncio

            if tool_choice == "none":
                if hasattr(llm_invoker, "call_finalization") and asyncio.iscoroutinefunction(
                    getattr(llm_invoker, "call_finalization", None)
                ):
                    return await llm_invoker.call_finalization(
                        profile=profile,
                        system_prompt=system_prompt,
                        context=context,
                        run_id=run_id,
                        task_id=task_id_str,
                        attempt=0,
                        turn_round=0,
                    )
                response = await llm_invoker.call(
                    profile=profile,
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
            if hasattr(llm_invoker, "call_decision") and asyncio.iscoroutinefunction(
                getattr(llm_invoker, "call_decision", None)
            ):
                return await llm_invoker.call_decision(
                    profile=profile,
                    system_prompt=system_prompt,
                    context=context,
                    tool_definitions=tool_definitions if tool_definitions else None,
                    run_id=run_id,
                    task_id=task_id_str,
                    attempt=0,
                    turn_round=0,
                )
            response = await llm_invoker.call(
                profile=profile,
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

        async def tool_runtime(tool_name: str, arguments: dict[str, Any]) -> Any:
            try:
                self._assert_task_runtime_guard_allows_tool(request)
                return await self._kernel._execute_single_tool(
                    tool_name=tool_name,
                    args=arguments,
                    context={"profile": profile, "request": request},
                )
            except (RuntimeError, TypeError, ValueError):
                # TODO: narrow exception type — underlying tool executor may raise
                # provider-specific exceptions (ConnectionError, TimeoutError, etc.)
                logger.exception("tool_runtime failed: tool=%s", tool_name)
                raise

        async def llm_provider_stream(request_payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
            if not hasattr(llm_invoker, "call_stream"):
                return
            raw_messages = list(request_payload.get("messages", []))
            messages = list(raw_messages)
            system_prompt = ""
            if messages and messages[0].get("role") == "system":
                system_prompt = str(messages[0].get("content", ""))
                messages = messages[1:]

            history: list[tuple[str, str]] = []
            for msg in messages:
                role_label = msg.get("role", "")
                content = msg.get("content", "")
                if role_label in ("user", "assistant", "tool"):
                    history.append((role_label, content))

            context_override = (
                dict(request.context_override or {}) if isinstance(request.context_override, dict) else {}
            )
            explicit_tool_disable = (
                isinstance(context_override.get("_transaction_kernel_forced_tool_definitions"), list)
                and not context_override.get("_transaction_kernel_forced_tool_definitions")
                and str(context_override.get("_transaction_kernel_forced_tool_choice") or "").strip().lower() == "none"
            )
            preserve_existing_forced_scope = _preserve_existing_forced_tool_scope(
                context_override,
                request_payload.get("tool_choice"),
            )
            context_override["_transaction_kernel_prebuilt_messages"] = [
                dict(item) for item in raw_messages if isinstance(item, dict)
            ]
            if (
                isinstance(request_payload.get("tools"), list)
                and not explicit_tool_disable
                and not preserve_existing_forced_scope
            ):
                context_override["_transaction_kernel_forced_tool_definitions"] = [
                    dict(item) for item in request_payload["tools"] if isinstance(item, dict)
                ]
            if (
                request_payload.get("tool_choice") is not None
                and not explicit_tool_disable
                and not preserve_existing_forced_scope
            ):
                context_override["_transaction_kernel_forced_tool_choice"] = request_payload.get("tool_choice")
            # ADR-0090 W2.6: phase-aware decoding — escalated mutation retries
            # carry a low-temperature override down the same channel.
            if request_payload.get("temperature_override") is not None:
                context_override["_transaction_kernel_temperature_override"] = request_payload.get(
                    "temperature_override"
                )

            context = ContextRequest(
                message=getattr(request, "message", "") or "",
                history=tuple(history),
                task_id=request.task_id,
                context_override=context_override,
            )

            run_id = str(request.run_id or "").strip() or None
            task_id_str = str(request.task_id or "").strip() or None

            async for chunk in llm_invoker.call_stream(
                profile=profile,
                system_prompt=system_prompt,
                context=context,
                run_id=run_id,
                task_id=task_id_str,
                attempt=0,
            ):
                yield chunk

        return TransactionKernel(
            llm_provider=llm_provider,
            tool_runtime=tool_runtime,
            config=TransactionConfig(
                role_id=role,
                domain="code" if role in {"director", "chief_engineer"} else "document",
                mutation_guard_mode="strict" if role == "director" else "warn",
                recon_required=self._resolve_recon_required(role, profile),
            ),
            llm_provider_stream=llm_provider_stream if hasattr(llm_invoker, "call_stream") else None,
        )

    @staticmethod
    def _resolve_recon_required(role: str, profile: Any) -> bool:
        """读侧落地不变量信号（ADR-0091 R3）。

        来源与 ``RoleContextGateway._recon_mode_active`` 镜像：角色 profile 的
        ``context_policy.recon_mode``（持久档位）OR scout 角色专属的
        ``KERNELONE_SCOUT_RECON_MODE`` 环境灰度开关。默认 False——
        非侦察角色的事务路径逐字节不变。
        """
        context_policy = getattr(profile, "context_policy", None)
        if bool(getattr(context_policy, "recon_mode", False)):
            return True
        env = os.getenv("KERNELONE_SCOUT_RECON_MODE", "").strip().lower()
        return env in {"1", "true", "yes", "on"} and str(role or "").strip().lower() == "scout"

    def _materialize_assistant_turn(
        self,
        *,
        profile: Any,
        raw_output: str,
        native_tool_calls: list[dict[str, Any]] | None = None,
        native_tool_provider: str = "auto",
    ) -> Any:
        return self._materializer.materialize(
            profile=profile,
            raw_output=raw_output,
            native_tool_calls=native_tool_calls,
            native_tool_provider=native_tool_provider,
            kernel=self._kernel,
        )

    def _materialize_stream_visible_turn(
        self,
        *,
        profile: Any,
        raw_output: str,
        streamed_thinking_parts: list[str],
        native_tool_calls: list[dict[str, Any]] | None = None,
        native_tool_provider: str = "auto",
    ) -> Any:
        return self._materializer.materialize_stream_visible(
            profile=profile,
            raw_output=raw_output,
            streamed_thinking_parts=streamed_thinking_parts,
            native_tool_calls=native_tool_calls,
            native_tool_provider=native_tool_provider,
            kernel=self._kernel,
        )

    def _parse_tool_calls_from_turn(
        self,
        *,
        profile: Any,
        turn: Any,
    ) -> list[Any]:
        return TurnMaterializer.parse_tool_calls(
            profile=profile,
            turn=turn,
            kernel=self._kernel,
        )

    async def _execute_single_tool(
        self,
        profile: Any,
        request: Any,
        call: Any,
    ) -> dict[str, Any]:
        from polaris.cells.roles.kernel.internal.tool_gateway import ToolAuthorizationError

        tool_name = ""
        raw_args: Any = {}
        if isinstance(call, dict):
            tool_name = str(call.get("tool") or call.get("name") or "").strip()
            raw_args = call.get("args")
        else:
            tool_name = str(getattr(call, "tool", "") or getattr(call, "name", "") or "").strip()
            raw_args = getattr(call, "args", {})
        tool_args = dict(raw_args) if isinstance(raw_args, dict) else {}

        try:
            return await self._kernel._execute_single_tool(
                tool_name=tool_name,
                args=tool_args,
                context={"profile": profile, "request": request},
            )
        except ToolAuthorizationError as exc:
            return {
                "success": False,
                "tool": tool_name,
                "error": f"TOOL_BLOCKED: {exc}",
                "authorized": False,
                "policy": "ToolPolicy",
                "loop_break": False,
                "authorization_failure": True,
                "error_type": "ToolAuthorizationError",
            }

    async def run(
        self,
        request: RoleTurnRequest,
        role: str,
        controller=None,
        system_prompt: str | None = None,
        fingerprint: Any | None = None,
        attempt: int = 0,
        response_model: type | None = None,
    ) -> RoleTurnResult:
        """非流式执行主入口 — 委托给 TransactionKernel。"""
        from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import (
            build_native_tool_schemas,
            extract_declared_step_target_files,
            pin_write_tool_file_param_to_targets,
            resolve_from_scratch_write_target,
            resolve_repair_edit_target,
            restrict_tool_definitions_to_edit,
            restrict_tool_definitions_to_write,
        )
        from polaris.cells.roles.kernel.internal.tool_loop_controller import ToolLoopController
        from polaris.cells.roles.kernel.public.service import RoleContextGateway

        kernel = self._kernel

        try:
            profile = kernel.registry.get_profile_or_raise(role)
        except ValueError as exc:
            logger.error("获取角色 profile 失败 (%s): %s", role, exc)
            return RoleTurnResult(
                error=str(exc),
                is_complete=False,
                profile_version="",
                prompt_fingerprint=fingerprint,
                tool_policy_id="",
            )

        _system_prompt = (
            system_prompt
            if system_prompt is not None
            else (
                kernel._build_system_prompt_for_request(profile, request, request.prompt_appendix or "")
                if hasattr(kernel, "_build_system_prompt_for_request")
                else self._prompt_builder.build_system_prompt(profile, request.prompt_appendix or "")
            )
        )
        _fingerprint = (
            fingerprint
            if fingerprint is not None
            else self._prompt_builder.build_fingerprint(profile, request.prompt_appendix or "")
        )

        _controller = (
            controller if controller is not None else ToolLoopController.from_request(request=request, profile=profile)
        )
        context_request = _controller.build_context_request()
        context_gateway = RoleContextGateway(profile, kernel.workspace)
        # ADR-0090 I4.3: the gateway budgets AND prepends the role system prompt —
        # the former second ProjectionEngine().project pass (double projection,
        # unbudgeted system prompt, throwaway ReceiptStore) is gone.
        context_result = await context_gateway.build_context(context_request, system_prompt=_system_prompt)
        messages: list[dict[str, Any]] = list(context_result.messages)

        tool_definitions = build_native_tool_schemas(profile)
        # Fix-11 (live I3-r9/r12): a fission step is single-file by contract —
        # pin write tools' file-param enum to the declared target. Strict guided
        # decoding (named tool forcing) makes a wrong-file write ungenerable;
        # schema-advisory providers still see the strongest possible signal.
        declared_step_targets = extract_declared_step_target_files(getattr(request, "context_override", None))
        if declared_step_targets:
            tool_definitions = pin_write_tool_file_param_to_targets(tool_definitions, declared_step_targets)
        # Prong A (I3-r23): a from-scratch leaf step writes on turn 1 — restrict to
        # minimal execution tools so weak Directors still receive schema-backed
        # read/locate tools referenced by prompts while mutation gates require a
        # write in the emitted batch.
        _from_scratch_target = resolve_from_scratch_write_target(
            getattr(request, "context_override", None), kernel.workspace
        )
        if _from_scratch_target:
            tool_definitions = restrict_tool_definitions_to_write(tool_definitions)
            logger.info(
                "first-turn minimal execution schema for from-scratch leaf step: target=%s",
                _from_scratch_target,
            )
        else:
            # R7 (I3-r28): a repair/bounce turn on an EXISTING target edits in place —
            # drop the whole-file rewrite verb so the weak model fixes the named
            # failure instead of rewriting the file smaller (live r28 main.js
            # 5762B->3095B). Mutually exclusive with the from-scratch branch above.
            _repair_target = resolve_repair_edit_target(getattr(request, "context_override", None), kernel.workspace)
            if _repair_target:
                tool_definitions = restrict_tool_definitions_to_edit(tool_definitions)
                logger.info(
                    "repair-turn edit-only for existing target: target=%s",
                    _repair_target,
                )
        tool_definitions = _apply_forced_transaction_tool_definitions(
            tool_definitions,
            getattr(request, "context_override", None),
        )

        tk = self._create_transaction_kernel(role, profile, request)
        turn_id = str(request.run_id or uuid.uuid4().hex[:12])

        try:
            tk_result = await tk.execute(turn_id, messages, tool_definitions)
        except Exception as exc:
            logger.exception("TransactionKernel execute failed: turn_id=%s", turn_id)
            try:
                context_gateway.record_projection_outcome(
                    success=False,
                    tokens_used=int(getattr(context_result, "token_estimate", 0) or 0),
                )
            except (AttributeError, RuntimeError, TypeError, ValueError):
                logger.debug("Projection outcome feedback failed after TransactionKernel error", exc_info=True)
            return RoleTurnResult(
                content="",
                error=f"TransactionKernel execution failed: {exc}",
                is_complete=False,
                profile_version=profile.version,
                prompt_fingerprint=_fingerprint,
                tool_policy_id=profile.tool_policy.policy_id,
            )

        kind = tk_result.get("kind", "final_answer")
        visible_content = tk_result.get("visible_content", "")
        thinking_text: str | None = None
        if visible_content:
            parsed = kernel._get_output_parser().parse_thinking(visible_content)
            visible_content = str(parsed.clean_content or "")
            thinking_text = parsed.thinking
        batch_receipt = tk_result.get("batch_receipt")
        normalized_batch_receipt = dict(batch_receipt) if isinstance(batch_receipt, dict) else None
        finalization = tk_result.get("finalization")
        workflow_context = tk_result.get("workflow_context")
        metrics = tk_result.get("metrics", {})

        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        if batch_receipt:
            for result in batch_receipt.get("results", []):
                tool_calls.append(
                    {
                        "tool": result.get("tool_name", ""),
                        "args": {},
                        "call_id": result.get("call_id", ""),
                    }
                )
                tool_results.append(
                    {
                        "tool": result.get("tool_name", ""),
                        "tool_name": result.get("tool_name", ""),
                        "result": result.get("result"),
                        "success": result.get("status") == "success",
                        "status": result.get("status"),
                        "call_id": result.get("call_id", ""),
                        "arguments": result.get("arguments"),
                        "effect_receipt": result.get("effect_receipt"),
                        "raw_result": dict(result),
                    }
                )

        structured_output: dict[str, Any] | None = None
        if response_model is not None and visible_content:
            try:
                parser = kernel._get_output_parser()
                candidate = parser.extract_json(visible_content)
                if candidate is not None:
                    validated = response_model(**candidate)
                    structured_output = validated.model_dump()
            except (RuntimeError, ValueError):
                structured_output = None

        execution_stats = {
            "duration_ms": metrics.get("duration_ms", 0),
            "llm_calls": metrics.get("llm_calls", 0),
            "tool_calls": metrics.get("tool_calls", 0),
            "transaction_kernel": True,
        }

        receipt_refs = self._receipt_refs_from_batch_receipt(batch_receipt)
        metadata = self._build_result_metadata(
            request=request,
            role=role,
            turn_id=turn_id,
            receipt_ids=receipt_refs,
        )
        context_os_audit_summary = summarize_context_os_audit_from_ledger(tk_result.get("ledger"))
        if context_os_audit_summary:
            metadata["context_os_audit"] = context_os_audit_summary
        if kind == "handoff_workflow" and workflow_context is not None:
            import time

            from polaris.domain.cognitive_runtime.models import ContextHandoffPack

            recoverable_context = workflow_context.get("recoverable_context") or {}
            decision = recoverable_context.get("decision") or {}
            batch_receipts = recoverable_context.get("batch_receipts") or []
            turn_id_str = str(tk_result.get("turn_id", ""))
            run_id = str(request.run_id or "").strip() or turn_id_str

            handoff_receipt_refs: list[str] = []
            for receipt in batch_receipts:
                batch_id = str(receipt.get("batch_id", ""))
                if batch_id:
                    handoff_receipt_refs.append(batch_id)

            turn_envelope = self._build_turn_envelope(
                request=request,
                role=role,
                turn_id=turn_id_str,
                receipt_ids=handoff_receipt_refs,
            )

            handoff_pack = ContextHandoffPack(
                handoff_id=f"handoff_{turn_id_str}_{uuid.uuid4().hex[:8]}",
                workspace=str(request.workspace or kernel.workspace or "."),
                created_at=str(int(time.time())),
                session_id=str(request.task_id or "").strip() or turn_id_str,
                run_id=run_id if run_id else None,
                reason=str(workflow_context.get("handoff_reason", "transaction_kernel_handoff")),
                current_goal=str(decision.get("metadata", {}).get("current_goal", "")),
                run_card=dict(decision.get("metadata", {}).get("run_card", {})),
                context_slice_plan={"workflow_context": workflow_context},
                decision_log=(recoverable_context,),
                receipt_refs=tuple(handoff_receipt_refs),
                turn_envelope=turn_envelope,
            )
            metadata["handoff_pack"] = handoff_pack.to_dict()
            metadata["transaction_kind"] = "handoff_workflow"

        error_msg: str | None = None
        is_complete = True
        if kind == "ask_user" and isinstance(finalization, dict):
            error_msg = finalization.get("error")
            is_complete = False
        if isinstance(finalization, dict) and bool(finalization.get("needs_followup_workflow")):
            workflow_reason = str(finalization.get("workflow_reason") or kind or "").strip()
            metadata["transaction_kind"] = str(kind or workflow_reason)
            metadata["needs_followup_workflow"] = True
            metadata["workflow_reason"] = workflow_reason
            metadata["blocked_reason"] = finalization.get("blocked_reason")
            metadata["blocked_detail"] = finalization.get("blocked_detail")
            error_msg = (
                str(
                    finalization.get("error")
                    or finalization.get("blocked_reason")
                    or workflow_reason
                    or "needs_followup_workflow"
                ).strip()
                or None
            )
            is_complete = False

        final_thinking = thinking_text
        if final_thinking is None and isinstance(finalization, dict):
            final_thinking = finalization.get("final_visible_message")

        try:
            metadata["projection_adaptive_weights_after_turn"] = context_gateway.record_projection_outcome(
                success=bool(is_complete and not error_msg),
                tokens_used=int(getattr(context_result, "token_estimate", 0) or 0),
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            logger.debug("Projection outcome feedback failed", exc_info=True)

        # Build turn history and events metadata for ContextOS persistence
        turn_history, turn_events_metadata = self._build_turn_transcript(
            turn_id=turn_id,
            user_message=str(getattr(request, "message", "") or ""),
            assistant_content=visible_content,
            tool_results=tool_results,
        )

        return RoleTurnResult(
            content=visible_content,
            thinking=final_thinking,
            structured_output=structured_output,
            tool_calls=tool_calls,
            tool_results=tool_results,
            batch_receipt=normalized_batch_receipt,
            profile_version=profile.version,
            prompt_fingerprint=_fingerprint,
            tool_policy_id=profile.tool_policy.policy_id,
            error=error_msg,
            is_complete=is_complete,
            execution_stats=execution_stats,
            turn_history=turn_history,
            turn_events_metadata=turn_events_metadata,
            metadata=metadata,
        )

    async def run_stream(
        self,
        request: RoleTurnRequest,
        role: str,
        controller=None,
        system_prompt: str | None = None,
        fingerprint: Any | None = None,
        attempt: int = 0,
        response_model: type | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式执行主入口 — 委托给 TransactionKernel.execute_stream()。"""
        from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import (
            build_native_tool_schemas,
            extract_declared_step_target_files,
            pin_write_tool_file_param_to_targets,
            resolve_from_scratch_write_target,
            resolve_repair_edit_target,
            restrict_tool_definitions_to_edit,
            restrict_tool_definitions_to_write,
        )
        from polaris.cells.roles.kernel.internal.tool_loop_controller import ToolLoopController
        from polaris.cells.roles.kernel.public.service import RoleContextGateway
        from polaris.cells.roles.kernel.public.turn_events import (
            CompletionEvent,
            ContentChunkEvent,
            ErrorEvent,
            FinalizationEvent,
            ToolBatchEvent,
            TurnPhaseEvent,
        )

        kernel = self._kernel
        stream_run_id = str(request.run_id or uuid.uuid4().hex[:8])

        try:
            profile = kernel.registry.get_profile_or_raise(role)
        except ValueError as exc:
            yield {"type": "error", "error": f"获取角色 profile 失败: {exc}"}
            return

        _system_prompt = (
            system_prompt
            if system_prompt is not None
            else (
                kernel._build_system_prompt_for_request(profile, request, request.prompt_appendix or "")
                if hasattr(kernel, "_build_system_prompt_for_request")
                else self._prompt_builder.build_system_prompt(profile, request.prompt_appendix or "")
            )
        )
        _fingerprint = (
            fingerprint
            if fingerprint is not None
            else self._prompt_builder.build_fingerprint(profile, request.prompt_appendix or "")
        )
        yield {"type": "fingerprint", "fingerprint": _fingerprint}

        _controller = (
            controller if controller is not None else ToolLoopController.from_request(request=request, profile=profile)
        )
        context_request = _controller.build_context_request()
        context_gateway = RoleContextGateway(profile, kernel.workspace)
        # ADR-0090 I4.3: gateway budgets AND prepends the role system prompt — no
        # second projection pass.
        context_result = await context_gateway.build_context(context_request, system_prompt=_system_prompt)
        messages: list[dict[str, Any]] = list(context_result.messages)

        tool_definitions = build_native_tool_schemas(profile)
        # Fix-11 (live I3-r9/r12): a fission step is single-file by contract —
        # pin write tools' file-param enum to the declared target. Strict guided
        # decoding (named tool forcing) makes a wrong-file write ungenerable;
        # schema-advisory providers still see the strongest possible signal.
        declared_step_targets = extract_declared_step_target_files(getattr(request, "context_override", None))
        if declared_step_targets:
            tool_definitions = pin_write_tool_file_param_to_targets(tool_definitions, declared_step_targets)
        # Prong A (I3-r23): a from-scratch leaf step writes on turn 1 — restrict to
        # minimal execution tools so weak Directors still receive schema-backed
        # read/locate tools referenced by prompts while mutation gates require a
        # write in the emitted batch.
        _from_scratch_target = resolve_from_scratch_write_target(
            getattr(request, "context_override", None), kernel.workspace
        )
        if _from_scratch_target:
            tool_definitions = restrict_tool_definitions_to_write(tool_definitions)
            logger.info(
                "first-turn minimal execution schema for from-scratch leaf step: target=%s",
                _from_scratch_target,
            )
        else:
            # R7 (I3-r28): a repair/bounce turn on an EXISTING target edits in place —
            # drop the whole-file rewrite verb so the weak model fixes the named
            # failure instead of rewriting the file smaller (live r28 main.js
            # 5762B->3095B). Mutually exclusive with the from-scratch branch above.
            _repair_target = resolve_repair_edit_target(getattr(request, "context_override", None), kernel.workspace)
            if _repair_target:
                tool_definitions = restrict_tool_definitions_to_edit(tool_definitions)
                logger.info(
                    "repair-turn edit-only for existing target: target=%s",
                    _repair_target,
                )
        tool_definitions = _apply_forced_transaction_tool_definitions(
            tool_definitions,
            getattr(request, "context_override", None),
        )

        tk = self._create_transaction_kernel(role, profile, request)
        turn_id = str(request.run_id or stream_run_id or uuid.uuid4().hex[:12])

        accumulated_content: list[str] = []
        accumulated_thinking: list[str] = []
        receipt_ids: list[str] = []
        tool_call_args: dict[str, dict[str, Any]] = {}
        stream_tool_calls: list[dict[str, Any]] = []
        stream_tool_results: list[dict[str, Any]] = []
        try:
            async for event in tk.execute_stream(turn_id, messages, tool_definitions):
                event_dict: dict[str, Any]
                if isinstance(event, TurnPhaseEvent):
                    event_dict = {
                        "type": event.phase,
                        "turn_id": event.turn_id,
                        "metadata": dict(event.metadata),
                    }
                elif isinstance(event, ContentChunkEvent):
                    if event.is_thinking:
                        accumulated_thinking.append(event.chunk)
                        event_dict = {
                            "type": "thinking_chunk",
                            "content": event.chunk,
                            "turn_id": event.turn_id,
                        }
                    else:
                        if getattr(event, "is_finalization", False):
                            accumulated_content = [event.chunk]
                        else:
                            accumulated_content.append(event.chunk)
                        event_dict = {
                            "type": "content_chunk",
                            "content": event.chunk,
                            "turn_id": event.turn_id,
                        }
                elif isinstance(event, ToolBatchEvent):
                    arguments = dict(event.arguments) if isinstance(event.arguments, dict) else {}
                    if event.status == "started":
                        tool_call_args[event.call_id] = arguments
                        stream_tool_calls.append(
                            {
                                "tool": event.tool_name,
                                "args": arguments,
                                "call_id": event.call_id,
                            }
                        )
                    else:
                        batch_id = str(event.batch_id or "").strip()
                        if batch_id and batch_id not in receipt_ids:
                            receipt_ids.append(batch_id)
                        stream_tool_results.append(
                            {
                                "tool": event.tool_name,
                                "result": event.result,
                                "success": event.status == "success",
                                "call_id": event.call_id,
                            }
                        )
                    event_dict = {
                        "type": "tool_result" if event.status in ("success", "error") else "tool_call",
                        "tool": event.tool_name,
                        "args": arguments if event.status == "started" else tool_call_args.get(event.call_id, {}),
                        "call_id": event.call_id,
                        "status": event.status,
                        "progress": event.progress,
                        "result": event.result,
                        "error": event.error,
                        "turn_id": event.turn_id,
                    }
                elif isinstance(event, FinalizationEvent):
                    continue
                elif isinstance(event, CompletionEvent):
                    event_dict = {
                        "type": "complete",
                        "status": event.status,
                        "content": "".join(accumulated_content),
                        "thinking": "".join(accumulated_thinking),
                        "duration_ms": event.duration_ms,
                        "llm_calls": event.llm_calls,
                        "tool_calls": event.tool_calls,
                        "turn_id": event.turn_id,
                    }
                    if event.monitoring:
                        event_dict["monitoring"] = dict(event.monitoring)
                    event_dict["result"] = self._build_stream_result(
                        request=request,
                        role=role,
                        profile=profile,
                        fingerprint=_fingerprint,
                        turn_id=event.turn_id,
                        status=event.status,
                        content=event_dict["content"],
                        thinking=event_dict["thinking"] or None,
                        tool_calls=stream_tool_calls,
                        tool_results=stream_tool_results,
                        execution_stats={
                            "duration_ms": event.duration_ms,
                            "llm_calls": event.llm_calls,
                            "tool_calls": event.tool_calls,
                            "transaction_kernel": True,
                            "monitoring": dict(event.monitoring) if event.monitoring else {},
                        },
                        batch_receipt=dict(event.batch_receipt)
                        if isinstance(getattr(event, "batch_receipt", None), dict)
                        else None,
                        receipt_ids=receipt_ids,
                        response_model=response_model,
                    )
                    try:
                        adaptive_weights = context_gateway.record_projection_outcome(
                            success=event.status == "success",
                            tokens_used=int(getattr(context_result, "token_estimate", 0) or 0),
                        )
                        result_metadata = dict(getattr(event_dict["result"], "metadata", {}) or {})
                        result_metadata["projection_adaptive_weights_after_turn"] = adaptive_weights
                        event_dict["result"].metadata = result_metadata
                    except (AttributeError, RuntimeError, TypeError, ValueError):
                        logger.debug("Projection outcome feedback failed after stream completion", exc_info=True)
                elif isinstance(event, ErrorEvent):
                    try:
                        context_gateway.record_projection_outcome(
                            success=False,
                            tokens_used=int(getattr(context_result, "token_estimate", 0) or 0),
                        )
                    except (AttributeError, RuntimeError, TypeError, ValueError):
                        logger.debug("Projection outcome feedback failed after stream error", exc_info=True)
                    event_dict = {
                        "type": "error",
                        "error": event.message,
                        "error_type": event.error_type,
                        "turn_id": event.turn_id,
                    }
                else:
                    continue
                yield event_dict
        except Exception as exc:
            logger.exception("TransactionKernel execute_stream failed: turn_id=%s", turn_id)
            try:
                context_gateway.record_projection_outcome(
                    success=False,
                    tokens_used=int(getattr(context_result, "token_estimate", 0) or 0),
                )
            except (AttributeError, RuntimeError, TypeError, ValueError):
                logger.debug("Projection outcome feedback failed after stream exception", exc_info=True)
            yield {"type": "error", "error": f"TransactionKernel stream execution failed: {exc}"}


__all__ = ["TurnEngine"]
