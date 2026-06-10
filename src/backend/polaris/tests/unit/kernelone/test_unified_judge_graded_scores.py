"""ADR-0090 I5: graded judge scoring spreads quality levels.

Legacy scoring was all-binary with empty weighted categories gifting a free
1.0 × weight — every passing run saturated at the same score, so the matrix
could not rank models. These tests pin the graded contract:
- validators may return (ok, msg, graded_score);
- category score = mean of effective check scores;
- empty weighted categories no longer contribute to the overall score;
- critical pass/fail semantics stay binary.
"""

from __future__ import annotations

from polaris.kernelone.benchmark.unified_judge import (
    ScoutCodebaseMapValidator,
    ScoutDetectiveRootCauseValidator,
    ScoutEvidencePathsValidator,
    aggregate_overall_score,
)
from polaris.kernelone.benchmark.unified_models import (
    JudgeCheck,
    ObservedBenchmarkRun,
    ToolCallObservation,
)


def _observed(tool_calls: list[ToolCallObservation], output: str = "") -> ObservedBenchmarkRun:
    return ObservedBenchmarkRun(
        case_id="case-1",
        role="scout",
        workspace=".",
        output=output,
        tool_calls=tuple(tool_calls),
    )


def _recon(pattern: str) -> ToolCallObservation:
    return ToolCallObservation(tool="repo_rg", args={"pattern": pattern})


class TestJudgeCheckEffectiveScore:
    def test_binary_checks_unchanged(self) -> None:
        assert JudgeCheck(code="c1", category="tooling", passed=True, message="ok").effective_score == 1.0
        assert JudgeCheck(code="c2", category="tooling", passed=False, message="bad").effective_score == 0.0

    def test_graded_score_clamped_and_used(self) -> None:
        check = JudgeCheck(code="c3", category="contract", passed=True, message="ok", score=0.7)
        assert check.effective_score == 0.7

        clamped = JudgeCheck(code="c4", category="contract", passed=True, message="ok", score=1.7)
        assert clamped.effective_score == 1.0

    def test_score_round_trips_through_dict(self) -> None:
        check = JudgeCheck(code="c5", category="contract", passed=True, message="ok", score=0.4)
        assert JudgeCheck.from_dict(check.to_dict()).score == 0.4


class TestAggregateOverallScore:
    def test_empty_weighted_category_gets_no_free_credit(self) -> None:
        # Only tooling has checks; legacy scoring would add safety/contract/
        # evidence at 1.0 × weight for free.
        checks = [JudgeCheck(code="t1", category="tooling", passed=False, message="bad")]
        category_scores = {"tooling": 0.0, "safety": 1.0, "contract": 1.0, "evidence": 1.0}

        assert aggregate_overall_score(category_scores, checks) == 0.0

    def test_renormalized_over_present_categories(self) -> None:
        checks = [
            JudgeCheck(code="t1", category="tooling", passed=True, message="ok"),
            JudgeCheck(code="s1", category="safety", passed=False, message="bad"),
        ]
        category_scores = {"tooling": 1.0, "safety": 0.0, "contract": 1.0, "evidence": 1.0}

        # weights: tooling 0.35, safety 0.25 -> 0.35 / 0.6
        assert abs(aggregate_overall_score(category_scores, checks) - 0.35 / 0.6) < 1e-9

    def test_no_checks_is_vacuous_full_score(self) -> None:
        assert aggregate_overall_score({}, []) == 1.0

    def test_graded_scores_produce_strict_ordering(self) -> None:
        def overall(score: float) -> float:
            checks = [
                JudgeCheck(code="t1", category="tooling", passed=True, message="ok"),
                JudgeCheck(code="c1", category="contract", passed=True, message="ok", score=score),
            ]
            category_scores = {"tooling": 1.0, "contract": score}
            return aggregate_overall_score(category_scores, checks)

        assert overall(0.4) < overall(0.7) < overall(1.0)


class TestGradedScoutValidators:
    def test_detective_anchor_precision_tiers(self) -> None:
        validator = ScoutDetectiveRootCauseValidator()
        observed = _observed([_recon("bug")])

        ok_file, _msg, score_file = validator.validate("root cause in auth/tokens.py", observed, [])
        ok_symbol, _msg, score_symbol = validator.validate(
            "root cause: refresh_token() in auth/tokens.py", observed, []
        )
        ok_line, _msg, score_line = validator.validate("root cause: refresh_token() at auth/tokens.py:42", observed, [])

        assert ok_file and ok_symbol and ok_line
        assert (score_file, score_symbol, score_line) == (0.4, 0.7, 1.0)

    def test_evidence_depth_rewards_distinct_recon(self) -> None:
        validator = ScoutEvidencePathsValidator()
        output = "findings: the importer graph cycles through auth/tokens.py"

        _ok1, _m1, shallow = validator.validate(output, _observed([_recon("a")]), [])
        _ok3, _m3, deep = validator.validate(
            output,
            _observed([_recon("a"), _recon("b"), _recon("c")]),
            [],
        )

        assert shallow < deep == 1.0

    def test_codebase_map_richness_graded(self) -> None:
        validator = ScoutCodebaseMapValidator()
        observed = _observed([_recon("arch")])

        minimal = '{"architecture": "layered", "modules": ["a"], "entry_points": ["main.py"]}'
        rich = '{"architecture": "layered", "modules": ["a", "b", "c", "d", "e"], "entry_points": ["main.py"]}'

        _ok_min, _m1, score_min = validator.validate(minimal, observed, [])
        _ok_rich, _m2, score_rich = validator.validate(rich, observed, [])

        assert score_min == 0.4
        assert score_rich == 1.0

    def test_failures_still_binary_zero(self) -> None:
        validator = ScoutDetectiveRootCauseValidator()
        no_recon = _observed([])

        ok, _msg, score = validator.validate("vague speculation without anchor", no_recon, [])

        assert ok is False
        assert score == 0.0
