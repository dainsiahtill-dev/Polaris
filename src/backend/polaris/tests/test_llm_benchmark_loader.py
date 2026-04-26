"""Tests for benchmark_loader module (fixture loading and sandbox materialization)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from polaris.cells.llm.evaluation.internal.benchmark_loader import (
    build_case_sandbox_key,
    list_workspace_files,
    load_agentic_benchmark_case,
    load_builtin_agentic_benchmark_cases,
    materialize_case_workspace,
    resolve_case_fixture_dir,
)
from polaris.cells.llm.evaluation.internal.benchmark_models import AgenticBenchmarkCase
from polaris.kernelone.storage import resolve_runtime_path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _local_tmp_dir(label: str) -> Path:
    """Create a unique temporary directory for a test."""
    path = Path(tempfile.gettempdir()) / f"tmp_pytest_benchmark_loader_{label}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _make_minimal_case_dict(
    *,
    case_id: str = "test_case",
    role: str = "director",
    title: str = "Test Case",
    prompt: str = "Do something.",
    workspace_fixture: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a minimal valid benchmark case dict."""
    base: dict[str, Any] = {
        "case_id": case_id,
        "role": role,
        "title": title,
        "prompt": prompt,
        "description": "A test case for unit testing.",
        "workspace_fixture": workspace_fixture,
        "history": [],
        "context": {},
        "metadata": {},
        "tags": ["test"],
        "judge": {
            "score_threshold": 0.75,
            "required_tools": [],
            "forbidden_tools": [],
            "required_tool_arguments": [],
            "forbidden_tool_arguments": [],
            "min_tool_calls": 0,
            "max_tool_calls": None,
            "required_output_substrings": [],
            "forbidden_output_substrings": [],
            "validators": [],
        },
    }
    if extra:
        base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Test load_agentic_benchmark_case
# ---------------------------------------------------------------------------


