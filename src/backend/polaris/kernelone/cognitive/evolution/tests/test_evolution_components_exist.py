"""Test that EvolutionEngine components can be instantiated."""

from __future__ import annotations

from polaris.kernelone.cognitive.evolution.bias_defense import (
    BiasDefenseEngine,
    BiasDetectionResult,
)
from polaris.kernelone.cognitive.evolution.engine import EvolutionEngine
from polaris.kernelone.cognitive.evolution.knowledge_precipitation import (
    KnowledgePrecipitation,
    PrecipitatedKnowledge,
)
from polaris.kernelone.cognitive.evolution.store import EvolutionStore


class TestBiasDefenseEngineSkeleton:
    """Test BiasDefenseEngine skeleton instantiation."""

    def test_bias_defense_engine_instantiable(self) -> None:
        """Verify BiasDefenseEngine can be instantiated."""
        engine = BiasDefenseEngine()
        assert engine is not None

    def test_detect_bias_returns_bias_detection_result(self) -> None:
        """Verify detect_bias returns expected type."""
        engine = BiasDefenseEngine()
        result = engine.detect_bias("test reasoning content")
        assert isinstance(result, BiasDetectionResult)
        assert result.biases_detected == ()
        assert result.mitigation_suggestions == ()
        assert result.confidence == 0.0

    def test_apply_mitigation_returns_string(self) -> None:
        """Verify apply_mitigation returns expected type."""
        engine = BiasDefenseEngine()
        result = engine.apply_mitigation("test content", ("bias1",))
        assert isinstance(result, str)
        # When biases are detected, mitigation note is added
        assert "test content" in result
        assert "[Bias Awareness]" in result

    def test_apply_mitigation_no_biases(self) -> None:
        """Verify apply_mitigation with no biases returns original content."""
        engine = BiasDefenseEngine()
        result = engine.apply_mitigation("test content", ())
        assert isinstance(result, str)
        assert result == "test content"


class TestKnowledgePrecipitationSkeleton:
    """Test KnowledgePrecipitation skeleton instantiation."""

    def test_knowledge_precipitation_instantiable(self) -> None:
        """Verify KnowledgePrecipitation can be instantiated."""
        precip = KnowledgePrecipitation()
        assert precip is not None

    def test_precipitate_returns_precipitated_knowledge(self) -> None:
        """Verify precipitate returns expected type."""
        precip = KnowledgePrecipitation()
        result = precip.precipitate({"task": "test"})
        assert isinstance(result, PrecipitatedKnowledge)
        assert result.rules_learned == ()
        assert result.patterns_identified == ()
        assert result.boundaries_updated == ()
        assert result.knowledge_gaps == ()

    def test_get_relevant_knowledge_returns_precipitated_knowledge(self) -> None:
        """Verify get_relevant_knowledge returns expected type."""
        precip = KnowledgePrecipitation()
        result = precip.get_relevant_knowledge("test_intent")
        assert isinstance(result, PrecipitatedKnowledge)
        assert result.rules_learned == ()
        assert result.patterns_identified == ()
        assert result.boundaries_updated == ()
        assert result.knowledge_gaps == ()

    def test_precipitate_with_success_pattern(self) -> None:
        """Verify precipitate uses success patterns for detection."""
        precip = KnowledgePrecipitation()
        # modify_file with success output containing "file modified successfully"
        result = precip.precipitate(
            {
                "intent_type": "modify_file",
                "success": False,  # Explicit False
                "output": "file modified successfully",
                "error_message": "",
            }
        )
        # Should detect success pattern and override explicit False
        assert len(result.rules_learned) > 0
        assert any("CONFIRMED" in r for r in result.rules_learned)

    def test_precipitate_with_failure_pattern(self) -> None:
        """Verify precipitate uses failure patterns for detection."""
        precip = KnowledgePrecipitation()
        # modify_file with failure in error_message
        result = precip.precipitate(
            {
                "intent_type": "modify_file",
                "success": False,
                "output": "",
                "error_message": "syntax error in file",
            }
        )
        # Should detect failure pattern
        assert len(result.rules_learned) > 0
        assert any("VIOLATED" in r for r in result.rules_learned)


class TestEvolutionEngineWithComponents:
    """Test EvolutionEngine with new components."""

    def test_evolution_engine_instantiates_bias_defense(self, tmp_path: object) -> None:
        """Verify EvolutionEngine can be created with bias defense component."""
        store = EvolutionStore(workspace=str(tmp_path))
        engine = EvolutionEngine(store=store)
        assert engine is not None
        assert hasattr(engine, "_bias_defense")
        assert isinstance(engine._bias_defense, BiasDefenseEngine)

    def test_evolution_engine_instantiates_knowledge_precipitation(self, tmp_path: object) -> None:
        """Verify EvolutionEngine can be created with knowledge precipitation component."""
        store = EvolutionStore(workspace=str(tmp_path))
        engine = EvolutionEngine(store=store)
        assert engine is not None
        assert hasattr(engine, "_knowledge_precipitation")
        assert isinstance(engine._knowledge_precipitation, KnowledgePrecipitation)
