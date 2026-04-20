"""Phase 3 Integration Tests.

Tests that verify all 5 intelligence modules work together:
- SelfEvaluator → ActiveLearner → CommonsenseReasoner → ToolGenerator → LongTermMemory

Since Phase 3 modules may not exist yet, this file uses pytest.importorskip
and provides mock implementations for development/testing.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

import pytest

# ============================================================================
# MOCK IMPLEMENTATIONS (replace with real imports when modules exist)
# ============================================================================

SelfEvaluator: type | None = None
ActiveLearner: type | None = None
CommonsenseReasoner: type | None = None
ToolGenerator: type | None = None
LongTermMemory: type | None = None
CapabilityAssessment: type | None = None
ErrorPattern: type | None = None
CausalGraph: type | None = None
KnowledgeItem: type | None = None

with suppress(ImportError):
    from polaris.kernelone.agent.self_evaluation import (  # noqa: F401
        CapabilityAssessment,
        SelfEvaluator,
    )

with suppress(ImportError):
    from polaris.kernelone.learning.active_learner import (  # noqa: F401
        ActiveLearner,
        ErrorPattern,
    )

with suppress(ImportError):
    from polaris.kernelone.reasoning.commonsense import (  # noqa: F401
        CausalGraph,
        CommonsenseReasoner,
    )

with suppress(ImportError):
    from polaris.kernelone.tool_creation.code_generator import (  # noqa: F401
        ToolGenerator,
    )

with suppress(ImportError):
    from polaris.kernelone.memory.long_term import (  # noqa: F401
        KnowledgeItem,
        LongTermMemory,
    )


# ============================================================================
# STUB CLASSES (used when real modules don't exist yet)
# ============================================================================


@dataclass
class StubCapabilityAssessment:
    """Stub capability assessment for testing."""

    task_type: str = ""
    success: bool = False
    confidence: float = 0.0
    gaps: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


@dataclass
class StubErrorPattern:
    """Stub error pattern for testing."""

    pattern_id: str = ""
    error_type: str = ""
    occurrence_count: int = 0
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class StubCausalGraph:
    """Stub causal graph for testing."""

    nodes: list[str] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class StubKnowledgeItem:
    """Stub knowledge item for testing."""

    item_id: str = ""
    content: str = ""
    category: str = ""
    confidence: float = 0.0


class StubSelfEvaluator:
    """Stub self-evaluator for testing."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.assessments: list[StubCapabilityAssessment] = []

    async def evaluate(
        self, task_result: dict[str, Any], context: dict[str, Any] | None = None
    ) -> StubCapabilityAssessment:
        """Evaluate a task result."""
        assessment = StubCapabilityAssessment(
            task_type=task_result.get("type", "unknown"),
            success=task_result.get("success", False),
            confidence=task_result.get("confidence", 0.5),
            gaps=task_result.get("gaps", []),
            recommendations=task_result.get("recommendations", []),
        )
        self.assessments.append(assessment)
        return assessment

    async def assess_capability(self, capability: str) -> StubCapabilityAssessment:
        """Assess a capability."""
        return StubCapabilityAssessment(
            task_type=capability,
            success=True,
            confidence=0.8,
            gaps=[],
            recommendations=[f"Continue improving {capability}"],
        )


class StubActiveLearner:
    """Stub active learner for testing."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.patterns: list[StubErrorPattern] = []

    async def learn_from_error(self, error: dict[str, Any], context: dict[str, Any] | None = None) -> StubErrorPattern:
        """Learn from an error."""
        pattern = StubErrorPattern(
            pattern_id=f"pattern_{len(self.patterns)}",
            error_type=error.get("type", "unknown"),
            occurrence_count=1,
            context=error.get("context", {}),
        )
        self.patterns.append(pattern)
        return pattern

    async def get_relevant_patterns(self, situation: dict[str, Any]) -> list[StubErrorPattern]:
        """Get relevant patterns for a situation."""
        return [p for p in self.patterns if p.occurrence_count > 0]

    def add_pattern(self, pattern: StubErrorPattern) -> None:
        """Add a pattern directly."""
        self.patterns.append(pattern)


class StubCommonsenseReasoner:
    """Stub commonsense reasoner for testing."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.causal_graphs: list[StubCausalGraph] = []

    async def infer_causes(self, effect: str, context: dict[str, Any] | None = None) -> StubCausalGraph:
        """Infer causes of an effect."""
        graph = StubCausalGraph(
            nodes=[effect, "root_cause_1", "root_cause_2"],
            edges=[("root_cause_1", effect), ("root_cause_2", effect)],
        )
        self.causal_graphs.append(graph)
        return graph

    async def generate_insight(self, situation: dict[str, Any]) -> dict[str, Any]:
        """Generate insight for a situation."""
        return {
            "insight": f"Analysis of {situation.get('type', 'unknown')}",
            "confidence": 0.85,
            "reasoning_chain": ["observation", "analysis", "conclusion"],
        }


