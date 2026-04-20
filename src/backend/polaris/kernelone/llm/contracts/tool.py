"""Tool system contracts - unified ToolCall definitions.

This module provides the canonical ToolCall and related types for the KernelOne
tool system. All tool-related contracts should be imported from here.

Unified ToolCall fields:
- id: Unique identifier for the tool call
- name: Tool name (normalized to lowercase, underscores)
- arguments: Tool arguments (dict, stored as immutable copy)
- source: Source of the tool call (e.g., "openai", "anthropic", "text_parser")
- raw: Raw original text if parsed from text
- parse_error: Error message if parsing failed
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

# Tool result truncation limit to prevent LLM context overflow
MAX_TOOL_RESULT_CHARS = 10_000


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Canonical tool call payload used by KernelOne runtime.

    This is the single source of truth for tool call representation.
    Combines fields from both text-parsed and native function calling sources.

    Attributes:
        id: Unique identifier for this tool call.
        name: Normalized tool name (lowercase, underscores).
        arguments: Tool arguments as a dictionary (immutable copy).
        source: Source of the tool call (default: "unknown").
        raw: Raw original text if parsed from text format.
        parse_error: Error message if parsing failed (default: None).
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    source: str = field(default="unknown", compare=False)
    raw: str = field(default="", compare=False)
    parse_error: str | None = field(default=None, compare=False)

    # Internal cached storage for immutable arguments copy
    _arguments_copy: ClassVar[tuple[str, ...]] = ()

    def __post_init__(self) -> None:
        """Validate fields and create immutable arguments copy."""
        # Auto-generate id if empty (for text-parsed tool calls)
        if not self.id:
            import uuid

            object.__setattr__(self, "id", f"auto_{uuid.uuid4().hex[:8]}")
        if not self.name:
            raise ValueError("ToolCall name cannot be empty")
        if not isinstance(self.arguments, dict):
            raise ValueError("ToolCall arguments must be a dictionary")

    @classmethod
    def from_openai_format(cls, data: dict[str, Any]) -> ToolCall:
        """Parse ToolCall from OpenAI tool_calls format.

        Args:
            data: OpenAI tool_calls dict with structure:
                {
                    "id": "call_xxx",
                    "type": "function",
                    "function": {
                        "name": "function_name",
                        "arguments": "{\"arg\": \"value\"}"  # JSON string
                    }
                }

        Returns:
            ToolCall instance parsed from the data.
        """
        func = data.get("function", {})
        args_str = func.get("arguments", "{}")

        # Parse arguments JSON string
        arguments: dict[str, Any] = {}
        parse_error: str | None = None
        if isinstance(args_str, str):
            try:
                arguments = json.loads(args_str)
                if not isinstance(arguments, dict):
                    arguments = {}
                    parse_error = "arguments must be a JSON object"
            except json.JSONDecodeError as e:
                arguments = {}
                parse_error = f"invalid JSON arguments: {e}"
        elif isinstance(args_str, dict):
            arguments = args_str
        else:
            arguments = {}
            parse_error = "arguments must be a string or dict"

        return cls(
            id=str(data.get("id", "")),
            name=str(func.get("name", "") or "").strip().lower(),
            arguments=copy.deepcopy(arguments),
            source="openai",
            raw=json.dumps(data, ensure_ascii=False) if not parse_error else "",
            parse_error=parse_error,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolCall:
        """Deserialize ToolCall from a dictionary.

        Args:
            data: Dictionary with tool call data.

        Returns:
            ToolCall instance.
        """
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "") or "").strip().lower(),
            arguments=copy.deepcopy(dict(data.get("arguments", {}))),
            source=str(data.get("source", "unknown")),
            raw=str(data.get("raw", "")),
            parse_error=data.get("parse_error"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize ToolCall to a dictionary.

        Returns:
            Dictionary representation of the tool call.
        """
        return {
            "id": self.id,
            "name": self.name,
            "arguments": copy.deepcopy(self.arguments),
            "source": self.source,
            "raw": self.raw,
            "parse_error": self.parse_error,
        }

    def to_openai_format(self) -> dict[str, Any]:
        """Convert to OpenAI tool_calls format.

        Returns:
            OpenAI-compatible tool_calls dict.
        """
        # If there's a parse error, mark it in arguments for debugging
        args = self.arguments
        if self.parse_error:
            args = {**args, "_parse_error": self.parse_error}

        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(args, ensure_ascii=False),
            },
        }

    def with_arguments(self, arguments: dict[str, Any]) -> ToolCall:
        """Create a new ToolCall with updated arguments (immutable update).

        Args:
            arguments: New arguments dict.

        Returns:
            New ToolCall instance with updated arguments.
        """
        return ToolCall(
            id=self.id,
            name=self.name,
            arguments=copy.deepcopy(arguments),
            source=self.source,
            raw=self.raw,
            parse_error=self.parse_error,
        )


