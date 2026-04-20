"""Tests for Benchmark Fixtures and FixtureAwareBenchmarkValidator."""

from __future__ import annotations

from typing import Any

import pytest
from polaris.kernelone.context.benchmarks import (
    BenchmarkCase,
    BudgetConditions,
    FixtureAwareBenchmarkValidator,
    load_all_fixtures,
    load_fixture,
)
from polaris.kernelone.context.benchmarks.validators import (
    ValidatorResult,
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
        "episode_store": [],
        "budget_plan": _budget(tokens, max_tokens),
        "updated_at": "2026-04-03T00:00:00Z",
        "pending_followup": None,
    }


# ---------------------------------------------------------------------------
# Fixture Loading Tests
# ---------------------------------------------------------------------------


class TestLoadFixture:
    def test_load_summarize_project(self) -> None:
        case = load_fixture("summarize_project")
        assert case.case_id == "summarize_project"
        assert case.description
        assert case.user_prompt
        assert case.expected_evidence_path
        assert case.score_threshold == 0.70
        assert case.budget_conditions.max_turns == 10
        assert case.budget_conditions.max_tokens == 200000
        assert case.canonical_profile == "canonical_balanced"

    def test_load_locate_bug_root_cause(self) -> None:
        case = load_fixture("locate_bug_root_cause")
        assert case.case_id == "locate_bug_root_cause"
        assert "score_receipt_from_case" in case.user_prompt
        assert case.score_threshold == 0.70

    def test_load_nonexistent_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_fixture("nonexistent_fixture")

    def test_case_to_dict(self) -> None:
        case = load_fixture("summarize_project")
        d = case.to_dict()
        assert d["case_id"] == "summarize_project"
        assert d["score_threshold"] == 0.70
        assert "expected_evidence_path" in d
        assert "budget_conditions" in d


class TestLoadAllFixtures:
    def test_load_all_returns_dict(self) -> None:
        fixtures = load_all_fixtures()
        assert isinstance(fixtures, dict)
        assert len(fixtures) >= 5  # We have 5 fixture files

    def test_all_fixtures_have_required_fields(self) -> None:
        fixtures = load_all_fixtures()
        for case_id, case in fixtures.items():
            assert case.case_id == case_id
            assert case.description
            assert case.user_prompt
            assert isinstance(case.expected_evidence_path, tuple)
            assert 0.0 <= case.score_threshold <= 1.0
            assert case.budget_conditions.max_turns > 0


class TestBudgetConditions:
    def test_from_mapping(self) -> None:
        data = {"max_tokens": 100000, "max_turns": 5, "max_wall_time_seconds": 60.0}
        bc = BudgetConditions.from_mapping(data)
        assert bc.max_tokens == 100000
        assert bc.max_turns == 5
        assert bc.max_wall_time_seconds == 60.0

    def test_from_mapping_defaults(self) -> None:
        bc = BudgetConditions.from_mapping({})
        assert bc.max_tokens == 0
        assert bc.max_turns == 0
        assert bc.max_wall_time_seconds == 0.0


# ---------------------------------------------------------------------------
# FixtureAwareBenchmarkValidator Tests
# ---------------------------------------------------------------------------


