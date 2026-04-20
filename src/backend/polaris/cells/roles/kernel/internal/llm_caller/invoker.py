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
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.engine import AIExecutor
from polaris.kernelone.telemetry.debug_stream import emit_debug_event

from ..llm_cache import get_global_llm_cache
from .error_handling import (
    ERROR_CATEGORY_CANCELLED,
    build_native_tool_unavailable_error,
    classify_error,
    is_native_tool_calling_unsupported,
    is_response_format_unsupported,
)
from .event_emitter import LLMEventEmitter
from .helpers import (
    extract_json_from_text,
    extract_native_tool_calls,
    resolve_tool_call_provider,
)
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

# Module loaded indicator
print(f"[LLMInvoker] MODULE LOADED: __name__={__name__}", flush=True)

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
            allow_native_tool_text_fallback_fn=lambda ctx: self._allow_native_tool_text_fallback(ctx),
            emit_call_start_event=lambda **kwargs: self._emit_call_start_event(**kwargs),
            emit_call_error_event=lambda **kwargs: self._emit_call_error_event(**kwargs),
            emit_call_end_event=lambda **kwargs: self._emit_call_end_event(**kwargs),
            emit_call_retry_event=lambda **kwargs: self._emit_call_retry_event(**kwargs),
        )

    def set_executor(self, executor: Any) -> None:
        """Set AIExecutor instance (for DI after construction)."""
        self._executor = executor

    def set_formatter(self, formatter: Any) -> None:
        """Set ProviderFormatter for lazy serialization."""
        self._formatter = formatter

    def _get_executor(self) -> Any:
        """Get or create AIExecutor instance (lazy, respects DI injection)."""
        if self._executor is not None:
            return self._executor
        return AIExecutor()

    # ========================================================================
    # Non-streaming call (migrated from call_sync.py)
    # ========================================================================

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
        print(
            f"[LLMInvoker.call] ENTRY POINT REACHED: profile={getattr(profile, 'role_id', 'unknown')} run_id={run_id}",
            flush=True,
        )
        logger.warning("[LLMInvoker.call] ENTRY: profile=%s run_id=%s", getattr(profile, "role_id", "unknown"), run_id)
        call_id = str(uuid.uuid4())[:8]
        run_id = run_id or f"llm_{call_id}"
        task_id = task_id or getattr(context, "task_id", None)
        role_id = str(getattr(profile, "role_id", "unknown") or "unknown")
        model = profile.model or "default"

        start_time = time.perf_counter()

        try:
            # Import here to avoid circular dependency
            from .caller import LLMCaller

            caller = LLMCaller(workspace=self.workspace, enable_cache=self._enable_cache, executor=self._executor)
            caller._model_catalog = self._model_catalog
            caller._formatter = self._formatter

            prepared = await caller._prepare_llm_request(
                profile=profile,
                system_prompt=system_prompt,
                context=context,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
                response_model=response_model,
                platform_retry_max=platform_retry_max,
            )
            context_result = prepared.context_result
            prompt_tokens = context_result.token_estimate if context_result else len(system_prompt) // 4

            self._emit_call_start_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                prompt_tokens=prompt_tokens,
                call_id=call_id,
                context_tokens_before=context_result.token_estimate if context_result else None,
                compression_strategy=context_result.compression_strategy if context_result else None,
                messages=prepared.messages,
                metadata={
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "prompt_fingerprint": prompt_fingerprint,
                    "native_tool_mode": prepared.native_tool_mode,
                    "response_format_mode": prepared.response_format_mode,
                    "compression_applied": context_result.compression_applied if context_result else False,
                    "turn_round": turn_round,
                },
            )

            cache_eligible = self._is_cache_eligible(prepared=prepared, response_model=response_model)

            if prepared.native_tool_mode == "native_tools_unavailable":
                # Try text-based fallback instead of immediately returning error
                allow_native_tool_text_fallback = self._allow_native_tool_text_fallback(context)
                if allow_native_tool_text_fallback and prepared.native_tool_schemas:
                    # Build and invoke fallback request (tools described in prompt, not native)
                    executor = self._get_executor()
                    fallback_request = caller._build_native_tool_fallback_request(
                        prepared=prepared, profile=profile, mode="chat"
                    )
                    response = await executor.invoke(fallback_request)
                    native_tool_fallback = True
                    response_ok = getattr(response, "ok", True)
                    response_error = getattr(response, "error", None)
                    if response_ok:
                        # Fallback succeeded, return the response
                        elapsed_ms = (time.perf_counter() - start_time) * 1000
                        return LLMResponse(
                            content=getattr(response, "content", "") or "",
                            error=None,
                            error_category=None,
                            metadata={
                                "model": model,
                                "native_tool_mode": "native_tools_text_fallback",
                                "response_format_mode": prepared.response_format_mode,
                                "native_tool_calling_fallback": native_tool_fallback,
                                "elapsed_ms": round(elapsed_ms, 2),
                                "run_id": run_id,
                                "workspace": self.workspace,
                                "attempt": attempt,
                            },
                        )
                    # Fallback failed, proceed to return error

                # Fallback not allowed or failed - return error
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                tool_error = build_native_tool_unavailable_error(profile)
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
                    metadata={
                        "native_tool_mode": prepared.native_tool_mode,
                        "response_format_mode": prepared.response_format_mode,
                    },
                )
                return LLMResponse(
                    content="",
                    error=tool_error,
                    error_category="provider",
                    metadata={
                        "model": model,
                        "native_tool_mode": prepared.native_tool_mode,
                        "response_format_mode": prepared.response_format_mode,
                        "run_id": run_id,
                        "workspace": self.workspace,
                        "attempt": attempt,
                    },
                )

            cache = get_global_llm_cache()
            if self._enable_cache and prompt_fingerprint and cache_eligible:
                cached = cache.get(
                    prompt_fingerprint=prompt_fingerprint,
                    context_summary=prepared.context_summary,
                    temperature=temperature,
                    model=model,
                )
                if cached:
                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                    logger.debug(
                        "[LLMInvoker] Cache hit, returning cached response: model=%s length=%d",
                        model,
                        len(cached),
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
                        context_tokens_after=context_result.token_estimate if context_result else None,
                        compression_strategy=context_result.compression_strategy if context_result else None,
                        response_content=cached,
                        metadata={
                            "elapsed_ms": round(elapsed_ms, 2),
                            "cached": True,
                            "source": "cache",
                            "compression_applied": context_result.compression_applied if context_result else False,
                            "turn_round": turn_round,
                        },
                    )
                    return LLMResponse(
                        content=cached,
                        token_estimate=len(cached) // 2,
                        metadata={
                            "cached": True,
                            "model": model,
                            "elapsed_ms": round(elapsed_ms, 2),
                            "run_id": run_id,
                            "workspace": self.workspace,
                            "attempt": attempt,
                            "turn_round": turn_round,
                            "native_tool_mode": prepared.native_tool_mode,
                        },
                    )

            executor = self._get_executor()
            active_request = prepared.ai_request
            native_tool_fallback = False
            allow_native_tool_text_fallback = self._allow_native_tool_text_fallback(context)
            response = await executor.invoke(active_request)
            native_response_fallback = False

            response_ok = getattr(response, "ok", True)
            # Detect error: hasattr checks instance __dict__ first (True for explicitly set None).
            # getattr with default only triggers for truly missing attributes.
            _has_error = hasattr(response, "error")
            _raw_error = getattr(response, "error", None) if _has_error else None
            response_error = str(_raw_error or "").strip() if _raw_error is not None else ""
            # Treat as failed if ok=False or if error string is present (handles providers
            # that return ok=True with an error field like "Unknown field: tools")
            is_response_ok = (bool(response_ok) if isinstance(response_ok, bool) else True) and not response_error

            if (
                not is_response_ok
                and prepared.native_tool_schemas
                and allow_native_tool_text_fallback
                and is_native_tool_calling_unsupported(response_error)
            ):
                active_request = caller._build_native_tool_fallback_request(
                    prepared=prepared, profile=profile, mode="chat"
                )
                response = await executor.invoke(active_request)
                native_tool_fallback = True
                response_ok = getattr(response, "ok", True)
                _has_error = hasattr(response, "error")
                _raw_error = getattr(response, "error", None) if _has_error else None
                response_error = str(_raw_error or "").strip() if _raw_error is not None else ""
                is_response_ok = (bool(response_ok) if isinstance(response_ok, bool) else True) and not response_error

            if (
                not is_response_ok
                and prepared.native_response_format
                and is_response_format_unsupported(response_error)
            ):
                active_request = caller._build_structured_fallback_request(
                    prepared=prepared, profile=profile, response_model=response_model or dict, mode="chat"
                )
                response = await executor.invoke(active_request)
                native_response_fallback = True
                response_ok = getattr(response, "ok", True)
                _has_error = hasattr(response, "error")
                _raw_error = getattr(response, "error", None) if _has_error else None
                response_error = str(_raw_error or "").strip() if _raw_error is not None else ""
                is_response_ok = (bool(response_ok) if isinstance(response_ok, bool) else True) and not response_error

            if not is_response_ok:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                classified = classify_error(response_error)
                active_context = active_request.context if isinstance(active_request.context, dict) else {}
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
                    metadata={
                        "native_tool_calling_fallback": native_tool_fallback,
                        "native_response_format_fallback": native_response_fallback,
                        "native_tool_mode": str(active_context.get("native_tool_mode") or prepared.native_tool_mode),
                        "response_format_mode": str(
                            active_context.get("response_format_mode") or prepared.response_format_mode
                        ),
                        "native_tool_text_fallback_allowed": allow_native_tool_text_fallback,
                    },
                )
                return LLMResponse(
                    content="",
                    error=response_error or "LLM call failed",
                    error_category=classified,
                    metadata={
                        "model": model,
                        "elapsed_ms": round(elapsed_ms, 2),
                        "native_tool_calling_fallback": native_tool_fallback,
                        "native_response_format_fallback": native_response_fallback,
                        "native_tool_text_fallback_allowed": allow_native_tool_text_fallback,
                        "run_id": run_id,
                        "workspace": self.workspace,
                        "attempt": attempt,
                    },
                )

            raw_payload = response.raw if isinstance(response.raw, dict) else {}
            # DEBUG: Log raw LLM response for debugging parsing issues
            output_text = str(getattr(response, "output", "") or "")
            print(
                f"[LLMInvoker] RAW_RESPONSE: model={raw_payload.get('model', 'unknown')} "
                f"output_length={len(output_text)} output_preview={output_text[:500]!r}",
                flush=True,
            )
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
            completion_tokens = len(response_text) // 2

            if self._enable_cache and prompt_fingerprint and cache_eligible:
                cache.put(
                    prompt_fingerprint=prompt_fingerprint,
                    context_summary=prepared.context_summary,
                    temperature=temperature,
                    model=model,
                    response_content=response_text,
                    token_estimate=completion_tokens,
                )

            self._emit_call_end_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=response_model_name,
                provider=response_provider,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                call_id=call_id,
                context_tokens_after=prepared.context_result.token_estimate if prepared.context_result else None,
                compression_strategy=prepared.context_result.compression_strategy if prepared.context_result else None,
                response_content=response_text,
                tool_calls_count=len(native_tool_calls),
                metadata={
                    "elapsed_ms": round(elapsed_ms, 2),
                    "cached": False,
                    "source": "llm",
                    "compression_applied": prepared.context_result.compression_applied
                    if prepared.context_result
                    else False,
                    "turn_round": turn_round,
                },
            )

            return LLMResponse(
                content=response_text,
                token_estimate=prepared.context_result.token_estimate + completion_tokens
                if prepared.context_result
                else completion_tokens,
                tool_calls=native_tool_calls,
                tool_call_provider=native_tool_provider,
                metadata={
                    "model": response_model_name,
                    "provider": response_provider,
                    "native_tool_calls_count": len(native_tool_calls),
                    "elapsed_ms": round(elapsed_ms, 2),
                    "run_id": run_id,
                    "workspace": self.workspace,
                    "attempt": attempt,
                    "turn_round": turn_round,
                    # SSOT Fix: Pass context token count for context panel display
                    "context_tokens": int(prepared.context_result.token_estimate) if prepared.context_result else 0,
                },
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
                metadata={"error_type": "CancelledError"},
            )
            raise

        except (ImportError, AttributeError, TypeError, ValueError) as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            error_category = classify_error(str(e))
            logger.error(f"LLM call failed: {e}")
            self._emit_call_error_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                error_category=error_category,
                error_message=str(e),
                call_id=call_id,
                elapsed_ms=elapsed_ms,
                metadata={"error_type": type(e).__name__},
            )
            return LLMResponse(
                content="",
                error=f"LLM call failed: {e}",
                error_category=error_category,
                metadata={
                    "run_id": run_id,
                    "workspace": self.workspace,
                    "attempt": attempt,
                    "elapsed_ms": round(elapsed_ms, 2),
                },
            )

        except RuntimeError as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            error_category = classify_error(str(e))
            logger.exception(f"LLM call unexpected error: {e}")
            self._emit_call_error_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                error_category=error_category,
                error_message=str(e),
                call_id=call_id,
                elapsed_ms=elapsed_ms,
                metadata={"error_type": type(e).__name__},
            )
            return LLMResponse(
                content="",
                error=f"LLM call failed: {e}",
                error_category=error_category,
                metadata={
                    "run_id": run_id,
                    "workspace": self.workspace,
                    "attempt": attempt,
                    "elapsed_ms": round(elapsed_ms, 2),
                },
            )

    # ========================================================================
    # Structured call (migrated from call_structured.py)
    # ========================================================================

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

        start_time = time.perf_counter()

        try:
            # Import here to avoid circular dependency
            from .caller import LLMCaller

            caller = LLMCaller(workspace=self.workspace, enable_cache=self._enable_cache, executor=self._executor)
            caller._model_catalog = self._model_catalog
            caller._formatter = self._formatter

            prepared = await caller._prepare_llm_request(
                profile=profile,
                system_prompt=system_prompt,
                context=context,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
                response_model=response_model,
                platform_retry_max=max_retries,
            )
            messages = prepared.messages
            context_result = prepared.context_result
            prompt_tokens = context_result.token_estimate if context_result else len(system_prompt) // 4

            self._emit_call_start_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                prompt_tokens=prompt_tokens,
                call_id=call_id,
                context_tokens_before=context_result.token_estimate if context_result else None,
                compression_strategy=context_result.compression_strategy if context_result else None,
                messages=messages,
                metadata={
                    "structured": True,
                    "response_model": response_model.__name__,
                    "instructor_available": INSTRUCTOR_AVAILABLE,
                    "native_tool_mode": prepared.native_tool_mode,
                    "response_format_mode": prepared.response_format_mode,
                    "compression_applied": context_result.compression_applied if context_result else False,
                    "turn_round": turn_round,
                },
            )

            # Try native response_format
            if prepared.native_response_format:
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
                            context_tokens_after=prepared.context_result.token_estimate
                            if prepared.context_result
                            else None,
                            compression_strategy=prepared.context_result.compression_strategy
                            if prepared.context_result
                            else None,
                            response_content=content,
                            metadata={
                                "structured": True,
                                "native_response_format": True,
                                "elapsed_ms": round(elapsed_ms, 2),
                                "compression_applied": prepared.context_result.compression_applied
                                if prepared.context_result
                                else False,
                                "turn_round": turn_round,
                            },
                        )
                        return StructuredLLMResponse(
                            data=validated.model_dump(),
                            raw_content=content,
                            token_estimate=prepared.context_result.token_estimate + len(content) // 2
                            if prepared.context_result
                            else len(content) // 2,
                            metadata={
                                "native_response_format": True,
                                "response_format_mode": prepared.response_format_mode,
                                "elapsed_ms": round(elapsed_ms, 2),
                                "turn_round": turn_round,
                            },
                        )
                    response_error = str(getattr(response, "error", "") or "").strip()
                    if not is_response_format_unsupported(response_error):
                        raise RuntimeError(response_error or "structured_llm_call_failed")
                except RuntimeError as e:
                    logger.warning("Native structured response_format call failed: %s", e)

            # Try Instructor
            if INSTRUCTOR_AVAILABLE:
                try:
                    provider = resolve_tool_call_provider(
                        provider_id=str(getattr(profile, "provider_id", "") or ""), model=model
                    )
                    structured_client = create_structured_client(
                        provider=provider, enable_instructor=True, async_mode=True
                    )
                    result: Any = await structured_client.create_structured(
                        messages=messages,
                        response_model=response_model,
                        model=model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        max_retries=max_retries,
                    )
                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                    self._emit_call_end_event(
                        event_emitter=event_emitter,
                        role=role_id,
                        run_id=run_id,
                        task_id=task_id,
                        attempt=attempt,
                        model=model,
                        call_id=call_id,
                        completion_tokens=len(result.model_dump_json()) // 2,
                        context_tokens_after=prepared.context_result.token_estimate
                        if prepared.context_result
                        else None,
                        compression_strategy=prepared.context_result.compression_strategy
                        if prepared.context_result
                        else None,
                        response_content=result.model_dump_json(),
                        metadata={
                            "structured": True,
                            "instructor_used": True,
                            "elapsed_ms": round(elapsed_ms, 2),
                            "compression_applied": prepared.context_result.compression_applied
                            if prepared.context_result
                            else False,
                            "turn_round": turn_round,
                        },
                    )
                    return StructuredLLMResponse(
                        data=result.model_dump(),
                        raw_content=result.model_dump_json(),
                        token_estimate=prompt_tokens + len(result.model_dump_json()) // 2,
                        metadata={
                            "model": model,
                            "instructor_used": True,
                            "elapsed_ms": round(elapsed_ms, 2),
                            "run_id": run_id,
                            "workspace": self.workspace,
                            "attempt": attempt,
                            "turn_round": turn_round,
                        },
                    )
                except RuntimeError as e:
                    logger.warning(f"Instructor structured call failed: {e}, falling back")

            # Fallback
            ai_request = caller._build_structured_fallback_request(
                prepared=prepared, profile=profile, response_model=response_model
            )
            executor = self._get_executor()
            response = await executor.invoke(ai_request)
            response_ok = getattr(response, "ok", True)
            _has_error = hasattr(response, "error")
            _raw_error = getattr(response, "error", None) if _has_error else None
            response_error = str(_raw_error or "").strip() if _raw_error is not None else ""
            is_response_ok = (bool(response_ok) if isinstance(response_ok, bool) else True) and not response_error
            response_format_mode = ""
            if isinstance(getattr(ai_request, "context", None), dict):
                response_format_mode = str(ai_request.context.get("response_format_mode", "") or "")
            if not is_response_ok:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                classified = classify_error(response_error)
                normalized_error = response_error or "structured_llm_call_failed"
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
                    metadata={"structured": True, "response_format_mode": response_format_mode},
                )
                return StructuredLLMResponse(
                    data={},
                    raw_content="",
                    error=normalized_error,
                    error_category=classified,
                    metadata={
                        "model": model,
                        "response_format_mode": response_format_mode,
                        "elapsed_ms": round(elapsed_ms, 2),
                        "run_id": run_id,
                        "workspace": self.workspace,
                        "attempt": attempt,
                    },
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
                self._emit_call_end_event(
                    event_emitter=event_emitter,
                    role=role_id,
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    model=model,
                    call_id=call_id,
                    completion_tokens=len(content) // 2,
                    context_tokens_after=prepared.context_result.token_estimate if prepared.context_result else None,
                    compression_strategy=prepared.context_result.compression_strategy
                    if prepared.context_result
                    else None,
                    response_content=content,
                    metadata={
                        "structured": True,
                        "instructor_used": False,
                        "elapsed_ms": round(elapsed_ms, 2),
                        "compression_applied": prepared.context_result.compression_applied
                        if prepared.context_result
                        else False,
                        "turn_round": turn_round,
                    },
                )
                return StructuredLLMResponse(
                    data=validated_data,
                    raw_content=content,
                    token_estimate=prompt_tokens + len(content) // 2,
                    metadata={
                        "model": model,
                        "instructor_used": False,
                        "elapsed_ms": round(elapsed_ms, 2),
                        "run_id": run_id,
                        "workspace": self.workspace,
                        "attempt": attempt,
                        "turn_round": turn_round,
                    },
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
                    metadata={"structured": True},
                )
                return StructuredLLMResponse(
                    data={},
                    raw_content=content,
                    error=error_msg,
                    error_category="validation_fail",
                    validation_errors=[str(parse_error)],
                    metadata={
                        "model": model,
                        "elapsed_ms": round(elapsed_ms, 2),
                        "run_id": run_id,
                        "workspace": self.workspace,
                        "attempt": attempt,
                    },
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
                metadata={"structured": True, "error_type": "CancelledError"},
            )
            raise

        except RuntimeError as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            error_category = classify_error(str(e))
            self._emit_call_error_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                error_category=error_category,
                error_message=str(e),
                call_id=call_id,
                elapsed_ms=elapsed_ms,
                metadata={"structured": True},
            )
            return StructuredLLMResponse(
                data={},
                error=f"Structured LLM call failed: {e}",
                error_category=error_category,
                metadata={
                    "model": model,
                    "elapsed_ms": round(elapsed_ms, 2),
                    "run_id": run_id,
                    "workspace": self.workspace,
                    "attempt": attempt,
                },
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

        # Emit debug event with actual LLM request content (only when debug stream is enabled)
        with contextlib.suppress(TypeError, AttributeError, RuntimeError):
            emit_debug_event(
                category="llm_request",
                label="invoke_request",
                source="polaris.kernelone.llm.invoker",
                payload={
                    "trace_id": run_id,
                    "role": role_id,
                    "model": model,
                    "call_id": call_id,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "messages": [],
                },
            )

        try:
            # Import here to avoid circular dependency
            from .caller import LLMCaller

            caller = LLMCaller(workspace=self.workspace, enable_cache=self._enable_cache, executor=self._executor)
            caller._model_catalog = self._model_catalog
            caller._formatter = self._formatter

            prepared = await caller._prepare_llm_request(
                profile=profile,
                system_prompt=system_prompt,
                context=context,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )

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
    def _allow_native_tool_text_fallback(context: Any) -> bool:
        """Check if native tool text fallback is allowed."""
        override = getattr(context, "context_override", None)
        if not isinstance(override, dict):
            return False
        for key in (
            "allow_native_tool_text_fallback",
            "native_tool_text_fallback",
            "allow_degraded_native_tool_text_fallback",
        ):
            raw = override.get(str(key))
            if isinstance(raw, bool):
                if raw:
                    return True
                continue
            token = str(raw or "").strip().lower()
            if token in {"1", "true", "yes", "on"}:
                return True
        return False

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
        self._event_emitter.emit_call_error_event(**kwargs)

    def _emit_call_start_event(self, **kwargs: Any) -> None:
        """Backward-compatible delegate to LLMEventEmitter.emit_call_start_event."""
        self._event_emitter.emit_call_start_event(**kwargs)

    def _emit_call_end_event(self, **kwargs: Any) -> None:
        """Backward-compatible delegate to LLMEventEmitter.emit_call_end_event."""
        self._event_emitter.emit_call_end_event(**kwargs)

    def _emit_call_retry_event(self, **kwargs: Any) -> None:
        """Backward-compatible delegate to LLMEventEmitter.emit_call_retry_event."""
        self._event_emitter.emit_call_retry_event(**kwargs)


__all__ = ["LLMInvoker"]
