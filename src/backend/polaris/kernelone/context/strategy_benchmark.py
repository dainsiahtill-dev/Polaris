"""Benchmark harness — offline replay, suite runner, and A/B comparison.

Offline replay evaluates pre-recorded or synthetic benchmark cases against
any registered strategy profile, producing Scorecards and structured results.

Fixtures are versioned alongside this module.

.. deprecated::
    This module is deprecated. Use ``polaris.kernelone.benchmark.unified_models``
    for new benchmark case definitions and ``polaris.kernelone.benchmark.unified_runner``
    for execution. The canonical benchmark framework is now
    ``polaris/kernelone/benchmark/``.

    This module is retained for backward compatibility with existing
    context module consumers and will be removed in a future release.
"""

from __future__ import annotations

import json
import logging
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.kernelone.storage import resolve_runtime_path

from .strategy_profiles import BUILTIN_PROFILES
from .strategy_scoring import compute_overall

if TYPE_CHECKING:
    from .strategy_contracts import Scorecard

__all__ = []

_logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Budget Conditions
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class BudgetConditions:
    """Budget constraints for a benchmark case."""

    max_tokens: int = 200_000
    max_turns: int = 10
    max_wall_time_seconds: float = 300.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_tokens": self.max_tokens,
            "max_turns": self.max_turns,
            "max_wall_time_seconds": self.max_wall_time_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BudgetConditions:
        return cls(
            max_tokens=data.get("max_tokens", 200_000),
            max_turns=data.get("max_turns", 10),
            max_wall_time_seconds=data.get("max_wall_time_seconds", 300.0),
        )


# ------------------------------------------------------------------
# Benchmark Case
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class BenchmarkCase:
    """A single offline benchmark case definition.

    Attributes:
        case_id: Unique identifier (e.g. "summarize_project").
        description: Human-readable description of the scenario.
        user_prompt: The user prompt to replay.
        workspace_fixture: Path or description of the workspace snapshot.
        expected_evidence_path: List of file paths that should have been read.
        expected_answer_shape: Shape of the expected answer
            ("edit", "summary", "diagnosis", "answer", "refactor_plan").
        budget_conditions: Budget constraints for the case.
        canonical_profile: Which profile to use as canonical baseline.
        score_threshold: Minimum acceptable overall_score (0.0–1.0).
    """

    case_id: str
    description: str
    user_prompt: str
    workspace_fixture: str = ""  # fixture path or snapshot description
    expected_evidence_path: tuple[str, ...] = field(default_factory=tuple)
    expected_answer_shape: str = "answer"  # "edit"|"summary"|"diagnosis"|"answer"|"refactor_plan"
    budget_conditions: BudgetConditions = field(default_factory=BudgetConditions)
    canonical_profile: str = "canonical_balanced"
    score_threshold: float = 0.70

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "description": self.description,
            "user_prompt": self.user_prompt,
            "workspace_fixture": self.workspace_fixture,
            "expected_evidence_path": list(self.expected_evidence_path),
            "expected_answer_shape": self.expected_answer_shape,
            "budget_conditions": self.budget_conditions.to_dict(),
            "canonical_profile": self.canonical_profile,
            "score_threshold": self.score_threshold,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BenchmarkCase:
        return cls(
            case_id=data["case_id"],
            description=data.get("description", ""),
            user_prompt=data["user_prompt"],
            workspace_fixture=data.get("workspace_fixture", ""),
            expected_evidence_path=tuple(data.get("expected_evidence_path", [])),
            expected_answer_shape=data.get("expected_answer_shape", "answer"),
            budget_conditions=BudgetConditions.from_dict(data.get("budget_conditions", {})),
            canonical_profile=data.get("canonical_profile", "canonical_balanced"),
            score_threshold=data.get("score_threshold", 0.70),
        )


# ------------------------------------------------------------------
# Benchmark Result
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class BenchmarkResult:
    """Result of running a single benchmark case."""

    case_id: str
    profile_id: str
    scores: Scorecard
    passed: bool
    receipt_id: str = ""
    duration_seconds: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "profile_id": self.profile_id,
            "scores": self.scores.to_dict(),
            "passed": self.passed,
            "receipt_id": self.receipt_id,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
        }


