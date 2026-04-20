"""Unit tests for Reasoning Layer."""

from __future__ import annotations

import pytest
from polaris.kernelone.cognitive.reasoning.engine import CriticalThinkingEngine
from polaris.kernelone.cognitive.reasoning.meta_cognition import MetaCognitionEngine
from polaris.kernelone.cognitive.reasoning.models import Assumption


@pytest.fixture
def cte():
    return CriticalThinkingEngine()


@pytest.fixture
def meta():
    return MetaCognitionEngine()


@pytest.mark.asyncio
async def test_assume_minimum_assumptions(cte):
    result = await cte.analyze(
        conclusion="The code should work",
        intent_chain=None,
    )
    # Should have at least one assumption
    assert len(result.six_questions.assumptions) >= 1


@pytest.mark.asyncio
async def test_confidence_classification(cte):
    result = await cte.analyze(
        conclusion="This is definitely correct because I said so",
        intent_chain=None,
    )
    assert result.confidence_level in ("high", "medium", "low", "unknown")


@pytest.mark.asyncio
async def test_should_proceed_when_high_probability(cte):
    result = await cte.analyze(
        conclusion="The solution is correct and complete",
        intent_chain=None,
    )
    # High confidence should allow proceeding
    assert result.six_questions.conclusion_probability >= 0.5


@pytest.mark.asyncio
async def test_knowledge_boundary_assessment(meta):
    confidence, gaps = await meta.assess_knowledge_boundary("python")
    assert 0.0 <= confidence <= 1.0
    assert isinstance(gaps, tuple)


@pytest.mark.asyncio
async def test_confidence_calibration(meta):
    record = await meta.calibrate_confidence(0.9, 0.85)
    assert record.calibration_type == "well_calibrated"
    assert abs(record.deviation) < 0.1


@pytest.mark.asyncio
async def test_reflection_produces_output(meta):
    result = await meta.reflect(
        task_result={"success": True, "quality": 0.9},
        intent={"graph_id": "test_123"},
    )
    assert result.task_level is not None
    assert "task_id" in result.task_level


def test_assumption_model():
    assumption = Assumption(
        id="test_1",
        text="Test assumption",
        confidence=0.8,
        conditions_for_failure=("condition1",),
        evidence=("evidence1",),
        is_hidden=False,
    )
    assert assumption.id == "test_1"
    assert assumption.confidence == 0.8
