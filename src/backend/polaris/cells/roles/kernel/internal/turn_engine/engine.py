"""TurnEngine - Facade over TransactionKernel.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Slice B (2026-04-16): TurnEngine has been cut over to a pure facade.
- All while-True loops, tool scheduling, and continuation logic removed.
- run() / run_stream() delegate directly to TransactionKernel.execute() / execute_stream().
- Public signatures preserved for backward compatibility.
"""

from __future__ import annotations

import logging
import uuid
import warnings
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel
from polaris.cells.roles.kernel.internal.turn_engine.compat import TurnEngineCompatMixin
from polaris.cells.roles.kernel.internal.turn_engine.turn_materializer import TurnMaterializer
from polaris.cells.roles.kernel.internal.turn_transaction_controller import TransactionConfig
from polaris.cells.roles.profile.public.service import RoleTurnResult
from polaris.kernelone.context.contracts import (
    TurnEngineContextRequest as ContextRequest,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from polaris.cells.roles.profile.public.service import RoleTurnRequest

logger = logging.getLogger(__name__)


class TurnEngine(TurnEngineCompatMixin):
    """Legacy loop engine reduced to a TransactionKernel facade.

    Deprecated compatibility shim. New execution behavior must land in
    TransactionKernel / RoleExecutionKernel, while this facade only preserves
    the minimum legacy result shape still consumed by tests and adapters.
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
        """Initialize facade (legacy collaborators ignored)."""
        self._kernel = kernel
        self._llm_caller = llm_caller if llm_caller is not None else kernel._get_llm_caller()
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
        if status == "handoff":
            metadata["transaction_kind"] = "handoff_workflow"

        # Build turn history and events metadata for ContextOS persistence
        import json

        turn_history: list[tuple[str, str]] = []
        turn_events_metadata: list[dict[str, Any]] = []
        user_message = str(getattr(request, "message", "") or "").strip()
        if user_message:
            turn_history.append(("user", user_message))
            turn_events_metadata.append(
                {
                    "role": "user",
                    "content": user_message,
                    "event_id": f"user_{turn_id}",
                    "kind": "user_turn",
                }
            )
        assistant_content = str(content or "").strip()
        if assistant_content:
            turn_history.append(("assistant", assistant_content))
            turn_events_metadata.append(
                {
                    "role": "assistant",
                    "content": assistant_content,
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

        return RoleTurnResult(
            content=content,
            thinking=thinking,
            structured_output=structured_output,
            tool_calls=list(tool_calls),
            tool_results=list(tool_results),
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

        # Prefer the caller wrapper so context_override-based forced tool scope
        # is honored during contract-retry paths.
        llm_invoker = self._llm_caller
        if not inspect.iscoroutinefunction(getattr(llm_invoker, "call", None)):
            llm_invoker = (
                self._llm_caller._get_invoker() if hasattr(self._llm_caller, "_get_invoker") else self._llm_caller
            )

        async def llm_provider(request_payload: dict[str, Any]) -> dict[str, Any]:
            messages = list(request_payload.get("messages", []))
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

            context = ContextRequest(
                message=getattr(request, "message", "") or "",
                history=tuple(history),
                task_id=request.task_id,
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
                return await self._kernel._execute_single_tool(
                    tool_name=tool_name,
                    args=arguments,
                    context={"profile": profile, "request": request},
                )
            except Exception:
                logger.exception("tool_runtime failed: tool=%s", tool_name)
                raise

        async def llm_provider_stream(request_payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
            if not hasattr(llm_invoker, "call_stream"):
                return
            messages = list(request_payload.get("messages", []))
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

            context = ContextRequest(
                message=getattr(request, "message", "") or "",
                history=tuple(history),
                task_id=request.task_id,
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
                domain="code" if role in {"director", "chief_engineer"} else "document",
            ),
            llm_provider_stream=llm_provider_stream if hasattr(llm_invoker, "call_stream") else None,
        )

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
        from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import build_native_tool_schemas
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
        context_result = await context_gateway.build_context(context_request)
        from polaris.kernelone.context.projection_engine import ProjectionEngine
        from polaris.kernelone.context.receipt_store import ReceiptStore

        projection_dict = {"system_hint": _system_prompt, "turns": list(context_result.messages)}
        messages: list[dict[str, Any]] = ProjectionEngine().project(projection_dict, ReceiptStore())

        tool_definitions = build_native_tool_schemas(profile)

        tk = self._create_transaction_kernel(role, profile, request)
        turn_id = str(request.run_id or uuid.uuid4().hex[:12])

        try:
            tk_result = await tk.execute(turn_id, messages, tool_definitions)
        except Exception as exc:
            logger.exception("TransactionKernel execute failed: turn_id=%s", turn_id)
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
                        "result": result.get("result"),
                        "success": result.get("status") == "success",
                        "call_id": result.get("call_id", ""),
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

        final_thinking = thinking_text
        if final_thinking is None and isinstance(finalization, dict):
            final_thinking = finalization.get("final_visible_message")

        # Build turn history and events metadata for ContextOS persistence
        import json

        turn_history: list[tuple[str, str]] = []
        turn_events_metadata: list[dict[str, Any]] = []
        user_message = str(getattr(request, "message", "") or "").strip()
        if user_message:
            turn_history.append(("user", user_message))
            turn_events_metadata.append(
                {
                    "role": "user",
                    "content": user_message,
                    "event_id": f"user_{turn_id}",
                    "kind": "user_turn",
                }
            )
        assistant_content = str(visible_content or "").strip()
        if assistant_content:
            turn_history.append(("assistant", assistant_content))
            turn_events_metadata.append(
                {
                    "role": "assistant",
                    "content": assistant_content,
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

        return RoleTurnResult(
            content=visible_content,
            thinking=final_thinking,
            structured_output=structured_output,
            tool_calls=tool_calls,
            tool_results=tool_results,
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
        from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import build_native_tool_schemas
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
        context_result = await context_gateway.build_context(context_request)
        from polaris.kernelone.context.projection_engine import ProjectionEngine
        from polaris.kernelone.context.receipt_store import ReceiptStore

        projection_dict = {"system_hint": _system_prompt, "turns": list(context_result.messages)}
        messages: list[dict[str, Any]] = ProjectionEngine().project(projection_dict, ReceiptStore())

        tool_definitions = build_native_tool_schemas(profile)

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
                    event_dict = {
                        "type": "finalization",
                        "mode": event.mode,
                        "turn_id": event.turn_id,
                    }
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
                        receipt_ids=receipt_ids,
                        response_model=response_model,
                    )
                elif isinstance(event, ErrorEvent):
                    event_dict = {
                        "type": "error",
                        "error": event.message,
                        "error_type": event.error_type,
                        "turn_id": event.turn_id,
                    }
                else:
                    event_dict = {"type": "unknown", "turn_id": getattr(event, "turn_id", turn_id)}
                yield event_dict
        except Exception as exc:
            logger.exception("TransactionKernel execute_stream failed: turn_id=%s", turn_id)
            yield {"type": "error", "error": f"TransactionKernel stream execution failed: {exc}"}


__all__ = ["TurnEngine"]
