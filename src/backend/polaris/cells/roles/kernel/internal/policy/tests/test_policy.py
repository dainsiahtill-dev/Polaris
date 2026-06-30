"""Tests for Policy module.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import pytest
from polaris.cells.roles.kernel.internal.policy.budget_policy import (
    BudgetDecision,
    BudgetPolicy,
    BudgetState,
)


class TestBudgetState:
    """Test suite for BudgetState dataclass."""

    @pytest.fixture
    def default_state(self) -> BudgetState:
        """Create a default budget state."""
        return BudgetState()

    @pytest.fixture
    def configured_state(self) -> BudgetState:
        """Create a configured budget state."""
        return BudgetState(
            total_tool_calls=10,
            max_tool_calls=50,
            wall_time_seconds=100.0,
            max_wall_time_seconds=300.0,
            total_tokens=1000,
            max_tokens=5000,
            artifact_count=5,
            max_artifacts=20,
            result_size_bytes=1024,
            max_result_size_bytes=10240,
        )

    def test_default_values(self, default_state: BudgetState) -> None:
        """Test BudgetState default values."""
        assert default_state.total_tool_calls == 0
        assert default_state.max_tool_calls == 64
        assert default_state.wall_time_seconds == 0.0
        assert default_state.max_wall_time_seconds == 900.0
        assert default_state.total_tokens == 0
        assert default_state.max_tokens is None
        assert default_state.artifact_count == 0
        assert default_state.max_artifacts == 10
        assert default_state.result_size_bytes == 0
        assert default_state.max_result_size_bytes is None
        assert default_state.max_stall_cycles == 2

    def test_configured_values(self, configured_state: BudgetState) -> None:
        """Test BudgetState configured values."""
        assert configured_state.total_tool_calls == 10
        assert configured_state.max_tool_calls == 50
        assert configured_state.wall_time_seconds == 100.0
        assert configured_state.max_wall_time_seconds == 300.0
        assert configured_state.total_tokens == 1000
        assert configured_state.max_tokens == 5000
        assert configured_state.artifact_count == 5
        assert configured_state.max_artifacts == 20
        assert configured_state.result_size_bytes == 1024
        assert configured_state.max_result_size_bytes == 10240

    def test_to_dict(self, configured_state: BudgetState) -> None:
        """Test BudgetState.to_dict() serialization."""
        result = configured_state.to_dict()
        assert result["total_tool_calls"] == 10
        assert result["max_tool_calls"] == 50
        assert result["wall_time_seconds"] == 100.0
        assert result["max_wall_time_seconds"] == 300.0
        assert result["total_tokens"] == 1000
        assert result["max_tokens"] == 5000
        assert result["artifact_count"] == 5
        assert result["max_artifacts"] == 20
        assert result["result_size_bytes"] == 1024
        assert result["max_result_size_bytes"] == 10240
        assert result["stall_cycles"] == 0

    def test_to_dict_with_stall_cycles(self) -> None:
        """Test BudgetState.to_dict() with stall cycles."""
        state = BudgetState()
        state._stall_cycles = 5
        result = state.to_dict()
        assert result["stall_cycles"] == 5


class TestBudgetPolicy:
    """Test suite for BudgetPolicy class."""

    @pytest.fixture
    def default_policy(self) -> BudgetPolicy:
        """Create a default budget policy."""
        return BudgetPolicy()

    @pytest.fixture
    def configured_policy(self) -> BudgetPolicy:
        """Create a configured budget policy."""
        return BudgetPolicy(
            BudgetState(
                max_tool_calls=32,
                max_wall_time_seconds=600,
                max_tokens=10000,
                max_artifacts=15,
            )
        )

    def test_initialization_default(self, default_policy: BudgetPolicy) -> None:
        """Test BudgetPolicy default initialization."""
        assert default_policy.state.max_tool_calls == 64
        assert default_policy.state.max_wall_time_seconds == 900.0

    def test_initialization_configured(self, configured_policy: BudgetPolicy) -> None:
        """Test BudgetPolicy configured initialization."""
        assert configured_policy.state.max_tool_calls == 32
        assert configured_policy.state.max_wall_time_seconds == 600
        assert configured_policy.state.max_tokens == 10000
        assert configured_policy.state.max_artifacts == 15

    def test_evaluate_within_budget(self, configured_policy: BudgetPolicy) -> None:
        """Test evaluate returns within_budget=True when under limits."""
        decision = configured_policy.evaluate()
        assert decision.within_budget is True
        assert decision.exceeded is None

    def test_evaluate_exceed_tool_calls(self) -> None:
        """Test evaluate detects tool_calls exceeded."""
        state = BudgetState(total_tool_calls=70, max_tool_calls=64)
        policy = BudgetPolicy(state)
        decision = policy.evaluate()
        assert decision.within_budget is False
        assert decision.exceeded == "tool_calls"

    def test_evaluate_exceed_wall_time(self) -> None:
        """Test evaluate detects wall_time exceeded."""
        state = BudgetState(wall_time_seconds=1000.0, max_wall_time_seconds=900.0)
        policy = BudgetPolicy(state)
        decision = policy.evaluate()
        assert decision.within_budget is False
        assert decision.exceeded == "wall_time"

    def test_evaluate_exceed_tokens(self) -> None:
        """Test evaluate detects tokens exceeded."""
        state = BudgetState(total_tokens=6000, max_tokens=5000)
        policy = BudgetPolicy(state)
        decision = policy.evaluate()
        assert decision.within_budget is False
        assert decision.exceeded == "tokens"

    def test_evaluate_exceed_artifacts(self) -> None:
        """Test evaluate detects artifacts exceeded."""
        state = BudgetState(artifact_count=15, max_artifacts=10)
        policy = BudgetPolicy(state)
        decision = policy.evaluate()
        assert decision.within_budget is False
        assert decision.exceeded == "artifacts"

    def test_evaluate_exceed_result_size(self) -> None:
        """Test evaluate detects result_size exceeded."""
        state = BudgetState(
            result_size_bytes=20000,
            max_result_size_bytes=10000,
        )
        policy = BudgetPolicy(state)
        decision = policy.evaluate()
        assert decision.within_budget is False
        assert decision.exceeded == "result_size"

    def test_record_tool_call(self, default_policy: BudgetPolicy) -> None:
        """Test record_tool_call increments counter."""
        assert default_policy.state.total_tool_calls == 0
        default_policy.record_tool_call()
        assert default_policy.state.total_tool_calls == 1
        default_policy.record_tool_call()
        assert default_policy.state.total_tool_calls == 2

    def test_record_time(self, default_policy: BudgetPolicy) -> None:
        """Test record_time adds to wall_time_seconds."""
        assert default_policy.state.wall_time_seconds == 0.0
        default_policy.record_time(10.5)
        assert default_policy.state.wall_time_seconds == 10.5
        default_policy.record_time(5.3)
        assert default_policy.state.wall_time_seconds == 15.8

    def test_record_tokens(self, default_policy: BudgetPolicy) -> None:
        """Test record_tokens adds to total_tokens."""
        assert default_policy.state.total_tokens == 0
        default_policy.record_tokens(100)
        assert default_policy.state.total_tokens == 100
        default_policy.record_tokens(200)
        assert default_policy.state.total_tokens == 300

    def test_record_artifact(self, default_policy: BudgetPolicy) -> None:
        """Test record_artifact increments counter."""
        assert default_policy.state.artifact_count == 0
        default_policy.record_artifact()
        assert default_policy.state.artifact_count == 1
        default_policy.record_artifact()
        assert default_policy.state.artifact_count == 2

    def test_record_result_size(self, default_policy: BudgetPolicy) -> None:
        """Test record_result_size adds to result_size_bytes."""
        assert default_policy.state.result_size_bytes == 0
        default_policy.record_result_size(1024)
        assert default_policy.state.result_size_bytes == 1024
        default_policy.record_result_size(2048)
        assert default_policy.state.result_size_bytes == 3072

    def test_configure(self, default_policy: BudgetPolicy) -> None:
        """Test configure updates policy parameters."""
        default_policy.configure(
            max_tool_calls=100,
            max_wall_time_seconds=1200,
            max_tokens=20000,
            max_artifacts=25,
            max_result_size_bytes=50000,
            max_stall_cycles=5,
        )
        assert default_policy.state.max_tool_calls == 100
        assert default_policy.state.max_wall_time_seconds == 1200
        assert default_policy.state.max_tokens == 20000
        assert default_policy.state.max_artifacts == 25
        assert default_policy.state.max_result_size_bytes == 50000
        assert default_policy.state.max_stall_cycles == 5

    def test_configure_partial(self, default_policy: BudgetPolicy) -> None:
        """Test configure updates only specified parameters."""
        original_wall_time = default_policy.state.max_wall_time_seconds
        default_policy.configure(max_tool_calls=100)
        assert default_policy.state.max_tool_calls == 100
        assert default_policy.state.max_wall_time_seconds == original_wall_time

    def test_sync_from_safety_policy(self, default_policy: BudgetPolicy) -> None:
        """Test sync_from_safety_policy updates parameters."""
        default_policy.sync_from_safety_policy(
            max_tool_calls=128,
            max_wall_time_seconds=1800,
            max_stall_cycles=4,
        )
        assert default_policy.state.max_tool_calls == 128
        assert default_policy.state.max_wall_time_seconds == 1800
        assert default_policy.state.max_stall_cycles == 4

    def test_evaluate_with_external_state(self, configured_policy: BudgetPolicy) -> None:
        """Test evaluate with external state."""
        external_state = BudgetState(
            total_tool_calls=40,
            max_tool_calls=32,
        )
        decision = configured_policy.evaluate(external_state)
        assert decision.within_budget is False
        assert decision.exceeded == "tool_calls"

    def test_state_property_returns_state(self, configured_policy: BudgetPolicy) -> None:
        """Test state property returns internal state."""
        assert configured_policy.state is configured_policy._state


class TestBudgetDecision:
    """Test suite for BudgetDecision dataclass."""

    def test_within_budget(self) -> None:
        """Test BudgetDecision for within budget."""
        decision = BudgetDecision(within_budget=True)
        assert decision.within_budget is True
        assert decision.exceeded is None

    def test_exceeded(self) -> None:
        """Test BudgetDecision for exceeded budget."""
        decision = BudgetDecision(within_budget=False, exceeded="tool_calls")
        assert decision.within_budget is False
        assert decision.exceeded == "tool_calls"


class TestBudgetPolicyEdgeCases:
    """Test edge cases for BudgetPolicy."""

    def test_from_metadata_empty(self) -> None:
        """Test from_metadata with empty dict."""
        policy = BudgetPolicy.from_metadata({})
        assert policy.state.max_tool_calls == 64
        assert policy.state.max_wall_time_seconds == 900

    def test_from_metadata_with_values(self) -> None:
        """Test from_metadata with values."""
        policy = BudgetPolicy.from_metadata(
            {
                "max_total_tool_calls": 128,
                "max_wall_time_seconds": 1800,
                "max_stall_cycles": 4,
            }
        )
        assert policy.state.max_tool_calls == 128
        assert policy.state.max_wall_time_seconds == 1800
        assert policy.state.max_stall_cycles == 4

    def test_from_metadata_alias(self) -> None:
        """Test from_metadata with alias key."""
        policy = BudgetPolicy.from_metadata(
            {
                "max_tool_calls": 96,
            }
        )
        assert policy.state.max_tool_calls == 96

    def test_from_metadata_invalid_values(self) -> None:
        """Test from_metadata handles invalid values."""
        policy = BudgetPolicy.from_metadata(
            {
                "max_total_tool_calls": "invalid",
                "max_wall_time_seconds": "not_a_number",
            }
        )
        assert policy.state.max_tool_calls == 64
        assert policy.state.max_wall_time_seconds == 900

    def test_from_metadata_clamps_values(self) -> None:
        """Test from_metadata clamps values to valid range."""
        policy = BudgetPolicy.from_metadata(
            {
                "max_total_tool_calls": 2000,
                "max_wall_time_seconds": 10000,
            }
        )
        assert policy.state.max_tool_calls == 1024
        assert policy.state.max_wall_time_seconds == 7200

    def test_evaluate_boundary_tool_calls(self) -> None:
        """Test evaluate at exact tool_calls limit."""
        state = BudgetState(total_tool_calls=64, max_tool_calls=64)
        policy = BudgetPolicy(state)
        decision = policy.evaluate()
        assert decision.within_budget is True

    def test_evaluate_boundary_wall_time(self) -> None:
        """Test evaluate at exact wall_time limit."""
        state = BudgetState(wall_time_seconds=900.0, max_wall_time_seconds=900.0)
        policy = BudgetPolicy(state)
        decision = policy.evaluate()
        assert decision.within_budget is True

    def test_evaluate_no_limits(self) -> None:
        """Test evaluate with no limits set."""
        state = BudgetState(
            max_tool_calls=0,
            max_wall_time_seconds=0,
            max_tokens=None,
            max_result_size_bytes=None,
        )
        policy = BudgetPolicy(state)
        decision = policy.evaluate()
        assert decision.within_budget is True
