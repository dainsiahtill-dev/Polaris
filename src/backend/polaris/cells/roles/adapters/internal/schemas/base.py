"""Base schema classes with tool call support.

Provides shared models for tool-enabled structured outputs.
"""

from typing import Literal

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """Generic tool call specification for all roles."""

    tool: str = Field(..., description="Tool name (e.g., search_code, read_file, glob)")
    arguments: dict = Field(default_factory=dict, description="Tool arguments")
    reasoning: str | None = Field(default=None, description="Why this tool is needed for the analysis")


class BaseToolEnabledOutput(BaseModel):
    """Base class for outputs that may include tool calls.

    All tool-enabled roles should inherit from this and add their specific fields.
    """

    tool_calls: list[ToolCall] = Field(
        default_factory=list, description="Optional tool calls to gather more information"
    )

    is_complete: bool = Field(default=True, description="Whether the analysis is complete or needs more tool calls")

    next_action: Literal["respond", "call_tools"] = Field(
        default="respond", description="What to do next: respond to user or execute tool calls"
    )

    def get_tools_to_execute(self) -> list[ToolCall]:
        """Get list of tools that need to be executed."""
        if self.next_action == "call_tools" and self.tool_calls:
            return self.tool_calls
        return []
