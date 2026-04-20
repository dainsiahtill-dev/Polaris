"""Unit tests for Execution Layer."""

from __future__ import annotations

import pytest
from polaris.kernelone.cognitive.execution.acting_handler import ActingPhaseHandler
from polaris.kernelone.cognitive.execution.cautious_policy import CautiousExecutionPolicy, ExecutionPath
from polaris.kernelone.cognitive.execution.rollback_manager import RollbackManager
from polaris.kernelone.cognitive.execution.thinking_engine import ThinkingPhaseEngine
from polaris.kernelone.cognitive.perception.models import IntentGraph, IntentNode
from polaris.kernelone.cognitive.types import ClarityLevel, RiskLevel


@pytest.fixture
def policy():
    return CautiousExecutionPolicy()


@pytest.fixture
def thinking_engine():
    return ThinkingPhaseEngine()


@pytest.fixture
def acting_handler():
    return ActingPhaseHandler()


@pytest.fixture
def rollback():
    return RollbackManager()


@pytest.fixture
def intent_graph_read():
    return IntentGraph(
        graph_id="test_read",
        nodes=(
            IntentNode(
                node_id="n1",
                intent_type="read_file",
                content="Read the file",
                confidence=0.9,
                source_event_id="test",
            ),
        ),
        edges=(),
        chains=(),
        session_id="test",
        created_at="2026-04-09",
        updated_at="2026-04-09",
    )


@pytest.fixture
def intent_graph_delete():
    return IntentGraph(
        graph_id="test_delete",
        nodes=(
            IntentNode(
                node_id="n1",
                intent_type="delete_file",
                content="Delete system config",
                confidence=0.9,
                source_event_id="test",
            ),
        ),
        edges=(),
        chains=(),
        session_id="test",
        created_at="2026-04-09",
        updated_at="2026-04-09",
    )


@pytest.mark.asyncio
async def test_l0_readonly_bypass(policy, intent_graph_read):
    result = await policy.evaluate(intent_graph_read, None, None)
    assert result.path == ExecutionPath.BYPASS
    assert result.risk_level == RiskLevel.L0_READONLY


@pytest.mark.asyncio
async def test_l4_delete_requires_confirmation(policy, intent_graph_delete):
    result = await policy.evaluate(intent_graph_delete, None, None)
    assert result.path == ExecutionPath.FULL_PIPE
    assert result.requires_user_confirmation
    assert result.requires_rollback_plan


def test_clarity_level_enum():
    assert ClarityLevel.FUZZY == 1
    assert ClarityLevel.TENDENCY == 2
    assert ClarityLevel.CERTAIN == 3
    assert ClarityLevel.ACTION_ORIENTED == 4
    assert ClarityLevel.FULL_TRANSPARENT == 5


@pytest.mark.asyncio
async def test_thinking_phase_produces_output(thinking_engine, intent_graph_read):
    from polaris.kernelone.cognitive.types import ExecutionPath, ExecutionRecommendation, RiskLevel

    rec = ExecutionRecommendation(
        path=ExecutionPath.THINKING,
        skip_cognitive_pipe=False,
        confidence=0.7,
        risk_level=RiskLevel.L2_MODIFY,
    )

    output = await thinking_engine.run_thinking_phase(intent_graph_read, rec, None, None)
    assert output.confidence == 0.7
    assert ClarityLevel.CERTAIN <= output.clarity_level <= ClarityLevel.FULL_TRANSPARENT


@pytest.mark.asyncio
async def test_risk_level_enum():
    assert RiskLevel.L0_READONLY == 0
    assert RiskLevel.L1_CREATE == 1
    assert RiskLevel.L2_MODIFY == 2
    assert RiskLevel.L3_DELETE == 3
    assert RiskLevel.L4_IRREVERSIBLE == 4