# ------------------------------------------------------------------
# Benchmark Result Summary
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class BenchmarkResultSummary:
    """Summary of running multiple benchmark cases."""

    profile_id: str
    total_cases: int
    passed_cases: int
    failed_cases: int
    results: tuple[BenchmarkResult, ...]
    overall_score_avg: float
    quality_score_avg: float
    efficiency_score_avg: float
    context_score_avg: float
    latency_score_avg: float
    cost_score_avg: float
    wall_time_seconds: float
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def pass_rate(self) -> float:
        if self.total_cases == 0:
            return 0.0
        return self.passed_cases / self.total_cases

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "pass_rate": self.pass_rate,
            "overall_score_avg": self.overall_score_avg,
            "quality_score_avg": self.quality_score_avg,
            "efficiency_score_avg": self.efficiency_score_avg,
            "context_score_avg": self.context_score_avg,
            "latency_score_avg": self.latency_score_avg,
            "cost_score_avg": self.cost_score_avg,
            "wall_time_seconds": self.wall_time_seconds,
            "timestamp": self.timestamp,
            "results": [r.to_dict() for r in self.results],
        }


# ------------------------------------------------------------------
# Profile Comparison
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ProfileComparison:
    """A/B comparison between two profiles across the same benchmark cases."""

    baseline_profile: str
    challenger_profile: str
    case_count: int
    baseline_wins: int
    challenger_wins: int
    ties: int
    baseline_overall_avg: float
    challenger_overall_avg: float
    overall_delta: float  # positive = challenger wins
    quality_delta: float
    efficiency_delta: float
    context_delta: float
    latency_delta: float
    cost_delta: float
    winner: str  # "baseline" | "challenger" | "tie"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_profile": self.baseline_profile,
            "challenger_profile": self.challenger_profile,
            "case_count": self.case_count,
            "baseline_wins": self.baseline_wins,
            "challenger_wins": self.challenger_wins,
            "ties": self.ties,
            "baseline_overall_avg": self.baseline_overall_avg,
            "challenger_overall_avg": self.challenger_overall_avg,
            "overall_delta": self.overall_delta,
            "quality_delta": self.quality_delta,
            "efficiency_delta": self.efficiency_delta,
            "context_delta": self.context_delta,
            "latency_delta": self.latency_delta,
            "cost_delta": self.cost_delta,
            "winner": self.winner,
            "timestamp": self.timestamp,
        }


# ------------------------------------------------------------------
# Shadow Diff
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ShadowDiff:
    """Result of a shadow-mode comparison between two profiles."""

    case_id: str
    primary_profile: str
    shadow_profile: str
    primary_scores: Scorecard
    shadow_scores: Scorecard
    quality_delta: float
    efficiency_delta: float
    context_delta: float
    latency_delta: float
    cost_delta: float
    overall_delta: float  # shadow - primary
    winner: str  # "primary" | "shadow" | "tie"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "primary_profile": self.primary_profile,
            "shadow_profile": self.shadow_profile,
            "primary_scores": self.primary_scores.to_dict(),
            "shadow_scores": self.shadow_scores.to_dict(),
            "quality_delta": self.quality_delta,
            "efficiency_delta": self.efficiency_delta,
            "context_delta": self.context_delta,
            "latency_delta": self.latency_delta,
            "cost_delta": self.cost_delta,
            "overall_delta": self.overall_delta,
            "winner": self.winner,
            "timestamp": self.timestamp,
        }


# ------------------------------------------------------------------
# Score Evaluator (canonical weighting model)
# ------------------------------------------------------------------


