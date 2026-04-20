"""Unit tests for Evolution Layer."""

from __future__ import annotations

import pytest
from polaris.kernelone.cognitive.evolution.engine import EvolutionEngine
from polaris.kernelone.cognitive.evolution.models import TriggerType
from polaris.kernelone.cognitive.evolution.store import EvolutionStore


@pytest.fixture
def store(tmp_path):
    return EvolutionStore(str(tmp_path))


@pytest.fixture
def engine(store):
    return EvolutionEngine(store)


def test_trigger_type_enum():
    assert TriggerType.USER_CORRECTION.value == "user_correction"
    assert TriggerType.PREDICTION_MISMATCH.value == "prediction_mismatch"
    assert TriggerType.SELF_REFLECTION.value == "self_reflection"


@pytest.mark.asyncio
async def test_record_evolution(engine):
    record = await engine.process_trigger(
        trigger_type=TriggerType.USER_CORRECTION,
        content="The previous approach was incorrect",
        context="user_feedback",
    )
    assert record is not None
    assert record.record_id.startswith("evo_")
    assert record.trigger_type == TriggerType.USER_CORRECTION


@pytest.mark.asyncio
async def test_bias_metrics_update(engine):
    initial_metrics = engine.get_bias_metrics()
    assert initial_metrics.confirmation_bias_exposure == 0.0

    await engine.process_trigger(
        trigger_type=TriggerType.BIAS_DETECTED,
        content="Detected confirmation bias",
    )

    updated_metrics = engine.get_bias_metrics()
    assert updated_metrics.confirmation_bias_exposure > 0.0


@pytest.mark.asyncio
async def test_detect_repeated_mistakes(engine):
    # Record multiple mistakes of the same type
    for _ in range(3):
        await engine.process_trigger(
            trigger_type=TriggerType.PREDICTION_MISMATCH,
            content="Prediction failed",
        )

    repeated = await engine.detect_repeated_mistakes(limit=10)
    assert len(repeated) >= 1
    assert "Recurring prediction_mismatch" in repeated[0]
