"""Architecture tests for ArtifactService - Unified artifact I/O.

These tests verify:
1. ArtifactService can read/write canonical artifacts
2. UTF-8 encoding is properly handled
3. Legacy path fallback works correctly
4. Registry consistency is maintained
"""

import builtins
import importlib
import json
import os
import sys
import tempfile

import pytest


class TestArtifactRegistry:
    """Tests for artifact registry consistency."""

    def test_registry_contains_all_keys(self):
        """Verify all expected artifact keys exist in registry."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            ARTIFACT_REGISTRY,
            LEGACY_KEY_MAPPING,
            list_artifact_keys,
        )

        actual_keys = set(list_artifact_keys())
        assert actual_keys == set(ARTIFACT_REGISTRY.keys())
        assert set(LEGACY_KEY_MAPPING.values()).issubset(actual_keys)

    def test_registry_paths_are_valid(self):
        """Verify all registry paths start with runtime/."""
        from polaris.cells.audit.verdict.internal.artifact_service import ARTIFACT_REGISTRY

        for key, path in ARTIFACT_REGISTRY.items():
            assert path.startswith("runtime/"), f"Key {key} path does not start with runtime/: {path}"

    def test_get_artifact_path_returns_canonical(self):
        """Verify get_artifact_path returns correct path."""
        from polaris.cells.audit.verdict.internal.artifact_service import get_artifact_path

        assert get_artifact_path("PLAN") == "runtime/contracts/plan.md"
        assert get_artifact_path("PM_TASKS_CONTRACT") == "runtime/contracts/pm_tasks.contract.json"
        assert get_artifact_path("DIRECTOR_RESULT") == "runtime/results/director.result.json"
        assert get_artifact_path("RUNTIME_EVENTS") == "runtime/events/runtime.events.jsonl"

    def test_get_artifact_key_returns_key(self):
        """Verify get_artifact_key can reverse lookup."""
        from polaris.cells.audit.verdict.internal.artifact_service import get_artifact_key

        assert get_artifact_key("runtime/contracts/plan.md") == "contract.plan"
        assert get_artifact_key("runtime/contracts/pm_tasks.contract.json") == "contract.pm_tasks"
        assert get_artifact_key("runtime/results/director.result.json") == "runtime.result.director"

    def test_unknown_key_raises_error(self):
        """Verify unknown key raises KeyError."""
        from polaris.cells.audit.verdict.internal.artifact_service import get_artifact_path

        with pytest.raises(KeyError):
            get_artifact_path("NONEXISTENT_KEY")


class TestArtifactServiceBasic:
    """Basic tests for ArtifactService."""

    @pytest.fixture
    def temp_workspace(self):
        """Create temporary workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create docs directory for valid workspace
            docs_dir = os.path.join(tmpdir, "docs")
            os.makedirs(docs_dir)
            yield tmpdir

    def test_service_initialization(self, temp_workspace):
        """Verify service initializes correctly."""
        from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService

        service = ArtifactService(workspace=temp_workspace)
        assert service.workspace == temp_workspace
        assert service.cache_root == ""

    def test_service_with_cache_root(self, temp_workspace):
        """Verify service initializes with cache root."""
        from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService

        cache_root = os.path.join(temp_workspace, ".polaris")
        os.makedirs(cache_root)

        service = ArtifactService(workspace=temp_workspace, cache_root=cache_root)
        assert service.workspace == temp_workspace
        assert service.cache_root == cache_root

    def test_get_path_returns_absolute_path(self, temp_workspace):
        """Verify get_path returns absolute path."""
        from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService

        service = ArtifactService(workspace=temp_workspace)
        plan_path = service.get_path("PLAN")

        assert os.path.isabs(plan_path)
        assert plan_path.replace("\\", "/").endswith("runtime/contracts/plan.md")

    def test_plan_path_uses_canonical_runtime_contract_location(self, temp_workspace):
        """Verify PLAN resolves to the canonical runtime/contracts location."""
        from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService
        from polaris.cells.runtime.artifact_store.public.service import resolve_artifact_path
        from polaris.kernelone.storage.io_paths import build_cache_root

        cache_root = build_cache_root("", temp_workspace)
        service = ArtifactService(workspace=temp_workspace, cache_root=cache_root)

        plan_path = service.get_path("PLAN")
        expected = resolve_artifact_path(
            temp_workspace,
            cache_root,
            "runtime/contracts/plan.md",
        )

        assert plan_path == expected
        assert f"runtime{os.sep}runtime" not in os.path.normpath(plan_path).lower()

    def test_import_keeps_canonical_resolver_when_utf8_helper_missing(self, temp_workspace, monkeypatch):
        """Optional UTF-8 helper import failure must not downgrade path resolution."""
        from polaris.cells.runtime.artifact_store.public.service import resolve_artifact_path
        from polaris.kernelone.storage.io_paths import build_cache_root

        module_name = "polaris.cells.audit.verdict.internal.artifact_service"
        optional_module = "polaris.kernelone.fs.encoding"
        real_import = builtins.__import__

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == optional_module:
                raise ImportError("simulated missing optional UTF-8 helper")
            return real_import(name, globals, locals, fromlist, level)

        with monkeypatch.context() as context:
            context.setattr(builtins, "__import__", guarded_import)
            sys.modules.pop(optional_module, None)
            sys.modules.pop(module_name, None)
            reloaded = importlib.import_module(module_name)

        cache_root = build_cache_root("", temp_workspace)
        service = reloaded.ArtifactService(workspace=temp_workspace, cache_root=cache_root)
        plan_path = service.get_path("PLAN")
        expected = resolve_artifact_path(
            temp_workspace,
            cache_root,
            "runtime/contracts/plan.md",
        )

        assert plan_path == expected
        assert f"runtime{os.sep}runtime" not in os.path.normpath(plan_path).lower()

        sys.modules.pop(module_name, None)
        sys.modules.pop(optional_module, None)
        importlib.import_module(module_name)


