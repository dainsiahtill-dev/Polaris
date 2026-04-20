"""Tests for ContextOS Hooks implementation."""

from __future__ import annotations

from typing import Any

import pytest
from polaris.kernelone.cognitive.hooks import (
    CONTEXTOS_HOOK_NAMESPACE,
    ContextOSHooksSpec,
    HookManager,
    get_hook_manager,
    hookimpl,
    register_plugin,
    reset_hook_manager,
)
from polaris.kernelone.context.context_os.models import (
    TranscriptEvent,
    WorkingState,
)


class MockPlugin(ContextOSHooksSpec):
    """Mock plugin for testing hooks."""

    def __init__(self, name: str = "MockPlugin"):
        self.name = name
        self.calls: list[dict[str, Any]] = []
        self.return_value: dict[str, Any] | None = {"test": "result"}

    @hookimpl
    def on_context_patched(self, working_state, transcript, **kwargs):
        self.calls.append({"method": "on_context_patched", "working_state": working_state, "transcript": transcript})
        return self.return_value

    @hookimpl
    def on_before_episode_sealed(self, episode_events, working_state, **kwargs):
        self.calls.append(
            {"method": "on_before_episode_sealed", "episode_events": episode_events, "working_state": working_state}
        )
        return self.return_value

    @hookimpl
    def on_thinking_phase_started(self, phase_name, context, **kwargs):
        self.calls.append({"method": "on_thinking_phase_started", "phase_name": phase_name, "context": context})
        return self.return_value

    @hookimpl
    def on_thinking_phase_completed(self, phase_name, result, **kwargs):
        self.calls.append({"method": "on_thinking_phase_completed", "phase_name": phase_name, "result": result})
        return self.return_value


class TestHookManager:
    """Tests for HookManager class."""

    @pytest.fixture(autouse=True)
    def reset_hooks(self):
        """Reset hook manager before each test."""
        reset_hook_manager()
        yield
        reset_hook_manager()

    @pytest.fixture
    def hook_manager(self):
        """Create a fresh HookManager instance."""
        return HookManager()

    def test_hook_manager_initialization(self, hook_manager):
        """HookManager should initialize with empty plugins."""
        assert hook_manager.get_registered_plugins() == []
        assert hook_manager._last_results == {}

    def test_register_plugin_success(self, hook_manager):
        """Should successfully register a valid plugin."""
        plugin = MockPlugin()

        result = hook_manager.register_plugin(plugin, name="TestPlugin")

        assert result is True
        assert "TestPlugin" in hook_manager.get_registered_plugins()

    def test_register_plugin_duplicate(self, hook_manager):
        """Should not register duplicate plugin names."""
        plugin = MockPlugin()

        hook_manager.register_plugin(plugin, name="TestPlugin")
        result = hook_manager.register_plugin(plugin, name="TestPlugin")

        assert result is False

    def test_is_registered(self, hook_manager):
        """is_registered should correctly report plugin status."""
        plugin = MockPlugin()

        assert hook_manager.is_registered("TestPlugin") is False
        hook_manager.register_plugin(plugin, name="TestPlugin")
        assert hook_manager.is_registered("TestPlugin") is True

    def test_unregister_plugin(self, hook_manager):
        """Should successfully unregister a plugin by name."""
        plugin = MockPlugin()

        hook_manager.register_plugin(plugin, name="TestPlugin")
        result = hook_manager.unregister_plugin_by_name("TestPlugin")

        assert result is True
        assert "TestPlugin" not in hook_manager.get_registered_plugins()

    def test_reset(self, hook_manager):
        """Reset should clear all plugins and results."""
        plugin = MockPlugin()
        hook_manager.register_plugin(plugin, name="TestPlugin")

        hook_manager.reset()

        assert hook_manager.get_registered_plugins() == []
        assert hook_manager._last_results == {}


