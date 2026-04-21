"""Unit tests for ToolSpecRegistry - the single source of truth for tool definitions."""

from contextvars import Context

import pytest
from polaris.kernelone.tool_execution.tool_spec_registry import (
    ToolSpec,
    ToolSpecRegistry,
)


class TestToolSpec:
    """Tests for ToolSpec dataclass."""

    def test_creation(self) -> None:
        """Test basic ToolSpec creation."""
        spec = ToolSpec(
            canonical_name="test_tool",
            aliases=("alias1", "alias2"),
            description="A test tool",
            parameters={
                "type": "object",
                "properties": {
                    "arg1": {"type": "string", "description": "First argument"},
                },
                "required": ["arg1"],
            },
            categories=("read",),
        )

        assert spec.canonical_name == "test_tool"
        assert spec.aliases == ("alias1", "alias2")
        assert "read" in spec.categories
        assert not spec.is_write_tool()
        assert spec.is_read_tool()

    def test_to_openai_function(self) -> None:
        """Test OpenAI function format generation."""
        spec = ToolSpec(
            canonical_name="my_tool",
            aliases=("alias",),
            description="My tool description",
            parameters={
                "type": "object",
                "properties": {
                    "arg1": {"type": "string"},
                },
                "required": ["arg1"],
            },
            categories=("read",),
        )

        openai_func = spec.to_openai_function()

        assert openai_func["type"] == "function"
        assert openai_func["function"]["name"] == "my_tool"
        assert openai_func["function"]["description"] == "My tool description"
        assert openai_func["function"]["parameters"]["type"] == "object"

    def test_to_anthropic_tool(self) -> None:
        """Test Anthropic tool format generation."""
        spec = ToolSpec(
            canonical_name="my_tool",
            aliases=(),
            description="My tool description",
            parameters={
                "type": "object",
                "properties": {
                    "arg1": {"type": "string"},
                },
                "required": ["arg1"],
            },
            categories=("read",),
        )

        anthropic_tool = spec.to_anthropic_tool()

        assert anthropic_tool["name"] == "my_tool"
        assert anthropic_tool["description"] == "My tool description"
        assert anthropic_tool["input_schema"]["type"] == "object"

    def test_category_helpers(self) -> None:
        """Test is_read_tool, is_write_tool, is_exec_tool helpers."""
        read_spec = ToolSpec(
            canonical_name="read",
            aliases=(),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("read",),
        )
        write_spec = ToolSpec(
            canonical_name="write",
            aliases=(),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("write",),
        )
        exec_spec = ToolSpec(
            canonical_name="exec",
            aliases=(),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("exec",),
        )

        assert read_spec.is_read_tool()
        assert not read_spec.is_write_tool()
        assert not read_spec.is_exec_tool()

        assert write_spec.is_write_tool()
        assert not write_spec.is_read_tool()
        assert not write_spec.is_exec_tool()

        assert exec_spec.is_exec_tool()
        assert not exec_spec.is_read_tool()
        assert not exec_spec.is_write_tool()