class TestArtifactServiceWriteRead:
    """Tests for write and read operations."""

    @pytest.fixture
    def temp_workspace(self):
        """Create temporary workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            docs_dir = os.path.join(tmpdir, "docs")
            os.makedirs(docs_dir)
            yield tmpdir

    def test_write_and_read_plan(self, temp_workspace):
        """Test plan write and read."""
        from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService

        service = ArtifactService(workspace=temp_workspace)
        content = "# Test Plan\n\nThis is a test plan."

        path = service.write_plan(content)
        assert os.path.isfile(path)

        read_content = service.read_plan()
        assert read_content == content

    def test_write_and_read_json(self, temp_workspace):
        """Test JSON artifact write and read."""
        from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService

        service = ArtifactService(workspace=temp_workspace)
        data = {
            "tasks": [
                {"id": "1", "title": "Test Task"},
                {"id": "2", "title": "Another Task"}
            ],
            "overall_goal": "Complete test",
        }

        path = service.write_task_contract(data)
        assert os.path.isfile(path)

        read_data = service.read_task_contract()
        assert read_data == data

    def test_read_nonexistent_returns_none(self, temp_workspace):
        """Test reading nonexistent artifact returns None."""
        from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService

        service = ArtifactService(workspace=temp_workspace)

        result = service.read_task_contract()
        assert result is None

    def test_exists_returns_bool(self, temp_workspace):
        """Test exists method returns correct boolean."""
        from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService

        service = ArtifactService(workspace=temp_workspace)

        # Should not exist initially
        assert not service.exists("PLAN")

        # After write, should exist
        service.write_plan("# Plan")
        assert service.exists("PLAN")

    def test_read_invalid_json_raises_runtime_error(self, temp_workspace):
        """Invalid JSON must fail explicitly instead of returning fallback defaults."""
        from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService

        service = ArtifactService(workspace=temp_workspace)
        path = service.get_path("PM_TASKS_CONTRACT")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{invalid_json}")

        with pytest.raises(RuntimeError, match="invalid"):
            service.read_task_contract()


class TestArtifactServiceUTF8:
    """Tests for UTF-8 encoding handling."""

    @pytest.fixture
    def temp_workspace(self):
        """Create temporary workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            docs_dir = os.path.join(tmpdir, "docs")
            os.makedirs(docs_dir)
            yield tmpdir

    def test_utf8_chinese_characters(self, temp_workspace):
        """Test UTF-8 handling with Chinese characters."""
        from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService

        service = ArtifactService(workspace=temp_workspace)
        content = "# 测试计划\n\n这是一个中文测试内容。"

        path = service.write_plan(content)

        # Read back and verify
        with open(path, encoding="utf-8") as f:
            read_content = f.read()

        assert read_content == content

    def test_utf8_emoji_characters(self, temp_workspace):
        """Test UTF-8 handling with emoji."""
        from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService

        service = ArtifactService(workspace=temp_workspace)
        content = "# Test Plan with Emojis\n\n🎉 Done\n⚠️ Warning"

        path = service.write_plan(content)

        with open(path, encoding="utf-8") as f:
            read_content = f.read()

        assert read_content == content

    def test_utf8_special_characters(self, temp_workspace):
        """Test UTF-8 handling with special characters."""
        from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService

        service = ArtifactService(workspace=temp_workspace)
        content = "# Test with Special Chars\n\n<>&\"' characters\nNewlines\nTabs\t"

        path = service.write_plan(content)

        with open(path, encoding="utf-8") as f:
            read_content = f.read()

        assert read_content == content

    def test_json_utf8_handling(self, temp_workspace):
        """Test JSON with UTF-8 characters."""
        from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService

        service = ArtifactService(workspace=temp_workspace)
        data = {
            "title": "测试项目",
            "tasks": [
                {"id": "1", "name": "任务一"},
                {"id": "2", "name": "任务二"},
            ],
            "emoji": "🎯",
        }

        path = service.write_task_contract(data)

        with open(path, encoding="utf-8") as f:
            read_data = json.load(f)

        assert read_data == data

    def test_read_non_utf8_text_raises_runtime_error(self, temp_workspace):
        """Text artifacts must be UTF-8; decode failure should raise."""
        from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService

        service = ArtifactService(workspace=temp_workspace)
        path = service.get_path("PLAN")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"\xff\xfe\xfd")

        with pytest.raises(RuntimeError, match="not valid UTF-8"):
            service.read_plan()


