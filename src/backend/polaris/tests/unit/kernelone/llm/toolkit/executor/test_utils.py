"""Tests for polaris.kernelone.llm.toolkit.executor.utils."""

from __future__ import annotations

from unittest.mock import MagicMock

from polaris.kernelone.llm.exceptions import BudgetExceededError
from polaris.kernelone.llm.toolkit.executor.utils import (
    get_budget_remaining_lines,
    resolve_workspace_path,
    to_workspace_relative_path,
)


class TestResolveWorkspacePath:
    def test_delegates_to_kernel_fs(self) -> None:
        fs = MagicMock()
        fs.resolve_workspace_path.return_value = MagicMock()
        result = resolve_workspace_path(fs, "test.py")
        fs.resolve_workspace_path.assert_called_once_with("test.py")
        assert result is fs.resolve_workspace_path.return_value


class TestToWorkspaceRelativePath:
    def test_delegates_to_kernel_fs(self) -> None:
        fs = MagicMock()
        fs.to_workspace_relative_path.return_value = "test.py"
        path = MagicMock()
        result = to_workspace_relative_path(fs, path)
        fs.to_workspace_relative_path.assert_called_once_with(str(path))
        assert result == "test.py"


class TestGetBudgetRemainingLines:
    def test_none_budget(self) -> None:
        assert get_budget_remaining_lines(None) is None

    def test_token_budget(self) -> None:
        budget = MagicMock()
        budget.max_tokens = 1000
        budget.total_tokens = 200
        result = get_budget_remaining_lines(budget)
        # (1000 - 200) / 25 = 32
        assert result == 32

    def test_byte_budget(self) -> None:
        budget = MagicMock()
        budget.max_tokens = None
        budget.max_result_size_bytes = 1040
        budget.result_size_bytes = 40
        result = get_budget_remaining_lines(budget)
        # (1040 - 40) // 104 = 9
        assert result == 9

    def test_no_budget_info(self) -> None:
        budget = MagicMock()
        budget.max_tokens = None
        budget.max_result_size_bytes = None
        assert get_budget_remaining_lines(budget) is None

    def test_budget_exceeded_error_reexport(self) -> None:
        assert BudgetExceededError is not None
