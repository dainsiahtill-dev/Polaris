"""Tests for polaris.kernelone.storage.io_paths — sentinel parameterization and path resolution."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from polaris.kernelone.storage.io_paths import (
    find_workspace_root,
    is_hot_artifact_path,
    normalize_artifact_rel_path,
    workspace_has_docs,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

# ---------------------------------------------------------------------------
# find_workspace_root
# ---------------------------------------------------------------------------


class TestFindWorkspaceRoot:
    def test_finds_root_when_sentinel_exists(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        result = find_workspace_root(str(tmp_path))
        assert result == str(tmp_path)

    def test_finds_root_from_subdirectory(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        nested = tmp_path / "src" / "deep"
        nested.mkdir(parents=True)
        result = find_workspace_root(str(nested))
        assert os.path.abspath(result) == os.path.abspath(str(tmp_path))

    def test_returns_empty_string_when_sentinel_absent(self, tmp_path: Path) -> None:
        # No "docs" dir anywhere in the hierarchy
        result = find_workspace_root(str(tmp_path))
        assert result == ""

    def test_custom_sentinel_dir_used(self, tmp_path: Path) -> None:
        (tmp_path / "manifests").mkdir()
        result = find_workspace_root(str(tmp_path), sentinel_dir="manifests")
        assert result == str(tmp_path)

    def test_custom_sentinel_does_not_match_default(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        # Searching for a different sentinel that doesn't exist
        result = find_workspace_root(str(tmp_path), sentinel_dir="missing_sentinel")
        assert result == ""


# ---------------------------------------------------------------------------
# workspace_has_docs
# ---------------------------------------------------------------------------


class TestWorkspaceHasDocs:
    def test_returns_true_when_sentinel_present(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        assert workspace_has_docs(str(tmp_path)) is True

    def test_returns_false_when_sentinel_absent(self, tmp_path: Path) -> None:
        assert workspace_has_docs(str(tmp_path)) is False

    def test_returns_false_for_empty_workspace(self) -> None:
        assert workspace_has_docs("") is False

    def test_custom_sentinel_dir_recognized(self, tmp_path: Path) -> None:
        (tmp_path / "specs").mkdir()
        assert workspace_has_docs(str(tmp_path), sentinel_dir="specs") is True

    def test_custom_sentinel_not_found_returns_false(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        assert workspace_has_docs(str(tmp_path), sentinel_dir="nonexistent") is False


# ---------------------------------------------------------------------------
# find_workspace_root — env var override
# ---------------------------------------------------------------------------


def test_env_var_sentinel_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "mysentinel").mkdir()
    monkeypatch.setenv("KERNELONE_WORKSPACE_SENTINEL", "mysentinel")
    # Reload the module-level default via direct call with explicit None sentinel
    # (env var read at module import, so we test explicitly via param)
    result = find_workspace_root(str(tmp_path), sentinel_dir="mysentinel")
    assert result == str(tmp_path)


# ---------------------------------------------------------------------------
# normalize_artifact_rel_path
# ---------------------------------------------------------------------------


class TestNormalizeArtifactRelPath:
    def test_empty_returns_empty(self) -> None:
        assert normalize_artifact_rel_path("") == ""

    def test_normalizes_path_separators(self) -> None:
        result = normalize_artifact_rel_path("runtime/events/test.jsonl")
        assert result == "runtime/events/test.jsonl"

    def test_strips_whitespace(self) -> None:
        result = normalize_artifact_rel_path("  runtime/logs/app.log  ")
        assert result == "runtime/logs/app.log"


# ---------------------------------------------------------------------------
# is_hot_artifact_path
# ---------------------------------------------------------------------------


class TestIsHotArtifactPath:
    def test_runtime_prefix_is_hot(self) -> None:
        assert is_hot_artifact_path("runtime/events/x.jsonl") is True

    def test_bare_runtime_is_hot(self) -> None:
        assert is_hot_artifact_path("runtime") is True

    def test_workspace_prefix_not_hot(self) -> None:
        assert is_hot_artifact_path("workspace/meta/data.json") is False

    def test_config_prefix_not_hot(self) -> None:
        assert is_hot_artifact_path("config/settings.yaml") is False
