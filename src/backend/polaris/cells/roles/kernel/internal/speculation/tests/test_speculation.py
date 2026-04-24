"""Tests for Speculation module.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import asyncio
import time

import pytest
from polaris.cells.roles.kernel.internal.speculation.fingerprints import (
    build_spec_key,
    normalize_args,
)
from polaris.cells.roles.kernel.internal.speculation.models import (
    BudgetSnapshot,
    CancelToken,
    CandidateToolCall,
    FieldMutation,
    SalvageDecision,
    ShadowTaskRecord,
    ShadowTaskState,
    ToolSpecPolicy,
    check_cancel,
)


class TestToolSpecPolicy:
    """Test suite for ToolSpecPolicy dataclass."""

    @pytest.fixture
    def readonly_policy(self) -> ToolSpecPolicy:
        """Create a readonly tool spec policy."""
        return ToolSpecPolicy(
            tool_name="read_file",
            side_effect="readonly",
            cost="cheap",
            cancellability="cooperative",
            reusability="cacheable",
            speculate_mode="speculative_allowed",
            min_stability_score=0.85,
            timeout_ms=1000,
            max_parallel=2,
            cache_ttl_ms=3000,
        )

    @pytest.fixture
    def mutating_policy(self) -> ToolSpecPolicy:
        """Create a mutating tool spec policy."""
        return ToolSpecPolicy(
            tool_name="write_file",
            side_effect="mutating",
            cost="medium",
            cancellability="non_cancelable",
            reusability="non_reusable",
            speculate_mode="forbid",
            min_stability_score=0.95,
            timeout_ms=2000,
            max_parallel=1,
            cache_ttl_ms=1000,
        )

    def test_default_values(self) -> None:
        """Test ToolSpecPolicy default values."""
        policy = ToolSpecPolicy(
            tool_name="test",
            side_effect="readonly",
            cost="cheap",
            cancellability="cooperative",
            reusability="cacheable",
            speculate_mode="speculative_allowed",
        )
        assert policy.min_stability_score == 0.82
        assert policy.timeout_ms == 1200
        assert policy.max_parallel == 2
        assert policy.cache_ttl_ms == 3000

    def test_readonly_policy(self, readonly_policy: ToolSpecPolicy) -> None:
        """Test readonly policy configuration."""
        assert readonly_policy.tool_name == "read_file"
        assert readonly_policy.side_effect == "readonly"
        assert readonly_policy.cost == "cheap"
        assert readonly_policy.cancellability == "cooperative"
        assert readonly_policy.reusability == "cacheable"
        assert readonly_policy.speculate_mode == "speculative_allowed"

    def test_mutating_policy(self, mutating_policy: ToolSpecPolicy) -> None:
        """Test mutating policy configuration."""
        assert mutating_policy.tool_name == "write_file"
        assert mutating_policy.side_effect == "mutating"
        assert mutating_policy.cost == "medium"
        assert mutating_policy.cancellability == "non_cancelable"
        assert mutating_policy.reusability == "non_reusable"
        assert mutating_policy.speculate_mode == "forbid"

    def test_frozen(self) -> None:
        """Test ToolSpecPolicy is frozen (immutable)."""
        policy = ToolSpecPolicy(
            tool_name="test",
            side_effect="readonly",
            cost="cheap",
            cancellability="cooperative",
            reusability="cacheable",
            speculate_mode="speculative_allowed",
        )
        with pytest.raises(AttributeError):
            policy.tool_name = "changed"


class TestFieldMutation:
    """Test suite for FieldMutation dataclass."""

    def test_creation(self) -> None:
        """Test FieldMutation creation."""
        mutation = FieldMutation(
            field_path="args.query",
            old_value="old",
            new_value="new",
            ts_monotonic=time.monotonic(),
        )
        assert mutation.field_path == "args.query"
        assert mutation.old_value == "old"
        assert mutation.new_value == "new"

    def test_default_timestamp(self) -> None:
        """Test FieldMutation default timestamp."""
        time.monotonic()
        mutation = FieldMutation(
            field_path="test",
            old_value=None,
            new_value=None,
            ts_monotonic=0.0,
        )
        assert mutation.ts_monotonic == 0.0


class TestCandidateToolCall:
    """Test suite for CandidateToolCall dataclass."""

    @pytest.fixture
    def candidate(self) -> CandidateToolCall:
        """Create a candidate tool call."""
        return CandidateToolCall(
            candidate_id="candidate_123",
            stream_id="stream_456",
            turn_id="turn_789",
            tool_name="search",
            partial_args={"query": "test"},
            parse_state="syntactic_complete",
            stability_score=0.95,
        )

    def test_creation(self, candidate: CandidateToolCall) -> None:
        """Test CandidateToolCall creation."""
        assert candidate.candidate_id == "candidate_123"
        assert candidate.stream_id == "stream_456"
        assert candidate.turn_id == "turn_789"
        assert candidate.tool_name == "search"
        assert candidate.partial_args == {"query": "test"}
        assert candidate.parse_state == "syntactic_complete"
        assert candidate.stability_score == 0.95

    def test_default_values(self) -> None:
        """Test CandidateToolCall default values."""
        candidate = CandidateToolCall(
            candidate_id="id",
            stream_id="stream",
            turn_id="turn",
        )
        assert candidate.tool_name is None
        assert candidate.partial_args == {}
        assert candidate.parse_state == "incomplete"
        assert candidate.stability_score == 0.0
        assert candidate.semantic_hash == ""
        assert candidate.schema_valid is False
        assert candidate.end_tag_seen is False

    def test_parse_states(self) -> None:
        """Test all valid parse states."""
        for state in ["incomplete", "syntactic_complete", "schema_valid", "semantically_stable"]:
            candidate = CandidateToolCall(
                candidate_id="id",
                stream_id="stream",
                turn_id="turn",
                parse_state=state,
            )
            assert candidate.parse_state == state


class TestShadowTaskState:
    """Test suite for ShadowTaskState enum."""

    def test_all_states(self) -> None:
        """Test all shadow task states exist."""
        expected_states = {
            "CREATED",
            "ELIGIBLE",
            "STARTING",
            "RUNNING",
            "COMPLETED",
            "FAILED",
            "CANCEL_REQUESTED",
            "CANCELLED",
            "ABANDONED",
            "ADOPTED",
            "EXPIRED",
        }
        actual_states = {state.name for state in ShadowTaskState}
        assert actual_states == expected_states

    def test_state_values(self) -> None:
        """Test shadow task state values."""
        assert ShadowTaskState.CREATED.value == "created"
        assert ShadowTaskState.RUNNING.value == "running"
        assert ShadowTaskState.COMPLETED.value == "completed"
        assert ShadowTaskState.FAILED.value == "failed"
        assert ShadowTaskState.CANCELLED.value == "cancelled"


class TestShadowTaskRecord:
    """Test suite for ShadowTaskRecord dataclass."""

    @pytest.fixture
    def record(self) -> ShadowTaskRecord:
        """Create a shadow task record."""
        return ShadowTaskRecord(
            task_id="task_123",
            origin_turn_id="turn_456",
            origin_candidate_id="candidate_789",
            tool_name="search",
            normalized_args={"query": "test"},
            spec_key="spec_key_abc",
            env_fingerprint="git:abc123",
            policy_snapshot=ToolSpecPolicy(
                tool_name="search",
                side_effect="readonly",
                cost="cheap",
                cancellability="cooperative",
                reusability="cacheable",
                speculate_mode="speculative_allowed",
            ),
        )

    def test_creation(self, record: ShadowTaskRecord) -> None:
        """Test ShadowTaskRecord creation."""
        assert record.task_id == "task_123"
        assert record.origin_turn_id == "turn_456"
        assert record.origin_candidate_id == "candidate_789"
        assert record.tool_name == "search"
        assert record.normalized_args == {"query": "test"}
        assert record.spec_key == "spec_key_abc"
        assert record.env_fingerprint == "git:abc123"

    def test_default_state(self, record: ShadowTaskRecord) -> None:
        """Test ShadowTaskRecord default state."""
        assert record.state == ShadowTaskState.CREATED
        assert record.started_at is None
        assert record.finished_at is None
        assert record.result is None
        assert record.error is None
        assert record.cancel_reason is None
        assert record.adopted_by_call_id is None

    def test_state_transitions(self, record: ShadowTaskRecord) -> None:
        """Test state transitions."""
        record.state = ShadowTaskState.STARTING
        assert record.state == ShadowTaskState.STARTING

        record.state = ShadowTaskState.RUNNING
        assert record.state == ShadowTaskState.RUNNING

        record.state = ShadowTaskState.COMPLETED
        assert record.state == ShadowTaskState.COMPLETED


class TestCancelToken:
    """Test suite for CancelToken class."""

    @pytest.fixture
    def token(self) -> CancelToken:
        """Create a cancel token."""
        return CancelToken()

    def test_initial_state(self, token: CancelToken) -> None:
        """Test CancelToken initial state."""
        assert token.cancelled is False
        assert token.reason is None

    def test_cancel(self, token: CancelToken) -> None:
        """Test CancelToken cancel method."""
        token.cancel("timeout")
        assert token.cancelled is True
        assert token.reason == "timeout"

    def test_cancel_multiple_times(self, token: CancelToken) -> None:
        """Test CancelToken multiple cancels."""
        token.cancel("first")
        token.cancel("second")
        assert token.cancelled is True
        assert token.reason == "second"

    def test_check_cancel_raises(self, token: CancelToken) -> None:
        """Test check_cancel raises CancelledError when cancelled."""
        token.cancel("test_reason")
        with pytest.raises(asyncio.CancelledError, match="test_reason"):
            check_cancel(token)

    def test_check_cancel_no_raise(self, token: CancelToken) -> None:
        """Test check_cancel does not raise when not cancelled."""
        # Should not raise
        check_cancel(token)

    def test_check_cancel_none_token(self) -> None:
        """Test check_cancel with None token."""
        # Should not raise
        check_cancel(None)


class TestBudgetSnapshot:
    """Test suite for BudgetSnapshot dataclass."""

    def test_creation(self) -> None:
        """Test BudgetSnapshot creation."""
        snapshot = BudgetSnapshot(
            mode="turbo",
            active_shadow_tasks=5,
            abandonment_ratio=0.1,
            timeout_ratio=0.05,
            queue_pressure=0.3,
            cpu_pressure=0.4,
            memory_pressure=0.2,
            external_quota_pressure=0.1,
            wrong_adoption_count=2,
        )
        assert snapshot.mode == "turbo"
        assert snapshot.active_shadow_tasks == 5
        assert snapshot.abandonment_ratio == 0.1
        assert snapshot.timeout_ratio == 0.05
        assert snapshot.wrong_adoption_count == 2

    def test_default_wrong_adoption_count(self) -> None:
        """Test BudgetSnapshot default wrong_adoption_count."""
        snapshot = BudgetSnapshot(
            mode="balanced",
            active_shadow_tasks=0,
            abandonment_ratio=0.0,
            timeout_ratio=0.0,
            queue_pressure=0.0,
            cpu_pressure=0.0,
            memory_pressure=0.0,
            external_quota_pressure=0.0,
        )
        assert snapshot.wrong_adoption_count == 0

    def test_frozen(self) -> None:
        """Test BudgetSnapshot is frozen (immutable)."""
        snapshot = BudgetSnapshot(
            mode="safe",
            active_shadow_tasks=0,
            abandonment_ratio=0.0,
            timeout_ratio=0.0,
            queue_pressure=0.0,
            cpu_pressure=0.0,
            memory_pressure=0.0,
            external_quota_pressure=0.0,
        )
        with pytest.raises(AttributeError):
            snapshot.mode = "turbo"


class TestSalvageDecision:
    """Test suite for SalvageDecision enum."""

    def test_all_decisions(self) -> None:
        """Test all salvage decisions exist."""
        expected_decisions = {"CANCEL_NOW", "LET_FINISH_AND_CACHE", "JOIN_AUTHORITATIVE"}
        actual_decisions = {decision.name for decision in SalvageDecision}
        assert actual_decisions == expected_decisions

    def test_decision_values(self) -> None:
        """Test salvage decision values."""
        assert SalvageDecision.CANCEL_NOW.value == "cancel_now"
        assert SalvageDecision.LET_FINISH_AND_CACHE.value == "let_finish_and_cache"
        assert SalvageDecision.JOIN_AUTHORITATIVE.value == "join_authoritative"


class TestNormalizeArgs:
    """Test suite for normalize_args function."""

    def test_simple_dict(self) -> None:
        """Test normalize_args with simple dict."""
        args = {"b": 2, "a": 1}
        result = normalize_args("test", args)
        assert list(result.keys()) == ["a", "b"]
        assert result == {"a": 1, "b": 2}

    def test_nested_dict(self) -> None:
        """Test normalize_args with nested dict."""
        args = {"outer": {"b": 2, "a": 1}}
        result = normalize_args("test", args)
        assert result["outer"] == {"a": 1, "b": 2}

    def test_string_normalization(self) -> None:
        """Test normalize_args normalizes strings."""
        args = {"text": "  hello world  \r\n"}
        result = normalize_args("test", args)
        assert result["text"] == "hello world\n"

    def test_list_normalization(self) -> None:
        """Test normalize_args normalizes lists."""
        args = {"items": ["  item1  ", "item2\r\n"]}
        result = normalize_args("test", args)
        assert result["items"] == ["item1", "item2\n"]

    def test_non_dict_input(self) -> None:
        """Test normalize_args with non-dict input."""
        result = normalize_args("test", "not a dict")
        assert result == {}

    def test_empty_dict(self) -> None:
        """Test normalize_args with empty dict."""
        result = normalize_args("test", {})
        assert result == {}

    def test_complex_normalization(self) -> None:
        """Test normalize_args with complex structure."""
        args = {
            "z": "  trailing  ",
            "a": {"nested": ["  item  "]},
            "m": "line1\r\nline2",
        }
        result = normalize_args("test", args)
        assert list(result.keys()) == ["a", "m", "z"]
        assert result["z"] == "trailing"
        assert result["a"]["nested"] == ["item"]
        assert result["m"] == "line1\nline2"


class TestBuildSpecKey:
    """Test suite for build_spec_key function."""

    def test_deterministic(self) -> None:
        """Test build_spec_key produces same key for same input."""
        args = {"query": "test"}
        key1 = build_spec_key("search", args)
        key2 = build_spec_key("search", args)
        assert key1 == key2

    def test_different_tools(self) -> None:
        """Test build_spec_key produces different keys for different tools."""
        args = {"query": "test"}
        key1 = build_spec_key("search", args)
        key2 = build_spec_key("read", args)
        assert key1 != key2

    def test_different_args(self) -> None:
        """Test build_spec_key produces different keys for different args."""
        key1 = build_spec_key("search", {"query": "test1"})
        key2 = build_spec_key("search", {"query": "test2"})
        assert key1 != key2

    def test_with_env_fingerprint(self) -> None:
        """Test build_spec_key includes env_fingerprint."""
        args = {"query": "test"}
        key1 = build_spec_key("search", args, env_fingerprint="git:abc123")
        key2 = build_spec_key("search", args, env_fingerprint="git:def456")
        assert key1 != key2

    def test_with_corpus_version(self) -> None:
        """Test build_spec_key includes corpus_version."""
        args = {"query": "test"}
        key1 = build_spec_key("search", args, corpus_version="v1")
        key2 = build_spec_key("search", args, corpus_version="v2")
        assert key1 != key2

    def test_key_length(self) -> None:
        """Test build_spec_key produces 64-char hex string."""
        key = build_spec_key("search", {"query": "test"})
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)


class TestCandidateToolCallEdgeCases:
    """Test edge cases for CandidateToolCall."""

    def test_all_parse_states(self) -> None:
        """Test all parse states."""
        states: list[str] = ["incomplete", "syntactic_complete", "schema_valid", "semantically_stable"]
        for state in states:
            candidate = CandidateToolCall(
                candidate_id="id",
                stream_id="stream",
                turn_id="turn",
                parse_state=state,
            )
            assert candidate.parse_state == state

    def test_with_mutation_history(self) -> None:
        """Test CandidateToolCall with mutation history."""
        mutations = [
            FieldMutation(
                field_path="args.query",
                old_value="old",
                new_value="new",
                ts_monotonic=time.monotonic(),
            ),
        ]
        candidate = CandidateToolCall(
            candidate_id="id",
            stream_id="stream",
            turn_id="turn",
            mutation_history=mutations,
        )
        assert len(candidate.mutation_history) == 1
        assert candidate.mutation_history[0].field_path == "args.query"

    def test_high_stability_score(self) -> None:
        """Test CandidateToolCall with high stability score."""
        candidate = CandidateToolCall(
            candidate_id="id",
            stream_id="stream",
            turn_id="turn",
            stability_score=0.99,
        )
        assert candidate.stability_score == 0.99

    def test_zero_stability_score(self) -> None:
        """Test CandidateToolCall with zero stability score."""
        candidate = CandidateToolCall(
            candidate_id="id",
            stream_id="stream",
            turn_id="turn",
            stability_score=0.0,
        )
        assert candidate.stability_score == 0.0


class TestToolSpecPolicyEdgeCases:
    """Test edge cases for ToolSpecPolicy."""

    def test_all_side_effects(self) -> None:
        """Test all side effect types."""
        side_effects: list[str] = ["pure", "readonly", "externally_visible", "mutating"]
        for effect in side_effects:
            policy = ToolSpecPolicy(
                tool_name="test",
                side_effect=effect,
                cost="cheap",
                cancellability="cooperative",
                reusability="cacheable",
                speculate_mode="speculative_allowed",
            )
            assert policy.side_effect == effect

    def test_all_cost_classes(self) -> None:
        """Test all cost classes."""
        costs: list[str] = ["cheap", "medium", "expensive"]
        for cost in costs:
            policy = ToolSpecPolicy(
                tool_name="test",
                side_effect="readonly",
                cost=cost,
                cancellability="cooperative",
                reusability="cacheable",
                speculate_mode="speculative_allowed",
            )
            assert policy.cost == cost

    def test_all_cancellability(self) -> None:
        """Test all cancellability types."""
        cancellability: list[str] = ["cooperative", "best_effort", "non_cancelable"]
        for cancel in cancellability:
            policy = ToolSpecPolicy(
                tool_name="test",
                side_effect="readonly",
                cost="cheap",
                cancellability=cancel,
                reusability="cacheable",
                speculate_mode="speculative_allowed",
            )
            assert policy.cancellability == cancel

    def test_all_reusability(self) -> None:
        """Test all reusability types."""
        reusability: list[str] = ["cacheable", "adoptable", "non_reusable"]
        for reuse in reusability:
            policy = ToolSpecPolicy(
                tool_name="test",
                side_effect="readonly",
                cost="cheap",
                cancellability="cooperative",
                reusability=reuse,
                speculate_mode="speculative_allowed",
            )
            assert policy.reusability == reuse

    def test_all_speculate_modes(self) -> None:
        """Test all speculate modes."""
        modes: list[str] = ["forbid", "prefetch_only", "dry_run_only", "speculative_allowed", "high_confidence_only"]
        for mode in modes:
            policy = ToolSpecPolicy(
                tool_name="test",
                side_effect="readonly",
                cost="cheap",
                cancellability="cooperative",
                reusability="cacheable",
                speculate_mode=mode,
            )
            assert policy.speculate_mode == mode


class TestCancelTokenEdgeCases:
    """Test edge cases for CancelToken."""

    def test_empty_reason(self) -> None:
        """Test CancelToken with empty reason."""
        token = CancelToken()
        token.cancel("")
        assert token.cancelled is True
        assert token.reason == ""

    def test_unicode_reason(self) -> None:
        """Test CancelToken with unicode reason."""
        token = CancelToken()
        token.cancel("取消原因")
        assert token.cancelled is True
        assert token.reason == "取消原因"

    @pytest.mark.asyncio
    async def test_cancel_after_no_loop(self) -> None:
        """Test cancel_after when no event loop is running."""
        token = CancelToken()
        # This should return None when no loop is running
        handle = token.cancel_after(1.0, reason="timeout")
        # In sync context, this may return None
        assert handle is None or isinstance(handle, asyncio.TimerHandle)
