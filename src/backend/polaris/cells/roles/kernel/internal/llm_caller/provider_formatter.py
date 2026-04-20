"""Provider Formatter Module.

Defines provider-specific message and tool result formatting behavior.

P1-TYPE-002: This module defines Cell-specific ProviderFormatter interfaces
that are distinct from the canonical KernelOne ProviderFormatter in
polaris.kernelone.llm.shared_contracts. The Cell layer uses ContextEvent
objects while the KernelOne layer uses dict representations.

The canonical KernelOne ProviderFormatter is imported and can be used when
appropriate (when working with dict-based message representations).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.tool_loop_controller import ContextEvent


class ProviderFormatter(Protocol):
    """Cell-layer Provider-specific formatting interface.

    This is distinct from the canonical KernelOne ProviderFormatter in
    shared_contracts.py which operates on dict representations.

    This interface handles ContextEvent objects from the Cell layer.
    """

    def format_messages(
        self,
        messages: list[ContextEvent],
    ) -> list[dict[str, str]]:
        """Format context events into LLM messages.

        Args:
            messages: Context event list

        Returns:
            Formatted message dictionary list
        """
        ...

    def format_tool_result(
        self,
        tool_name: str,
        result: dict[str, Any],
    ) -> str:
        """Format tool execution result.

        Args:
            tool_name: Tool name
            result: Tool execution result

        Returns:
            Formatted tool result string
        """
        ...

    def format_tools(
        self,
        tool_schemas: list[dict[str, Any]],
        provider_id: str,
    ) -> list[dict[str, Any]]:
        """Format tool schemas for provider.

        Args:
            tool_schemas: OpenAI-format tool schemas
            provider_id: Provider identifier

        Returns:
            Provider-formatted tool schemas
        """
        ...


class NativeProviderFormatter:
    """Native message array provider formatter.

    Formats messages for providers that support native message arrays
    (OpenAI/Anthropic). Uses standard XML tags to preserve semantics.
    """

    __slots__ = ()

    def format_messages(self, messages: list[ContextEvent]) -> list[dict[str, str]]:
        """Format context events into native message format.

        Returns messages with role/content structure for native providers.
        """
        result: list[dict[str, str]] = []
        for event in messages:
            role = str(event.role or "user")
            content = str(event.content or "")
            result.append({"role": role, "content": content})
        return result

    def format_tool_result(self, tool_name: str, result: dict[str, Any]) -> str:
        """Format tool result as XML tags.

        Uses standard XML tag format for tool results.
        """
        marker = "<tool>"
        close = "</tool>"
        tool_content = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        return f"{marker}\n[{tool_name}]\n{tool_content}\n{close}"

    def format_tools(
        self,
        tool_schemas: list[dict[str, Any]],
        provider_id: str,
    ) -> list[dict[str, Any]]:
        """Format tool schemas for native providers.

        Native providers accept OpenAI-format tool schemas directly.
        """
        return list(tool_schemas)


class AnnotatedProviderFormatter:
    """Chinese annotation provider formatter.

    Formats messages using Chinese markers, suitable for text-only providers.
    """

    __slots__ = ()

    def format_messages(self, messages: list[ContextEvent]) -> list[dict[str, str]]:
        """Format context events into Chinese annotation format.

        Returns messages with actual role values (e.g., "tool", "user").
        The annotation is applied by _messages_to_input when formatting.
        """
        result: list[dict[str, str]] = []
        for event in messages:
            role = str(event.role or "user")
            content = str(event.content or "")
            result.append({"role": role, "content": content})
        return result

    def format_tool_result(self, tool_name: str, result: dict[str, Any]) -> str:
        """Format tool result as Chinese annotation.

        Uses Chinese markers for tool results.
        """
        tool_content = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        return f"【工具结果】\n[{tool_name}]\n{tool_content}"

    def format_tools(
        self,
        tool_schemas: list[dict[str, Any]],
        provider_id: str,
    ) -> list[dict[str, Any]]:
        """Format tool schemas for annotated providers.

        Annotated providers use the same OpenAI-format schemas.
        """
        return list(tool_schemas)


def create_formatter(provider_id: str) -> ProviderFormatter:
    """Create appropriate formatter for provider.

    Args:
        provider_id: Provider identifier string

    Returns:
        ProviderFormatter instance appropriate for the provider
    """
    native_providers = {"openai", "anthropic", "claude", "gpt", "codex", "kimi"}
    provider_lower = str(provider_id or "").strip().lower()

    if any(pid in provider_lower for pid in native_providers):
        return NativeProviderFormatter()
    return AnnotatedProviderFormatter()


__all__ = [
    "AnnotatedProviderFormatter",
    "NativeProviderFormatter",
    "ProviderFormatter",
    "create_formatter",
]
