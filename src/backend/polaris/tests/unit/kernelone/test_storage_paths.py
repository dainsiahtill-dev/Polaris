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
    def test_basic(self) -> None:
        path = resolve_signal_path("/ws", "pm", "plan")
        assert path == Path("/ws") / "runtime/signals" / "plan.pm.signals.json"


class TestResolveArtifactPath:
    def test_basic(self) -> None:
        path = resolve_artifact_path("/ws", "artifact1")
        assert path == Path("/ws") / "runtime/artifacts" / "artifact1"


class TestResolveSessionPath:
    def test_basic(self) -> None:
        path = resolve_session_path("/ws", "sess-123")
        assert path == Path("/ws") / "runtime/sessions" / "sess-123"


class TestResolveTaskboardPath:
    def test_basic(self) -> None:
        path = resolve_taskboard_path("/ws")
        assert path == Path("/ws") / "runtime/tasks" / "taskboard.json"


class TestResolveRuntimePath:
    def test_basic(self) -> None:
        path = resolve_runtime_path("/ws", "foo/bar.txt")
        assert path == Path("/ws") / "runtime" / "foo/bar.txt"
