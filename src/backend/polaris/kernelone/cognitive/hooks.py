"""ContextOS HOOKs implementation using Pluggy.

This module provides a plugin hook system for ContextOS lifecycle events,
enabling extensible integration with cognitive components like CriticalThinkingEngine.
"""

from __future__ import annotations

import logging
from typing import Any

import pluggy
from polaris.kernelone.context.context_os.models import (
    TranscriptEvent,
    WorkingState,
)

logger = logging.getLogger(__name__)

# Pluggy hook namespace
CONTEXTOS_HOOK_NAMESPACE = "contextos"

# Create the hook specification marker
hookspec = pluggy.HookspecMarker(CONTEXTOS_HOOK_NAMESPACE)
hookimpl = pluggy.HookimplMarker(CONTEXTOS_HOOK_NAMESPACE)


class ContextOSHooksSpec:
    """Hook specifications for ContextOS lifecycle events."""

    @hookspec
    def on_context_patched(
        self,
        working_state: WorkingState,
        transcript: tuple[TranscriptEvent, ...],
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Called after working state is patched from transcript.

        Args:
            working_state: The newly patched working state.
            transcript: Current transcript log.
            **kwargs: Additional context data.

        Returns:
            Optional dict with metadata about the hook execution.
        """

    @hookspec
    def on_before_episode_sealed(
        self,
        episode_events: tuple[TranscriptEvent, ...],
        working_state: WorkingState,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Called before an episode is sealed.

        Args:
            episode_events: Events that will be sealed into the episode.
            working_state: Current working state.
            **kwargs: Additional context data.

        Returns:
            Optional dict with metadata or veto information.
        """

    @hookspec
    def on_thinking_phase_started(
        self,
        phase_name: str,
        context: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Called when a thinking phase starts.

        Args:
            phase_name: Name of the thinking phase.
            context: Context data for the thinking phase.
            **kwargs: Additional arguments.

        Returns:
            Optional dict with metadata about the phase start.
        """

    @hookspec
    def on_thinking_phase_completed(
        self,
        phase_name: str,
        result: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Called when a thinking phase completes.

        Args:
            phase_name: Name of the thinking phase.
            result: Result data from the thinking phase.
            **kwargs: Additional arguments.

        Returns:
            Optional dict with metadata about the phase completion.
        """


class HookManager:
    """Manages ContextOS hooks and plugin registration.

    This class provides a centralized way to register and call hooks
    for ContextOS lifecycle events using Pluggy.

    Usage:
        # Create manager and register plugins
        manager = HookManager()
        manager.register_plugin(my_plugin)

        # Call hooks
        manager.on_context_patched(working_state, transcript)

        # Get results from all registered plugins
        results = manager.get_hook_results("on_context_patched")
    """

    def __init__(self) -> None:
        """Initialize the hook manager with plugin system."""
        self._plugin_manager = pluggy.PluginManager(CONTEXTOS_HOOK_NAMESPACE)
        self._plugin_manager.add_hookspecs(ContextOSHooksSpec)
        self._registered_plugins: dict[str, Any] = {}
        self._last_results: dict[str, list[dict[str, Any] | None]] = {}

    def register_plugin(self, plugin: Any, name: str | None = None) -> bool:
        """Register a plugin that implements hook specifications.

        Args:
            plugin: Plugin object implementing one or more hookspec methods.
            name: Optional name for the plugin (used for tracking).

        Returns:
            True if registration succeeded, False otherwise.
        """
        plugin_name = name or getattr(plugin, "__class__", type(plugin)).__name__

        if plugin_name in self._registered_plugins:
            logger.debug("Plugin %s already registered, skipping", plugin_name)
            return False

        try:
            self._plugin_manager.register(plugin, name=plugin_name)
            self._registered_plugins[plugin_name] = plugin
            logger.debug("Registered plugin: %s", plugin_name)
            return True
        except (RuntimeError, ValueError) as e:
            logger.exception("Failed to register plugin %s: %s", plugin_name, e)
            return False

    def unregister_plugin(self, plugin: Any, name: str | None = None) -> bool:
        """Unregister a previously registered plugin.

        Args:
            plugin: Plugin object to unregister.
            name: Optional name used when registering. If not provided, uses class name.

        Returns:
            True if unregistration succeeded, False otherwise.
        """
        # Use provided name or look up by class name
        plugin_name = name
        if plugin_name is None:
            plugin_name = getattr(plugin, "__class__", type(plugin)).__name__

        if plugin_name not in self._registered_plugins:
            return False

        try:
            self._plugin_manager.unregister(plugin)
            del self._registered_plugins[plugin_name]
            logger.debug("Unregistered plugin: %s", plugin_name)
            return True
        except (RuntimeError, ValueError) as e:
            logger.exception("Failed to unregister plugin: %s", e)
            return False

    def unregister_plugin_by_name(self, name: str) -> bool:
        """Unregister a plugin by name.

        Args:
            name: Plugin name to unregister.

        Returns:
            True if unregistration succeeded, False otherwise.
        """
        if name not in self._registered_plugins:
            return False

        plugin = self._registered_plugins[name]
        return self.unregister_plugin(plugin, name=name)

    def is_registered(self, name: str) -> bool:
        """Check if a plugin with the given name is registered.

        Args:
            name: Plugin name to check.

        Returns:
            True if registered, False otherwise.
        """
        return name in self._registered_plugins

    def get_registered_plugins(self) -> list[str]:
        """Get list of registered plugin names.

        Returns:
            List of registered plugin names.
        """
        return list(self._registered_plugins.keys())

    def _call_hook(
        self,
        hook_name: str,
        **kwargs: Any,
    ) -> list[dict[str, Any] | None]:
        """Call a hook and collect results from all registered plugins.

        Args:
            hook_name: Name of the hook to call.
            **kwargs: Arguments to pass to the hook.

        Returns:
            List of results from all plugins.
        """
        hook_attr = getattr(self._plugin_manager.hook, hook_name, None)
        if hook_attr is None:
            logger.warning("Hook %s not found", hook_name)
            return []

        try:
            # Call the hook - Pluggy returns a list of results from all implementations
            results: list[dict[str, Any] | None] = hook_attr(**kwargs)
            self._last_results[hook_name] = results
            return results
        except (RuntimeError, ValueError) as e:
            logger.exception("Error calling hook %s: %s", hook_name, e)
            return []

    def on_context_patched(
        self,
        working_state: WorkingState,
        transcript: tuple[TranscriptEvent, ...],
        **kwargs: Any,
    ) -> list[dict[str, Any] | None]:
        """Call on_context_patched hooks on all registered plugins.

        Args:
            working_state: The newly patched working state.
            transcript: Current transcript log.
            **kwargs: Additional context data.

        Returns:
            List of results from all plugins.
        """
        return self._call_hook(
            "on_context_patched",
            working_state=working_state,
            transcript=transcript,
            **kwargs,
        )

    def on_before_episode_sealed(
        self,
        episode_events: tuple[TranscriptEvent, ...],
        working_state: WorkingState,
        **kwargs: Any,
    ) -> list[dict[str, Any] | None]:
        """Call on_before_episode_sealed hooks on all registered plugins.

        Args:
            episode_events: Events that will be sealed into the episode.
            working_state: Current working state.
            **kwargs: Additional context data.

        Returns:
            List of results from all plugins.
        """
        return self._call_hook(
            "on_before_episode_sealed",
            episode_events=episode_events,
            working_state=working_state,
            **kwargs,
        )

    def on_thinking_phase_started(
        self,
        phase_name: str,
        context: dict[str, Any],
        **kwargs: Any,
    ) -> list[dict[str, Any] | None]:
        """Call on_thinking_phase_started hooks on all registered plugins.

        Args:
            phase_name: Name of the thinking phase.
            context: Context data for the thinking phase.
            **kwargs: Additional arguments.

        Returns:
            List of results from all plugins.
        """
        return self._call_hook(
            "on_thinking_phase_started",
            phase_name=phase_name,
            context=context,
            **kwargs,
        )

    def on_thinking_phase_completed(
        self,
        phase_name: str,
        result: dict[str, Any],
        **kwargs: Any,
    ) -> list[dict[str, Any] | None]:
        """Call on_thinking_phase_completed hooks on all registered plugins.

        Args:
            phase_name: Name of the thinking phase.
            result: Result data from the thinking phase.
            **kwargs: Additional arguments.

        Returns:
            List of results from all plugins.
        """
        return self._call_hook(
            "on_thinking_phase_completed",
            phase_name=phase_name,
            result=result,
            **kwargs,
        )

    def get_hook_results(self, hook_name: str) -> list[dict[str, Any] | None]:
        """Get the last results from a specific hook call.

        Args:
            hook_name: Name of the hook to get results for.

        Returns:
            List of results from the last call, or empty list if not called.
        """
        return self._last_results.get(hook_name, [])

    def reset(self) -> None:
        """Reset the hook manager, clearing all plugins and results."""
        self._registered_plugins.clear()
        self._last_results.clear()
        # Re-create plugin manager to clear all registrations
        self._plugin_manager = pluggy.PluginManager(CONTEXTOS_HOOK_NAMESPACE)
        self._plugin_manager.add_hookspecs(ContextOSHooksSpec)


# Global hook manager instance
_global_hook_manager: HookManager | None = None


def get_hook_manager() -> HookManager:
    """Get or create the global hook manager instance.

    Returns:
        The global HookManager instance.
    """
    global _global_hook_manager
    if _global_hook_manager is None:
        _global_hook_manager = HookManager()
    return _global_hook_manager


def reset_hook_manager() -> None:
    """Reset the global hook manager (useful for testing)."""
    global _global_hook_manager
    if _global_hook_manager is not None:
        _global_hook_manager.reset()
    _global_hook_manager = None


def register_plugin(plugin: Any, name: str | None = None) -> bool:
    """Convenience function to register a plugin with the global hook manager.

    Args:
        plugin: Plugin object implementing one or more hookspec methods.
        name: Optional name for the plugin.

    Returns:
        True if registration succeeded, False otherwise.
    """
    return get_hook_manager().register_plugin(plugin, name)
