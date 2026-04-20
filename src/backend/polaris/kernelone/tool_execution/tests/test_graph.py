"""Unit tests for the tool call graph module.

Tests cover:
- ToolCallNode and ToolCallEdge creation
- ToolCallGraph structure
- ConditionEvaluator and DefaultConditionEvaluator
- GraphExecutor execution with parallel nodes
- Edge condition evaluation
- Retry policies
- ToolCallGraphBuilder DSL
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.kernelone.llm.contracts.tool import ToolCall, ToolExecutionResult
from polaris.kernelone.tool_execution.graph import (
    DEFAULT_TIMEOUT_SECONDS,
    ConditionEvaluator,
    DefaultConditionEvaluator,
    ExecutionContext,
    GraphExecutionResult,
    GraphExecutor,
    NodeResult,
    ToolCallEdge,
    ToolCallGraph,
    ToolCallGraphBuilder,
    ToolCallNode,
)
from polaris.kernelone.workflow.contracts import RetryPolicy


class TestConditionEvaluator:
    """Tests for ConditionEvaluator base class."""

    def test_evaluate_empty_condition_returns_true(self) -> None:
        """Empty condition string should return True."""
        evaluator = ConditionEvaluator()
        assert evaluator.evaluate("", {"any_key": True}) is True

    def test_evaluate_truthy_context_value(self) -> None:
        """Condition key with truthy value should return True."""
        evaluator = ConditionEvaluator()
        context = {"feature_enabled": True, "count": 42}
        assert evaluator.evaluate("feature_enabled", context) is True

    def test_evaluate_falsy_context_value(self) -> None:
        """Condition key with falsy value should return False."""
        evaluator = ConditionEvaluator()
        context = {"feature_enabled": False, "count": 0}
        assert evaluator.evaluate("feature_enabled", context) is False

    def test_evaluate_missing_key(self) -> None:
        """Missing context key should return False."""
        evaluator = ConditionEvaluator()
        context: dict[str, Any] = {}
        assert evaluator.evaluate("missing_key", context) is False


class TestDefaultConditionEvaluator:
    """Tests for DefaultConditionEvaluator with extended expression support."""

    def test_evaluate_empty_condition_returns_true(self) -> None:
        """Empty condition should return True."""
        evaluator = DefaultConditionEvaluator()
        assert evaluator.evaluate("", {}) is True

    def test_evaluate_truthy_key(self) -> None:
        """Truthy key should return True."""
        evaluator = DefaultConditionEvaluator()
        assert evaluator.evaluate("enabled", {"enabled": True}) is True

    def test_evaluate_falsy_key(self) -> None:
        """Falsy key should return False."""
        evaluator = DefaultConditionEvaluator()
        assert evaluator.evaluate("enabled", {"enabled": False}) is False

    def test_evaluate_negation_true(self) -> None:
        """Negation of falsy key should return True."""
        evaluator = DefaultConditionEvaluator()
        assert evaluator.evaluate("!disabled", {"disabled": False}) is True

    def test_evaluate_negation_false(self) -> None:
        """Negation of truthy key should return False."""
        evaluator = DefaultConditionEvaluator()
        assert evaluator.evaluate("!enabled", {"enabled": True}) is False

    def test_evaluate_equality_true(self) -> None:
        """Equality comparison with matching values should return True."""
        evaluator = DefaultConditionEvaluator()
        assert evaluator.evaluate("status==active", {"status": "active"}) is True

    def test_evaluate_equality_false(self) -> None:
        """Equality comparison with non-matching values should return False."""
        evaluator = DefaultConditionEvaluator()
        assert evaluator.evaluate("status==active", {"status": "inactive"}) is False

    def test_evaluate_not_equality_true(self) -> None:
        """Not-equal comparison with non-matching values should return True."""
        evaluator = DefaultConditionEvaluator()
        assert evaluator.evaluate("status!=inactive", {"status": "active"}) is True

    def test_evaluate_not_equality_false(self) -> None:
        """Not-equal comparison with matching values should return False."""
        evaluator = DefaultConditionEvaluator()
        assert evaluator.evaluate("status!=active", {"status": "active"}) is False


class TestToolCallNode:
    """Tests for ToolCallNode dataclass."""

    def test_create_node_with_required_fields(self) -> None:
        """Creating node with only required fields should succeed."""
        tool_call = ToolCall(id="call_1", name="read_file", arguments={"path": "/tmp/test.txt"})
        node = ToolCallNode(id="node_1", tool_call=tool_call)
        assert node.id == "node_1"
        assert node.tool_call == tool_call
        assert node.condition is None
        assert node.retry_policy is None
        assert node.timeout_seconds == DEFAULT_TIMEOUT_SECONDS

    def test_create_node_with_all_fields(self) -> None:
        """Creating node with all optional fields should succeed."""
        tool_call = ToolCall(id="call_1", name="read_file", arguments={})
        retry_policy = RetryPolicy(max_attempts=3)
        node = ToolCallNode(
            id="node_1",
            tool_call=tool_call,
            condition="feature_enabled",
            retry_policy=retry_policy,
            timeout_seconds=60,
        )
        assert node.id == "node_1"
        assert node.condition == "feature_enabled"
        assert node.retry_policy == retry_policy
        assert node.timeout_seconds == 60


class TestToolCallEdge:
    """Tests for ToolCallEdge dataclass."""

    def test_create_edge_with_required_fields(self) -> None:
        """Creating edge with only required fields should succeed."""
        edge = ToolCallEdge(from_id="node_1", to_id="node_2")
        assert edge.from_id == "node_1"
        assert edge.to_id == "node_2"
        assert edge.condition is None

    def test_create_edge_with_condition(self) -> None:
        """Creating edge with condition should succeed."""
        edge = ToolCallEdge(from_id="node_1", to_id="node_2", condition="result_available")
        assert edge.condition == "result_available"


class TestToolCallGraph:
    """Tests for ToolCallGraph dataclass."""

    def test_create_empty_graph(self) -> None:
        """Creating empty graph should succeed."""
        graph = ToolCallGraph(nodes=(), edges=())
        assert graph.nodes == ()
        assert graph.edges == ()
        assert graph.entry_points == ()

    def test_create_graph_with_nodes_and_edges(self) -> None:
        """Creating graph with nodes and edges should succeed."""
        tool_call = ToolCall(id="call_1", name="read_file", arguments={})
        node1 = ToolCallNode(id="node_1", tool_call=tool_call)
        node2 = ToolCallNode(id="node_2", tool_call=tool_call)
        edge = ToolCallEdge(from_id="node_1", to_id="node_2")
        graph = ToolCallGraph(
            nodes=(node1, node2),
            edges=(edge,),
            entry_points=("node_1",),
        )
        assert len(graph.nodes) == 2
        assert len(graph.edges) == 1
        assert graph.entry_points == ("node_1",)


class TestGraphExecutor:
    """Tests for GraphExecutor execution logic."""

    @pytest.fixture
    def mock_executor(self) -> MagicMock:
        """Create a mock tool executor."""
        executor = MagicMock()
        executor.execute_call.return_value = ToolExecutionResult(
            tool_call_id="call_1",
            name="test_tool",
            success=True,
            result={"output": "test result"},
            duration_ms=10,
        )
        return executor

    @pytest.fixture
    def simple_tool_call(self) -> ToolCall:
        """Create a simple tool call for testing."""
        return ToolCall(id="call_1", name="test_tool", arguments={"input": "value"})

    @pytest.mark.asyncio
    async def test_execute_single_node_success(self, mock_executor: MagicMock, simple_tool_call: ToolCall) -> None:
        """Single node should execute successfully."""
        node = ToolCallNode(id="node_1", tool_call=simple_tool_call)
        graph = ToolCallGraph(nodes=(node,), edges=(), entry_points=("node_1",))
        executor = GraphExecutor(executor=mock_executor)
        result = await executor.execute(graph)
        assert result.ok is True
        assert result.completed_nodes == 1
        assert result.failed_nodes == 0
        assert result.skipped_nodes == 0

    @pytest.mark.asyncio
    async def test_execute_node_with_condition_skipped(
        self, mock_executor: MagicMock, simple_tool_call: ToolCall
    ) -> None:
        """Node with falsy condition should be skipped."""
        node = ToolCallNode(id="node_1", tool_call=simple_tool_call, condition="!enabled")
        graph = ToolCallGraph(nodes=(node,), edges=(), entry_points=("node_1",))
        executor = GraphExecutor(executor=mock_executor)
        context = ExecutionContext(workspace=".", metadata={"enabled": True})
        result = await executor.execute(graph, initial_context=context)
        assert result.ok is True
        assert result.completed_nodes == 0
        assert result.skipped_nodes == 1

    @pytest.mark.asyncio
    async def test_execute_node_with_condition_not_skipped(
        self, mock_executor: MagicMock, simple_tool_call: ToolCall
    ) -> None:
        """Node with truthy condition should execute."""
        node = ToolCallNode(id="node_1", tool_call=simple_tool_call, condition="enabled")
        graph = ToolCallGraph(nodes=(node,), edges=(), entry_points=("node_1",))
        executor = GraphExecutor(executor=mock_executor)
        context = ExecutionContext(workspace=".", metadata={"enabled": True})
        result = await executor.execute(graph, initial_context=context)
        assert result.ok is True
        assert result.completed_nodes == 1
        assert result.skipped_nodes == 0

    @pytest.mark.asyncio
    async def test_execute_sequential_nodes(self, mock_executor: MagicMock, simple_tool_call: ToolCall) -> None:
        """Sequential nodes should execute in order."""
        node1 = ToolCallNode(id="node_1", tool_call=simple_tool_call)
        node2 = ToolCallNode(id="node_2", tool_call=simple_tool_call)
        edge = ToolCallEdge(from_id="node_1", to_id="node_2")
        graph = ToolCallGraph(
            nodes=(node1, node2),
            edges=(edge,),
            entry_points=("node_1",),
        )
        executor = GraphExecutor(executor=mock_executor)
        result = await executor.execute(graph)
        assert result.ok is True
        assert result.completed_nodes == 2

    @pytest.mark.asyncio
    async def test_execute_parallel_nodes(self, mock_executor: MagicMock, simple_tool_call: ToolCall) -> None:
        """Parallel nodes (entry points) should execute concurrently."""
        node1 = ToolCallNode(id="node_1", tool_call=simple_tool_call)
        node2 = ToolCallNode(id="node_2", tool_call=simple_tool_call)
        graph = ToolCallGraph(nodes=(node1, node2), edges=(), entry_points=("node_1", "node_2"))
        executor = GraphExecutor(executor=mock_executor)
        result = await executor.execute(graph)
        assert result.ok is True
        assert result.completed_nodes == 2
        # Both should execute in the first batch
        assert mock_executor.execute_call.call_count == 2

    @pytest.mark.asyncio
    async def test_execute_node_failure(self, mock_executor: MagicMock, simple_tool_call: ToolCall) -> None:
        """Failed node should record the failure."""
        mock_executor.execute_call.return_value = ToolExecutionResult(
            tool_call_id="call_1",
            name="test_tool",
            success=False,
            error="Test error",
            duration_ms=10,
        )
        node = ToolCallNode(id="node_1", tool_call=simple_tool_call)
        graph = ToolCallGraph(nodes=(node,), edges=(), entry_points=("node_1",))
        executor = GraphExecutor(executor=mock_executor)
        result = await executor.execute(graph)
        assert result.ok is False
        assert result.failed_nodes == 1
        assert "Test error" in result.node_results["node_1"].error

    @pytest.mark.asyncio
    async def test_execute_retry_on_failure(self, mock_executor: MagicMock, simple_tool_call: ToolCall) -> None:
        """Failed node should retry according to retry policy."""
        # Fail first, then succeed
        mock_executor.execute_call.side_effect = [
            ToolExecutionResult(
                tool_call_id="call_1", name="test_tool", success=False, error="First fail", duration_ms=5
            ),
            ToolExecutionResult(
                tool_call_id="call_1", name="test_tool", success=True, result={"ok": True}, duration_ms=5
            ),
        ]
        retry_policy = RetryPolicy(max_attempts=2, initial_interval_seconds=0.01)
        node = ToolCallNode(id="node_1", tool_call=simple_tool_call, retry_policy=retry_policy)
        graph = ToolCallGraph(nodes=(node,), edges=(), entry_points=("node_1",))
        executor = GraphExecutor(executor=mock_executor)
        result = await executor.execute(graph)
        assert result.ok is True
        assert mock_executor.execute_call.call_count == 2


class TestToolCallGraphBuilder:
    """Tests for ToolCallGraphBuilder DSL."""

    def test_build_simple_linear_graph(self) -> None:
        """Builder should create simple linear graph correctly."""
        tool_call = ToolCall(id="call_1", name="read_file", arguments={})
        graph = (
            ToolCallGraphBuilder()
            .node("read", tool_call=tool_call)
            .node("search", tool_call=tool_call, depends_on=["read"])
            .node("edit", tool_call=tool_call, depends_on=["search"])
            .build()
        )
        assert len(graph.nodes) == 3
        assert len(graph.edges) == 2

    def test_build_parallel_graph(self) -> None:
        """Builder should create graph with parallel branches."""
        tool_call = ToolCall(id="call_1", name="read_file", arguments={})
        graph = (
            ToolCallGraphBuilder()
            .node("start", tool_call=tool_call)
            .node("branch_a", tool_call=tool_call, depends_on=["start"])
            .node("branch_b", tool_call=tool_call, depends_on=["start"])
            .node("merge", tool_call=tool_call, depends_on=["branch_a", "branch_b"])
            .build()
        )
        assert len(graph.nodes) == 4
        assert len(graph.edges) == 4

    def test_duplicate_node_id_raises(self) -> None:
        """Adding duplicate node ID should raise ValueError."""
        tool_call = ToolCall(id="call_1", name="read_file", arguments={})
        builder = ToolCallGraphBuilder().node("node_1", tool_call=tool_call)
        with pytest.raises(ValueError, match="already exists"):
            builder.node("node_1", tool_call=tool_call)

    def test_dependency_before_node_raises(self) -> None:
        """Using dependency before adding node should raise ValueError."""
        tool_call = ToolCall(id="call_1", name="read_file", arguments={})
        with pytest.raises(ValueError, match="referenced before being added"):
            ToolCallGraphBuilder().node("later", tool_call=tool_call, depends_on=["earlier"])

    def test_edge_self_reference_raises(self) -> None:
        """Self-referencing edge should raise ValueError."""
        tool_call = ToolCall(id="call_1", name="read_file", arguments={})
        with pytest.raises(ValueError, match="Self-referencing edge"):
            (ToolCallGraphBuilder().node("node_1", tool_call=tool_call).edge("node_1", "node_1").build())

    def test_explicit_edge_with_condition(self) -> None:
        """Builder should support explicit edges with conditions."""
        tool_call = ToolCall(id="call_1", name="read_file", arguments={})
        graph = (
            ToolCallGraphBuilder()
            .node("read", tool_call=tool_call)
            .node("search", tool_call=tool_call)
            .edge("read", "search", condition="has_results")
            .build()
        )
        # Find the edge between read and search
        matching_edges = [e for e in graph.edges if e.from_id == "read" and e.to_id == "search"]
        assert len(matching_edges) == 1
        assert matching_edges[0].condition == "has_results"

    def test_node_with_retry_policy(self) -> None:
        """Builder should support nodes with retry policies."""
        tool_call = ToolCall(id="call_1", name="read_file", arguments={})
        retry_policy = RetryPolicy(max_attempts=3)
        graph = ToolCallGraphBuilder().node("read", tool_call=tool_call, retry_policy=retry_policy).build()
        assert graph.nodes[0].retry_policy == retry_policy

    def test_node_with_timeout(self) -> None:
        """Builder should support custom timeout values."""
        tool_call = ToolCall(id="call_1", name="read_file", arguments={})
        graph = ToolCallGraphBuilder().node("read", tool_call=tool_call, timeout_seconds=120).build()
        assert graph.nodes[0].timeout_seconds == 120

    def test_node_with_condition(self) -> None:
        """Builder should support conditional nodes."""
        tool_call = ToolCall(id="call_1", name="read_file", arguments={})
        graph = ToolCallGraphBuilder().node("read", tool_call=tool_call, condition="file_exists").build()
        assert graph.nodes[0].condition == "file_exists"


class TestExecutionContext:
    """Tests for ExecutionContext dataclass."""

    def test_create_context_with_defaults(self) -> None:
        """Creating context with defaults should succeed."""
        context = ExecutionContext(workspace="/tmp")
        assert context.workspace == "/tmp"
        assert context.node_results == {}
        assert context.metadata == {}

    def test_create_context_with_all_fields(self) -> None:
        """Creating context with all fields should succeed."""
        node_result = NodeResult(node_id="node_1", ok=True, result={"data": "value"})
        context = ExecutionContext(
            workspace="/tmp",
            node_results={"node_1": node_result},
            metadata={"key": "value"},
        )
        assert context.workspace == "/tmp"
        assert "node_1" in context.node_results
        assert context.metadata == {"key": "value"}

    def test_get_node_result(self) -> None:
        """get_node_result should return correct result or None."""
        node_result = NodeResult(node_id="node_1", ok=True)
        context = ExecutionContext(
            workspace="/tmp",
            node_results={"node_1": node_result},
        )
        assert context.get_node_result("node_1") == node_result
        assert context.get_node_result("nonexistent") is None

    def test_is_node_completed(self) -> None:
        """is_node_completed should return correct status."""
        node_result_ok = NodeResult(node_id="node_1", ok=True)
        node_result_failed = NodeResult(node_id="node_2", ok=False)
        context = ExecutionContext(
            workspace="/tmp",
            node_results={
                "node_1": node_result_ok,
                "node_2": node_result_failed,
            },
        )
        assert context.is_node_completed("node_1") is True
        assert context.is_node_completed("node_2") is False
        assert context.is_node_completed("nonexistent") is False


class TestNodeResult:
    """Tests for NodeResult dataclass."""

    def test_create_successful_result(self) -> None:
        """Creating successful result should succeed."""
        result = NodeResult(node_id="node_1", ok=True, result={"data": "value"})
        assert result.node_id == "node_1"
        assert result.ok is True
        assert result.result == {"data": "value"}
        assert result.error is None
        assert result.skipped is False

    def test_create_failed_result(self) -> None:
        """Creating failed result should succeed."""
        result = NodeResult(node_id="node_1", ok=False, error="Something went wrong")
        assert result.ok is False
        assert result.error == "Something went wrong"
        assert result.skipped is False

    def test_create_skipped_result(self) -> None:
        """Creating skipped result should succeed."""
        result = NodeResult(node_id="node_1", ok=True, skipped=True)
        assert result.ok is True
        assert result.skipped is True


class TestGraphExecutionResult:
    """Tests for GraphExecutionResult dataclass."""

    def test_create_successful_result(self) -> None:
        """Creating successful execution result should succeed."""
        node_result = NodeResult(node_id="node_1", ok=True)
        result = GraphExecutionResult(
            ok=True,
            node_results={"node_1": node_result},
            total_nodes=1,
            completed_nodes=1,
            failed_nodes=0,
            skipped_nodes=0,
            duration_ms=100,
        )
        assert result.ok is True
        assert result.completed_nodes == 1
        assert result.failed_nodes == 0

    def test_create_mixed_result(self) -> None:
        """Creating result with mixed outcomes should succeed."""
        results = {
            "node_1": NodeResult(node_id="node_1", ok=True),
            "node_2": NodeResult(node_id="node_2", ok=False, error="Failed"),
            "node_3": NodeResult(node_id="node_3", ok=True, skipped=True),
        }
        result = GraphExecutionResult(
            ok=False,
            node_results=results,
            total_nodes=3,
            completed_nodes=1,
            failed_nodes=1,
            skipped_nodes=1,
            duration_ms=50,
        )
        assert result.ok is False
        assert result.completed_nodes == 1
        assert result.failed_nodes == 1
        assert result.skipped_nodes == 1
