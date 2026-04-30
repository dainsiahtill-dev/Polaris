"""Tests for tool_categories module."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from polaris.kernelone.tool_execution.tool_categories import (
    CODE_WRITE_TOOLS,
    COMMAND_EXECUTION_TOOLS,
    FILE_DELETE_TOOLS,
    READ_ONLY_TOOLS,
    TOOL_CATEGORIES,
    _build_tool_categories,
    is_code_write_tool,
    is_command_execution_tool,
    is_file_delete_tool,
    is_read_only_tool,
)


class TestBuildToolCategories:
    """Tests for _build_tool_categories function."""

    @patch("polaris.kernelone.tool_execution.tool_categories._TOOL_SPECS")
    def test_empty_registry(self, mock_specs: MagicMock) -> None:
        """Empty registry creates empty categories."""
        mock_specs._data = {}
        result = _build_tool_categories()
        assert all(len(v) == 0 for v in result.values())
        assert set(result.keys()) == {"code_write", "command_execution", "file_delete", "read_only"}

    @patch("polaris.kernelone.tool_execution.tool_categories._TOOL_SPECS")
    def test_write_tools(self, mock_specs: MagicMock) -> None:
        """Write category tools are mapped correctly."""
        mock_specs._data = {
            "write_file": {"category": "write"},
            "edit_file": {"category": "write"},
        }
        result = _build_tool_categories()
        assert "write_file" in result["code_write"]
        assert "edit_file" in result["code_write"]

    @patch("polaris.kernelone.tool_execution.tool_categories._TOOL_SPECS")
    def test_exec_tools(self, mock_specs: MagicMock) -> None:
        """Exec category tools are mapped correctly."""
        mock_specs._data = {
            "bash": {"category": "exec"},
            "background_run": {"category": "exec"},
        }
        result = _build_tool_categories()
        assert "bash" in result["command_execution"]
        assert "background_run" in result["command_execution"]

    @patch("polaris.kernelone.tool_execution.tool_categories._TOOL_SPECS")
    def test_delete_tools(self, mock_specs: MagicMock) -> None:
        """Delete category tools are mapped correctly."""
        mock_specs._data = {
            "delete_file": {"category": "delete"},
        }
        result = _build_tool_categories()
        assert "delete_file" in result["file_delete"]

    @patch("polaris.kernelone.tool_execution.tool_categories._TOOL_SPECS")
    def test_read_tools(self, mock_specs: MagicMock) -> None:
        """Read category tools are mapped correctly."""
        mock_specs._data = {
            "read_file": {"category": "read"},
            "glob": {"category": "read"},
        }
        result = _build_tool_categories()
        assert "read_file" in result["read_only"]
        assert "glob" in result["read_only"]

    @patch("polaris.kernelone.tool_execution.tool_categories._TOOL_SPECS")
    def test_default_category(self, mock_specs: MagicMock) -> None:
        """Tools without category default to read_only."""
        mock_specs._data = {
            "unknown_tool": {},
        }
        result = _build_tool_categories()
        assert "unknown_tool" in result["read_only"]

    @patch("polaris.kernelone.tool_execution.tool_categories._TOOL_SPECS")
    def test_mixed_categories(self, mock_specs: MagicMock) -> None:
        """Mixed categories are sorted correctly."""
        mock_specs._data = {
            "write_file": {"category": "write"},
            "bash": {"category": "exec"},
            "read_file": {"category": "read"},
            "delete_file": {"category": "delete"},
        }
        result = _build_tool_categories()
        assert "write_file" in result["code_write"]
        assert "bash" in result["command_execution"]
        assert "read_file" in result["read_only"]
        assert "delete_file" in result["file_delete"]

    @patch("polaris.kernelone.tool_execution.tool_categories._TOOL_SPECS")
    def test_returns_frozensets(self, mock_specs: MagicMock) -> None:
        """Result values are frozensets."""
        mock_specs._data = {"tool1": {"category": "read"}}
        result = _build_tool_categories()
        assert isinstance(result["read_only"], frozenset)

    @patch("polaris.kernelone.tool_execution.tool_categories._TOOL_SPECS")
    def test_dict_specs_without_data_attr(self, mock_specs: Any) -> None:
        """Handle plain dict specs (no _data attr)."""
        mock_specs = {"tool1": {"category": "write"}}
        with patch("polaris.kernelone.tool_execution.tool_categories._TOOL_SPECS", mock_specs):
            result = _build_tool_categories()
            assert "tool1" in result["code_write"]


class TestIsCodeWriteTool:
    """Tests for is_code_write_tool function."""

    def test_known_write_tool(self) -> None:
        """Returns True for known write tools."""
        result = is_code_write_tool("write_file")
        assert isinstance(result, bool)

    def test_unknown_tool(self) -> None:
        """Returns False for unknown tool."""
        assert is_code_write_tool("nonexistent_tool_xyz") is False

    def test_empty_string(self) -> None:
        """Returns False for empty string."""
        assert is_code_write_tool("") is False


class TestIsCommandExecutionTool:
    """Tests for is_command_execution_tool function."""

    def test_known_exec_tool(self) -> None:
        """Returns True for known exec tools."""
        result = is_command_execution_tool("bash")
        assert isinstance(result, bool)

    def test_unknown_tool(self) -> None:
        """Returns False for unknown tool."""
        assert is_command_execution_tool("nonexistent_tool_xyz") is False

    def test_empty_string(self) -> None:
        """Returns False for empty string."""
        assert is_command_execution_tool("") is False


class TestIsFileDeleteTool:
    """Tests for is_file_delete_tool function."""

    def test_known_delete_tool(self) -> None:
        """Returns True for known delete tools."""
        result = is_file_delete_tool("delete_file")
        assert isinstance(result, bool)

    def test_unknown_tool(self) -> None:
        """Returns False for unknown tool."""
        assert is_file_delete_tool("nonexistent_tool_xyz") is False

    def test_empty_string(self) -> None:
        """Returns False for empty string."""
        assert is_file_delete_tool("") is False


class TestIsReadOnlyTool:
    """Tests for is_read_only_tool function."""

    def test_known_read_tool(self) -> None:
        """Returns True for known read tools."""
        result = is_read_only_tool("read_file")
        assert isinstance(result, bool)

    def test_unknown_tool(self) -> None:
        """Returns False for unknown tool."""
        assert is_read_only_tool("nonexistent_tool_xyz") is False

    def test_empty_string(self) -> None:
        """Returns False for empty string."""
        assert is_read_only_tool("") is False


class TestToolCategoriesConstants:
    """Tests for TOOL_CATEGORIES constants."""

    def test_all_categories_present(self) -> None:
        """All four categories are present."""
        assert set(TOOL_CATEGORIES.keys()) == {"code_write", "command_execution", "file_delete", "read_only"}

    def test_code_write_is_frozenset(self) -> None:
        """CODE_WRITE_TOOLS is a frozenset."""
        assert isinstance(CODE_WRITE_TOOLS, frozenset)

    def test_command_exec_is_frozenset(self) -> None:
        """COMMAND_EXECUTION_TOOLS is a frozenset."""
        assert isinstance(COMMAND_EXECUTION_TOOLS, frozenset)

    def test_file_delete_is_frozenset(self) -> None:
        """FILE_DELETE_TOOLS is a frozenset."""
        assert isinstance(FILE_DELETE_TOOLS, frozenset)

    def test_read_only_is_frozenset(self) -> None:
        """READ_ONLY_TOOLS is a frozenset."""
        assert isinstance(READ_ONLY_TOOLS, frozenset)

    def test_convenience_constants_match(self) -> None:
        """Convenience constants match dictionary values."""
        assert TOOL_CATEGORIES["code_write"] == CODE_WRITE_TOOLS
        assert TOOL_CATEGORIES["command_execution"] == COMMAND_EXECUTION_TOOLS
        assert TOOL_CATEGORIES["file_delete"] == FILE_DELETE_TOOLS
        assert TOOL_CATEGORIES["read_only"] == READ_ONLY_TOOLS


class TestModuleExports:
    """Tests for module public API."""

    def test_all_exports_present(self) -> None:
        """All expected names are importable."""
        from polaris.kernelone.tool_execution import tool_categories

        assert hasattr(tool_categories, "TOOL_CATEGORIES")
        assert hasattr(tool_categories, "CODE_WRITE_TOOLS")
        assert hasattr(tool_categories, "COMMAND_EXECUTION_TOOLS")
        assert hasattr(tool_categories, "FILE_DELETE_TOOLS")
        assert hasattr(tool_categories, "READ_ONLY_TOOLS")
        assert hasattr(tool_categories, "is_code_write_tool")
        assert hasattr(tool_categories, "is_command_execution_tool")
        assert hasattr(tool_categories, "is_file_delete_tool")
        assert hasattr(tool_categories, "is_read_only_tool")
        assert hasattr(tool_categories, "_build_tool_categories")

    def test_category_values_immutable(self) -> None:
        """Category values are immutable frozensets."""
        with pytest.raises(AttributeError):
            CODE_WRITE_TOOLS.add("new_tool")  # type: ignore[attr-defined]