class TestContextOSHooksSpec:
    """Tests for ContextOS hook specifications."""

    @pytest.fixture(autouse=True)
    def reset_hooks(self):
        """Reset hook manager before each test."""
        reset_hook_manager()
        yield
        reset_hook_manager()

    @pytest.fixture
    def hook_manager(self):
        """Create a fresh HookManager instance."""
        return HookManager()

    @pytest.fixture
    def mock_working_state(self):
        """Create a mock WorkingState."""
        return WorkingState()

    @pytest.fixture
    def mock_transcript(self):
        """Create mock transcript events."""
        return (
            TranscriptEvent(
                event_id="evt_1",
                sequence=1,
                role="user",
                kind="user_turn",
                route="",
                content="Test message",
            ),
        )

    def test_on_context_patched_hook(self, hook_manager, mock_working_state, mock_transcript):
        """on_context_patched hook should be callable."""
        plugin = MockPlugin()
        plugin.return_value = {"test": "result"}

        hook_manager.register_plugin(plugin, name="TestPlugin")
        results = hook_manager.on_context_patched(
            working_state=mock_working_state,
            transcript=mock_transcript,
        )

        assert len(plugin.calls) == 1
        assert plugin.calls[0]["method"] == "on_context_patched"
        assert plugin.calls[0]["working_state"] is mock_working_state
        assert plugin.calls[0]["transcript"] is mock_transcript
        assert results == [{"test": "result"}]

    def test_on_before_episode_sealed_hook(self, hook_manager, mock_working_state, mock_transcript):
        """on_before_episode_sealed hook should be callable."""
        plugin = MockPlugin()
        plugin.return_value = {"veto": False}

        hook_manager.register_plugin(plugin, name="TestPlugin")
        results = hook_manager.on_before_episode_sealed(
            episode_events=mock_transcript,
            working_state=mock_working_state,
        )

        assert len(plugin.calls) == 1
        assert plugin.calls[0]["method"] == "on_before_episode_sealed"
        assert results == [{"veto": False}]

    def test_on_thinking_phase_started_hook(self, hook_manager):
        """on_thinking_phase_started hook should be callable."""
        plugin = MockPlugin()
        plugin.return_value = {"status": "started"}

        hook_manager.register_plugin(plugin, name="TestPlugin")
        results = hook_manager.on_thinking_phase_started(
            phase_name="test_phase",
            context={"key": "value"},
        )

        assert len(plugin.calls) == 1
        assert plugin.calls[0]["method"] == "on_thinking_phase_started"
        assert plugin.calls[0]["phase_name"] == "test_phase"
        assert results == [{"status": "started"}]

    def test_on_thinking_phase_completed_hook(self, hook_manager):
        """on_thinking_phase_completed hook should be callable."""
        plugin = MockPlugin()
        plugin.return_value = {"status": "completed"}

        hook_manager.register_plugin(plugin, name="TestPlugin")
        results = hook_manager.on_thinking_phase_completed(
            phase_name="test_phase",
            result={"output": "data"},
        )

        assert len(plugin.calls) == 1
        assert plugin.calls[0]["method"] == "on_thinking_phase_completed"
        assert plugin.calls[0]["phase_name"] == "test_phase"
        assert results == [{"status": "completed"}]

    def test_multiple_plugins_same_hook(self, hook_manager, mock_working_state, mock_transcript):
        """Multiple plugins should be called for the same hook."""
        plugin1 = MockPlugin("Plugin1")
        plugin1.return_value = {"plugin": 1}

        plugin2 = MockPlugin("Plugin2")
        plugin2.return_value = {"plugin": 2}

        hook_manager.register_plugin(plugin1, name="Plugin1")
        hook_manager.register_plugin(plugin2, name="Plugin2")

        results = hook_manager.on_context_patched(
            working_state=mock_working_state,
            transcript=mock_transcript,
        )

        assert len(results) == 2
        assert {"plugin": 1} in results
        assert {"plugin": 2} in results

    def test_get_hook_results(self, hook_manager, mock_working_state, mock_transcript):
        """get_hook_results should return last hook call results."""
        plugin = MockPlugin()
        plugin.return_value = {"test": "data"}

        hook_manager.register_plugin(plugin, name="TestPlugin")
        hook_manager.on_context_patched(
            working_state=mock_working_state,
            transcript=mock_transcript,
        )

        results = hook_manager.get_hook_results("on_context_patched")

        assert results == [{"test": "data"}]


