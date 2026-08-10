"""Streaming and thin-wrapper call mixins for LLMInvoker."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any, cast

from polaris.kernelone.telemetry.debug_stream import emit_debug_event

from ..error_handling import (
    ERROR_CATEGORY_CANCELLED,
    classify_error,
)
from ..request_preparer import LLMRequestPreparer
from ..stream_handler import (
    normalize_stream_chunk,  # noqa: F401
    resolve_stream_runtime_config,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
    from polaris.cells.roles.profile.public.service import RoleProfile

from ._helpers import (
    _enforce_factory_semantic_zero_transport,
    _invoker_owned_factory_semantic_identity,
    _prepared_request_temperature,
)

logger = logging.getLogger(__name__)


class _InvokerStreamMixin:
    """call_stream, call_decision, and call_finalization."""

    __slots__ = ()

    if TYPE_CHECKING:
        workspace: str
        _formatter: Any
        _model_catalog: Any
        _stream_engine: Any

        def _emit_call_error_event(self, **kwargs: Any) -> None: ...
        def _is_stream_cancel_requested(self, context: Any) -> bool: ...

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
        call_id = uuid.uuid4().hex
        factory_semantic_identity = _invoker_owned_factory_semantic_identity(
            run_id=run_id,
            turn_round=turn_round,
            call_id=call_id,
        )
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
            request_preparer = LLMRequestPreparer(
                workspace=self.workspace,
                formatter=self._formatter,
                model_catalog=self._model_catalog,
            )

            prepared = await request_preparer._prepare_llm_request(
                profile=profile,
                system_prompt=system_prompt,
                context=context,
                temperature=effective_temperature,
                max_tokens=effective_max_tokens,
                stream=True,
                factory_semantic_identity=factory_semantic_identity,
            )
            _enforce_factory_semantic_zero_transport(prepared)
            effective_temperature = _prepared_request_temperature(prepared, effective_temperature)
            resolved_provider_id = str(
                getattr(prepared.ai_request, "provider_id", None) or getattr(profile, "provider_id", "") or ""
            )
            resolved_model = str(getattr(prepared.ai_request, "model", None) or model or "")
            prepared_messages = list(prepared.messages)
            message_roles: list[str] = []
            message_content_sha256: list[str] = []
            message_content_chars: list[int] = []
            for message in cast(list[Any], prepared_messages):
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