class TestFixtureAwareBenchmarkValidator:
    def _v(
        self,
        snapshots: list[dict[str, Any]],
        case: BenchmarkCase | None = None,
    ) -> ValidatorResult:
        return FixtureAwareBenchmarkValidator(benchmark_case=case).validate(snapshots)

    def test_without_fixture_passes_through(self) -> None:
        # Without a fixture, should behave like standard validator
        snapshots = [_snapshot(transcript_count=i + 1, tokens=(i + 1) * 100) for i in range(5)]
        result = self._v(snapshots, case=None)
        assert result.passed
        assert result.validator_name == "FixtureAwareBenchmarkValidator"

    def test_with_fixture_budget_turns_exceeded(self) -> None:
        case = load_fixture("summarize_project")  # max_turns=10
        # Create 15 turns (more than the 10 allowed)
        snapshots = [_snapshot(transcript_count=i + 1, tokens=1000) for i in range(15)]
        result = self._v(snapshots, case=case)
        assert not result.passed
        assert any(v.metric == "budget_max_turns_exceeded" for v in result.violations)

    def test_with_fixture_budget_turns_ok(self) -> None:
        case = load_fixture("summarize_project")  # max_turns=10
        # Create 5 turns (within the 10 allowed)
        # Include all expected evidence paths to avoid evidence violation
        evidence_paths = list(case.expected_evidence_path)
        content = " ".join(evidence_paths)
        snapshots = [
            {
                "version": 1,
                "mode": "state_first_context_os_v1",
                "adapter_id": "generic",
                "transcript_log": [
                    {
                        "event_id": f"evt_{i}",
                        "sequence": i,
                        "role": "assistant" if i % 2 == 1 else "user",
                        "kind": "message",
                        "route": "clear",
                        "content": f"Working on {content}" if i == 1 else f"message {i}",
                    }
                    for i in range(5)
                ],
                "working_state": {},
                "artifact_store": [],
                "episode_store": [],
                "budget_plan": _budget(1000),
                "updated_at": "2026-04-03T00:00:00Z",
                "pending_followup": None,
            }
        ]
        result = self._v(snapshots, case=case)
        assert result.passed

    def test_with_fixture_budget_tokens_exceeded(self) -> None:
        case = load_fixture("summarize_project")  # max_tokens=200000
        # Create snapshot with tokens exceeding limit
        snapshots = [_snapshot(transcript_count=1, tokens=250000)]
        result = self._v(snapshots, case=case)
        assert not result.passed
        assert any(v.metric == "budget_max_tokens_exceeded" for v in result.violations)

    def test_with_fixture_evidence_path_missing(self) -> None:
        case = load_fixture("summarize_project")
        # Create snapshot without any evidence paths touched
        snapshots = [_snapshot(transcript_count=1, tokens=1000)]
        result = self._v(snapshots, case=case)
        assert not result.passed
        assert any(v.metric == "expected_evidence_path_missing" for v in result.violations)

    def test_with_fixture_evidence_path_found_in_transcript(self) -> None:
        case = load_fixture("summarize_project")
        # Create snapshot with ALL expected evidence paths mentioned in transcript
        evidence_paths = list(case.expected_evidence_path)
        content = " ".join(evidence_paths)
        snapshots = [
            {
                "version": 1,
                "mode": "state_first_context_os_v1",
                "adapter_id": "generic",
                "transcript_log": [
                    {
                        "event_id": "evt_0",
                        "sequence": 0,
                        "role": "user",
                        "kind": "message",
                        "route": "clear",
                        "content": f"Working on files: {content}",
                    }
                ],
                "working_state": {},
                "artifact_store": [],
                "episode_store": [],
                "budget_plan": _budget(1000),
                "updated_at": "2026-04-03T00:00:00Z",
                "pending_followup": None,
            }
        ]
        result = self._v(snapshots, case=case)
        # Should pass since all evidence paths are mentioned
        assert result.passed

    def test_score_calculation(self) -> None:
        case = load_fixture("summarize_project")  # threshold=0.70
        snapshots = [_snapshot(transcript_count=1, tokens=1000)]
        result = self._v(snapshots, case=case)
        assert "score" in result.details
        assert "score_threshold" in result.details
        assert result.details["score_threshold"] == 0.70

    def test_with_case_method(self) -> None:
        validator = FixtureAwareBenchmarkValidator()
        case = load_fixture("summarize_project")
        new_validator = validator.with_case(case)
        assert new_validator.benchmark_case == case
        assert new_validator is not validator

    def test_fixture_case_id_in_details(self) -> None:
        case = load_fixture("summarize_project")
        snapshots = [_snapshot(transcript_count=1, tokens=1000)]
        result = self._v(snapshots, case=case)
        assert result.details["fixture_case_id"] == "summarize_project"

    def test_base_validator_passed_tracked(self) -> None:
        case = load_fixture("summarize_project")
        snapshots = [_snapshot(transcript_count=1, tokens=0)]  # zero tokens with transcript = violation
        result = self._v(snapshots, case=case)
        assert "base_validator_passed" in result.details
