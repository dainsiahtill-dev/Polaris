"""Polaris AI Platform - Stream Executor Core

Unified streaming engine providing LLM streaming invocation capability.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.toolkit.parsers.textual_tool_recovery import (
    has_textual_tool_calls,
    recover_textual_tool_calls,
    strip_textual_tool_call_markers,
)
from polaris.kernelone.telemetry import debug_stream as _debug_stream_module
from polaris.kernelone.trace import get_trace_id

from ... import providers as _providers_module
from ...provider_adapters.factory import get_adapter
from ...providers.base_provider import THINKING_PREFIX
from ...providers.stream_thinking_parser import ChunkKind, StreamThinkingParser
from .._executor_base import (
    build_final_request_observability_fields,
    build_final_request_trace_refs,
    build_invoke_config,
    build_safe_token_budget_payload,
    clamp_output_tokens_to_window,
    coerce_required_flag,
    estimate_payload_overhead_tokens,
    get_provider_config,
    provider_type_policy_error,
    resolve_provider_model,
    resolve_requested_output_tokens,
    safe_observability_payload,
)
from ..contracts import (
    AIRequest,
    AIResponse,
    AIStreamEvent,
    AIStreamGenerator,
    ErrorCategory,
    ModelSpec,
    StreamEventType,
    Usage,
)
from ..model_catalog import ModelCatalog
from ..normalizer import ResponseNormalizer
from ..prompt_budget import TokenBudgetManager, compress_chat_messages_to_budget
from .config import DEFAULT_MAX_COLLECTED_STREAM_CHARS, StreamConfig
from .result_tracker import _StreamResultTracker
from .tool_accumulator import (
    _normalize_arguments,
    _provider_supports_structured_stream,
    _tool_accumulator_key,
    _ToolCallAccumulator,
)

if TYPE_CHECKING:
    from ..telemetry import TelemetryCollector

logger = logging.getLogger(__name__)

_STREAM_OUTPUT_TOKEN_CHAR_MULTIPLIER = 16


def _append_bounded_stream_text(current: str, addition: str, *, limit_chars: int, label: str) -> str:
    if not addition:
        return current
    next_len = len(current) + len(addition)
    if limit_chars > 0 and next_len > limit_chars:
        msg = f"stream {label} exceeded accumulation limit ({next_len}>{limit_chars} chars)"
        raise RuntimeError(msg)
    return current + addition


def _stream_collection_limit_from_env() -> int:
    try:
        parsed = int(os.environ.get("KERNELONE_LLM_STREAM_MAX_COLLECTED_CHARS", DEFAULT_MAX_COLLECTED_STREAM_CHARS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_COLLECTED_STREAM_CHARS
    return parsed if parsed > 0 else DEFAULT_MAX_COLLECTED_STREAM_CHARS


def normalize_stream_usage(raw_usage: dict[str, Any] | None) -> Usage:
    """Normalize stream usage payload with cached token support."""
    payload = dict(raw_usage or {})
    cache_creation_tokens = int(payload.get("cache_creation_input_tokens") or 0)
    cache_read_tokens = int(payload.get("cache_read_input_tokens") or 0)
    cached_tokens = int(payload.get("cached_tokens") or payload.get("cached_prompt_tokens") or cache_read_tokens or 0)
    prompt_tokens = int(
        payload.get("prompt_tokens")
        or (int(payload.get("input_tokens") or 0) + cache_creation_tokens + cache_read_tokens)
        or 0
    )
    completion_tokens = int(payload.get("completion_tokens") or payload.get("output_tokens") or 0)
    total_tokens = int(payload.get("total_tokens") or 0)
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    return Usage(
        cached_tokens=max(0, cached_tokens),
        prompt_tokens=max(0, prompt_tokens),
        completion_tokens=max(0, completion_tokens),
        total_tokens=max(0, total_tokens),
        estimated=False,
    )


def _requested_tool_names(invoke_cfg: dict[str, Any]) -> set[str]:
    """Extract the set of tool names actually sent in the request (lowercased).

    Used to gate textual tool-call recovery: a recovered call is only accepted
    when its name is in the tool set the request advertised, so recovery cannot
    fabricate calls for tools the model was never offered.
    """
    tools = invoke_cfg.get("tools")
    names: set[str] = set()
    if not isinstance(tools, list):
        return names
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        name = ""
        if isinstance(function, dict):
            name = str(function.get("name") or "").strip()
        if not name:
            name = str(tool.get("name") or "").strip()
        if name:
            names.add(name.lower())
    return names


class StreamExecutor:
    """Unified streaming executor."""

    def __init__(
        self,
        workspace: str | None = None,
        telemetry: TelemetryCollector | None = None,
        model_catalog: ModelCatalog | None = None,
        token_budget: TokenBudgetManager | None = None,
        config: StreamConfig | None = None,
        final_request_receipt_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.workspace = workspace
        self.telemetry = telemetry
        self.model_catalog = model_catalog or ModelCatalog(workspace=workspace or ".")
        self.token_budget = token_budget or TokenBudgetManager()
        self._config = config or StreamConfig.from_env()
        self.final_request_receipt_sink = final_request_receipt_sink

    @property
    def config(self) -> StreamConfig:
        return self._config

    @property
    def timeout(self) -> float:
        return self._config.timeout_sec

    @property
    def max_pending_calls(self) -> int:
        return self._config.max_pending_calls

    def _stream_collection_limit(self, max_tokens: int) -> int:
        configured_limit = self._config.max_collected_chars
        if max_tokens <= 0:
            return configured_limit
        token_derived_limit = max(4096, max_tokens * _STREAM_OUTPUT_TOKEN_CHAR_MULTIPLIER)
        return min(configured_limit, token_derived_limit)

    def _resolve_provider_model(self, request: AIRequest) -> tuple[str | None, str | None]:
        return resolve_provider_model(
            provider_id=request.provider_id,
            model=request.model,
            role=request.role,
            logger_prefix="[stream-executor]",
        )

    def _get_provider_config(self, provider_id: str) -> dict[str, Any]:
        return get_provider_config(
            workspace=self.workspace,
            provider_id=provider_id,
            logger_prefix="[stream-executor]",
        )

    def _build_invoke_config(self, provider_cfg: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        return build_invoke_config(provider_cfg, options, streaming=True)

    def _resolve_requested_output_tokens(
        self, options: dict[str, Any], invoke_cfg: dict[str, Any], model_spec: ModelSpec
    ) -> int:
        return resolve_requested_output_tokens(options, invoke_cfg, model_spec)

    def _record_final_request_receipt(
        self,
        *,
        request: AIRequest,
        trace_id: str,
        model_spec: ModelSpec,
        invoke_cfg: dict[str, Any],
        budget_decision: Any,
        requested_output_tokens: int,
        max_tokens_before_clamp: int,
        payload_overhead: int,
        prompt_input: Any,
        chat_messages_before: Any,
        chat_messages_after: list[Any] | None,
        clamp_prompt_text: str,
    ) -> None:
        context = request.context if isinstance(request.context, dict) else {}
        receipt_required = coerce_required_flag(context.get("cognitive_runtime_required")) or coerce_required_flag(
            context.get("context_os_expected")
        )
        tools = invoke_cfg.get("tools")
        tool_count = len(tools) if isinstance(tools, list) else 0
        chat_count_before = len(chat_messages_before) if isinstance(chat_messages_before, list) else 0
        chat_count_after = len(chat_messages_after) if isinstance(chat_messages_after, list) else 0
        chat_messages_compressed = self._normalize_chat_messages_for_receipt(
            chat_messages_before
        ) != self._normalize_chat_messages_for_receipt(chat_messages_after)
        input_sha256 = self._sha256_text(prompt_input)
        effective_prompt_sha256 = self._sha256_text(clamp_prompt_text)
        tool_schema_sha256 = self._sha256_json(tools) if tool_count else ""
        token_budget = build_safe_token_budget_payload(budget_decision)
        observability_fields = build_final_request_observability_fields(
            context=context,
            trace_id=trace_id,
            provider_id=str(model_spec.provider_id or ""),
            model=str(model_spec.model or ""),
            final_max_tokens=self._coerce_int(invoke_cfg.get("max_tokens")),
            token_budget=token_budget,
            input_sha256=input_sha256,
            effective_prompt_sha256=effective_prompt_sha256,
        )
        if isinstance(request.context, dict):
            request.context.update(
                {
                    key: value
                    for key, value in observability_fields.items()
                    if key
                    in {
                        "projection_id",
                        "context_projection_id",
                        "context_result_id",
                        "final_request_receipt_id",
                        "provider_request_id",
                        "telemetry_trace_id",
                        "budget_admission_id",
                    }
                }
            )
        sink = self.final_request_receipt_sink
        if sink is None:
            if receipt_required:
                raise RuntimeError("final request receipt sink required but unavailable")
            return
        receipt = {
            "receipt_type": "contextos.final_request",
            "payload": {
                "schema_version": 1,
                "source": "kernelone.llm.engine.stream_executor",
                "stream": True,
                "trace_id": trace_id,
                "run_id": self._non_empty_str(context.get("run_id")),
                "task_id": self._non_empty_str(context.get("task_id")),
                "session_id": self._non_empty_str(context.get("session_id")),
                "cognitive_runtime_required": coerce_required_flag(context.get("cognitive_runtime_required")),
                "context_os_expected": coerce_required_flag(context.get("context_os_expected")),
                **observability_fields,
                "role": request.role,
                "task_type": request.task_type.value,
                "provider_id": model_spec.provider_id,
                "provider_type": model_spec.provider_type,
                "model": model_spec.model,
                "model_window_tokens": int(model_spec.max_context_tokens or 0),
                "model_output_limit_tokens": int(model_spec.max_output_tokens or 0),
                "requested_output_tokens": requested_output_tokens,
                "max_tokens_before_clamp": max_tokens_before_clamp,
                "final_max_tokens": self._coerce_int(invoke_cfg.get("max_tokens")),
                "payload_overhead_tokens": int(payload_overhead or 0),
                "token_budget": token_budget,
                "chat_message_count_before": chat_count_before,
                "chat_message_count_after": chat_count_after,
                "chat_messages_compressed": chat_messages_compressed,
                "tool_count": tool_count,
                "native_tool_mode": self._non_empty_str(context.get("native_tool_mode")),
                "input_sha256": input_sha256,
                "effective_prompt_sha256": effective_prompt_sha256,
                "tool_schema_sha256": tool_schema_sha256,
            },
            "trace_refs": build_final_request_trace_refs(
                trace_id=trace_id,
                observability_fields=observability_fields,
            ),
        }
        try:
            sink(receipt)
        except Exception as exc:
            logger.warning("[stream-executor] final request receipt sink failed: %s", exc)
            if receipt_required:
                raise

    @staticmethod
    def _normalize_chat_messages_for_receipt(value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "role": str(item.get("role") or ""),
                    "content": str(item.get("content") or ""),
                }
            )
        return normalized

    @staticmethod
    def _sha256_text(value: Any) -> str:
        return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()

    @staticmethod
    def _sha256_json(value: Any) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _non_empty_str(value: Any) -> str:
        text = str(value or "").strip()
        safe_text = safe_observability_payload(text)
        text = str(safe_text or "").strip()
        return text

    @staticmethod
    def _coerce_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _traceability_metadata(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        keys = (
            "projection_id",
            "context_projection_id",
            "context_result_id",
            "final_request_receipt_id",
            "provider_request_id",
            "telemetry_trace_id",
            "budget_admission_id",
        )
        return {key: value.get(key) for key in keys if value.get(key)}

    def _create_stream_result_tracker(self, trace_id: str) -> _StreamResultTracker:
        return _StreamResultTracker(trace_id=trace_id)

    def _enforce_pending_tool_calls_limit(self, pending_tool_calls: dict[str, _ToolCallAccumulator]) -> None:
        max_pending = self._config.max_pending_calls
        if max_pending > 0 and len(pending_tool_calls) >= max_pending:
            oldest_key = next(iter(pending_tool_calls))
            logger.error(
                "[stream-executor] Too many pending tool calls (%d), dropping oldest: %s",
                len(pending_tool_calls),
                oldest_key,
            )
            del pending_tool_calls[oldest_key]

    def _build_stream_tool_payload(
        self,
        accumulator: _ToolCallAccumulator,
        *,
        allow_provisional_empty_arguments: bool = False,
    ) -> dict[str, Any] | None:
        tool_name = str(accumulator.tool_name or "").strip()
        if not tool_name:
            return None

        arguments: dict[str, Any] | None = None
        if accumulator.arguments_buffer:
            parsed_arguments, parsed_complete = _normalize_arguments(accumulator.arguments_buffer)
            if parsed_complete:
                arguments = parsed_arguments
        elif isinstance(accumulator.explicit_arguments, dict):
            if accumulator.explicit_arguments_provisional and not allow_provisional_empty_arguments:
                return None
            arguments = dict(accumulator.explicit_arguments)

        if arguments is None:
            return None

        return {
            "tool": tool_name,
            "arguments": arguments,
            "call_id": accumulator.call_id,
            "provider_meta": dict(accumulator.provider_meta),
        }

    def _finalize_stream_tool_call(self, accumulator: _ToolCallAccumulator) -> dict[str, Any] | None:
        # End-of-stream flush is the ONLY emission point for legitimately
        # empty-argument tool calls (ADR-0090 I1): mid-stream an empty dict is
        # always a provisional placeholder awaiting argument deltas.
        payload = self._build_stream_tool_payload(accumulator, allow_provisional_empty_arguments=True)
        if payload is None and accumulator.tool_name.strip() and accumulator.arguments_buffer.strip():
            # Last resort at stream end (never mid-stream): the buffer is final,
            # so a bounded lenient repair of almost-valid JSON is safe (ADR-0090).
            from polaris.kernelone.llm.toolkit.parsers.lenient_json import parse_lenient_json_object

            repaired, was_repaired = parse_lenient_json_object(accumulator.arguments_buffer)
            if isinstance(repaired, dict) and repaired:
                logger.info(
                    "[stream-executor] lenient JSON repair recovered tool call: tool=%s chars=%d",
                    accumulator.tool_name.strip(),
                    len(accumulator.arguments_buffer),
                )
                payload = {
                    "tool": accumulator.tool_name.strip(),
                    "arguments": repaired,
                    "call_id": accumulator.call_id,
                    "provider_meta": {
                        **dict(accumulator.provider_meta),
                        "lenient_repair_applied": was_repaired,
                    },
                }
        if payload is None:
            return None
        signature = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if signature == accumulator.emitted_signature:
            return None
        accumulator.emitted_signature = signature
        return payload

    def _accumulate_stream_tool_call(
        self,
        pending_tool_calls: dict[str, _ToolCallAccumulator],
        tool_call: dict[str, Any],
        *,
        ordinal: int,
        provider_type: str,
    ) -> dict[str, Any] | None:
        key = _tool_accumulator_key(tool_call, ordinal)
        self._enforce_pending_tool_calls_limit(pending_tool_calls)
        accumulator = pending_tool_calls.setdefault(key, _ToolCallAccumulator())

        tool_name = str(tool_call.get("tool") or "").strip()
        if tool_name:
            accumulator.tool_name = tool_name

        call_id = str(tool_call.get("call_id") or "").strip()
        if call_id:
            accumulator.call_id = call_id

        arguments_text = str(tool_call.get("arguments_text") or "")
        arguments_complete = bool(tool_call.get("arguments_complete", False))
        raw_arguments = tool_call.get("arguments")
        explicit_arguments, explicit_complete = _normalize_arguments(raw_arguments)
        content_block_index = tool_call.get("content_block_index")
        arguments_text_is_empty_placeholder = arguments_text.strip() in {"", "{}"}
        anthropic_placeholder = isinstance(content_block_index, int) and arguments_text_is_empty_placeholder
        if explicit_complete and not explicit_arguments and anthropic_placeholder:
            arguments_text = ""
            arguments_complete = False
            arguments_text_is_empty_placeholder = True

        if arguments_text:
            if arguments_complete and not accumulator.arguments_buffer:
                accumulator.arguments_buffer = arguments_text
            elif arguments_complete and arguments_text.lstrip().startswith("{"):
                parsed_next_arguments, parsed_next_complete = _normalize_arguments(arguments_text)
                if parsed_next_complete and parsed_next_arguments:
                    accumulator.arguments_buffer = arguments_text
            else:
                accumulator.arguments_buffer += arguments_text

        if explicit_complete:
            accumulator.explicit_arguments = explicit_arguments
            # ADR-0090 I1: an empty explicit-arguments dict is provisional for ALL
            # providers, not only Anthropic content-block placeholders. OpenAI-compat
            # servers (vLLM/qwen) open a tool call with arguments "" / {} before the
            # argument deltas arrive; emitting that placeholder mid-stream caused the
            # partial call to be executed alongside the completed one.
            accumulator.explicit_arguments_provisional = not explicit_arguments
            if arguments_complete and explicit_arguments and not accumulator.arguments_buffer:
                accumulator.arguments_buffer = json.dumps(explicit_arguments, ensure_ascii=False)

        max_tool_argument_chars = self._config.max_tool_argument_chars
        if max_tool_argument_chars > 0 and len(accumulator.arguments_buffer) > max_tool_argument_chars:
            pending_tool_calls.pop(key, None)
            msg = (
                "stream tool arguments exceeded accumulation limit "
                f"({len(accumulator.arguments_buffer)}>{max_tool_argument_chars} chars)"
            )
            raise RuntimeError(msg)

        accumulator.provider_meta = {
            "provider": provider_type,
            "index": tool_call.get("index"),
            "content_block_index": tool_call.get("content_block_index"),
        }

        payload = self._build_stream_tool_payload(accumulator)
        if payload is None:
            return None

        signature = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if signature == accumulator.emitted_signature:
            return None
        accumulator.emitted_signature = signature
        return payload

    async def invoke_stream_with_fallback(
        self, request: AIRequest, fallback_fn: Any | None = None
    ) -> AIStreamGenerator:
        """Streaming call with fallback."""
        try:
            async for event in self.invoke_stream(request):
                yield event
        except (asyncio.CancelledError, asyncio.TimeoutError):
            raise
        except (AttributeError, TypeError, RuntimeError) as exc:
            logger.exception("[stream-executor] stream failed, using fallback")
            if fallback_fn is not None:
                if asyncio.iscoroutinefunction(fallback_fn):
                    result = await fallback_fn()
                else:
                    result = fallback_fn()
                yield AIStreamEvent.complete({"fallback_result": result})
            else:
                yield AIStreamEvent.error_event(f"Stream failed and no fallback: {exc}")

    async def invoke_stream(self, request: AIRequest) -> AIStreamGenerator:
        """Execute streaming AI call."""
        trace_id = get_trace_id()
        start_time = time.time()

        if self.telemetry:
            try:
                self.telemetry.record_invoke_start(trace_id, request)
            except (AttributeError, TypeError, RuntimeError) as exc:
                logger.warning("[stream-executor] telemetry record_invoke_start failed: %s", exc)
            except (ConnectionError, TimeoutError, RuntimeError) as exc:  # noqa: B025
                logger.warning("[stream-executor] telemetry record_invoke_start failed (unexpected): %s", exc)

        provider_id, model = self._resolve_provider_model(request)
        if not provider_id or not model:
            try:
                _debug_stream_module.emit_debug_event(
                    category="llm_call",
                    label="invoke_error",
                    source="polaris.kernelone.llm.stream_executor",
                    payload={"trace_id": trace_id, "error": "Provider or model not resolved"},
                )
            except (TypeError, AttributeError, RuntimeError) as exc:
                logger.debug("[stream-executor] emit_debug_event (invoke_error) failed: %s", exc)
            except (ConnectionError, TimeoutError, RuntimeError) as exc:  # noqa: B025
                logger.debug("[stream-executor] emit_debug_event (invoke_error) failed (unexpected): %s", exc)
            yield AIStreamEvent.error_event("Provider or model not resolved")
            return

        request.provider_id = provider_id
        request.model = model

        try:
            _debug_stream_module.emit_debug_event(
                category="llm_call",
                label="invoke_start",
                source="polaris.kernelone.llm.stream_executor",
                payload={
                    "trace_id": trace_id,
                    "role": str(request.role or ""),
                    "provider_id": provider_id,
                    "model": model,
                    "input_length": len(str(request.input or "")),
                },
            )
        except (TypeError, AttributeError, RuntimeError) as exc:
            logger.debug("[stream-executor] emit_debug_event (invoke_start) failed: %s", exc)
        except (ConnectionError, TimeoutError, RuntimeError) as exc:  # noqa: B025
            logger.debug("[stream-executor] emit_debug_event (invoke_start) failed (unexpected): %s", exc)

        provider_cfg = self._get_provider_config(provider_id)
        provider_type = str(provider_cfg.get("type") or "").strip().lower()

        if not provider_type:
            yield AIStreamEvent.error_event(f"Provider type not found for {provider_id}")
            return
        provider_policy_error = provider_type_policy_error(provider_type, request.options)
        if provider_policy_error:
            yield AIStreamEvent.error_event(provider_policy_error)
            return

        provider_instance = _providers_module.get_provider_manager().get_provider_instance(provider_type)
        if provider_instance is None:
            yield AIStreamEvent.error_event(f"Provider not found: {provider_type}")
            return

        invoke_cfg = self._build_invoke_config(provider_cfg, request.options)
        invoke_cfg["stream"] = True
        model_spec = self.model_catalog.resolve(provider_id, model, provider_cfg)
        requested_output_tokens = self._resolve_requested_output_tokens(request.options, invoke_cfg, model_spec)
        if requested_output_tokens > 0:
            invoke_cfg["max_tokens"] = requested_output_tokens

        early_chat_messages = request.context.get("chat_messages") if isinstance(request.context, dict) else None
        payload_overhead = estimate_payload_overhead_tokens(invoke_cfg, early_chat_messages)

        budget_decision = self.token_budget.enforce(
            request.input,
            model_spec,
            requested_output_tokens=requested_output_tokens,
            workspace=self.workspace,
            role=request.role,
            overhead_tokens=payload_overhead,
        )
        if not budget_decision.allowed:
            yield AIStreamEvent.error_event(budget_decision.error or "Prompt exceeds model context budget")
            return
        safe_token_budget = build_safe_token_budget_payload(budget_decision)

        prompt_input = request.input
        clamp_prompt_text = prompt_input if isinstance(prompt_input, str) else str(prompt_input)
        # ADR-0090 W1.5: pass the structured message array through so chat
        # providers can preserve real role anchoring instead of flattening
        # the whole transcript into one user message.
        chat_messages = request.context.get("chat_messages") if isinstance(request.context, dict) else None
        if budget_decision.compression_applied and budget_decision.compression is not None:
            prompt_input = budget_decision.compression.compressed_input
            clamp_prompt_text = prompt_input if isinstance(prompt_input, str) else str(prompt_input)
            request.context["token_budget"] = safe_token_budget

        # ADR-0090 W1.5b/d: always budget the structured array independently
        # from the flattened prompt so provider-bound chat messages cannot
        # exceed the resolved window just because request.input fits.
        effective_chat_messages: list[Any] | None = None
        if isinstance(chat_messages, list) and chat_messages:
            budgeted_chat_messages = compress_chat_messages_to_budget(
                chat_messages,
                budget_decision.allowed_prompt_tokens,
            )
            if budgeted_chat_messages is not None:
                effective_chat_messages = budgeted_chat_messages
                invoke_cfg["chat_messages"] = effective_chat_messages
                clamp_prompt_text = "\n".join(str(m.get("content") or "") for m in effective_chat_messages)

        max_tokens_before_clamp = self._coerce_int(invoke_cfg.get("max_tokens"))
        clamp_output_tokens_to_window(
            invoke_cfg,
            model_spec,
            clamp_prompt_text,
            overhead_tokens=payload_overhead,
            logger_prefix="[stream-executor]",
        )
        stream_collection_limit = self._stream_collection_limit(self._coerce_int(invoke_cfg.get("max_tokens")))
        try:
            self._record_final_request_receipt(
                request=request,
                trace_id=trace_id,
                model_spec=model_spec,
                invoke_cfg=invoke_cfg,
                budget_decision=budget_decision,
                requested_output_tokens=requested_output_tokens,
                max_tokens_before_clamp=max_tokens_before_clamp,
                payload_overhead=payload_overhead,
                prompt_input=prompt_input,
                chat_messages_before=chat_messages,
                chat_messages_after=effective_chat_messages,
                clamp_prompt_text=clamp_prompt_text,
            )
        except RuntimeError as exc:
            yield AIStreamEvent.error_event(str(exc))
            return

        collected_output = ""
        collected_reasoning = ""
        chunk_count = 0
        emitted_tool_calls: list[dict[str, Any]] = []
        stream_usage: Usage | None = None

        stream_overall_timeout = self._config.timeout_sec
        stream_start_time = time.time()

        def _check_overall_timeout() -> None:
            elapsed = time.time() - stream_start_time
            if elapsed > stream_overall_timeout:
                raise asyncio.TimeoutError(f"Stream overall timeout after {stream_overall_timeout}s")

        # Zombie-stream hardening (factory-bench L1 live capture, 2026-06-12):
        # raising out of the `async for` (overall-timeout, caller errors)
        # ABANDONS the provider's async generator — its aiohttp response never
        # exits the `async with`, the connection stays open, and vLLM keeps
        # generating to a socket nobody reads. Each zombie halves the shared
        # decode throughput, cascading later calls into timeouts/empty output.
        # Explicit aclose() on every exit throws GeneratorExit through the
        # yield points so the provider unwinds and the server aborts.
        upstream_stream: Any | None = None
        try:
            if _provider_supports_structured_stream(provider_instance):
                upstream_stream = self._invoke_structured_stream(
                    provider_instance=provider_instance,
                    provider_type=provider_type,
                    prompt_input=prompt_input,
                    model=model,
                    invoke_cfg=invoke_cfg,
                    trace_id=trace_id,
                )
                async for event in upstream_stream:
                    _check_overall_timeout()
                    if (
                        event.type == StreamEventType.COMPLETE
                        and event.meta
                        and event.meta.get("_internal_provider_usage")
                    ):
                        stream_usage = normalize_stream_usage(event.meta.get("usage"))
                        continue
                    chunk_count += 1
                    if event.type == StreamEventType.CHUNK and event.chunk:
                        collected_output = _append_bounded_stream_text(
                            collected_output,
                            event.chunk,
                            limit_chars=stream_collection_limit,
                            label="output",
                        )
                    elif event.type == StreamEventType.REASONING_CHUNK and event.reasoning:
                        collected_reasoning = _append_bounded_stream_text(
                            collected_reasoning,
                            event.reasoning,
                            limit_chars=stream_collection_limit,
                            label="reasoning",
                        )
                    elif event.type == StreamEventType.TOOL_CALL and event.tool_call:
                        emitted_tool_calls.append(dict(event.tool_call))
                    elif event.type == StreamEventType.ERROR:
                        if self.telemetry:
                            try:
                                self.telemetry.record_error(
                                    trace_id, str(event.error or "error"), ErrorCategory.PROVIDER_ERROR
                                )
                            except (AttributeError, TypeError, RuntimeError) as exc:
                                logger.warning("[stream-executor] telemetry record_error failed: %s", exc)
                            except (ConnectionError, TimeoutError, RuntimeError) as exc:  # noqa: B025
                                logger.warning("[stream-executor] telemetry record_error failed (unexpected): %s", exc)
                        yield event
                        return
                    if self.telemetry:
                        try:
                            self.telemetry.record_stream_chunk(trace_id, event, chunk_count)
                        except (AttributeError, TypeError, RuntimeError) as exc:
                            logger.warning("[stream-executor] telemetry record_stream_chunk failed: %s", exc)
                        except (ConnectionError, TimeoutError, RuntimeError) as exc:  # noqa: B025
                            logger.warning(
                                "[stream-executor] telemetry record_stream_chunk failed (unexpected): %s", exc
                            )
                    yield event
            else:
                upstream_stream = self._invoke_text_stream(
                    provider_instance=provider_instance,
                    prompt_input=prompt_input,
                    model=model,
                    invoke_cfg=invoke_cfg,
                    trace_id=trace_id,
                )
                async for event in upstream_stream:
                    _check_overall_timeout()
                    chunk_count += 1
                    if event.type == StreamEventType.CHUNK and event.chunk:
                        collected_output = _append_bounded_stream_text(
                            collected_output,
                            event.chunk,
                            limit_chars=stream_collection_limit,
                            label="output",
                        )
                    elif event.type == StreamEventType.REASONING_CHUNK and event.reasoning:
                        collected_reasoning = _append_bounded_stream_text(
                            collected_reasoning,
                            event.reasoning,
                            limit_chars=stream_collection_limit,
                            label="reasoning",
                        )
                    elif event.type == StreamEventType.ERROR:
                        if self.telemetry:
                            try:
                                self.telemetry.record_error(
                                    trace_id, str(event.error or "error"), ErrorCategory.PROVIDER_ERROR
                                )
                            except (AttributeError, TypeError, RuntimeError) as exc:
                                logger.warning("[stream-executor] telemetry record_error failed: %s", exc)
                            except (ConnectionError, TimeoutError, RuntimeError) as exc:  # noqa: B025
                                logger.warning("[stream-executor] telemetry record_error failed (unexpected): %s", exc)
                        yield event
                        return
                    if self.telemetry:
                        try:
                            self.telemetry.record_stream_chunk(trace_id, event, chunk_count)
                        except (AttributeError, TypeError, RuntimeError) as exc:
                            logger.warning("[stream-executor] telemetry record_stream_chunk failed: %s", exc)
                        except (ConnectionError, TimeoutError, RuntimeError) as exc:  # noqa: B025
                            logger.warning(
                                "[stream-executor] telemetry record_stream_chunk failed (unexpected): %s", exc
                            )
                    yield event

        except asyncio.TimeoutError as exc:
            if self.telemetry:
                try:
                    self.telemetry.record_error(trace_id, str(exc), ErrorCategory.TIMEOUT)
                except (AttributeError, TypeError, RuntimeError) as telemetry_exc:
                    logger.warning("[stream-executor] telemetry record_error (timeout) failed: %s", telemetry_exc)
                except (ConnectionError, TimeoutError, RuntimeError) as telemetry_exc:  # noqa: B025
                    logger.warning(
                        "[stream-executor] telemetry record_error (timeout) failed (unexpected): %s", telemetry_exc
                    )
            yield AIStreamEvent.error_event(str(exc))
            return
        except (AttributeError, TypeError, RuntimeError) as exc:
            logger.exception("[stream-executor] stream error")
            if self.telemetry:
                try:
                    self.telemetry.record_error(trace_id, str(exc), ErrorCategory.UNKNOWN)
                except (AttributeError, TypeError, RuntimeError) as telemetry_exc:
                    logger.warning("[stream-executor] telemetry record_error (unknown) failed: %s", telemetry_exc)
                except (ConnectionError, TimeoutError, RuntimeError) as telemetry_exc:  # noqa: B025
                    logger.warning(
                        "[stream-executor] telemetry record_error (unknown) failed (unexpected): %s", telemetry_exc
                    )
            yield AIStreamEvent.error_event(str(exc))
            return
        finally:
            # Close the upstream generator on EVERY exit (normal, timeout,
            # error, or GeneratorExit from our own consumer) so the provider's
            # HTTP response unwinds and the server aborts generation instead
            # of feeding a zombie.
            if upstream_stream is not None:
                with contextlib.suppress(Exception):
                    await upstream_stream.aclose()

        # Compatibility recovery for models/servers without native function-calling
        # (e.g. Gemma served by llama.cpp): the provider returned NO native tool
        # calls but the assembled content carries a textual tool-call format
        # `<|tool_call>call:NAME{...}`. Recover them into canonical tool_call events.
        # Gated on (1) no native tool calls, (2) tools were actually requested,
        # (3) recovered tool name is in the requested set — native-FC providers
        # (deepseek/kimi) return native tool_calls so this never triggers.
        if not emitted_tool_calls and collected_output and has_textual_tool_calls(collected_output):
            allowed_tool_names = _requested_tool_names(invoke_cfg)
            if allowed_tool_names:
                recovered = recover_textual_tool_calls(collected_output, allowed_tool_names)
                for recovered_call in recovered:
                    emitted_tool_calls.append(dict(recovered_call))
                    yield AIStreamEvent.tool_call_event(
                        dict(recovered_call),
                        meta={"provider": provider_type, "trace_id": trace_id, "textual_recovery": True},
                    )
                if recovered:
                    collected_output = strip_textual_tool_call_markers(collected_output, allowed_tool_names)

        latency_ms = int((time.time() - start_time) * 1000)
        if self.telemetry:
            try:
                self.telemetry.record_stream_end(
                    trace_id, total_chunks=chunk_count, total_chars=len(collected_output), latency_ms=latency_ms
                )
            except (AttributeError, TypeError, RuntimeError) as exc:
                logger.warning("[stream-executor] telemetry record_stream_end failed: %s", exc)
            except (ConnectionError, TimeoutError, RuntimeError) as exc:  # noqa: B025
                logger.warning("[stream-executor] telemetry record_stream_end failed (unexpected): %s", exc)

        structured = ResponseNormalizer.extract_json_object(collected_output)
        final_usage = stream_usage or Usage.estimate(prompt_input, collected_output)
        response = AIResponse.success(
            output=collected_output,
            usage=final_usage,
            latency_ms=latency_ms,
            structured=structured,
            trace_id=trace_id,
            thinking=collected_reasoning if collected_reasoning else None,
            metadata=self._traceability_metadata(request.context),
        )

        if self.telemetry:
            try:
                self.telemetry.record_invoke_end(trace_id, request, response, start_time)
            except (AttributeError, TypeError, RuntimeError) as exc:
                logger.warning("[stream-executor] telemetry record_invoke_end failed: %s", exc)
            except (ConnectionError, TimeoutError, RuntimeError) as exc:  # noqa: B025
                logger.warning("[stream-executor] telemetry record_invoke_end failed (unexpected): %s", exc)

        yield AIStreamEvent.complete(
            {
                "output_sha256": self._sha256_text(collected_output),
                "output_chars": len(collected_output),
                "reasoning_sha256": self._sha256_text(collected_reasoning),
                "reasoning_chars": len(collected_reasoning),
                "tool_call_count": len(emitted_tool_calls),
                "tool_calls": safe_observability_payload(emitted_tool_calls),
                "structured": safe_observability_payload(structured),
                "latency_ms": latency_ms,
                "usage": final_usage.to_dict(),
                "model_spec": model_spec.to_dict(),
                "token_budget": safe_token_budget,
            }
        )

    async def _invoke_text_stream(
        self,
        *,
        provider_instance: Any,
        prompt_input: str,
        model: str,
        invoke_cfg: dict[str, Any],
        trace_id: str,
    ) -> AIStreamGenerator:
        """Compatibility path for providers that only expose token streams."""
        from polaris.kernelone.llm.toolkit.parsers.xml_based import XMLToolParser

        think_parser = StreamThinkingParser()
        stream_timeout = max(1, float(invoke_cfg.get("timeout", 300)))

        def _emit_text_tool_call_events(raw_inner_text: str) -> list[AIStreamEvent]:
            token = str(raw_inner_text or "").strip()
            if not token:
                return []
            wrapped = f"<tool>{token}</tool>"
            parsed_calls = XMLToolParser.parse(wrapped)
            if not parsed_calls:
                return [AIStreamEvent.chunk_event(token)]
            return [
                AIStreamEvent.tool_call_event(
                    {
                        "tool": str(call.name or ""),
                        "arguments": dict(call.arguments or {}),
                        "call_id": str(call.id or ""),
                    },
                    meta={"compat_text_provider": True},
                )
                for call in parsed_calls
            ]

        async def stream_generator() -> AIStreamGenerator:
            async for token in provider_instance.invoke_stream(prompt_input, model, invoke_cfg):
                yield token

        stream_iter = stream_generator().__aiter__()
        try:
            while True:
                try:
                    token = await asyncio.wait_for(stream_iter.__anext__(), timeout=stream_timeout)
                except StopAsyncIteration:
                    break

                token_text = str(token or "")
                if not token_text:
                    continue

                if token_text.startswith("Error:"):
                    yield AIStreamEvent.error_event(token_text[6:].strip())
                    return

                if token_text.startswith(THINKING_PREFIX):
                    reasoning_text = token_text[len(THINKING_PREFIX) :]
                    if reasoning_text:
                        yield AIStreamEvent.reasoning_event(reasoning_text)
                    continue

                for kind, text in think_parser.feed_sync(token_text):
                    if not text:
                        continue
                    if kind == ChunkKind.THINKING:
                        yield AIStreamEvent.reasoning_event(text)
                    elif kind in (ChunkKind.TOOL_CALL_START, ChunkKind.TOOL_CALL_CONTENT, ChunkKind.TOOL_CALL_END):
                        for event in _emit_text_tool_call_events(text):
                            yield event
                    else:
                        yield AIStreamEvent.chunk_event(text)
        finally:
            aclose = getattr(stream_iter, "aclose", None)
            if callable(aclose):
                try:
                    # aclose may be an async bound method. asyncio.iscoroutinefunction()
                    # checks unbound functions and returns False for bound methods, so we
                    # must detect async by checking if the result is a coroutine.
                    result = aclose()
                    if asyncio.iscoroutine(result):
                        # Add timeout to prevent async_generator_athrow blocking for 128s
                        # when the provider's stream is waiting on network I/O
                        await asyncio.wait_for(result, timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("[stream-executor] text stream aclose() timed out after 5s")
                except (AttributeError, TypeError, RuntimeError, StopAsyncIteration) as exc:
                    # StopAsyncIteration is normal when generator is already closed
                    logger.debug("[stream-executor] text stream aclose() result: %s", exc)

        for kind, text in think_parser.flush():
            if not text:
                continue
            if kind == ChunkKind.THINKING:
                yield AIStreamEvent.reasoning_event(text)
            elif kind in (ChunkKind.TOOL_CALL_START, ChunkKind.TOOL_CALL_CONTENT, ChunkKind.TOOL_CALL_END):
                for event in _emit_text_tool_call_events(text):
                    yield event
            else:
                yield AIStreamEvent.chunk_event(text)

    async def _invoke_structured_stream(
        self,
        *,
        provider_instance: Any,
        provider_type: str,
        prompt_input: str,
        model: str,
        invoke_cfg: dict[str, Any],
        trace_id: str,
    ) -> AIStreamGenerator:
        """Preferred path for providers that expose raw structured stream deltas."""
        adapter = get_adapter(provider_type)
        pending_tool_calls: dict[str, _ToolCallAccumulator] = {}
        tool_call_ordinals_by_key: dict[str, int] = {}
        tool_call_ordinal = 0
        raw_tool_delta_ordinal = 0
        stream_timeout = max(1, float(invoke_cfg.get("timeout", 300)))
        stream_usage_payload: dict[str, Any] | None = None

        async def stream_generator() -> Any:
            async for raw_event in provider_instance.invoke_stream_events(prompt_input, model, invoke_cfg):
                yield raw_event

        stream_iter = stream_generator().__aiter__()
        try:
            while True:
                try:
                    raw_event = await asyncio.wait_for(stream_iter.__anext__(), timeout=stream_timeout)
                except StopAsyncIteration:
                    break

                if not isinstance(raw_event, dict):
                    continue

                decoded = adapter.decode_stream_event(raw_event)
                if decoded is None:
                    continue
                decoded_error = getattr(decoded, "error", None)
                if decoded_error:
                    yield AIStreamEvent.error_event(str(decoded_error))
                    return
                decoded_usage = getattr(decoded, "usage", None)
                if decoded_usage:
                    stream_usage_payload = dict(decoded_usage)

                for item in decoded.transcript_items:
                    item_type = type(item).__name__
                    if item_type == "ReasoningSummary":
                        text = str(getattr(item, "content", "") or "")
                        if text:
                            yield AIStreamEvent.reasoning_event(
                                text, meta={"provider": provider_type, "trace_id": trace_id}
                            )
                    elif item_type == "AssistantMessage":
                        text = str(getattr(item, "content", "") or "")
                        if text:
                            yield AIStreamEvent.chunk_event(
                                text, meta={"provider": provider_type, "trace_id": trace_id}
                            )

                for tool_call in decoded.tool_calls:
                    if not isinstance(tool_call, dict):
                        continue
                    raw_tool_delta_ordinal += 1
                    key = _tool_accumulator_key(tool_call, raw_tool_delta_ordinal)
                    tool_name_hint = str(tool_call.get("tool") or "").strip()
                    has_call_id = bool(str(tool_call.get("call_id") or "").strip())
                    has_arguments = bool(tool_call.get("arguments")) or bool(
                        str(tool_call.get("arguments_text") or "").strip()
                    )
                    has_stream_identity = (
                        isinstance(tool_call.get("index"), int)
                        or isinstance(tool_call.get("content_block_index"), int)
                        or has_call_id
                        or bool(tool_name_hint)
                        or has_arguments
                    )
                    if not has_stream_identity:
                        continue

                    if key not in tool_call_ordinals_by_key and tool_name_hint:
                        tool_call_ordinal += 1
                        tool_call_ordinals_by_key[key] = tool_call_ordinal

                    emitted = self._accumulate_stream_tool_call(
                        pending_tool_calls, tool_call, ordinal=raw_tool_delta_ordinal, provider_type=provider_type
                    )
                    if emitted is None:
                        continue

                    ordinal_for_call = tool_call_ordinals_by_key.get(key)
                    if ordinal_for_call is None:
                        tool_call_ordinal += 1
                        ordinal_for_call = tool_call_ordinal
                        tool_call_ordinals_by_key[key] = ordinal_for_call

                    yield AIStreamEvent.tool_call_event(emitted, meta={"provider": provider_type, "trace_id": trace_id})
        finally:
            aclose = getattr(stream_iter, "aclose", None)
            if callable(aclose):
                try:
                    # aclose may be an async bound method. asyncio.iscoroutinefunction()
                    # checks unbound functions and returns False for bound methods, so we
                    # must detect async by catching TypeError when the result is not awaited.
                    result = aclose()
                    if asyncio.iscoroutine(result):
                        # Add timeout to prevent async_generator_athrow blocking for 128s
                        # when the provider's stream is waiting on network I/O
                        await asyncio.wait_for(result, timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("[stream-executor] structured stream aclose() timed out after 5s")
                except (AttributeError, TypeError, RuntimeError, StopAsyncIteration) as exc:
                    # StopAsyncIteration is normal when generator is already closed
                    logger.debug("[stream-executor] structured stream aclose() result: %s", exc)

        for key, pending in pending_tool_calls.items():
            emitted = self._finalize_stream_tool_call(pending)
            if emitted is None:
                continue

            ordinal_for_call = tool_call_ordinals_by_key.get(key)
            if ordinal_for_call is None:
                tool_call_ordinal += 1
                ordinal_for_call = tool_call_ordinal
                tool_call_ordinals_by_key[key] = ordinal_for_call

            yield AIStreamEvent.tool_call_event(
                emitted, meta={"provider": provider_type, "trace_id": trace_id, "finalized": True}
            )

        if stream_usage_payload:
            yield AIStreamEvent.complete(
                {
                    "_internal_provider_usage": True,
                    "provider": provider_type,
                    "trace_id": trace_id,
                    "usage": stream_usage_payload,
                }
            )


async def stream_to_response(stream_gen: AIStreamGenerator, timeout: float | None = None) -> AIResponse:
    """Convert stream generator to complete response."""
    collected_output = ""
    collected_reasoning = ""
    error: str | None = None
    structured: dict[str, Any] | None = None
    stream_usage: Usage | None = None
    start_time = time.time()
    stream_collection_limit = _stream_collection_limit_from_env()

    try:
        if timeout:
            stream_gen = _with_timeout(stream_gen, timeout)

        async for event in stream_gen:
            if event.type == StreamEventType.CHUNK and event.chunk:
                collected_output = _append_bounded_stream_text(
                    collected_output,
                    event.chunk,
                    limit_chars=stream_collection_limit,
                    label="output",
                )
            elif event.type == StreamEventType.REASONING_CHUNK and event.reasoning:
                collected_reasoning = _append_bounded_stream_text(
                    collected_reasoning,
                    event.reasoning,
                    limit_chars=stream_collection_limit,
                    label="reasoning",
                )
            elif event.type == StreamEventType.COMPLETE and event.meta:
                structured = event.meta.get("structured")
                usage_payload = event.meta.get("usage")
                if isinstance(usage_payload, dict):
                    stream_usage = normalize_stream_usage(usage_payload)
            elif event.type == StreamEventType.ERROR:
                error = event.error

    except asyncio.TimeoutError:
        error = f"Stream timeout after {timeout}s"
    except (AttributeError, TypeError, RuntimeError) as exc:
        error = str(exc)

    latency_ms = int((time.time() - start_time) * 1000)

    if error:
        return AIResponse.failure(
            error=error,
            category=ErrorCategory.TIMEOUT if "timeout" in error.lower() else ErrorCategory.UNKNOWN,
            latency_ms=latency_ms,
        )

    if not structured and collected_output:
        structured = ResponseNormalizer.extract_json_object(collected_output)

    return AIResponse.success(
        output=collected_output,
        usage=stream_usage or Usage.estimate("", collected_output),
        latency_ms=latency_ms,
        structured=structured,
        thinking=collected_reasoning if collected_reasoning else None,
    )


async def _with_timeout(gen: AIStreamGenerator, timeout: float) -> AIStreamGenerator:
    """Add timeout to generator."""
    start = time.time()
    async for item in gen:
        if time.time() - start > timeout:
            raise asyncio.TimeoutError()
        yield item


async def _stream_with_overall_timeout(gen: AIStreamGenerator, timeout: float) -> AIStreamGenerator:
    """Wrap a stream generator with overall timeout enforcement."""
    start_time = time.time()

    async def _check_timeout() -> None:
        elapsed = time.time() - start_time
        if elapsed > timeout:
            raise asyncio.TimeoutError(f"Stream overall timeout after {timeout}s")

    async for event in gen:
        await _check_timeout()
        yield event

    await _check_timeout()
