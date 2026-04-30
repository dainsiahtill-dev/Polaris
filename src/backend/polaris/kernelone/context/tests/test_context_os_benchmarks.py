"""Tests for ContextOS Baseline Benchmark Validators."""

from __future__ import annotations

from typing import Any

from polaris.kernelone.context.benchmarks import (
    ContextOSBenchmarkValidator,
    ContextOSDesynchronizationValidator,
    ContextOSIncorrectTruncationValidator,
    ContextOSLongSessionValidator,
    ContextOSLossValidator,
)
from polaris.kernelone.context.benchmarks.validators import (
    ValidatorResult,
    ValidatorViolation,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _budget(
    current_tokens: int,
    max_tokens: int = 128000,
) -> dict[str, Any]:
    return {
        "current_input_tokens": current_tokens,
        "model_context_window": max_tokens,
        "input_budget": max_tokens,
        "output_reserve": 0,
        "tool_reserve": 0,
        "safety_margin": 0,
        "retrieval_budget": 0,
        "soft_limit": 0,
        "hard_limit": max_tokens,
        "emergency_limit": max_tokens,
        "expected_next_input_tokens": 0,
        "p95_tool_result_tokens": 0,
        "planned_retrieval_tokens": 0,
    }


def _transcript(count: int) -> list[dict[str, Any]]:
    return [
        {
            "event_id": f"evt_{i}",
            "sequence": i,
            "role": "user" if i % 2 == 0 else "assistant",
            "kind": "message",
            "route": "clear",
            "content": f"message {i}",
        }
        for i in range(count)
    ]


def _episode(count: int) -> list[dict[str, Any]]:
    return [
        {
            "episode_id": f"ep_{i}",
            "from_sequence": i * 10,
            "to_sequence": (i + 1) * 10 - 1,
            "intent": f"intent {i}",
            "outcome": f"outcome {i}",
        }
        for i in range(count)
    ]


def _snapshot(
    *,
    transcript_count: int = 0,
    tokens: int = 0,
    max_tokens: int = 128000,
    episode_count: int = 0,
) -> dict[str, Any]:
    return {
        "version": 1,
        "mode": "state_first_context_os_v1",
        "adapter_id": "generic",
        "transcript_log": _transcript(transcript_count),
        "working_state": {},
        "artifact_store": [],
        "episode_store": _episode(episode_count),
        "budget_plan": _budget(tokens, max_tokens),
        "updated_at": "2026-04-03T00:00:00Z",
        "pending_followup": None,
    }


# ---------------------------------------------------------------------------
# ContextOSLossValidator Tests
# ---------------------------------------------------------------------------


class TestContextOSLossValidator:
    def _v(self, snapshots: list[dict[str, Any]]) -> ValidatorResult:
        return ContextOSLossValidator().validate(snapshots)

    def test_pass_empty_snapshots(self) -> None:
        result = self._v([])
        assert result.passed
        assert result.validator_name == "ContextOSLossValidator"
        assert result.violations == ()

    def test_pass_valid_snapshot(self) -> None:
        result = self._v([_snapshot(transcript_count=3, tokens=500)])
        assert result.passed
        assert result.violations == ()

    def test_fail_null_budget_plan(self) -> None:
        snap = _snapshot(transcript_count=5, tokens=100)
        snap["budget_plan"] = None
        result = self._v([snap])
        assert not result.passed
        assert len(result.violations) == 1
        assert result.violations[0].metric == "null_budget_plan"

    def test_fail_negative_tokens(self) -> None:
        snap = _snapshot(transcript_count=5, tokens=-10)
        snap["budget_plan"] = _budget(-10)
        result = self._v([snap])
        assert not result.passed
        assert any(v.metric == "negative_tokens" for v in result.violations)

    def test_fail_zero_tokens_with_transcript(self) -> None:
        result = self._v([_snapshot(transcript_count=5, tokens=0)])
        assert not result.passed
        assert any(v.metric == "zero_tokens_with_transcript" for v in result.violations)

    def test_pass_zero_tokens_empty_transcript(self) -> None:
        # Zero tokens with no transcript is acceptable (fresh session)
        result = self._v([_snapshot(transcript_count=0, tokens=0)])
        assert result.passed

    def test_fail_parse_error(self) -> None:
        bad_snapshots: list[Any] = [None]
        result = self._v(bad_snapshots)
        assert not result.passed
        assert result.violations[0].metric == "context_loss"


# ---------------------------------------------------------------------------
# ContextOSLongSessionValidator Tests
# ---------------------------------------------------------------------------


class TestContextOSLongSessionValidator:
    def _v(
        self,
        snapshots: list[dict[str, Any]],
        min_turns: int = 50,
    ) -> ValidatorResult:
        return ContextOSLongSessionValidator(min_turns=min_turns).validate(snapshots)

    def test_pass_too_few_snapshots(self) -> None:
        result = self._v([_snapshot(transcript_count=1, tokens=100)], min_turns=50)
        assert result.passed
        assert "skipped_reason" in result.details

    def test_pass_normal_growth(self) -> None:
        # 100 turns, each adding ~100 tokens — no violation
        snapshots = [_snapshot(transcript_count=i + 1, tokens=(i + 1) * 100) for i in range(55)]
        result = self._v(snapshots, min_turns=50)
        assert result.passed

    def test_fail_unbounded_growth(self) -> None:
        # Tokens triple between turn 10 and 11 — exceeds max_growth_factor=3.0
        snapshots = [_snapshot(transcript_count=i + 1, tokens=(i + 1) * 100) for i in range(55)]
        # Inject a spike at turn 11 (index 10)
        snapshots[10]["budget_plan"] = _budget(20000)  # 200x jump
        result = self._v(snapshots, min_turns=50)
        assert not result.passed
        assert any(v.metric == "unbounded_growth" for v in result.violations)

    def test_fail_context_drop_to_zero(self) -> None:
        # Build 60 non-zero turns then 6 consecutive zero turns
        # (grace=5, so 6 consecutive zeros triggers the violation)
        snapshots = [_snapshot(transcript_count=i + 1, tokens=(i + 1) * 100) for i in range(60)]
        # Append 6 zero-token snapshots (grace=5, gap=6 triggers)
        for _j in range(6):
            snapshots.append(_snapshot(transcript_count=60, tokens=0))
        result = self._v(snapshots, min_turns=50)
        assert not result.passed
        assert any(v.metric == "context_drop_to_zero" for v in result.violations)

    def test_pass_gradual_compaction(self) -> None:
        # Gradual token reduction over 5 turns is fine (within grace)
        snapshots = [_snapshot(transcript_count=50 + i, tokens=max(0, (60 - i) * 1000)) for i in range(60)]
        # Compact gracefully: tokens reduce over 5 turns
        for j in range(5):
            snapshots[50 + j]["budget_plan"] = _budget((10 - j) * 1000)
        result = self._v(snapshots, min_turns=50)
        assert result.passed

    def test_custom_thresholds(self) -> None:
        # With max_growth_factor=10.0, a 5x jump passes
        snapshots = [_snapshot(transcript_count=i + 1, tokens=(i + 1) * 100) for i in range(55)]
        snapshots[11]["budget_plan"] = _budget(5000)  # 5x jump
        result = ContextOSLongSessionValidator(min_turns=50, max_growth_factor=10.0).validate(snapshots)
        assert result.passed


# ---------------------------------------------------------------------------
# ContextOSDesynchronizationValidator Tests
# ---------------------------------------------------------------------------


class TestContextOSDesynchronizationValidator:
    def _v(
        self,
        snapshots: list[dict[str, Any]],
        max_ratio: float = 0.5,
    ) -> ValidatorResult:
        return ContextOSDesynchronizationValidator(
            max_window_to_transcript_ratio=max_ratio,
        ).validate(snapshots)

    def test_pass_single_snapshot(self) -> None:
        result = self._v([_snapshot(transcript_count=5, tokens=1000)])
        assert result.passed
        assert "skipped_reason" in result.details

    def test_pass_normal_sequence(self) -> None:
        snapshots = [_snapshot(transcript_count=i + 1, tokens=(i + 1) * 100) for i in range(5)]
        result = self._v(snapshots)
        assert result.passed

    def test_fail_transcript_shrink_without_compaction(self) -> None:
        # Turn count drops from 5 to 3 with no episode gain
        snapshots = [
            _snapshot(transcript_count=5, tokens=500),
            _snapshot(transcript_count=3, tokens=300),
        ]
        result = self._v(snapshots)
        assert not result.passed
        assert any(v.metric == "transcript_shrink_without_compaction" for v in result.violations)

    def test_pass_transcript_shrink_with_compaction(self) -> None:
        # Turn count drops from 5 to 3 with episode gain = compaction
        snapshots = [
            _snapshot(transcript_count=5, tokens=500, episode_count=0),
            _snapshot(transcript_count=3, tokens=300, episode_count=1),
        ]
        result = self._v(snapshots)
        assert result.passed

    def test_fail_episode_count_decrease(self) -> None:
        snapshots = [
            _snapshot(transcript_count=5, tokens=500, episode_count=2),
            _snapshot(transcript_count=3, tokens=300, episode_count=1),
        ]
        result = self._v(snapshots)
        assert not result.passed
        assert any(v.metric == "episode_count_decrease" for v in result.violations)

    def test_fail_tokens_below_turn_count(self) -> None:
        # tokens < transcript_count is suspicious — need 2+ snapshots
        snapshots = [
            _snapshot(transcript_count=5, tokens=1000),
            _snapshot(transcript_count=10, tokens=5),
        ]
        result = self._v(snapshots)
        assert not result.passed
        assert any(v.metric == "tokens_below_turn_count" for v in result.violations)

    def test_fail_parse_error(self) -> None:
        bad_snapshots: list[Any] = [_snapshot(transcript_count=5, tokens=100), None]
        result = self._v(bad_snapshots)
        assert not result.passed
        assert any(v.metric == "parse_error" for v in result.violations)


# ---------------------------------------------------------------------------
# ContextOSIncorrectTruncationValidator Tests
# ---------------------------------------------------------------------------


class TestContextOSIncorrectTruncationValidator:
    def _v(
        self,
        snapshots: list[dict[str, Any]],
        max_drop_ratio: float = 0.5,
    ) -> ValidatorResult:
        return ContextOSIncorrectTruncationValidator(
            max_single_drop_ratio=max_drop_ratio,
        ).validate(snapshots)

    def test_pass_single_snapshot(self) -> None:
        result = self._v([_snapshot(transcript_count=5, tokens=1000)])
        assert result.passed

    def test_pass_gradual_drop(self) -> None:
        # Tokens drop 30% — within default max_drop_ratio of 50%
        snapshots = [_snapshot(transcript_count=i + 1, tokens=10000 - i * 1000) for i in range(3)]
        result = self._v(snapshots)
        assert result.passed

    def test_fail_large_drop(self) -> None:
        # Tokens drop from 10000 to 1000 = 90% with no episodes created
        # — exceeds max_single_drop_ratio of 50%
        snapshots = [
            _snapshot(transcript_count=10, tokens=10000, episode_count=0),
            _snapshot(transcript_count=0, tokens=1000, episode_count=0),
        ]
        result = self._v(snapshots)
        assert not result.passed
        assert any(v.metric == "full_truncation_no_episodes" for v in result.violations)

    def test_pass_drop_with_episodes(self) -> None:
        # Large drop is OK if episodes were created
        snapshots = [
            _snapshot(transcript_count=20, tokens=10000, episode_count=0),
            _snapshot(transcript_count=3, tokens=1000, episode_count=1),
        ]
        result = self._v(snapshots)
        assert result.passed

    def test_fail_full_truncation_no_episodes(self) -> None:
        # Tokens go to 0 with no episodes created
        snapshots = [
            _snapshot(transcript_count=20, tokens=10000, episode_count=0),
            _snapshot(transcript_count=0, tokens=0, episode_count=0),
        ]
        result = self._v(snapshots)
        assert not result.passed
        assert any(v.metric == "full_truncation_no_episodes" for v in result.violations)


# ---------------------------------------------------------------------------
# ContextOSBenchmarkValidator (Composite) Tests
# ---------------------------------------------------------------------------


class TestContextOSBenchmarkValidator:
    def _v(self, snapshots: list[dict[str, Any]]) -> ValidatorResult:
        return ContextOSBenchmarkValidator().validate(snapshots)

    def test_pass_empty(self) -> None:
        result = self._v([])
        assert result.passed

    def test_pass_valid_session(self) -> None:
        snapshots = [_snapshot(transcript_count=i + 1, tokens=(i + 1) * 100) for i in range(10)]
        result = self._v(snapshots)
        assert result.passed
        assert "sub_results" in result.details
        assert result.details["total_violations"] == 0

    def test_fail_combined(self) -> None:
        # Inject two different violations
        snapshots = [
            _snapshot(transcript_count=1, tokens=0),  # LossValidator: zero_tokens_with_transcript
            _snapshot(transcript_count=1, tokens=1),
            _snapshot(transcript_count=0, tokens=0),  # TruncationValidator: full_truncation_no_episodes
        ]
        result = self._v(snapshots)
        assert not result.passed
        assert result.details["total_violations"] >= 2
        # Check sub-results are present
        assert "ContextOSLossValidator" in result.details["sub_results"]
        assert "ContextOSIncorrectTruncationValidator" in result.details["sub_results"]


# ---------------------------------------------------------------------------
# ValidatorResult / ValidatorViolation Tests
# ---------------------------------------------------------------------------


class TestValidatorResult:
    def test_to_dict(self) -> None:
        violation = ValidatorViolation(
            turn_index=1,
            snapshot_index=1,
            metric="zero_tokens_with_transcript",
            value=0.0,
            threshold=1.0,
            detail="test detail",
        )
        result = ValidatorResult(
            validator_name="TestValidator",
            passed=False,
            violations=(violation,),
            details={"extra": "value"},
        )
        d = result.to_dict()
        assert d["validator_name"] == "TestValidator"
        assert d["passed"] is False
        assert len(d["violations"]) == 1
        assert d["violations"][0]["metric"] == "zero_tokens_with_transcript"
        assert d["details"]["extra"] == "value"