def score_receipt_from_case(
    case: BenchmarkCase,
    profile_id: str,
    receipt_data: dict[str, Any] | None = None,
) -> Scorecard:
    """Derive a Scorecard from a benchmark case and optional receipt data.

    In offline replay, receipts may be synthetic (filled from case metadata).
    In online mode, receipts come from real StrategyReceiptEmitter runs.

    This function normalizes both paths into a Scorecard.
    """
    from .strategy_contracts import Scorecard as SC

    # Default scoring based on case properties (offline/synthetic mode)
    quality = 0.85 if case.expected_answer_shape else 0.75
    efficiency = 0.80
    context = 0.80
    latency = 0.85
    cost = 0.80

    if receipt_data is not None:
        # Real receipt: derive from actual metrics
        tool_seq = receipt_data.get("tool_sequence", [])
        cache_hits = receipt_data.get("cache_hits", [])
        cache_misses = receipt_data.get("cache_misses", [])
        receipt_data.get("budget_decisions", [])
        prompt_tokens = receipt_data.get("prompt_tokens_estimate", 0)

        # Efficiency: fewer tool calls is better
        efficiency = max(0.0, 1.0 - len(tool_seq) / (case.budget_conditions.max_turns * 5))

        # Context: closer to expected evidence path is better
        cache_total = len(cache_hits) + len(cache_misses)
        cache_rate = len(cache_hits) / cache_total if cache_total > 0 else 0.5
        context = round(min(1.0, cache_rate + 0.3), 4)

        # Latency: normalized by max wall time (placeholder)
        latency = 0.85

        # Cost: based on token budget usage
        budget_pct = prompt_tokens / case.budget_conditions.max_tokens
        cost = round(max(0.0, 1.0 - budget_pct), 4)

        # Quality: pass/fail based on threshold
        quality = 1.0 if _receipt_matches_case(receipt_data, case) else 0.5

    overall = compute_overall(
        SC(
            quality_score=quality,
            efficiency_score=efficiency,
            context_score=context,
            latency_score=latency,
            cost_score=cost,
            overall_score=0.0,
            bundle_id="kernelone.default.v1",
            profile_id=profile_id,
        )
    )

    return SC(
        quality_score=round(quality, 4),
        efficiency_score=efficiency,
        context_score=context,
        latency_score=latency,
        cost_score=cost,
        overall_score=overall,
        bundle_id="kernelone.default.v1",
        profile_id=profile_id,
        profile_hash="fixture",
        turn_index=0,
    )


def _receipt_matches_case(receipt_data: dict[str, Any], case: BenchmarkCase) -> bool:
    """Check if a receipt's evidence paths match the case's expected paths.

    Compares the case's expected_evidence_path against the actual evidence
    recorded in the receipt (via read_escalations asset keys and tool_sequence).

    Returns True if all expected evidence paths are found in the receipt,
    or if there are no expected paths (no constraints).
    """
    if not case.expected_evidence_path:
        return True  # No constraints means always matching

    # Extract evidence from read_escalations (primary source of file read evidence)
    read_escalations = receipt_data.get("read_escalations", [])
    evidence_keys: set[str] = set()
    for escalation in read_escalations:
        if isinstance(escalation, dict) and "asset_key" in escalation:
            evidence_keys.add(escalation["asset_key"])
        elif isinstance(escalation, str):
            # Some receipt formats may store asset_key directly as string
            evidence_keys.add(escalation)

    # Note: tool_sequence contains tool names (not file paths), so we do NOT
    # add them to evidence_keys to avoid false path matches like "read" matching
    # any file path containing "read".

    # Check if all expected paths have corresponding evidence
    # Use substring matching to handle partial path matches
    expected_set = set(case.expected_evidence_path)
    for expected_path in expected_set:
        path_found = False
        normalized_expected = expected_path.lower().replace("\\", "/")
        for evidence_key in evidence_keys:
            normalized_evidence = evidence_key.lower().replace("\\", "/")
            # Match if evidence contains the expected path or vice versa
            if normalized_expected in normalized_evidence or normalized_evidence in normalized_expected:
                path_found = True
                break
        if not path_found:
            return False

    return True


# ------------------------------------------------------------------
# Strategy Benchmark Harness
# ------------------------------------------------------------------


