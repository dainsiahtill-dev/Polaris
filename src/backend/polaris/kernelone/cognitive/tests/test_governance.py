"""Unit tests for Cognitive Governance - Verification Cards."""

from __future__ import annotations

import pytest
from polaris.kernelone.cognitive.governance import CognitiveGovernance, GovernanceState, VCResult


@pytest.fixture
def governance():
    return CognitiveGovernance()


@pytest.mark.asyncio
async def test_pre_perception_empty_message(governance):
    """Empty message should fail VC-Intent-001."""
    result = await governance.verify_pre_perception("")
    assert result.status == "FAIL"
    assert result.vc_id == "VC-Intent-001"


@pytest.mark.asyncio
async def test_pre_perception_whitespace_message(governance):
    """Whitespace-only message should fail."""
    result = await governance.verify_pre_perception("   ")
    assert result.status == "FAIL"


@pytest.mark.asyncio
async def test_pre_perception_valid_message(governance):
    """Valid message should pass."""
    result = await governance.verify_pre_perception("Read the file")
    assert result.status == "PASS"
    assert result.vc_id == "VC-Intent-001"


@pytest.mark.asyncio
async def test_post_perception_unknown_intent(governance):
    """Unknown intent should fail VC-Intent-001."""
    result = await governance.verify_post_perception("unknown", 0.5)
    assert result.status == "FAIL"


@pytest.mark.asyncio
async def test_post_perception_low_confidence(governance):
    """Low confidence should warn."""
    result = await governance.verify_post_perception("read_file", 0.2)
    assert result.status == "WARN"


@pytest.mark.asyncio
async def test_post_perception_good_confidence(governance):
    """Good confidence should pass."""
    result = await governance.verify_post_perception("read_file", 0.7)
    assert result.status == "PASS"


@pytest.mark.asyncio
async def test_pre_reasoning_low_confidence_high_risk(governance):
    """Low confidence for high-risk intent should warn."""
    result = await governance.verify_pre_reasoning("delete_file", 0.3)
    assert result.status == "WARN"


@pytest.mark.asyncio
async def test_pre_reasoning_high_confidence_low_risk(governance):
    """High confidence for low-risk intent should pass."""
    result = await governance.verify_pre_reasoning("read_file", 0.8)
    assert result.status == "PASS"


@pytest.mark.asyncio
async def test_post_reasoning_critical_blockers(governance):
    """Critical blockers should fail VC-Cautious-001."""
    blockers = ("Critical severity - requires explicit approval",)
    result = await governance.verify_post_reasoning(0.9, "critical", blockers)
    assert result.status == "FAIL"


@pytest.mark.asyncio
async def test_post_reasoning_good_reasoning(governance):
    """Good reasoning should pass."""
    result = await governance.verify_post_reasoning(0.8, "low", ())
    assert result.status == "PASS"


@pytest.mark.asyncio
async def test_pre_execution_full_pipe_without_confirmation(governance):
    """Full pipe without confirmation should fail VC-Cautious-001."""
    result = await governance.verify_pre_execution("full_pipe", False)
    assert result.status == "FAIL"


@pytest.mark.asyncio
async def test_pre_execution_with_confirmation(governance):
    """Full pipe with confirmation should pass."""
    result = await governance.verify_pre_execution("full_pipe", True)
    assert result.status == "PASS"


@pytest.mark.asyncio
async def test_pre_execution_bypass(governance):
    """Bypass path should always pass."""
    result = await governance.verify_pre_execution("bypass", False)
    assert result.status == "PASS"


@pytest.mark.asyncio
async def test_post_execution_success(governance):
    """Successful execution should pass."""
    result = await governance.verify_post_execution(True, True)
    assert result.status == "PASS"


@pytest.mark.asyncio
async def test_meta_cognition_valid(governance):
    """Valid meta-cognition should pass."""
    result = await governance.verify_meta_cognition(0.8, 0.7)
    assert result.status == "PASS"


@pytest.mark.asyncio
async def test_meta_cognition_overconfidence(governance):
    """Output confidence exceeding knowledge boundary should warn."""
    result = await governance.verify_meta_cognition(0.5, 0.9)
    assert result.status == "WARN"


def test_vc_result_dataclass():
    """Test VCResult dataclass."""
    result = VCResult(
        vc_id="VC-Test-001",
        status="PASS",
        message="Test passed",
        details={"key": "value"},
    )
    assert result.vc_id == "VC-Test-001"
    assert result.status == "PASS"
    assert result.message == "Test passed"
    assert result.details == {"key": "value"}


def test_vc_result_defaults():
    """Test VCResult default values."""
    result = VCResult(vc_id="VC-Test", status="PASS", message="OK")
    assert result.details == {}


# ---------------------------------------------------------------------------
# Enhanced verify_post_perception tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_perception_consecutive_unknown_escalation(governance):
    """Consecutive unknown intents (>=2) should FAIL with escalation message."""
    result = await governance.verify_post_perception("unknown", 0.5, consecutive_unknown_count=1)
    assert result.status == "FAIL"
    assert "Consecutive unknown" in result.message
    assert result.details["consecutive_unknown_count"] == 2


@pytest.mark.asyncio
async def test_post_perception_confidence_declining(governance):
    """Monotonically declining confidence trajectory should WARN."""
    result = await governance.verify_post_perception("read_file", 0.3, confidence_trajectory=(0.9, 0.7, 0.4))
    assert result.status == "WARN"
    assert "declining" in result.message.lower()