class TestToolSpecRegistry:
    """Tests for ToolSpecRegistry class."""

    def setup_method(self) -> None:
        """Clear registry before each test."""
        ToolSpecRegistry.clear()

    def test_register_and_get(self) -> None:
        """Test basic register and get."""
        spec = ToolSpec(
            canonical_name="test_tool",
            aliases=("alias1", "alias2"),
            description="Test",
            parameters={"type": "object", "properties": {}},
            categories=("read",),
        )

        ToolSpecRegistry.register(spec)

        # Get by canonical name
        assert ToolSpecRegistry.get("test_tool") == spec

        # Get by alias
        assert ToolSpecRegistry.get("alias1") == spec
        assert ToolSpecRegistry.get("alias2") == spec

    def test_register_duplicate_raises(self) -> None:
        """Test that registering duplicate canonical name raises when strict=True."""
        spec1 = ToolSpec(
            canonical_name="test_tool",
            aliases=(),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("read",),
        )
        spec2 = ToolSpec(
            canonical_name="test_tool",
            aliases=(),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("read",),
        )

        ToolSpecRegistry.register(spec1)
        with pytest.raises(ValueError, match="Duplicate tool"):
            ToolSpecRegistry.register(spec2, strict=True)

    def test_register_duplicate_alias_raises(self) -> None:
        """Test that registering duplicate alias raises when strict=True."""
        spec1 = ToolSpec(
            canonical_name="tool1",
            aliases=("shared_alias",),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("read",),
        )
        spec2 = ToolSpec(
            canonical_name="tool2",
            aliases=("shared_alias",),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("read",),
        )

        ToolSpecRegistry.register(spec1)
        with pytest.raises(ValueError, match="Duplicate alias"):
            ToolSpecRegistry.register(spec2, strict=True)

    def test_get_canonical(self) -> None:
        """Test get_canonical returns correct name."""
        spec = ToolSpec(
            canonical_name="canonical_tool",
            aliases=("alias1", "alias2"),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("read",),
        )
        ToolSpecRegistry.register(spec)

        assert ToolSpecRegistry.get_canonical("canonical_tool") == "canonical_tool"
        assert ToolSpecRegistry.get_canonical("alias1") == "canonical_tool"
        assert ToolSpecRegistry.get_canonical("alias2") == "canonical_tool"
        assert ToolSpecRegistry.get_canonical("unknown") == "unknown"

    def test_is_canonical(self) -> None:
        """Test is_canonical correctly identifies canonical names."""
        spec = ToolSpec(
            canonical_name="my_tool",
            aliases=("alias1",),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("read",),
        )
        ToolSpecRegistry.register(spec)

        assert ToolSpecRegistry.is_canonical("my_tool")
        assert not ToolSpecRegistry.is_canonical("alias1")
        assert not ToolSpecRegistry.is_canonical("unknown")

    def test_get_all_canonical_names(self) -> None:
        """Test getting all canonical names."""
        spec1 = ToolSpec(
            canonical_name="tool1",
            aliases=(),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("read",),
        )
        spec2 = ToolSpec(
            canonical_name="tool2",
            aliases=(),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("read",),
        )
        ToolSpecRegistry.register(spec1)
        ToolSpecRegistry.register(spec2)

        names = ToolSpecRegistry.get_all_canonical_names()
        assert "tool1" in names
        assert "tool2" in names
        assert len(names) == 2

    def test_new_context_receives_builtin_registry(self) -> None:
        """A fresh ContextVar execution context should still expose built-in tools."""
        ToolSpecRegistry.clear()

        fresh_context = Context()
        names = fresh_context.run(ToolSpecRegistry.get_all_canonical_names)

        assert "repo_read_head" in names
        assert "repo_rg" in names

    def test_get_all_tools_deduplicates(self) -> None:
        """Test that get_all_tools returns unique specs (not aliases)."""
        spec = ToolSpec(
            canonical_name="unique_tool",
            aliases=("alias1", "alias2"),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("read",),
        )
        ToolSpecRegistry.register(spec)

        tools = ToolSpecRegistry.get_all_tools()
        assert len(tools) == 1
        assert tools[0].canonical_name == "unique_tool"

    def test_generate_llm_schemas_openai(self) -> None:
        """Test generating OpenAI format schemas."""
        spec = ToolSpec(
            canonical_name="my_tool",
            aliases=(),
            description="My tool",
            parameters={
                "type": "object",
                "properties": {
                    "arg1": {"type": "string", "description": "Arg1"},
                },
                "required": ["arg1"],
            },
            categories=("read",),
        )
        ToolSpecRegistry.register(spec)

        schemas = ToolSpecRegistry.generate_llm_schemas(format="openai")

        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "my_tool"
        assert schemas[0]["type"] == "function"

    def test_generate_llm_schemas_anthropic(self) -> None:
        """Test generating Anthropic format schemas."""
        spec = ToolSpec(
            canonical_name="my_tool",
            aliases=(),
            description="My tool",
            parameters={
                "type": "object",
                "properties": {
                    "arg1": {"type": "string"},
                },
                "required": ["arg1"],
            },
            categories=("read",),
        )
        ToolSpecRegistry.register(spec)

        schemas = ToolSpecRegistry.generate_llm_schemas(format="anthropic")

        assert len(schemas) == 1
        assert schemas[0]["name"] == "my_tool"
        assert "input_schema" in schemas[0]

    def test_generate_llm_schemas_with_category_filter(self) -> None:
        """Test filtering schemas by category."""
        read_spec = ToolSpec(
            canonical_name="read_tool",
            aliases=(),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("read",),
        )
        write_spec = ToolSpec(
            canonical_name="write_tool",
            aliases=(),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("write",),
        )
        ToolSpecRegistry.register(read_spec)
        ToolSpecRegistry.register(write_spec)

        read_schemas = ToolSpecRegistry.generate_llm_schemas(categories=("read",))
        assert len(read_schemas) == 1
        assert read_schemas[0]["function"]["name"] == "read_tool"

        write_schemas = ToolSpecRegistry.generate_llm_schemas(categories=("write",))
        assert len(write_schemas) == 1
        assert write_schemas[0]["function"]["name"] == "write_tool"

    def test_generate_handler_registry(self) -> None:
        """Test generating handler registry."""
        spec = ToolSpec(
            canonical_name="my_tool",
            aliases=("alias",),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("read",),
            handler_module="my_module",
            handler_function="my_handler",
        )
        ToolSpecRegistry.register(spec)

        registry = ToolSpecRegistry.generate_handler_registry()

        assert "my_tool" in registry
        assert registry["my_tool"] == ("my_module", "my_handler")

    def test_get_by_category(self) -> None:
        """Test getting tools by category."""
        read_spec = ToolSpec(
            canonical_name="read1",
            aliases=(),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("read",),
        )
        write_spec = ToolSpec(
            canonical_name="write1",
            aliases=(),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("write",),
        )
        multi_spec = ToolSpec(
            canonical_name="multi",
            aliases=(),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("read", "write"),
        )
        ToolSpecRegistry.register(read_spec)
        ToolSpecRegistry.register(write_spec)
        ToolSpecRegistry.register(multi_spec)

        read_tools = ToolSpecRegistry.get_by_category("read")
        assert len(read_tools) == 2
        assert {t.canonical_name for t in read_tools} == {"read1", "multi"}

        write_tools = ToolSpecRegistry.get_by_category("write")
        assert len(write_tools) == 2
        assert {t.canonical_name for t in write_tools} == {"write1", "multi"}

    def test_get_read_write_exec_tools(self) -> None:
        """Test convenience methods for tool categories."""
        spec1 = ToolSpec(
            canonical_name="read",
            aliases=(),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("read",),
        )
        spec2 = ToolSpec(
            canonical_name="write",
            aliases=(),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("write",),
        )
        spec3 = ToolSpec(
            canonical_name="exec",
            aliases=(),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("exec",),
        )
        ToolSpecRegistry.register(spec1)
        ToolSpecRegistry.register(spec2)
        ToolSpecRegistry.register(spec3)

        assert len(ToolSpecRegistry.get_read_tools()) == 1
        assert len(ToolSpecRegistry.get_write_tools()) == 1
        assert len(ToolSpecRegistry.get_exec_tools()) == 1

    def test_count(self) -> None:
        """Test count method."""
        assert ToolSpecRegistry.count() == 0

        spec1 = ToolSpec(
            canonical_name="tool1",
            aliases=(),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("read",),
        )
        spec2 = ToolSpec(
            canonical_name="tool2",
            aliases=(),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("read",),
        )
        ToolSpecRegistry.register(spec1)
        assert ToolSpecRegistry.count() == 1

        ToolSpecRegistry.register(spec2)
        assert ToolSpecRegistry.count() == 2

    def test_clear(self) -> None:
        """Test clear method."""
        spec = ToolSpec(
            canonical_name="test",
            aliases=(),
            description="",
            parameters={"type": "object", "properties": {}},
            categories=("read",),
        )
        ToolSpecRegistry.register(spec)
        assert ToolSpecRegistry.count() == 1

        ToolSpecRegistry.clear()
        assert ToolSpecRegistry.count() == 0


