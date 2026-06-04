"""Tests for polaris.delivery.cli.pm.director_interface_core module."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from polaris.delivery.cli.pm.director_interface_core import (
    CanonicalDirectorAdapter,
    DirectorFactory,
    DirectorInterface,
    DirectorResult,
    DirectorTask,
    NoDirectorAdapter,
    create_director,
)


class TestDirectorTask:
    """Tests for DirectorTask dataclass."""

    def test_basic_creation(self) -> None:
        """Test basic task creation."""
        task = DirectorTask(
            task_id="task-1",
            goal="Test goal",
            target_files=["file.py"],
            acceptance_criteria=["criteria1"],
            constraints=["constraint1"],
            context={},
        )
        assert task.task_id == "task-1"
        assert task.goal == "Test goal"

    def test_default_scope_paths(self) -> None:
        """Test default scope_paths is None but normalized to []."""
        task = DirectorTask(
            task_id="task-1",
            goal="Test",
            target_files=["file.py"],
            acceptance_criteria=[],
            constraints=[],
            context={},
        )
        assert task.scope_paths == []

    def test_default_scope_mode(self) -> None:
        """Test default scope_mode is 'module'."""
        task = DirectorTask(
            task_id="task-1",
            goal="Test",
            target_files=["file.py"],
            acceptance_criteria=[],
            constraints=[],
            context={},
        )
        assert task.scope_mode == "module"

    def test_post_init_normalizes_target_files(self) -> None:
        """Test target_files are normalized."""
        task = DirectorTask(
            task_id="task-1",
            goal="Test",
            target_files=None,  # type: ignore[arg-type]
            acceptance_criteria=[],
            constraints=[],
            context={},
        )
        assert task.target_files == []

    def test_post_init_normalizes_acceptance_criteria_non_list(self) -> None:
        """Test acceptance_criteria reset to [] when not a list."""
        task = DirectorTask(
            task_id="task-1",
            goal="Test",
            target_files=["file.py"],
            acceptance_criteria="not a list",  # type: ignore[arg-type]
            constraints=[],
            context={},
        )
        assert task.acceptance_criteria == []

    def test_post_init_normalizes_constraints_non_list(self) -> None:
        """Test constraints reset to [] when not a list."""
        task = DirectorTask(
            task_id="task-1",
            goal="Test",
            target_files=["file.py"],
            acceptance_criteria=[],
            constraints="not a list",  # type: ignore[arg-type]
            context={},
        )
        assert task.constraints == []


class TestDirectorResult:
    """Tests for DirectorResult dataclass."""

    def test_basic_creation(self) -> None:
        """Test basic result creation."""
        result = DirectorResult(
            success=True,
            task_id="task-1",
            changed_files=["file.py"],
            patches=[],
        )
        assert result.success is True
        assert result.task_id == "task-1"

    def test_post_init_normalizes_changed_files(self) -> None:
        """Test changed_files are normalized."""
        result = DirectorResult(
            success=True,
            task_id="task-1",
            changed_files=None,  # type: ignore[arg-type]
            patches=[],
        )
        assert result.changed_files == []

    def test_post_init_default_metadata(self) -> None:
        """Test metadata defaults to {}."""
        result = DirectorResult(
            success=True,
            task_id="task-1",
            changed_files=[],
            patches=[],
        )
        assert result.metadata == {}

    def test_error_field(self) -> None:
        """Test error field."""
        result = DirectorResult(
            success=False,
            task_id="task-1",
            changed_files=[],
            patches=[],
            error="Something failed",
        )
        assert result.error == "Something failed"


class TestDirectorInterface:
    """Tests for DirectorInterface abstract class."""

    def test_cannot_instantiate_directly(self) -> None:
        """Test DirectorInterface cannot be instantiated directly."""
        with pytest.raises(TypeError):
            DirectorInterface(Path("/tmp"))

    def test_workspace_stored(self) -> None:
        """Test workspace is stored as Path."""

        class ConcreteDirector(DirectorInterface):
            def execute(self, task):
                pass

            def is_available(self):
                return True

            def get_info(self):
                return {}

        director = ConcreteDirector("/tmp/workspace")
        assert director.workspace == Path("/tmp/workspace")

    def test_config_defaults_to_empty_dict(self) -> None:
        """Test config defaults to empty dict."""

        class ConcreteDirector(DirectorInterface):
            def execute(self, task):
                pass

            def is_available(self):
                return True

            def get_info(self):
                return {}

        director = ConcreteDirector("/tmp")
        assert director.config == {}


class TestNoDirectorAdapter:
    """Tests for NoDirectorAdapter."""

    def test_is_available_always_true(self) -> None:
        """Test is_available always returns True."""
        adapter = NoDirectorAdapter(Path("/tmp"))
        assert adapter.is_available() is True

    def test_execute_returns_success(self) -> None:
        """Test execute returns success result."""
        adapter = NoDirectorAdapter(Path("/tmp"))
        task = DirectorTask(
            task_id="task-1",
            goal="Test",
            target_files=[],
            acceptance_criteria=[],
            constraints=[],
            context={},
        )
        result = adapter.execute(task)
        assert result.success is True
        assert result.task_id == "task-1"

    def test_execute_returns_empty_changed_files(self) -> None:
        """Test execute returns empty changed_files."""
        adapter = NoDirectorAdapter(Path("/tmp"))
        task = DirectorTask(
            task_id="task-1",
            goal="Test",
            target_files=[],
            acceptance_criteria=[],
            constraints=[],
            context={},
        )
        result = adapter.execute(task)
        assert result.changed_files == []
        assert result.patches == []

    def test_execute_metadata_contains_note(self) -> None:
        """Test execute metadata contains mode note."""
        adapter = NoDirectorAdapter(Path("/tmp"))
        task = DirectorTask(
            task_id="task-1",
            goal="Test",
            target_files=[],
            acceptance_criteria=[],
            constraints=[],
            context={},
        )
        result = adapter.execute(task)
        assert "note" in result.metadata
        assert "standalone" in result.metadata["note"].lower()

    def test_get_info(self) -> None:
        """Test get_info returns expected dict."""
        adapter = NoDirectorAdapter(Path("/tmp"))
        info = adapter.get_info()
        assert info["type"] == "none"
        assert "standalone" in info["name"].lower()

    def test_config_override(self) -> None:
        """Test config can be overridden."""
        adapter = NoDirectorAdapter(Path("/tmp"), {"key": "value"})
        assert adapter.config == {"key": "value"}


class TestCanonicalDirectorAdapter:
    """Tests for CanonicalDirectorAdapter."""

    def test_is_available(self) -> None:
        """The canonical adapter is always selected for materialization."""
        adapter = CanonicalDirectorAdapter(Path("/tmp"))
        assert adapter.is_available() is True

    def test_get_info(self) -> None:
        """Test get_info returns canonical adapter metadata."""
        adapter = CanonicalDirectorAdapter(Path("/tmp"))
        info = adapter.get_info()
        assert info["type"] == "canonical"
        assert info["adapter"] == "roles.adapters.director"

    def test_to_orchestrator_task_preserves_contract_fields(self) -> None:
        """Task conversion preserves fields needed by DirectorOrchestrator."""
        task = DirectorTask(
            task_id="TASK-1",
            goal="Implement feature",
            target_files=["src/app.py"],
            acceptance_criteria=["works"],
            constraints=["no external writes"],
            context={"task": {"metadata": {"priority": "high"}}},
            scope_paths=["src"],
            scope_mode="module",
        )
        payload = CanonicalDirectorAdapter._to_orchestrator_task(task)
        assert payload["id"] == "TASK-1"
        assert payload["description"] == "Implement feature"
        assert payload["target_files"] == ["src/app.py"]
        assert payload["scope_paths"] == ["src"]
        assert payload["metadata"]["source"] == "pm.director_interface"
        assert payload["metadata"]["priority"] == "high"

    def test_execute_delegates_to_director_orchestrator(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """execute() must use DirectorOrchestrator, not a subprocess adapter."""
        captured: dict[str, object] = {}

        class _Result:
            success = True
            error = ""
            metadata = {
                "changed_files": ["src/app.py"],
                "adapter_result": {"patches": [{"path": "src/app.py"}]},
            }

        class _Orchestrator:
            def __init__(self, config) -> None:
                captured["config"] = config

            async def execute_task(self, payload):
                captured["payload"] = payload
                return _Result()

        monkeypatch.setattr(
            "polaris.application.orchestration.director_orchestrator.DirectorOrchestrator",
            _Orchestrator,
        )
        adapter = CanonicalDirectorAdapter(tmp_path, {"model": "test-model", "timeout": 30})
        result = adapter.execute(
            DirectorTask(
                task_id="TASK-1",
                goal="Implement feature",
                target_files=[],
                acceptance_criteria=[],
                constraints=[],
                context={},
            )
        )

        assert result.success is True
        assert result.changed_files == ["src/app.py"]
        assert result.patches == [{"path": "src/app.py"}]
        assert result.metadata["canonical_role_adapter"] == "roles.adapters.director"
        assert captured["payload"]


class TestDirectorFactory:
    """Tests for DirectorFactory."""

    def test_create_canonical_director(self, tmp_path: Path) -> None:
        """Test creating canonical director."""
        director = DirectorFactory.create("canonical", tmp_path)
        assert isinstance(director, CanonicalDirectorAdapter)

    def test_script_alias_routes_to_canonical_director(self, tmp_path: Path) -> None:
        """Historical script configuration values no longer spawn scripts."""
        director = DirectorFactory.create("script", tmp_path)
        assert isinstance(director, CanonicalDirectorAdapter)

    def test_create_none_director(self, tmp_path: Path) -> None:
        """Test creating none director."""
        director = DirectorFactory.create("none", tmp_path)
        assert isinstance(director, NoDirectorAdapter)

    def test_create_unknown_type_raises(self, tmp_path: Path) -> None:
        """Test creating unknown director type raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            DirectorFactory.create("unknown", tmp_path)
        assert "Unknown director type" in str(exc_info.value)

    def test_list_available(self) -> None:
        """Test listing available director types."""
        types = DirectorFactory.list_available()
        assert "canonical" in types
        assert "none" in types
        assert "script" not in types

    def test_register_new_director(self, tmp_path: Path) -> None:
        """Test registering a new director type."""

        class MockDirector(DirectorInterface):
            def execute(self, task):
                pass

            def is_available(self):
                return True

            def get_info(self):
                return {}

        DirectorFactory.register("mock", MockDirector)
        assert "mock" in DirectorFactory.list_available()
        director = DirectorFactory.create("mock", tmp_path)
        assert isinstance(director, MockDirector)

    def test_register_non_subclass_raises(self) -> None:
        """Test registering non-subclass raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            DirectorFactory.register("bad", str)
        assert "must inherit" in str(exc_info.value)


class TestCreateDirector:
    """Tests for create_director convenience function."""

    def test_explicit_type(self, tmp_path: Path) -> None:
        """Test explicit director type."""
        director = create_director(str(tmp_path), director_type="none")
        assert isinstance(director, NoDirectorAdapter)

    def test_auto_uses_canonical(self, tmp_path: Path) -> None:
        """Test auto mode uses the canonical adapter."""
        director = create_director(str(tmp_path), director_type="auto")
        assert isinstance(director, CanonicalDirectorAdapter)

    def test_from_env_var(self, tmp_path: Path) -> None:
        """Test director type from environment variable."""
        with patch.dict(os.environ, {"KERNELONE_DIRECTOR_TYPE": "none"}):
            director = create_director(str(tmp_path))
            assert isinstance(director, NoDirectorAdapter)

    def test_default_env_var(self, tmp_path: Path) -> None:
        """Test default when env var is not set."""
        with patch.dict(os.environ, {"KERNELONE_DIRECTOR_TYPE": "auto"}, clear=True):
            director = create_director(str(tmp_path))
            assert isinstance(director, CanonicalDirectorAdapter)

    def test_workspace_converted_to_path(self, tmp_path: Path) -> None:
        """Test workspace string converted to Path."""
        director = create_director(str(tmp_path), "none")
        assert director.workspace == tmp_path


class TestModuleExports:
    """Tests for module exports."""

    def test_all_exports(self) -> None:
        """Test __all__ contains expected exports."""
        from polaris.delivery.cli.pm.director_interface_core import __all__

        assert "DirectorFactory" in __all__
        assert "DirectorInterface" in __all__
        assert "DirectorResult" in __all__
        assert "DirectorTask" in __all__
        assert "CanonicalDirectorAdapter" in __all__
        assert "NoDirectorAdapter" in __all__
        assert "create_director" in __all__