@pytest.mark.asyncio
async def test_post_perception_confidence_stable_passes(governance):
    """Non-declining trajectory should not trigger trajectory WARN."""
    result = await governance.verify_post_perception("read_file", 0.8, confidence_trajectory=(0.5, 0.6, 0.7))
    assert result.status == "PASS"


@pytest.mark.asyncio
async def test_post_perception_unmapped_intent_passes(governance):
    """Intent not in _INTENT_CATEGORY_MAP should still pass (higher-level cognitive intent)."""
    result = await governance.verify_post_perception("plan", 0.8)
    assert result.status == "PASS"


# ---------------------------------------------------------------------------
# Enhanced verify_post_reasoning tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_reasoning_high_risk_low_probability(governance):
    """High severity + probability < 0.7 should WARN."""
    result = await governance.verify_post_reasoning(0.5, "high", ())
    assert result.status == "WARN"
    assert "0.50" in result.message


@pytest.mark.asyncio
async def test_post_reasoning_blocker_action_conflict(governance):
    """Blocker referencing an action name should WARN."""
    result = await governance.verify_post_reasoning(
        0.9, "low", ("blocked due to create_file failure",), actions=("create_file",)
    )
    assert result.status == "WARN"
    assert "references action" in result.message


@pytest.mark.asyncio
async def test_post_reasoning_accumulated_warn_escalation(governance):
    """Accumulated warn_count >= 3 should escalate to FAIL."""
    result = await governance.verify_post_reasoning(0.8, "low", (), accumulated_warn_count=3)
    assert result.status == "FAIL"
    assert "Accumulated warn count" in result.message


@pytest.mark.asyncio
async def test_post_reasoning_accumulated_warn_below_threshold(governance):
    """Accumulated warn_count < 3 should not escalate."""
    result = await governance.verify_post_reasoning(0.8, "low", (), accumulated_warn_count=2)
    assert result.status == "PASS"


# ---------------------------------------------------------------------------
# verify_reasoning_consistency tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reasoning_consistency_empty_conclusion(governance):
    """Empty conclusion should FAIL."""
    result = await governance.verify_reasoning_consistency("", "create_file", ())
    assert result.status == "FAIL"
    assert result.vc_id == "VC-Consistency-001"


@pytest.mark.asyncio
async def test_reasoning_consistency_contradiction(governance):
    """Conclusion with negation phrase matching intent type should WARN."""
    result = await governance.verify_reasoning_consistency("We should do not modify the file", "modify_file", ())
    assert result.status == "WARN"
    assert "contradict" in result.message.lower()


@pytest.mark.asyncio
async def test_reasoning_consistency_too_many_assumptions(governance):
    """Many assumptions with short conclusion should WARN."""
    assumptions = tuple(f"assumption_{i}" for i in range(7))
    result = await governance.verify_reasoning_consistency("Short conclusion", "read_file", assumptions)
    assert result.status == "WARN"
    assert "Too many assumptions" in result.message


@pytest.mark.asyncio
async def test_reasoning_consistency_pass(governance):
    """Valid reasoning should pass consistency check."""
    result = await governance.verify_reasoning_consistency(
        "The file was read successfully and all checks passed.",
        "read_file",
        ("file exists", "permissions OK"),
    )
    assert result.status == "PASS"


# ---------------------------------------------------------------------------
# GovernanceState tests
# ---------------------------------------------------------------------------


def test_governance_state_record_warn():
    state = GovernanceState()
    state.record_result("WARN", "read_file", 0.8)
    assert state.warn_count == 1
    assert state.fail_count == 0
    assert state.last_intent_type == "read_file"
    assert state.confidence_trajectory == [0.8]


def test_governance_state_record_fail():
    state = GovernanceState()
    state.record_result("FAIL", "unknown", 0.1)
    assert state.warn_count == 0
    assert state.fail_count == 1
    assert state.consecutive_unknown_count == 1


def test_governance_state_consecutive_unknown():
    state = GovernanceState()
    state.record_result("WARN", "unknown", 0.3)
    state.record_result("WARN", "unknown", 0.2)
    assert state.consecutive_unknown_count == 2
    state.record_result("WARN", "read_file", 0.5)
    assert state.consecutive_unknown_count == 0


def test_governance_state_should_escalate():
    state = GovernanceState(max_warn_before_escalation=3)
    assert not state.should_escalate()
    state.record_result("WARN", "a", 0.5)
    state.record_result("WARN", "b", 0.4)
    state.record_result("WARN", "c", 0.3)
    assert state.should_escalate()


def test_governance_state_confidence_declining():
    state = GovernanceState()
    assert not state.is_confidence_declining()
    state.record_result("PASS", "a", 0.9)
    state.record_result("PASS", "b", 0.7)
    state.record_result("PASS", "c", 0.5)
    assert state.is_confidence_declining()


def test_governance_state_confidence_not_declining():
    state = GovernanceState()
    state.record_result("PASS", "a", 0.5)
    state.record_result("PASS", "b", 0.7)
    state.record_result("PASS", "c", 0.9)
    assert not state.is_confidence_declining()


def test_governance_state_reset():
    state = GovernanceState()
    state.record_result("WARN", "unknown", 0.3)
    state.record_result("FAIL", "bad", 0.1)
    state.reset()
    assert state.warn_count == 0
    assert state.fail_count == 0
    assert state.last_intent_type == ""
    assert state.consecutive_unknown_count == 0
    assert state.confidence_trajectory == []
