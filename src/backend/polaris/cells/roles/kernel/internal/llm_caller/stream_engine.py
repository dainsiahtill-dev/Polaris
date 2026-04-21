"""LLM Invoker Stream Engine - Streaming call execution logic.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from .error_handling import (
    ERROR_CATEGORY_CANCELLED,
    build_native_tool_unavailable_error,
    classify_error,
    is_retryable_error,
)
from .stream_handler import (
    build_stream_slo_metrics,
    normalize_stream_chunk,
    resolve_stream_runtime_config,
    tool_call_signature_from_normalized,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)


def _is_stream_cancel_requested(context: Any) -> bool:
    """Check if stream cancellation was requested."""
    # Inline to avoid circular import from helpers
    override = getattr(context, "context_override", None) if context else None
    if isinstance(override, dict) and override.get("stream_cancelled"):
        return True
    return bool(getattr(context, "stream_cancelled", False))


class StreamEngine:
    """Executes LLM streaming calls with retry, dedupe, and SLO tracking."""

    def __init__(
        self,
        *,
        workspace: str,
        get_executor: Any,
        allow_native_tool_text_fallback_fn: Any,
        emit_call_start_event: Any,
        emit_call_error_event: Any,
        emit_call_end_event: Any,
        emit_call_retry_event: Any,
    ) -> None:
        """Initialize stream engine.

        Args:
            workspace: Workspace path.
            get_executor: Callable that returns an executor with invoke_stream.
            allow_native_tool_text_fallback_fn: Callable(context) -> bool.
            emit_call_start_event: Event emitter callable.
            emit_call_error_event: Event emitter callable.
            emit_call_end_event: Event emitter callable.
            emit_call_retry_event: Event emitter callable.
        """
        self.workspace = workspace
        self._get_executor = get_executor
        self._allow_native_tool_text_fallback = allow_native_tool_text_fallback_fn
        self._emit_call_start = emit_call_start_event
        self._emit_call_error = emit_call_error_event
        self._emit_call_end = emit_call_end_event
        self._emit_call_retry = emit_call_retry_event

    async def run_stream(
        self,
        *,
        profile: Any,
        prepared: Any,
        context: Any,
        start_time: float,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        model: str,
        call_id: str,
        event_emitter: Any | None,
        turn_round: int,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Run the streaming execution after request preparation."""
        runtime_cfg = resolve_stream_runtime_config(context)
        max_reconnects = int(runtime_cfg.get("max_reconnects", 1))
        retry_backoff_seconds = float(runtime_cfg.get("retry_backoff_seconds", 0.35))
        emit_unknown_events = bool(runtime_cfg.get("emit_unknown_events", False))
        dedupe_reconnect_replay = bool(runtime_cfg.get("dedupe_reconnect_replay", True))
        max_events = int(runtime_cfg.get("max_events", 0))

        stream_event_count = 0
        reconnect_count = 0
        deduped_chunk_count = 0
        deduped_tool_call_count = 0
        raw_tool_call_count = 0
        total_backpressure_wait_ms = 0.0
        first_event_latency_ms: float | None = None
        emitted_content = ""
        reconnect_prefix = ""
        emitted_tool_signatures: set[str] = set()
        active_native_tool_mode = "disabled"
        active_tool_protocol = "none"

        def _current_slo(elapsed_ms: float) -> dict[str, Any]:
            return build_stream_slo_metrics(
                elapsed_ms=elapsed_ms,
                event_count=stream_event_count,
                reconnect_count=reconnect_count,
                deduped_chunks=deduped_chunk_count,
                deduped_tool_calls=deduped_tool_call_count,
                raw_tool_calls=raw_tool_call_count,
                first_event_latency_ms=first_event_latency_ms,
                backpressure_wait_ms=total_backpressure_wait_ms,
            )

        def _build_stream_error_metadata(*, elapsed_ms: float, error_type: str = "") -> dict[str, Any]:
            payload: dict[str, Any] = {
                "stream": True,
                "native_tool_mode": active_native_tool_mode,
                "tool_protocol": active_tool_protocol,
                "native_tool_calling_fallback": False,
            }
            payload.update(_current_slo(elapsed_ms))
            if error_type:
                payload["error_type"] = error_type
            return payload

        context_result = prepared.context_result
        prompt_tokens = context_result.token_estimate if context_result else 0

        self._emit_call_start(
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
                "stream": True,
                "temperature": getattr(context, "temperature", 0.7),
                "max_tokens": getattr(context, "max_tokens", 4000),
                "stream_max_reconnects": max_reconnects,
                "stream_retry_backoff_seconds": retry_backoff_seconds,
                "stream_dedupe_reconnect_replay": dedupe_reconnect_replay,
                "native_tool_mode": prepared.native_tool_mode,
                "response_format_mode": prepared.response_format_mode,
                "compression_applied": context_result.compression_applied if context_result else False,
                "turn_round": turn_round,
            },
        )

        if prepared.native_tool_mode == "native_tools_unavailable":
            allow_fallback = self._allow_native_tool_text_fallback(context)
            if allow_fallback and prepared.native_tool_schemas:
                from .caller import LLMCaller

                caller = LLMCaller(workspace=self.workspace, enable_cache=False, executor=self._get_executor())
                fallback_request = caller._build_native_tool_fallback_request(
                    prepared=prepared, profile=profile, mode="chat"
                )
                executor = self._get_executor()
                try:
                    async for chunk in executor.invoke_stream(fallback_request):
                        stream_event_count += 1
                        if first_event_latency_ms is None:
                            first_event_latency_ms = (time.perf_counter() - start_time) * 1000
                        normalized = normalize_stream_chunk(
                            chunk, native_tool_mode="native_tools_text_fallback", tool_protocol="none"
                        )
                        event_type = normalized.event_type
                        content = normalized.content
                        metadata = dict(normalized.metadata)
                        metadata.setdefault("stream_event_index", stream_event_count)
                        metadata.setdefault("native_tool_fallback", True)
                        yield {
                            "type": event_type,
                            "content": content,
                            "metadata": metadata,
                            "iteration": turn_round,
                        }
                        if event_type == "complete":
                            return
                except (RuntimeError, ValueError):
                    pass

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            tool_error = build_native_tool_unavailable_error(profile)
            self._emit_call_error(
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
                metadata=_build_stream_error_metadata(elapsed_ms=elapsed_ms),
            )
            yield {
                "type": "error",
                "error": tool_error,
                "metadata": _build_stream_error_metadata(elapsed_ms=elapsed_ms),
                "iteration": turn_round,
            }
            return

        executor = self._get_executor()
        total_content = ""
        active_request = prepared.ai_request
        active_native_tool_mode = prepared.native_tool_mode
        active_tool_protocol = (
            "structured_native_tools" if active_native_tool_mode == "native_tools_streaming" else "none"
        )

        while True:
            if _is_stream_cancel_requested(context):
                raise asyncio.CancelledError("stream_cancelled_by_context")

            should_retry = False
            retry_error_message = ""
            retry_error_category = "unknown"

            try:
                async for chunk in executor.invoke_stream(active_request):
                    stream_event_count += 1
                    if first_event_latency_ms is None:
                        first_event_latency_ms = (time.perf_counter() - start_time) * 1000
                    if max_events > 0 and stream_event_count > max_events:
                        raise RuntimeError(f"stream_event_limit_exceeded:{max_events}")

                    normalized = normalize_stream_chunk(
                        chunk, native_tool_mode=active_native_tool_mode, tool_protocol=active_tool_protocol
                    )
                    event_type = normalized.event_type
                    content = normalized.content
                    metadata = dict(normalized.metadata)
                    metadata.setdefault("stream_event_index", stream_event_count)
                    metadata.setdefault("stream_reconnect_attempt", reconnect_count)
                    metadata.setdefault("stream_reconnect_recovered", reconnect_count > 0)

                    if event_type == "error":
                        error_message = str(normalized.error or content or "stream_error").strip() or "stream_error"
                        error_category = classify_error(error_message)
                        if is_retryable_error(error_category) and reconnect_count < max_reconnects:
                            should_retry = True
                            retry_error_message = error_message
                            retry_error_category = error_category
                            break
                        elapsed_ms = (time.perf_counter() - start_time) * 1000
                        metadata.setdefault("error", error_message)
                        metadata.update(_current_slo(elapsed_ms))
                        self._emit_call_error(
                            event_emitter=event_emitter,
                            role=role_id,
                            run_id=run_id,
                            task_id=task_id,
                            attempt=attempt,
                            model=model,
                            error_category=error_category,
                            error_message=error_message,
                            call_id=call_id,
                            elapsed_ms=elapsed_ms,
                            metadata={
                                **_build_stream_error_metadata(elapsed_ms=elapsed_ms),
                                "stream_reconnect_attempt": reconnect_count,
                            },
                        )
                        yield_started_at = time.perf_counter()
                        yield {"type": "error", "error": error_message, "metadata": metadata, "iteration": turn_round}
                        total_backpressure_wait_ms += (time.perf_counter() - yield_started_at) * 1000
                        return

                    if event_type == "chunk":
                        visible_content = content
                        if dedupe_reconnect_replay and reconnect_prefix:
                            if reconnect_prefix.startswith(visible_content):
                                reconnect_prefix = reconnect_prefix[len(visible_content) :]
                                visible_content = ""
                            elif visible_content.startswith(reconnect_prefix):
                                visible_content = visible_content[len(reconnect_prefix) :]
                                reconnect_prefix = ""
                            else:
                                reconnect_prefix = ""
                        if not visible_content:
                            deduped_chunk_count += 1
                            continue
                        total_content += visible_content
                        emitted_content += visible_content
                        yield_started_at = time.perf_counter()
                        yield {"type": "chunk", "content": visible_content, "metadata": metadata}
                        total_backpressure_wait_ms += (time.perf_counter() - yield_started_at) * 1000
                        continue

                    if event_type == "reasoning_chunk" and content:
                        yield_started_at = time.perf_counter()
                        yield {"type": "reasoning_chunk", "content": content, "metadata": metadata}
                        total_backpressure_wait_ms += (time.perf_counter() - yield_started_at) * 1000
                        continue

                    if event_type == "tool_call":
                        raw_tool_call_count += 1
                        signature = tool_call_signature_from_normalized(normalized)
                        if dedupe_reconnect_replay and reconnect_count > 0:
                            if signature in emitted_tool_signatures:
                                deduped_tool_call_count += 1
                                continue
                            emitted_tool_signatures.add(signature)
                        elif dedupe_reconnect_replay:
                            emitted_tool_signatures.add(signature)
                        yield_started_at = time.perf_counter()
                        yield {
                            "type": "tool_call",
                            "tool": normalized.tool_name,
                            "args": dict(normalized.tool_args),
                            "call_id": normalized.tool_call_id,
                            "metadata": metadata,
                            "iteration": turn_round,
                        }
                        total_backpressure_wait_ms += (time.perf_counter() - yield_started_at) * 1000
                        continue

                    if event_type == "tool_result":
                        yield_started_at = time.perf_counter()
                        yield {
                            "type": "tool_result",
                            "result": dict(normalized.tool_result),
                            "metadata": metadata,
                            "iteration": turn_round,
                        }
                        total_backpressure_wait_ms += (time.perf_counter() - yield_started_at) * 1000
                        continue

                    if event_type == "complete":
                        elapsed_ms = (time.perf_counter() - start_time) * 1000
                        metadata.update(_current_slo(elapsed_ms))
                        yield_started_at = time.perf_counter()
                        yield {"type": "complete", "content": content, "metadata": metadata, "iteration": turn_round}
                        total_backpressure_wait_ms += (time.perf_counter() - yield_started_at) * 1000
                        continue

                    if emit_unknown_events:
                        yield_started_at = time.perf_counter()
                        yield {"type": event_type or "unknown", "content": content, "metadata": metadata}
                        total_backpressure_wait_ms += (time.perf_counter() - yield_started_at) * 1000

            except asyncio.CancelledError as cancelled_exc:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                self._emit_call_error(
                    event_emitter=event_emitter,
                    role=role_id,
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    model=model,
                    error_category=ERROR_CATEGORY_CANCELLED,
                    error_message=str(cancelled_exc or "stream_cancelled"),
                    call_id=call_id,
                    elapsed_ms=elapsed_ms,
                    metadata=_build_stream_error_metadata(
                        elapsed_ms=elapsed_ms, error_type=type(cancelled_exc).__name__
                    ),
                )
                raise

            except (RuntimeError, ValueError) as stream_exc:
                error_message = str(stream_exc or "stream_exception")
                error_category = classify_error(error_message)
                if is_retryable_error(error_category) and reconnect_count < max_reconnects:
                    should_retry, retry_error_message, retry_error_category = True, error_message, error_category
                else:
                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                    self._emit_call_error(
                        event_emitter=event_emitter,
                        role=role_id,
                        run_id=run_id,
                        task_id=task_id,
                        attempt=attempt,
                        model=model,
                        error_category=error_category,
                        error_message=error_message,
                        call_id=call_id,
                        elapsed_ms=elapsed_ms,
                        metadata=_build_stream_error_metadata(
                            elapsed_ms=elapsed_ms, error_type=type(stream_exc).__name__
                        ),
                    )
                    yield {
                        "type": "error",
                        "error": error_message,
                        "metadata": _build_stream_error_metadata(
                            elapsed_ms=elapsed_ms, error_type=type(stream_exc).__name__
                        ),
                        "iteration": turn_round,
                    }
                    return

            if should_retry:
                reconnect_count += 1
                reconnect_prefix = emitted_content if dedupe_reconnect_replay else ""
                backoff_seconds = max(0.0, retry_backoff_seconds * reconnect_count)
                self._emit_call_retry(
                    event_emitter=event_emitter,
                    role=role_id,
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    model=model,
                    call_id=call_id,
                    retry_decision=f"stream_reconnect_{reconnect_count}",
                    backoff_seconds=backoff_seconds,
                    metadata={
                        "stream": True,
                        "error_category": retry_error_category,
                        "error_message": retry_error_message,
                        "stream_event_count": stream_event_count,
                        "stream_reconnect_count": reconnect_count,
                    },
                )
                if backoff_seconds > 0:
                    await asyncio.sleep(backoff_seconds)
                continue
            break

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        # BUG-03 fix: When the LLM emits only tool calls (no text chunks),
        # total_content is empty/whitespace.  Previously this produced
        # completion_tokens=0 and response_content="\n" in telemetry,
        # which misrepresents a successful tool-calling turn as empty.
        # Fix: estimate tokens from tool call count when text is absent,
        # and normalize response_content to "" for tool-only responses.
        _has_tool_calls = len(emitted_tool_signatures) > 0
        _effective_content = total_content if total_content.strip() else ""
        if _effective_content:
            completion_tokens = len(_effective_content) // 2
        elif _has_tool_calls:
            # Each tool call consumes ~50 tokens on average (name + args).
            # This is an estimate for telemetry purposes only.
            completion_tokens = len(emitted_tool_signatures) * 50
        else:
            completion_tokens = 0
        prompt_tokens_val = int(context_result.token_estimate) if context_result else 0
        total_tokens = prompt_tokens_val + completion_tokens
        yield {
            "type": "context_metadata",
            "context_tokens": prompt_tokens_val,
            "model_context_window": int(context_result.token_estimate) if context_result else 0,
            "usage": {
                "prompt_tokens": prompt_tokens_val,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
        }

        self._emit_call_end(
            event_emitter=event_emitter,
            role=role_id,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            model=model,
            call_id=call_id,
            completion_tokens=completion_tokens,
            prompt_tokens=prompt_tokens_val,
            context_tokens_after=context_result.token_estimate if context_result else None,
            compression_strategy=context_result.compression_strategy if context_result else None,
            response_content=_effective_content,
            tool_calls_count=len(emitted_tool_signatures),
            metadata={
                "stream": True,
                "native_tool_mode": active_native_tool_mode,
                "tool_protocol": active_tool_protocol,
                "native_tool_calling_fallback": False,
                "compression_applied": context_result.compression_applied if context_result else False,
                "turn_round": turn_round,
                **_current_slo(elapsed_ms),
            },
        )


__all__ = ["StreamEngine"]
