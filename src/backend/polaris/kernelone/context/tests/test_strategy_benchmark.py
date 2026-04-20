"""Tests for polaris.kernelone.context.strategy_benchmark."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from polaris.kernelone.context.strategy_benchmark import (
    BenchmarkCase,
    BudgetConditions,
    ShadowComparator,
    StrategyBenchmark,
    _receipt_matches_case,
)
from polaris.kernelone.context.strategy_contracts import Scorecard
from polaris.kernelone.context.strategy_scoring import (
    CANONICAL_WEIGHTING,
    compute_overall,
    score_diff,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestBudgetConditions:
    def test_defaults(self) -> None:
        bc = BudgetConditions()
        assert bc.max_tokens == 200_000
        assert bc.max_turns == 10
        assert bc.max_wall_time_seconds == 300.0

    def test_custom_values(self) -> None:
        bc = BudgetConditions(max_tokens=100_000, max_turns=5, max_wall_time_seconds=60.0)
        assert bc.max_tokens == 100_000
        assert bc.max_turns == 5
        assert bc.max_wall_time_seconds == 60.0

    def test_round_trip(self) -> None:
        bc = BudgetConditions(max_tokens=50_000, max_turns=3, max_wall_time_seconds=30.0)
        restored = BudgetConditions.from_dict(bc.to_dict())
        assert restored.max_tokens == bc.max_tokens
        assert restored.max_turns == bc.max_turns
        assert restored.max_wall_time_seconds == bc.max_wall_time_seconds


class TestBenchmarkCase:
    def test_defaults(self) -> None:
        c = BenchmarkCase(case_id="test", description="desc", user_prompt="prompt")
        assert c.case_id == "test"
        assert c.canonical_profile == "canonical_balanced"
        assert c.score_threshold == 0.70
        assert c.budget_conditions == BudgetConditions()

    def test_round_trip(self) -> None:
        bc = BudgetConditions(max_tokens=80_000)
        c = BenchmarkCase(
            case_id="my_case",
            description="A test case",
            user_prompt="Fix the bug",
            workspace_fixture="workspace/",
            expected_evidence_path=("file1.py", "file2.py"),
            expected_answer_shape="edit",
            budget_conditions=bc,
            canonical_profile="deep_research",
            score_threshold=0.75,
        )
        restored = BenchmarkCase.from_dict(c.to_dict())
        assert restored.case_id == c.case_id
        assert restored.expected_answer_shape == c.expected_answer_shape
        assert restored.canonical_profile == "deep_research"
        assert restored.score_threshold == 0.75
        assert restored.expected_evidence_path == ("file1.py", "file2.py")


class TestScorecard:
    def test_canonical_weighting_values(self) -> None:
        assert abs(sum(CANONICAL_WEIGHTING.values()) - 1.0) < 1e-9
        assert CANONICAL_WEIGHTING["quality_score"] == 0.35
        assert CANONICAL_WEIGHTING["efficiency_score"] == 0.20
        assert CANONICAL_WEIGHTING["context_score"] == 0.20
        assert CANONICAL_WEIGHTING["latency_score"] == 0.15
        assert CANONICAL_WEIGHTING["cost_score"] == 0.10

    def test_compute_overall(self) -> None:
        s = Scorecard(
            quality_score=1.0,
            efficiency_score=1.0,
            context_score=1.0,
            latency_score=1.0,
            cost_score=1.0,
            overall_score=0.0,
        )
        assert compute_overall(s) == pytest.approx(1.0)

    def test_compute_overall_with_zeros(self) -> None:
        s = Scorecard(
            quality_score=0.0,
            efficiency_score=0.0,
            context_score=0.0,
            latency_score=0.0,
            cost_score=0.0,
            overall_score=0.0,
        )
        assert compute_overall(s) == pytest.approx(0.0)

    def test_compute_overall_mixed(self) -> None:
        s = Scorecard(
            quality_score=1.0,
            efficiency_score=0.5,
            context_score=0.0,
            latency_score=0.5,
            cost_score=1.0,
            overall_score=0.0,
        )
        result = compute_overall(s)
        # 1.0*0.35 + 0.5*0.20 + 0.0*0.20 + 0.5*0.15 + 1.0*0.10
        expected = 0.35 + 0.10 + 0.0 + 0.075 + 0.10
        assert result == pytest.approx(expected)

    def test_score_diff(self) -> None:
        a = Scorecard(
            quality_score=0.8,
            efficiency_score=0.6,
            context_score=0.7,
            latency_score=0.9,
            cost_score=0.5,
            overall_score=0.0,
        )
        b = Scorecard(
            quality_score=0.9,
            efficiency_score=0.6,
            context_score=0.8,
            latency_score=0.7,
            cost_score=0.6,
            overall_score=0.0,
        )
        d = score_diff(a, b)
        assert d["quality_delta"] == pytest.approx(0.1)
        assert d["efficiency_delta"] == pytest.approx(0.0)
        assert d["context_delta"] == pytest.approx(0.1)
        assert d["latency_delta"] == pytest.approx(-0.2)
        assert d["cost_delta"] == pytest.approx(0.1)


class TestStrategyBenchmark:
    def test_load_suite_empty_dir(self, tmp_path: Path) -> None:
        StrategyBenchmark(workspace=tmp_path)
        cases = StrategyBenchmark.load_suite(tmp_path)
        assert cases == []

    def test_load_case_from_file_roundtrip(self, tmp_path: Path) -> None:
        import json

        fixture = {
            "case_id": "test_roundtrip",
            "description": "Testing roundtrip",
            "user_prompt": "Do the thing",
            "workspace_fixture": "workspace",
            "expected_evidence_path": [],
            "expected_answer_shape": "answer",
            "budget_conditions": {"max_tokens": 100000, "max_turns": 5},
            "canonical_profile": "canonical_balanced",
            "score_threshold": 0.80,
        }
        p = tmp_path / "test.json"
        p.write_text(json.dumps(fixture), encoding="utf-8")

        harness = StrategyBenchmark(workspace=tmp_path)
        case = harness.load_case_from_file(p)
        assert case.case_id == "test_roundtrip"
        assert case.budget_conditions.max_tokens == 100000

    def test_run_case_unknown_profile(self, tmp_path: Path) -> None:
        harness = StrategyBenchmark(workspace=tmp_path)
        case = BenchmarkCase(
            case_id="test",
            description="desc",
            user_prompt="prompt",
        )
        result = harness.run_case(case, "nonexistent_profile")
        assert result.error is not None
        assert "nonexistent_profile" in result.error

    def test_run_suite_single_case(self, tmp_path: Path) -> None:
        harness = StrategyBenchmark(workspace=tmp_path)
        cases = [
            BenchmarkCase(
                case_id="case1",
                description="desc",
                user_prompt="prompt",
                score_threshold=0.0,  # very low so synthetic scoring always passes
            ),
        ]
        summary = harness.run_suite(cases, "canonical_balanced")
        assert summary.total_cases == 1
        assert summary.passed_cases == 1
        assert summary.failed_cases == 0
        assert 0.0 <= summary.overall_score_avg <= 1.0

    def test_run_suite_empty(self, tmp_path: Path) -> None:
        harness = StrategyBenchmark(workspace=tmp_path)
        summary = harness.run_suite([], "canonical_balanced")
        assert summary.total_cases == 0
        assert summary.overall_score_avg == 0.0

    def test_compare_profiles(self, tmp_path: Path) -> None:
        harness = StrategyBenchmark(workspace=tmp_path)
        cases = [
            BenchmarkCase(case_id="c1", description="d", user_prompt="p", score_threshold=0.0),
            BenchmarkCase(case_id="c2", description="d", user_prompt="p", score_threshold=0.0),
        ]
        comparison = harness.compare_profiles("canonical_balanced", "deep_research", cases)
        assert comparison.baseline_profile == "canonical_balanced"
        assert comparison.challenger_profile == "deep_research"
        assert comparison.case_count == 2
        # In offline mode both profiles get identical synthetic scores, so it's a tie
        assert comparison.winner in ("baseline", "challenger", "tie")

    def test_result_summary_pass_rate(self, tmp_path: Path) -> None:
        harness = StrategyBenchmark(workspace=tmp_path)
        cases = [
            BenchmarkCase(case_id="c1", description="d", user_prompt="p", score_threshold=0.0),
            BenchmarkCase(case_id="c2", description="d", user_prompt="p", score_threshold=0.0),
        ]
        summary = harness.run_suite(cases, "canonical_balanced")
        assert summary.total_cases == 2
        # pass_rate is passed/total (fixed from buggy failed/total)
        assert summary.pass_rate == 1.0  # passed/total = 2/2


class TestShadowComparator:
    def test_run_shadow(self, tmp_path: Path) -> None:
        comp = ShadowComparator(workspace=tmp_path)
        case = BenchmarkCase(
            case_id="shadow_test",
            description="Testing shadow diff",
            user_prompt="Do something",
            score_threshold=0.0,
        )
        diff = comp.run_shadow(case, "canonical_balanced", "claude_like_dynamic")
        assert diff.primary_profile == "canonical_balanced"
        assert diff.shadow_profile == "claude_like_dynamic"
        assert diff.case_id == "shadow_test"
        assert diff.winner in ("primary", "shadow", "tie")
        # Overall delta is shadow - primary
        assert diff.overall_delta == pytest.approx(diff.shadow_scores.overall_score - diff.primary_scores.overall_score)

    def test_compare_all_shadow(self, tmp_path: Path) -> None:
        comp = ShadowComparator(workspace=tmp_path)
        cases = [
            BenchmarkCase(case_id="s1", description="d", user_prompt="p", score_threshold=0.0),
            BenchmarkCase(case_id="s2", description="d", user_prompt="p", score_threshold=0.0),
        ]
        diffs = comp.compare_all_shadow(cases, "canonical_balanced", "deep_research")
        assert len(diffs) == 2
        assert diffs[0].case_id == "s1"
        assert diffs[1].case_id == "s2"


class TestBuiltinCases:
    def test_fixtures_load(self) -> None:
        """Verify that builtin fixtures are loadable."""
        import polaris.kernelone.context.strategy_benchmark as bm

        cases = bm.BUILTIN_CASES
        assert len(cases) == 5
        case_ids = {c.case_id for c in cases}
        expected = {
            "summarize_project",
            "locate_bug_root_cause",
            "edit_targeted_symbol",
            "cross_file_refactor",
            "resume_long_session",
        }
        assert case_ids == expected

    def test_all_cases_have_valid_shapes(self) -> None:
        import polaris.kernelone.context.strategy_benchmark as bm

        valid_shapes = {"edit", "summary", "diagnosis", "answer", "refactor_plan"}
        for case in bm.BUILTIN_CASES:
            assert case.expected_answer_shape in valid_shapes
            assert 0.0 <= case.score_threshold <= 1.0
            assert case.canonical_profile in {
                "canonical_balanced",
                "speed_first",
                "deep_research",
                "cost_guarded",
                "claude_like_dynamic",
            }

    def test_all_cases_have_descriptive_budget(self) -> None:
        import polaris.kernelone.context.strategy_benchmark as bm

        for case in bm.BUILTIN_CASES:
            assert case.budget_conditions.max_tokens > 0
            assert case.budget_conditions.max_turns > 0
            assert case.budget_conditions.max_wall_time_seconds > 0


class TestReceiptMatchesCase:
    """Tests for P2-1: _receipt_matches_case real implementation."""

    def test_no_expected_paths_returns_true(self) -> None:
        """When case has no expected_evidence_path, always returns True."""
        case = BenchmarkCase(case_id="test", description="desc", user_prompt="prompt")
        receipt: dict[str, object] = {}
        assert _receipt_matches_case(receipt, case) is True

    def test_matching_evidence_path(self) -> None:
        """When receipt contains matching evidence paths, returns True."""
        case = BenchmarkCase(
            case_id="test",
            description="desc",
            user_prompt="prompt",
            expected_evidence_path=("src/main.py", "src/utils.py"),
        )
        receipt: dict[str, object] = {
            "read_escalations": [
                {"asset_key": "src/main.py"},
                {"asset_key": "src/utils.py"},
            ],
        }
        assert _receipt_matches_case(receipt, case) is True

    def test_missing_evidence_path_returns_false(self) -> None:
        """When receipt is missing expected evidence path, returns False."""
        case = BenchmarkCase(
            case_id="test",
            description="desc",
            user_prompt="prompt",
            expected_evidence_path=("src/main.py", "src/missing.py"),
        )
        receipt: dict[str, object] = {
            "read_escalations": [
                {"asset_key": "src/main.py"},
            ],
        }
        assert _receipt_matches_case(receipt, case) is False

    def test_partial_path_match(self) -> None:
        """Substring matching handles partial paths correctly."""
        case = BenchmarkCase(
            case_id="test",
            description="desc",
            user_prompt="prompt",
            expected_evidence_path=("main.py",),
        )
        # Full path containing the expected path matches
        receipt: dict[str, object] = {
            "read_escalations": [
                {"asset_key": "src/main.py"},
            ],
        }
        assert _receipt_matches_case(receipt, case) is True

    def test_windows_path_normalization(self) -> None:
        """Windows backslash paths are normalized for comparison."""
        case = BenchmarkCase(
            case_id="test",
            description="desc",
            user_prompt="prompt",
            expected_evidence_path=("src/main.py",),
        )
        receipt: dict[str, object] = {
            "read_escalations": [
                {"asset_key": "src\\main.py"},
            ],
        }
        assert _receipt_matches_case(receipt, case) is True

    def test_string_asset_key(self) -> None:
        """String asset keys in read_escalations are handled."""
        case = BenchmarkCase(
            case_id="test",
            description="desc",
            user_prompt="prompt",
            expected_evidence_path=("file.py",),
        )
        receipt: dict[str, object] = {
            "read_escalations": [
                "file.py",  # direct string, not dict
            ],
        }
        assert _receipt_matches_case(receipt, case) is True

    def test_empty_read_escalations_returns_false(self) -> None:
        """Empty read_escalations when paths are expected returns False."""
        case = BenchmarkCase(
            case_id="test",
            description="desc",
            user_prompt="prompt",
            expected_evidence_path=("src/main.py",),
        )
        receipt: dict[str, object] = {
            "read_escalations": [],
        }
        assert _receipt_matches_case(receipt, case) is False

    def test_tool_sequence_not_used_for_path_matching(self) -> None:
        """Tool names in tool_sequence are not mistakenly matched as paths."""
        case = BenchmarkCase(
            case_id="test",
            description="desc",
            user_prompt="prompt",
            expected_evidence_path=("read",),  # "read" is a tool name, not a file
        )
        receipt: dict[str, object] = {
            "read_escalations": [],
            "tool_sequence": ["read", "write"],  # tool names, not file paths
        }
        # "read" is not found as an evidence path (tool_sequence is not used)
        assert _receipt_matches_case(receipt, case) is False
