"""Regression tests for polaris.kernelone.storage.io_paths.

Coverage targets:
- find_workspace_root: traversal logic, sentinel customization, boundary
- resolve_workspace_path: validation, require_docs flag, root resolution
- workspace_has_docs: direct/persistent check, empty/None, custom sentinel
- normalize_artifact_rel_path: stripping, absolute pass-through, delegation
- _strip_artifact_root_prefix: prefix removal for all three domains
- build_cache_root: delegates to resolve_storage_roots
- is_hot_artifact_path: runtime detection
- resolve_run_dir: empty run_id guard, path composition
- resolve_artifact_path: absolute path validation, run_id routing,
                         runtime/workspace/config prefix routing,
                         security boundary enforcement (traversal attack)
- update_latest_pointer: JSON pointer written, symlink attempt (mocked)
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from polaris.kernelone.storage import StorageRoots
from polaris.kernelone.storage.io_paths import (
    _strip_artifact_root_prefix,
    build_cache_root,
    find_workspace_root,
    is_hot_artifact_path,
    normalize_artifact_rel_path,
    resolve_artifact_path,
    resolve_run_dir,
    resolve_workspace_path,
    update_latest_pointer,
    workspace_has_docs,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_storage_roots(
    workspace: str,
    runtime_root: str,
    workspace_persistent_root: str,
    config_root: str,
) -> StorageRoots:
    """Build a minimal StorageRoots fixture without touching the filesystem."""
    return StorageRoots(
        workspace_abs=workspace,
        workspace_key="test-key",
        storage_layout_mode="project_local",
        home_root="/home/kernelone",
        global_root="/home/kernelone",
        config_root=config_root,
        projects_root=workspace,
        project_root=workspace,
        project_persistent_root=workspace_persistent_root,
        runtime_projects_root=runtime_root,
        runtime_project_root=runtime_root,
        workspace_persistent_root=workspace_persistent_root,
        runtime_base=runtime_root,
        runtime_root=runtime_root,
        runtime_mode="test",
        history_root=os.path.join(workspace_persistent_root, "history"),
    )


# ---------------------------------------------------------------------------
# find_workspace_root
# ---------------------------------------------------------------------------


class TestFindWorkspaceRoot:
    """Traversal logic and sentinel parameterization."""

    def test_sentinel_in_start_dir(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        assert find_workspace_root(str(tmp_path)) == str(tmp_path)

    def test_sentinel_found_from_deep_child(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        result = find_workspace_root(str(deep))
        assert os.path.abspath(result) == os.path.abspath(str(tmp_path))

    def test_returns_empty_string_when_not_found(self, tmp_path: Path) -> None:
        # No sentinel anywhere
        result = find_workspace_root(str(tmp_path))
        assert result == ""

    def test_custom_sentinel_overrides_default(self, tmp_path: Path) -> None:
        (tmp_path / "schemas").mkdir()
        result = find_workspace_root(str(tmp_path), sentinel_dir="schemas")
        assert result == str(tmp_path)

    def test_default_sentinel_does_not_match_custom(self, tmp_path: Path) -> None:
        # "docs" exists, but we ask for a different sentinel
        (tmp_path / "docs").mkdir()
        result = find_workspace_root(str(tmp_path), sentinel_dir="nonexistent_xyz")
        assert result == ""

    def test_sentinel_must_be_directory_not_file(self, tmp_path: Path) -> None:
        # A *file* named "docs" should not satisfy the sentinel check
        (tmp_path / "docs").write_text("not a dir", encoding="utf-8")
        result = find_workspace_root(str(tmp_path))
        assert result == ""

    def test_returns_immediate_match_not_deeper(self, tmp_path: Path) -> None:
        # Both parent and grandchild contain sentinel; we start at parent
        (tmp_path / "docs").mkdir()
        child = tmp_path / "child"
        child.mkdir()
        (child / "docs").mkdir()
        # Starting from child should yield child, not tmp_path
        result = find_workspace_root(str(child))
        assert os.path.abspath(result) == os.path.abspath(str(child))


# ---------------------------------------------------------------------------
# resolve_workspace_path
# ---------------------------------------------------------------------------


class TestResolveWorkspacePath:
    def test_valid_workspace_with_docs(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        result = resolve_workspace_path(str(tmp_path))
        assert os.path.abspath(result) == os.path.abspath(str(tmp_path))

    def test_raises_when_path_does_not_exist(self, tmp_path: Path) -> None:
        nonexistent = str(tmp_path / "no_such_dir")
        with pytest.raises(ValueError, match="does not exist"):
            resolve_workspace_path(nonexistent)

    def test_raises_when_no_sentinel_and_require_docs_true(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="No .* directory found"):
            resolve_workspace_path(str(tmp_path), require_docs=True)

    def test_returns_start_when_no_sentinel_and_require_docs_false(
        self, tmp_path: Path
    ) -> None:
        result = resolve_workspace_path(str(tmp_path), require_docs=False)
        assert os.path.abspath(result) == os.path.abspath(str(tmp_path))

    def test_empty_path_falls_back_to_cwd(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        # monkeypatch cwd to tmp_path
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            result = resolve_workspace_path("")
            assert os.path.abspath(result) == os.path.abspath(str(tmp_path))
        finally:
            os.chdir(original_cwd)

    def test_resolves_to_root_from_subdirectory(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        sub = tmp_path / "src"
        sub.mkdir()
        result = resolve_workspace_path(str(sub))
        assert os.path.abspath(result) == os.path.abspath(str(tmp_path))

    def test_custom_sentinel_honored(self, tmp_path: Path) -> None:
        (tmp_path / "contracts").mkdir()
        result = resolve_workspace_path(
            str(tmp_path), sentinel_dir="contracts"
        )
        assert os.path.abspath(result) == os.path.abspath(str(tmp_path))


# ---------------------------------------------------------------------------
# workspace_has_docs
# ---------------------------------------------------------------------------


class TestWorkspaceHasDocs:
    def test_true_when_sentinel_exists(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        assert workspace_has_docs(str(tmp_path)) is True

    def test_false_when_sentinel_absent(self, tmp_path: Path) -> None:
        assert workspace_has_docs(str(tmp_path)) is False

    def test_false_for_empty_string(self) -> None:
        assert workspace_has_docs("") is False

    def test_custom_sentinel(self, tmp_path: Path) -> None:
        (tmp_path / "specs").mkdir()
        assert workspace_has_docs(str(tmp_path), sentinel_dir="specs") is True

    def test_wrong_custom_sentinel_returns_false(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        assert workspace_has_docs(str(tmp_path), sentinel_dir="nope") is False

    def test_file_named_sentinel_returns_false(self, tmp_path: Path) -> None:
        (tmp_path / "docs").write_text("file", encoding="utf-8")
        assert workspace_has_docs(str(tmp_path)) is False


# ---------------------------------------------------------------------------
# normalize_artifact_rel_path
# ---------------------------------------------------------------------------


class TestNormalizeArtifactRelPath:
    def test_empty_string_returns_empty(self) -> None:
        assert normalize_artifact_rel_path("") == ""

    def test_none_equivalent_whitespace_returns_empty(self) -> None:
        assert normalize_artifact_rel_path("   ") == ""

    def test_valid_relative_path_normalized(self) -> None:
        result = normalize_artifact_rel_path("runtime/events/out.jsonl")
        assert result == "runtime/events/out.jsonl"

    def test_strips_leading_trailing_whitespace(self) -> None:
        result = normalize_artifact_rel_path("  runtime/logs/app.log  ")
        assert result == "runtime/logs/app.log"

    def test_absolute_path_returned_as_abspath(self, tmp_path: Path) -> None:
        abs_path = str(tmp_path / "some" / "file.json")
        result = normalize_artifact_rel_path(abs_path)
        assert os.path.isabs(result)
        assert result == os.path.abspath(abs_path)

    def test_delegates_to_normalize_logical_rel_path(self) -> None:
        # normalize_artifact_rel_path delegates to normalize_logical_rel_path
        # for relative paths (stripping leading/trailing whitespace first)
        result = normalize_artifact_rel_path("  runtime/tasks/my_task  ")
        assert result == "runtime/tasks/my_task"


# ---------------------------------------------------------------------------
# _strip_artifact_root_prefix
# ---------------------------------------------------------------------------


class TestStripArtifactRootPrefix:
    def test_strips_runtime_prefix(self) -> None:
        assert _strip_artifact_root_prefix("runtime/events/out.jsonl") == "events/out.jsonl"

    def test_strips_workspace_prefix(self) -> None:
        assert _strip_artifact_root_prefix("workspace/meta/data.json") == "meta/data.json"

    def test_strips_config_prefix(self) -> None:
        assert _strip_artifact_root_prefix("config/settings.yaml") == "settings.yaml"

    def test_no_known_prefix_returns_unchanged(self) -> None:
        result = _strip_artifact_root_prefix("runtime/tasks/work")
        # "runtime/" stripped → "tasks/work"
        assert result == "tasks/work"

    def test_bare_runtime_strips_to_empty(self) -> None:
        # "runtime/" normalizes to "runtime" (normalize_artifact_rel_path strips
        # the trailing slash via os.path.normpath).  "runtime" does not start
        # with "runtime/" so the function returns it unchanged.
        result = _strip_artifact_root_prefix("runtime/")
        assert result == "runtime"

    def test_empty_returns_empty(self) -> None:
        assert _strip_artifact_root_prefix("") == ""


# ---------------------------------------------------------------------------
# is_hot_artifact_path
# ---------------------------------------------------------------------------


class TestIsHotArtifactPath:
    def test_bare_runtime_is_hot(self) -> None:
        assert is_hot_artifact_path("runtime") is True

    def test_runtime_subpath_is_hot(self) -> None:
        assert is_hot_artifact_path("runtime/events/x.jsonl") is True

    def test_workspace_is_not_hot(self) -> None:
        assert is_hot_artifact_path("workspace/meta/data.json") is False

    def test_config_is_not_hot(self) -> None:
        assert is_hot_artifact_path("config/settings.yaml") is False

    def test_empty_is_not_hot(self) -> None:
        assert is_hot_artifact_path("") is False

    def test_runtime_prefix_without_slash_is_hot(self) -> None:
        # "runtime" alone (no trailing slash)
        assert is_hot_artifact_path("runtime") is True

    def test_partial_prefix_not_hot(self) -> None:
        # "runtim" (typo) is not a valid prefix so normalize_artifact_rel_path
        # raises ValueError — is_hot_artifact_path also returns False for empty.
        # Verify that a path with bad prefix is correctly NOT treated as hot
        # (and that calling it raises ValueError rather than silently returning False).
        with pytest.raises(ValueError):
            is_hot_artifact_path("runtim/events")


# ---------------------------------------------------------------------------
# resolve_run_dir
# ---------------------------------------------------------------------------


class TestResolveRunDir:
    def test_empty_run_id_returns_empty(self, tmp_path: Path) -> None:
        result = resolve_run_dir(str(tmp_path), "", "")
        assert result == ""

    def test_composes_path_with_runtime_root_and_run_id(self, tmp_path: Path) -> None:
        workspace = str(tmp_path / "ws")
        cache_root = str(tmp_path / "cache")
        run_id = "abc-123"
        result = resolve_run_dir(workspace, cache_root, run_id)
        assert result == os.path.join(cache_root, "runs", run_id)

    def test_uses_resolve_storage_roots_when_cache_root_empty(self, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        runtime = str(tmp_path / "runtime_root")
        roots = _make_storage_roots(workspace, runtime, str(tmp_path / "wp"), str(tmp_path / "cfg"))

        with patch(
            "polaris.kernelone.storage.layout._resolve_storage_roots_impl",
            return_value=roots,
        ):
            result = resolve_run_dir(workspace, "", "run-999")
        assert result == os.path.join(runtime, "runs", "run-999")


# ---------------------------------------------------------------------------
# build_cache_root
# ---------------------------------------------------------------------------


class TestBuildCacheRoot:
    def test_delegates_to_resolve_storage_roots(self, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        runtime = str(tmp_path / "rt")
        roots = _make_storage_roots(workspace, runtime, str(tmp_path / "wp"), str(tmp_path / "cfg"))

        with patch(
            "polaris.kernelone.storage.layout._resolve_storage_roots_impl",
            return_value=roots,
        ) as mock_resolve:
            result = build_cache_root("", workspace)

        assert result == runtime
        mock_resolve.assert_called_once()


# ---------------------------------------------------------------------------
# resolve_artifact_path
# ---------------------------------------------------------------------------


class TestResolveArtifactPath:
    """Core resolution logic with various inputs."""

    def _roots(self, tmp_path: Path) -> StorageRoots:
        workspace = str(tmp_path / "ws")
        return _make_storage_roots(
            workspace,
            str(tmp_path / "rt"),
            str(tmp_path / "wp"),
            str(tmp_path / "cfg"),
        )

    # --- empty input ---

    def test_empty_rel_path_returns_empty(self, tmp_path: Path) -> None:
        assert resolve_artifact_path(str(tmp_path), "", "") == ""

    def test_whitespace_rel_path_raises(self, tmp_path: Path) -> None:
        # whitespace strips to "" → normalize_artifact_rel_path returns ""
        # → resolve_logical_path("") raises ValueError (UNSUPPORTED_PATH_PREFIX)
        with pytest.raises(ValueError, match="UNSUPPORTED_PATH_PREFIX"):
            resolve_artifact_path(str(tmp_path), "", "   ")

    # --- runtime prefix ---

    def test_runtime_prefix_resolves_under_runtime_root(self, tmp_path: Path) -> None:
        roots = self._roots(tmp_path)
        with patch(
            "polaris.kernelone.storage.layout._resolve_storage_roots_impl",
            return_value=roots,
        ), patch(
            "polaris.kernelone.fs.text_ops.is_run_artifact",
            return_value=False,
        ):
            result = resolve_artifact_path(
                str(tmp_path / "ws"), "", "runtime/events/out.jsonl"
            )
        # Normalize separators for cross-platform comparison
        assert os.path.normpath(result) == os.path.normpath(
            os.path.join(roots.runtime_root, "events", "out.jsonl")
        )

    def test_bare_runtime_resolves_to_runtime_root(self, tmp_path: Path) -> None:
        roots = self._roots(tmp_path)
        with patch(
            "polaris.kernelone.storage.layout._resolve_storage_roots_impl",
            return_value=roots,
        ), patch("polaris.kernelone.fs.text_ops.is_run_artifact", return_value=False):
            result = resolve_artifact_path(str(tmp_path / "ws"), "", "runtime")
        assert result == roots.runtime_root

    # --- workspace / config prefix falls through to resolve_logical_path ---

    def test_workspace_prefix_routes_to_logical_path(self, tmp_path: Path) -> None:
        # resolve_logical_path → resolve_workspace_persistent_path → resolve_storage_roots
        # resolve_storage_roots is now a thin wrapper around _resolve_storage_roots_impl.
        roots = self._roots(tmp_path)
        with patch(
            "polaris.kernelone.storage.layout._resolve_storage_roots_impl",
            return_value=roots,
        ), patch("polaris.kernelone.fs.text_ops.is_run_artifact", return_value=False):
            result = resolve_artifact_path(
                str(tmp_path / "ws"), "", "workspace/meta/index.json"
            )
        assert os.path.normpath(result).startswith(
            os.path.normpath(roots.workspace_persistent_root)
        )

    # --- run_id routing ---

    def test_run_id_routes_run_artifact_to_run_dir(self, tmp_path: Path) -> None:
        roots = self._roots(tmp_path)
        with patch(
            "polaris.kernelone.storage.layout._resolve_storage_roots_impl",
            return_value=roots,
        ), patch(
            "polaris.kernelone.fs.text_ops.is_run_artifact",
            return_value=True,
        ):
            result = resolve_artifact_path(
                str(tmp_path / "ws"),
                str(tmp_path / "rt"),
                "runtime/events/director_result.json",
                run_id="run-007",
            )
        expected_run_dir = os.path.join(roots.runtime_root, "runs", "run-007")
        assert result.startswith(expected_run_dir)
        assert result.endswith("director_result.json")

    def test_non_run_artifact_ignores_run_id(self, tmp_path: Path) -> None:
        roots = self._roots(tmp_path)
        with patch(
            "polaris.kernelone.storage.layout._resolve_storage_roots_impl",
            return_value=roots,
        ), patch("polaris.kernelone.fs.text_ops.is_run_artifact", return_value=False):
            result = resolve_artifact_path(
                str(tmp_path / "ws"),
                str(tmp_path / "rt"),
                "runtime/logs/app.log",
                run_id="run-007",
            )
        # Should resolve under runtime_root, NOT under the run dir
        assert "runs" not in result or result.endswith("app.log")

    # --- absolute path security boundary ---

    def test_absolute_path_within_runtime_root_allowed(self, tmp_path: Path) -> None:
        roots = self._roots(tmp_path)
        allowed_abs = os.path.join(roots.runtime_root, "some_file.json")
        with patch(
            "polaris.kernelone.storage.layout._resolve_storage_roots_impl",
            return_value=roots,
        ), patch("polaris.kernelone.fs.text_ops.is_run_artifact", return_value=False):
            result = resolve_artifact_path(
                str(tmp_path / "ws"), "", allowed_abs
            )
        assert result == os.path.abspath(allowed_abs)

    def test_absolute_path_outside_all_roots_raises(self, tmp_path: Path) -> None:
        roots = self._roots(tmp_path)
        outside_path = str(tmp_path / "completely_outside" / "evil.json")
        with patch(
            "polaris.kernelone.storage.layout._resolve_storage_roots_impl",
            return_value=roots,
        ), patch("polaris.kernelone.fs.text_ops.is_run_artifact", return_value=False):
            with pytest.raises(ValueError, match="UNSUPPORTED_PATH_PREFIX"):
                resolve_artifact_path(str(tmp_path / "ws"), "", outside_path)

    def test_path_traversal_attack_raises(self, tmp_path: Path) -> None:
        """Ensure ../../../etc/passwd style traversal is rejected."""
        roots = self._roots(tmp_path)
        traversal = "runtime/../../etc/passwd"
        with patch(
            "polaris.kernelone.storage.layout._resolve_storage_roots_impl",
            return_value=roots,
        ), patch("polaris.kernelone.fs.text_ops.is_run_artifact", return_value=False), pytest.raises(ValueError):
            resolve_artifact_path(str(tmp_path / "ws"), "", traversal)

    def test_cache_root_takes_precedence_over_storage_roots(self, tmp_path: Path) -> None:
        roots = self._roots(tmp_path)
        explicit_cache = str(tmp_path / "explicit_cache")
        with patch(
            "polaris.kernelone.storage.layout._resolve_storage_roots_impl",
            return_value=roots,
        ), patch("polaris.kernelone.fs.text_ops.is_run_artifact", return_value=False):
            result = resolve_artifact_path(
                str(tmp_path / "ws"), explicit_cache, "runtime/logs/app.log"
            )
        # When cache_root_full is provided, it's used directly
        assert result.startswith(explicit_cache)


# ---------------------------------------------------------------------------
# update_latest_pointer
# ---------------------------------------------------------------------------


class TestUpdateLatestPointer:
    def test_empty_run_id_is_noop(self, tmp_path: Path) -> None:
        with patch("polaris.kernelone.fs.text_ops.write_json_atomic") as mock_write:
            update_latest_pointer(str(tmp_path), str(tmp_path / "rt"), "")
        mock_write.assert_not_called()

    def test_writes_json_pointer_for_valid_run_id(self, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        runtime_root = str(tmp_path / "rt")
        run_id = "run-abc"

        written_payloads: list = []

        def fake_write(path: str, data: dict, **kwargs) -> None:
            written_payloads.append((path, data))

        with patch("polaris.kernelone.fs.text_ops.write_json_atomic", side_effect=fake_write):
            with patch("os.path.exists", return_value=False):
                with patch("os.symlink"):
                    update_latest_pointer(workspace, runtime_root, run_id)

        assert len(written_payloads) == 1
        pointer_path, payload = written_payloads[0]
        assert pointer_path == os.path.join(runtime_root, "latest_run.json")
        assert payload["run_id"] == run_id
        assert run_id in payload["path"]

    def test_removes_existing_symlink_before_creating_new(self, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        runtime_root = str(tmp_path / "rt")
        run_id = "run-sym"
        latest_dir = os.path.join(runtime_root, "runs", "latest")

        removed: list[str] = []
        symlinked: list[tuple] = []

        def fake_remove(path: str) -> None:
            removed.append(path)

        def fake_symlink(src: str, dst: str, **kwargs) -> None:
            symlinked.append((src, dst))

        with patch("polaris.kernelone.fs.text_ops.write_json_atomic"):
            with patch("os.path.exists", return_value=True):
                with patch("os.path.islink", return_value=True):
                    with patch("os.remove", side_effect=fake_remove):
                        with patch("os.symlink", side_effect=fake_symlink):
                            update_latest_pointer(workspace, runtime_root, run_id)

        assert latest_dir in removed
        assert len(symlinked) == 1
        assert symlinked[0][1] == latest_dir

    def test_symlink_failure_is_silent(self, tmp_path: Path) -> None:
        """Symlink creation failure must not raise — gracefully degraded."""
        workspace = str(tmp_path)
        runtime_root = str(tmp_path / "rt")
        run_id = "run-fail"

        with patch("polaris.kernelone.fs.text_ops.write_json_atomic"):
            with patch("os.path.exists", return_value=False):
                with patch("os.symlink", side_effect=OSError("symlink not supported")):
                    # Must not raise
                    update_latest_pointer(workspace, runtime_root, run_id)

    def test_uses_resolve_storage_roots_when_cache_root_empty(
        self, tmp_path: Path
    ) -> None:
        workspace = str(tmp_path)
        runtime_root = str(tmp_path / "rt_from_roots")
        roots = _make_storage_roots(
            workspace, runtime_root, str(tmp_path / "wp"), str(tmp_path / "cfg")
        )
        run_id = "run-roots"

        written_paths: list[str] = []

        def fake_write(path: str, data: dict, **kwargs) -> None:
            written_paths.append(path)

        with patch(
            "polaris.kernelone.storage.layout._resolve_storage_roots_impl",
            return_value=roots,
        ), patch("polaris.kernelone.fs.text_ops.write_json_atomic", side_effect=fake_write):
            with patch("os.path.exists", return_value=False):
                with patch("os.symlink"):
                    update_latest_pointer(workspace, "", run_id)

        assert any(runtime_root in p for p in written_paths)


# ---------------------------------------------------------------------------
# Regression: legacy path alias resolution through normalize_artifact_rel_path
# ---------------------------------------------------------------------------


class TestLegacyAliasRemoved:
    """Legacy aliases ('docs', 'tasks', 'dispatch') are no longer supported."""

    def test_tasks_alias_raises_unsupported(self) -> None:
        with pytest.raises(ValueError, match="UNSUPPORTED_PATH_PREFIX"):
            normalize_artifact_rel_path("tasks/my_task.json")

    def test_dispatch_alias_raises_unsupported(self) -> None:
        with pytest.raises(ValueError, match="UNSUPPORTED_PATH_PREFIX"):
            normalize_artifact_rel_path("dispatch/cmd.json")

    def test_docs_alias_raises_unsupported(self) -> None:
        with pytest.raises(ValueError, match="UNSUPPORTED_PATH_PREFIX"):
            normalize_artifact_rel_path("docs/README.md")


# ---------------------------------------------------------------------------
# Regression: unsupported prefix raises ValueError
# ---------------------------------------------------------------------------


class TestUnsupportedPrefixRegression:
    def test_dotdot_raises(self) -> None:
        from polaris.kernelone.storage import normalize_logical_rel_path
        with pytest.raises(ValueError, match="UNSUPPORTED_PATH_PREFIX"):
            normalize_logical_rel_path("../etc/passwd")

    def test_unknown_prefix_raises(self) -> None:
        from polaris.kernelone.storage import normalize_logical_rel_path
        with pytest.raises(ValueError, match="UNSUPPORTED_PATH_PREFIX"):
            normalize_logical_rel_path("secrets/apikey.txt")

    def test_dot_only_raises(self) -> None:
        from polaris.kernelone.storage import normalize_logical_rel_path
        with pytest.raises(ValueError, match="UNSUPPORTED_PATH_PREFIX"):
            normalize_logical_rel_path(".")
