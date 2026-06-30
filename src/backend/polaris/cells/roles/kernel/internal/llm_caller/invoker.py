"""LLM Invoker Service.

Unified service for LLM invocation that consolidates call, call_structured, and call_stream
functionality from the deprecated standalone modules.

This is the service layer implementation that replaces:
- call_sync.py (call method)
- call_structured.py (call_structured method)
- call_stream.py (call_stream method)

Migration: 2026-03-31
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
import uuid
from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.engine import AIExecutor
from polaris.kernelone.llm.engine._executor_base import coerce_required_flag
from polaris.kernelone.llm.runtime_config import (
    RoleBindingSlot,
    clear_role_provider_override,
    get_role_binding_override,
    get_role_binding_slots,
    get_role_provider_override,
    is_role_binding_healthy,
    mark_role_binding_unhealthy,
    set_role_binding_override,
    set_role_provider_override,
)
from polaris.kernelone.telemetry.debug_stream import emit_debug_event

from ..llm_cache import get_global_llm_cache
from .context_audit import (
    build_final_provider_request_snapshot,
    build_final_request_context_audit_for_request,
    enforce_final_request_evidence_coverage,
)
from .error_handling import (
    ERROR_CATEGORY_CANCELLED,
    build_native_tool_unavailable_error,
    classify_error,
    is_response_format_unsupported,
    is_retryable_error,
)
from .event_emitter import LLMEventEmitter
from .helpers import (
    extract_json_from_text,
    extract_native_tool_calls,
    resolve_tool_call_provider,
)
from .invoker_phases import FallbackLadderResult, read_response_status
from .response_types import LLMResponse, PreparedLLMRequest, StructuredLLMResponse
from .stream_engine import StreamEngine
from .stream_handler import (
    normalize_stream_chunk,  # noqa: F401
    resolve_stream_runtime_config,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
    from polaris.cells.roles.profile.public.service import RoleProfile

logger = logging.getLogger(__name__)


def _with_context_os_audit(metadata: dict[str, Any], prepared: PreparedLLMRequest | None) -> dict[str, Any]:
    payload = dict(metadata)
    audit = getattr(prepared, "context_os_audit", None) if prepared is not None else None
    if isinstance(audit, dict):
        payload["context_os_audit"] = dict(audit)
    return payload


def _with_final_request_context_audit(
    metadata: dict[str, Any],
    *,
    prepared: PreparedLLMRequest,
    active_request: Any,
    profile: Any,
) -> dict[str, Any]:
    payload = dict(metadata)
    audit = build_final_request_context_audit_for_request(
        ai_request=active_request,
        prepared=prepared,
        profile=profile,
    )
    raw_final_tokens = audit.get("final_request_token_estimate")
    final_tokens = int(
        raw_final_tokens
        if raw_final_tokens is not None
        else (prepared.context_result.token_estimate if prepared.context_result else 0)
    )
    payload["final_request_context_audit"] = audit
    payload["context_tokens_after"] = final_tokens
    payload["contextTokens"] = final_tokens
    return payload


def _final_request_context_tokens(metadata: dict[str, Any], fallback: int | None = None) -> int | None:
    raw = metadata.get("contextTokens")
    if raw is None:
        raw = metadata.get("context_tokens_after")
    if raw is None:
        raw = fallback
    if isinstance(raw, bool) or raw is None:
        return fallback
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return fallback


def _with_optional_final_request_context_audit(
    metadata: dict[str, Any],
    *,
    prepared: PreparedLLMRequest | None,
    active_request: Any,
    profile: Any,
) -> dict[str, Any]:
    if prepared is None or active_request is None:
        return dict(metadata)
    return _with_final_request_context_audit(
        metadata,
        prepared=prepared,
        active_request=active_request,
        profile=profile,
    )


def _with_context_snapshot_diagnostics(metadata: dict[str, Any], request: Any) -> dict[str, Any]:
    """Attach context snapshot degradation evidence from AIRequest.context, when present."""
    payload = dict(metadata)
    ctx = getattr(request, "context", None)
    if isinstance(ctx, dict):
        degraded = ctx.get("context_snapshot_degraded")
        if isinstance(degraded, dict):
            payload["context_snapshot_degraded"] = dict(degraded)
            payload["context_snapshot_degraded_reason"] = degraded.get("reason") or degraded.get("code")
    return payload


_CONTEXT_SNAPSHOT_CONTEXT_KEYS = (
    "context_snapshot_ref",
    "contextSnapshotRef",
    "context_snapshot_degraded",
    "contextSnapshotDegraded",
)


def _clear_context_snapshot_context(request: Any) -> dict[str, Any] | None:
    ctx = getattr(request, "context", None)
    if not isinstance(ctx, dict):
        try:
            request.context = {}
        except (AttributeError, TypeError):
            return None
        ctx = getattr(request, "context", None)
    if not isinstance(ctx, dict):
        return None
    for key in _CONTEXT_SNAPSHOT_CONTEXT_KEYS:
        ctx.pop(key, None)
    return ctx


def _context_snapshot_degraded_payload(exc: BaseException) -> dict[str, str]:
    return {
        "code": "CONTEXT_STORE_WRITE_FAILED",
        "reason": "context_snapshot_store_failure",
        "message": str(exc)[:200],
        "exception_type": type(exc).__name__,
    }


async def _store_call_start_context_snapshot(
    *,
    workspace: str | None,
    prepared: PreparedLLMRequest,
    profile: Any,
    run_id: str,
    call_id: str,
) -> None:
    """Persist the final provider messages before call_start emits its ref."""
    request = prepared.ai_request
    ctx = _clear_context_snapshot_context(request)
    messages = list(getattr(prepared, "messages", []) or [])
    if not messages:
        return
    try:
        context_store_hash = await AIExecutor._store_context_messages(
            workspace,
            messages,
            run_id,
            call_id,
            build_final_provider_request_snapshot(
                ai_request=request,
                prepared=prepared,
                profile=profile,
            ),
        )
    except (RuntimeError, ValueError, TypeError, OSError) as exc:
        logger.warning(
            "Failed to store LLM context snapshot before call_start: workspace=%s run_id=%s call_id=%s",
            workspace,
            run_id,
            call_id,
            exc_info=True,
        )
        if isinstance(ctx, dict):
            ctx["context_snapshot_degraded"] = _context_snapshot_degraded_payload(exc)
        return
    if context_store_hash and isinstance(ctx, dict):
        ctx["context_snapshot_ref"] = str(context_store_hash)


def _usage_int(payload: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float) and value.is_integer():
            return max(0, int(value))
        if isinstance(value, str) and value.strip():
            try:
                return max(0, int(float(value.strip())))
            except ValueError:
                continue
    return 0


def _normalize_provider_usage(raw_usage: Any) -> dict[str, Any] | None:
    if raw_usage is None:
        return None
    if hasattr(raw_usage, "to_dict"):
        maybe_payload = raw_usage.to_dict()
    elif isinstance(raw_usage, dict):
        maybe_payload = dict(raw_usage)
    else:
        return None
    if not isinstance(maybe_payload, dict):
        return None

    prompt_tokens = _usage_int(maybe_payload, "prompt_tokens", "promptTokens", "input_tokens", "inputTokens")
    completion_tokens = _usage_int(
        maybe_payload,
        "completion_tokens",
        "completionTokens",
        "output_tokens",
        "outputTokens",
    )
    total_tokens = _usage_int(maybe_payload, "total_tokens", "totalTokens")
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    if prompt_tokens <= 0 and completion_tokens <= 0 and total_tokens <= 0:
        return None

    return {
        "cached_tokens": _usage_int(maybe_payload, "cached_tokens", "cachedTokens"),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "estimated": bool(maybe_payload.get("estimated", False)),
        "prompt_chars": _usage_int(maybe_payload, "prompt_chars", "promptChars"),
        "completion_chars": _usage_int(maybe_payload, "completion_chars", "completionChars"),
    }


def _get_cognitive_runtime_receipt_deps() -> tuple[Any, Any]:
    from polaris.cells.factory.cognitive_runtime.public import (
        RecordRuntimeReceiptCommandV1,
        get_cognitive_runtime_public_service,
    )

    return RecordRuntimeReceiptCommandV1, get_cognitive_runtime_public_service


logger.debug("LLMInvoker module loaded: __name__=%s", __name__)

# Instructor integration
try:
    from polaris.infrastructure.llm.instructor_client import create_structured_client

    INSTRUCTOR_AVAILABLE = True
except ImportError:
    INSTRUCTOR_AVAILABLE = False


class LLMInvoker:
    """Unified LLM invocation service.

    Consolidates functionality previously spread across call_sync.py,
    call_structured.py, and call_stream.py into a single service class.

    This class is designed to be used by LLMCaller (facade) or directly
    for advanced use cases.
    """

    __slots__ = (
        "_cache",
        "_enable_cache",
        "_event_emitter",
        "_executor",
        "_formatter",
        "_model_catalog",
        "_stream_engine",
        "workspace",
    )

    def __init__(
        self,
        workspace: str = "",
        enable_cache: bool = True,
        executor: Any | None = None,
    ) -> None:
        """Initialize the LLM invoker service.

        Args:
            workspace: Workspace path for context
            enable_cache: Whether to enable LLM response caching
            executor: Optional AIExecutor instance for DI (injected, not created inline).
                When provided, tests can inject mocks without patching.
                When None, creates a default AIExecutor instance.
        """
        self.workspace = workspace
        self._enable_cache = enable_cache
        self._cache = None  # Lazy load
        from polaris.kernelone.llm.engine.model_catalog import ModelCatalog

        self._model_catalog = ModelCatalog(workspace=workspace or ".")
        self._formatter: Any = None  # ProviderFormatter for lazy serialization
        self._executor: Any | None = executor  # Injected executor for DI
        self._event_emitter = LLMEventEmitter(workspace=workspace)
        self._stream_engine = StreamEngine(
            workspace=workspace,
            get_executor=lambda: self._get_executor(),
            allow_native_tool_text_fallback_fn=lambda _ctx: False,
            emit_call_start_event=lambda **kwargs: self._emit_call_start_event(**kwargs),
            emit_call_error_event=lambda **kwargs: self._emit_call_error_event(**kwargs),
            emit_call_end_event=lambda **kwargs: self._emit_call_end_event(**kwargs),
            emit_call_retry_event=lambda **kwargs: self._emit_call_retry_event(**kwargs),
            # Phase 1 critical fix: stream the prepared messages through the
            # same context-snapshot store the sync path uses (executor:440).
            # Without this, every streamed invocation emits a call_start with
            # an empty context_snapshot_ref and the per-LLM context viewer
            # never shows data for Director multi-worker streams.
            #
            # Performance hardening (HIGH #2): must be an async coroutine so
            # ``StreamEngine`` can await it and the underlying disk write runs
            # in the thread pool via ``asyncio.to_thread`` instead of blocking
            # the event loop on every streamed LLM call.
            store_context_messages=(
                lambda ws, msgs, trace_id, call_id_value: AIExecutor._store_context_messages(
                    workspace=ws,
                    messages=msgs,
                    trace_id=trace_id,
                    call_id=call_id_value,
                )
            ),
        )

    def set_executor(self, executor: Any) -> None:
        """Set AIExecutor instance (for DI after construction)."""
        self._executor = executor

    def set_formatter(self, formatter: Any) -> None:
        """Set ProviderFormatter for lazy serialization."""
        self._formatter = formatter

    @staticmethod
    def _role_allows_binding_fallback(role_id: str) -> bool:
        return role_id.strip().lower() in {"pm", "chief_engineer", "qa", "architect", "director"}

    @staticmethod
    def _profile_for_binding(profile: RoleProfile, slot: RoleBindingSlot) -> RoleProfile:
        try:
            return replace(profile, provider_id=slot.provider_id, model=slot.model)
        except (TypeError, ValueError):
            clone = SimpleNamespace(**vars(profile))
            clone.provider_id = slot.provider_id
            clone.model = slot.model
            return clone  # type: ignore[return-value]

    @staticmethod
    def _fallback_slots_for_role(role_id: str, profile: RoleProfile) -> tuple[RoleBindingSlot, ...]:
        if not LLMInvoker._role_allows_binding_fallback(role_id):
            return ()
        try:
            slots = get_role_binding_slots(role_id)
        except (RuntimeError, ValueError, TypeError):
            return ()
        current_provider = str(getattr(profile, "provider_id", "") or "").strip()
        current_model = str(getattr(profile, "model", "") or "").strip()
        candidates: list[RoleBindingSlot] = []
        seen: set[tuple[str, str, str]] = set()
        for slot in slots:
            provider_id = str(slot.provider_id or "").strip()
            model = str(slot.model or "").strip()
            if not provider_id or not model:
                continue
            if provider_id == current_provider and model == current_model:
                continue
            key = (provider_id, model, str(slot.binding_id or ""))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(slot)
        return tuple(candidates)

    @staticmethod
    def _profile_uses_healthy_binding(role_id: str, profile: RoleProfile) -> bool:
        binding = get_role_binding_override(role_id)
        provider_id = str((binding or {}).get("provider_id") or getattr(profile, "provider_id", "") or "").strip()
        model = str((binding or {}).get("model") or getattr(profile, "model", "") or "").strip()
        binding_id = str((binding or {}).get("binding_id") or "").strip()
        return is_role_binding_healthy(
            role_id,
            provider_id=provider_id,
            model=model,
            binding_id=binding_id,
        )

    @staticmethod
    def _profile_for_healthy_binding(role_id: str, profile: RoleProfile) -> RoleProfile:
        binding = get_role_binding_override(role_id)
        if binding:
            provider_id = str(binding.get("provider_id") or "").strip()
            model = str(binding.get("model") or "").strip()
            binding_id = str(binding.get("binding_id") or "").strip()
            fanout_locked = str(binding.get("_fanout_locked") or "").strip().lower() == "true"
            if provider_id and model:
                slot = RoleBindingSlot(
                    role_id=role_id,
                    provider_id=provider_id,
                    model=model,
                    binding_id=binding_id,
                )
                if fanout_locked:
                    return LLMInvoker._profile_for_binding(profile, slot)
                if is_role_binding_healthy(
                    role_id,
                    provider_id=provider_id,
                    model=model,
                    binding_id=binding_id,
                ):
                    return LLMInvoker._profile_for_binding(profile, slot)

        if LLMInvoker._profile_uses_healthy_binding(role_id, profile):
            return profile
        for slot in get_role_binding_slots(role_id):
            if is_role_binding_healthy(
                role_id,
                provider_id=slot.provider_id,
                model=slot.model,
                binding_id=slot.binding_id,
            ):
                return LLMInvoker._profile_for_binding(profile, slot)
        return profile

    @staticmethod
    def _mark_profile_binding_unhealthy(role_id: str, profile: RoleProfile) -> None:
        binding = get_role_binding_override(role_id)
        provider_id = str((binding or {}).get("provider_id") or getattr(profile, "provider_id", "") or "").strip()
        model = str((binding or {}).get("model") or getattr(profile, "model", "") or "").strip()
        binding_id = str((binding or {}).get("binding_id") or "").strip()
        mark_role_binding_unhealthy(
            role_id,
            provider_id=provider_id,
            model=model,
            binding_id=binding_id or None,
        )

    @staticmethod
    def _restore_role_binding_override(
        role_id: str,
        previous_binding: dict[str, str] | None,
        previous_provider: str | None,
    ) -> None:
        if previous_binding:
            set_role_binding_override(
                role_id,
                provider_id=str(previous_binding.get("provider_id") or ""),
                model=str(previous_binding.get("model") or ""),
                binding_id=str(previous_binding.get("binding_id") or "") or None,
            )
            return
        if previous_provider:
            set_role_provider_override(role_id, previous_provider)
            return
        clear_role_provider_override(role_id)

    async def _try_role_binding_fallback(
        self,
        *,
        caller: Any,
        profile: RoleProfile,
        system_prompt: str,
        context: ContextRequest,
        temperature: float,
        max_tokens: int,
        response_model: type | None,
        platform_retry_max: int,
        executor: Any,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        model: str,
        call_id: str,
        event_emitter: Any | None,
        original_error: str,
    ) -> tuple[RoleProfile, PreparedLLMRequest, Any, Any] | None:
        self._mark_profile_binding_unhealthy(role_id, profile)
        fallback_slots = self._fallback_slots_for_role(role_id, profile)
        if not fallback_slots:
            return None

        for slot in fallback_slots:
            previous_binding = get_role_binding_override(role_id)
            previous_provider = get_role_provider_override(role_id)
            set_role_binding_override(
                role_id,
                provider_id=slot.provider_id,
                model=slot.model,
                binding_id=slot.binding_id or None,
            )
            fallback_profile = self._profile_for_binding(profile, slot)
            try:
                self._emit_call_retry_event(
                    event_emitter=event_emitter,
                    role=role_id,
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    model=model,
                    provider=slot.provider_id,
                    call_id=call_id,
                    retry_decision="role_binding_fallback",
                    backoff_seconds=0.0,
                    metadata={
                        "from_provider": str(getattr(profile, "provider_id", "") or ""),
                        "from_model": str(getattr(profile, "model", "") or ""),
                        "to_provider": slot.provider_id,
                        "to_model": slot.model,
                        "binding_id": slot.binding_id,
                        "trigger_error_category": classify_error(original_error),
                    },
                )
                fallback_prepared = await caller._prepare_llm_request(
                    profile=fallback_profile,
                    system_prompt=system_prompt,
                    context=context,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=False,
                    response_model=response_model,
                    platform_retry_max=platform_retry_max,
                )
                fallback_audit_metadata = _with_final_request_context_audit(
                    {
                        "fallback_request": True,
                        "retry_decision": "role_binding_fallback_request",
                        "from_provider": str(getattr(profile, "provider_id", "") or ""),
                        "from_model": str(getattr(profile, "model", "") or ""),
                        "to_provider": slot.provider_id,
                        "to_model": slot.model,
                        "binding_id": slot.binding_id,
                        "context_snapshot_ref": self._extract_context_snapshot_ref(fallback_prepared.ai_request),
                    },
                    prepared=fallback_prepared,
                    active_request=fallback_prepared.ai_request,
                    profile=fallback_profile,
                )
                self._emit_call_retry_event(
                    event_emitter=event_emitter,
                    role=role_id,
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    model=slot.model,
                    provider=slot.provider_id,
                    call_id=call_id,
                    retry_decision="role_binding_fallback_request",
                    backoff_seconds=0.0,
                    metadata=fallback_audit_metadata,
                )
                fallback_response = await executor.invoke(fallback_prepared.ai_request)
            except (RuntimeError, TypeError, ValueError) as exc:
                fallback_error = str(exc)
            else:
                fallback_ok = getattr(fallback_response, "ok", True)
                raw_error = getattr(fallback_response, "error", None)
                fallback_error = str(raw_error or "").strip() if raw_error is not None else ""
                if (bool(fallback_ok) if isinstance(fallback_ok, bool) else True) and not fallback_error:
                    return (
                        fallback_profile,
                        fallback_prepared,
                        fallback_prepared.ai_request,
                        fallback_response,
                    )
            finally:
                self._restore_role_binding_override(role_id, previous_binding, previous_provider)

            fallback_category = classify_error(fallback_error)
            if is_retryable_error(fallback_category):
                mark_role_binding_unhealthy(
                    role_id,
                    provider_id=slot.provider_id,
                    model=slot.model,
                    binding_id=slot.binding_id or None,
                )
            else:
                return None

        return None

    def _get_executor(self) -> Any:
        """Get or create AIExecutor instance (lazy, respects DI injection)."""
        if self._executor is not None:
            return self._executor
        return AIExecutor(
            workspace=self.workspace,
            final_request_receipt_sink=self._record_final_request_receipt,
        )

    def _record_final_request_receipt(self, receipt: dict[str, Any]) -> None:
        if not isinstance(receipt, dict):
            return
        raw_payload = receipt.get("payload")
        payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
        receipt_required = coerce_required_flag(payload.get("cognitive_runtime_required")) or coerce_required_flag(
            payload.get("context_os_expected")
        )
        receipt_type = str(receipt.get("receipt_type") or "contextos.final_request").strip()
        if not receipt_type:
            receipt_type = "contextos.final_request"

        trace_refs = self._normalize_trace_refs(receipt.get("trace_refs"), payload.get("trace_id"))
        try:
            command_cls, get_service = _get_cognitive_runtime_receipt_deps()
            result = get_service().record_runtime_receipt(
                command_cls(
                    workspace=self.workspace or ".",
                    receipt_type=receipt_type,
                    payload=payload,
                    session_id=self._optional_str(payload.get("session_id")),
                    run_id=self._optional_str(payload.get("run_id")),
                    trace_refs=trace_refs,
                    turn_envelope={
                        "source": "roles.kernel.llm_invoker",
                        "receipt_type": receipt_type,
                        "trace_id": self._optional_str(payload.get("trace_id")),
                        "task_id": self._optional_str(payload.get("task_id")),
                        "role": self._optional_str(payload.get("role")),
                    },
                )
            )
        except Exception as exc:
            logger.warning("[LLMInvoker] contextos final request receipt failed: %s", exc)
            if receipt_required:
                raise RuntimeError("contextos final request receipt failed in required mode") from exc
            return
        if not bool(getattr(result, "ok", False)):
            message = str(getattr(result, "error_message", "") or getattr(result, "error_code", "") or "").strip()
            logger.warning("[LLMInvoker] contextos final request receipt rejected: %s", message)
            if receipt_required:
                raise RuntimeError(message or "contextos final request receipt rejected in required mode")

    @staticmethod
    def _normalize_trace_refs(raw_refs: Any, fallback_trace_id: Any = None) -> tuple[str, ...]:
        refs: list[str] = []
        if isinstance(raw_refs, (list, tuple, set, frozenset)):
            refs.extend(str(item).strip() for item in raw_refs if str(item or "").strip())
        else:
            text = str(raw_refs or "").strip()
            if text:
                refs.append(text)
        fallback = str(fallback_trace_id or "").strip()
        if fallback and fallback not in refs:
            refs.append(fallback)
        return tuple(refs)

    @staticmethod
    def _optional_str(value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _extract_context_snapshot_ref(request: Any) -> str | None:
        """Extract context_snapshot_ref from an AIRequest's context dict, if present."""
        ctx = getattr(request, "context", None)
        if isinstance(ctx, dict):
            ref = ctx.get("context_snapshot_ref")
            if isinstance(ref, str) and ref.strip():
                return ref.strip()
        return None

    def _build_call_error_response(
        self,
        *,
        prepared: PreparedLLMRequest,
        active_request: Any,
        response_error: str,
        profile: RoleProfile,
        native_tool_fallback: bool,
        native_response_fallback: bool,
        allow_native_tool_text_fallback: bool,
        model: str,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        call_id: str,
        event_emitter: Any | None,
        start_time: float,
    ) -> LLMResponse:
        """Emit the call_error event and build the failure ``LLMResponse``."""
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        classified = classify_error(response_error)
        active_context = active_request.context if isinstance(active_request.context, dict) else {}
        event_metadata = _with_final_request_context_audit(
            {
                "native_tool_calling_fallback": native_tool_fallback,
                "native_response_format_fallback": native_response_fallback,
                "native_tool_mode": str(active_context.get("native_tool_mode") or prepared.native_tool_mode),
                "response_format_mode": str(
                    active_context.get("response_format_mode") or prepared.response_format_mode
                ),
                "native_tool_text_fallback_allowed": allow_native_tool_text_fallback,
                "context_snapshot_ref": self._extract_context_snapshot_ref(active_request),
            },
            prepared=prepared,
            active_request=active_request,
            profile=profile,
        )
        self._emit_call_error_event(
            event_emitter=event_emitter,
            role=role_id,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            model=model,
            error_category=classified,
            error_message=response_error or "LLM call failed",
            call_id=call_id,
            elapsed_ms=elapsed_ms,
            metadata=_with_context_os_audit(event_metadata, prepared),
        )
        return LLMResponse(
            content="",
            error=response_error or "LLM call failed",
            error_category=classified,
            metadata=_with_context_os_audit(
                _with_final_request_context_audit(
                    {
                        "model": model,
                        "elapsed_ms": round(elapsed_ms, 2),
                        "native_tool_calling_fallback": native_tool_fallback,
                        "native_response_format_fallback": native_response_fallback,
                        "native_tool_text_fallback_allowed": allow_native_tool_text_fallback,
                        "run_id": run_id,
                        "workspace": self.workspace,
                        "attempt": attempt,
                        "context_snapshot_ref": self._extract_context_snapshot_ref(active_request),
                    },
                    prepared=prepared,
                    active_request=active_request,
                    profile=profile,
                ),
                prepared,
            ),
        )

    def _finalize_call_response(
        self,
        *,
        cache: Any,
        prepared: PreparedLLMRequest,
        active_request: Any,
        response: Any,
        cache_eligible: bool,
        prompt_fingerprint: str | None,
        temperature: float,
        model: str,
        profile: RoleProfile,
        prompt_tokens: int,
        turn_round: int,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        call_id: str,
        event_emitter: Any | None,
        start_time: float,
    ) -> LLMResponse:
        """Normalize a successful response, store cache, emit end, and build result."""
        raw_payload = response.raw if isinstance(response.raw, dict) else {}
        # DEBUG: Log raw LLM response for debugging parsing issues
        output_text = str(getattr(response, "output", "") or "")
        logger.debug(
            "[LLMInvoker] Raw LLM response received: model=%s provider=%s output_length=%d output_preview=%r",
            raw_payload.get("model", "unknown"),
            raw_payload.get("provider", "unknown"),
            len(output_text),
            output_text[:500] if output_text else "<empty>",
        )
        response_text = output_text
        if not response_text.strip() and raw_payload:
            try:
                from polaris.kernelone.llm.engine import ResponseNormalizer

                response_text = ResponseNormalizer.extract_text(raw_payload)
            except (RuntimeError, ValueError):
                response_text = str(getattr(response, "output", "") or "")

        response_model_name = str((getattr(response, "model", None) or raw_payload.get("model") or model) or "")
        response_provider = str(
            (
                getattr(response, "provider_id", None)
                or raw_payload.get("provider_id")
                or raw_payload.get("provider")
                or ""
            )
            or ""
        )
        native_tool_calls, native_tool_provider = extract_native_tool_calls(
            raw_payload, provider_id=response_provider, model=response_model_name, response_text=response_text
        )

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        provider_usage = _normalize_provider_usage(getattr(response, "usage", None)) or _normalize_provider_usage(
            raw_payload.get("usage")
        )
        estimated_completion_tokens = len(response_text) // 2
        event_prompt_tokens = int(provider_usage["prompt_tokens"]) if provider_usage else prompt_tokens
        completion_tokens = (
            int(provider_usage["completion_tokens"])
            if provider_usage and int(provider_usage["completion_tokens"]) > 0
            else estimated_completion_tokens
        )
        total_token_estimate = (
            int(provider_usage["total_tokens"])
            if provider_usage and int(provider_usage["total_tokens"]) > 0
            else (
                prepared.context_result.token_estimate + completion_tokens
                if prepared.context_result
                else completion_tokens
            )
        )

        self._store_cache(
            cache=cache,
            prepared=prepared,
            cache_eligible=cache_eligible,
            prompt_fingerprint=prompt_fingerprint,
            temperature=temperature,
            model=model,
            response_text=response_text,
            completion_tokens=completion_tokens,
        )

        event_metadata: dict[str, Any] = {
            "elapsed_ms": round(elapsed_ms, 2),
            "cached": False,
            "source": "llm",
            "compression_applied": prepared.context_result.compression_applied if prepared.context_result else False,
            "turn_round": turn_round,
            "context_snapshot_ref": self._extract_context_snapshot_ref(active_request),
        }
        event_metadata = _with_context_snapshot_diagnostics(event_metadata, active_request)
        if provider_usage is not None:
            event_metadata["usage"] = provider_usage
            event_metadata["usage_source"] = "provider"
        event_metadata = _with_final_request_context_audit(
            event_metadata,
            prepared=prepared,
            active_request=active_request,
            profile=profile,
        )
        final_context_tokens = _final_request_context_tokens(
            event_metadata,
            prepared.context_result.token_estimate if prepared.context_result else None,
        )

        self._emit_call_end_event(
            event_emitter=event_emitter,
            role=role_id,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            model=response_model_name,
            provider=response_provider,
            prompt_tokens=event_prompt_tokens,
            completion_tokens=completion_tokens,
            call_id=call_id,
            context_tokens_after=final_context_tokens,
            compression_strategy=prepared.context_result.compression_strategy if prepared.context_result else None,
            response_content=response_text,
            tool_calls_count=len(native_tool_calls),
            metadata=_with_context_os_audit(event_metadata, prepared),
        )

        response_metadata: dict[str, Any] = {
            "model": response_model_name,
            "provider": response_provider,
            "native_tool_calls_count": len(native_tool_calls),
            "elapsed_ms": round(elapsed_ms, 2),
            "run_id": run_id,
            "workspace": self.workspace,
            "attempt": attempt,
            "turn_round": turn_round,
            # SSOT Fix: Pass context token count for context panel display
            "context_tokens": int(final_context_tokens or 0),
            "context_snapshot_ref": self._extract_context_snapshot_ref(active_request),
        }
        response_metadata = _with_context_snapshot_diagnostics(response_metadata, active_request)
        if provider_usage is not None:
            response_metadata["usage"] = provider_usage
            response_metadata["usage_source"] = "provider"
        response_metadata = _with_final_request_context_audit(
            response_metadata,
            prepared=prepared,
            active_request=active_request,
            profile=profile,
        )

        return LLMResponse(
            content=response_text,
            token_estimate=total_token_estimate,
            tool_calls=native_tool_calls,
            tool_call_provider=native_tool_provider,
            metadata=_with_context_os_audit(
                response_metadata,
                prepared,
            ),
        )

    async def _run_fallback_ladder(
        self,
        *,
        caller: Any,
        executor: Any,
        prepared: PreparedLLMRequest,
        profile: RoleProfile,
        context: ContextRequest,
        response: Any,
        active_request: Any,
        response_model: type | None,
        response_error: str,
        is_response_ok: bool,
        allow_native_tool_text_fallback: bool,
        native_tool_fallback: bool,
        native_response_fallback: bool,
        system_prompt: str,
        temperature: float,
        effective_max_tokens: int,
        platform_retry_max: int,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        model: str,
        call_id: str,
        event_emitter: Any | None,
    ) -> FallbackLadderResult:
        """Run the call-phase fallback ladder after the primary invoke.

        Three rungs in fixed order: response_format fallback,
        reasoning-truncation re-ask, and role-binding fallback. Returns a
        :class:`FallbackLadderResult` carrying the mutated state so the
        orchestrator can repoint its locals.
        """

        def emit_fallback_request_audit(retry_decision: str, request: Any, request_profile: RoleProfile) -> None:
            audit_metadata = _with_final_request_context_audit(
                {
                    "fallback_request": True,
                    "retry_decision": retry_decision,
                    "context_snapshot_ref": self._extract_context_snapshot_ref(request),
                },
                prepared=prepared,
                active_request=request,
                profile=request_profile,
            )
            self._emit_call_retry_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                provider=str(getattr(request_profile, "provider_id", "") or ""),
                call_id=call_id,
                retry_decision=retry_decision,
                backoff_seconds=0.0,
                metadata=audit_metadata,
            )

        _ = allow_native_tool_text_fallback
        if not is_response_ok and prepared.native_response_format and is_response_format_unsupported(response_error):
            active_request = caller._build_structured_fallback_request(
                prepared=prepared, profile=profile, response_model=response_model or dict, mode="chat"
            )
            emit_fallback_request_audit("response_format_text_fallback", active_request, profile)
            response = await executor.invoke(active_request)
            native_response_fallback = True
            is_response_ok, response_error = read_response_status(response)

        # 5th floor: a reasoning model truncated mid-thought (finish_reason=length)
        # and emitted no visible output / tool call. Re-ask ONCE with a reserved
        # output budget + a "minimal reasoning, emit the tool call now" directive so
        # the write actually lands instead of dying as director_no_materialized_changes.
        response_error_lower = response_error.lower()
        _is_reasoning_truncation = "empty visible output" in response_error_lower and (
            "reasoning truncated" in response_error_lower
            or "reasoning exhausted" in response_error_lower
            or "finish_reason=length" in response_error_lower
        )
        if not is_response_ok and _is_reasoning_truncation:
            active_request = caller._build_reasoning_truncation_retry_request(prepared=prepared, profile=profile)
            emit_fallback_request_audit("reasoning_truncation_retry", active_request, profile)
            response = await executor.invoke(active_request)
            logger.warning(
                "[invoker] reasoning-truncation re-ask: reserved output budget + minimal-reasoning directive"
            )
            is_response_ok, response_error = read_response_status(response)

        if not is_response_ok:
            classified = classify_error(response_error)
            if is_retryable_error(classified):
                fallback = await self._try_role_binding_fallback(
                    caller=caller,
                    profile=profile,
                    system_prompt=system_prompt,
                    context=context,
                    temperature=temperature,
                    max_tokens=effective_max_tokens,
                    response_model=response_model,
                    platform_retry_max=platform_retry_max,
                    executor=executor,
                    role_id=role_id,
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    model=model,
                    call_id=call_id,
                    event_emitter=event_emitter,
                    original_error=response_error,
                )
                if fallback is not None:
                    profile, prepared, active_request, response = fallback
                    model = str(getattr(profile, "model", "") or model)
                    native_tool_fallback = False
                    native_response_fallback = False
                    is_response_ok, response_error = read_response_status(response)

        return FallbackLadderResult(
            response=response,
            active_request=active_request,
            profile=profile,
            prepared=prepared,
            model=model,
            native_tool_fallback=native_tool_fallback,
            native_response_fallback=native_response_fallback,
            is_response_ok=is_response_ok,
            response_error=response_error,
        )

    def _try_cache_hit(
        self,
        *,
        cache: Any,
        prepared: PreparedLLMRequest,
        context_result: Any,
        cache_eligible: bool,
        prompt_fingerprint: str | None,
        temperature: float,
        model: str,
        profile: RoleProfile,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        turn_round: int,
        call_id: str,
        event_emitter: Any | None,
        start_time: float,
    ) -> LLMResponse | None:
        """Return a cached ``LLMResponse`` on cache hit, else ``None`` to continue.

        Falls through (returns ``None``) when caching is disabled, no
        fingerprint is supplied, the turn is not cache-eligible, or the cache
        lookup misses.
        """
        if not (self._enable_cache and prompt_fingerprint and cache_eligible):
            return None
        cached = cache.get(
            prompt_fingerprint=prompt_fingerprint,
            context_summary=prepared.context_summary,
            temperature=temperature,
            model=model,
        )
        if not cached:
            return None
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.debug(
            "[LLMInvoker] Cache hit, returning cached response: model=%s length=%d",
            model,
            len(cached),
        )
        cache_metadata = _with_final_request_context_audit(
            {
                "elapsed_ms": round(elapsed_ms, 2),
                "cached": True,
                "source": "cache",
                "compression_applied": context_result.compression_applied if context_result else False,
                "turn_round": turn_round,
                "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request),
            },
            prepared=prepared,
            active_request=prepared.ai_request,
            profile=profile,
        )
        final_context_tokens = _final_request_context_tokens(
            cache_metadata,
            context_result.token_estimate if context_result else None,
        )
        self._emit_call_end_event(
            event_emitter=event_emitter,
            role=role_id,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            model=model,
            call_id=call_id,
            completion_tokens=len(cached) // 2,
            context_tokens_after=final_context_tokens,
            compression_strategy=context_result.compression_strategy if context_result else None,
            response_content=cached,
            metadata=_with_context_os_audit(cache_metadata, prepared),
        )
        return LLMResponse(
            content=cached,
            token_estimate=len(cached) // 2,
            metadata=_with_context_os_audit(
                {
                    "cached": True,
                    "model": model,
                    "elapsed_ms": round(elapsed_ms, 2),
                    "run_id": run_id,
                    "workspace": self.workspace,
                    "attempt": attempt,
                    "turn_round": turn_round,
                    "native_tool_mode": prepared.native_tool_mode,
                    "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request),
                },
                prepared,
            ),
        )

    def _store_cache(
        self,
        *,
        cache: Any,
        prepared: PreparedLLMRequest,
        cache_eligible: bool,
        prompt_fingerprint: str | None,
        temperature: float,
        model: str,
        response_text: str,
        completion_tokens: int,
    ) -> None:
        """Persist a successful response to the LLM cache when eligible.

        Re-checks the SAME eligibility flags used by :meth:`_try_cache_hit` so
        get/put stay consistent.
        """
        if self._enable_cache and prompt_fingerprint and cache_eligible:
            cache.put(
                prompt_fingerprint=prompt_fingerprint,
                context_summary=prepared.context_summary,
                temperature=temperature,
                model=model,
                response_content=response_text,
                token_estimate=completion_tokens,
            )

    async def _handle_native_tools_unavailable(
        self,
        *,
        caller: Any,
        prepared: PreparedLLMRequest,
        profile: RoleProfile,
        context: ContextRequest,
        model: str,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        call_id: str,
        event_emitter: Any | None,
        start_time: float,
    ) -> LLMResponse:
        """Handle ``native_tool_mode == 'native_tools_unavailable'`` (call phase).

        Emits a call_error event and returns the ``native_tool_unavailable``
        error response. Text-mode tool fallback is retired; provider-native
        tool schemas are required.
        """
        _ = (caller, context)
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        tool_error = build_native_tool_unavailable_error(profile)
        error_metadata = _with_final_request_context_audit(
            {
                "native_tool_mode": prepared.native_tool_mode,
                "response_format_mode": prepared.response_format_mode,
                "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request),
            },
            prepared=prepared,
            active_request=prepared.ai_request,
            profile=profile,
        )
        self._emit_call_error_event(
            event_emitter=event_emitter,
            role=role_id,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            model=model,
            error_category="provider",
            error_message=tool_error,
            call_id=call_id,
            elapsed_ms=elapsed_ms,
            metadata=_with_context_os_audit(error_metadata, prepared),
        )
        response_metadata = _with_final_request_context_audit(
            {
                "model": model,
                "native_tool_mode": prepared.native_tool_mode,
                "response_format_mode": prepared.response_format_mode,
                "run_id": run_id,
                "workspace": self.workspace,
                "attempt": attempt,
                "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request),
            },
            prepared=prepared,
            active_request=prepared.ai_request,
            profile=profile,
        )
        return LLMResponse(
            content="",
            error=tool_error,
            error_category="provider",
            metadata=_with_context_os_audit(response_metadata, prepared),
        )

    async def call(
        self,
        profile: RoleProfile,
        system_prompt: str,
        context: ContextRequest,
        response_model: type | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        prompt_fingerprint: str | None = None,
        platform_retry_max: int = 1,
        run_id: str | None = None,
        task_id: str | None = None,
        attempt: int = 0,
        turn_round: int = 0,
        event_emitter: Any | None = None,
    ) -> LLMResponse:
        """Invoke LLM with non-streaming mode."""
        logger.warning("[LLMInvoker.call] ENTRY: profile=%s run_id=%s", getattr(profile, "role_id", "unknown"), run_id)
        call_id = str(uuid.uuid4())[:8]
        run_id = run_id or f"llm_{call_id}"
        task_id = task_id or getattr(context, "task_id", None)
        role_id = str(getattr(profile, "role_id", "unknown") or "unknown")
        model = profile.model or "default"
        from .helpers import resolve_max_tokens, resolve_temperature

        context_override = getattr(context, "context_override", None)
        effective_max_tokens = resolve_max_tokens(max_tokens, context_override)
        effective_temperature = resolve_temperature(temperature, context_override)

        start_time = time.perf_counter()
        prepared: PreparedLLMRequest | None = None
        active_request: Any | None = None

        try:
            # Import here to avoid circular dependency
            from .caller import LLMCaller

            profile = self._profile_for_healthy_binding(role_id, profile)
            model = str(getattr(profile, "model", "") or model)
            caller = LLMCaller(
                workspace=self.workspace,
                enable_cache=self._enable_cache,
                executor=self._executor,
                emit_deprecation_warning=False,
            )
            caller._model_catalog = self._model_catalog
            caller._formatter = self._formatter

            prepared = await caller._prepare_llm_request(
                profile=profile,
                system_prompt=system_prompt,
                context=context,
                temperature=effective_temperature,
                max_tokens=effective_max_tokens,
                stream=False,
                response_model=response_model,
                platform_retry_max=platform_retry_max,
            )
            active_request = prepared.ai_request
            await _store_call_start_context_snapshot(
                workspace=self.workspace,
                prepared=prepared,
                profile=profile,
                run_id=run_id,
                call_id=call_id,
            )
            context_result = prepared.context_result
            prompt_tokens = context_result.token_estimate if context_result else len(system_prompt) // 4
            final_context_audit = build_final_request_context_audit_for_request(
                ai_request=prepared.ai_request,
                prepared=prepared,
                profile=profile,
            )
            raw_final_context_tokens = final_context_audit.get("final_request_token_estimate")
            final_context_tokens = int(
                raw_final_context_tokens if raw_final_context_tokens is not None else prompt_tokens
            )
            enforce_final_request_evidence_coverage(
                ai_request=prepared.ai_request,
                audit=final_context_audit,
            )

            self._emit_call_start_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                provider=str(getattr(profile, "provider_id", "") or ""),
                prompt_tokens=prompt_tokens,
                call_id=call_id,
                context_tokens_before=final_context_tokens,
                compression_strategy=context_result.compression_strategy if context_result else None,
                messages=prepared.messages,
                metadata=_with_context_os_audit(
                    _with_context_snapshot_diagnostics(
                        {
                            "temperature": effective_temperature,
                            "max_tokens": effective_max_tokens,
                            "prompt_fingerprint": prompt_fingerprint,
                            "native_tool_mode": prepared.native_tool_mode,
                            "response_format_mode": prepared.response_format_mode,
                            "compression_applied": context_result.compression_applied if context_result else False,
                            "turn_round": turn_round,
                            "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request),
                            "context_tokens_after": final_context_tokens,
                            "contextTokens": final_context_tokens,
                            "final_request_context_audit": final_context_audit,
                        },
                        prepared.ai_request,
                    ),
                    prepared,
                ),
            )

            cache_eligible = self._is_cache_eligible(prepared=prepared, response_model=response_model)

            if prepared.native_tool_mode == "native_tools_unavailable":
                return await self._handle_native_tools_unavailable(
                    caller=caller,
                    prepared=prepared,
                    profile=profile,
                    context=context,
                    model=model,
                    role_id=role_id,
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    call_id=call_id,
                    event_emitter=event_emitter,
                    start_time=start_time,
                )

            cache = get_global_llm_cache()
            cache_hit = self._try_cache_hit(
                cache=cache,
                prepared=prepared,
                context_result=context_result,
                cache_eligible=cache_eligible,
                prompt_fingerprint=prompt_fingerprint,
                temperature=effective_temperature,
                model=model,
                profile=profile,
                role_id=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                turn_round=turn_round,
                call_id=call_id,
                event_emitter=event_emitter,
                start_time=start_time,
            )
            if cache_hit is not None:
                return cache_hit

            executor = self._get_executor()
            native_tool_fallback = False
            allow_native_tool_text_fallback = False
            response = await executor.invoke(active_request)
            native_response_fallback = False

            is_response_ok, response_error = read_response_status(response)

            ladder = await self._run_fallback_ladder(
                caller=caller,
                executor=executor,
                prepared=prepared,
                profile=profile,
                context=context,
                response=response,
                active_request=active_request,
                response_model=response_model,
                response_error=response_error,
                is_response_ok=is_response_ok,
                allow_native_tool_text_fallback=allow_native_tool_text_fallback,
                native_tool_fallback=native_tool_fallback,
                native_response_fallback=native_response_fallback,
                system_prompt=system_prompt,
                temperature=effective_temperature,
                effective_max_tokens=effective_max_tokens,
                platform_retry_max=platform_retry_max,
                role_id=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                call_id=call_id,
                event_emitter=event_emitter,
            )
            response = ladder.response
            active_request = ladder.active_request
            profile = ladder.profile
            prepared = ladder.prepared
            model = ladder.model
            native_tool_fallback = ladder.native_tool_fallback
            native_response_fallback = ladder.native_response_fallback
            is_response_ok = ladder.is_response_ok
            response_error = ladder.response_error

            if not is_response_ok:
                return self._build_call_error_response(
                    prepared=prepared,
                    active_request=active_request,
                    response_error=response_error,
                    profile=profile,
                    native_tool_fallback=native_tool_fallback,
                    native_response_fallback=native_response_fallback,
                    allow_native_tool_text_fallback=allow_native_tool_text_fallback,
                    model=model,
                    role_id=role_id,
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    call_id=call_id,
                    event_emitter=event_emitter,
                    start_time=start_time,
                )

            return self._finalize_call_response(
                cache=cache,
                prepared=prepared,
                active_request=active_request,
                response=response,
                cache_eligible=cache_eligible,
                prompt_fingerprint=prompt_fingerprint,
                temperature=effective_temperature,
                model=model,
                profile=profile,
                prompt_tokens=prompt_tokens,
                turn_round=turn_round,
                role_id=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                call_id=call_id,
                event_emitter=event_emitter,
                start_time=start_time,
            )

        except asyncio.CancelledError:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self._emit_call_error_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                error_category=ERROR_CATEGORY_CANCELLED,
                error_message="call_cancelled",
                call_id=call_id,
                elapsed_ms=elapsed_ms,
                metadata=_with_context_os_audit(
                    _with_optional_final_request_context_audit(
                        {
                            "error_type": "CancelledError",
                            "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request)
                            if prepared
                            else None,
                        },
                        prepared=prepared,
                        active_request=active_request or (prepared.ai_request if prepared else None),
                        profile=profile,
                    ),
                    prepared,
                ),
            )
            raise

        except (ImportError, AttributeError, TypeError, ValueError) as e:
            logger.error(f"LLM call failed: {e}")
            return self._call_exception_response(
                e,
                prepared=prepared,
                active_request=active_request or (prepared.ai_request if prepared else None),
                profile=profile,
                model=model,
                role_id=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                call_id=call_id,
                event_emitter=event_emitter,
                start_time=start_time,
            )

        except RuntimeError as e:
            logger.exception(f"LLM call unexpected error: {e}")
            return self._call_exception_response(
                e,
                prepared=prepared,
                active_request=active_request or (prepared.ai_request if prepared else None),
                profile=profile,
                model=model,
                role_id=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                call_id=call_id,
                event_emitter=event_emitter,
                start_time=start_time,
            )

    def _call_exception_response(
        self,
        exc: BaseException,
        *,
        prepared: PreparedLLMRequest | None,
        active_request: Any,
        profile: RoleProfile,
        model: str,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        call_id: str,
        event_emitter: Any | None,
        start_time: float,
    ) -> LLMResponse:
        """Shared builder for the call-phase return-except arms (NOT CancelledError).

        Emits the call_error event and returns the failure ``LLMResponse`` with
        byte-identical metadata. The caller is responsible for the distinct log
        severity (``logger.error`` vs ``logger.exception``) before invoking this.
        """
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        error_category = classify_error(str(exc))
        self._emit_call_error_event(
            event_emitter=event_emitter,
            role=role_id,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            model=model,
            error_category=error_category,
            error_message=str(exc),
            call_id=call_id,
            elapsed_ms=elapsed_ms,
            metadata=_with_context_os_audit(
                _with_optional_final_request_context_audit(
                    {
                        "error_type": type(exc).__name__,
                        "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request)
                        if prepared
                        else None,
                    },
                    prepared=prepared,
                    active_request=active_request,
                    profile=profile,
                ),
                prepared,
            ),
        )
        return LLMResponse(
            content="",
            error=f"LLM call failed: {exc}",
            error_category=error_category,
            metadata=_with_context_os_audit(
                _with_optional_final_request_context_audit(
                    {
                        "run_id": run_id,
                        "workspace": self.workspace,
                        "attempt": attempt,
                        "elapsed_ms": round(elapsed_ms, 2),
                    },
                    prepared=prepared,
                    active_request=active_request,
                    profile=profile,
                ),
                prepared,
            ),
        )

    # ========================================================================
    # Structured call (migrated from call_structured.py)
    # ========================================================================

    async def _try_native_response_format_structured(
        self,
        *,
        caller: Any,
        prepared: PreparedLLMRequest,
        profile: RoleProfile,
        response_model: type,
        model: str,
        prompt_tokens: int,
        turn_round: int,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        call_id: str,
        event_emitter: Any | None,
        start_time: float,
    ) -> StructuredLLMResponse | None:
        """Structured strategy 1: native response_format.

        Returns a ``StructuredLLMResponse`` on success, or ``None`` to fall
        through to the next strategy. A non-``response_format_unsupported`` error
        is raised as ``RuntimeError`` then swallowed-and-warned (still falling
        through), preserving the original two-level control flow.
        """
        if not prepared.native_response_format:
            return None
        try:
            executor = self._get_executor()
            response = await executor.invoke(prepared.ai_request)
            if bool(getattr(response, "ok", True)):
                content = str(getattr(response, "output", "") or "")
                if not content.strip() and isinstance(getattr(response, "raw", None), dict):
                    content = json.dumps(response.raw, ensure_ascii=False)
                data = extract_json_from_text(content)
                validated = response_model(**data)
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                event_metadata = _with_final_request_context_audit(
                    {
                        "structured": True,
                        "native_response_format": True,
                        "elapsed_ms": round(elapsed_ms, 2),
                        "compression_applied": prepared.context_result.compression_applied
                        if prepared.context_result
                        else False,
                        "turn_round": turn_round,
                        "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request),
                    },
                    prepared=prepared,
                    active_request=prepared.ai_request,
                    profile=profile,
                )
                final_context_tokens = _final_request_context_tokens(
                    event_metadata,
                    prepared.context_result.token_estimate if prepared.context_result else None,
                )
                self._emit_call_end_event(
                    event_emitter=event_emitter,
                    role=role_id,
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    model=str(getattr(response, "model", "") or model),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=len(content) // 2,
                    call_id=call_id,
                    context_tokens_after=final_context_tokens,
                    compression_strategy=prepared.context_result.compression_strategy
                    if prepared.context_result
                    else None,
                    response_content=content,
                    metadata=_with_context_os_audit(event_metadata, prepared),
                )
                return StructuredLLMResponse(
                    data=validated.model_dump(),
                    raw_content=content,
                    token_estimate=prepared.context_result.token_estimate + len(content) // 2
                    if prepared.context_result
                    else len(content) // 2,
                    metadata=_with_context_os_audit(
                        _with_final_request_context_audit(
                            {
                                "native_response_format": True,
                                "response_format_mode": prepared.response_format_mode,
                                "elapsed_ms": round(elapsed_ms, 2),
                                "turn_round": turn_round,
                            },
                            prepared=prepared,
                            active_request=prepared.ai_request,
                            profile=profile,
                        ),
                        prepared,
                    ),
                )
            response_error = str(getattr(response, "error", "") or "").strip()
            normalized_error = response_error or "structured_llm_call_failed"
            fallback_request = caller._build_structured_fallback_request(
                prepared=prepared,
                profile=profile,
                response_model=response_model,
            )
            retry_metadata = _with_final_request_context_audit(
                {
                    "structured": True,
                    "native_response_format": True,
                    "retry_decision": "structured_response_format_fallback",
                    "error_category": classify_error(normalized_error),
                    "error_message": normalized_error,
                    "context_snapshot_ref": self._extract_context_snapshot_ref(fallback_request),
                },
                prepared=prepared,
                active_request=fallback_request,
                profile=profile,
            )
            self._emit_call_retry_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                call_id=call_id,
                retry_decision="structured_response_format_fallback",
                backoff_seconds=0.0,
                metadata=_with_context_os_audit(retry_metadata, prepared),
            )
            if not is_response_format_unsupported(normalized_error):
                raise RuntimeError(normalized_error)
        except RuntimeError as e:
            logger.warning("Native structured response_format call failed: %s", e)
        return None

    async def _try_instructor_structured(
        self,
        *,
        prepared: PreparedLLMRequest,
        profile: RoleProfile,
        messages: list[dict[str, str]],
        response_model: type,
        model: str,
        temperature: float,
        max_tokens: int,
        max_retries: int,
        prompt_tokens: int,
        turn_round: int,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        call_id: str,
        event_emitter: Any | None,
        start_time: float,
    ) -> StructuredLLMResponse | None:
        """Structured strategy 2: Instructor ``create_structured``.

        Returns a ``StructuredLLMResponse`` on success, or ``None`` (after a
        warned ``RuntimeError``) to fall through to the deterministic fallback.
        """
        try:
            provider = resolve_tool_call_provider(
                provider_id=str(getattr(profile, "provider_id", "") or ""), model=model
            )
            structured_client = create_structured_client(provider=provider, enable_instructor=True, async_mode=True)
            result: Any = await structured_client.create_structured(
                messages=messages,
                response_model=response_model,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                max_retries=max_retries,
            )
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            event_metadata = _with_final_request_context_audit(
                {
                    "structured": True,
                    "instructor_used": True,
                    "elapsed_ms": round(elapsed_ms, 2),
                    "compression_applied": prepared.context_result.compression_applied
                    if prepared.context_result
                    else False,
                    "turn_round": turn_round,
                    "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request),
                },
                prepared=prepared,
                active_request=prepared.ai_request,
                profile=profile,
            )
            final_context_tokens = _final_request_context_tokens(
                event_metadata,
                prepared.context_result.token_estimate if prepared.context_result else None,
            )
            self._emit_call_end_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                call_id=call_id,
                completion_tokens=len(result.model_dump_json()) // 2,
                context_tokens_after=final_context_tokens,
                compression_strategy=prepared.context_result.compression_strategy if prepared.context_result else None,
                response_content=result.model_dump_json(),
                metadata=_with_context_os_audit(event_metadata, prepared),
            )
            return StructuredLLMResponse(
                data=result.model_dump(),
                raw_content=result.model_dump_json(),
                token_estimate=prompt_tokens + len(result.model_dump_json()) // 2,
                metadata=_with_context_os_audit(
                    _with_final_request_context_audit(
                        {
                            "model": model,
                            "instructor_used": True,
                            "elapsed_ms": round(elapsed_ms, 2),
                            "run_id": run_id,
                            "workspace": self.workspace,
                            "attempt": attempt,
                            "turn_round": turn_round,
                        },
                        prepared=prepared,
                        active_request=prepared.ai_request,
                        profile=profile,
                    ),
                    prepared,
                ),
            )
        except RuntimeError as e:
            logger.warning(f"Instructor structured call failed: {e}, falling back")
        return None

    async def _run_structured_fallback(
        self,
        *,
        caller: Any,
        prepared: PreparedLLMRequest,
        profile: RoleProfile,
        response_model: type,
        model: str,
        prompt_tokens: int,
        turn_round: int,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        call_id: str,
        event_emitter: Any | None,
        start_time: float,
    ) -> StructuredLLMResponse:
        """Structured strategy 3: deterministic text-JSON fallback.

        Builds the structured fallback request, invokes, and either returns the
        not-ok error response, the validated success response, or the parse-fail
        ``validation_fail`` response. This strategy ALWAYS returns.
        """
        ai_request = caller._build_structured_fallback_request(
            prepared=prepared, profile=profile, response_model=response_model
        )
        executor = self._get_executor()
        response = await executor.invoke(ai_request)
        is_response_ok, response_error = read_response_status(response)
        response_format_mode = ""
        if isinstance(getattr(ai_request, "context", None), dict):
            response_format_mode = str(ai_request.context.get("response_format_mode", "") or "")
        if not is_response_ok:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            classified = classify_error(response_error)
            normalized_error = response_error or "structured_llm_call_failed"
            error_metadata = _with_final_request_context_audit(
                {
                    "structured": True,
                    "response_format_mode": response_format_mode,
                    "context_snapshot_ref": self._extract_context_snapshot_ref(ai_request),
                },
                prepared=prepared,
                active_request=ai_request,
                profile=profile,
            )
            self._emit_call_error_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                error_category=classified,
                error_message=normalized_error,
                call_id=call_id,
                elapsed_ms=elapsed_ms,
                metadata=_with_context_os_audit(error_metadata, prepared),
            )
            return StructuredLLMResponse(
                data={},
                raw_content="",
                error=normalized_error,
                error_category=classified,
                metadata=_with_context_os_audit(
                    _with_final_request_context_audit(
                        {
                            "model": model,
                            "response_format_mode": response_format_mode,
                            "elapsed_ms": round(elapsed_ms, 2),
                            "run_id": run_id,
                            "workspace": self.workspace,
                            "attempt": attempt,
                        },
                        prepared=prepared,
                        active_request=ai_request,
                        profile=profile,
                    ),
                    prepared,
                ),
            )

        content = str(getattr(response, "output", "") or "")
        if not content.strip() and isinstance(getattr(response, "raw", None), dict):
            try:
                content = json.dumps(response.raw, ensure_ascii=False)
            except (RuntimeError, ValueError):
                content = str(getattr(response, "output", "") or "")

        try:
            data = extract_json_from_text(content)
            validated = response_model(**data)
            validated_data = validated.model_dump() if hasattr(validated, "model_dump") else dict(validated)
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            event_metadata = _with_final_request_context_audit(
                {
                    "structured": True,
                    "instructor_used": False,
                    "elapsed_ms": round(elapsed_ms, 2),
                    "compression_applied": prepared.context_result.compression_applied
                    if prepared.context_result
                    else False,
                    "turn_round": turn_round,
                    "context_snapshot_ref": self._extract_context_snapshot_ref(ai_request),
                },
                prepared=prepared,
                active_request=ai_request,
                profile=profile,
            )
            final_context_tokens = _final_request_context_tokens(
                event_metadata,
                prepared.context_result.token_estimate if prepared.context_result else None,
            )
            self._emit_call_end_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                call_id=call_id,
                completion_tokens=len(content) // 2,
                context_tokens_after=final_context_tokens,
                compression_strategy=prepared.context_result.compression_strategy if prepared.context_result else None,
                response_content=content,
                metadata=_with_context_os_audit(event_metadata, prepared),
            )
            return StructuredLLMResponse(
                data=validated_data,
                raw_content=content,
                token_estimate=prompt_tokens + len(content) // 2,
                metadata=_with_context_os_audit(
                    _with_final_request_context_audit(
                        {
                            "model": model,
                            "instructor_used": False,
                            "elapsed_ms": round(elapsed_ms, 2),
                            "run_id": run_id,
                            "workspace": self.workspace,
                            "attempt": attempt,
                            "turn_round": turn_round,
                            "context_snapshot_ref": self._extract_context_snapshot_ref(ai_request),
                        },
                        prepared=prepared,
                        active_request=ai_request,
                        profile=profile,
                    ),
                    prepared,
                ),
            )
        except (RuntimeError, ValueError) as parse_error:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            error_msg = f"Failed to parse structured output: {parse_error}"
            self._emit_call_error_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                error_category="validation_fail",
                error_message=error_msg,
                call_id=call_id,
                elapsed_ms=elapsed_ms,
                metadata=_with_context_os_audit(
                    _with_final_request_context_audit(
                        {
                            "structured": True,
                            "response_format_mode": response_format_mode,
                            "context_snapshot_ref": self._extract_context_snapshot_ref(ai_request),
                        },
                        prepared=prepared,
                        active_request=ai_request,
                        profile=profile,
                    ),
                    prepared,
                ),
            )
            return StructuredLLMResponse(
                data={},
                raw_content=content,
                error=error_msg,
                error_category="validation_fail",
                validation_errors=[str(parse_error)],
                metadata=_with_context_os_audit(
                    _with_final_request_context_audit(
                        {
                            "model": model,
                            "elapsed_ms": round(elapsed_ms, 2),
                            "run_id": run_id,
                            "workspace": self.workspace,
                            "attempt": attempt,
                            "response_format_mode": response_format_mode,
                            "context_snapshot_ref": self._extract_context_snapshot_ref(ai_request),
                        },
                        prepared=prepared,
                        active_request=ai_request,
                        profile=profile,
                    ),
                    prepared,
                ),
            )

    async def call_structured(
        self,
        profile: RoleProfile,
        system_prompt: str,
        context: ContextRequest,
        response_model: type,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        max_retries: int = 3,
        prompt_fingerprint: str | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        attempt: int = 0,
        turn_round: int = 0,
        event_emitter: Any | None = None,
    ) -> StructuredLLMResponse:
        """Invoke LLM with structured output validation."""
        call_id = str(uuid.uuid4())[:8]
        run_id = run_id or f"llm_struct_{call_id}"
        task_id = task_id or getattr(context, "task_id", None)
        role_id = str(getattr(profile, "role_id", "unknown") or "unknown")
        model = profile.model or "default"
        from .helpers import resolve_max_tokens, resolve_temperature

        context_override = getattr(context, "context_override", None)
        effective_max_tokens = resolve_max_tokens(max_tokens, context_override)
        effective_temperature = resolve_temperature(temperature, context_override)

        start_time = time.perf_counter()
        prepared: PreparedLLMRequest | None = None

        try:
            # Import here to avoid circular dependency
            from .caller import LLMCaller

            caller = LLMCaller(
                workspace=self.workspace,
                enable_cache=self._enable_cache,
                executor=self._executor,
                emit_deprecation_warning=False,
            )
            caller._model_catalog = self._model_catalog
            caller._formatter = self._formatter

            prepared = await caller._prepare_llm_request(
                profile=profile,
                system_prompt=system_prompt,
                context=context,
                temperature=effective_temperature,
                max_tokens=effective_max_tokens,
                stream=False,
                response_model=response_model,
                platform_retry_max=max_retries,
            )
            await _store_call_start_context_snapshot(
                workspace=self.workspace,
                prepared=prepared,
                profile=profile,
                run_id=run_id,
                call_id=call_id,
            )
            messages = prepared.messages
            context_result = prepared.context_result
            prompt_tokens = context_result.token_estimate if context_result else len(system_prompt) // 4
            final_context_audit = build_final_request_context_audit_for_request(
                ai_request=prepared.ai_request,
                prepared=prepared,
                profile=profile,
            )
            raw_final_context_tokens = final_context_audit.get("final_request_token_estimate")
            final_context_tokens = int(
                raw_final_context_tokens if raw_final_context_tokens is not None else prompt_tokens
            )
            enforce_final_request_evidence_coverage(
                ai_request=prepared.ai_request,
                audit=final_context_audit,
            )

            self._emit_call_start_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                provider=str(getattr(profile, "provider_id", "") or ""),
                prompt_tokens=prompt_tokens,
                call_id=call_id,
                context_tokens_before=final_context_tokens,
                compression_strategy=context_result.compression_strategy if context_result else None,
                messages=messages,
                metadata=_with_context_os_audit(
                    {
                        "structured": True,
                        "response_model": response_model.__name__,
                        "instructor_available": INSTRUCTOR_AVAILABLE,
                        "native_tool_mode": prepared.native_tool_mode,
                        "response_format_mode": prepared.response_format_mode,
                        "compression_applied": context_result.compression_applied if context_result else False,
                        "turn_round": turn_round,
                        "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request),
                        "context_tokens_after": final_context_tokens,
                        "contextTokens": final_context_tokens,
                        "final_request_context_audit": final_context_audit,
                    },
                    prepared,
                ),
            )

            # Try native response_format
            native_result = await self._try_native_response_format_structured(
                caller=caller,
                prepared=prepared,
                profile=profile,
                response_model=response_model,
                model=model,
                prompt_tokens=prompt_tokens,
                turn_round=turn_round,
                role_id=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                call_id=call_id,
                event_emitter=event_emitter,
                start_time=start_time,
            )
            if native_result is not None:
                return native_result

            # Try Instructor
            if INSTRUCTOR_AVAILABLE:
                instructor_result = await self._try_instructor_structured(
                    prepared=prepared,
                    profile=profile,
                    messages=messages,
                    response_model=response_model,
                    model=model,
                    temperature=effective_temperature,
                    max_tokens=effective_max_tokens,
                    max_retries=max_retries,
                    prompt_tokens=prompt_tokens,
                    turn_round=turn_round,
                    role_id=role_id,
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    call_id=call_id,
                    event_emitter=event_emitter,
                    start_time=start_time,
                )
                if instructor_result is not None:
                    return instructor_result

            # Fallback
            return await self._run_structured_fallback(
                caller=caller,
                prepared=prepared,
                profile=profile,
                response_model=response_model,
                model=model,
                prompt_tokens=prompt_tokens,
                turn_round=turn_round,
                role_id=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                call_id=call_id,
                event_emitter=event_emitter,
                start_time=start_time,
            )

        except asyncio.CancelledError:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self._emit_call_error_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                error_category=ERROR_CATEGORY_CANCELLED,
                error_message="structured_call_cancelled",
                call_id=call_id,
                elapsed_ms=elapsed_ms,
                metadata=_with_context_os_audit(
                    _with_optional_final_request_context_audit(
                        {
                            "structured": True,
                            "error_type": "CancelledError",
                            "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request)
                            if prepared
                            else None,
                        },
                        prepared=prepared,
                        active_request=prepared.ai_request if prepared else None,
                        profile=profile,
                    ),
                    prepared,
                ),
            )
            raise

        except RuntimeError as e:
            return self._structured_exception_response(
                e,
                prepared=prepared,
                active_request=prepared.ai_request if prepared else None,
                profile=profile,
                model=model,
                role_id=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                call_id=call_id,
                event_emitter=event_emitter,
                start_time=start_time,
            )

    def _structured_exception_response(
        self,
        exc: BaseException,
        *,
        prepared: PreparedLLMRequest | None,
        active_request: Any,
        profile: RoleProfile,
        model: str,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        call_id: str,
        event_emitter: Any | None,
        start_time: float,
    ) -> StructuredLLMResponse:
        """Shared builder for the structured RuntimeError except arm (NOT CancelledError).

        Emits the call_error event and returns the failure
        ``StructuredLLMResponse`` with byte-identical structured metadata.
        """
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        error_category = classify_error(str(exc))
        self._emit_call_error_event(
            event_emitter=event_emitter,
            role=role_id,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            model=model,
            error_category=error_category,
            error_message=str(exc),
            call_id=call_id,
            elapsed_ms=elapsed_ms,
            metadata=_with_context_os_audit(
                _with_optional_final_request_context_audit(
                    {
                        "structured": True,
                        "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request)
                        if prepared
                        else None,
                    },
                    prepared=prepared,
                    active_request=active_request,
                    profile=profile,
                ),
                prepared,
            ),
        )
        return StructuredLLMResponse(
            data={},
            error=f"Structured LLM call failed: {exc}",
            error_category=error_category,
            metadata=_with_context_os_audit(
                _with_optional_final_request_context_audit(
                    {
                        "model": model,
                        "elapsed_ms": round(elapsed_ms, 2),
                        "run_id": run_id,
                        "workspace": self.workspace,
                        "attempt": attempt,
                    },
                    prepared=prepared,
                    active_request=active_request,
                    profile=profile,
                ),
                prepared,
            ),
        )

    # ========================================================================
    # Streaming call (migrated from call_stream.py)
    # ========================================================================

    async def call_stream(
        self,
        profile: RoleProfile,
        system_prompt: str,
        context: ContextRequest,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        run_id: str | None = None,
        task_id: str | None = None,
        attempt: int = 0,
        turn_round: int = 0,
        event_emitter: Any | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Invoke LLM with streaming mode."""
        logger.warning(
            "[LLMInvoker.call_stream] ENTRY: profile=%s run_id=%s", getattr(profile, "role_id", "unknown"), run_id
        )
        call_id = str(uuid.uuid4())[:8]
        run_id = run_id or f"llm_stream_{call_id}"
        task_id = task_id or getattr(context, "task_id", None)
        role_id = str(getattr(profile, "role_id", "unknown") or "unknown")
        model = profile.model or "default"
        start_time = time.perf_counter()
        from .helpers import resolve_max_tokens, resolve_temperature

        context_override = getattr(context, "context_override", None)
        effective_max_tokens = resolve_max_tokens(max_tokens, context_override)
        effective_temperature = resolve_temperature(temperature, context_override)

        runtime_cfg = resolve_stream_runtime_config(context)

        if runtime_cfg.get("cancel_requested") or self._is_stream_cancel_requested(context):
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            cancel_msg = "stream_cancelled_before_invoke"
            self._emit_call_error_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                error_category=ERROR_CATEGORY_CANCELLED,
                error_message=cancel_msg,
                call_id=call_id,
                elapsed_ms=elapsed_ms,
                metadata={
                    "stream": True,
                    "native_tool_mode": "disabled",
                    "tool_protocol": "none",
                    "native_tool_calling_fallback": False,
                },
            )
            yield {
                "type": "error",
                "error": cancel_msg,
                "metadata": {
                    "stream": True,
                    "native_tool_mode": "disabled",
                    "tool_protocol": "none",
                    "native_tool_calling_fallback": False,
                },
            }
            return

        try:
            # Import here to avoid circular dependency
            from .caller import LLMCaller

            caller = LLMCaller(
                workspace=self.workspace,
                enable_cache=self._enable_cache,
                executor=self._executor,
                emit_deprecation_warning=False,
            )
            caller._model_catalog = self._model_catalog
            caller._formatter = self._formatter

            prepared = await caller._prepare_llm_request(
                profile=profile,
                system_prompt=system_prompt,
                context=context,
                temperature=effective_temperature,
                max_tokens=effective_max_tokens,
                stream=True,
            )
            resolved_provider_id = str(
                getattr(prepared.ai_request, "provider_id", None) or getattr(profile, "provider_id", "") or ""
            )
            resolved_model = str(getattr(prepared.ai_request, "model", None) or model or "")
            prepared_messages = list(prepared.messages)
            message_roles: list[str] = []
            message_content_sha256: list[str] = []
            message_content_chars: list[int] = []
            for message in prepared_messages:
                if not isinstance(message, dict):
                    continue
                message_roles.append(str(message.get("role") or ""))
                content = str(message.get("content") or "")
                message_content_sha256.append(hashlib.sha256(content.encode("utf-8")).hexdigest())
                message_content_chars.append(len(content))

            with contextlib.suppress(TypeError, AttributeError, RuntimeError):
                emit_debug_event(
                    category="llm_request",
                    label="invoke_request",
                    source="polaris.kernelone.llm.invoker",
                    payload={
                        "trace_id": run_id,
                        "role": role_id,
                        "provider_id": resolved_provider_id,
                        "model": resolved_model,
                        "call_id": call_id,
                        "temperature": effective_temperature,
                        "max_tokens": effective_max_tokens,
                        "message_count": len(prepared_messages),
                        "message_roles": message_roles,
                        "message_content_sha256": message_content_sha256,
                        "message_content_chars": message_content_chars,
                        "redacted": True,
                    },
                )

            model = resolved_model or model

            async for event in self._stream_engine.run_stream(
                profile=profile,
                prepared=prepared,
                context=context,
                start_time=start_time,
                role_id=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                call_id=call_id,
                event_emitter=event_emitter,
                turn_round=turn_round,
            ):
                yield event

        except asyncio.CancelledError:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self._emit_call_error_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                error_category=ERROR_CATEGORY_CANCELLED,
                error_message="stream_cancelled",
                call_id=call_id,
                elapsed_ms=elapsed_ms,
                metadata={
                    "stream": True,
                    "native_tool_mode": "disabled",
                    "tool_protocol": "none",
                    "native_tool_calling_fallback": False,
                    "error_type": "CancelledError",
                },
            )
            raise

        except (ImportError, AttributeError, TypeError, ValueError) as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.exception(f"Stream LLM call failed: {e}")
            self._emit_call_error_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                error_category=classify_error(str(e)),
                error_message=str(e),
                call_id=call_id,
                elapsed_ms=elapsed_ms,
                metadata={
                    "stream": True,
                    "native_tool_mode": "disabled",
                    "tool_protocol": "none",
                    "native_tool_calling_fallback": False,
                    "error_type": type(e).__name__,
                },
            )
            yield {
                "type": "error",
                "error": str(e),
                "metadata": {
                    "stream": True,
                    "native_tool_mode": "disabled",
                    "tool_protocol": "none",
                    "native_tool_calling_fallback": False,
                    "error_type": type(e).__name__,
                },
            }

        except RuntimeError as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.exception(f"Stream LLM unexpected error: {e}")
            self._emit_call_error_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                error_category=classify_error(str(e)),
                error_message=str(e),
                call_id=call_id,
                elapsed_ms=elapsed_ms,
                metadata={
                    "stream": True,
                    "native_tool_mode": "disabled",
                    "tool_protocol": "none",
                    "native_tool_calling_fallback": False,
                    "error_type": type(e).__name__,
                },
            )
            yield {
                "type": "error",
                "error": str(e),
                "metadata": {
                    "stream": True,
                    "native_tool_mode": "disabled",
                    "tool_protocol": "none",
                    "native_tool_calling_fallback": False,
                    "error_type": type(e).__name__,
                },
            }

    # ========================================================================
    # Decision / Finalization callers (Slice B cutover)
    # ========================================================================

    async def call_decision(
        self,
        profile: RoleProfile,
        system_prompt: str,
        context: ContextRequest,
        tool_definitions: list[dict[str, Any]] | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        attempt: int = 0,
        turn_round: int = 0,
    ) -> dict[str, Any]:
        """Decision-phase LLM call via DecisionCaller."""
        from .decision_caller import DecisionCaller

        caller = DecisionCaller(self)
        return await caller.call(
            profile=profile,
            system_prompt=system_prompt,
            context=context,
            tool_definitions=tool_definitions,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            turn_round=turn_round,
        )

    async def call_finalization(
        self,
        profile: RoleProfile,
        system_prompt: str,
        context: ContextRequest,
        run_id: str | None = None,
        task_id: str | None = None,
        attempt: int = 0,
        turn_round: int = 0,
    ) -> dict[str, Any]:
        """Finalization-phase LLM call via FinalizationCaller."""
        from .finalization_caller import FinalizationCaller

        caller = FinalizationCaller(self)
        return await caller.call(
            profile=profile,
            system_prompt=system_prompt,
            context=context,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            turn_round=turn_round,
        )

    # ========================================================================
    # Helper methods
    # ========================================================================

    @staticmethod
    def _is_cache_eligible(
        *,
        prepared: PreparedLLMRequest,
        response_model: type | None,
    ) -> bool:
        """Cache is only safe for plain-text, no-tools turns."""
        if response_model is not None:
            return False
        if prepared.native_tool_mode != "disabled":
            return False
        if prepared.response_format_mode != "plain_text":
            return False
        return not prepared.native_tool_schemas

    @staticmethod
    def _is_stream_cancel_requested(context: Any) -> bool:
        """Check if stream cancellation was requested."""
        override = getattr(context, "context_override", None) if context else None
        if isinstance(override, dict) and override.get("stream_cancelled"):
            return True
        return bool(getattr(context, "stream_cancelled", False))

    # ========================================================================
    # Backward-compatible event emission delegates
    # ========================================================================

    def _emit_call_error_event(self, **kwargs: Any) -> None:
        """Backward-compatible delegate to LLMEventEmitter.emit_call_error_event."""
        self._fill_provider_from_metadata(kwargs)
        self._event_emitter.emit_call_error_event(**kwargs)

    def _emit_call_start_event(self, **kwargs: Any) -> None:
        """Backward-compatible delegate to LLMEventEmitter.emit_call_start_event."""
        self._fill_provider_from_metadata(kwargs)
        self._event_emitter.emit_call_start_event(**kwargs)

    def _emit_call_end_event(self, **kwargs: Any) -> None:
        """Backward-compatible delegate to LLMEventEmitter.emit_call_end_event."""
        self._event_emitter.emit_call_end_event(**kwargs)

    def _emit_call_retry_event(self, **kwargs: Any) -> None:
        """Backward-compatible delegate to LLMEventEmitter.emit_call_retry_event."""
        self._fill_provider_from_metadata(kwargs)
        self._event_emitter.emit_call_retry_event(**kwargs)

    @staticmethod
    def _fill_provider_from_metadata(kwargs: dict[str, Any]) -> None:
        if kwargs.get("provider"):
            return
        metadata = kwargs.get("metadata")
        if not isinstance(metadata, dict):
            return
        provider = metadata.get("provider") or metadata.get("provider_id") or metadata.get("to_provider")
        if provider:
            kwargs["provider"] = str(provider)


__all__ = ["LLMInvoker"]
