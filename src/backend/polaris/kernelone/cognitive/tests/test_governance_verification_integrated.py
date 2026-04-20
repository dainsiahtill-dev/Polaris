"""Integration tests for CognitiveGovernance verification calls in orchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.kernelone.cognitive.governance import CognitiveGovernance, VCResult
from polaris.kernelone.cognitive.orchestrator import CognitiveOrchestrator


@pytest.fixture
def mock_governance():
    """Create a mock governance that tracks all verification calls."""
    governance = AsyncMock(spec=CognitiveGovernance)
    # Default: all verifications pass
    governance.verify_post_perception.return_value = VCResult(
        vc_id="VC-Intent-001",
        status="PASS",
        message="Intent clarity acceptable",
    )
    governance.verify_pre_reasoning.return_value = VCResult(
        vc_id="VC-CT-001",
        status="PASS",
        message="Confidence threshold met",
    )
    governance.verify_post_reasoning.return_value = VCResult(
        vc_id="VC-CT-001",
        status="PASS",
        message="Reasoning quality acceptable",
    )
    governance.verify_pre_execution.return_value = VCResult(
        vc_id="VC-Cautious-001",
        status="PASS",
        message="Cautious execution verified",
    )
    return governance


@pytest.fixture
def orchestrator_with_mocked_layers(mock_governance):
    """Create orchestrator with governance enabled and mocked layers."""
    # Mock session manager
    mock_session_manager = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.conversation_history = []
    mock_session_manager.get_or_create_session.return_value = mock_ctx
    mock_session_manager.update_session = MagicMock()

    with (
        patch("polaris.kernelone.cognitive.orchestrator.PerceptionLayer") as mock_perception,
        patch("polaris.kernelone.cognitive.orchestrator.CriticalThinkingEngine") as mock_reasoning,
        patch("polaris.kernelone.cognitive.orchestrator.MetaCognitionEngine") as mock_meta,
        patch("polaris.kernelone.cognitive.orchestrator.CautiousExecutionPolicy") as mock_policy,
        patch("polaris.kernelone.cognitive.orchestrator.ThinkingPhaseEngine"),
        patch("polaris.kernelone.cognitive.orchestrator.ActingPhaseHandler"),
        patch("polaris.kernelone.cognitive.orchestrator.CognitivePipeline") as mock_pipeline,
        patch("polaris.kernelone.cognitive.orchestrator.PersonalityIntegrator") as mock_personality,
        patch("polaris.kernelone.cognitive.orchestrator.get_session_manager", return_value=mock_session_manager),
    ):
        # Setup mock perception
        mock_intent = MagicMock()
        mock_intent.intent_type = "read_file"
        mock_intent.confidence = 0.8
        mock_intent_graph = MagicMock()
        mock_intent_graph.nodes = [mock_intent]
        mock_intent_graph.chains = []
        mock_uncertainty = MagicMock()
        mock_uncertainty.uncertainty_score = 0.3
        mock_perception.return_value.process = AsyncMock(return_value=(mock_intent_graph, mock_uncertainty))

        # Setup mock reasoning
        mock_reasoning_chain = MagicMock()
        mock_reasoning_chain.confidence_level = "high"
        mock_reasoning_chain.should_proceed = True
        mock_reasoning_chain.blockers = ()
        mock_reasoning_chain.six_questions.assumptions = []
        mock_reasoning.return_value.analyze = AsyncMock(return_value=mock_reasoning_chain)

        # Setup mock meta
        mock_meta.return_value.audit_thought_process = AsyncMock(return_value=MagicMock())
        mock_meta.return_value.reflect = AsyncMock(return_value=MagicMock())

        # Setup mock policy
        mock_recommendation = MagicMock()
        mock_recommendation.path.value = "bypass"
        mock_recommendation.requires_user_confirmation = False
        mock_policy.return_value.evaluate = AsyncMock(return_value=mock_recommendation)

        # Setup mock pipeline
        mock_pipeline_result = MagicMock()
        mock_pipeline_result.path_taken.value = "bypass"
        mock_pipeline_result.blocked = False
        mock_pipeline_result.thinking_output = None
        mock_pipeline_result.acting_output = None
        mock_pipeline.return_value.execute = AsyncMock(return_value=mock_pipeline_result)

        # Setup mock personality
        mock_personality.return_value.apply_posture_to_response = MagicMock(
            side_effect=lambda response, **kwargs: response
        )

        orchestrator = CognitiveOrchestrator(
            enable_governance=True,
            enable_personality=False,
        )
        orchestrator._governance = mock_governance

        yield orchestrator, mock_governance


@pytest.mark.asyncio
async def test_governance_verification_post_perception_called(orchestrator_with_mocked_layers):
    """Test that verify_post_perception is called after perception."""
    orchestrator, mock_governance = orchestrator_with_mocked_layers

    # Make governance verify_post_perception fail to block early
    mock_governance.verify_post_perception.return_value = VCResult(
        vc_id="VC-Intent-001",
        status="FAIL",
        message="Intent unclear",
    )

    result = await orchestrator.process(
        message="test message",
        session_id="test_session",
        role_id="director",
    )

    # Should have been called
    mock_governance.verify_post_perception.assert_called_once()
    assert result.blocked is True
    assert "Governance blocked" in result.content


@pytest.mark.asyncio
async def test_governance_verification_pre_reasoning_called(orchestrator_with_mocked_layers):
    """Test that verify_pre_reasoning is called before reasoning."""
    orchestrator, mock_governance = orchestrator_with_mocked_layers

    # Make governance verify_pre_reasoning fail
    mock_governance.verify_pre_reasoning.return_value = VCResult(
        vc_id="VC-CT-001",
        status="FAIL",
        message="Confidence too low",
    )

    result = await orchestrator.process(
        message="test message",
        session_id="test_session",
        role_id="director",
    )

    # Should have been called
    mock_governance.verify_pre_reasoning.assert_called_once()
    assert result.blocked is True
    assert "Governance blocked" in result.content


@pytest.mark.asyncio
async def test_governance_verification_post_reasoning_called(orchestrator_with_mocked_layers):
    """Test that verify_post_reasoning is called after reasoning."""
    orchestrator, mock_governance = orchestrator_with_mocked_layers

    # Make governance verify_post_reasoning fail
    mock_governance.verify_post_reasoning.return_value = VCResult(
        vc_id="VC-Cautious-001",
        status="FAIL",
        message="Critical blockers found",
    )

    result = await orchestrator.process(
        message="test message",
        session_id="test_session",
        role_id="director",
    )

    # Should have been called
    mock_governance.verify_post_reasoning.assert_called_once()
    assert result.blocked is True
    assert "Governance blocked" in result.content


@pytest.mark.asyncio
async def test_governance_verification_pre_execution_called(orchestrator_with_mocked_layers):
    """Test that verify_pre_execution is called before execution."""
    orchestrator, mock_governance = orchestrator_with_mocked_layers

    # Make governance verify_pre_execution fail
    mock_governance.verify_pre_execution.return_value = VCResult(
        vc_id="VC-Cautious-001",
        status="FAIL",
        message="L3+ action without user confirmation",
    )

    result = await orchestrator.process(
        message="test message",
        session_id="test_session",
        role_id="director",
    )

    # Should have been called
    mock_governance.verify_pre_execution.assert_called_once()
    assert result.blocked is True
    assert "Governance blocked" in result.content


@pytest.mark.asyncio
async def test_governance_verification_all_pass(orchestrator_with_mocked_layers):
    """Test that process completes when all verifications pass."""
    orchestrator, mock_governance = orchestrator_with_mocked_layers

    result = await orchestrator.process(
        message="test message",
        session_id="test_session",
        role_id="director",
    )

    # All governance methods should have been called
    mock_governance.verify_post_perception.assert_called_once()
    mock_governance.verify_pre_reasoning.assert_called_once()
    mock_governance.verify_post_reasoning.assert_called_once()
    mock_governance.verify_pre_execution.assert_called_once()

    # Process should complete (may or may not be blocked depending on pipeline result)
    assert result is not None


@pytest.mark.asyncio
async def test_governance_disabled_does_not_call_verification():
    """Test that governance verification is skipped when disabled."""
    # Mock session manager
    mock_session_manager = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.conversation_history = []
    mock_session_manager.get_or_create_session.return_value = mock_ctx
    mock_session_manager.update_session = MagicMock()

    with (
        patch("polaris.kernelone.cognitive.orchestrator.PerceptionLayer") as mock_perception,
        patch("polaris.kernelone.cognitive.orchestrator.CriticalThinkingEngine") as mock_reasoning,
        patch("polaris.kernelone.cognitive.orchestrator.MetaCognitionEngine") as mock_meta,
        patch("polaris.kernelone.cognitive.orchestrator.CautiousExecutionPolicy") as mock_policy,
        patch("polaris.kernelone.cognitive.orchestrator.ThinkingPhaseEngine"),
        patch("polaris.kernelone.cognitive.orchestrator.ActingPhaseHandler"),
        patch("polaris.kernelone.cognitive.orchestrator.CognitivePipeline") as mock_pipeline,
        patch("polaris.kernelone.cognitive.orchestrator.PersonalityIntegrator") as mock_personality,
        patch("polaris.kernelone.cognitive.orchestrator.get_session_manager", return_value=mock_session_manager),
    ):
        # Setup mocks
        mock_intent = MagicMock()
        mock_intent.intent_type = "read_file"
        mock_intent.confidence = 0.8
        mock_intent_graph = MagicMock()
        mock_intent_graph.nodes = [mock_intent]
        mock_intent_graph.chains = []
        mock_uncertainty = MagicMock()
        mock_uncertainty.uncertainty_score = 0.3
        mock_perception.return_value.process = AsyncMock(return_value=(mock_intent_graph, mock_uncertainty))

        mock_reasoning_chain = MagicMock()
        mock_reasoning_chain.confidence_level = "high"
        mock_reasoning_chain.should_proceed = True
        mock_reasoning_chain.blockers = ()
        mock_reasoning_chain.six_questions.assumptions = []
        mock_reasoning.return_value.analyze = AsyncMock(return_value=mock_reasoning_chain)
        mock_meta.return_value.audit_thought_process = AsyncMock(return_value=MagicMock())
        mock_meta.return_value.reflect = AsyncMock(return_value=MagicMock())

        mock_recommendation = MagicMock()
        mock_recommendation.path.value = "bypass"
        mock_recommendation.requires_user_confirmation = False
        mock_policy.return_value.evaluate = AsyncMock(return_value=mock_recommendation)

        mock_pipeline_result = MagicMock()
        mock_pipeline_result.path_taken.value = "bypass"
        mock_pipeline_result.blocked = False
        mock_pipeline_result.thinking_output = None
        mock_pipeline_result.acting_output = None
        mock_pipeline.return_value.execute = AsyncMock(return_value=mock_pipeline_result)

        mock_personality.return_value.apply_posture_to_response = MagicMock(
            side_effect=lambda response, **kwargs: response
        )

        orchestrator = CognitiveOrchestrator(
            enable_governance=False,  # Disabled
            enable_personality=False,
        )

        result = await orchestrator.process(
            message="test message",
            session_id="test_session",
            role_id="director",
        )

        # Governance should be None
        assert orchestrator._governance is None
        assert result is not None


@pytest.mark.asyncio
async def test_governance_disabled_by_default():
    """Test that governance is disabled by default for backward compatibility."""
    with (
        patch("polaris.kernelone.cognitive.orchestrator.PerceptionLayer"),
        patch("polaris.kernelone.cognitive.orchestrator.CriticalThinkingEngine"),
        patch("polaris.kernelone.cognitive.orchestrator.MetaCognitionEngine"),
        patch("polaris.kernelone.cognitive.orchestrator.CautiousExecutionPolicy"),
        patch("polaris.kernelone.cognitive.orchestrator.ThinkingPhaseEngine"),
        patch("polaris.kernelone.cognitive.orchestrator.ActingPhaseHandler"),
        patch("polaris.kernelone.cognitive.orchestrator.CognitivePipeline"),
        patch("polaris.kernelone.cognitive.orchestrator.PersonalityIntegrator"),
        patch("polaris.kernelone.cognitive.orchestrator.get_session_manager"),
    ):
        orchestrator = CognitiveOrchestrator(enable_personality=False)

        # Governance is disabled by default for backward compatibility
        # Use enable_governance=True to opt-in
        assert orchestrator._enable_governance is False
        assert orchestrator._governance is None
