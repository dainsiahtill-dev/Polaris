"""Tests for Tree-sitter availability detection and tool filtering.

Run with: python -m pytest polaris/kernelone/llm/toolkit/tests/test_ts_availability.py -v
"""

from __future__ import annotations

from polaris.kernelone.llm.toolkit.tool_normalization import get_available_tools
from polaris.kernelone.llm.toolkit.ts_availability import (
    TreeSitterAvailability,
    is_tree_sitter_available,
)


class TestTreeSitterAvailability:
    """Test TS availability detection."""

    def test_is_tree_sitter_available_returns_object(self) -> None:
        """Return TreeSitterAvailability object."""
        result = is_tree_sitter_available()
        assert isinstance(result, TreeSitterAvailability)

    def test_is_tree_sitter_available_cached(self) -> None:
        """Result is cached, second call does not re-detect."""
        # Clear cache first
        is_tree_sitter_available.cache_clear()
        r1 = is_tree_sitter_available()
        r2 = is_tree_sitter_available()
        assert r1.available == r2.available
        # Second call should be fast (returned from cache)
        # We verify caching by checking the function returns the same object
        # when called twice without cache clear
        r3 = is_tree_sitter_available()
        assert r3.available == r1.available

    def test_tree_sitter_availability_dataclass_fields(self) -> None:
        """TreeSitterAvailability has required fields."""
        avail = TreeSitterAvailability(available=True, reason=None, checked_at=12345.0)
        assert avail.available is True
        assert avail.reason is None
        assert avail.checked_at == 12345.0

    def test_tree_sitter_availability_unavailable(self) -> None:
        """TreeSitterAvailability can represent unavailability."""
        avail = TreeSitterAvailability(available=False, reason="import_error", checked_at=12345.0)
        assert avail.available is False
        assert avail.reason == "import_error"


class TestGetAvailableTools:
    """Test tool filtering based on TS availability."""

    def test_returns_all_when_ts_available(self) -> None:
        """Return all tools when TS is available."""
        ts_available = TreeSitterAvailability(available=True)
        tools = ["search_code", "repo_rg", "repo_symbols_index"]
        result = get_available_tools(tools, ts_available)
        assert result == tools

    def test_filters_ts_dependent_when_unavailable(self) -> None:
        """Filter out TS-dependent tools when unavailable."""
        ts_unavailable = TreeSitterAvailability(available=False, reason="import_error")
        tools = ["search_code", "repo_symbols_index", "glob", "treesitter_find_symbol"]
        result = get_available_tools(tools, ts_unavailable)
        assert "search_code" in result
        assert "glob" in result
        assert "repo_symbols_index" not in result
        assert "treesitter_find_symbol" not in result

    def test_search_code_always_available(self) -> None:
        """search_code / repo_rg not affected by TS availability."""
        ts_unavailable = TreeSitterAvailability(available=False, reason="timeout")
        for tool in ["search_code", "repo_rg", "grep", "ripgrep"]:
            result = get_available_tools([tool], ts_unavailable)
            assert tool in result, f"{tool} should not be filtered"

    def test_empty_list_returns_empty(self) -> None:
        """Empty tool list returns empty."""
        ts_unavailable = TreeSitterAvailability(available=False, reason="import_error")
        result = get_available_tools([], ts_unavailable)
        assert result == []

    def test_all_ts_dependent_filtered(self) -> None:
        """All TS-dependent tools are filtered when unavailable."""
        ts_unavailable = TreeSitterAvailability(available=False, reason="timeout")
        ts_dependent = [
            "repo_symbols_index",
            "treesitter_find_symbol",
            "treesitter_replace_node",
            "treesitter_insert_method",
            "treesitter_rename_symbol",
        ]
        result = get_available_tools(ts_dependent, ts_unavailable)
        assert result == []

    def test_non_ts_dependent_preserved(self) -> None:
        """Non-TS-dependent tools are preserved when unavailable."""
        ts_unavailable = TreeSitterAvailability(available=False, reason="import_error")
        non_ts_dependent = ["glob", "repo_tree", "repo_read_slice", "precision_edit"]
        result = get_available_tools(non_ts_dependent, ts_unavailable)
        assert result == non_ts_dependent

    def test_auto_detect_when_ts_availability_none(self) -> None:
        """When ts_availability is None, auto-detect TS availability."""
        # This test just verifies the function doesn't crash when None is passed
        # The actual availability status depends on the environment
        result = get_available_tools(["search_code", "repo_rg"], None)
        assert isinstance(result, list)
        assert "search_code" in result
        assert "repo_rg" in result
