"""Tests for P2-2: ToolRegistry.search_tools real implementation."""

from __future__ import annotations

from polaris.kernelone.single_agent.tools.registry import ToolRegistry


class TestToolRegistrySearchTools:
    """Tests for ToolRegistry.search_tools() embedding-based semantic search."""

    def test_empty_query_returns_empty(self) -> None:
        """Empty or whitespace-only query returns empty list."""
        registry = ToolRegistry()
        assert registry.search_tools("") == []
        assert registry.search_tools("   ") == []
        assert registry.search_tools("  \t  ") == []

    def test_returns_core_tools(self) -> None:
        """search_tools returns enabled core tools matching the query."""
        registry = ToolRegistry()
        results = registry.search_tools("read file", limit=5)
        # Should return some results
        assert isinstance(results, list)
        assert len(results) > 0
        # Results should be AgentToolSpec instances
        for tool in results:
            assert hasattr(tool, "tool_id")
            assert hasattr(tool, "name")

    def test_limit_applied(self) -> None:
        """limit parameter bounds the number of results."""
        registry = ToolRegistry()
        results = registry.search_tools("file", limit=3)
        assert len(results) <= 3

    def test_read_file_found_for_read_query(self) -> None:
        """read_file tool appears in results for 'read' query."""
        registry = ToolRegistry()
        results = registry.search_tools("read", limit=10)
        tool_names = [t.name for t in results]
        assert "read_file" in tool_names

    def test_execute_command_found_for_execute_query(self) -> None:
        """execute_command tool appears in results for 'execute' query."""
        registry = ToolRegistry()
        results = registry.search_tools("execute", limit=10)
        tool_names = [t.name for t in results]
        assert "execute_command" in tool_names

    def test_glob_found_for_glob_query(self) -> None:
        """glob tool appears in results for 'glob' query."""
        registry = ToolRegistry()
        results = registry.search_tools("glob", limit=10)
        tool_names = [t.name for t in results]
        assert "glob" in tool_names

    def test_tags_contribute_to_search(self) -> None:
        """Tool tags are included in searchable text."""
        registry = ToolRegistry()
        results = registry.search_tools("grep", limit=10)
        tool_names = [t.name for t in results]
        # search_code has "grep" tag
        assert "search_code" in tool_names

    def test_results_are_agent_tool_specs(self) -> None:
        """Results are AgentToolSpec instances with expected attributes."""
        registry = ToolRegistry()
        results = registry.search_tools("read", limit=3)
        assert len(results) > 0
        for tool in results:
            assert hasattr(tool, "tool_id")
            assert hasattr(tool, "name")
            assert hasattr(tool, "description")
            assert hasattr(tool, "tags")

    def test_disabled_tools_not_returned(self) -> None:
        """Disabled tools are excluded from search results."""
        registry = ToolRegistry()
        # Register a disabled tool
        from polaris.kernelone.single_agent.tools.contracts import AgentToolSpec

        registry.register_tool(
            AgentToolSpec(
                tool_id="test:disabled_tool",
                name="disabled_tool",
                source="test",
                description="A disabled tool",
                parameters={"type": "object"},
                enabled=False,
                tags=("test",),
            )
        )
        results = registry.search_tools("disabled", limit=10)
        tool_names = [t.name for t in results]
        assert "disabled_tool" not in tool_names