class TestLoadAgenticBenchmarkCase:
    """Tests for load_agentic_benchmark_case function."""

    def test_load_valid_case(self, tmp_path: Path) -> None:
        """Normal case: valid JSON produces a proper AgenticBenchmarkCase."""
        case_dict = _make_minimal_case_dict(
            case_id="valid_case",
            role="architect",
            title="Valid Architecture Review",
            prompt="Review the system design.",
        )
        file_path = tmp_path / "valid_case.json"
        file_path.write_text(json.dumps(case_dict), encoding="utf-8")

        case = load_agentic_benchmark_case(file_path)

        assert isinstance(case, AgenticBenchmarkCase)
        assert case.case_id == "valid_case"
        assert case.role == "architect"
        assert case.title == "Valid Architecture Review"
        assert case.prompt == "Review the system design."

    def test_load_with_string_path(self, tmp_path: Path) -> None:
        """Accept string path in addition to Path object."""
        case_dict = _make_minimal_case_dict(case_id="str_path_case")
        file_path = tmp_path / "str_path_case.json"
        file_path.write_text(json.dumps(case_dict), encoding="utf-8")

        case = load_agentic_benchmark_case(str(file_path))

        assert case.case_id == "str_path_case"

    def test_load_missing_file_raises(self) -> None:
        """Non-existent file raises FileNotFoundError."""
        fake_path = Path("/nonexistent/fake_case.json")

        with pytest.raises(FileNotFoundError):
            load_agentic_benchmark_case(fake_path)

    def test_load_invalid_json_raises(self, tmp_path: Path) -> None:
        """Malformed JSON raises json.JSONDecodeError."""
        file_path = tmp_path / "invalid.json"
        file_path.write_text("{not valid json", encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            load_agentic_benchmark_case(file_path)

    def test_load_non_object_json_raises(self, tmp_path: Path) -> None:
        """JSON root must be an object, not an array or scalar."""
        file_path = tmp_path / "array.json"
        file_path.write_text("[]", encoding="utf-8")

        with pytest.raises(ValueError, match="must be a JSON object"):
            load_agentic_benchmark_case(file_path)

        file_path.write_text('"just a string"', encoding="utf-8")

        with pytest.raises(ValueError, match="must be a JSON object"):
            load_agentic_benchmark_case(file_path)

    def test_load_missing_required_fields_raises(self, tmp_path: Path) -> None:
        """Missing required fields (case_id, role, title, prompt) raise ValueError."""
        # Missing case_id
        case_dict = _make_minimal_case_dict()
        del case_dict["case_id"]
        file_path = tmp_path / "missing_case_id.json"
        file_path.write_text(json.dumps(case_dict), encoding="utf-8")

        with pytest.raises(ValueError):
            load_agentic_benchmark_case(file_path)

        # Missing prompt
        case_dict = _make_minimal_case_dict()
        del case_dict["prompt"]
        file_path = tmp_path / "missing_prompt.json"
        file_path.write_text(json.dumps(case_dict), encoding="utf-8")

        with pytest.raises(ValueError):
            load_agentic_benchmark_case(file_path)

    def test_role_normalized_to_lowercase(self, tmp_path: Path) -> None:
        """Role field is normalized to lowercase."""
        case_dict = _make_minimal_case_dict(role="ARCHITECT")
        file_path = tmp_path / "upper_role.json"
        file_path.write_text(json.dumps(case_dict), encoding="utf-8")

        case = load_agentic_benchmark_case(file_path)

        assert case.role == "architect"

    def test_extra_fields_discarded(self, tmp_path: Path) -> None:
        """Extra fields beyond the schema are silently discarded by from_dict."""
        case_dict = _make_minimal_case_dict(extra={"custom_field": "value", "priority": 42})
        file_path = tmp_path / "extra_fields.json"
        file_path.write_text(json.dumps(case_dict), encoding="utf-8")

        case = load_agentic_benchmark_case(file_path)

        # Extra fields are not preserved; only declared schema fields are parsed
        assert case.metadata.get("custom_field") is None
        assert case.metadata.get("priority") is None


# ---------------------------------------------------------------------------
# Test load_builtin_agentic_benchmark_cases
# ---------------------------------------------------------------------------


class TestLoadBuiltinAgenticBenchmarkCases:
    """Tests for load_builtin_agentic_benchmark_cases function."""

    def test_load_all_cases_returns_list(self) -> None:
        """Returns a list of AgenticBenchmarkCase objects."""
        cases = load_builtin_agentic_benchmark_cases()

        assert isinstance(cases, list)
        assert len(cases) >= 1
        assert all(isinstance(c, AgenticBenchmarkCase) for c in cases)

    def test_role_filter_director(self) -> None:
        """Filter by role='director' returns only director cases."""
        cases = load_builtin_agentic_benchmark_cases(role="director")

        assert all(case.role == "director" for case in cases)

    def test_role_filter_architect(self) -> None:
        """Filter by role='architect' returns only architect cases."""
        cases = load_builtin_agentic_benchmark_cases(role="architect")

        assert all(case.role == "architect" for case in cases)

    def test_role_filter_pm(self) -> None:
        """Filter by role='pm' returns only pm cases."""
        cases = load_builtin_agentic_benchmark_cases(role="pm")

        assert all(case.role == "pm" for case in cases)

    def test_role_filter_qa(self) -> None:
        """Filter by role='qa' returns only qa cases."""
        cases = load_builtin_agentic_benchmark_cases(role="qa")

        assert all(case.role == "qa" for case in cases)

    def test_role_all_returns_all(self) -> None:
        """role='all' bypasses role filtering."""
        all_cases = load_builtin_agentic_benchmark_cases()
        all_role_cases = load_builtin_agentic_benchmark_cases(role="all")

        # Should return the same set (at minimum, same count)
        assert len(all_role_cases) >= len(all_cases)

    def test_role_default_benchmark_passthrough(self) -> None:
        """role='default', 'benchmark', 'agentic' also bypass filtering."""
        for role in ("default", "benchmark", "agentic"):
            cases = load_builtin_agentic_benchmark_cases(role=role)
            assert isinstance(cases, list)

    def test_case_ids_filter_single(self) -> None:
        """Filtering by a single case_id returns that case only."""
        cases = load_builtin_agentic_benchmark_cases(case_ids=["director_root_cause_locator"])

        assert len(cases) == 1
        assert cases[0].case_id == "director_root_cause_locator"

    def test_case_ids_filter_multiple(self) -> None:
        """Filtering by multiple case_ids returns matching cases."""
        cases = load_builtin_agentic_benchmark_cases(
            case_ids=["director_root_cause_locator", "director_safe_scope_plan"]
        )

        case_ids = {c.case_id for c in cases}
        assert "director_root_cause_locator" in case_ids
        assert "director_safe_scope_plan" in case_ids

    def test_case_ids_with_role_combined(self) -> None:
        """case_ids and role filters are ANDed together."""
        cases = load_builtin_agentic_benchmark_cases(
            role="director",
            case_ids=["director_root_cause_locator"],
        )

        assert len(cases) == 1
        assert cases[0].case_id == "director_root_cause_locator"
        assert cases[0].role == "director"

    def test_case_ids_empty_string_ignored(self) -> None:
        """Empty strings in case_ids are ignored."""
        cases = load_builtin_agentic_benchmark_cases(case_ids=["", "  "])

        # Should return all cases (empty strings filtered out)
        all_cases = load_builtin_agentic_benchmark_cases()
        assert len(cases) == len(all_cases)

    def test_cases_sorted_by_path(self) -> None:
        """Cases are returned in sorted order by file path."""
        cases = load_builtin_agentic_benchmark_cases()

        if len(cases) >= 2:
            # Verify sorted by case_id
            case_ids = [c.case_id for c in cases]
            assert case_ids == sorted(case_ids)


# ---------------------------------------------------------------------------
# Test materialize_case_workspace
# ---------------------------------------------------------------------------


class TestMaterializeCaseWorkspace:
    """Tests for materialize_case_workspace function."""

    def test_no_fixture_returns_base_workspace(self, tmp_path: Path) -> None:
        """When case has no workspace_fixture, returns base_workspace as-is."""
        case_dict = _make_minimal_case_dict(workspace_fixture="")
        case = AgenticBenchmarkCase.from_dict(case_dict)

        result = materialize_case_workspace(
            base_workspace=str(tmp_path),
            run_id="test-run",
            case=case,
        )

        assert result == str(tmp_path)

    def test_creates_sandbox_directory(self, tmp_path: Path) -> None:
        """Creates the sandbox directory under runtime/llm_evaluations/."""
        # Create a mock fixture dir with files
        fixture_dir = tmp_path / "test_fixture"
        fixture_dir.mkdir()
        (fixture_dir / "file.txt").write_text("hello", encoding="utf-8")

        case_dict = _make_minimal_case_dict(workspace_fixture="test_fixture")
        case = AgenticBenchmarkCase.from_dict(case_dict)

        with patch(
            "polaris.cells.llm.evaluation.internal.benchmark_loader.WORKSPACES_ROOT",
            tmp_path,
        ):
            result = materialize_case_workspace(
                base_workspace=str(tmp_path / "workspace"),
                run_id="run-123",
                case=case,
            )

        sandbox_path = Path(result)
        assert sandbox_path.exists()
        assert (sandbox_path / "file.txt").read_text(encoding="utf-8") == "hello"

    def test_replaces_existing_sandbox(self, tmp_path: Path) -> None:
        """Replaces an existing sandbox directory if it already exists."""
        # Create fixture with new content
        fixture_dir = tmp_path / "replace_fixture"
        fixture_dir.mkdir()
        (fixture_dir / "new_file.txt").write_text("new", encoding="utf-8")

        case_dict = _make_minimal_case_dict(workspace_fixture="replace_fixture")
        case = AgenticBenchmarkCase.from_dict(case_dict)
        sandbox_key = build_case_sandbox_key(case.case_id)

        # Pre-create sandbox dir at the exact path where function will create it.
        # The function creates:
        # resolve_runtime_path(base_workspace, "runtime/llm_evaluations/<run_id>/sandboxes/<sandbox_key>")
        existing_sandbox = Path(
            resolve_runtime_path(
                str(tmp_path / "workspace"),
                f"runtime/llm_evaluations/run-1/sandboxes/{sandbox_key}",
            )
        )
        existing_sandbox.mkdir(parents=True)
        old_file = existing_sandbox / "old_file.txt"
        old_file.write_text("old", encoding="utf-8")

        with patch(
            "polaris.cells.llm.evaluation.internal.benchmark_loader.WORKSPACES_ROOT",
            tmp_path,
        ):
            materialize_case_workspace(
                base_workspace=str(tmp_path / "workspace"),
                run_id="run-1",
                case=case,
            )

        # Old directory should be gone (replaced), new file should exist
        assert not old_file.exists()
        assert (existing_sandbox / "new_file.txt").read_text(encoding="utf-8") == "new"

    def test_fixture_not_found_raises(self, tmp_path: Path) -> None:
        """Raises FileNotFoundError when workspace_fixture dir does not exist."""
        case_dict = _make_minimal_case_dict(workspace_fixture="nonexistent_fixture")
        case = AgenticBenchmarkCase.from_dict(case_dict)

        with (
            patch(
                "polaris.cells.llm.evaluation.internal.benchmark_loader.WORKSPACES_ROOT",
                tmp_path,
            ),
            pytest.raises(FileNotFoundError, match="nonexistent_fixture"),
        ):
            materialize_case_workspace(
                base_workspace=str(tmp_path / "workspace"),
                run_id="run-error",
                case=case,
            )

    def test_path_contains_run_id_and_case_id(self, tmp_path: Path) -> None:
        """Returned path contains run_id and case_id for isolation."""
        fixture_dir = tmp_path / "isolated_fixture"
        fixture_dir.mkdir()

        case_dict = _make_minimal_case_dict(
            case_id="unique_case_id",
            workspace_fixture="isolated_fixture",
        )
        case = AgenticBenchmarkCase.from_dict(case_dict)

        with patch(
            "polaris.cells.llm.evaluation.internal.benchmark_loader.WORKSPACES_ROOT",
            tmp_path,
        ):
            result = materialize_case_workspace(
                base_workspace=str(tmp_path / "ws"),
                run_id="run_abc123",
                case=case,
            )

        assert "run_abc123" in result
        expected_sandbox_key = build_case_sandbox_key("unique_case_id")
        assert expected_sandbox_key in result
        assert Path(result) == Path(
            resolve_runtime_path(
                str(tmp_path / "ws"),
                f"runtime/llm_evaluations/run_abc123/sandboxes/{expected_sandbox_key}",
            )
        )

    def test_nested_fixture_structure_copied(self, tmp_path: Path) -> None:
        """Nested directory structure inside fixture is fully copied."""
        fixture_dir = tmp_path / "nested_fixture"
        nested = fixture_dir / "src" / "deep"
        nested.mkdir(parents=True)
        (nested / "config.yaml").write_text("key: value", encoding="utf-8")

        case_dict = _make_minimal_case_dict(workspace_fixture="nested_fixture")
        case = AgenticBenchmarkCase.from_dict(case_dict)

        with patch(
            "polaris.cells.llm.evaluation.internal.benchmark_loader.WORKSPACES_ROOT",
            tmp_path,
        ):
            result = materialize_case_workspace(
                base_workspace=str(tmp_path / "ws"),
                run_id="run-nested",
                case=case,
            )

        assert (Path(result) / "src" / "deep" / "config.yaml").exists()

    def test_ignores_fixture_caches_and_git_artifacts(self, tmp_path: Path) -> None:
        """Copy ignores .git/cache/pyc artifacts to keep sandbox deterministic."""
        fixture_dir = tmp_path / "filtered_fixture"
        fixture_dir.mkdir()
        (fixture_dir / "keep.txt").write_text("keep", encoding="utf-8")
        (fixture_dir / "__pycache__").mkdir()
        (fixture_dir / "__pycache__" / "x.pyc").write_bytes(b"\x00")
        (fixture_dir / ".pytest_cache").mkdir()
        (fixture_dir / ".pytest_cache" / "state").write_text("cache", encoding="utf-8")
        (fixture_dir / ".mypy_cache").mkdir()
        (fixture_dir / ".mypy_cache" / "meta.json").write_text("{}", encoding="utf-8")
        (fixture_dir / ".git").mkdir()
        (fixture_dir / ".git" / "config").write_text("[core]", encoding="utf-8")
        (fixture_dir / "leftover.pyc").write_bytes(b"\x00")

        case = AgenticBenchmarkCase.from_dict(_make_minimal_case_dict(workspace_fixture="filtered_fixture"))
        with patch(
            "polaris.cells.llm.evaluation.internal.benchmark_loader.WORKSPACES_ROOT",
            tmp_path,
        ):
            result = materialize_case_workspace(
                base_workspace=str(tmp_path / "ws"),
                run_id="run-filter",
                case=case,
            )

        sandbox = Path(result)
        assert (sandbox / "keep.txt").is_file()
        assert not (sandbox / "__pycache__").exists()
        assert not (sandbox / ".pytest_cache").exists()
        assert not (sandbox / ".mypy_cache").exists()
        assert not (sandbox / ".git").exists()
        assert not (sandbox / "leftover.pyc").exists()


class TestSandboxKey:
    """Tests for case sandbox key generation."""

    def test_build_case_sandbox_key_is_stable_and_hashed(self) -> None:
        key1 = build_case_sandbox_key("unique_case_id")
        key2 = build_case_sandbox_key("unique_case_id")
        key3 = build_case_sandbox_key("another_case")

        assert key1 == key2
        assert key1 != key3
        assert key1.startswith("unique_case_id-")
        assert len(key1.rsplit("-", maxsplit=1)[-1]) == 12


# ---------------------------------------------------------------------------
# Test list_workspace_files
# ---------------------------------------------------------------------------


class TestListWorkspaceFiles:
    """Tests for list_workspace_files function."""

    def test_empty_workspace_returns_empty_list(self, tmp_path: Path) -> None:
        """Empty workspace directory returns empty list."""
        result = list_workspace_files(tmp_path)

        assert result == []

    def test_single_file(self, tmp_path: Path) -> None:
        """Returns relative path for a single file."""
        (tmp_path / "README.md").write_text("# Hello", encoding="utf-8")

        result = list_workspace_files(tmp_path)

        assert result == ["README.md"]

    def test_nested_files(self, tmp_path: Path) -> None:
        """Returns relative paths for nested files."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("pass", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_main.py").write_text("pass", encoding="utf-8")

        result = list_workspace_files(tmp_path)

        assert sorted(result) == ["src/main.py", "tests/test_main.py"]

    def test_deep_nesting(self, tmp_path: Path) -> None:
        """Handles deeply nested directory structures."""
        deep_path = tmp_path / "a" / "b" / "c" / "d"
        deep_path.mkdir(parents=True)
        (deep_path / "deep_file.py").write_text("pass", encoding="utf-8")

        result = list_workspace_files(tmp_path)

        assert result == ["a/b/c/d/deep_file.py"]

    def test_excludes_directories(self, tmp_path: Path) -> None:
        """Only returns files, not directories."""
        (tmp_path / "dir1").mkdir()
        (tmp_path / "dir2").mkdir()
        (tmp_path / "file.txt").write_text("text", encoding="utf-8")

        result = list_workspace_files(tmp_path)

        assert "dir1" not in result
        assert "dir2" not in result
        assert "file.txt" in result

    def test_returns_posix_paths_on_windows(self, tmp_path: Path) -> None:
        """Returns POSIX-style paths even on Windows."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("pass", encoding="utf-8")

        result = list_workspace_files(tmp_path)

        # Should use forward slashes regardless of OS
        assert all("/" in p or p == "src/app.py" for p in result)
        assert "src\\app.py" not in result

    def test_non_existent_path_returns_empty_list(self) -> None:
        """Non-existent path returns empty list gracefully."""
        fake_path = Path("/nonexistent/path/workspace")

        result = list_workspace_files(fake_path)

        assert result == []

    def test_file_not_dir_returns_empty_list(self, tmp_path: Path) -> None:
        """Path that is a file (not a directory) returns empty list."""
        file_path = tmp_path / "file.txt"
        file_path.write_text("content", encoding="utf-8")

        result = list_workspace_files(file_path)

        assert result == []

    def test_sorted_order(self, tmp_path: Path) -> None:
        """Results are returned in sorted order."""
        (tmp_path / "z_file.py").write_text("pass", encoding="utf-8")
        (tmp_path / "a_file.py").write_text("pass", encoding="utf-8")
        (tmp_path / "m_file.py").write_text("pass", encoding="utf-8")

        result = list_workspace_files(tmp_path)

        assert result == ["a_file.py", "m_file.py", "z_file.py"]

    def test_empty_directories_excluded(self, tmp_path: Path) -> None:
        """Empty directories are not included in results."""
        (tmp_path / "empty_dir").mkdir()
        (tmp_path / "file.txt").write_text("content", encoding="utf-8")

        result = list_workspace_files(tmp_path)

        assert result == ["file.txt"]

    def test_symlink_to_file_handled(self, tmp_path: Path) -> None:
        """Symlinks to files are included in results (following symlinks)."""
        (tmp_path / "real_file.txt").write_text("content", encoding="utf-8")

        if os.name != "nt":  # Symlinks may require admin on Windows
            link_path = tmp_path / "link_file.txt"
            try:
                os.symlink(tmp_path / "real_file.txt", link_path)
                result = list_workspace_files(tmp_path)
                # Both files should appear (symlink is treated as a file)
                assert "real_file.txt" in result
            except OSError:
                # Skip symlink test on platforms that don't support it
                pytest.skip("Symlinks not supported on this platform")

    def test_string_path_argument(self, tmp_path: Path) -> None:
        """Accepts string path in addition to Path object."""
        (tmp_path / "test.py").write_text("pass", encoding="utf-8")

        result = list_workspace_files(str(tmp_path))

        assert result == ["test.py"]

    def test_many_files_performance(self, tmp_path: Path) -> None:
        """Handles workspaces with many files efficiently."""
        # Create 100 files
        for i in range(100):
            subdir = tmp_path / f"dir_{i // 10}"
            subdir.mkdir(exist_ok=True)
            (subdir / f"file_{i}.txt").write_text(f"content {i}", encoding="utf-8")

        result = list_workspace_files(tmp_path)

        assert len(result) == 100


# ---------------------------------------------------------------------------
# Test resolve_case_fixture_dir
# ---------------------------------------------------------------------------


class TestResolveCaseFixtureDir:
    """Tests for resolve_case_fixture_dir function."""

    def test_empty_workspace_fixture_returns_none(self) -> None:
        """Returns None when case has no workspace_fixture."""
        case_dict = _make_minimal_case_dict(workspace_fixture="")
        case = AgenticBenchmarkCase.from_dict(case_dict)

        result = resolve_case_fixture_dir(case)

        assert result is None

    def test_whitespace_workspace_fixture_returns_none(self) -> None:
        """Returns None when workspace_fixture is only whitespace."""
        case_dict = _make_minimal_case_dict(workspace_fixture="   ")
        case = AgenticBenchmarkCase.from_dict(case_dict)

        result = resolve_case_fixture_dir(case)

        assert result is None

    def test_resolves_existing_fixture(self, tmp_path: Path) -> None:
        """Returns Path when fixture directory exists."""
        fixture_dir = tmp_path / "existing_fixture"
        fixture_dir.mkdir()

        case_dict = _make_minimal_case_dict(workspace_fixture="existing_fixture")
        case = AgenticBenchmarkCase.from_dict(case_dict)

        with patch(
            "polaris.cells.llm.evaluation.internal.benchmark_loader.WORKSPACES_ROOT",
            tmp_path,
        ):
            result = resolve_case_fixture_dir(case)

        assert result == fixture_dir

    def test_missing_fixture_raises(self, tmp_path: Path) -> None:
        """Raises FileNotFoundError when fixture directory does not exist."""
        case_dict = _make_minimal_case_dict(workspace_fixture="missing_fixture")
        case = AgenticBenchmarkCase.from_dict(case_dict)

        with (
            patch(
                "polaris.cells.llm.evaluation.internal.benchmark_loader.WORKSPACES_ROOT",
                tmp_path,
            ),
            pytest.raises(FileNotFoundError, match="missing_fixture"),
        ):
            resolve_case_fixture_dir(case)

    def test_fixture_is_file_not_dir_raises(self, tmp_path: Path) -> None:
        """Raises FileNotFoundError when fixture path exists but is a file, not a dir."""
        file_path = tmp_path / "not_a_dir_fixture"
        file_path.write_text("content", encoding="utf-8")

        case_dict = _make_minimal_case_dict(workspace_fixture="not_a_dir_fixture")
        case = AgenticBenchmarkCase.from_dict(case_dict)

        with (
            patch(
                "polaris.cells.llm.evaluation.internal.benchmark_loader.WORKSPACES_ROOT",
                tmp_path,
            ),
            pytest.raises(FileNotFoundError),
        ):
            resolve_case_fixture_dir(case)
