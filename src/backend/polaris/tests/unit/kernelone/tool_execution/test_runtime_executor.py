"""Tests for polaris.kernelone.tool_execution.runtime_executor."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from polaris.kernelone.tool_execution.runtime_executor import (
    BackendToolRuntime,
    ReadBudgetGuard,
    ToolArgumentNormalizer,
    ToolCliBuilder,
    WorkspacePathResolver,
)


class TestReadBudgetGuard:
    def test_empty_file_arg_returns_none(self) -> None:
        guard = ReadBudgetGuard()
        assert guard.check_file_budget("", "/tmp") is None

    def test_nonexistent_file_returns_none(self) -> None:
        guard = ReadBudgetGuard()
        assert guard.check_file_budget("/nonexistent/path.txt", "/tmp") is None

    def test_small_file_within_budget(self, tmp_path: Path) -> None:
        guard = ReadBudgetGuard(warn_lines=500, hard_limit=2000)
        file_path = tmp_path / "small.py"
        file_path.write_text("line\n" * 100, encoding="utf-8")
        assert guard.check_file_budget(str(file_path), str(tmp_path)) is None

    def test_hard_limit_exceeded(self, tmp_path: Path) -> None:
        guard = ReadBudgetGuard(warn_lines=500, hard_limit=2000)
        file_path = tmp_path / "large.py"
        # Create a file with ~3000 lines
        file_path.write_text("x\n" * 3000, encoding="utf-8")
        result = guard.check_file_budget(str(file_path), str(tmp_path))
        assert result is not None
        assert result["error_code"] == "BUDGET_EXCEEDED"
        assert result["limit"] == 2000
        # Estimated lines should be reasonably close to 3000
        assert result["line_count"] > 2500

    def test_warn_zone_logs_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        guard = ReadBudgetGuard(warn_lines=100, hard_limit=2000)
        file_path = tmp_path / "medium.py"
        # Create a file with ~600 lines
        file_path.write_text("x\n" * 600, encoding="utf-8")
        with caplog.at_level("WARNING", logger="polaris.kernelone.tool_execution.runtime_executor"):
            result = guard.check_file_budget(str(file_path), str(tmp_path))
        assert result is None
        assert "warn threshold" in caplog.text

    def test_sampling_accuracy(self, tmp_path: Path) -> None:
        guard = ReadBudgetGuard()
        file_path = tmp_path / "exact.py"
        # Create a file with exactly 500 lines
        file_path.write_text("line content here\n" * 500, encoding="utf-8")
        # _estimate_lines should be reasonably accurate
        estimated = guard._estimate_lines(str(file_path), file_path.stat().st_size)
        assert 450 <= estimated <= 550

    def test_sampling_with_no_newlines(self, tmp_path: Path) -> None:
        guard = ReadBudgetGuard()
        file_path = tmp_path / "single_line.bin"
        # Large single-line file
        file_path.write_bytes(b"a" * 100_000)
        estimated = guard._estimate_lines(str(file_path), 100_000)
        # Should fall back to conservative estimate: 100000 // 50 = 2000
        assert estimated >= 1000

    def test_os_error_during_sampling(self, tmp_path: Path) -> None:
        guard = ReadBudgetGuard()
        file_path = tmp_path / "locked.txt"
        file_path.write_text("x\n" * 100, encoding="utf-8")
        with patch("builtins.open", side_effect=OSError("permission denied")):
            estimated = guard._estimate_lines(str(file_path), file_path.stat().st_size)
        # Should fall back to conservative estimate
        assert estimated >= 1

    def test_raise_if_exceeded_budget(self) -> None:
        from polaris.kernelone.llm.exceptions import BudgetExceededError

        guard = ReadBudgetGuard()
        check_result = {
            "error_code": "BUDGET_EXCEEDED",
            "error": "too big",
            "file": "test.py",
            "line_count": 5000,
            "limit": 2000,
            "suggestion": "use slice",
        }
        with pytest.raises(BudgetExceededError):
            guard.raise_if_exceeded(check_result)

    def test_raise_if_exceeded_none(self) -> None:
        guard = ReadBudgetGuard()
        # Should not raise
        guard.raise_if_exceeded(None)

    def test_raise_if_exceeded_check_failed(self) -> None:
        from polaris.kernelone.llm.exceptions import BudgetExceededError

        guard = ReadBudgetGuard()
        check_result = {
            "error_code": "BUDGET_CHECK_FAILED",
            "error": "failed",
            "file": "test.py",
        }
        with pytest.raises(BudgetExceededError):
            guard.raise_if_exceeded(check_result)


class TestWorkspacePathResolver:
    def test_resolve_workspace_path_absolute_outside(self, tmp_path: Path) -> None:
        resolver = WorkspacePathResolver(str(tmp_path))
        with pytest.raises(ValueError, match="outside workspace"):
            resolver.resolve_workspace_path("/etc/passwd")

    def test_resolve_workspace_path_relative_inside(self, tmp_path: Path) -> None:
        resolver = WorkspacePathResolver(str(tmp_path))
        result = resolver.resolve_workspace_path("subdir/file.txt")
        expected = os.path.abspath(str(tmp_path / "subdir" / "file.txt"))
        assert os.path.normpath(str(result)) == os.path.normpath(expected)

    def test_resolve_tool_cwd_missing(self, tmp_path: Path) -> None:
        resolver = WorkspacePathResolver(str(tmp_path))
        with pytest.raises(ValueError, match="not found"):
            resolver.resolve_tool_cwd("nonexistent_dir")


class TestToolArgumentNormalizer:
    def test_normalize_timeout_default(self) -> None:
        normalizer = ToolArgumentNormalizer()
        assert normalizer.normalize_timeout(None) == 30
        assert normalizer.normalize_timeout("abc") == 30
        assert normalizer.normalize_timeout(0) == 30
        assert normalizer.normalize_timeout(-5) == 30

    def test_normalize_timeout_clamps_to_max(self) -> None:
        normalizer = ToolArgumentNormalizer()
        assert normalizer.normalize_timeout(1000) == 600

    def test_as_string_list_none(self) -> None:
        normalizer = ToolArgumentNormalizer()
        assert normalizer.as_string_list(None) == []

    def test_as_string_list_list(self) -> None:
        normalizer = ToolArgumentNormalizer()
        assert normalizer.as_string_list(["a", "b", ""]) == ["a", "b"]

    def test_as_string_list_comma_separated(self) -> None:
        normalizer = ToolArgumentNormalizer()
        assert normalizer.as_string_list("a, b, c") == ["a", "b", "c"]

    def test_workspace_to_repo_relative(self) -> None:
        normalizer = ToolArgumentNormalizer()
        with patch.object(
            ToolArgumentNormalizer, "find_repo_root_path", return_value="/repo"
        ):
            result = normalizer.workspace_to_repo_relative("src/main.py", "/repo")
            assert result == "src/main.py"

    def test_find_repo_root_path_finds_git(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        normalizer = ToolArgumentNormalizer()
        result = normalizer.find_repo_root_path(str(tmp_path / "subdir"))
        assert os.path.normpath(result) == os.path.normpath(str(tmp_path))


class TestToolCliBuilder:
    def test_build_backend_tool_args_empty(self) -> None:
        assert ToolCliBuilder.build_backend_tool_args("read_file", {}) == []

    def test_build_backend_tool_args_raw_list(self) -> None:
        args = {"args": ["a", "b", "c"]}
        result = ToolCliBuilder.build_backend_tool_args("read_file", args)
        assert result == ["a", "b", "c"]

    def test_build_generic_named_args(self) -> None:
        args = {"path": "src/main.py", "verbose": True, "count": 5}
        result = ToolCliBuilder._build_generic_named_args(args)
        assert "--path" in result
        assert "src/main.py" in result
        assert "--verbose" in result
        assert "--count" in result
        assert "5" in result


class TestBackendToolRuntime:
    def test_executor_caching(self, tmp_path: Path) -> None:
        runtime = BackendToolRuntime(str(tmp_path))
        # First call creates executor
        executor1 = runtime._get_executor(str(tmp_path))
        # Second call with same cwd returns cached executor
        executor2 = runtime._get_executor(str(tmp_path))
        assert executor1 is executor2

    def test_executor_cache_eviction(self, tmp_path: Path) -> None:
        runtime = BackendToolRuntime(str(tmp_path))
        runtime._EXECUTOR_CACHE_MAX = 2

        cwd1 = str(tmp_path / "a")
        cwd2 = str(tmp_path / "b")
        cwd3 = str(tmp_path / "c")
        os.makedirs(cwd1, exist_ok=True)
        os.makedirs(cwd2, exist_ok=True)
        os.makedirs(cwd3, exist_ok=True)

        e1 = runtime._get_executor(cwd1)
        e2 = runtime._get_executor(cwd2)
        # Add third should evict first
        e3 = runtime._get_executor(cwd3)

        assert cwd1 not in runtime._executor_cache
        assert cwd2 in runtime._executor_cache
        assert cwd3 in runtime._executor_cache
        assert e3 is runtime._get_executor(cwd3)

    def test_close_clears_cache(self, tmp_path: Path) -> None:
        runtime = BackendToolRuntime(str(tmp_path))
        executor = runtime._get_executor(str(tmp_path))
        assert len(runtime._executor_cache) > 0
        runtime.close()
        assert len(runtime._executor_cache) == 0

    def test_invoke_unknown_tool(self, tmp_path: Path) -> None:
        from polaris.kernelone.llm.exceptions import ToolExecutionError

        runtime = BackendToolRuntime(str(tmp_path))
        with pytest.raises(ToolExecutionError, match="unknown tool"):
            runtime.invoke("nonexistent_tool_xyz")

    def test_invoke_empty_tool_name(self, tmp_path: Path) -> None:
        from polaris.kernelone.llm.exceptions import ToolExecutionError

        runtime = BackendToolRuntime(str(tmp_path))
        with pytest.raises(ToolExecutionError, match="missing tool name"):
            runtime.invoke("")

    def test_list_tools_caches_result(self, tmp_path: Path) -> None:
        runtime = BackendToolRuntime(str(tmp_path))
        tools1 = runtime.list_tools()
        tools2 = runtime.list_tools()
        assert tools1 == tools2