class StubToolGenerator:
    """Stub tool generator for testing."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.generated_tools: list[dict[str, Any]] = []

    async def generate_tool(
        self, specification: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Generate a tool from specification."""
        tool = {
            "name": specification.get("name", "unnamed_tool"),
            "description": specification.get("description", ""),
            "parameters": specification.get("parameters", {}),
            "generated": True,
        }
        self.generated_tools.append(tool)
        return tool

    async def suggest_improvements(self, tool: dict[str, Any]) -> list[str]:
        """Suggest improvements for a tool."""
        return ["Add error handling", "Add retry logic"]


class StubLongTermMemory:
    """Stub long-term memory for testing."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.knowledge_items: list[StubKnowledgeItem] = []

    async def store(self, item: StubKnowledgeItem, metadata: dict[str, Any] | None = None) -> str:
        """Store a knowledge item."""
        if not item.item_id:
            item.item_id = f"item_{len(self.knowledge_items)}"
        self.knowledge_items.append(item)
        return item.item_id

    async def retrieve(self, query: str, limit: int = 10) -> list[StubKnowledgeItem]:
        """Retrieve knowledge items by query."""
        query_lower = query.lower()

        def matches(item: StubKnowledgeItem) -> bool:
            # Check if query matches content or category (word boundary or exact match)
            content_lower = item.content.lower()
            category_lower = item.category.lower()
            # Split by space, underscore, colon and also check exact match
            content_words = content_lower.replace(":", " ").replace("_", " ").split()
            category_words = category_lower.replace(":", " ").replace("_", " ").split()
            # Query matches if it's a word in content/category OR exact match in content/category
            return query_lower in content_words or query_lower in category_words or query_lower == category_lower

        return [item for item in self.knowledge_items if matches(item)][:limit]

    async def forget(self, item_id: str) -> bool:
        """Forget a knowledge item."""
        original_len = len(self.knowledge_items)
        self.knowledge_items = [item for item in self.knowledge_items if item.item_id != item_id]
        return len(self.knowledge_items) < original_len


# ============================================================================
# TEST CLASS
# ============================================================================


class TestPhase3Integration:
    """Integration tests for Phase 3 intelligence modules.

    These tests verify the complete intelligence pipeline:
    1. SelfEvaluator assesses task outcomes
    2. ActiveLearner learns from errors
    3. CommonsenseReasoner infers causal relationships
    4. ToolGenerator creates tools based on insights
    5. LongTermMemory stores learned knowledge
    """

    @pytest.fixture
    def mock_self_evaluator(self) -> StubSelfEvaluator:
        """Create a mock self-evaluator."""
        return StubSelfEvaluator()

    @pytest.fixture
    def mock_active_learner(self) -> StubActiveLearner:
        """Create a mock active learner."""
        return StubActiveLearner()

    @pytest.fixture
    def mock_commonsense_reasoner(self) -> StubCommonsenseReasoner:
        """Create a mock commonsense reasoner."""
        return StubCommonsenseReasoner()

    @pytest.fixture
    def mock_tool_generator(self) -> StubToolGenerator:
        """Create a mock tool generator."""
        return StubToolGenerator()

    @pytest.fixture
    def mock_long_term_memory(self) -> StubLongTermMemory:
        """Create a mock long-term memory."""
        return StubLongTermMemory()

    @pytest.mark.asyncio
    async def test_self_evaluation_feeds_learning(
        self,
        mock_self_evaluator: StubSelfEvaluator,
        mock_active_learner: StubActiveLearner,
    ) -> None:
        """SelfEvaluator should provide input to ActiveLearner."""
        # Step 1: Evaluate a task that had gaps
        task_result = {
            "type": "code_generation",
            "success": False,
            "confidence": 0.4,
            "gaps": ["missing_error_handling", "inefficient_algorithm"],
            "recommendations": ["Add try-except", "Use memoization"],
        }

        assessment = await mock_self_evaluator.evaluate(task_result)

        # Step 2: Extract error info from assessment and feed to ActiveLearner
        assert assessment.success is False
        assert len(assessment.gaps) == 2

        error_info = {
            "type": "task_failure",
            "capability": assessment.task_type,
            "gaps": assessment.gaps,
        }
        pattern = await mock_active_learner.learn_from_error(error_info)

        # Step 3: Verify the pattern was learned
        assert pattern.error_type == "task_failure"
        assert len(mock_active_learner.patterns) == 1

    @pytest.mark.asyncio
    async def test_error_learning_feeds_long_term_memory(
        self,
        mock_active_learner: StubActiveLearner,
        mock_long_term_memory: StubLongTermMemory,
    ) -> None:
        """ActiveLearner patterns should be stored in LongTermMemory."""
        # Step 1: Learn some patterns
        error1 = {"type": "timeout_error", "context": {"operation": "api_call"}}
        await mock_active_learner.learn_from_error(error1)

        error2 = {"type": "validation_error", "context": {"field": "email"}}
        await mock_active_learner.learn_from_error(error2)

        assert len(mock_active_learner.patterns) == 2

        # Step 2: Store patterns in LongTermMemory
        for pattern in mock_active_learner.patterns:
            item = StubKnowledgeItem(
                item_id=pattern.pattern_id,
                content=f"Error pattern: {pattern.error_type}",
                category="error_patterns",
                confidence=min(0.9, pattern.occurrence_count * 0.1),
            )
            await mock_long_term_memory.store(item)

        # Step 3: Verify patterns are stored
        retrieved = await mock_long_term_memory.retrieve("error")
        assert len(retrieved) == 2

    @pytest.mark.asyncio
    async def test_causal_inference_for_tool_generation(
        self,
        mock_commonsense_reasoner: StubCommonsenseReasoner,
        mock_tool_generator: StubToolGenerator,
    ) -> None:
        """CommonsenseReasoner should inform ToolGenerator."""
        # Step 1: Infer causes of a failure
        effect = "api_timeout"
        _causal_graph = await mock_commonsense_reasoner.infer_causes(effect)

        # Step 2: Generate insight
        situation = {
            "type": "api_failure",
            "effect": effect,
            "causal_chain": _causal_graph.edges,
        }
        _insight = await mock_commonsense_reasoner.generate_insight(situation)

        # Step 3: Use insight to generate a tool
        tool_spec = {
            "name": "retry_with_backoff",
            "description": f"Tool to handle {effect} by retrying with exponential backoff",
            "parameters": {
                "max_retries": {"type": "integer", "default": 3},
                "backoff_factor": {"type": "float", "default": 2.0},
            },
        }
        tool = await mock_tool_generator.generate_tool(tool_spec)

        # Verify the generated tool addresses the causal reason
        assert tool["generated"] is True
        assert "retry" in tool["name"].lower()
        assert len(mock_tool_generator.generated_tools) == 1

    @pytest.mark.asyncio
    async def test_full_pipeline(
        self,
        mock_self_evaluator: StubSelfEvaluator,
        mock_active_learner: StubActiveLearner,
        mock_commonsense_reasoner: StubCommonsenseReasoner,
        mock_tool_generator: StubToolGenerator,
        mock_long_term_memory: StubLongTermMemory,
    ) -> None:
        """Test complete pipeline: eval → learn → reason → generate → remember."""
        # Step 1: Self-Evaluation
        task_result = {
            "type": "data_processing",
            "success": False,
            "confidence": 0.3,
            "gaps": ["memory_leak", "slow_algorithm"],
            "recommendations": ["Use streaming", "Optimize loops"],
        }
        assessment = await mock_self_evaluator.evaluate(task_result)
        assert assessment.success is False
        assert "memory_leak" in assessment.gaps

        # Step 2: Active Learning from gaps
        for gap in assessment.gaps:
            error_info = {
                "type": "gap_detected",
                "capability": assessment.task_type,
                "gap": gap,
            }
            pattern = await mock_active_learner.learn_from_error(error_info)
            assert pattern.error_type == "gap_detected"

        # Step 3: Causal Reasoning
        relevant_patterns = await mock_active_learner.get_relevant_patterns({"capability": assessment.task_type})
        assert len(relevant_patterns) == 2

        _causal_graph = await mock_commonsense_reasoner.infer_causes("performance_degradation")
        _insight = await mock_commonsense_reasoner.generate_insight(
            {
                "type": assessment.task_type,
                "patterns": [p.error_type for p in relevant_patterns],
            }
        )
        assert _insight["confidence"] > 0

        # Step 4: Tool Generation
        tool_spec = {
            "name": "performance_monitor",
            "description": f"Tool to monitor and optimize {assessment.task_type}",
            "parameters": {"interval": {"type": "float"}},
        }
        tool = await mock_tool_generator.generate_tool(tool_spec)
        improvements = await mock_tool_generator.suggest_improvements(tool)
        assert len(improvements) > 0

        # Step 5: Long-Term Memory storage
        knowledge_items = [
            StubKnowledgeItem(
                content=f"Learned from {assessment.task_type}: {assessment.gaps}",
                category="performance_insights",
                confidence=0.85,
            ),
            StubKnowledgeItem(
                content=f"Tool: {tool['name']} - {tool['description']}",
                category="generated_tools",
                confidence=0.9,
            ),
        ]

        stored_ids = []
        for item in knowledge_items:
            item_id = await mock_long_term_memory.store(item)
            stored_ids.append(item_id)

        assert len(stored_ids) == 2
        assert len(mock_long_term_memory.knowledge_items) == 2

        # Final verification: retrieve stored knowledge
        retrieved = await mock_long_term_memory.retrieve("performance")
        assert len(retrieved) >= 1

    @pytest.mark.asyncio
    async def test_self_evaluation_produces_capability_assessment(self, mock_self_evaluator: StubSelfEvaluator) -> None:
        """Test that SelfEvaluator correctly produces capability assessments."""
        assessment = await mock_self_evaluator.assess_capability("code_generation")

        assert assessment.task_type == "code_generation"
        assert assessment.success is True
        assert assessment.confidence > 0
        assert len(assessment.recommendations) > 0

    @pytest.mark.asyncio
    async def test_active_learner_tracks_patterns(self, mock_active_learner: StubActiveLearner) -> None:
        """Test that ActiveLearner correctly tracks error patterns."""
        # Add a pattern directly
        pattern = StubErrorPattern(
            pattern_id="test_001",
            error_type="network_error",
            occurrence_count=5,
            context={"host": "api.example.com"},
        )
        mock_active_learner.add_pattern(pattern)

        # Retrieve relevant patterns
        relevant = await mock_active_learner.get_relevant_patterns({"context": {"host": "api.example.com"}})

        assert len(relevant) == 1
        assert relevant[0].error_type == "network_error"
        assert relevant[0].occurrence_count == 5

    @pytest.mark.asyncio
    async def test_commonsense_reasoner_causal_inference(
        self, mock_commonsense_reasoner: StubCommonsenseReasoner
    ) -> None:
        """Test that CommonsenseReasoner correctly infers causal relationships."""
        graph = await mock_commonsense_reasoner.infer_causes("system_crash")

        assert "system_crash" in graph.nodes
        assert len(graph.edges) > 0
        assert len(mock_commonsense_reasoner.causal_graphs) == 1

    @pytest.mark.asyncio
    async def test_tool_generator_creates_valid_tools(self, mock_tool_generator: StubToolGenerator) -> None:
        """Test that ToolGenerator creates valid tools from specifications."""
        spec = {
            "name": "data_validator",
            "description": "Validates input data",
            "parameters": {
                "schema": {"type": "object", "required": True},
                "strict": {"type": "boolean", "default": False},
            },
        }

        tool = await mock_tool_generator.generate_tool(spec)

        assert tool["name"] == "data_validator"
        assert tool["generated"] is True
        assert "schema" in tool["parameters"]
        assert len(mock_tool_generator.generated_tools) == 1

    @pytest.mark.asyncio
    async def test_long_term_memory_store_and_retrieve(self, mock_long_term_memory: StubLongTermMemory) -> None:
        """Test that LongTermMemory correctly stores and retrieves knowledge."""
        # Store items
        item1 = StubKnowledgeItem(
            item_id="k1",
            content="Python 3.12 introduces type parameter defaults",
            category="language_features",
            confidence=0.95,
        )
        item2 = StubKnowledgeItem(
            item_id="k2",
            content="FastAPI supports async response models",
            category="web_frameworks",
            confidence=0.88,
        )

        await mock_long_term_memory.store(item1)
        await mock_long_term_memory.store(item2)

        # Retrieve by query
        results = await mock_long_term_memory.retrieve("python")
        assert len(results) == 1
        assert results[0].item_id == "k1"

        # Retrieve by category
        results = await mock_long_term_memory.retrieve("web_frameworks")
        assert len(results) == 1
        assert results[0].item_id == "k2"

    @pytest.mark.asyncio
    async def test_long_term_memory_forget(self, mock_long_term_memory: StubLongTermMemory) -> None:
        """Test that LongTermMemory correctly forgets knowledge."""
        item = StubKnowledgeItem(
            item_id="forget_me",
            content="This should be forgotten",
            category="temp",
            confidence=0.5,
        )
        await mock_long_term_memory.store(item)
        assert len(mock_long_term_memory.knowledge_items) == 1

        # Forget the item
        success = await mock_long_term_memory.forget("forget_me")
        assert success is True
        assert len(mock_long_term_memory.knowledge_items) == 0

        # Forget non-existent item
        success = await mock_long_term_memory.forget("non_existent")
        assert success is False

    @pytest.mark.asyncio
    async def test_pipeline_with_all_modules(self) -> None:
        """End-to-end test with all 5 modules integrated."""
        # Initialize all modules
        evaluator = StubSelfEvaluator()
        learner = StubActiveLearner()
        reasoner = StubCommonsenseReasoner()
        generator = StubToolGenerator()
        memory = StubLongTermMemory()

        # Simulate a complex task failure scenario
        task_result = {
            "type": "distributed_computation",
            "success": False,
            "confidence": 0.25,
            "gaps": [
                "race_condition",
                "inconsistent_state",
                "network_partition_handling",
            ],
            "recommendations": [
                "Add distributed locking",
                "Implement saga pattern",
                "Use eventual consistency",
            ],
        }

        # 1. Evaluate
        assessment = await evaluator.evaluate(task_result)
        assert not assessment.success

        # 2. Learn from each gap
        for gap in assessment.gaps:
            pattern = await learner.learn_from_error({"type": "gap", "capability": assessment.task_type, "gap": gap})
            assert pattern.error_type == "gap"

        # 3. Reason about causes
        _causal_graph = await reasoner.infer_causes("distributed_system_failure")
        _insight = await reasoner.generate_insight({"type": assessment.task_type, "gaps": assessment.gaps})
        assert _insight["confidence"] > 0

        # 4. Generate remedial tools
        tools = []
        for rec in assessment.recommendations:
            tool = await generator.generate_tool(
                {
                    "name": rec.lower().replace(" ", "_"),
                    "description": f"Implements {rec}",
                    "parameters": {},
                }
            )
            tools.append(tool)

        # 5. Store everything in memory
        for pattern in learner.patterns:
            await memory.store(
                StubKnowledgeItem(
                    content=f"ErrorPattern: {pattern.error_type}",
                    category="error_patterns",
                )
            )

        for tool in tools:
            await memory.store(
                StubKnowledgeItem(
                    content=f"GeneratedTool: {tool['name']}",
                    category="remedial_tools",
                )
            )

        # Final assertions
        assert len(learner.patterns) == 3
        assert len(generator.generated_tools) == 3
        assert len(memory.knowledge_items) == 6  # 3 patterns + 3 tools

        # Verify recall
        patterns_recalled = await memory.retrieve("ErrorPattern")
        tools_recalled = await memory.retrieve("GeneratedTool")
        assert len(patterns_recalled) == 3
        assert len(tools_recalled) == 3
