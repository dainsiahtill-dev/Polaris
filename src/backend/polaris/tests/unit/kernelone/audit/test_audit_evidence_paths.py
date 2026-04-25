"""Tests for polaris.kernelone.audit.evidence_paths."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

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
        assert _is_within_path("/a/b", "/a/b/c") is True

    def test_same_path(self) -> None:
        assert _is_within_path("/a/b", "/a/b") is True

    def test_outside_parent(self) -> None:
        assert _is_within_path("/a/b", "/x/y") is False

    def test_relative_paths(self) -> None:
        assert _is_within_path(".", "./file.txt") is True

    def test_error_returns_false(self) -> None:
        with patch("os.path.commonpath", side_effect=ValueError("boom")):
            assert _is_within_path("/a", "/b") is False


class TestResolveEvidenceArtifactReference:
    def test_empty_path_raises(self) -> None:
        with pytest.raises(ValueError, match="artifact_path is required"):
            resolve_evidence_artifact_reference(".", "")

    def test_absolute_path(self, tmp_path: Path) -> None:
        artifact = tmp_path / "artifact.txt"
        artifact.write_text("data", encoding="utf-8")
        abs_path, logical = resolve_evidence_artifact_reference(str(tmp_path), str(artifact))
        assert os.path.isabs(abs_path)
        assert logical.startswith("workspace/")

    def test_relative_path(self, tmp_path: Path) -> None:
        abs_path, logical = resolve_evidence_artifact_reference(str(tmp_path), "logs/app.log")
        assert os.path.isabs(abs_path)
        assert logical == "workspace/logs/app.log"


class TestNormalizeFailureRunId:
    def test_valid_run_id(self) -> None:
        assert normalize_failure_run_id("run-123") == "run-123"

    def test_invalid_run_id(self) -> None:
        with pytest.raises(ValueError, match="invalid run_id"):
            normalize_failure_run_id("")
        with pytest.raises(ValueError, match="invalid run_id"):
            normalize_failure_run_id("  ")


class TestResolveFailureHopsOutputPath:
    def test_returns_path(self, tmp_path: Path) -> None:
        with patch(
            "polaris.kernelone.audit.evidence_paths.resolve_runtime_path",
            return_value=str(tmp_path / "out.json"),
        ):
            path = resolve_failure_hops_output_path(str(tmp_path), "run-123")
            assert "failure_hops.json" in path


class TestEnsureRuntimeScopedDirectory:
    def test_valid_directory(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "runtime" / "runs" / "run-123"
        run_dir.mkdir(parents=True)
        with patch(
            "polaris.kernelone.audit.evidence_paths.resolve_storage_roots",
            return_value=MagicMock(runtime_root=str(tmp_path / "runtime")),
        ):
            result = ensure_runtime_scoped_directory(str(tmp_path), str(run_dir))
            assert os.path.isabs(result)

    def test_empty_directory_raises(self) -> None:
        with pytest.raises(ValueError, match="run_dir is required"):
            ensure_runtime_scoped_directory(".", "")

    def test_outside_runtime_raises(self, tmp_path: Path) -> None:
        with patch(
            "polaris.kernelone.audit.evidence_paths.resolve_storage_roots",
            return_value=MagicMock(runtime_root=str(tmp_path / "runtime")),
        ):
            with pytest.raises(ValueError):
                ensure_runtime_scoped_directory(str(tmp_path), "/outside/runtime")
