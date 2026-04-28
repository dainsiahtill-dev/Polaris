"""Tool Handler Registry - explicit handler registration.

Phase 5: Handler 显式注册
Replaces lazy module loading with explicit registration via decorator.

Design:
- ToolHandlerRegistry: global registry of tool_name -> handler function
- @register_handler(name): decorator to register a handler function
- Handlers can also register via ToolHandlerRegistry.register(name, func)
- Executor loads from registry instead of module-level register_handlers()

Handler signature: (executor: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

    from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor


class ToolHandler(Protocol):
    """Explicit handler signature — replaces untyped Callable[[Any, ...], ...].

    Each registered tool handler must accept an executor instance and
    arbitrary keyword arguments matching the tool's parameter schema.
    """

    def __call__(
        self,
        executor: AgentAccelToolExecutor,
        **kwargs: Any,
    ) -> dict[str, Any]: ...


class ToolHandlerRegistry:
    """Global registry for tool handlers.

    Provides explicit handler registration as an alternative to
    module-level register_handlers() functions.
    """

    _handlers: dict[str, ToolHandler] = {}

    @classmethod
    def register(cls, tool_name: str, handler: ToolHandler) -> None:
        """Register a tool handler.

        Args:
            tool_name: Canonical tool name (lowercase, underscores)
            handler: Handler function with signature (executor, **kwargs) -> dict

        Raises:
            ValueError: If tool_name is already registered
        """
        normalized = tool_name.strip().lower()
        if not normalized:
            raise ValueError("tool_name cannot be empty")
        if normalized in cls._handlers:
            raise ValueError(f"Handler already registered for tool: {normalized}")
        cls._handlers[normalized] = handler

    @classmethod
    def get(cls, tool_name: str) -> ToolHandler | None:
        """Get a registered handler by tool name.

        Args:
            tool_name: Canonical tool name

        Returns:
            Handler function or None if not registered
        """
        return cls._handlers.get(tool_name.strip().lower())

    @classmethod
    def get_all(cls) -> dict[str, ToolHandler]:
        """Get all registered handlers (copy).

        Returns:
            Dict of tool_name -> handler
        """
        return dict(cls._handlers)

    @classmethod
    def register_from_module(
        cls,
        module_dict: dict[str, ToolHandler],
    ) -> None:
        """Bulk register handlers from a module dict.

        Does not raise on duplicate - last-wins for backward compatibility.

        Args:
            module_dict: Dict of tool_name -> handler
        """
        for name, handler in module_dict.items():
            normalized = name.strip().lower()
            if normalized:
                cls._handlers[normalized] = handler

    @classmethod
    def clear(cls) -> None:
        """Clear all registered handlers.

        Intended for tests only.
        """
        cls._handlers.clear()

    @classmethod
    def load_all(cls) -> dict[str, ToolHandler]:
        """Load all handlers from known handler modules.

        Imports all handler modules and calls their register_handlers().

        Returns:
            Dict of all registered handlers
        """
        from polaris.kernelone.llm.toolkit.executor.handlers import (
            command,
            filesystem,
            navigation,
            repo,
            search,
            session_memory,
            skills,
        )

        cls.register_from_module(command.register_handlers())
        cls.register_from_module(filesystem.register_handlers())
        cls.register_from_module(navigation.register_handlers())
        cls.register_from_module(repo.register_handlers())
        cls.register_from_module(search.register_handlers())
        cls.register_from_module(session_memory.register_handlers())
        cls.register_from_module(skills.register_handlers())

        return cls.get_all()


# Convenience decorator
def register_handler(tool_name: str) -> Callable[[ToolHandler], ToolHandler]:
    """Decorator to register a handler function.

    Usage:
        @register_handler("search_code")
        def _handle_search_code(executor, **kwargs):
            ...

    Args:
        tool_name: Canonical tool name to register

    Returns:
        Decorator function
    """

    def decorator(func: ToolHandler) -> ToolHandler:
        ToolHandlerRegistry.register(tool_name, func)
        return func

    return decorator