class TestMigrationFromContracts:
    """Tests for migration from contracts.py _TOOL_SPECS."""

    def setup_method(self) -> None:
        """Clear and re-migrate registry before each test."""
        ToolSpecRegistry.clear()

        # 触发迁移
        from polaris.kernelone.tool_execution import tool_spec_registry

        tool_spec_registry.migrate_from_contracts_specs()

    def test_migration_registers_all_tools(self) -> None:
        """Test that migration registers all tools from _TOOL_SPECS."""
        # 从contracts.py我们知道有repo_tree, repo_rg, repo_read_head等工具
        assert ToolSpecRegistry.count() > 0

        # 验证几个关键工具
        assert ToolSpecRegistry.get("repo_tree") is not None
        assert ToolSpecRegistry.get("repo_rg") is not None
        assert ToolSpecRegistry.get("repo_read_head") is not None
        assert ToolSpecRegistry.get("repo_read_tail") is not None

    def test_migration_preserves_aliases(self) -> None:
        """Test that migration preserves aliases."""
        spec = ToolSpecRegistry.get("repo_rg")
        assert spec is not None
        # grep is now an alias for repo_rg (fix 2026-04-05)
        assert "grep" in spec.aliases
        assert "search" in spec.aliases
        assert "ripgrep" in spec.aliases

    def test_migration_alias_lookup_works(self) -> None:
        """Test that alias lookup works for migrated tools.

        After tool consolidation (2026-03-29) and grep alias fix (2026-04-05):
        - ripgrep and search_code are deprecated aliases for repo_rg
        - repo_rg is the canonical tool with aliases (rg, search, grep, etc.)
        - grep is an alias for repo_rg (same normalizer and handler)
        - All search tools now route correctly via their handlers
        """
        spec_by_alias = ToolSpecRegistry.get("grep")
        assert spec_by_alias is not None
        # grep is now an alias for repo_rg
        assert spec_by_alias.canonical_name == "repo_rg"

        # get_canonical should also work
        canonical = ToolSpecRegistry.get_canonical("grep")
        assert canonical == "repo_rg"

        # rg -> repo_rg (ripgrep is deprecated but still routes to repo_rg)
        assert ToolSpecRegistry.get_canonical("rg") == "repo_rg"

    def test_migration_generates_valid_schemas(self) -> None:
        """Test that migrated specs generate valid LLM schemas."""
        schemas = ToolSpecRegistry.generate_llm_schemas(format="openai")
        assert len(schemas) > 0

        # 检查schema结构
        for schema in schemas:
            assert schema["type"] == "function"
            assert "function" in schema
            assert "name" in schema["function"]
            assert "description" in schema["function"]
            assert "parameters" in schema["function"]

    def test_migration_categories_preserved(self) -> None:
        """Test that tool categories are preserved."""
        read_spec = ToolSpecRegistry.get("repo_read_head")
        assert read_spec is not None
        assert "read" in read_spec.categories

        write_spec = ToolSpecRegistry.get("precision_edit")
        assert write_spec is not None
        assert "write" in write_spec.categories

    def test_migration_response_format_hint_preserved(self) -> None:
        """Test that response_format_hint is preserved."""
        spec = ToolSpecRegistry.get("repo_tree")
        assert spec is not None
        assert spec.response_format_hint != ""
        assert "Tree" in spec.response_format_hint or "tree" in spec.response_format_hint.lower()