# Per-provider result format templates (deep-copied at call time)
_ANTHROPIC_RESULT_TEMPLATE: dict[str, Any] = {
    "role": "user",
    "content": [
        {
            "type": "tool_result",
            "tool_use_id": "",
            "content": "",
        }
    ],
}
_TOOL_RESULT_TEMPLATE: dict[str, Any] = {
    "role": "tool",
    "tool_call_id": "",
    "content": "",
}


@dataclass(frozen=True)
class ToolExecutionResult:
    """Execution result for one tool call.

    P2-018 Intent Separation:
        This is the canonical EXECUTION phase result for LLM runtime.
        Intentional separation from:
        - polaris.cells.roles.kernel.internal.output_parser.ToolCallResult
            (Parse phase: has tool, args fields)
        - polaris.kernelone.benchmark.llm.tool_accuracy.ToolCallResult
            (Benchmark phase: has case_id, execution_time_ms, error)
        - polaris.kernelone.agent.tools.contracts.ToolExecutionResult
            (Agent phase: uses ToolStatus enum instead of bool success)

    Attributes:
        tool_call_id: ID of the tool call that produced this result.
        name: Tool name.
        success: Whether the tool executed successfully.
        result: Execution result data.
        error: Error message if execution failed.
        duration_ms: Execution time in milliseconds.
        blocked: Whether the tool was blocked by policy.
    """

    tool_call_id: str
    name: str
    success: bool
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    duration_ms: int = 0
    blocked: bool = False

    def to_provider_native(self, provider: str) -> dict[str, Any]:
        """Convert to provider-native tool result format.

        Args:
            provider: Provider name ("openai", "anthropic", "ollama", "gemini", etc.)

        Returns:
            Provider-native tool result payload.
        """
        # Normalize error content
        if not self.success and self.error:
            content = f"Error: {self.error}"
        elif isinstance(self.result, dict):
            content = json.dumps(self.result, ensure_ascii=False)
        else:
            content = str(self.result)

        # Truncate to prevent token overflow in LLM context
        if len(content) > MAX_TOOL_RESULT_CHARS:
            content = content[:MAX_TOOL_RESULT_CHARS] + "\n[RESULT_TRUNCATED]"

        # Anthropic uses tool_use_id + tool_result content block
        if provider == "anthropic":
            result = copy.deepcopy(_ANTHROPIC_RESULT_TEMPLATE)
            result["content"][0]["tool_use_id"] = self.tool_call_id
            result["content"][0]["content"] = content
            return result

        # All other providers (openai, ollama, gemini, deepseek, etc.) use tool format
        result = copy.deepcopy(_TOOL_RESULT_TEMPLATE)
        result["tool_call_id"] = self.tool_call_id
        result["content"] = content
        return result


@dataclass(frozen=True)
class ToolPolicy:
    """Kernel-level runtime policy for one tool-calling round.

    Attributes:
        allowed_tool_names: Tuple of allowed tool names (empty = all allowed).
        max_tool_calls: Maximum number of tool calls per round.
        fail_fast: Whether to stop execution on first failure.
    """

    allowed_tool_names: tuple[str, ...] = ()
    max_tool_calls: int = 4
    fail_fast: bool = False