class TestArtifactServiceAtomic:
    """Tests for atomic write behavior."""

    @pytest.fixture
    def temp_workspace(self):
        """Create temporary workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            docs_dir = os.path.join(tmpdir, "docs")
            os.makedirs(docs_dir)
            yield tmpdir

    def test_atomic_write_creates_temp_file(self, temp_workspace):
        """Verify atomic write uses temp file."""
        from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService

        service = ArtifactService(workspace=temp_workspace)
        content = "# Test Content"

        path = service.write_plan(content)
        dir_path = os.path.dirname(path)

        # No temp files should remain
        temp_files = [f for f in os.listdir(dir_path) if f.endswith(".tmp")]
        assert not temp_files, f"Temp files found: {temp_files}"

    def test_atomic_write_preserves_on_error(self, temp_workspace):
        """Verify existing content is preserved on write error."""
        from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService

        service = ArtifactService(workspace=temp_workspace)

        # Write initial content
        initial_content = "# Initial Content"
        service.write_plan(initial_content)

        # Write new content
        new_content = "# New Content"
        service.write_plan(new_content)

        # Read should have new content
        assert service.read_plan() == new_content


class TestArtifactServiceControlFlags:
    """Tests for control flag operations."""

    @pytest.fixture
    def temp_workspace(self):
        """Create temporary workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            docs_dir = os.path.join(tmpdir, "docs")
            os.makedirs(docs_dir)
            yield tmpdir

    def test_pm_stop_flag_operations(self, temp_workspace):
        """Test PM stop flag create/clear/check."""
        from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService

        service = ArtifactService(workspace=temp_workspace)

        # Should not be set initially
        assert not service.is_pm_stop_requested()

        # Create flag
        service.write_pm_stop_flag()
        assert service.is_pm_stop_requested()

        # Clear flag
        service.clear_pm_stop_flag()
        assert not service.is_pm_stop_requested()

    def test_pause_flag_operations(self, temp_workspace):
        """Test pause flag create/clear/check."""
        from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService

        service = ArtifactService(workspace=temp_workspace)

        # Should not be paused initially
        assert not service.is_paused()

        # Create flag
        service.write_pause_flag()
        assert service.is_paused()

        # Clear flag
        service.clear_pause_flag()
        assert not service.is_paused()


class TestArtifactServiceGeneric:
    """Tests for generic operations."""

    @pytest.fixture
    def temp_workspace(self):
        """Create temporary workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            docs_dir = os.path.join(tmpdir, "docs")
            os.makedirs(docs_dir)
            yield tmpdir

    def test_generic_write_text(self, temp_workspace):
        """Test generic write_text."""
        from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService

        service = ArtifactService(workspace=temp_workspace)
        content = "Generic text content"

        path = service.write_text("PLAN", content)
        assert os.path.isfile(path)
        assert service.read_text("PLAN") == content

    def test_generic_write_json(self, temp_workspace):
        """Test generic write_json."""
        from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService

        service = ArtifactService(workspace=temp_workspace)
        data = {"key": "value", "number": 42}

        path = service.write_json("PM_STATE", data)
        assert os.path.isfile(path)

        read_data = service.read_json("PM_STATE")
        assert read_data == data

    def test_generic_read_nonexistent(self, temp_workspace):
        """Test generic read on nonexistent file."""
        from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService

        service = ArtifactService(workspace=temp_workspace)

        # Text should return empty string
        assert service.read_text("PLAN") == ""

        # JSON should return None
        assert service.read_json("PM_STATE") is None


class TestLegacyPathAliases:
    """Tests for legacy path handling."""

    def test_legacy_aliases_defined(self):
        """Verify legacy path aliases are defined."""
        from polaris.cells.audit.verdict.internal.artifact_service import LEGACY_PATH_ALIASES

        assert len(LEGACY_PATH_ALIASES) > 0
        assert "runtime/contracts/pm_tasks.json" in LEGACY_PATH_ALIASES


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


