"""LLM Invoker Stream Engine - Streaming call execution logic.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import TYPE_CHECKING, Any

from .context_audit import (
    build_final_provider_request_snapshot,
    build_final_request_context_audit_for_request,
    enforce_final_request_evidence_coverage,
)
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


def _store_context_messages_accepts_provider_request(store_context_messages: Any) -> bool:
    try:
        signature = inspect.signature(store_context_messages)
    except (TypeError, ValueError):
        return True
    positional_count = 0
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            return True
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            positional_count += 1
    return positional_count >= 5


def _is_stream_cancel_requested(context: Any) -> bool:
    """Check if stream cancellation was requested."""
    # Inline to avoid circular import from helpers
    override = getattr(context, "context_override", None) if context else None
    if isinstance(override, dict) and override.get("stream_cancelled"):
        return True
    return bool(getattr(context, "stream_cancelled", False))


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


def _final_request_context_tokens(audit: dict[str, Any], fallback: int = 0) -> int:
    raw = audit.get("final_request_token_estimate")
    if raw is None:
        return max(0, int(fallback))
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return max(0, int(fallback))


def _context_snapshot_degraded_payload(exc: BaseException) -> dict[str, str]:
    """Return stable, JSON-safe evidence for a non-fatal context snapshot failure."""
    return {
        "code": "CONTEXT_STORE_WRITE_FAILED",
        "reason": "context_snapshot_store_failure",
        "message": str(exc)[:200],
        "exception_type": type(exc).__name__,
    }


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
        store_context_messages: Any | None = None,
    ) -> None:
        """Initialize stream engine.

        Args:
            workspace: Workspace path.
            get_executor: Callable that returns an executor with invoke_stream.
            allow_native_tool_text_fallback_fn: Ignored text-fallback hook.
            emit_call_start_event: Event emitter callable.
            emit_call_error_event: Event emitter callable.
            emit_call_end_event: Event emitter callable.
            emit_call_retry_event: Event emitter callable.
            store_context_messages: Optional async callable
                ``async (workspace, messages, trace_id, call_id, provider_request) -> str | None``
                that persists the post-compression chat messages to the
                runtime context store and returns the 24-char reference hash.
                The callable MUST be a coroutine — it is awaited so the
                underlying disk write runs in a worker thread and does not
                block the event loop. When provided, the hash is injected into
                ``prepared.ai_request.context`` BEFORE the call-start event so
                streamed invocations carry a non-empty ``context_snapshot_ref``
                in event metadata (mirrors the sync path's
                ``AIExecutor._store_context_messages`` wiring). Failing
                closed: any exception raised by the callable is swallowed and
                the stream proceeds with no snapshot ref so a misbehaving
                store never blocks the LLM call.
        """
        self.workspace = workspace
        self._get_executor = get_executor
        _ = allow_native_tool_text_fallback_fn
        self._emit_call_start = emit_call_start_event
        self._emit_call_error = emit_call_error_event
        self._emit_call_end = emit_call_end_event
        self._emit_call_retry = emit_call_retry_event
        self._store_context_messages = store_context_messages

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
        total_content = ""
        emitted_content = ""
        reconnect_prefix = ""
        emitted_tool_signatures: set[str] = set()
        active_request = prepared.ai_request
        request_context = getattr(active_request, "context", None)
        if isinstance(request_context, dict):
            request_context.pop("context_snapshot_ref", None)
            request_context.pop("contextSnapshotRef", None)
            request_context.pop("context_snapshot_degraded", None)
            request_context.pop("contextSnapshotDegraded", None)
        active_native_tool_mode = prepared.native_tool_mode
        active_tool_protocol = (
            "structured_native_tools" if active_native_tool_mode == "native_tools_streaming" else "none"
        )
        fallback_stream_completed = False
        prepared_context_os_audit = getattr(prepared, "context_os_audit", None)
        context_os_audit = dict(prepared_context_os_audit) if isinstance(prepared_context_os_audit, dict) else {}
        provider_usage: dict[str, Any] | None = None
        context_result = prepared.context_result
        prompt_tokens = context_result.token_estimate if context_result else 0

        def _with_context_os_audit(payload: dict[str, Any]) -> dict[str, Any]:
            result = dict(payload)
            if context_os_audit:
                result["context_os_audit"] = dict(context_os_audit)
            return result

        def _with_final_request_context_audit(payload: dict[str, Any], request: Any) -> dict[str, Any]:
            audit = build_final_request_context_audit_for_request(
                ai_request=request,
                prepared=prepared,
                profile=profile,
            )
            final_tokens = _final_request_context_tokens(audit, prompt_tokens)
            result = dict(payload)
            result["final_request_context_audit"] = audit
            result["context_tokens_after"] = final_tokens
            result["contextTokens"] = final_tokens
            return result

        def _extract_context_snapshot_ref(request: Any) -> str | None:
            """Extract context_snapshot_ref from an AIRequest's context dict, if present."""
            ctx = getattr(request, "context", None)
            if isinstance(ctx, dict):
                ref = ctx.get("context_snapshot_ref")
                if isinstance(ref, str) and ref.strip():
                    return ref.strip()
            return None

        def _extract_context_snapshot_degraded(request: Any) -> dict[str, Any] | None:
            """Extract structured context snapshot degradation evidence, if present."""
            ctx = getattr(request, "context", None)
            if isinstance(ctx, dict):
                degraded = ctx.get("context_snapshot_degraded")
                if isinstance(degraded, dict):
                    return dict(degraded)
            return None

        def _with_context_snapshot_diagnostics(payload: dict[str, Any], request: Any) -> dict[str, Any]:
            result = dict(payload)
            degraded = _extract_context_snapshot_degraded(request)
            if degraded:
                result["context_snapshot_degraded"] = degraded
                result["context_snapshot_degraded_reason"] = degraded.get("reason") or degraded.get("code")
            return result

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
                "context_snapshot_ref": _extract_context_snapshot_ref(active_request),
            }
            payload = _with_context_snapshot_diagnostics(payload, active_request)
            payload.update(_current_slo(elapsed_ms))
            if error_type:
                payload["error_type"] = error_type
            return _with_context_os_audit(_with_final_request_context_audit(payload, active_request))

        final_context_audit = build_final_request_context_audit_for_request(
            ai_request=active_request,
            prepared=prepared,
            profile=profile,
        )
        final_context_tokens = _final_request_context_tokens(final_context_audit, prompt_tokens)
        enforce_final_request_evidence_coverage(
            ai_request=active_request,
            audit=final_context_audit,
        )

        # Phase 1 critical fix (CRITICAL FIX_SCHEMA): stream path was never
        # persisting context snapshots, so the per-LLM context viewer in
        # RoleInternalPanel stayed empty for every streamed call. Mirror the
        # sync path (executor._store_context_messages at line 440) by hashing
        # the prepared messages BEFORE call_start. The hash is written into
        # prepared.ai_request.context so the existing _extract_context_snapshot_ref
        # below resolves it for the event metadata.
        #
        # Performance hardening (HIGH #2): ``_store_context_messages`` is an
        # async coroutine that runs the disk write in the default thread pool
        # (asyncio.to_thread). Awaing it here keeps the event loop responsive
        # while the on-disk file lands — the hash is still guaranteed durable
        # by the time the call_start event emits.
        if self._store_context_messages is not None:
            snapshot_messages: list[Any] = list(getattr(prepared, "messages", []) or [])
            if snapshot_messages:
                try:
                    provider_request = build_final_provider_request_snapshot(
                        ai_request=prepared.ai_request,
                        prepared=prepared,
                        profile=profile,
                    )
                    if _store_context_messages_accepts_provider_request(self._store_context_messages):
                        context_store_hash = await self._store_context_messages(
                            self.workspace,
                            snapshot_messages,
                            run_id,
                            call_id,
                            provider_request,
                        )
                    else:
                        context_store_hash = await self._store_context_messages(
                            self.workspace,
                            snapshot_messages,
                            run_id,
                            call_id,
                        )
                except (RuntimeError, ValueError, TypeError, OSError) as exc:
                    logger.warning(
                        "[StreamEngine] context_snapshot store failed (non-fatal): %s",
                        exc,
                    )
                    context_store_hash = None
                    request_context = getattr(prepared.ai_request, "context", None)
                    if isinstance(request_context, dict):
                        request_context["context_snapshot_degraded"] = _context_snapshot_degraded_payload(exc)
                if context_store_hash:
                    request_context = getattr(prepared.ai_request, "context", None)
                    if isinstance(request_context, dict):
                        request_context["context_snapshot_ref"] = context_store_hash

        self._emit_call_start(
            event_emitter=event_emitter,
            role=role_id,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            model=model,
            prompt_tokens=prompt_tokens,
            call_id=call_id,
            context_tokens_before=final_context_tokens,
            compression_strategy=context_result.compression_strategy if context_result else None,
            messages=prepared.messages,
            metadata=_with_context_os_audit(
                _with_context_snapshot_diagnostics(
                    {
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
                        "context_snapshot_ref": _extract_context_snapshot_ref(prepared.ai_request),
                        "context_tokens_after": final_context_tokens,
                        "contextTokens": final_context_tokens,
                        "final_request_context_audit": final_context_audit,
                    },
                    prepared.ai_request,
                )
            ),
        )

        if prepared.native_tool_mode == "native_tools_unavailable":
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

        while not fallback_stream_completed:
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
                        usage_from_metadata = _normalize_provider_usage(
                            metadata.get("usage")
                        ) or _normalize_provider_usage(metadata)
                        if usage_from_metadata is not None:
                            provider_usage = usage_from_metadata
                            metadata.setdefault("usage", usage_from_metadata)
                            metadata.setdefault("usage_source", "provider")
                        metadata.update(_current_slo(elapsed_ms))
                        metadata = _with_context_os_audit(
                            _with_context_snapshot_diagnostics(
                                _with_final_request_context_audit(metadata, active_request),
                                active_request,
                            )
                        )
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
                retry_metadata = _with_context_os_audit(
                    _with_context_snapshot_diagnostics(
                        _with_final_request_context_audit(
                            {
                                "stream": True,
                                "error_category": retry_error_category,
                                "error_message": retry_error_message,
                                "stream_event_count": stream_event_count,
                                "stream_reconnect_count": reconnect_count,
                            },
                            active_request,
                        ),
                        active_request,
                    )
                )
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
                    metadata=retry_metadata,
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
        estimated_completion_tokens = 0
        if _effective_content:
            estimated_completion_tokens = len(_effective_content) // 2
        elif _has_tool_calls:
            # Each tool call consumes ~50 tokens on average (name + args).
            # This is an estimate for telemetry purposes only.
            estimated_completion_tokens = len(emitted_tool_signatures) * 50
        prompt_tokens_val = int(context_result.token_estimate) if context_result else 0
        if provider_usage is not None:
            provider_prompt_tokens = int(provider_usage["prompt_tokens"])
            provider_completion_tokens = int(provider_usage["completion_tokens"])
            provider_total_tokens = int(provider_usage["total_tokens"])
            prompt_tokens_val = provider_prompt_tokens if provider_prompt_tokens > 0 else prompt_tokens_val
            completion_tokens = (
                provider_completion_tokens if provider_completion_tokens > 0 else estimated_completion_tokens
            )
            total_tokens = provider_total_tokens if provider_total_tokens > 0 else prompt_tokens_val + completion_tokens
        else:
            completion_tokens = estimated_completion_tokens
            total_tokens = prompt_tokens_val + completion_tokens
        usage_payload = provider_usage or {
            "prompt_tokens": prompt_tokens_val,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        final_context_audit = build_final_request_context_audit_for_request(
            ai_request=active_request,
            prepared=prepared,
            profile=profile,
        )
        final_context_tokens = _final_request_context_tokens(final_context_audit, prompt_tokens_val)
        yield {
            "type": "context_metadata",
            "context_tokens": final_context_tokens,
            "model_context_window": int(final_context_audit.get("context_window_tokens") or 0),
            "context_os_audit": dict(context_os_audit),
            "final_request_context_audit": final_context_audit,
            "usage": usage_payload,
            "usage_source": "provider" if provider_usage is not None else "estimate",
        }

        call_end_metadata: dict[str, Any] = {
            "stream": True,
            "native_tool_mode": active_native_tool_mode,
            "tool_protocol": active_tool_protocol,
            "native_tool_calling_fallback": False,
            "compression_applied": context_result.compression_applied if context_result else False,
            "turn_round": turn_round,
            "context_snapshot_ref": _extract_context_snapshot_ref(active_request),
            "context_tokens_after": final_context_tokens,
            "contextTokens": final_context_tokens,
            "final_request_context_audit": final_context_audit,
            **_current_slo(elapsed_ms),
        }
        call_end_metadata = _with_context_snapshot_diagnostics(call_end_metadata, active_request)
        if provider_usage is not None:
            call_end_metadata["usage"] = provider_usage
            call_end_metadata["usage_source"] = "provider"

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
            context_tokens_after=final_context_tokens,
            compression_strategy=context_result.compression_strategy if context_result else None,
            response_content=_effective_content,
            tool_calls_count=len(emitted_tool_signatures),
            metadata=_with_context_os_audit(call_end_metadata),
        )


__all__ = ["StreamEngine"]