@dataclass(frozen=True)
class ToolRoundRequest:
    """Input for one tool-calling round.

    Attributes:
        workspace: Workspace path for tool execution.
        assistant_text: Raw assistant text that may contain text-based tool calls.
        native_tool_calls: Native function calling format tool calls.
        response_payload: Provider-specific response payload.
        provider_hint: Hint for which provider format to expect.
        policy: Tool execution policy.
    """

    workspace: str
    assistant_text: str = ""
    native_tool_calls: Sequence[dict[str, Any]] = ()
    response_payload: dict[str, Any] | None = None
    provider_hint: str = "auto"
    policy: ToolPolicy = ToolPolicy()


@dataclass(frozen=True)
class ToolRoundOutcome:
    """Output of one tool-calling round.

    Attributes:
        tool_calls: Successfully parsed tool calls.
        tool_results: Execution results for all tool calls.
        assistant_remainder: Text remaining after extracting tool calls.
        feedback_text: Formatted feedback text for the next round.
        should_continue: Whether to continue with another round.
    """

    tool_calls: list[ToolCall]
    tool_results: list[ToolExecutionResult]
    assistant_remainder: str
    feedback_text: str
    should_continue: bool


class ToolCallParserPort(Protocol):
    """Port for multi-provider tool-call parsing.

    Implementations should handle parsing from different LLM providers
    (OpenAI, Anthropic, text-based, etc.) and normalize to ToolCall.
    """

    def parse_calls(
        self,
        *,
        text: str = "",
        native_tool_calls: Sequence[dict[str, Any]] = (),
        response_payload: dict[str, Any] | None = None,
        provider_hint: str = "auto",
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[ToolCall]:
        """Parse tool calls from various formats.

        Args:
            text: Raw text that may contain text-based tool calls.
            native_tool_calls: Native function calling format.
            response_payload: Provider-specific response payload.
            provider_hint: Hint for provider format.
            allowed_tool_names: Optional filter for allowed tools.

        Returns:
            List of parsed ToolCall objects.
        """
        ...

    def extract_calls_and_remainder(
        self,
        text: str,
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> tuple[list[ToolCall], str]:
        """Extract tool calls from text and return remainder.

        Args:
            text: Text containing tool calls.
            allowed_tool_names: Optional filter for allowed tools.

        Returns:
            Tuple of (extracted_calls, remaining_text).
        """
        ...


class ToolExecutorPort(Protocol):
    """Port for tool-call execution (KernelOne canonical interface).

    This is the primary interface for KernelOne runtime.
    Implementations should execute tool calls and return results.
    """

    def execute_call(
        self,
        *,
        workspace: str,
        tool_call: ToolCall,
    ) -> ToolExecutionResult:
        """Execute a single tool call.

        Args:
            workspace: Workspace path for execution.
            tool_call: The tool call to execute.

        Returns:
            Execution result.
        """
        ...


class CellToolExecutorPort(Protocol):
    """Port for Cells-layer tool execution with simplified signature.

    This interface is designed for Cells layer use cases where
    tool_name and args are passed separately (not as a ToolCall object).
    It bridges to the canonical ToolExecutorPort via adapters.

    This is the unified interface for:
    - cells/roles/kernel/internal/testing/fake_tools.ToolExecutorProtocol
    - cells/roles/kernel/services/contracts.IToolExecutor
    - cells/roles/kernel/internal/services/contracts.ToolExecutorProtocol
    """

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a tool by name with given arguments.

        Args:
            tool_name: Tool name (canonical, normalized)
            args: Tool arguments dictionary
            context: Optional execution context (e.g., profile, request)

        Returns:
            Execution result dictionary with at least 'success' key
        """
        ...


__all__ = [
    "CellToolExecutorPort",
    "ToolCall",
    "ToolCallParserPort",
    "ToolExecutionResult",
    "ToolExecutorPort",
    "ToolPolicy",
    "ToolRoundOutcome",
    "ToolRoundRequest",
]
