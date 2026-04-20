"""Tool Composer - Automatic tool chain generation from high-level goals.

This module provides the ToolComposer class that automatically generates
tool call graphs from natural language goals.

Phase 1 (S1-2): Tool Composition Capability
- Goal Analysis: Decompose high-level goals into required capabilities
- Tool Selection: Match capabilities to available tools via semantic tags
- Dependency Resolution: Order selected tools based on input/output dependencies
- Graph Construction: Build executable DAG from selected tools
- Confidence Evaluation: Assess composition quality via LLM reasoning
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from polaris.kernelone.llm.contracts.tool import ToolCall
from polaris.kernelone.tool_execution.graph import (
    ToolCallEdge,
    ToolCallGraph,
    ToolCallNode,
)
from polaris.kernelone.tool_execution.tool_spec_registry import (
    ToolSpecRegistry,
)


@runtime_checkable
class LLMAnalyzer(Protocol):
    """Minimal protocol for LLM-based goal analysis."""

    async def analyze_goal(self, goal: str) -> list[str]: ...


logger = logging.getLogger(__name__)


# =============================================================================
# Capability & Selection Models
# =============================================================================


@dataclass(frozen=True)
class ToolCapability:
    """Tool capability description for matching.

    Attributes:
        tool_name: Canonical tool name.
        input_type: Type of input this tool consumes.
        output_type: Type of output this tool produces.
        description: Human-readable description of capability.
        semantic_tags: Semantic keywords for capability matching.
    """

    tool_name: str
    input_type: str
    output_type: str
    description: str
    semantic_tags: tuple[str, ...]


@dataclass
class ToolSelection:
    """A tool selected for inclusion in a composition.

    Attributes:
        capability: The capability this tool provides.
        confidence: Confidence score (0.0-1.0) for this selection.
        reasoning: Why this tool was selected.
        args: Estimated arguments based on goal analysis.
    """

    capability: ToolCapability
    confidence: float
    reasoning: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GoalAnalysis:
    """Result of goal decomposition.

    Attributes:
        required_capabilities: List of capabilities needed to achieve the goal.
        reasoning: Explanation of how the goal was decomposed.
        estimated_steps: Estimated number of execution steps.
        potential_blockers: Potential issues that might prevent execution.
    """

    required_capabilities: tuple[str, ...]
    reasoning: str
    estimated_steps: int
    potential_blockers: tuple[str, ...] = field(default_factory=tuple)


# =============================================================================
# Constraints
# =============================================================================


@dataclass(frozen=True)
class Constraints:
    """Constraints for tool composition.

    Attributes:
        allowed_categories: Limit tool selection to these categories.
        excluded_tools: Tool names to exclude from selection.
        max_tools: Maximum number of tools in the composition.
        prefer_read_only: Prefer read-only tools when possible.
        workspace: Workspace path for context.
    """

    allowed_categories: tuple[str, ...] = field(default_factory=lambda: ("read", "write", "exec"))
    excluded_tools: tuple[str, ...] = field(default_factory=tuple)
    max_tools: int = 10
    prefer_read_only: bool = False
    workspace: str = "."


# =============================================================================
# Composition Result
# =============================================================================


@dataclass
class CompositionResult:
    """Result of tool composition.

    Attributes:
        graph: The constructed tool call graph.
        confidence: Overall confidence score (0.0-1.0).
        reasoning: Explanation of the composition.
        selected_tools: List of tool selections in execution order.
    """

    graph: ToolCallGraph
    confidence: float
    reasoning: str
    selected_tools: tuple[ToolSelection, ...] = field(default_factory=tuple)


# =============================================================================
# Capability Registry
# =============================================================================


class CapabilityRegistry:
    """Registry mapping capabilities to tool specs.

    This registry maintains a mapping from semantic capability tags
    to available tools, enabling capability-based tool selection.
    """

    def __init__(self) -> None:
        """Initialize empty capability registry."""
        self._capabilities: dict[str, list[ToolCapability]] = {}
        self._tool_capabilities: dict[str, list[str]] = {}

    def register_capability(
        self,
        capability: ToolCapability,
    ) -> None:
        """Register a tool capability.

        Args:
            capability: The capability to register.
        """
        for tag in capability.semantic_tags:
            tag_lower = tag.lower()
            if tag_lower not in self._capabilities:
                self._capabilities[tag_lower] = []
            self._capabilities[tag_lower].append(capability)

        if capability.tool_name not in self._tool_capabilities:
            self._tool_capabilities[capability.tool_name] = []
        self._tool_capabilities[capability.tool_name].extend(capability.semantic_tags)

    def find_by_tag(
        self,
        tag: str,
    ) -> list[ToolCapability]:
        """Find capabilities matching a semantic tag.

        Args:
            tag: Semantic tag to search for.

        Returns:
            List of matching capabilities.
        """
        return self._capabilities.get(tag.lower(), [])

    def find_by_tags(
        self,
        tags: tuple[str, ...],
    ) -> list[ToolCapability]:
        """Find capabilities matching any of the given tags.

        Args:
            tags: Semantic tags to search for.

        Returns:
            List of matching capabilities (deduplicated).
        """
        seen: set[str] = set()
        result: list[ToolCapability] = []
        for tag in tags:
            for cap in self._capabilities.get(tag.lower(), []):
                if cap.tool_name not in seen:
                    seen.add(cap.tool_name)
                    result.append(cap)
        return result

    def get_all_capabilities(self) -> list[ToolCapability]:
        """Get all registered capabilities.

        Returns:
            List of all capabilities.
        """
        seen: set[str] = set()
        result: list[ToolCapability] = []
        for caps in self._capabilities.values():
            for cap in caps:
                if cap.tool_name not in seen:
                    seen.add(cap.tool_name)
                    result.append(cap)
        return result


def _build_capability_registry() -> CapabilityRegistry:
    """Build capability registry from ToolSpecRegistry.

    Returns:
        CapabilityRegistry populated from registered tools.
    """
    registry = CapabilityRegistry()

    # Semantic tag mappings for common capabilities
    tag_mappings: dict[str, tuple[str, ...]] = {
        "file_read": ("read", "file", "content", "view"),
        "file_write": ("write", "create", "file", "content"),
        "file_search": ("search", "grep", "find", "code", "pattern"),
        "file_tree": ("tree", "list", "directory", "structure"),
        "file_edit": ("edit", "replace", "modify", "change"),
        "git_diff": ("diff", "changes", "git", "uncommitted"),
        "symbol_index": ("symbol", "index", "class", "function"),
        "command_exec": ("execute", "run", "command", "shell"),
        "task_create": ("task", "create", "new"),
        "task_update": ("task", "update", "status"),
        "task_list": ("task", "list", "ready"),
        "context_compact": ("context", "compact", "compress"),
        "memory_search": ("memory", "search", "context"),
        "artifact_read": ("artifact", "read", "context"),
        "background_run": ("background", "async", "long_running"),
        "background_wait": ("wait", "background", "complete"),
    }

    for spec in ToolSpecRegistry.get_all_tools():
        # Determine input/output types based on categories
        if "read" in spec.categories:
            input_type = "path_query"
            output_type = "content"
        elif "write" in spec.categories:
            input_type = "content_path"
            output_type = "confirmation"
        elif "exec" in spec.categories:
            input_type = "command_args"
            output_type = "result"
        else:
            input_type = "any"
            output_type = "any"

        # Extract semantic tags
        semantic_tags: list[str] = []
        canonical = spec.canonical_name.lower()
        for _cap_key, tags in tag_mappings.items():
            if any(tag in canonical for tag in tags):
                semantic_tags.extend(tags)
        semantic_tags.append(spec.canonical_name.replace("_", " "))

        # Add description words as tags
        desc_lower = spec.description.lower()
        for word in desc_lower.split():
            if len(word) > 3 and word not in ("tool", "for", "the", "and", "with"):
                semantic_tags.append(word)

        capability = ToolCapability(
            tool_name=spec.canonical_name,
            input_type=input_type,
            output_type=output_type,
            description=spec.description,
            semantic_tags=tuple(set(semantic_tags)),
        )
        registry.register_capability(capability)

    return registry


# =============================================================================
# Tool Composer
# =============================================================================


class ToolComposer:
    """Tool composer - automatically generate tool chains from goals.

    This class decomposes high-level goals into executable tool call graphs
    by analyzing goal semantics, matching capabilities, resolving dependencies,
    and building DAG-structured execution plans.

    Attributes:
        _registry: Tool specification registry.
        _llm: LLM provider for reasoning and confidence evaluation.
        _capability_registry: Registry of tool capabilities.
    """

    def __init__(
        self,
        tool_registry: type[ToolSpecRegistry],
        llm: LLMAnalyzer,
    ) -> None:
        """Initialize the tool composer.

        Args:
            tool_registry: Tool specification registry class.
            llm: LLM provider for reasoning and confidence evaluation.
        """
        self._registry = tool_registry
        self._llm = llm
        self._capability_registry = _build_capability_registry()

    async def compose(
        self,
        goal: str,
        constraints: Constraints,
    ) -> CompositionResult:
        """Compose a tool call graph from a high-level goal.

        Args:
            goal: High-level goal description.
            constraints: Composition constraints.

        Returns:
            CompositionResult with the constructed graph and confidence.
        """
        # Step 1: Analyze the goal
        goal_analysis = await self._analyze_goal(goal, constraints)

        # Step 2: Select tools for required capabilities
        selected_tools = await self._select_tools(
            goal_analysis.required_capabilities,
            constraints,
        )

        # Step 3: Resolve dependencies and order
        execution_order = self._resolve_dependencies(selected_tools)

        # Step 4: Build the tool call graph
        graph = self._build_graph(execution_order, goal_analysis)

        # Step 5: Evaluate confidence
        confidence = await self._evaluate_confidence(graph, goal, goal_analysis)

        return CompositionResult(
            graph=graph,
            confidence=confidence,
            reasoning=goal_analysis.reasoning,
            selected_tools=tuple(selected_tools),
        )

    async def _analyze_goal(
        self,
        goal: str,
        constraints: Constraints,
    ) -> GoalAnalysis:
        """Analyze a goal to determine required capabilities.

        Uses keyword matching and LLM reasoning to decompose goals
        into required capabilities.

        Args:
            goal: High-level goal description.
            constraints: Composition constraints.

        Returns:
            GoalAnalysis with required capabilities.
        """
        goal_lower = goal.lower()

        # Map goal keywords to capabilities
        capability_map: dict[str, tuple[str, ...]] = {
            "read": ("file_read",),
            "view": ("file_read",),
            "show": ("file_read",),
            "find": ("file_search",),
            "search": ("file_search",),
            "grep": ("file_search",),
            "list": ("file_tree",),
            "tree": ("file_tree",),
            "write": ("file_write",),
            "create": ("file_write",),
            "edit": ("file_edit",),
            "modify": ("file_edit",),
            "replace": ("file_edit",),
            "changes": ("git_diff",),
            "diff": ("git_diff",),
            "symbols": ("symbol_index",),
            "index": ("symbol_index",),
            "run": ("command_exec",),
            "execute": ("command_exec",),
            "command": ("command_exec",),
            "task": ("task_create", "task_list"),
            "background": ("background_run",),
            "async": ("background_run",),
            "wait": ("background_wait",),
            "compress": ("context_compact",),
            "compact": ("context_compact",),
            "memory": ("memory_search",),
            "artifact": ("artifact_read",),
        }

        required: list[str] = []
        seen_capabilities: set[str] = set()

        # Match keywords to capabilities
        for keyword, capabilities in capability_map.items():
            if keyword in goal_lower:
                for cap in capabilities:
                    if cap not in seen_capabilities:
                        required.append(cap)
                        seen_capabilities.add(cap)

        # If no capabilities matched, use LLM to analyze
        if not required:
            required = await self._llm_analyze_goal(goal)

        # Estimate steps
        estimated_steps = len(required) + 1

        # Check for potential blockers
        blockers: list[str] = []
        if constraints.max_tools < len(required):
            blockers.append(f"Goal requires {len(required)} tools but max_tools is {constraints.max_tools}")

        reasoning = (
            f"Analyzed goal '{goal[:50]}...' -> identified capabilities: {required}. "
            f"Estimated {estimated_steps} execution steps."
        )

        return GoalAnalysis(
            required_capabilities=tuple(required),
            reasoning=reasoning,
            estimated_steps=estimated_steps,
            potential_blockers=tuple(blockers),
        )

    async def _llm_analyze_goal(
        self,
        goal: str,
    ) -> list[str]:
        """Use LLM to analyze a goal and extract capabilities.

        Args:
            goal: High-level goal description.

        Returns:
            List of capability identifiers.
        """
        # Simple keyword-based fallback for now
        # In production, this would call the LLM
        goal_lower = goal.lower()

        capabilities: list[str] = []

        # Basic keyword matching
        if any(kw in goal_lower for kw in ["read", "view", "show", "file", "content"]):
            capabilities.append("file_read")
        if any(kw in goal_lower for kw in ["search", "find", "grep", "find"]):
            capabilities.append("file_search")
        if any(kw in goal_lower for kw in ["write", "create", "new", "file"]):
            capabilities.append("file_write")
        if any(kw in goal_lower for kw in ["edit", "modify", "replace", "change"]):
            capabilities.append("file_edit")
        if any(kw in goal_lower for kw in ["list", "tree", "directory"]):
            capabilities.append("file_tree")

        return capabilities if capabilities else ["file_read"]

    async def _select_tools(
        self,
        capabilities: tuple[str, ...],
        constraints: Constraints,
    ) -> list[ToolSelection]:
        """Select tools that satisfy the required capabilities.

        Args:
            capabilities: Required capabilities.
            constraints: Selection constraints.

        Returns:
            List of tool selections.
        """
        selections: list[ToolSelection] = []
        all_caps = self._capability_registry.get_all_capabilities()

        # Map capability names to tool name patterns
        capability_to_tool_map: dict[str, tuple[str, ...]] = {
            "file_read": ("repo_read_head", "repo_read_slice", "repo_read_around", "read_file"),
            "file_search": ("repo_rg", "grep", "search"),
            "file_write": ("write_file", "append_to_file"),
            "file_edit": ("precision_edit", "edit_file", "search_replace"),
            "file_tree": ("repo_tree", "repo_map"),
            "git_diff": ("repo_diff",),
            "symbol_index": ("repo_symbols_index",),
            "command_exec": ("execute_command", "background_run"),
            "task_create": ("task_create",),
            "task_update": ("task_update",),
            "task_list": ("task_ready", "todo_read"),
            "context_compact": ("compact_context",),
            "memory_search": ("search_memory",),
            "artifact_read": ("read_artifact",),
            "background_run": ("background_run",),
            "background_wait": ("background_wait",),
        }

        for cap_name in capabilities:
            matches: list[ToolCapability] = []

            # First try direct tag match
            tag_matches = self._capability_registry.find_by_tag(cap_name)
            if tag_matches:
                matches.extend(tag_matches)

            # Fallback: look up tool names for this capability
            tool_names = capability_to_tool_map.get(cap_name, ())
            for tool_name in tool_names:
                for capability in all_caps:
                    if capability.tool_name == tool_name and capability not in matches:
                        matches.append(capability)

            if not matches:
                logger.warning("[ToolComposer] No tool found for capability: %s", cap_name)
                continue

            # Score and rank matches
            best_match: ToolCapability | None = None
            best_confidence = 0.0

            for match in matches:
                # Filter by constraints
                spec = self._registry.get(match.tool_name)
                if not spec:
                    continue

                # Check excluded tools
                if match.tool_name in constraints.excluded_tools:
                    continue

                # Check categories
                if not any(cat in constraints.allowed_categories for cat in spec.categories):
                    continue

                # Calculate confidence
                confidence = self._calculate_selection_confidence(match, constraints)

                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = match

            if best_match:
                selection = ToolSelection(
                    capability=best_match,
                    confidence=best_confidence,
                    reasoning=f"Selected {best_match.tool_name} for {cap_name}",
                    args={},
                )
                selections.append(selection)

        return selections

    def _calculate_selection_confidence(
        self,
        capability: ToolCapability,
        constraints: Constraints,
    ) -> float:
        """Calculate confidence score for a tool selection.

        Args:
            capability: The capability to score.
            constraints: Selection constraints.

        Returns:
            Confidence score between 0.0 and 1.0.
        """
        confidence = 0.7  # Base confidence

        spec = self._registry.get(capability.tool_name)
        if not spec:
            return 0.0

        # Penalize if not in preferred categories
        if constraints.prefer_read_only and "write" in spec.categories:
            confidence -= 0.2

        # Boost for exact capability match
        confidence += 0.15

        # Boost for read tools (generally safer)
        if "read" in spec.categories:
            confidence += 0.1

        return max(0.0, min(1.0, confidence))

    def _resolve_dependencies(
        self,
        tools: list[ToolSelection],
    ) -> list[ToolSelection]:
        """Resolve execution order based on input/output dependencies.

        Args:
            tools: Selected tools to order.

        Returns:
            Tools in execution order.
        """
        if not tools:
            return []

        # Build dependency graph
        # Output of tool A becomes input to tool B if B's input type matches A's output type
        ordered: list[ToolSelection] = []
        remaining = list(tools)
        placed: set[str] = set()

        while remaining:
            made_progress = False
            for i, tool in enumerate(remaining):
                # Check if this tool's dependencies are satisfied
                deps_satisfied = True

                for placed_tool in ordered:
                    # If placed tool produces what this tool needs
                    if self._is_dependency_satisfied(placed_tool.capability, tool.capability):
                        deps_satisfied = True
                        break

                if deps_satisfied:
                    ordered.append(tool)
                    placed.add(tool.capability.tool_name)
                    remaining.pop(i)
                    made_progress = True
                    break

            if not made_progress:
                # Circular dependency or no dependencies - just append remaining
                ordered.extend(remaining)
                break

        return ordered

    def _is_dependency_satisfied(
        self,
        producer: ToolCapability,
        consumer: ToolCapability,
    ) -> bool:
        """Check if producer's output satisfies consumer's input needs.

        Args:
            producer: Tool producing output.
            consumer: Tool consuming input.

        Returns:
            True if dependency is satisfied.
        """
        # Read tools produce content that search tools can consume
        if consumer.input_type == "path_query" and producer.output_type == "content":
            return True

        # Search tools produce locations that read tools can use
        if consumer.input_type == "content_path" and producer.output_type in ("content", "location"):
            return True

        # Write tools don't typically depend on other writes
        if producer.output_type == "confirmation":
            return False

        return False

    def _build_graph(
        self,
        tools: list[ToolSelection],
        analysis: GoalAnalysis,
    ) -> ToolCallGraph:
        """Build a tool call graph from selected tools.

        Args:
            tools: Selected tools in execution order.
            analysis: Goal analysis context.

        Returns:
            Constructed ToolCallGraph.
        """
        nodes: list[ToolCallNode] = []
        edges: list[ToolCallEdge] = []

        for i, selection in enumerate(tools):
            node_id = f"step_{i}_{selection.capability.tool_name}"

            # Create tool call with estimated args
            tool_call = ToolCall(
                id=node_id,
                name=selection.capability.tool_name,
                arguments=selection.args,
                source="composer",
            )

            node = ToolCallNode(
                id=node_id,
                tool_call=tool_call,
            )
            nodes.append(node)

            # Add edge from previous node if dependency exists
            if i > 0:
                prev_selection = tools[i - 1]
                # Check if there's a data dependency
                if self._is_dependency_satisfied(prev_selection.capability, selection.capability):
                    edges.append(
                        ToolCallEdge(
                            from_id=f"step_{i - 1}_{prev_selection.capability.tool_name}",
                            to_id=node_id,
                        )
                    )

        return ToolCallGraph(
            nodes=tuple(nodes),
            edges=tuple(edges),
        )

    async def _evaluate_confidence(
        self,
        graph: ToolCallGraph,
        goal: str,
        analysis: GoalAnalysis,
    ) -> float:
        """Evaluate confidence in the composed graph.

        Args:
            graph: The composed tool call graph.
            goal: Original goal.
            analysis: Goal analysis.

        Returns:
            Confidence score between 0.0 and 1.0.
        """
        # Base confidence from analysis
        base_confidence = 0.8

        # Reduce confidence for potential blockers
        if analysis.potential_blockers:
            base_confidence -= 0.1 * len(analysis.potential_blockers)

        # Reduce confidence if estimated steps seems high
        if analysis.estimated_steps > 5:
            base_confidence -= 0.05 * (analysis.estimated_steps - 5)

        # Boost confidence for good tool coverage
        if len(graph.nodes) >= len(analysis.required_capabilities):
            base_confidence += 0.1

        return max(0.0, min(1.0, base_confidence))


__all__ = [
    "CapabilityRegistry",
    "CompositionResult",
    "Constraints",
    "GoalAnalysis",
    "ToolCapability",
    "ToolComposer",
    "ToolSelection",
]
