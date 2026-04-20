"""LLM Invoker Event Emitter - UEP lifecycle and legacy event emission.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from polaris.kernelone.events.uep_publisher import UEPEventPublisher

logger = logging.getLogger(__name__)


class LLMEventEmitter:
    """Emits UEP v2.0 lifecycle events and backward-compatible LLM events."""

    def __init__(self, workspace: str = "") -> None:
        """Initialize event emitter.

        Args:
            workspace: Workspace path for context.
        """
        self.workspace = workspace

    def publish_uep_lifecycle_event(
        self,
        *,
        role: str,
        run_id: str,
        event_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Fire-and-forget publish of an LLM lifecycle event to the UEP bus."""

        async def _publish() -> None:
            publisher = UEPEventPublisher()
            await publisher.publish_llm_lifecycle_event(
                workspace=self.workspace or ".",
                run_id=run_id,
                role=role,
                event_type=event_type,
                metadata=dict(metadata) if metadata else {},
            )

        try:
            _ = asyncio.create_task(_publish())
        except (RuntimeError, ValueError) as exc:
            if isinstance(exc, RuntimeError):
                logger.debug("UEP lifecycle publish skipped: no running event loop")
            else:
                logger.debug("UEP lifecycle publish fire-and-forget failed: %s", exc)

    def emit_call_error_event(
        self,
        *,
        event_emitter: Any | None,
        role: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        model: str,
        error_category: str,
        error_message: str,
        call_id: str,
        elapsed_ms: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Emit call error event."""
        _payload = dict(metadata or {})
        _payload.setdefault("call_id", call_id)
        _payload.setdefault("elapsed_ms", round(elapsed_ms, 2))
        _payload.setdefault("workspace", self.workspace)
        _payload.setdefault("error_category", error_category)
        _payload.setdefault("error_message", error_message)
        self.publish_uep_lifecycle_event(
            role=role,
            run_id=run_id,
            event_type="call_error",
            metadata=_payload,
        )
        if event_emitter is not None and hasattr(event_emitter, "_emit_call_error_event"):
            event_emitter._emit_call_error_event(
                role=role,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                error_category=error_category,
                error_message=error_message,
                call_id=call_id,
                elapsed_ms=elapsed_ms,
                metadata=metadata,
            )
        else:
            from polaris.cells.roles.kernel.internal.events import LLMEventType, emit_llm_event

            payload = dict(metadata or {})
            payload.setdefault("call_id", call_id)
            payload.setdefault("elapsed_ms", round(elapsed_ms, 2))
            payload.setdefault("workspace", self.workspace)
            emit_llm_event(
                event_type=LLMEventType.CALL_ERROR,
                role=role,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                error_category=error_category,
                error_message=error_message,
                metadata=payload,
            )

    def emit_call_start_event(
        self,
        *,
        event_emitter: Any | None,
        role: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        model: str,
        prompt_tokens: int = 0,
        call_id: str,
        context_tokens_before: int | None = None,
        compression_strategy: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Emit call start event."""
        _payload = dict(metadata or {})
        _payload.setdefault("call_id", call_id)
        _payload.setdefault("workspace", self.workspace)
        if messages is not None:
            _payload["messages"] = messages
        if context_tokens_before is not None:
            _payload["context_tokens_before"] = context_tokens_before
        if compression_strategy is not None:
            _payload["compression_strategy"] = compression_strategy
        self.publish_uep_lifecycle_event(
            role=role,
            run_id=run_id,
            event_type="call_start",
            metadata=_payload,
        )
        if event_emitter is not None and hasattr(event_emitter, "_emit_call_start_event"):
            event_emitter._emit_call_start_event(
                role=role,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                prompt_tokens=prompt_tokens,
                call_id=call_id,
                context_tokens_before=context_tokens_before,
                compression_strategy=compression_strategy,
                messages=messages,
                metadata=metadata,
            )
        else:
            from polaris.cells.roles.kernel.internal.events import LLMEventType, emit_llm_event

            payload = dict(metadata or {})
            payload.setdefault("call_id", call_id)
            payload.setdefault("workspace", self.workspace)
            if messages is not None:
                payload["messages"] = messages
            kwargs: dict[str, Any] = {
                "event_type": LLMEventType.CALL_START,
                "role": role,
                "run_id": run_id,
                "task_id": task_id,
                "attempt": attempt,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "metadata": payload,
            }
            if context_tokens_before is not None:
                kwargs["context_tokens_before"] = context_tokens_before
            if compression_strategy is not None:
                kwargs["compression_strategy"] = compression_strategy
            emit_llm_event(**kwargs)

    def emit_call_end_event(
        self,
        *,
        event_emitter: Any | None,
        role: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        model: str,
        call_id: str,
        completion_tokens: int = 0,
        prompt_tokens: int | None = None,
        provider: str | None = None,
        context_tokens_after: int | None = None,
        compression_strategy: str | None = None,
        response_content: str | None = None,
        tool_calls_count: int = 0,
        tool_errors_count: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Emit call end event."""
        _payload = dict(metadata or {})
        _payload.setdefault("call_id", call_id)
        _payload.setdefault("workspace", self.workspace)
        if response_content is not None:
            _payload["response_content"] = response_content
        if prompt_tokens is not None:
            _payload["prompt_tokens"] = prompt_tokens
        if provider:
            _payload["provider"] = provider
        if context_tokens_after is not None:
            _payload["context_tokens_after"] = context_tokens_after
        if compression_strategy is not None:
            _payload["compression_strategy"] = compression_strategy
        _payload.setdefault("completion_tokens", completion_tokens)
        _payload.setdefault("tool_calls_count", tool_calls_count)
        _payload.setdefault("tool_errors_count", tool_errors_count)
        self.publish_uep_lifecycle_event(
            role=role,
            run_id=run_id,
            event_type="call_end",
            metadata=_payload,
        )
        if event_emitter is not None and hasattr(event_emitter, "_emit_call_end_event"):
            event_emitter._emit_call_end_event(
                role=role,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                call_id=call_id,
                completion_tokens=completion_tokens,
                prompt_tokens=prompt_tokens,
                provider=provider,
                context_tokens_after=context_tokens_after,
                compression_strategy=compression_strategy,
                response_content=response_content,
                tool_calls_count=tool_calls_count,
                tool_errors_count=tool_errors_count,
                metadata=metadata,
            )
        else:
            from polaris.cells.roles.kernel.internal.events import LLMEventType, emit_llm_event

            payload = dict(metadata or {})
            payload.setdefault("call_id", call_id)
            payload.setdefault("workspace", self.workspace)
            if response_content is not None:
                payload["response_content"] = response_content
            kwargs: dict[str, Any] = {
                "event_type": LLMEventType.CALL_END,
                "role": role,
                "run_id": run_id,
                "task_id": task_id,
                "attempt": attempt,
                "model": model,
                "completion_tokens": completion_tokens,
                "tool_calls_count": tool_calls_count,
                "tool_errors_count": tool_errors_count,
                "metadata": payload,
            }
            if prompt_tokens is not None:
                kwargs["prompt_tokens"] = prompt_tokens
            if provider:
                kwargs["provider"] = provider
            if context_tokens_after is not None:
                kwargs["context_tokens_after"] = context_tokens_after
            if compression_strategy is not None:
                kwargs["compression_strategy"] = compression_strategy
            emit_llm_event(**kwargs)

    def emit_call_retry_event(
        self,
        *,
        event_emitter: Any | None,
        role: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        model: str,
        call_id: str,
        retry_decision: str,
        backoff_seconds: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Emit call retry event."""
        _payload = dict(metadata or {})
        _payload.setdefault("call_id", call_id)
        _payload.setdefault("workspace", self.workspace)
        _payload.setdefault("retry_decision", retry_decision)
        _payload.setdefault("backoff_seconds", backoff_seconds)
        self.publish_uep_lifecycle_event(
            role=role,
            run_id=run_id,
            event_type="call_retry",
            metadata=_payload,
        )
        if event_emitter is not None and hasattr(event_emitter, "_emit_call_retry_event"):
            event_emitter._emit_call_retry_event(
                role=role,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                call_id=call_id,
                retry_decision=retry_decision,
                backoff_seconds=backoff_seconds,
                metadata=metadata,
            )
        else:
            from polaris.cells.roles.kernel.internal.events import LLMEventType, emit_llm_event

            payload = dict(metadata or {})
            payload.setdefault("call_id", call_id)
            payload.setdefault("workspace", self.workspace)
            emit_llm_event(
                event_type=LLMEventType.CALL_RETRY,
                role=role,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                retry_decision=retry_decision,
                backoff_seconds=max(0.0, float(backoff_seconds)),
                metadata=payload,
            )


__all__ = ["LLMEventEmitter"]
