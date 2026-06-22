"""Unit tests for Execution Layer."""

from __future__ import annotations

import pytest
from polaris.kernelone.cognitive.execution.acting_handler import ActingPhaseHandler
from polaris.kernelone.cognitive.execution.cautious_policy import CautiousExecutionPolicy, ExecutionPath
from polaris.kernelone.cognitive.execution.pipeline import CognitivePipeline
from polaris.kernelone.cognitive.execution.rollback_manager import RollbackManager
from polaris.kernelone.cognitive.execution.thinking_engine import ThinkingPhaseEngine
from polaris.kernelone.cognitive.perception.models import IntentGraph, IntentNode, UncertaintyAssessment
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


def test_acting_handler_only_accepts_explicit_actions(acting_handler):
    assert acting_handler.can_execute_action("read README.md") is True
    assert acting_handler.can_execute_action("[mode:materialize]\n范围: style.css") is False
    assert acting_handler.can_execute_action("实现响应式 CSS 样式与布局") is False


@pytest.mark.asyncio
async def test_pipeline_does_not_execute_materialize_request_as_action(tmp_path):
    pipeline = CognitivePipeline(workspace=str(tmp_path))
    graph = IntentGraph(
        graph_id="materialize_request",
        nodes=(
            IntentNode(
                node_id="n1",
                intent_type="unknown",
                content="[mode:materialize]\n范围: style.css",
                confidence=0.0,
                source_event_id="test",
            ),
        ),
        edges=(),
        chains=(),
        session_id="test",
        created_at="2026-06-19",
        updated_at="2026-06-19",
    )
    uncertainty = UncertaintyAssessment(
        uncertainty_score=0.2,
        confidence_lower=0.0,
        confidence_upper=0.3,
        recommended_action="thinking",
    )

    result = await pipeline.execute(
        "[mode:materialize]\n范围: style.css",
        graph,
        uncertainty,
    )

    assert result.path_taken == ExecutionPath.THINKING
    assert result.thinking_output is not None
    assert result.acting_output is None
    assert result.blocked is False


@pytest.mark.asyncio
async def test_bypass_rejects_materialize_contract_even_when_intent_is_read_file(tmp_path):
    pipeline = CognitivePipeline(workspace=str(tmp_path))
    graph = IntentGraph(
        graph_id="misclassified_materialize_request",
        nodes=(
            IntentNode(
                node_id="n1",
                intent_type="read_file",
                content="[mode:materialize]\n范围: src/main.ts\n目标文件: src/main.ts",
                confidence=0.8,
                source_event_id="test",
            ),
        ),
        edges=(),
        chains=(),
        session_id="test",
        created_at="2026-06-22",
        updated_at="2026-06-22",
    )
    uncertainty = UncertaintyAssessment(
        uncertainty_score=0.1,
        confidence_lower=0.7,
        confidence_upper=0.9,
        recommended_action="bypass",
    )

    result = await pipeline.execute(
        "[mode:materialize]\n范围: src/main.ts\n目标文件: src/main.ts",
        graph,
        uncertainty,
    )

    assert result.path_taken == ExecutionPath.BYPASS
    assert result.acting_output is not None
    assert result.acting_output.actions_taken == ()
    assert result.acting_output.retryable is False
    assert "Failed to parse action" not in result.acting_output.content
    assert result.metadata["action_input_rejected"] is True
    assert result.metadata["tool_call_normalization_stage"] == "cognitive_bypass_guard"


@pytest.mark.asyncio
async def test_risk_level_enum():
    assert RiskLevel.L0_READONLY == 0
    assert RiskLevel.L1_CREATE == 1
    assert RiskLevel.L2_MODIFY == 2
    assert RiskLevel.L3_DELETE == 3
    assert RiskLevel.L4_IRREVERSIBLE == 4
