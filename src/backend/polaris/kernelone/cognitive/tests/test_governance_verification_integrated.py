"""Integration tests for CognitiveGovernance verification calls via the governance gate.

Current architecture: ``CognitiveOrchestrator`` is a thin facade over
``CognitivePipelineCoordinator``. The phase-level VC verification is owned by
``CognitiveGovernanceGate`` (``check_*`` methods), which wraps
``CognitiveGovernance`` (``verify_*`` methods) and short-circuits to
``blocked=False`` when disabled. These tests pin that integration: every
``check_*`` must delegate to the matching ``verify_*`` exactly once when the
gate is enabled, and none may run when it is disabled.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from polaris.kernelone.cognitive.governance import CognitiveGovernance, VCResult
from polaris.kernelone.cognitive.governance_gate import CognitiveGovernanceGate
from polaris.kernelone.cognitive.orchestrator import CognitiveOrchestrator

_PASS = VCResult(vc_id="VC-OK", status="PASS", message="acceptable")


@pytest.fixture
def mock_governance() -> AsyncMock:
    """Create a mock governance that tracks all verification calls."""
    governance: AsyncMock = AsyncMock(spec=CognitiveGovernance)
    # Default: all verifications pass
    governance.verify_pre_perception.return_value = _PASS
    governance.verify_post_perception.return_value = _PASS
    governance.verify_pre_reasoning.return_value = _PASS
    governance.verify_post_reasoning.return_value = _PASS
    governance.verify_pre_execution.return_value = _PASS
    return governance


@pytest.fixture
def enabled_gate(mock_governance: AsyncMock) -> CognitiveGovernanceGate:
    """Enabled gate wired to the mock governance under test."""
    gate = CognitiveGovernanceGate(enabled=True)
    gate._governance = mock_governance
    return gate


@pytest.mark.asyncio
async def test_governance_verification_pre_perception_called(
    enabled_gate: CognitiveGovernanceGate,
    mock_governance: AsyncMock,
) -> None:
    """check_pre_perception delegates to verify_pre_perception and blocks on FAIL."""
    mock_governance.verify_pre_perception.return_value = VCResult(
        vc_id="VC-Intent-000",
        status="FAIL",
        message="Unsafe message content",
    )

    result = await enabled_gate.check_pre_perception("test message")

    mock_governance.verify_pre_perception.assert_called_once_with("test message")
    assert result.blocked is True
    assert "Governance blocked" in (result.content or "")


@pytest.mark.asyncio
async def test_governance_verification_post_perception_called(
    enabled_gate: CognitiveGovernanceGate,
    mock_governance: AsyncMock,
) -> None:
    """check_post_perception delegates to verify_post_perception and blocks on FAIL."""
    mock_governance.verify_post_perception.return_value = VCResult(
        vc_id="VC-Intent-001",
        status="FAIL",
        message="Intent unclear",
    )

    result = await enabled_gate.check_post_perception("read_file", 0.8)

    mock_governance.verify_post_perception.assert_called_once_with("read_file", 0.8)
    assert result.blocked is True
    assert "Governance blocked" in (result.content or "")


@pytest.mark.asyncio
async def test_governance_verification_pre_reasoning_called(
    enabled_gate: CognitiveGovernanceGate,
    mock_governance: AsyncMock,
) -> None:
    """check_pre_reasoning delegates to verify_pre_reasoning and blocks on FAIL."""
    mock_governance.verify_pre_reasoning.return_value = VCResult(
        vc_id="VC-CT-001",
        status="FAIL",
        message="Confidence too low",
    )

    result = await enabled_gate.check_pre_reasoning("read_file", 0.8)

    mock_governance.verify_pre_reasoning.assert_called_once_with("read_file", 0.8)
    assert result.blocked is True
    assert "Governance blocked" in (result.content or "")


@pytest.mark.asyncio
async def test_governance_verification_post_reasoning_called(
    enabled_gate: CognitiveGovernanceGate,
    mock_governance: AsyncMock,
) -> None:
    """check_post_reasoning delegates to verify_post_reasoning and blocks on FAIL."""
    mock_governance.verify_post_reasoning.return_value = VCResult(
        vc_id="VC-CT-002",
        status="FAIL",
        message="Critical blockers found",
    )

    result = await enabled_gate.check_post_reasoning(probability=0.9, severity="high", blockers=("b1",))

    mock_governance.verify_post_reasoning.assert_called_once_with(
        probability=0.9,
        severity="high",
        blockers=("b1",),
    )
    assert result.blocked is True
    assert "Governance blocked" in (result.content or "")


@pytest.mark.asyncio
async def test_governance_verification_pre_execution_called(
    enabled_gate: CognitiveGovernanceGate,
    mock_governance: AsyncMock,
) -> None:
    """check_pre_execution delegates to verify_pre_execution and blocks on FAIL."""
    mock_governance.verify_pre_execution.return_value = VCResult(
        vc_id="VC-Cautious-001",
        status="FAIL",
        message="L3+ action without user confirmation",
    )

    result = await enabled_gate.check_pre_execution("execute", requires_confirmation=True)

    mock_governance.verify_pre_execution.assert_called_once_with(
        execution_path="execute",
        requires_confirmation=True,
    )
    assert result.blocked is True
    assert "Governance blocked" in (result.content or "")


@pytest.mark.asyncio
async def test_governance_verification_all_pass(
    enabled_gate: CognitiveGovernanceGate,
    mock_governance: AsyncMock,
) -> None:
    """All phase checks run their verification exactly once and never block on PASS."""
    results = [
        await enabled_gate.check_pre_perception("test message"),
        await enabled_gate.check_post_perception("read_file", 0.8),
        await enabled_gate.check_pre_reasoning("read_file", 0.8),
        await enabled_gate.check_post_reasoning(probability=0.9, severity="low", blockers=()),
        await enabled_gate.check_pre_execution("bypass", requires_confirmation=False),
    ]

    mock_governance.verify_pre_perception.assert_called_once()
    mock_governance.verify_post_perception.assert_called_once()
    mock_governance.verify_pre_reasoning.assert_called_once()
    mock_governance.verify_post_reasoning.assert_called_once()
    mock_governance.verify_pre_execution.assert_called_once()

    assert all(result.blocked is False for result in results)


@pytest.mark.asyncio
async def test_governance_disabled_does_not_call_verification(mock_governance: AsyncMock) -> None:
    """A disabled gate short-circuits: no verification runs, nothing blocks."""
    gate = CognitiveGovernanceGate(enabled=False)
    gate._governance = mock_governance

    results = [
        await gate.check_pre_perception("test message"),
        await gate.check_post_perception("read_file", 0.8),
        await gate.check_pre_reasoning("read_file", 0.8),
        await gate.check_post_reasoning(probability=0.9, severity="low", blockers=()),
        await gate.check_pre_execution("bypass", requires_confirmation=False),
    ]

    for method in (
        "verify_pre_perception",
        "verify_post_perception",
        "verify_pre_reasoning",
        "verify_post_reasoning",
        "verify_pre_execution",
    ):
        assert not getattr(mock_governance, method).called, f"{method} called on a disabled gate"
    assert all(result.blocked is False for result in results)


@pytest.mark.asyncio
async def test_governance_disabled_by_default() -> None:
    """Governance is disabled by default; the orchestrator exposes a disabled gate."""
    with (
        patch("polaris.kernelone.cognitive.pipeline_coordinator.PerceptionLayer"),
        patch("polaris.kernelone.cognitive.pipeline_coordinator.CriticalThinkingEngine"),
        patch("polaris.kernelone.cognitive.pipeline_coordinator.MetaCognitionEngine"),
        patch("polaris.kernelone.cognitive.pipeline_coordinator.CautiousExecutionPolicy"),
        patch("polaris.kernelone.cognitive.pipeline_coordinator.CognitivePipeline"),
        patch("polaris.kernelone.cognitive.pipeline_coordinator.create_llm_adapter"),
        patch("polaris.kernelone.cognitive.orchestrator.get_session_manager"),
    ):
        orchestrator = CognitiveOrchestrator(enable_evolution=False, enable_personality=False)

    # Governance is disabled by default for backward compatibility
    # (COGNITIVE_ENABLE_GOVERNANCE defaults to "0"); opt in with enable_governance=True.
    assert orchestrator._enable_governance is False
    assert orchestrator._governance_gate.is_enabled is False
    assert orchestrator._governance_gate._governance is None
