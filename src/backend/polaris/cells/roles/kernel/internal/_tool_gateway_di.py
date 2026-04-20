"""ToolGatewayPort DI support module.

This module provides _DelegatingToolGateway for backward compatibility
when injecting ToolGatewayPort implementations into RoleExecutionKernel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.public.contracts import ToolGatewayPort


class _DelegatingToolGateway:
    """Wrapper that adapts a ToolGatewayPort to RoleToolGateway interface.

    This class allows any ToolGatewayPort implementation to be used
    where a RoleToolGateway is expected, enabling DI flexibility
    while maintaining backward compatibility.

    Example:
        >>> mock_gateway = MockToolGatewayForPort()
        >>> delegating = _DelegatingToolGateway(mock_gateway)
        >>> # Now delegating can be used wherever RoleToolGateway is expected
        >>> result = delegating.execute("read_file", {"path": "test.py"})
    """

    def __init__(self, port: ToolGatewayPort) -> None:
        """Initialize with a ToolGatewayPort implementation.

        Args:
            port: A ToolGatewayPort-compliant implementation.
        """
        self._port = port
        # Mimic RoleToolGateway interface for backward compatibility
        self._execution_count = 0

    def execute(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute tool via delegated port.

        Args:
            tool_name: Name of the tool to execute.
            args: Tool arguments.

        Returns:
            Tool execution result.
        """
        self._execution_count += 1
        return self._port.execute(tool_name, args)

    def execute_tool(self, tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
        """Alias for execute() matching RoleToolGateway interface.

        Args:
            tool_name: Name of the tool to execute.
            tool_args: Tool arguments.

        Returns:
            Tool execution result.
        """
        return self.execute(tool_name, tool_args)

    def requires_approval(
        self,
        tool_name: str,
        args: dict | None = None,
        state: Any | None = None,
    ) -> bool:
        """Check if tool requires approval via delegated port.

        Args:
            tool_name: Name of the tool to check.
            args: Optional tool arguments.
            state: Optional execution state.

        Returns:
            True if approval is required.
        """
        return self._port.requires_approval(tool_name, args, state)

    def check_tool_permission(
        self,
        tool_name: str,
        tool_args: dict | None = None,
    ) -> tuple[bool, str]:
        """Check tool permission (delegates to requires_approval).

        Args:
            tool_name: Name of the tool to check.
            tool_args: Optional tool arguments.

        Returns:
            (is_allowed, reason) tuple.
        """
        if self.requires_approval(tool_name, tool_args):
            return False, f"Tool '{tool_name}' requires approval"
        return True, "authorized"

    def reset_execution_count(self) -> None:
        """Reset the execution counter."""
        self._execution_count = 0

    def set_iteration(self, round_index: int) -> None:
        """Set current round iteration (no-op for port adapters)."""

    def close(self) -> None:
        """Close any resources held by the delegated port."""
        close = getattr(self._port, "close", None)
        if callable(close):
            close()
