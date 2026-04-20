"""LLM Caller Core Module.

Provides the main LLMCaller class for LLM invocation.

Deprecation Notice:
    This module is now a facade over LLMInvoker. Direct use of LLMCaller is
    deprecated in favor of using LLMInvoker directly. LLMCaller is maintained
    for backward compatibility only.

Migration: 2026-03-31
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.events import LLMEventType, emit_llm_event
from polaris.cells.roles.kernel.internal.interaction_contract import (
    ProviderCapabilities,
    build_interaction_contract,
)
from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType
from polaris.kernelone.llm.engine.model_catalog import ModelCatalog

from .error_handling import (
    append_runtime_fallback_instruction,
    build_text_response_fallback_instruction,
)
from .helpers import (
    build_native_response_format,
    build_native_tool_schemas,
    compute_context_summary,
    messages_to_input,
    resolve_platform_retry_max,
    resolve_timeout_seconds,
)
from .invoker import LLMInvoker
from .response_types import LLMResponse, PreparedLLMRequest, StructuredLLMResponse

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
    from polaris.cells.roles.profile.public.service import RoleProfile


_TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY = "_transaction_kernel_prebuilt_messages"
_TRANSACTION_KERNEL_PREBUILT_TOKEN_ESTIMATE_KEY = "_transaction_kernel_prebuilt_token_estimate"
_TRANSACTION_KERNEL_PREBUILT_COMPRESSION_APPLIED_KEY = "_transaction_kernel_prebuilt_compression_applied"
_TRANSACTION_KERNEL_PREBUILT_COMPRESSION_STRATEGY_KEY = "_transaction_kernel_prebuilt_compression_strategy"
_TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY = "_transaction_kernel_forced_tool_definitions"
_TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY = "_transaction_kernel_forced_tool_choice"


def _normalize_user_message_for_dedupe(value: Any) -> str:
    token = str(value or "")
    token = token.replace("\r\n", "\n").replace("\r", "\n")
    token = token.replace("\ufeff", "").strip()
    return token


class LLMCaller:
    """LLM Caller.

    Extracts LLM invocation logic from RoleExecutionKernel for single responsibility.

    .. deprecated::
        This class is now a facade over LLMInvoker. Use LLMInvoker directly
        for new code. LLMCaller is maintained for backward compatibility.
    """

    __slots__ = ("_cache", "_enable_cache", "_executor", "_formatter", "_invoker", "_model_catalog", "workspace")

    def __init__(self, workspace: str = "", enable_cache: bool = True, executor: Any | None = None) -> None:
        """Initialize LLM caller.

        Args:
            workspace: Workspace path
            enable_cache: Whether to enable LLM response caching
            executor: Optional AIExecutor instance for DI (injected, not created inline).
                When provided, tests can inject mocks without patching.
                When None, LLMInvoker creates a default AIExecutor instance.
        """
        self.workspace = workspace
        self._enable_cache = enable_cache
        self._cache = None  # Lazy load
        self._model_catalog = ModelCatalog(workspace=workspace or ".")
        self._formatter: Any | None = None  # ProviderFormatter for lazy serialization
        self._invoker: LLMInvoker | None = None
        self._executor: Any | None = executor  # Injected executor for DI

        # Emit deprecation warning for direct instantiation
        warnings.warn(
            "LLMCaller is deprecated. Use LLMInvoker directly for new code. "
            "LLMCaller is maintained as a backward-compatible facade.",
            DeprecationWarning,
            stacklevel=2,
        )

    def _get_invoker(self) -> LLMInvoker:
        """Get or create LLMInvoker instance (respects DI executor injection)."""
        if self._invoker is None:
            self._invoker = LLMInvoker(
                workspace=self.workspace, enable_cache=self._enable_cache, executor=self._executor
            )
            self._invoker._model_catalog = self._model_catalog
            self._invoker._formatter = self._formatter
        return self._invoker

    def set_formatter(self, formatter: Any) -> None:
        """Set ProviderFormatter for lazy serialization."""
        self._formatter = formatter

    @staticmethod
    def _build_native_tool_schemas(profile: RoleProfile) -> list[dict[str, Any]]:
        """Build native tool schemas from profile (for test compatibility)."""
        return build_native_tool_schemas(profile)

    def _resolve_provider_capabilities(self, profile: RoleProfile) -> ProviderCapabilities:
        """Resolve per-model capability flags with conservative keyword fallback."""
        provider_id = str(getattr(profile, "provider_id", "") or "").strip()
        model = str(getattr(profile, "model", "") or "").strip()
        whitelist = [
            str(name).strip()
            for name in list(getattr(getattr(profile, "tool_policy", None), "whitelist", []) or [])
            if str(name).strip()
        ]
        supports_tools = False
        supports_json_schema = False

        try:
            spec = self._model_catalog.resolve(provider_id, model)
            supports_tools = bool(spec.supports_tools)
            supports_json_schema = bool(spec.supports_json_schema)
        except (RuntimeError, ValueError):
            spec = None

        token = " ".join([provider_id.lower(), model.lower()])
        if not supports_tools and any(
            keyword in token for keyword in ("openai", "gpt", "codex", "anthropic", "claude", "kimi", "minimax")
        ):
            supports_tools = True
        if not supports_tools and whitelist:
            unknown_tokens = {
                "",
                "unknown",
                "unknown-provider",
                "unknown-model",
                "n/a",
                "na",
                "none",
                "null",
                "default",
            }
            provider_unknown = provider_id.lower() in unknown_tokens or provider_id.lower().startswith("unknown")
            model_unknown = model.lower() in unknown_tokens or model.lower().startswith("unknown")
            if provider_unknown and model_unknown:
                supports_tools = True
        if not supports_json_schema and any(keyword in token for keyword in ("openai", "gpt", "codex")):
            supports_json_schema = True

        return ProviderCapabilities(
            supports_native_tools=supports_tools,
            supports_json_schema=supports_json_schema,
            supports_stream_native_tools=supports_tools,
        )

    @staticmethod
    def _extract_prebuilt_projection_messages(context: ContextRequest) -> list[dict[str, Any]] | None:
        """Extract TransactionKernel-provided projected messages from context override."""
        override = getattr(context, "context_override", None)
        if not isinstance(override, dict):
            return None
        raw_messages = override.get(_TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY)
        if not isinstance(raw_messages, list):
            return None

        normalized_user_turns: list[tuple[str, str, str]] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip()
            if not role:
                continue
            content = str(item.get("content", ""))
            normalized_content = _normalize_user_message_for_dedupe(content) if role == "user" else ""
            normalized_user_turns.append((role, content, normalized_content))

        current_user_token = _normalize_user_message_for_dedupe(getattr(context, "message", ""))
        last_current_user_index = -1
        if current_user_token:
            for index, (role, _content, normalized_content) in enumerate(normalized_user_turns):
                if role == "user" and normalized_content == current_user_token:
                    last_current_user_index = index

        messages: list[dict[str, Any]] = []
        last_user_token: str | None = None
        for index, (role, content, normalized_content) in enumerate(normalized_user_turns):
            if (
                role == "user"
                and current_user_token
                and normalized_content == current_user_token
                and index != last_current_user_index
            ):
                continue
            if role == "user":
                if last_user_token is not None and normalized_content == last_user_token:
                    continue
                last_user_token = normalized_content
                if current_user_token and normalized_content == current_user_token:
                    content = current_user_token
            else:
                last_user_token = None
            messages.append({"role": role, "content": content})
        return messages

    async def _prepare_llm_request(
        self,
        *,
        profile: RoleProfile,
        system_prompt: str,
        context: ContextRequest,
        temperature: float,
        max_tokens: int,
        stream: bool,
        response_model: type | None = None,
        platform_retry_max: int = 1,
    ) -> PreparedLLMRequest:
        """Build canonical LLM request bundle."""
        from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
        from polaris.kernelone.context.contracts import TurnEngineContextResult
        from polaris.kernelone.context.projection_engine import ProjectionEngine
        from polaris.kernelone.context.receipt_store import ReceiptStore

        override = getattr(context, "context_override", None)
        prebuilt_messages = self._extract_prebuilt_projection_messages(context)
        forced_tool_definitions: list[dict[str, Any]] | None = None
        forced_tool_choice: Any | None = None
        if isinstance(override, dict):
            raw_forced_tool_definitions = override.get(_TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY)
            if isinstance(raw_forced_tool_definitions, list):
                forced_tool_definitions = [dict(item) for item in raw_forced_tool_definitions if isinstance(item, dict)]
            raw_forced_tool_choice = override.get(_TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY)
            if raw_forced_tool_choice is not None:
                if isinstance(raw_forced_tool_choice, str):
                    normalized_tool_choice = raw_forced_tool_choice.strip()
                    forced_tool_choice = normalized_tool_choice or None
                else:
                    forced_tool_choice = raw_forced_tool_choice

        if prebuilt_messages is not None:
            messages = list(prebuilt_messages)
            if not messages or str(messages[0].get("role", "")).strip().lower() != "system":
                messages = [{"role": "system", "content": str(system_prompt or "")}, *messages]
            input_text = messages_to_input(
                messages,
                format_type="auto",
                provider_id=str(getattr(profile, "provider_id", "")),
            )
            default_token_estimate = max(0, len(input_text) // 4)
            token_estimate = default_token_estimate
            compression_applied = False
            compression_strategy: str | None = None
            if isinstance(override, dict):
                raw_token_estimate = override.get(_TRANSACTION_KERNEL_PREBUILT_TOKEN_ESTIMATE_KEY)
                if isinstance(raw_token_estimate, (int, float, str)):
                    try:
                        token_estimate = max(0, int(raw_token_estimate))
                    except ValueError:
                        token_estimate = default_token_estimate
                compression_applied = bool(override.get(_TRANSACTION_KERNEL_PREBUILT_COMPRESSION_APPLIED_KEY))
                raw_compression_strategy = override.get(_TRANSACTION_KERNEL_PREBUILT_COMPRESSION_STRATEGY_KEY)
                if raw_compression_strategy is not None:
                    normalized_strategy = str(raw_compression_strategy).strip()
                    compression_strategy = normalized_strategy or None
            context_result = TurnEngineContextResult(
                messages=tuple(
                    {
                        "role": str(message.get("role", "")),
                        "content": str(message.get("content", "")),
                    }
                    for message in messages
                ),
                token_estimate=token_estimate,
                compression_applied=compression_applied,
                compression_strategy=compression_strategy,
                metadata={
                    "prebuilt_projection_messages": True,
                    "source": "transaction_kernel",
                },
            )
        else:
            context_gateway = RoleContextGateway(profile, self.workspace)
            context_result = await context_gateway.build_context(context)
            projection_dict = {"system_hint": system_prompt, "turns": list(context_result.messages)}
            messages = ProjectionEngine().project(projection_dict, ReceiptStore())

        input_text = messages_to_input(
            messages,
            format_type="auto",
            provider_id=str(getattr(profile, "provider_id", "")),
        )
        context_summary = compute_context_summary(input_text)

        request_timeout_seconds = resolve_timeout_seconds(profile)
        request_options: dict[str, Any] = {
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": request_timeout_seconds,
        }
        capabilities = self._resolve_provider_capabilities(profile)
        contract = build_interaction_contract(
            profile=profile,
            message=str(getattr(context, "message", "") or ""),
            domain=str(
                getattr(context, "domain", "")
                or (
                    (context.context_override or {}).get("domain")
                    if isinstance(getattr(context, "context_override", None), dict)
                    else ""
                )
                or "code"
            ),
            stream=stream,
            response_model=response_model,
            capabilities=capabilities,
        )
        native_tool_schemas: list[dict[str, Any]] = []
        native_tool_mode = "disabled"
        native_response_format: dict[str, Any] | None = None
        response_format_mode = "plain_text"
        provider_id = str(getattr(profile, "provider_id", "") or "")

        if stream:
            raw_tool_schemas = (
                forced_tool_definitions
                if forced_tool_definitions is not None
                else (self._build_native_tool_schemas(profile) if contract.native_tools_enabled else [])
            )
            if raw_tool_schemas:
                if self._formatter is not None:
                    request_options["tools"] = self._formatter.format_tools(raw_tool_schemas, provider_id)
                else:
                    request_options["tools"] = raw_tool_schemas
                request_options["tool_choice"] = forced_tool_choice if forced_tool_choice is not None else "auto"
                native_tool_mode = "native_tools_streaming"
            elif contract.tool_whitelist:
                native_tool_mode = "native_tools_unavailable"
        else:
            effective_platform_retry_max = resolve_platform_retry_max(profile, platform_retry_max)
            request_options["max_retries"] = effective_platform_retry_max
            request_options["platform_transport_only"] = True
            raw_tool_schemas = (
                forced_tool_definitions
                if forced_tool_definitions is not None
                else (self._build_native_tool_schemas(profile) if contract.native_tools_enabled else [])
            )
            if raw_tool_schemas:
                if self._formatter is not None:
                    request_options["tools"] = self._formatter.format_tools(raw_tool_schemas, provider_id)
                else:
                    request_options["tools"] = raw_tool_schemas
                request_options["tool_choice"] = forced_tool_choice if forced_tool_choice is not None else "auto"
                native_tool_mode = "native_tools"
            elif contract.tool_whitelist:
                native_tool_mode = "native_tools_unavailable"
            if contract.structured_output_enabled and response_model is not None:
                native_response_format = build_native_response_format(response_model)
                if native_response_format:
                    request_options["response_format"] = native_response_format
                    response_format_mode = "native_json_schema"
                else:
                    response_format_mode = "text_json_fallback"

        ai_request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role=profile.role_id,
            input=input_text,
            options=request_options,
            context={
                "workspace": self.workspace,
                "mode": "chat",
                "native_tool_mode": native_tool_mode,
                "response_format_mode": response_format_mode,
                "interaction_contract": contract.to_metadata(),
            },
        )
        return PreparedLLMRequest(
            messages=messages,
            input_text=input_text,
            context_result=context_result,
            context_summary=context_summary,
            request_options=request_options,
            ai_request=ai_request,
            native_tool_schemas=native_tool_schemas,
            native_tool_mode=native_tool_mode,
            response_model=response_model,
            native_response_format=native_response_format,
            response_format_mode=response_format_mode,
        )

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
    def _allow_native_tool_text_fallback(context: ContextRequest) -> bool:
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

    def _build_native_tool_fallback_request(
        self,
        *,
        prepared: PreparedLLMRequest,
        profile: RoleProfile,
        mode: str = "chat",
    ) -> AIRequest:
        """Retry without native tool parameters when provider rejects them."""
        fallback_options = dict(prepared.request_options)
        fallback_options.pop("tools", None)
        fallback_options.pop("tool_choice", None)
        fallback_options.pop("parallel_tool_calls", None)
        fallback_input = append_runtime_fallback_instruction(
            str(prepared.input_text or ""),
            (
                "【运行时工具回退】\n"
                "当前 provider 不接受原生 tools 参数。请禁止输出任何伪造的工具调用、函数调用、"
                '[TOOL_CALL] 包装、XML 工具标签或"已执行工具"的表述; 只能基于现有上下文直接回答。'
            ),
        )
        fallback_context = dict(prepared.ai_request.context if isinstance(prepared.ai_request.context, dict) else {})
        fallback_context["workspace"] = self.workspace
        fallback_context["mode"] = str(mode or "chat")
        fallback_context["native_tool_mode"] = "native_tools_text_fallback"
        return AIRequest(
            task_type=TaskType.DIALOGUE,
            role=profile.role_id,
            input=fallback_input,
            options=fallback_options,
            context=fallback_context,
        )

    def _build_structured_fallback_request(
        self,
        *,
        prepared: PreparedLLMRequest,
        profile: RoleProfile,
        response_model: type,
        mode: str = "structured",
    ) -> AIRequest:
        """Reuse prepared request baseline when native structured output is unavailable."""
        fallback_options = dict(prepared.request_options)
        fallback_options.pop("response_format", None)
        fallback_input = append_runtime_fallback_instruction(
            str(prepared.input_text or ""),
            build_text_response_fallback_instruction(response_model),
        )
        fallback_context = dict(prepared.ai_request.context if isinstance(prepared.ai_request.context, dict) else {})
        fallback_context["workspace"] = self.workspace
        fallback_context["mode"] = str(mode or "structured")
        fallback_context["response_format_mode"] = "text_json_fallback"
        return AIRequest(
            task_type=TaskType.DIALOGUE,
            role=profile.role_id,
            input=fallback_input,
            options=fallback_options,
            context=fallback_context,
        )

    # Event emission methods - delegated to invoker
    def _emit_call_error_event(
        self,
        *,
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

    def _emit_call_start_event(
        self,
        *,
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

    def _emit_call_end_event(
        self,
        *,
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

    def _emit_call_retry_event(
        self,
        *,
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

    # Public API methods - delegate to LLMInvoker
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
    ) -> LLMResponse:
        """Invoke LLM with non-streaming mode.

        .. deprecated::
            Use LLMInvoker.call() directly for new code.
        """
        warnings.warn(
            "LLMCaller.call() is deprecated. Use LLMInvoker.call() directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        invoker = self._get_invoker()
        return await invoker.call(
            profile=profile,
            system_prompt=system_prompt,
            context=context,
            response_model=response_model,
            temperature=temperature,
            max_tokens=max_tokens,
            prompt_fingerprint=prompt_fingerprint,
            platform_retry_max=platform_retry_max,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            turn_round=turn_round,
            event_emitter=self,
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
    ) -> StructuredLLMResponse:
        """Invoke LLM with structured output validation.

        .. deprecated::
            Use LLMInvoker.call_structured() directly for new code.
        """
        warnings.warn(
            "LLMCaller.call_structured() is deprecated. Use LLMInvoker.call_structured() directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        invoker = self._get_invoker()
        return await invoker.call_structured(
            profile=profile,
            system_prompt=system_prompt,
            context=context,
            response_model=response_model,
            temperature=temperature,
            max_tokens=max_tokens,
            max_retries=max_retries,
            prompt_fingerprint=prompt_fingerprint,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            event_emitter=self,
        )

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
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Invoke LLM with streaming mode.

        .. deprecated::
            Use LLMInvoker.call_stream() directly for new code.
        """
        warnings.warn(
            "LLMCaller.call_stream() is deprecated. Use LLMInvoker.call_stream() directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        invoker = self._get_invoker()
        async for event in invoker.call_stream(
            profile=profile,
            system_prompt=system_prompt,
            context=context,
            temperature=temperature,
            max_tokens=max_tokens,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            event_emitter=self,
        ):
            yield event


__all__ = ["LLMCaller"]
