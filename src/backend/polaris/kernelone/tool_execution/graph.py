"""Tool call graph for KernelOne tool execution.

This module provides DAG-based tool call execution with parallel execution support,
conditional branching, and retry policies.

Phase 1 (AGI Core Capability):
- ToolCallGraph: DAG structure for tool calls
- GraphExecutor: Parallel execution engine
- ConditionEvaluator: Dynamic branching support
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.contracts.tool import ToolCall, ToolExecutorPort
from polaris.kernelone.workflow.contracts import RetryPolicy

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)


# Default timeout for tool call execution (seconds)
DEFAULT_TIMEOUT_SECONDS: int = 30

# Maximum concurrent parallel executions
DEFAULT_MAX_CONCURRENCY: int = 8


@dataclass(frozen=True)
class ToolCallNode:
    """A node in a tool call graph representing a single tool execution.

    Attributes:
        id: Unique identifier for this node within the graph.
        tool_call: The tool call to execute.
        condition: Optional condition expression for conditional execution.
            If evaluates to falsy, node is skipped.
        retry_policy: Optional retry policy for failed executions.
        timeout_seconds: Execution timeout in seconds (default: 30).
    """

    id: str
    tool_call: ToolCall
    condition: str | None = None
    retry_policy: RetryPolicy | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class ToolCallEdge:
    """A directed edge in a tool call graph representing a dependency.

    Attributes:
        from_id: Source node ID.
        to_id: Target node ID (execution depends on from_id completing).
        condition: Optional condition for this edge.
            If evaluates to falsy, target node is not triggered.
    """

    from_id: str
    to_id: str
    condition: str | None = None


@dataclass(frozen=True)
class ToolCallGraph:
    """A directed acyclic graph (DAG) of tool calls.

    Attributes:
        nodes: Tuple of tool call nodes in the graph.
        edges: Tuple of directed edges defining dependencies.
        entry_points: Tuple of node IDs that have no incoming edges.
            These nodes are executed first (possibly in parallel).
    """

    nodes: tuple[ToolCallNode, ...]
    edges: tuple[ToolCallEdge, ...]
    entry_points: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class NodeResult:
    """Result of executing a single node in the graph.

    Attributes:
        node_id: ID of the executed node.
        ok: Whether execution succeeded.
        result: Execution result from the tool (if successful).
        error: Error message (if failed).
        skipped: Whether node was skipped due to condition.
        duration_ms: Execution duration in milliseconds.
    """

    node_id: str
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None
    skipped: bool = False
    duration_ms: int = 0


@dataclass
class GraphExecutionResult:
    """Result of executing an entire tool call graph.

    Attributes:
        ok: Whether all node executions succeeded.
        node_results: Mapping from node ID to NodeResult.
        total_nodes: Total number of nodes in the graph.
        completed_nodes: Number of successfully completed nodes.
        failed_nodes: Number of failed nodes.
        skipped_nodes: Number of skipped nodes.
        duration_ms: Total execution duration in milliseconds.
    """

    ok: bool
    node_results: dict[str, NodeResult]
    total_nodes: int = 0
    completed_nodes: int = 0
    failed_nodes: int = 0
    skipped_nodes: int = 0
    duration_ms: int = 0


class ConditionEvaluator:
    """Evaluates condition expressions for graph branching.

    This is the default implementation that supports simple expression evaluation.
    Subclasses can override evaluate() for custom condition logic.
    """

    def evaluate(
        self,
        condition: str,
        context: Mapping[str, Any],
    ) -> bool:
        """Evaluate a condition expression.

        Args:
            condition: Condition expression string.
            context: Execution context for variable resolution.

        Returns:
            True if condition is truthy, False otherwise.
        """
        if not condition:
            return True

        # Default implementation: treat empty/missing context as True
        # Subclasses can override for custom logic (e.g., JMESPath, JSONLogic)
        try:
            # Simple expression evaluation: check if condition exists in context
            # and is truthy
            value = context.get(condition)
            return bool(value)
        except (RuntimeError, ValueError):
            logger.warning("[Graph] Condition evaluation failed for: %s", condition)
            return False


class DefaultConditionEvaluator(ConditionEvaluator):
    """Default condition evaluator with basic expression support."""

    def evaluate(
        self,
        condition: str,
        context: Mapping[str, Any],
    ) -> bool:
        """Evaluate condition with default logic.

        Supports:
        - Truthy check: condition key exists and is truthy
        - Negation: "!<key>" returns True if key is falsy or missing
        - Comparison: "key==value" or "key!=value" for equality checks

        Args:
            condition: Condition expression.
            context: Execution context mapping.

        Returns:
            Boolean evaluation result.
        """
        if not condition:
            return True

        # Handle negation
        if condition.startswith("!"):
            key = condition[1:]
            value = context.get(key)
            return not bool(value)

        # Handle equality comparison
        if "==" in condition:
            key, expected = condition.split("==", 1)
            key = key.strip()
            expected = expected.strip()
            value = context.get(key)
            return str(value) == expected

        if "!=" in condition:
            key, unexpected = condition.split("!=", 1)
            key = key.strip()
            unexpected = unexpected.strip()
            value = context.get(key)
            return str(value) != unexpected

        # Simple truthy check
        value = context.get(condition)
        return bool(value)


@dataclass
class ExecutionContext:
    """Context passed during graph execution.

    Attributes:
        workspace: Workspace path for tool execution.
        node_results: Mapping from node ID to NodeResult for completed nodes.
        metadata: Additional execution metadata.
    """

    workspace: str
    node_results: dict[str, NodeResult] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_node_result(self, node_id: str) -> NodeResult | None:
        """Get result of a specific node."""
        return self.node_results.get(node_id)

    def is_node_completed(self, node_id: str) -> bool:
        """Check if a node has completed successfully."""
        result = self.node_results.get(node_id)
        return result is not None and result.ok


class GraphExecutor:
    """Executes a ToolCallGraph with parallel execution support.

    This executor:
    - Resolves node dependencies from edges
    - Executes nodes in parallel when dependencies are met
    - Supports conditional branching via ConditionEvaluator
    - Applies retry policies on failure
    - Respects timeout settings per node

    Attributes:
        executor: Tool executor port for actual tool execution.
        evaluator: Condition evaluator for branching logic.
        max_concurrency: Maximum parallel executions (default: 8).
    """

    def __init__(
        self,
        executor: ToolExecutorPort,
        evaluator: ConditionEvaluator | None = None,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    ) -> None:
        """Initialize the graph executor.

        Args:
            executor: Tool executor port for executing individual tool calls.
            evaluator: Condition evaluator for branching. Uses default if None.
            max_concurrency: Maximum concurrent node executions.
        """
        self._executor = executor
        self._evaluator = evaluator or DefaultConditionEvaluator()
        self._max_concurrency = max_concurrency

    async def execute(
        self,
        graph: ToolCallGraph,
        initial_context: ExecutionContext | None = None,
    ) -> GraphExecutionResult:
        """Execute a tool call graph.

        Args:
            graph: The tool call graph to execute.
            initial_context: Optional initial execution context.

        Returns:
            GraphExecutionResult with execution outcomes.
        """
        import time

        start_time = time.time()
        context = initial_context or ExecutionContext(workspace=".")

        # Build adjacency list, in-degree map, and edge condition map
        adjacency: dict[str, list[str]] = {node.id: [] for node in graph.nodes}
        in_degree: dict[str, int] = {node.id: 0 for node in graph.nodes}
        node_map: dict[str, ToolCallNode] = {node.id: node for node in graph.nodes}
        # Map from dependent_id to list of (source_id, edge_condition)
        incoming_edges: dict[str, list[tuple[str, str | None]]] = {node.id: [] for node in graph.nodes}

        for edge in graph.edges:
            if edge.from_id in adjacency and edge.to_id in node_map:
                adjacency[edge.from_id].append(edge.to_id)
                in_degree[edge.to_id] += 1
                incoming_edges[edge.to_id].append((edge.from_id, edge.condition))

        # Track edge-skip status: edge skipped if its condition evaluated to False
        edge_skipped: dict[tuple[str, str], bool] = {}

        # Initialize results
        node_results: dict[str, NodeResult] = {}
        completed = 0
        failed = 0
        skipped = 0

        # Determine initial ready nodes (entry points or 0 in-degree)
        entry_ids = (
            set(graph.entry_points)
            if graph.entry_points
            else {node_id for node_id, degree in in_degree.items() if degree == 0}
        )
        ready_queue = list(entry_ids)

        # Semaphore for concurrency control
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def execute_node(node: ToolCallNode) -> NodeResult:
            """Execute a single node with retry and timeout."""
            nonlocal completed, failed

            # Check node condition
            if node.condition and not self._evaluator.evaluate(node.condition, context.metadata):
                return NodeResult(
                    node_id=node.id,
                    ok=True,
                    skipped=True,
                )

            # Apply retry policy
            retry_policy = node.retry_policy
            max_attempts = retry_policy.max_attempts if retry_policy else 1

            last_error: str | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    # Execute with timeout
                    result = await asyncio.wait_for(
                        asyncio.to_thread(
                            self._executor.execute_call,
                            workspace=context.workspace,
                            tool_call=node.tool_call,
                        ),
                        timeout=node.timeout_seconds,
                    )

                    if result.success:
                        node_result = NodeResult(
                            node_id=node.id,
                            ok=True,
                            result=result.result,
                        )
                        context.node_results[node.id] = node_result
                        return node_result
                    else:
                        last_error = result.error or "unknown error"

                except asyncio.TimeoutError:
                    last_error = f"timeout after {node.timeout_seconds}s"
                except (RuntimeError, ValueError) as exc:
                    last_error = str(exc)

                # Apply backoff if retry available
                if attempt < max_attempts and retry_policy:
                    sleep_time = min(
                        retry_policy.initial_interval_seconds * (retry_policy.backoff_coefficient ** (attempt - 1)),
                        retry_policy.max_interval_seconds,
                    )
                    await asyncio.sleep(sleep_time)

            # All attempts failed
            node_result = NodeResult(
                node_id=node.id,
                ok=False,
                error=last_error,
            )
            context.node_results[node.id] = node_result
            failed += 1
            return node_result

        async def execute_parallel(node_ids: list[str]) -> None:
            """Execute multiple nodes in parallel."""
            nonlocal completed, skipped

            async with semaphore:
                tasks = [execute_node(node_map[node_id]) for node_id in node_ids if node_id in node_map]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for node_id, result in zip(node_ids, results, strict=False):
                    if isinstance(result, Exception):
                        node_results[node_id] = NodeResult(
                            node_id=node_id,
                            ok=False,
                            error=str(result),
                        )
                        failed += 1
                    elif isinstance(result, NodeResult):
                        node_results[node_id] = result
                        if result.skipped:
                            skipped += 1
                        elif result.ok:
                            completed += 1
                    else:
                        node_results[node_id] = NodeResult(
                            node_id=node_id,
                            ok=False,
                            error="unexpected result type",
                        )
                        failed += 1

        # Execute in topological order with parallelism
        while ready_queue:
            # Execute current batch in parallel
            await execute_parallel(ready_queue)

            # Find next batch: nodes whose all incoming edges are satisfied
            next_ready: list[str] = []
            for node_id in ready_queue:
                for d_id in adjacency.get(node_id, []):
                    # Check if this edge (from node_id to d_id) is satisfied
                    edge_key = (node_id, d_id)
                    if edge_key in edge_skipped:
                        continue  # already evaluated

                    # Find the condition for this edge
                    edge_condition: str | None = None
                    for src, cond in incoming_edges.get(d_id, []):
                        if src == node_id:
                            edge_condition = cond
                            break

                    # Evaluate edge condition
                    if edge_condition is not None:
                        satisfied = self._evaluator.evaluate(edge_condition, context.metadata)
                        edge_skipped[edge_key] = not satisfied
                    else:
                        edge_skipped[edge_key] = False

            # Check each dependent node
            for node_id in ready_queue:
                for dependent_id in adjacency.get(node_id, []):
                    # If the edge was skipped, decrement in_degree but don't add to ready
                    edge_key = (node_id, dependent_id)
                    if edge_skipped.get(edge_key, False):
                        in_degree[dependent_id] -= 1
                        continue

                    in_degree[dependent_id] -= 1
                    if in_degree[dependent_id] == 0 and dependent_id not in node_results:
                        next_ready.append(dependent_id)

            ready_queue = next_ready

        # Collect remaining nodes that weren't executed (shouldn't happen in valid DAG)
        for node in graph.nodes:
            if node.id not in node_results:
                node_results[node.id] = NodeResult(
                    node_id=node.id,
                    ok=False,
                    error="node not executed (dependency cycle or invalid graph)",
                )
                failed += 1

        duration_ms = int((time.time() - start_time) * 1000)

        return GraphExecutionResult(
            ok=failed == 0,
            node_results=node_results,
            total_nodes=len(graph.nodes),
            completed_nodes=completed,
            failed_nodes=failed,
            skipped_nodes=skipped,
            duration_ms=duration_ms,
        )

    def build_graph(
        self,
        nodes: list[ToolCallNode],
        edges: list[ToolCallEdge],
    ) -> ToolCallGraph:
        """Build a ToolCallGraph from nodes and edges.

        Args:
            nodes: List of tool call nodes.
            edges: List of tool call edges.

        Returns:
            ToolCallGraph instance.
        """
        # Determine entry points (nodes with no incoming edges)
        target_ids = {edge.to_id for edge in edges}
        entry_points = tuple(node.id for node in nodes if node.id not in target_ids)

        return ToolCallGraph(
            nodes=tuple(nodes),
            edges=tuple(edges),
            entry_points=entry_points,
        )


class ToolCallGraphBuilder:
    """DSL builder for constructing ToolCallGraph instances.

    Provides a fluent interface for defining tool call graphs with
    nodes, edges, and dependencies.

    Example:
        graph = (
            ToolCallGraphBuilder()
            .node("read", tool_call=read_file_tool)
            .node("search", tool_call=repo_rg_tool, depends_on=["read"])
            .node("edit", tool_call=precision_edit_tool, depends_on=["search"])
            .build()
        )

    Attributes:
        _nodes: Internal list of ToolCallNode instances.
        _edges: Internal list of ToolCallEdge instances.
        _node_map: Lookup map from node ID to ToolCallNode.
    """

    def __init__(self) -> None:
        """Initialize an empty graph builder."""
        self._nodes: list[ToolCallNode] = []
        self._edges: list[ToolCallEdge] = []
        self._node_map: dict[str, ToolCallNode] = {}

    def node(
        self,
        node_id: str,
        *,
        tool_call: ToolCall,
        condition: str | None = None,
        retry_policy: RetryPolicy | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        depends_on: list[str] | None = None,
    ) -> ToolCallGraphBuilder:
        """Add a node to the graph.

        Args:
            node_id: Unique identifier for this node.
            tool_call: The tool call to execute.
            condition: Optional condition expression for conditional execution.
            retry_policy: Optional retry policy for failed executions.
            timeout_seconds: Execution timeout in seconds.
            depends_on: List of node IDs that must complete before this node executes.

        Returns:
            Self for method chaining.
        """
        if node_id in self._node_map:
            raise ValueError(f"Node with id '{node_id}' already exists in graph")

        node = ToolCallNode(
            id=node_id,
            tool_call=tool_call,
            condition=condition,
            retry_policy=retry_policy,
            timeout_seconds=timeout_seconds,
        )
        self._nodes.append(node)
        self._node_map[node_id] = node

        # Add edges for dependencies
        if depends_on:
            for dep_id in depends_on:
                if dep_id not in self._node_map:
                    raise ValueError(f"Dependency '{dep_id}' referenced before being added as a node")
                self._edges.append(ToolCallEdge(from_id=dep_id, to_id=node_id))

        return self

    def edge(
        self,
        from_id: str,
        to_id: str,
        condition: str | None = None,
    ) -> ToolCallGraphBuilder:
        """Add an edge between two nodes.

        Args:
            from_id: Source node ID.
            to_id: Target node ID.
            condition: Optional condition for this edge.

        Returns:
            Self for method chaining.
        """
        if from_id not in self._node_map:
            raise ValueError(f"Source node '{from_id}' does not exist in graph")
        if to_id not in self._node_map:
            raise ValueError(f"Target node '{to_id}' does not exist in graph")

        self._edges.append(ToolCallEdge(from_id=from_id, to_id=to_id, condition=condition))
        return self

    def build(self) -> ToolCallGraph:
        """Build the final ToolCallGraph.

        Returns:
            ToolCallGraph instance with all nodes and edges.

        Raises:
            ValueError: If the graph would contain a cycle or has invalid references.
        """
        # Validate no self-references
        for edge in self._edges:
            if edge.from_id == edge.to_id:
                raise ValueError(f"Self-referencing edge detected: {edge.from_id}")

        # Build and return the graph
        graph = ToolCallGraph(
            nodes=tuple(self._nodes),
            edges=tuple(self._edges),
        )

        # Validate DAG (no cycles)
        if self._has_cycle(graph):
            raise ValueError("Graph contains a cycle")

        return graph

    def _has_cycle(self, graph: ToolCallGraph) -> bool:
        """Check if the graph contains a cycle using DFS.

        Args:
            graph: The graph to check.

        Returns:
            True if cycle exists, False otherwise.
        """
        visited: dict[str, bool] = {}
        rec_stack: dict[str, bool] = {}

        def dfs(node_id: str) -> bool:
            visited[node_id] = True
            rec_stack[node_id] = True

            # Find all nodes this node points to
            outgoing: list[str] = []
            for edge in graph.edges:
                if edge.from_id == node_id:
                    outgoing.append(edge.to_id)

            for neighbor in outgoing:
                if neighbor not in visited:
                    if dfs(neighbor):
                        return True
                elif rec_stack.get(neighbor, False):
                    return True

            rec_stack[node_id] = False
            return False

        return any(node.id not in visited and dfs(node.id) for node in graph.nodes)


__all__ = [
    "ConditionEvaluator",
    "DefaultConditionEvaluator",
    "ExecutionContext",
    "GraphExecutionResult",
    "GraphExecutor",
    "NodeResult",
    "ToolCallEdge",
    "ToolCallGraph",
    "ToolCallGraphBuilder",
    "ToolCallNode",
]