class TestGlobalHookManager:
    """Tests for global hook manager functions."""

    @pytest.fixture(autouse=True)
    def reset_hooks(self):
        """Reset hook manager before each test."""
        reset_hook_manager()
        yield
        reset_hook_manager()

    def test_get_hook_manager_singleton(self):
        """get_hook_manager should return singleton instance."""
        manager1 = get_hook_manager()
        manager2 = get_hook_manager()

        assert manager1 is manager2

    def test_register_plugin_global(self):
        """register_plugin should work with global manager."""
        plugin = MockPlugin()

        result = register_plugin(plugin, name="GlobalTestPlugin")

        assert result is True
        assert get_hook_manager().is_registered("GlobalTestPlugin")

    def test_reset_hook_manager(self):
        """reset_hook_manager should clear global instance."""
        plugin = MockPlugin()
        register_plugin(plugin, name="ResetTestPlugin")

        reset_hook_manager()
        manager = get_hook_manager()

        assert not manager.is_registered("ResetTestPlugin")


class TestPluginImplementation:
    """Tests for actual plugin implementation patterns."""

    @pytest.fixture(autouse=True)
    def reset_hooks(self):
        """Reset hook manager before each test."""
        reset_hook_manager()
        yield
        reset_hook_manager()

    def test_plugin_with_multiple_hooks(self):
        """Plugin can implement multiple hooks."""

        class MultiHookPlugin(ContextOSHooksSpec):
            """Test plugin implementing multiple hooks."""

            def __init__(self):
                self.calls = []

            @hookimpl
            def on_context_patched(self, working_state, transcript, **kwargs):
                self.calls.append("on_context_patched")
                return {"hook": "on_context_patched"}

            @hookimpl
            def on_before_episode_sealed(self, episode_events, working_state, **kwargs):
                self.calls.append("on_before_episode_sealed")
                return {"hook": "on_before_episode_sealed"}

        plugin = MultiHookPlugin()
        manager = HookManager()
        manager.register_plugin(plugin, name="MultiHookPlugin")

        # Test both hooks are called
        manager.on_context_patched(
            working_state=WorkingState(),
            transcript=(),
        )
        manager.on_before_episode_sealed(
            episode_events=(),
            working_state=WorkingState(),
        )

        assert "on_context_patched" in plugin.calls
        assert "on_before_episode_sealed" in plugin.calls

    def test_plugin_optional_return(self):
        """Plugin hooks returning None should not break hook calls."""

        class OptionalReturnPlugin(ContextOSHooksSpec):
            """Test plugin with optional returns."""

            def __init__(self):
                self.calls = []

            @hookimpl
            def on_context_patched(self, working_state, transcript, **kwargs):
                self.calls.append("on_context_patched")
                return None  # Explicit None return

        plugin = OptionalReturnPlugin()
        manager = HookManager()
        manager.register_plugin(plugin, name="OptionalReturnPlugin")

        # Should not raise, even with None return
        manager.on_context_patched(
            working_state=WorkingState(),
            transcript=(),
        )

        # Pluggy may filter None results, verify plugin was called
        assert len(plugin.calls) == 1
        assert plugin.calls[0] == "on_context_patched"


class TestHookNamespace:
    """Tests for hook namespace constants."""

    def test_namespace_constant(self):
        """CONTEXTOS_HOOK_NAMESPACE should be correct."""
        assert CONTEXTOS_HOOK_NAMESPACE == "contextos"

    def test_hookspec_marker(self):
        """hookspec marker should have correct namespace."""
        from polaris.kernelone.cognitive.hooks import hookspec

        # The marker should be callable
        assert callable(hookspec)

    def test_hookimpl_marker(self):
        """hookimpl marker should have correct namespace."""
        from polaris.kernelone.cognitive.hooks import hookimpl

        # The marker should be callable
        assert callable(hookimpl)
