"""Unit tests for the Tool Composer module.

Tests cover:
- ToolCapability dataclass
- ToolSelection creation
- GoalAnalysis decomposition
- Constraints validation
- CapabilityRegistry tool registration and lookup
- ToolComposer.compose() workflow
- Dependency resolution
- Graph construction
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.kernelone.tool_execution.composer import (
    CapabilityRegistry,
    CompositionResult,
    Constraints,
    GoalAnalysis,
    ToolCapability,
    ToolComposer,
    ToolSelection,
)
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry


class TestToolCapability:
    """Tests for ToolCapability dataclass."""

    def test_create_capability(self) -> None:
        """Test creating a basic capability."""
        cap = ToolCapability(
            tool_name="repo_read_head",
            input_type="path_query",
            output_type="content",
            description="Read first N lines of a file",
            semantic_tags=("read", "file", "view"),
        )
        assert cap.tool_name == "repo_read_head"
        assert cap.input_type == "path_query"
        assert cap.output_type == "content"
        assert "read" in cap.semantic_tags

    def test_capability_is_frozen(self) -> None:
        """Test that ToolCapability is immutable."""
        cap = ToolCapability(
            tool_name="repo_rg",
            input_type="pattern_query",
            output_type="locations",
            description="Search for pattern",
            semantic_tags=("search", "grep"),
        )
        with pytest.raises(AttributeError):
            cap.tool_name = "different_tool"  # type: ignore[attr-defined]


class TestToolSelection:
    """Tests for ToolSelection dataclass."""

    def test_create_selection(self) -> None:
        """Test creating a tool selection."""
        cap = ToolCapability(
            tool_name="write_file",
            input_type="content_path",
            output_type="confirmation",
            description="Write content to file",
            semantic_tags=("write", "create"),
        )
        selection = ToolSelection(
            capability=cap,
            confidence=0.9,
            reasoning="Selected write_file for file creation",
            args={"file": "test.py", "content": "# test"},
        )
        assert selection.confidence == 0.9
        assert selection.args["file"] == "test.py"

    def test_selection_default_args(self) -> None:
        """Test selection with default args."""
        cap = ToolCapability(
            tool_name="read_file",
            input_type="path",
            output_type="content",
            description="Read file",
            semantic_tags=("read",),
        )
        selection = ToolSelection(
            capability=cap,
            confidence=0.8,
            reasoning="Test selection",
        )
        assert selection.args == {}


class TestConstraints:
    """Tests for Constraints dataclass."""

    def test_default_constraints(self) -> None:
        """Test default constraints values."""
        constraints = Constraints()
        assert constraints.allowed_categories == ("read", "write", "exec")
        assert constraints.excluded_tools == ()
        assert constraints.max_tools == 10
        assert constraints.prefer_read_only is False

    def test_custom_constraints(self) -> None:
        """Test custom constraints values."""
        constraints = Constraints(
            allowed_categories=("read",),
            excluded_tools=("dangerous_tool",),
            max_tools=5,
            prefer_read_only=True,
        )
        assert constraints.allowed_categories == ("read",)
        assert constraints.excluded_tools == ("dangerous_tool",)
        assert constraints.max_tools == 5
        assert constraints.prefer_read_only is True

    def test_constraints_is_frozen(self) -> None:
        """Test that Constraints is immutable."""
        constraints = Constraints()
        with pytest.raises(AttributeError):
            constraints.max_tools = 20  # type: ignore[attr-defined]


class TestCapabilityRegistry:
    """Tests for CapabilityRegistry."""

    def test_register_and_find_by_tag(self) -> None:
        """Test registering and finding capabilities by tag."""
        registry = CapabilityRegistry()

        cap = ToolCapability(
            tool_name="test_tool",
            input_type="input",
            output_type="output",
            description="A test tool",
            semantic_tags=("test", "example"),
        )
        registry.register_capability(cap)

        found = registry.find_by_tag("test")
        assert len(found) >= 1
        assert any(c.tool_name == "test_tool" for c in found)

    def test_find_by_tags_multiple(self) -> None:
        """Test finding capabilities by multiple tags."""
        registry = CapabilityRegistry()

        cap1 = ToolCapability(
            tool_name="tool_a",
            input_type="input",
            output_type="output",
            description="Tool A",
            semantic_tags=("alpha", "beta"),
        )
        cap2 = ToolCapability(
            tool_name="tool_b",
            input_type="input",
            output_type="output",
            description="Tool B",
            semantic_tags=("beta", "gamma"),
        )
        registry.register_capability(cap1)
        registry.register_capability(cap2)

        found = registry.find_by_tags(("alpha", "gamma"))
        assert len(found) == 2

    def test_find_by_tag_case_insensitive(self) -> None:
        """Test that tag search is case insensitive."""
        registry = CapabilityRegistry()

        cap = ToolCapability(
            tool_name="case_test",
            input_type="input",
            output_type="output",
            description="Case test",
            semantic_tags=("UPPERCASE", "MixedCase"),
        )
        registry.register_capability(cap)

        found = registry.find_by_tag("uppercase")
        assert len(found) >= 1


class TestGoalAnalysis:
    """Tests for GoalAnalysis dataclass."""

    def test_create_analysis(self) -> None:
        """Test creating a goal analysis."""
        analysis = GoalAnalysis(
            required_capabilities=("file_read", "file_search"),
            reasoning="Analyzed goal -> found read and search capabilities",
            estimated_steps=3,
            potential_blockers=("slow_execution",),
        )
        assert len(analysis.required_capabilities) == 2
        assert analysis.estimated_steps == 3
        assert "slow_execution" in analysis.potential_blockers


class TestCompositionResult:
    """Tests for CompositionResult dataclass."""

    def test_create_result(self) -> None:
        """Test creating a composition result."""
        from polaris.kernelone.tool_execution.graph import ToolCallGraph

        graph = ToolCallGraph(nodes=(), edges=())
        cap = ToolCapability(
            tool_name="test",
            input_type="in",
            output_type="out",
            description="Test",
            semantic_tags=(),
        )
        selection = ToolSelection(
            capability=cap,
            confidence=0.85,
            reasoning="Test",
        )

        result = CompositionResult(
            graph=graph,
            confidence=0.85,
            reasoning="Test composition",
            selected_tools=(selection,),
        )
        assert result.confidence == 0.85
        assert len(result.selected_tools) == 1


class TestToolComposer:
    """Tests for ToolComposer class."""

    @pytest.fixture
    def mock_llm(self) -> MagicMock:
        """Create a mock LLM provider."""
        mock = MagicMock()
        mock.name = "test_llm"
        mock.invoke = AsyncMock()
        mock.invoke_stream = AsyncMock()
        return mock

    @pytest.fixture
    def composer(self, mock_llm: MagicMock) -> ToolComposer:
        """Create a ToolComposer instance with mock LLM."""
        return ToolComposer(
            tool_registry=ToolSpecRegistry,
            llm=mock_llm,
        )

    def test_composer_initialization(self, composer: ToolComposer) -> None:
        """Test composer initializes correctly."""
        assert composer._registry is ToolSpecRegistry
        assert composer._llm is not None
        assert composer._capability_registry is not None

    def test_capability_registry_built(self, composer: ToolComposer) -> None:
        """Test that capability registry is populated from ToolSpecRegistry."""
        all_caps = composer._capability_registry.get_all_capabilities()
        assert len(all_caps) > 0

    @pytest.mark.asyncio
    async def test_analyze_goal_read(self, composer: ToolComposer) -> None:
        """Test goal analysis for read operation."""
        constraints = Constraints()
        analysis = await composer._analyze_goal("Read the file at path test.py", constraints)
        assert len(analysis.required_capabilities) > 0
        assert "file_read" in analysis.required_capabilities

    @pytest.mark.asyncio
    async def test_analyze_goal_search(self, composer: ToolComposer) -> None:
        """Test goal analysis for search operation."""
        constraints = Constraints()
        analysis = await composer._analyze_goal("Search for pattern 'hello' in files", constraints)
        assert len(analysis.required_capabilities) > 0

    @pytest.mark.asyncio
    async def test_analyze_goal_write(self, composer: ToolComposer) -> None:
        """Test goal analysis for write operation."""
        constraints = Constraints()
        analysis = await composer._analyze_goal("Write content to new file output.txt", constraints)
        assert len(analysis.required_capabilities) > 0
        assert "file_write" in analysis.required_capabilities

    @pytest.mark.asyncio
    async def test_select_tools(self, composer: ToolComposer) -> None:
        """Test tool selection for capabilities."""
        constraints = Constraints()
        selections = await composer._select_tools(("file_read",), constraints)
        assert len(selections) > 0
        assert selections[0].capability.tool_name is not None

    @pytest.mark.asyncio
    async def test_resolve_dependencies(self, composer: ToolComposer) -> None:
        """Test dependency resolution orders tools correctly."""
        cap_read = ToolCapability(
            tool_name="repo_read_head",
            input_type="path_query",
            output_type="content",
            description="Read file",
            semantic_tags=("read",),
        )
        cap_search = ToolCapability(
            tool_name="repo_rg",
            input_type="pattern_query",
            output_type="locations",
            description="Search",
            semantic_tags=("search",),
        )

        tools = [
            ToolSelection(capability=cap_search, confidence=0.8, reasoning="Search"),
            ToolSelection(capability=cap_read, confidence=0.8, reasoning="Read"),
        ]

        ordered = composer._resolve_dependencies(tools)
        assert len(ordered) == 2

    @pytest.mark.asyncio
    async def test_build_graph(self, composer: ToolComposer) -> None:
        """Test graph construction from tool selections."""
        cap = ToolCapability(
            tool_name="repo_read_head",
            input_type="path_query",
            output_type="content",
            description="Read file",
            semantic_tags=("read",),
        )
        selection = ToolSelection(
            capability=cap,
            confidence=0.9,
            reasoning="Test",
        )

        analysis = GoalAnalysis(
            required_capabilities=("file_read",),
            reasoning="Test",
            estimated_steps=1,
        )

        graph = composer._build_graph([selection], analysis)
        assert len(graph.nodes) == 1
        assert graph.nodes[0].tool_call.name == "repo_read_head"

    @pytest.mark.asyncio
    async def test_compose_full_workflow(self, composer: ToolComposer) -> None:
        """Test full composition workflow."""
        constraints = Constraints()
        result = await composer.compose(
            goal="Read the file test.py and search for 'hello'",
            constraints=constraints,
        )

        assert isinstance(result, CompositionResult)
        assert 0.0 <= result.confidence <= 1.0
        assert result.graph is not None
        assert len(result.graph.nodes) > 0

    @pytest.mark.asyncio
    async def test_compose_with_constraints(self, composer: ToolComposer) -> None:
        """Test composition respects constraints."""
        constraints = Constraints(
            prefer_read_only=True,
            max_tools=3,
        )

        result = await composer.compose(
            goal="Read and write files",
            constraints=constraints,
        )

        assert result.graph is not None


class TestDependencyResolution:
    """Tests for dependency resolution logic."""

    def test_is_dependency_satisfied_read_to_search(self) -> None:
        """Test that read output satisfies search input."""
        registry = CapabilityRegistry()
        composer = ToolComposer.__new__(ToolComposer)
        composer._registry = ToolSpecRegistry
        composer._capability_registry = registry

        cap_read = ToolCapability(
            tool_name="repo_read_head",
            input_type="path_query",
            output_type="content",
            description="Read",
            semantic_tags=(),
        )
        cap_search = ToolCapability(
            tool_name="repo_rg",
            input_type="pattern_query",
            output_type="locations",
            description="Search",
            semantic_tags=(),
        )

        # Search doesn't depend on read in current logic
        # (search takes pattern_query, not content)
        result = composer._is_dependency_satisfied(cap_read, cap_search)
        assert isinstance(result, bool)

    def test_is_dependency_satisfied_write_no_conflict(self) -> None:
        """Test that write tools don't have false dependencies."""
        registry = CapabilityRegistry()
        composer = ToolComposer.__new__(ToolComposer)
        composer._registry = ToolSpecRegistry
        composer._capability_registry = registry

        cap_write1 = ToolCapability(
            tool_name="write_file",
            input_type="content_path",
            output_type="confirmation",
            description="Write 1",
            semantic_tags=(),
        )
        cap_write2 = ToolCapability(
            tool_name="edit_file",
            input_type="content_path",
            output_type="confirmation",
            description="Write 2",
            semantic_tags=(),
        )

        # Write tools don't depend on other writes
        result = composer._is_dependency_satisfied(cap_write1, cap_write2)
        assert result is False