class StrategyBenchmark:
    """Offline benchmark replay harness.

    Run pre-recorded benchmark cases against a selected strategy profile,
    producing Scorecards and BenchmarkResults.

    Usage::

        harness = StrategyBenchmark(workspace="/path/to/repo")
        results = harness.run_suite(BUILTIN_CASES, profile_id="canonical_balanced")
        for r in results.results:
            print(r.case_id, r.passed, r.scores.overall_score)
    """

    def __init__(self, workspace: str | Path | None = None) -> None:
        self._workspace = Path(workspace) if workspace else Path.cwd()

    # ------------------------------------------------------------------
    # Case loading
    # ------------------------------------------------------------------

    @staticmethod
    def load_case_from_file(path: str | Path) -> BenchmarkCase:
        """Load a BenchmarkCase from a JSON fixture file."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Benchmark fixture not found: {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        return BenchmarkCase.from_dict(data)

    @staticmethod
    def load_suite(fixture_dir: str | Path) -> list[BenchmarkCase]:
        """Load all JSON fixtures from a directory."""
        d = Path(fixture_dir)
        if not d.is_dir():
            raise NotADirectoryError(f"Fixture directory not found: {d}")
        cases: list[BenchmarkCase] = []
        for p in sorted(d.glob("*.json")):
            try:
                cases.append(StrategyBenchmark.load_case_from_file(p))
            except (RuntimeError, ValueError) as exc:
                _logger.warning("Skipping invalid fixture %s: %s", p.name, exc)
        return cases

    # ------------------------------------------------------------------
    # Single case run
    # ------------------------------------------------------------------

    def run_case(
        self,
        case: BenchmarkCase,
        profile_id: str,
    ) -> BenchmarkResult:
        """Run a single benchmark case against the given profile.

        In offline replay mode, scores are derived from case metadata
        and any available receipt data under runtime/strategy_runs/.

        Returns a BenchmarkResult with scores, pass/fail, and timing.
        """
        start = time.monotonic()
        error: str | None = None
        scores: Scorecard | None = None
        receipt_id = ""

        # Resolve profile
        if profile_id not in BUILTIN_PROFILES:
            error = f"Unknown profile: {profile_id}"
            _logger.error(error)
        else:
            # Try to load any matching receipt for this case
            receipt_data: dict[str, Any] | None = None
            try:
                receipt_data = self._find_receipt(case.case_id, profile_id)
            except FileNotFoundError:
                pass  # No receipt: use synthetic scoring

            try:
                scores = score_receipt_from_case(case, profile_id, receipt_data)
                receipt_id = receipt_data.get("run_id", "") if receipt_data else f"synthetic_{case.case_id}"
            except (RuntimeError, ValueError) as exc:
                error = f"Score derivation failed: {exc!s}"
                _logger.error(error)
                # Fallback: return a zero-score result
                from .strategy_contracts import Scorecard

                scores = Scorecard(
                    quality_score=0.0,
                    efficiency_score=0.0,
                    context_score=0.0,
                    latency_score=0.0,
                    cost_score=0.0,
                    overall_score=0.0,
                    profile_id=profile_id,
                )

        duration = time.monotonic() - start
        passed = scores is not None and error is None and scores.overall_score >= case.score_threshold

        return BenchmarkResult(
            case_id=case.case_id,
            profile_id=profile_id,
            scores=scores or _zero_scorecard(profile_id),
            passed=passed,
            receipt_id=receipt_id,
            duration_seconds=round(duration, 3),
            error=error,
        )

    # ------------------------------------------------------------------
    # Suite run
    # ------------------------------------------------------------------

    def run_suite(
        self,
        cases: list[BenchmarkCase],
        profile_id: str,
    ) -> BenchmarkResultSummary:
        """Run all cases in a suite against the given profile."""
        wall_start = time.monotonic()
        results: list[BenchmarkResult] = []
        passed_cases = 0
        failed_cases = 0

        for case in cases:
            r = self.run_case(case, profile_id)
            results.append(r)
            if r.passed:
                passed_cases += 1
            else:
                failed_cases += 1

        wall_time = time.monotonic() - wall_start

        if results:
            overall_avg = _avg([r.scores.overall_score for r in results])
            quality_avg = _avg([r.scores.quality_score for r in results])
            efficiency_avg = _avg([r.scores.efficiency_score for r in results])
            context_avg = _avg([r.scores.context_score for r in results])
            latency_avg = _avg([r.scores.latency_score for r in results])
            cost_avg = _avg([r.scores.cost_score for r in results])
        else:
            overall_avg = quality_avg = efficiency_avg = 0.0
            context_avg = latency_avg = cost_avg = 0.0

        return BenchmarkResultSummary(
            profile_id=profile_id,
            total_cases=len(results),
            passed_cases=passed_cases,
            failed_cases=failed_cases,
            results=tuple(results),
            overall_score_avg=round(overall_avg, 4),
            quality_score_avg=round(quality_avg, 4),
            efficiency_score_avg=round(efficiency_avg, 4),
            context_score_avg=round(context_avg, 4),
            latency_score_avg=round(latency_avg, 4),
            cost_score_avg=round(cost_avg, 4),
            wall_time_seconds=round(wall_time, 3),
        )

    # ------------------------------------------------------------------
    # Profile comparison (A/B)
    # ------------------------------------------------------------------

    def compare_profiles(
        self,
        baseline: str,
        challenger: str,
        cases: list[BenchmarkCase],
    ) -> ProfileComparison:
        """Run the same suite with two profiles and compare results."""
        baseline_summary = self.run_suite(cases, baseline)
        challenger_summary = self.run_suite(cases, challenger)

        baseline_wins = 0
        challenger_wins = 0
        ties = 0

        for br, cr in zip(baseline_summary.results, challenger_summary.results):
            if cr.scores.overall_score > br.scores.overall_score:
                challenger_wins += 1
            elif cr.scores.overall_score < br.scores.overall_score:
                baseline_wins += 1
            else:
                ties += 1

        quality_delta = challenger_summary.quality_score_avg - baseline_summary.quality_score_avg
        efficiency_delta = challenger_summary.efficiency_score_avg - baseline_summary.efficiency_score_avg
        context_delta = challenger_summary.context_score_avg - baseline_summary.context_score_avg
        latency_delta = challenger_summary.latency_score_avg - baseline_summary.latency_score_avg
        cost_delta = challenger_summary.cost_score_avg - baseline_summary.cost_score_avg
        overall_delta = challenger_summary.overall_score_avg - baseline_summary.overall_score_avg

        if overall_delta > 0.001:
            winner = "challenger"
        elif overall_delta < -0.001:
            winner = "baseline"
        else:
            winner = "tie"

        return ProfileComparison(
            baseline_profile=baseline,
            challenger_profile=challenger,
            case_count=len(cases),
            baseline_wins=baseline_wins,
            challenger_wins=challenger_wins,
            ties=ties,
            baseline_overall_avg=baseline_summary.overall_score_avg,
            challenger_overall_avg=challenger_summary.overall_score_avg,
            overall_delta=round(overall_delta, 4),
            quality_delta=round(quality_delta, 4),
            efficiency_delta=round(efficiency_delta, 4),
            context_delta=round(context_delta, 4),
            latency_delta=round(latency_delta, 4),
            cost_delta=round(cost_delta, 4),
            winner=winner,
        )

    # ------------------------------------------------------------------
    # Receipt lookup helpers
    # ------------------------------------------------------------------

    def _find_receipt(self, case_id: str, profile_id: str) -> dict[str, Any]:
        """Find a receipt JSON file for the given case and profile."""
        run_dir = Path(resolve_runtime_path(str(self._workspace), "runtime/strategy_runs"))
        if not run_dir.is_dir():
            raise FileNotFoundError(f"Receipt directory not found: {run_dir}")

        pattern = f"*{case_id}*{profile_id}*.json"
        matches = list(run_dir.glob(pattern))
        if not matches:
            raise FileNotFoundError(f"No receipt found for case={case_id} profile={profile_id} in {run_dir}")
        # Return most recent
        latest = sorted(matches, key=lambda p: p.stat().st_mtime)[-1]
        return json.loads(latest.read_text(encoding="utf-8"))


# ------------------------------------------------------------------
# Shadow Comparator
# ------------------------------------------------------------------


class ShadowComparator:
    """Shadow-mode comparator.

    Runs the canonical (primary) strategy for real and executes a
    challenger strategy in shadow — producing receipts but not affecting
    the user experience.

    The actual shadow execution is a future integration point;
    this class provides the comparison logic once receipts are available.
    """

    def __init__(self, workspace: str | Path | None = None) -> None:
        self._workspace = Path(workspace) if workspace else Path.cwd()
        self._harness = StrategyBenchmark(self._workspace)

    def run_shadow(
        self,
        case: BenchmarkCase,
        primary: str = "canonical_balanced",
        shadow: str = "claude_like_dynamic",
    ) -> ShadowDiff:
        """Compare primary vs shadow profile on a single case.

        Both profiles are evaluated in offline mode (no live LLM required).
        In a live integration, primary would write real receipts and shadow
        would write shadow-only receipts.
        """

        primary_result = self._harness.run_case(case, primary)
        shadow_result = self._harness.run_case(case, shadow)

        ps = primary_result.scores
        ss = shadow_result.scores

        quality_delta = ss.quality_score - ps.quality_score
        efficiency_delta = ss.efficiency_score - ps.efficiency_score
        context_delta = ss.context_score - ps.context_score
        latency_delta = ss.latency_score - ps.latency_score
        cost_delta = ss.cost_score - ps.cost_score
        overall_delta = ss.overall_score - ps.overall_score

        if overall_delta > 0.001:
            winner = "shadow"
        elif overall_delta < -0.001:
            winner = "primary"
        else:
            winner = "tie"

        return ShadowDiff(
            case_id=case.case_id,
            primary_profile=primary,
            shadow_profile=shadow,
            primary_scores=ps,
            shadow_scores=ss,
            quality_delta=round(quality_delta, 4),
            efficiency_delta=round(efficiency_delta, 4),
            context_delta=round(context_delta, 4),
            latency_delta=round(latency_delta, 4),
            cost_delta=round(cost_delta, 4),
            overall_delta=round(overall_delta, 4),
            winner=winner,
        )

    def compare_all_shadow(
        self,
        cases: list[BenchmarkCase],
        primary: str = "canonical_balanced",
        shadow: str = "claude_like_dynamic",
    ) -> list[ShadowDiff]:
        """Run shadow comparison across an entire suite."""
        return [self.run_shadow(c, primary, shadow) for c in cases]


# ------------------------------------------------------------------
# Built-in benchmark cases
# ------------------------------------------------------------------

# Lazy-loaded from fixture files — populated on first access
_BUILTIN_CASES: list[BenchmarkCase] | None = None


def _load_builtin_cases() -> list[BenchmarkCase]:
    """Load built-in fixture cases from the benchmarks/fixtures directory."""
    fixtures_dir = Path(__file__).parent / "benchmarks" / "fixtures"
    if not fixtures_dir.is_dir():
        _logger.warning(
            "Built-in fixtures directory not found at %s. Returning empty case list.",
            fixtures_dir,
        )
        return []
    return StrategyBenchmark.load_suite(fixtures_dir)


def _get_builtin_cases() -> list[BenchmarkCase]:
    global _BUILTIN_CASES
    if _BUILTIN_CASES is None:
        _BUILTIN_CASES = _load_builtin_cases()
    return _BUILTIN_CASES


def __getattr__(name: str) -> list[BenchmarkCase]:
    """Lazy module-level BUILTIN_CASES getter."""
    if name == "BUILTIN_CASES":
        return _get_builtin_cases()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ------------------------------------------------------------------
# Utility
# ------------------------------------------------------------------


def _avg(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _zero_scorecard(profile_id: str) -> Scorecard:
    from .strategy_contracts import Scorecard

    return Scorecard(
        quality_score=0.0,
        efficiency_score=0.0,
        context_score=0.0,
        latency_score=0.0,
        cost_score=0.0,
        overall_score=0.0,
        profile_id=profile_id,
    )
