"""Unified Provider contracts for KernelOne LLM layer.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

本模块定义了 Provider 层与 Adapter 层之间的统一契约。
所有 Provider 实现和 Adapter 实现必须遵循此契约。

契约链路:
    Caller -> Adapter.build_request(state) -> ProviderRequest
    Caller -> Provider.invoke(request) -> ProviderResponse
    Caller -> Adapter.decode_response(response) -> DecodedProviderOutput

---
边界规则 (TypedDict vs Dataclass):
- TypedDict: API边界契约（序列化/反序列化）
- dataclass: 内部数据结构和运行时对象
---
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, TypedDict


class ProviderRequest(TypedDict, total=False):
    """Unified Provider request structure.

    Produced by Adapter.build_request(), consumed by Provider.invoke().
    All Provider implementations should accept this structure or compatible subset.

    Attributes:
        messages: Provider-native message format list (Anthropic/OpenAI/etc)
        model: Model name
        temperature: Temperature parameter
        max_tokens: Max generation tokens
        system: System prompt (Anthropic format)
        tools: Tool definitions list
        tool_choice: Tool choice strategy
        stream: Whether to stream output
        prompt: Plain text prompt (for legacy BaseProvider.invoke compatibility)
        config: Complete provider config dict
    """

    messages: list[dict[str, Any]]
    model: str
    temperature: float | None
    max_tokens: int | None
    system: str | None
    tools: list[dict[str, Any]] | None
    tool_choice: dict[str, Any] | str | None
    stream: bool
    prompt: str  # Plain text, for BaseProvider.invoke compatibility
    config: dict[str, Any]  # Complete raw config


class ProviderResponse(TypedDict, total=False):
    """Unified Provider response structure.

    Raw response returned by Provider.invoke().
    Caller should use Adapter.decode_response() to convert to DecodedProviderOutput.

    Attributes:
        ok: Whether request succeeded
        output: Text output
        error: Error message
        raw: Raw response dict
        usage: Provider-native usage info
    """

    ok: bool
    output: str
    error: str
    raw: dict[str, Any]
    usage: dict[str, Any]


class AdapterProviderContract:
    """Adapter-Provider contract validator.

    Validates that Adapter.build_request() output conforms to ProviderRequest format.
    """

    @staticmethod
    def validate_request(request: dict[str, Any]) -> tuple[bool, list[str]]:
        """Validate ProviderRequest format.

        Args:
            request: Dict returned by Adapter.build_request()

        Returns:
            (is_valid, error_messages)
        """
        errors: list[str] = []

        if not isinstance(request, dict):
            return False, ["request must be a dict"]

        # config is required
        config = request.get("config")
        if not isinstance(config, dict):
            errors.append("request['config'] must be a dict")

        # messages should be in config
        if isinstance(config, dict):
            messages = config.get("messages")
            if not isinstance(messages, list):
                errors.append("request['config']['messages'] must be a list")

        return len(errors) == 0, errors

    @staticmethod
    def extract_messages(request: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract messages from ProviderRequest.

        Prefers config['messages'], falls back to prompt for legacy compatibility.

        Args:
            request: Dict returned by Adapter.build_request()

        Returns:
            messages list, or empty list
        """
        config = request.get("config", {})
        if isinstance(config, dict):
            messages = config.get("messages")
            if isinstance(messages, list):
                return messages

        # Fallback: build single user message from prompt
        prompt = request.get("prompt", "")
        if prompt:
            return [{"role": "user", "content": [{"type": "text", "text": prompt}]}]

        return []


@dataclass
class RuntimeProviderInvokeResult:
    """Result of a single LLM provider invocation within the KernelOne runtime.

    Emitted by the runtime adapter to record whether the call was attempted,
    whether it succeeded, and any telemetry useful for auditing.

    Attributes:
        attempted: True if the provider was actually called.
        ok: True if the call succeeded without error.
        output: Raw text output from the model, or "" if not attempted / failed.
        provider_id: Provider identifier used for this call.
        provider_type: Provider type string (e.g. "openai", "anthropic").
        model: Model name that produced the output.
        latency_ms: Wall-clock time spent in the provider call, in milliseconds.
        error: Error message string. Empty if ``ok`` is True.
        usage: Provider-specific token-usage dict, or None.
    """

    attempted: bool
    ok: bool
    output: str
    provider_id: str
    provider_type: str
    model: str
    latency_ms: int = 0
    error: str = ""
    usage: Any = None


class KernelLLMRuntimeAdapter(Protocol):
    """Abstract port for KernelOne LLM runtime orchestration.

    Resolves role-to-model mappings, loads provider configuration from the
    workspace, and retrieves provider instances. Implementations:
    ``RuntimeLLMAdapter`` (in-process).
    """

    def get_role_model(self, role: str) -> tuple[str, str]:
        """Resolve the provider ID and model name for a given role.

        Args:
            role: Role string, e.g. "director", "pm", "architect".

        Returns:
            A ``(provider_id, model_name)`` tuple.
        """

    def load_provider_config(
        self,
        *,
        workspace: str,
        provider_id: str,
    ) -> dict[str, Any]:
        """Load the configuration for a specific provider from the workspace.

        Args:
            workspace: Absolute workspace path.
            provider_id: Provider identifier to load config for.

        Returns:
            Dict of provider configuration key-value pairs.
        """

    def get_provider_instance(self, provider_type: str) -> Any:
        """Retrieve the registered provider instance for the given type.

        Args:
            provider_type: Provider type string, e.g. "openai", "anthropic".

        Returns:
            A ``ProviderPort`` implementation.
        """

    def record_provider_failure(self, provider_type: str) -> None:
        """Record a provider failure for TTL-based eviction tracking.

        Args:
            provider_type: Provider type string that failed.
        """


__all__ = [
    "AdapterProviderContract",
    # Runtime contracts
    "KernelLLMRuntimeAdapter",
    # Provider contracts
    "ProviderRequest",
    "ProviderResponse",
    "RuntimeProviderInvokeResult",
]
