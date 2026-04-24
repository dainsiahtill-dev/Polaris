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
from polaris.cells.roles.kernel.internal.policy.tool_policy import (
    ToolPolicy,
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


class TestToolPolicy:
    """Test suite for ToolPolicy class."""

    @pytest.fixture
    def default_policy(self) -> ToolPolicy:
        """Create a default tool policy."""
        return ToolPolicy()

    @pytest.fixture
    def whitelist_policy(self) -> ToolPolicy:
        """Create a tool policy with whitelist."""
        return ToolPolicy(whitelist=["read_file", "write_file", "search"])

    @pytest.fixture
    def blacklist_policy(self) -> ToolPolicy:
        """Create a tool policy with blacklist."""
        return ToolPolicy(blacklist=["delete_file", "execute_command"])

    @pytest.fixture
    def restricted_policy(self) -> ToolPolicy:
        """Create a restricted tool policy."""
        return ToolPolicy(
            allow_code_write=False,
            allow_command_execution=False,
            allow_file_delete=False,
            max_tool_calls_per_turn=10,
        )

    def test_initialization_default(self, default_policy: ToolPolicy) -> None:
        """Test ToolPolicy default initialization."""
        assert default_policy.whitelist == []
        assert default_policy.blacklist == []
        assert default_policy.allow_code_write is True
        assert default_policy.allow_command_execution is True
        assert default_policy.allow_file_delete is True
        assert default_policy.max_tool_calls_per_turn == 64

    def test_initialization_with_whitelist(self, whitelist_policy: ToolPolicy) -> None:
        """Test ToolPolicy initialization with whitelist."""
        assert whitelist_policy.whitelist == ["read_file", "write_file", "search"]

    def test_initialization_with_blacklist(self, blacklist_policy: ToolPolicy) -> None:
        """Test ToolPolicy initialization with blacklist."""
        assert blacklist_policy.blacklist == ["delete_file", "execute_command"]

    def test_evaluate_allowed_tool(self, whitelist_policy: ToolPolicy) -> None:
        """Test evaluate allows whitelisted tool."""
        decision = whitelist_policy.evaluate("read_file")
        assert decision.allowed is True
        assert decision.reason == "allowed"

    def test_evaluate_blocked_tool(self, whitelist_policy: ToolPolicy) -> None:
        """Test evaluate blocks non-whitelisted tool."""
        decision = whitelist_policy.evaluate("unknown_tool")
        assert decision.allowed is False

    def test_evaluate_blacklisted_tool(self, blacklist_policy: ToolPolicy) -> None:
        """Test evaluate blocks blacklisted tool."""
        decision = blacklist_policy.evaluate("delete_file")
        assert decision.allowed is False

    def test_evaluate_empty_tool_name(self, default_policy: ToolPolicy) -> None:
        """Test evaluate rejects empty tool name."""
        decision = default_policy.evaluate("")
        assert decision.allowed is False
        assert decision.reason == "tool name is empty"

    def test_evaluate_whitespace_tool_name(self, default_policy: ToolPolicy) -> None:
        """Test evaluate rejects whitespace-only tool name."""
        decision = default_policy.evaluate("   ")
        assert decision.allowed is False
        assert decision.reason == "tool name is empty"

    def test_evaluate_requires_approval_for_write_tools(self, default_policy: ToolPolicy) -> None:
        """Test evaluate marks write tools as requiring approval."""
        decision = default_policy.evaluate("write_file")
        assert decision.allowed is True
        assert decision.requires_approval is True

    def test_evaluate_requires_approval_for_execute_tools(self, default_policy: ToolPolicy) -> None:
        """Test evaluate marks execute tools as requiring approval."""
        decision = default_policy.evaluate("execute_command")
        assert decision.allowed is True
        assert decision.requires_approval is True

    def test_evaluate_no_approval_for_read_tools(self, default_policy: ToolPolicy) -> None:
        """Test evaluate does not mark read tools as requiring approval."""
        decision = default_policy.evaluate("read_file")
        assert decision.allowed is True
        assert decision.requires_approval is False

    def test_filter_allowed_tools(self, whitelist_policy: ToolPolicy) -> None:
        """Test filter returns only allowed tools."""
        tool_calls = [
            {"tool": "read_file", "args": {"path": "test.txt"}},
            {"tool": "write_file", "args": {"path": "test.txt", "content": "test"}},
            {"tool": "unknown_tool", "args": {}},
        ]
        filtered = whitelist_policy.filter(tool_calls)
        assert len(filtered) == 2
        assert filtered[0]["tool"] == "read_file"
        assert filtered[1]["tool"] == "write_file"

    def test_filter_empty_list(self, default_policy: ToolPolicy) -> None:
        """Test filter handles empty list."""
        filtered = default_policy.filter([])
        assert filtered == []

    def test_filter_none_list(self, default_policy: ToolPolicy) -> None:
        """Test filter handles None list."""
        filtered = default_policy.filter(None)
        assert filtered == []

    def test_evaluate_calls(self, whitelist_policy: ToolPolicy) -> None:
        """Test evaluate_calls processes multiple calls."""
        calls = [
            {"tool": "read_file", "args": {}},
            {"tool": "write_file", "args": {}},
            {"tool": "unknown", "args": {}},
        ]
        approved, blocked, _violations = whitelist_policy.evaluate_calls(calls)
        assert len(approved) == 2
        assert len(blocked) == 1
        assert approved[0].tool == "read_file"
        assert approved[1].tool == "write_file"
        assert blocked[0].tool == "unknown"

    def test_from_profile_without_policy(self) -> None:
        """Test from_profile creates default policy when no tool_policy."""
        profile = type("Profile", (), {"tool_policy": None})()
        policy = ToolPolicy.from_profile(profile)
        assert policy.whitelist == []
        assert policy.blacklist == []

    def test_from_profile_with_policy(self) -> None:
        """Test from_profile creates policy from profile tool_policy."""
        tool_policy = type(
            "ToolPolicyConfig",
            (),
            {
                "whitelist": ["read_file"],
                "blacklist": ["delete_file"],
                "allow_code_write": False,
                "allow_command_execution": True,
                "allow_file_delete": False,
                "max_tool_calls_per_turn": 32,
                "policy_id": "test_policy",
            },
        )()
        profile = type("Profile", (), {"tool_policy": tool_policy})()
        policy = ToolPolicy.from_profile(profile)
        assert policy.whitelist == ["read_file"]
        assert policy.blacklist == ["delete_file"]
        assert policy.allow_code_write is False
        assert policy.allow_command_execution is True
        assert policy.allow_file_delete is False
        assert policy.max_tool_calls_per_turn == 32
        assert policy.policy_id == "test_policy"


class TestToolPolicyEdgeCases:
    """Test edge cases for ToolPolicy."""

    def test_normalize_args_dict(self) -> None:
        """Test _normalize_args handles dict."""
        args = {"key": "value"}
        result = ToolPolicy._normalize_args(args)
        assert result == {"key": "value"}

    def test_normalize_args_string_json(self) -> None:
        """Test _normalize_args handles JSON string."""
        args = '{"key": "value"}'
        result = ToolPolicy._normalize_args(args)
        assert result == {"key": "value"}

    def test_normalize_args_string_invalid_json(self) -> None:
        """Test _normalize_args handles invalid JSON string."""
        args = "not json"
        result = ToolPolicy._normalize_args(args)
        assert result == {"raw": "not json"}

    def test_normalize_args_string_empty(self) -> None:
        """Test _normalize_args handles empty string."""
        result = ToolPolicy._normalize_args("")
        assert result == {}

    def test_normalize_args_string_whitespace(self) -> None:
        """Test _normalize_args handles whitespace string."""
        result = ToolPolicy._normalize_args("   ")
        assert result == {}

    def test_normalize_args_other_type(self) -> None:
        """Test _normalize_args handles other types."""
        result = ToolPolicy._normalize_args(123)
        assert result == {}

    def test_requires_approval_case_insensitive(self) -> None:
        """Test _requires_approval is case insensitive."""
        assert ToolPolicy._requires_approval("WRITE_FILE") is True
        assert ToolPolicy._requires_approval("Write_File") is True
        assert ToolPolicy._requires_approval("write_file") is True

    def test_requires_approval_empty_name(self) -> None:
        """Test _requires_approval with empty name."""
        assert ToolPolicy._requires_approval("") is False

    def test_coerce_call_dict(self) -> None:
        """Test _coerce_call handles dict."""
        policy = ToolPolicy()
        call = {"tool": "search", "args": {"query": "test"}}
        result = policy._coerce_call(call)
        assert result.tool == "search"
        assert result.args == {"query": "test"}

    def test_coerce_call_dict_with_name_key(self) -> None:
        """Test _coerce_call handles dict with 'name' key."""
        policy = ToolPolicy()
        call = {"name": "search", "arguments": {"query": "test"}}
        result = policy._coerce_call(call)
        assert result.tool == "search"
        assert result.args == {"query": "test"}

    def test_evaluate_with_list(self) -> None:
        """Test evaluate with list input delegates to evaluate_calls."""
        policy = ToolPolicy(whitelist=["search"])
        calls = [{"tool": "search", "args": {}}]
        result = policy.evaluate(calls)
        assert isinstance(result, tuple)
        approved, blocked, _violations = result
        assert len(approved) == 1
        assert len(blocked) == 0


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
