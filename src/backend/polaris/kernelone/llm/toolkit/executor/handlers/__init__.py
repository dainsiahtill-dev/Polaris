"""Tool handler categories — categorization of tool types.

This module provides categorization of handler methods organized by tool type.
Each category groups related tools for policy enforcement and routing.
The actual handler registry is in registry.py (ToolHandlerRegistry).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.llm_toolkit.executor.core import AgentAccelToolExecutor

# Handler registry type alias
HandlerMethod = Any


class ToolHandlerCategories:
    """Categorization of tool handlers by functional domain.

    This class provides category-based classification of tools for:
    - Policy enforcement (e.g., filesystem tools need special approval)
    - Handler routing (e.g., search tools use shared search logic)
    - Tool validation (e.g., only registered tools are valid)

    Note: This is NOT a handler registry. Use ToolHandlerRegistry (registry.py)
    for actual handler function registration.
    """

    # File system handlers
    FILESYSTEM_HANDLERS = {
        "write_file",
        "read_file",
        "edit_file",
        "search_replace",
        "append_to_file",
    }

    # Search handlers
    SEARCH_HANDLERS = {
        "search_code",
        "grep",
        "ripgrep",
    }

    # Command handlers
    COMMAND_HANDLERS = {
        "execute_command",
    }

    # Navigation handlers
    NAVIGATION_HANDLERS = {
        "glob",
        "list_directory",  # Legacy alias, kept for backward compat
        "repo_tree",
        "file_exists",
    }

    # Session memory handlers
    SESSION_MEMORY_HANDLERS = {
        "search_memory",
        "read_artifact",
        "read_episode",
        "get_state",
    }

    # Tree-sitter handlers
    TREESITTER_HANDLERS = {
        "treesitter_find_symbol",
    }

    # All base tool names
    BASE_TOOLS = (
        FILESYSTEM_HANDLERS
        | SEARCH_HANDLERS
        | COMMAND_HANDLERS
        | NAVIGATION_HANDLERS
        | SESSION_MEMORY_HANDLERS
        | TREESITTER_HANDLERS
    )

    @classmethod
    def get_handler_method(
        cls,
        executor: AgentAccelToolExecutor,
        tool_name: str,
    ) -> HandlerMethod | None:
        """Get the handler method for a tool.

        Args:
            executor: The tool executor instance
            tool_name: Name of the tool

        Returns:
            Handler method or None if not found
        """
        handler_name = f"_handle_{tool_name}"
        return getattr(executor, handler_name, None)

    @classmethod
    def is_valid_tool(cls, tool_name: str) -> bool:
        """Check if a tool name is valid.

        Args:
            tool_name: Name of the tool

        Returns:
            True if the tool is registered
        """
        return tool_name in cls.BASE_TOOLS

    @classmethod
    def get_tool_category(cls, tool_name: str) -> str | None:
        """Get the category of a tool.

        Args:
            tool_name: Name of the tool

        Returns:
            Category name or None if not found
        """
        if tool_name in cls.FILESYSTEM_HANDLERS:
            return "filesystem"
        if tool_name in cls.SEARCH_HANDLERS:
            return "search"
        if tool_name in cls.COMMAND_HANDLERS:
            return "command"
        if tool_name in cls.NAVIGATION_HANDLERS:
            return "navigation"
        if tool_name in cls.SESSION_MEMORY_HANDLERS:
            return "session_memory"
        if tool_name in cls.TREESITTER_HANDLERS:
            return "treesitter"
        return None
