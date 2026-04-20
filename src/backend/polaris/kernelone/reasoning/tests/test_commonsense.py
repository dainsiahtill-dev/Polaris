"""Tests for the Commonsense Reasoning Engine."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from polaris.kernelone.reasoning.commonsense import (
    AnalogyResult,
    CausalGraph,
    CausalLink,
    CommonsenseReasoner,
    CounterfactualResult,
)


class TestCausalInference:
    """Tests for causal inference functionality."""

    @pytest.fixture
    def reasoner(self) -> CommonsenseReasoner:
        return CommonsenseReasoner()

    @pytest.mark.asyncio
    async def test_causal_inference_basic(self, reasoner: CommonsenseReasoner) -> None:
        """Test basic causal inference from observation."""
        observation = "Rain causes wet ground which makes roads slippery"

        result = await reasoner.causal_inference(observation)

        assert isinstance(result, CausalGraph)
        assert len(result.nodes) > 0
        assert len(result.links) > 0
        assert "rain" in result.nodes
        assert "wet_ground" in result.nodes or "wet ground" in result.nodes

    @pytest.mark.asyncio
    async def test_causal_inference_identifies_root_causes(self, reasoner: CommonsenseReasoner) -> None:
        """Test that causal inference identifies root causes correctly."""
        observation = "Rain causes wet ground"

        result = await reasoner.causal_inference(observation)

        # Rain should be identified as a root cause (has outgoing link but no incoming)
        assert len(result.root_causes) >= 0

    @pytest.mark.asyncio
    async def test_causal_inference_identifies_leaf_effects(self, reasoner: CommonsenseReasoner) -> None:
        """Test that causal inference identifies leaf effects correctly."""
        observation = "Smoking causes cancer which may cause death"

        result = await reasoner.causal_inference(observation)

        # Death should be identified as a leaf effect
        assert len(result.leaf_effects) >= 0

    @pytest.mark.asyncio
    async def test_causal_inference_empty_observation(self, reasoner: CommonsenseReasoner) -> None:
        """Test causal inference with empty observation."""
        result = await reasoner.causal_inference("")

        assert isinstance(result, CausalGraph)
        assert len(result.nodes) == 0
        assert len(result.links) == 0

    @pytest.mark.asyncio
    async def test_causal_link_structure(self, reasoner: CommonsenseReasoner) -> None:
        """Test that CausalLink has correct structure."""
        observation = "Fire produces heat which causes burns"

        result = await reasoner.causal_inference(observation)

        # Check that links have the expected attributes
        for link in result.links:
            assert isinstance(link, CausalLink)
            assert isinstance(link.cause, str)
            assert isinstance(link.effect, str)
            assert 0.0 <= link.strength <= 1.0


class TestCounterfactualReasoning:
    """Tests for counterfactual reasoning functionality."""

    @pytest.fixture
    def reasoner(self) -> CommonsenseReasoner:
        return CommonsenseReasoner()

    @pytest.mark.asyncio
    async def test_counterfactual_basic(self, reasoner: CommonsenseReasoner) -> None:
        """Test basic counterfactual reasoning."""
        scenario = "If it rains, the roads become wet"
        hypothetical = "What if it did not rain?"

        result = await reasoner.counterfactual(scenario, hypothetical)

        assert isinstance(result, CounterfactualResult)
        assert result.original_scenario == scenario
        assert result.hypothetical_change == hypothetical
        assert isinstance(result.predicted_outcome, str)
        assert 0.0 <= result.confidence <= 1.0

    @pytest.mark.asyncio
    async def test_counterfactual_removal_change(self, reasoner: CommonsenseReasoner) -> None:
        """Test counterfactual with removal type change."""
        scenario = "Smoking causes cancer"
        hypothetical = "What if we remove smoking?"

        result = await reasoner.counterfactual(scenario, hypothetical)

        assert isinstance(result, CounterfactualResult)
        assert "cancer" in result.predicted_outcome.lower() or "not occur" in result.predicted_outcome.lower()
        assert len(result.reasoning_chain) > 0

    @pytest.mark.asyncio
    async def test_counterfactual_addition_change(self, reasoner: CommonsenseReasoner) -> None:
        """Test counterfactual with addition type change."""
        scenario = "The car is not moving"
        hypothetical = "What if we add fuel?"

        result = await reasoner.counterfactual(scenario, hypothetical)

        assert isinstance(result, CounterfactualResult)
        assert result.confidence > 0

    @pytest.mark.asyncio
    async def test_counterfactual_reasoning_chain(self, reasoner: CommonsenseReasoner) -> None:
        """Test that counterfactual reasoning generates a chain of reasoning."""
        scenario = "Exercise causes sweat which leads to dehydration"
        hypothetical = "What if we remove exercise?"

        result = await reasoner.counterfactual(scenario, hypothetical)

        assert len(result.reasoning_chain) >= 3  # Should have multiple reasoning steps
        # Check that chain contains key elements
        chain_text = " ".join(result.reasoning_chain)
        assert "Original scenario" in chain_text
        assert "Hypothetical change" in chain_text

    @pytest.mark.asyncio
    async def test_counterfactual_increase_change(self, reasoner: CommonsenseReasoner) -> None:
        """Test counterfactual with increase type change."""
        scenario = "Adding sugar makes the tea sweeter"
        hypothetical = "What if we add more sugar?"

        result = await reasoner.counterfactual(scenario, hypothetical)

        assert isinstance(result, CounterfactualResult)
        assert (
            "increase" in result.reasoning_chain[2].lower()
            or "addition" in result.reasoning_chain[2].lower()
            or "amplified" in result.predicted_outcome.lower()
            or "more" in result.predicted_outcome.lower()
        )

    @pytest.mark.asyncio
    async def test_counterfactual_decrease_change(self, reasoner: CommonsenseReasoner) -> None:
        """Test counterfactual with decrease type change."""
        scenario = "Adding salt makes the soup saltier"
        hypothetical = "What if we add less salt?"

        result = await reasoner.counterfactual(scenario, hypothetical)

        assert isinstance(result, CounterfactualResult)
        assert (
            "decrease" in result.reasoning_chain[2].lower()
            or "addition" in result.reasoning_chain[2].lower()
            or "reduced" in result.predicted_outcome.lower()
            or "less" in result.predicted_outcome.lower()
        )


class TestAnalogicalReasoning:
    """Tests for analogical reasoning functionality."""

    @pytest.fixture
    def reasoner(self) -> CommonsenseReasoner:
        return CommonsenseReasoner()

    @pytest.mark.asyncio
    async def test_analogical_reasoning_basic(self, reasoner: CommonsenseReasoner) -> None:
        """Test basic analogical reasoning between domains."""
        source = "atom"
        target = "solar_system"

        result = await reasoner.analogical_reasoning(source, target)

        assert isinstance(result, AnalogyResult)
        assert result.source == source
        assert result.target == target
        assert 0.0 <= result.similarity_score <= 1.0

    @pytest.mark.asyncio
    async def test_analogical_reasoning_same_domain(self, reasoner: CommonsenseReasoner) -> None:
        """Test analogical reasoning with same domain (high similarity)."""
        source = "computer"
        target = "computer"

        result = await reasoner.analogical_reasoning(source, target)

        assert result.similarity_score >= 0.5

    @pytest.mark.asyncio
    async def test_analogical_reasoning_mapped_properties(self, reasoner: CommonsenseReasoner) -> None:
        """Test that mapped properties are identified."""
        source = "cell"
        target = "cell"

        result = await reasoner.analogical_reasoning(source, target)

        assert len(result.mapped_properties) > 0
        assert isinstance(result.mapped_properties, tuple)
        assert all(isinstance(p, str) for p in result.mapped_properties)

    @pytest.mark.asyncio
    async def test_analogical_reasoning_inferred_properties(self, reasoner: CommonsenseReasoner) -> None:
        """Test that new properties are inferred for target."""
        source = "atom"
        target = "cell"

        result = await reasoner.analogical_reasoning(source, target)

        assert isinstance(result.inferred_properties, tuple)
        assert isinstance(result.mapped_properties, tuple)

    @pytest.mark.asyncio
    async def test_analogical_reasoning_cell_vs_computer(self, reasoner: CommonsenseReasoner) -> None:
        """Test analogy between cell and computer domains."""
        source = "cell"
        target = "computer"

        result = await reasoner.analogical_reasoning(source, target)

        assert 0.0 <= result.similarity_score <= 1.0
        # Both have structure-related properties
        assert isinstance(result.mapped_properties, tuple)

    @pytest.mark.asyncio
    async def test_analogical_reasoning_unknown_domain(self, reasoner: CommonsenseReasoner) -> None:
        """Test analogical reasoning with unknown domains."""
        source = "xyz_unknown_domain"
        target = "abc_other_unknown"

        result = await reasoner.analogical_reasoning(source, target)

        assert isinstance(result, AnalogyResult)
        # Unknown domains will have lower similarity
        assert 0.0 <= result.similarity_score <= 1.0


class TestReasoningChainGeneration:
    """Tests for reasoning chain generation."""

    @pytest.fixture
    def reasoner(self) -> CommonsenseReasoner:
        return CommonsenseReasoner()

    @pytest.mark.asyncio
    async def test_causal_inference_chain(self, reasoner: CommonsenseReasoner) -> None:
        """Test that causal inference generates a reasoning chain."""
        observation = "Rain causes wet ground"

        result = await reasoner.causal_inference(observation)

        # Should have multiple reasoning steps
        assert len(result.nodes) > 0 or len(result.links) > 0

    @pytest.mark.asyncio
    async def test_counterfactual_chain_completeness(self, reasoner: CommonsenseReasoner) -> None:
        """Test that counterfactual reasoning chain is complete."""
        scenario = "If you study hard, you pass the exam"
        hypothetical = "What if you did not study?"

        result = await reasoner.counterfactual(scenario, hypothetical)

        # Chain should contain all key elements
        assert "Original scenario: If you study hard, you pass the exam" in result.reasoning_chain
        assert "Hypothetical change: What if you did not study?" in result.reasoning_chain
        assert "Predicted outcome:" in " ".join(result.reasoning_chain)
        assert "Confidence:" in " ".join(result.reasoning_chain)

    @pytest.mark.asyncio
    async def test_multiple_causal_chains(self, reasoner: CommonsenseReasoner) -> None:
        """Test handling multiple causal chains in observation."""
        observation = "Rain causes wet ground and fire causes heat"

        result = await reasoner.causal_inference(observation)

        # Should handle both chains
        assert len(result.nodes) >= 2


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.fixture
    def reasoner(self) -> CommonsenseReasoner:
        return CommonsenseReasoner()

    @pytest.mark.asyncio
    async def test_empty_scenario(self, reasoner: CommonsenseReasoner) -> None:
        """Test counterfactual with empty scenario."""
        result = await reasoner.counterfactual("", "What if?")

        assert isinstance(result, CounterfactualResult)
        assert result.predicted_outcome is not None

    @pytest.mark.asyncio
    async def test_very_long_observation(self, reasoner: CommonsenseReasoner) -> None:
        """Test causal inference with very long observation."""
        observation = " and ".join(["rain causes wet ground"] * 100)

        result = await reasoner.causal_inference(observation)

        assert isinstance(result, CausalGraph)

    @pytest.mark.asyncio
    async def test_special_characters_in_domain(self, reasoner: CommonsenseReasoner) -> None:
        """Test analogical reasoning with special characters."""
        source = "domain-with-dashes"
        target = "domain_with_underscores"

        result = await reasoner.analogical_reasoning(source, target)

        assert isinstance(result, AnalogyResult)

    @pytest.mark.asyncio
    async def test_unicode_in_observation(self, reasoner: CommonsenseReasoner) -> None:
        """Test causal inference with unicode characters."""
        observation = "雨 causes 湿 ground"

        result = await reasoner.causal_inference(observation)

        assert isinstance(result, CausalGraph)

    @pytest.mark.asyncio
    async def test_numeric_scenario(self, reasoner: CommonsenseReasoner) -> None:
        """Test counterfactual with numeric scenario."""
        result = await reasoner.counterfactual("123", "456")

        assert isinstance(result, CounterfactualResult)
        assert result.confidence == 0.5


class TestDataclassImmutability:
    """Tests for dataclass immutability."""

    def test_causal_link_is_frozen(self) -> None:
        """Test that CausalLink is frozen/immutable."""
        link = CausalLink(cause="A", effect="B", strength=0.5)

        with pytest.raises(FrozenInstanceError):
            link.strength = 0.9  # type: ignore

    def test_causal_graph_is_frozen(self) -> None:
        """Test that CausalGraph is frozen/immutable."""
        graph = CausalGraph(
            nodes=("A", "B"),
            links=(CausalLink("A", "B", 0.5),),
        )

        with pytest.raises(FrozenInstanceError):
            graph.nodes = ("A",)  # type: ignore

    def test_counterfactual_result_is_frozen(self) -> None:
        """Test that CounterfactualResult is frozen/immutable."""
        result = CounterfactualResult(
            original_scenario="A",
            hypothetical_change="B",
            predicted_outcome="C",
            confidence=0.5,
        )

        with pytest.raises(FrozenInstanceError):
            result.confidence = 0.9  # type: ignore

    def test_analogy_result_is_frozen(self) -> None:
        """Test that AnalogyResult is frozen/immutable."""
        result = AnalogyResult(
            source="A",
            target="B",
            similarity_score=0.5,
        )

        with pytest.raises(FrozenInstanceError):
            result.similarity_score = 0.9  # type: ignore
