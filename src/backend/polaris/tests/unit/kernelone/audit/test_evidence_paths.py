"""Unit tests for polaris.kernelone.audit.evidence_paths."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from polaris.kernelone.audit.evidence_paths import (
    _is_within_path,
    ensure_runtime_scoped_directory,
    normalize_failure_run_id,
    resolve_evidence_artifact_reference,
    resolve_failure_hops_output_path,
)


class TestIsWithinPath:
    def test_child_within_parent(self) -> None:
        parent = os.path.abspath("/tmp")
        child = os.path.abspath("/tmp/sub/file.txt")
        assert _is_within_path(parent, child) is True

    def test_same_path(self) -> None:
        path = os.path.abspath("/tmp")
        assert _is_within_path(path, path) is True

    def test_outside_path(self) -> None:
        parent = os.path.abspath("/tmp/a")
        child = os.path.abspath("/tmp/b")
        assert _is_within_path(parent, child) is False

    def test_invalid_path_returns_false(self) -> None:
        assert _is_within_path("", "") is False


class TestResolveEvidenceArtifactReference:
    def test_empty_path_raises(self) -> None:
        with pytest.raises(ValueError, match="artifact_path is required"):
            resolve_evidence_artifact_reference(".", "")

    def test_absolute_path(self, tmp_path: Path) -> None:
        file_path = tmp_path / "artifact.txt"
        file_path.write_text("data", encoding="utf-8")
        abs_path, logical = resolve_evidence_artifact_reference(str(tmp_path), str(file_path))
        assert os.path.isabs(abs_path)
        assert logical.startswith("workspace/") or logical.startswith("runtime/")

    def test_relative_path(self, tmp_path: Path) -> None:
        abs_path, logical = resolve_evidence_artifact_reference(str(tmp_path), "logs/event.json")
        assert os.path.isabs(abs_path)
        assert logical == "workspace/logs/event.json"


class TestNormalizeFailureRunId:
    def test_valid_run_id(self) -> None:
        assert normalize_failure_run_id("run-123") == "run-123"

    def test_invalid_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid run_id"):
            normalize_failure_run_id("")

    def test_whitespace_stripped(self) -> None:
        assert normalize_failure_run_id("  run-abc  ") == "run-abc"


class TestResolveFailureHopsOutputPath:
    def test_returns_path_with_run_id(self, tmp_path: Path) -> None:
        result = resolve_failure_hops_output_path(str(tmp_path), "run-123")
        assert "failure_hops.json" in result
        assert "run-123" in result


class TestEnsureRuntimeScopedDirectory:
    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="run_dir is required"):
            ensure_runtime_scoped_directory(".", "")

    def test_outside_runtime_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="UNSUPPORTED_PATH"):
            ensure_runtime_scoped_directory(str(tmp_path), "/outside")

    def test_runtime_subdir_ok(self, tmp_path: Path) -> None:
        runtime_dir = tmp_path / ".polaris" / "runtime"
        runtime_dir.mkdir(parents=True)
        sub = runtime_dir / "runs" / "run-1"
        sub.mkdir(parents=True)
        result = ensure_runtime_scoped_directory(str(tmp_path), str(sub))
        assert os.path.isabs(result)
