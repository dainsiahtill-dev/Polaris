"""Tests for polaris.kernelone.storage.paths."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.storage.paths import (
    WORKSPACE_ARTIFACTS,
    WORKSPACE_SESSIONS,
    WORKSPACE_SIGNALS,
    WORKSPACE_TASKS,
    resolve_artifact_path,
    resolve_runtime_path,
    resolve_session_path,
    resolve_signal_path,
    resolve_taskboard_path,
)


class TestStoragePathConstants:
    def test_constants(self) -> None:
        assert WORKSPACE_SIGNALS == "runtime/signals"
        assert WORKSPACE_ARTIFACTS == "runtime/artifacts"
        assert WORKSPACE_SESSIONS == "runtime/sessions"
        assert WORKSPACE_TASKS == "runtime/tasks"


class TestResolveSignalPath:
    def test_basic(self, tmp_path: Path) -> None:
        workspace = tmp_path / "project"
        path = resolve_signal_path(str(workspace), "pm", "plan")
        assert path == workspace / ".polaris/runtime/signals" / "plan.pm.signals.json"


class TestResolveArtifactPath:
    def test_basic(self, tmp_path: Path) -> None:
        workspace = tmp_path / "project"
        path = resolve_artifact_path(str(workspace), "artifact1")
        assert path == workspace / ".polaris/runtime/artifacts" / "artifact1"


class TestResolveSessionPath:
    def test_basic(self, tmp_path: Path) -> None:
        workspace = tmp_path / "project"
        path = resolve_session_path(str(workspace), "sess-123")
        assert path == workspace / ".polaris/runtime/sessions" / "sess-123"


class TestResolveTaskboardPath:
    def test_basic(self, tmp_path: Path) -> None:
        workspace = tmp_path / "project"
        path = resolve_taskboard_path(str(workspace))
        assert path == workspace / ".polaris/runtime/tasks" / "taskboard.json"


class TestResolveRuntimePath:
    def test_basic(self, tmp_path: Path) -> None:
        workspace = tmp_path / "project"
        path = resolve_runtime_path(str(workspace), "foo/bar.txt")
        assert path == workspace / ".polaris/runtime" / "foo/bar.txt"

    def test_accepts_legacy_runtime_logical_prefix_without_nesting(self, tmp_path: Path) -> None:
        workspace = tmp_path / "project"
        path = resolve_runtime_path(str(workspace), "runtime/signals/pm.json")
        assert path == workspace / ".polaris/runtime/signals/pm.json"
