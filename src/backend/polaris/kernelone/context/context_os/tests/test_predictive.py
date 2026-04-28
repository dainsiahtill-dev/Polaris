"""Tests for Predictive Compression (ContextOS 3.0 P2)."""

import pytest

from polaris.kernelone.context.context_os.predictive import (
    PHASE_CONTENT_PATTERNS,
    PHASE_TOOL_PATTERNS,
    PredictionResult,
    PredictionStrategy,
    PredictiveCompressor,
)


class TestPredictionStrategy:
    """Test PredictionStrategy enum."""

    def test_enum_values(self) -> None:
        assert PredictionStrategy.TASK_PATTERN.value == "task_pattern"
        assert PredictionStrategy.PHASE_TRANSITION.value == "phase_transition"
        assert PredictionStrategy.FORWARD_REFERENCE.value == "forward_reference"
        assert PredictionStrategy.HISTORICAL.value == "historical"


class TestPredictionResult:
    """Test PredictionResult dataclass."""

    def test_create_result(self) -> None:
        result = PredictionResult(
            strategy=PredictionStrategy.PHASE_TRANSITION,
            confidence=0.7,
            predicted_content_types=("code_snippet",),
            predicted_tools=("read_file",),
            reasoning="Phase-based prediction",
        )
        assert result.strategy == PredictionStrategy.PHASE_TRANSITION
        assert result.confidence == 0.7

    def test_to_dict(self) -> None:
        result = PredictionResult(
            strategy=PredictionStrategy.PHASE_TRANSITION,
            confidence=0.7,
            predicted_content_types=("code_snippet",),
            predicted_tools=("read_file",),
            reasoning="Phase-based prediction",
        )
        d = result.to_dict()
        assert d["strategy"] == "phase_transition"
        assert d["confidence"] == 0.7


class TestPredictiveCompressor:
    """Test PredictiveCompressor class."""

    def test_create_compressor(self) -> None:
        compressor = PredictiveCompressor()
        assert len(PHASE_CONTENT_PATTERNS) > 0

    def test_predict_implementation(self) -> None:
        compressor = PredictiveCompressor()
        result = compressor.predict(current_phase="implementation")
        assert result.confidence > 0
        assert len(result.predicted_content_types) > 0

    def test_predict_debugging(self) -> None:
        compressor = PredictiveCompressor()
        result = compressor.predict(current_phase="debugging")
        assert result.confidence > 0
        assert "error_log" in result.predicted_content_types

    def test_predict_with_forward_references(self) -> None:
        compressor = PredictiveCompressor()

        # Create mock events with forward references
        event = type("MockEvent", (), {
            "content": "Next I'll check the test results",
            "kind": "assistant_turn",
            "metadata": {},
        })()

        result = compressor.predict(
            current_phase="implementation",
            recent_events=(event,),
        )
        assert result.confidence > 0

    def test_predict_phase_content_patterns(self) -> None:
        compressor = PredictiveCompressor()

        # Check all phases have patterns
        for phase in ("intake", "planning", "exploration", "implementation", "verification", "debugging", "review"):
            result = compressor.predict(current_phase=phase)
            assert len(result.predicted_content_types) > 0

    def test_predict_phase_tool_patterns(self) -> None:
        compressor = PredictiveCompressor()

        # Check all phases have tool patterns
        for phase in ("intake", "planning", "exploration", "implementation", "verification", "debugging", "review"):
            result = compressor.predict(current_phase=phase)
            assert len(result.predicted_tools) > 0
