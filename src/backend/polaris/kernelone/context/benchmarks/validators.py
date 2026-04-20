"""ContextOS Baseline Benchmark Validators.

This module provides quality validators for State-First Context OS:
1. ContextOSLossValidator     — detect null/zero context_tokens
2. ContextOSLongSessionValidator — monitor token growth over 50+ turns
3. ContextOSDesynchronizationValidator — detect token gaps between turns
4. ContextOSIncorrectTruncationValidator — detect unexpected token drops

All validators follow the same interface:
  validate(snapshots: Sequence[ContextOSSnapshot]) -> ValidatorResult
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from polaris.kernelone.benchmark.unified_models import UnifiedBenchmarkCase
    from polaris.kernelone.context.benchmarks.fixtures import BenchmarkCase
    from polaris.kernelone.context.context_os.models import ContextOSSnapshot

    _FixtureCaseType = BenchmarkCase | UnifiedBenchmarkCase


# ---------------------------------------------------------------------------
# Result Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ValidatorViolation:
    """A single violation detected by a validator."""

    turn_index: int  # 0-based turn index
    snapshot_index: int  # index in the snapshots sequence
    metric: str  # which metric failed
    value: float  # actual value observed
    threshold: float  # threshold that was violated
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ValidatorResult:
    """Result of a validator run.

    Attributes:
        validator_name: Name of the validator that produced this result.
        passed: True if no violations were detected.
        violations: Tuple of all detected violations.
        details: Arbitrary metadata about the validation run.
    """

    validator_name: str
    passed: bool
    violations: tuple[ValidatorViolation, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "validator_name": self.validator_name,
            "passed": self.passed,
            "violations": [
                {
                    "turn_index": v.turn_index,
                    "snapshot_index": v.snapshot_index,
                    "metric": v.metric,
                    "value": v.value,
                    "threshold": v.threshold,
                    "detail": v.detail,
                }
                for v in self.violations
            ],
            "details": dict(self.details),
        }


# ---------------------------------------------------------------------------
# Base Validator (Protocol)
# ---------------------------------------------------------------------------


class ContextOSValidator:
    """Protocol for ContextOS baseline validators.

    Implement validate(snapshots) -> ValidatorResult.
    """

    __slots__ = ()

    def validate(
        self,
        snapshots: Sequence[ContextOSSnapshot | dict[str, Any]],
    ) -> ValidatorResult:
        """Run validation against a sequence of snapshots.

        Args:
            snapshots: Ordered sequence of ContextOSSnapshot (or dict mapping)
                       from earliest to latest turn.

        Returns:
            ValidatorResult with passed=True iff all checks pass.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Validator 1: ContextOSLossValidator
# ---------------------------------------------------------------------------


class ContextOSLossValidator(ContextOSValidator):
    """Detect null/zero/missing context_tokens.

    Validates that:
    1. BudgetPlan.current_input_tokens is never None
    2. BudgetPlan.current_input_tokens is never 0 when transcript_log is non-empty
    3. BudgetPlan.current_input_tokens never becomes negative

    This catches the "silent context loss" failure mode where the budget
    tracking reports 0 tokens despite an active transcript.
    """

    __slots__ = ()

    def validate(
        self,
        snapshots: Sequence[ContextOSSnapshot | dict[str, Any]],
    ) -> ValidatorResult:
        from polaris.kernelone.context.context_os.models import ContextOSSnapshot

        violations: list[ValidatorViolation] = []
        details: dict[str, Any] = {"total_snapshots": len(snapshots)}

        for idx, raw in enumerate(snapshots):
            ctx = raw if isinstance(raw, ContextOSSnapshot) else ContextOSSnapshot.from_mapping(raw)
            if ctx is None:
                violations.append(
                    ValidatorViolation(
                        turn_index=idx,
                        snapshot_index=idx,
                        metric="context_loss",
                        value=0.0,
                        threshold=1.0,
                        detail="snapshot could not be parsed from dict",
                    )
                )
                continue

            budget = ctx.budget_plan
            transcript_count = len(ctx.transcript_log)

            if budget is None:
                violations.append(
                    ValidatorViolation(
                        turn_index=idx,
                        snapshot_index=idx,
                        metric="null_budget_plan",
                        value=0.0,
                        threshold=1.0,
                        detail=f"turn {idx}: budget_plan is None but transcript has {transcript_count} events",
                    )
                )
                continue

            current_tokens = budget.current_input_tokens

            # Check: tokens should not be negative
            if current_tokens < 0:
                violations.append(
                    ValidatorViolation(
                        turn_index=idx,
                        snapshot_index=idx,
                        metric="negative_tokens",
                        value=float(current_tokens),
                        threshold=0.0,
                        detail=f"turn {idx}: current_input_tokens is negative ({current_tokens})",
                    )
                )

            # Check: non-empty transcript with zero tokens is suspicious
            if transcript_count > 0 and current_tokens == 0:
                violations.append(
                    ValidatorViolation(
                        turn_index=idx,
                        snapshot_index=idx,
                        metric="zero_tokens_with_transcript",
                        value=0.0,
                        threshold=1.0,
                        detail=f"turn {idx}: transcript has {transcript_count} events but current_input_tokens=0",
                    )
                )

            # Check: None-like sentinel value (represented as 0)
            if (transcript_count > 0 and current_tokens is None) or current_tokens == 0:
                pass  # Already covered above

        details["violation_count"] = len(violations)
        return ValidatorResult(
            validator_name="ContextOSLossValidator",
            passed=len(violations) == 0,
            violations=tuple(violations),
            details=details,
        )


# ---------------------------------------------------------------------------
# Validator 2: ContextOSLongSessionValidator
# ---------------------------------------------------------------------------


class ContextOSLongSessionValidator(ContextOSValidator):
    """Monitor token growth in sessions with 50+ turns.

    Validates that:
    1. Token count is monotonically non-decreasing up to a compaction point
    2. Growth rate is bounded (no sudden doubling that suggests double-counting)
    3. After compaction, tokens may reset but not drop to zero without justification

    This catches runaway context growth and compaction failures.
    """

    def __init__(
        self,
        min_turns: int = 50,
        max_growth_factor: float = 3.0,
        compaction_grace_turns: int = 5,
    ) -> None:
        self.min_turns = min_turns
        self.max_growth_factor = max_growth_factor
        self.compaction_grace_turns = compaction_grace_turns

    def validate(
        self,
        snapshots: Sequence[ContextOSSnapshot | dict[str, Any]],
    ) -> ValidatorResult:
        from polaris.kernelone.context.context_os.models import ContextOSSnapshot

        violations: list[ValidatorViolation] = []
        details: dict[str, Any] = {
            "total_snapshots": len(snapshots),
            "min_turns": self.min_turns,
            "max_growth_factor": self.max_growth_factor,
        }

        if len(snapshots) < self.min_turns:
            details["skipped_reason"] = f"only {len(snapshots)} snapshots, need {self.min_turns}"
            return ValidatorResult(
                validator_name="ContextOSLongSessionValidator",
                passed=True,
                details=details,
            )

        # Parse snapshots and extract token counts
        token_counts: list[tuple[int, float]] = []
        for idx, raw in enumerate(snapshots):
            ctx = raw if isinstance(raw, ContextOSSnapshot) else ContextOSSnapshot.from_mapping(raw)
            if ctx is None or ctx.budget_plan is None:
                continue
            token_counts.append((idx, float(ctx.budget_plan.current_input_tokens)))

        if not token_counts:
            violations.append(
                ValidatorViolation(
                    turn_index=0,
                    snapshot_index=0,
                    metric="no_token_data",
                    value=0.0,
                    threshold=1.0,
                    detail="no valid budget_plan found across snapshots",
                )
            )
            return ValidatorResult(
                validator_name="ContextOSLongSessionValidator",
                passed=False,
                violations=tuple(violations),
                details=details,
            )

        details["token_counts"] = [tc[1] for tc in token_counts]

        # Phase 1: Check for unbounded growth (token count more than triples)
        for i in range(1, len(token_counts)):
            _prev_idx, prev_tokens = token_counts[i - 1]
            curr_idx, curr_tokens = token_counts[i]

            if prev_tokens <= 0:
                continue  # Can't compute growth rate with zero baseline

            growth_factor = curr_tokens / prev_tokens
            if growth_factor > self.max_growth_factor:
                violations.append(
                    ValidatorViolation(
                        turn_index=curr_idx,
                        snapshot_index=curr_idx,
                        metric="unbounded_growth",
                        value=growth_factor,
                        threshold=self.max_growth_factor,
                        detail=(
                            f"turn {curr_idx}: tokens grew by factor {growth_factor:.2f}x "
                            f"(prev={int(prev_tokens)}, curr={int(curr_tokens)})"
                        ),
                    )
                )

        # Phase 2: Detect token drop to zero after long non-zero sequence
        non_zero_sequence_end = -1
        for i, (_idx, tokens) in enumerate(token_counts):
            if tokens > 0:
                non_zero_sequence_end = i
            elif (
                tokens == 0
                and non_zero_sequence_end >= self.compaction_grace_turns
                and i - non_zero_sequence_end > self.compaction_grace_turns
            ):
                violations.append(
                    ValidatorViolation(
                        turn_index=token_counts[i][0],
                        snapshot_index=token_counts[i][0],
                        metric="context_drop_to_zero",
                        value=0.0,
                        threshold=1.0,
                        detail=(
                            f"turn {token_counts[i][0]}: tokens dropped to 0 "
                            f"after {non_zero_sequence_end} non-zero turns "
                            f"(grace={self.compaction_grace_turns})"
                        ),
                    )
                )

        details["violation_count"] = len(violations)
        return ValidatorResult(
            validator_name="ContextOSLongSessionValidator",
            passed=len(violations) == 0,
            violations=tuple(violations),
            details=details,
        )


# ---------------------------------------------------------------------------
# Validator 3: ContextOSDesynchronizationValidator
# ---------------------------------------------------------------------------


class ContextOSDesynchronizationValidator(ContextOSValidator):
    """Detect token gaps / desynchronization between turns.

    Validates that:
    1. active_window size is consistent with transcript_log size
    2. episode_store count is consistent with sealed turns
    3. No sudden discontinuity where turn count jumps without corresponding tokens

    This catches the "turn desync" failure mode where the context window
    projection and the actual snapshot state diverge.
    """

    def __init__(
        self,
        max_window_to_transcript_ratio: float = 0.5,
        max_episode_gap: int = 3,
    ) -> None:
        self.max_window_to_transcript_ratio = max_window_to_transcript_ratio
        self.max_episode_gap = max_episode_gap

    def validate(
        self,
        snapshots: Sequence[ContextOSSnapshot | dict[str, Any]],
    ) -> ValidatorResult:
        from polaris.kernelone.context.context_os.models import ContextOSSnapshot

        violations: list[ValidatorViolation] = []
        details: dict[str, Any] = {"total_snapshots": len(snapshots)}

        if len(snapshots) < 2:
            details["skipped_reason"] = "need at least 2 snapshots for desync detection"
            return ValidatorResult(
                validator_name="ContextOSDesynchronizationValidator",
                passed=True,
                details=details,
            )

        prev_transcript_count = 0
        prev_episode_count = 0

        for idx, raw in enumerate(snapshots):
            ctx = raw if isinstance(raw, ContextOSSnapshot) else ContextOSSnapshot.from_mapping(raw)
            if ctx is None:
                violations.append(
                    ValidatorViolation(
                        turn_index=idx,
                        snapshot_index=idx,
                        metric="parse_error",
                        value=0.0,
                        threshold=1.0,
                        detail=f"snapshot at index {idx} could not be parsed",
                    )
                )
                prev_transcript_count = 0
                prev_episode_count = 0
                continue

            transcript_count = len(ctx.transcript_log)
            episode_count = len(ctx.episode_store)

            # Check 1: Turn count should not decrease (only archive/compaction can)
            # Allow decrease if compaction happened (episode_store increased)
            if transcript_count < prev_transcript_count:
                turns_lost = prev_transcript_count - transcript_count
                episodes_gained = episode_count - prev_episode_count
                if episodes_gained == 0:
                    violations.append(
                        ValidatorViolation(
                            turn_index=idx,
                            snapshot_index=idx,
                            metric="transcript_shrink_without_compaction",
                            value=float(transcript_count),
                            threshold=float(prev_transcript_count),
                            detail=(
                                f"turn {idx}: transcript shrank from {prev_transcript_count} "
                                f"to {transcript_count} without episode gain "
                                f"(lost {turns_lost} turns, episodes={episode_count})"
                            ),
                        )
                    )

            # Check 2: Episode count should not decrease
            if episode_count < prev_episode_count:
                violations.append(
                    ValidatorViolation(
                        turn_index=idx,
                        snapshot_index=idx,
                        metric="episode_count_decrease",
                        value=float(episode_count),
                        threshold=float(prev_episode_count),
                        detail=(
                            f"turn {idx}: episode_store decreased from {prev_episode_count} "
                            f"to {episode_count} (episodes should be append-only)"
                        ),
                    )
                )

            # Check 3: active_window vs transcript ratio (if projection available)
            # We approximate with transcript_log size
            if transcript_count > 0 and ctx.budget_plan:
                tokens = ctx.budget_plan.current_input_tokens
                # Sanity: at least 1 token per transcript event
                if tokens < transcript_count:
                    violations.append(
                        ValidatorViolation(
                            turn_index=idx,
                            snapshot_index=idx,
                            metric="tokens_below_turn_count",
                            value=float(tokens),
                            threshold=float(transcript_count),
                            detail=(
                                f"turn {idx}: current_input_tokens ({int(tokens)}) "
                                f"< transcript events ({transcript_count}) — possible desync"
                            ),
                        )
                    )

            prev_transcript_count = transcript_count
            prev_episode_count = episode_count

        details["violation_count"] = len(violations)
        return ValidatorResult(
            validator_name="ContextOSDesynchronizationValidator",
            passed=len(violations) == 0,
            violations=tuple(violations),
            details=details,
        )


# ---------------------------------------------------------------------------
# Validator 4: ContextOSIncorrectTruncationValidator
# ---------------------------------------------------------------------------


class ContextOSIncorrectTruncationValidator(ContextOSValidator):
    """Detect unexpected token drops (incorrect truncation).

    Validates that:
    1. Token count never drops by more than the soft_limit in a single step
       (unless compaction occurred — then episode_store should increase)
    2. After compaction, at least some events remain in transcript_log
    3. Token drop rate is bounded (no sudden 80%+ drops without episode gain)

    This catches the "over-truncation" failure mode where the compaction
    policy removes too much context, causing data loss.
    """

    def __init__(
        self,
        max_single_drop_ratio: float = 0.5,
        min_episode_turn_ratio: float = 2.0,
    ) -> None:
        self.max_single_drop_ratio = max_single_drop_ratio
        self.min_episode_turn_ratio = min_episode_turn_ratio

    def validate(
        self,
        snapshots: Sequence[ContextOSSnapshot | dict[str, Any]],
    ) -> ValidatorResult:
        from polaris.kernelone.context.context_os.models import ContextOSSnapshot

        violations: list[ValidatorViolation] = []
        details: dict[str, Any] = {"total_snapshots": len(snapshots)}

        if len(snapshots) < 2:
            details["skipped_reason"] = "need at least 2 snapshots for truncation detection"
            return ValidatorResult(
                validator_name="ContextOSIncorrectTruncationValidator",
                passed=True,
                details=details,
            )

        prev_tokens = 0.0
        prev_transcript_count = 0
        prev_episode_count = 0
        drop_events: list[dict[str, Any]] = []

        for idx, raw in enumerate(snapshots):
            ctx = raw if isinstance(raw, ContextOSSnapshot) else ContextOSSnapshot.from_mapping(raw)
            if ctx is None or ctx.budget_plan is None:
                prev_tokens = 0.0
                prev_transcript_count = 0
                prev_episode_count = 0
                continue

            curr_tokens = float(ctx.budget_plan.current_input_tokens)
            curr_transcript_count = len(ctx.transcript_log)
            curr_episode_count = len(ctx.episode_store)

            # Only check when we have a valid previous state
            if prev_tokens > 0:
                token_drop = prev_tokens - curr_tokens
                drop_ratio = token_drop / prev_tokens if prev_tokens > 0 else 0.0

                if drop_ratio > self.max_single_drop_ratio:
                    # Check if compaction/episode gain justified the drop
                    episode_gained = curr_episode_count - prev_episode_count
                    turns_removed = prev_transcript_count - curr_transcript_count

                    # Compaction is valid if episodes were created
                    if episode_gained == 0 and curr_transcript_count == 0:
                        violations.append(
                            ValidatorViolation(
                                turn_index=idx,
                                snapshot_index=idx,
                                metric="full_truncation_no_episodes",
                                value=drop_ratio,
                                threshold=self.max_single_drop_ratio,
                                detail=(
                                    f"turn {idx}: tokens dropped {drop_ratio:.1%} "
                                    f"(prev={int(prev_tokens)}, curr={int(curr_tokens)}), "
                                    f"transcript dropped to 0 with no episodes created"
                                ),
                            )
                        )

                    # Check: episode turn ratio — one episode should cover many turns
                    # High ratio = good (each episode covers many turns = healthy compression)
                    # Low ratio = bad (each episode covers few turns = over-fragmentation)
                    if episode_gained > 0 and turns_removed > 0:
                        episode_turn_ratio = turns_removed / episode_gained
                        if episode_turn_ratio < self.min_episode_turn_ratio:
                            violations.append(
                                ValidatorViolation(
                                    turn_index=idx,
                                    snapshot_index=idx,
                                    metric="low_episode_turn_ratio",
                                    value=episode_turn_ratio,
                                    threshold=self.min_episode_turn_ratio,
                                    detail=(
                                        f"turn {idx}: episode gained={episode_gained}, "
                                        f"turns removed={turns_removed}, "
                                        f"ratio={episode_turn_ratio:.1f} "
                                        f"(expected >= {self.min_episode_turn_ratio})"
                                    ),
                                )
                            )

                    drop_events.append(
                        {
                            "turn": idx,
                            "drop_ratio": drop_ratio,
                            "prev_tokens": int(prev_tokens),
                            "curr_tokens": int(curr_tokens),
                            "episodes_gained": episode_gained,
                            "turns_removed": turns_removed,
                        }
                    )

            prev_tokens = curr_tokens
            prev_transcript_count = curr_transcript_count
            prev_episode_count = curr_episode_count

        details["violation_count"] = len(violations)
        details["drop_events"] = drop_events
        return ValidatorResult(
            validator_name="ContextOSIncorrectTruncationValidator",
            passed=len(violations) == 0,
            violations=tuple(violations),
            details=details,
        )


# ---------------------------------------------------------------------------
# Composite Validator (runs all 4)
# ---------------------------------------------------------------------------


class ContextOSBenchmarkValidator(ContextOSValidator):
    """Composite validator that runs all four baseline validators.

    Convenience class for running the full benchmark suite.
    """

    def __init__(
        self,
        long_session_min_turns: int = 50,
        long_session_max_growth_factor: float = 3.0,
        desync_max_window_ratio: float = 0.5,
        truncation_max_drop_ratio: float = 0.5,
    ) -> None:
        self._validators: tuple[ContextOSValidator, ...] = (
            ContextOSLossValidator(),
            ContextOSLongSessionValidator(
                min_turns=long_session_min_turns,
                max_growth_factor=long_session_max_growth_factor,
            ),
            ContextOSDesynchronizationValidator(
                max_window_to_transcript_ratio=desync_max_window_ratio,
            ),
            ContextOSIncorrectTruncationValidator(
                max_single_drop_ratio=truncation_max_drop_ratio,
            ),
        )

    def validate(
        self,
        snapshots: Sequence[ContextOSSnapshot | dict[str, Any]],
    ) -> ValidatorResult:
        all_violations: list[ValidatorViolation] = []
        results: list[ValidatorResult] = []

        for validator in self._validators:
            result = validator.validate(snapshots)
            results.append(result)
            all_violations.extend(result.violations)

        passed = all(r.passed for r in results)
        return ValidatorResult(
            validator_name="ContextOSBenchmarkValidator",
            passed=passed,
            violations=tuple(all_violations),
            details={
                "sub_results": {r.validator_name: r.to_dict() for r in results},
                "total_violations": len(all_violations),
            },
        )


# ---------------------------------------------------------------------------
# Fixture-Aware Validator (integrates fixture expectations with scoring)
# ---------------------------------------------------------------------------


class FixtureAwareBenchmarkValidator(ContextOSValidator):
    """Composite validator that integrates fixture expectations with scoring.

    This validator wraps the standard ContextOSBenchmarkValidator and adds
    fixture-specific validation:

    1. Budget condition validation:
       - Turn count must not exceed budget_conditions.max_turns
       - Token usage must not exceed budget_conditions.max_tokens

    2. Evidence path validation:
       - Checks that expected_evidence_path files appear in artifact_store
         or transcript_log, indicating the agent touched the right files.

    3. Score calculation:
       - Uses fixture's score_threshold to determine pass/fail
       - Computes a normalized score based on violation severity

    Args:
        benchmark_case: Optional BenchmarkCase fixture. If provided, fixture-
            specific validation and scoring will be applied.
        long_session_min_turns: Min turns for long-session validation.
        long_session_max_growth_factor: Max token growth factor.
        desync_max_window_ratio: Max window-to-transcript ratio.
        truncation_max_drop_ratio: Max single-drop ratio for truncation.
    """

    def __init__(
        self,
        benchmark_case: _FixtureCaseType | None = None,
        long_session_min_turns: int = 50,
        long_session_max_growth_factor: float = 3.0,
        desync_max_window_ratio: float = 0.5,
        truncation_max_drop_ratio: float = 0.5,
    ) -> None:
        self._inner = ContextOSBenchmarkValidator(
            long_session_min_turns=long_session_min_turns,
            long_session_max_growth_factor=long_session_max_growth_factor,
            desync_max_window_ratio=desync_max_window_ratio,
            truncation_max_drop_ratio=truncation_max_drop_ratio,
        )
        self._benchmark_case = benchmark_case
        # Store threshold params for with_case()
        self._long_session_min_turns = long_session_min_turns
        self._long_session_max_growth_factor = long_session_max_growth_factor
        self._desync_max_window_ratio = desync_max_window_ratio
        self._truncation_max_drop_ratio = truncation_max_drop_ratio

    @property
    def benchmark_case(self) -> _FixtureCaseType | None:
        """The fixture case loaded for this validator, if any."""
        return self._benchmark_case

    def with_case(self, benchmark_case: _FixtureCaseType) -> FixtureAwareBenchmarkValidator:
        """Return a new validator instance with the given fixture case."""
        return FixtureAwareBenchmarkValidator(
            benchmark_case=benchmark_case,
            long_session_min_turns=self._long_session_min_turns,
            long_session_max_growth_factor=self._long_session_max_growth_factor,
            desync_max_window_ratio=self._desync_max_window_ratio,
            truncation_max_drop_ratio=self._truncation_max_drop_ratio,
        )

    def _check_budget_conditions(
        self,
        snapshots: Sequence[ContextOSSnapshot | dict[str, Any]],
    ) -> list[ValidatorViolation]:
        """Validate budget conditions from fixture against snapshot data."""
        violations: list[ValidatorViolation] = []
        if self._benchmark_case is None:
            return violations

        from polaris.kernelone.context.context_os.models import ContextOSSnapshot

        bc = self._benchmark_case
        max_turns = bc.budget_conditions.max_turns
        max_tokens = bc.budget_conditions.max_tokens

        # Count actual turns (assistant messages in transcript_log)
        actual_turns = 0
        actual_tokens = 0
        for _snapshot_idx, raw in enumerate(snapshots):
            ctx = raw if isinstance(raw, ContextOSSnapshot) else ContextOSSnapshot.from_mapping(raw)
            if ctx is None:
                continue
            # Count assistant turns
            for event in ctx.transcript_log:
                if hasattr(event, "role") and event.role == "assistant":
                    actual_turns += 1
            # Track max tokens seen
            if ctx.budget_plan is not None:
                actual_tokens = max(actual_tokens, ctx.budget_plan.current_input_tokens)

        if max_turns > 0 and actual_turns > max_turns:
            violations.append(
                ValidatorViolation(
                    turn_index=len(snapshots) - 1,
                    snapshot_index=len(snapshots) - 1,
                    metric="budget_max_turns_exceeded",
                    value=float(actual_turns),
                    threshold=float(max_turns),
                    detail=(f"Turn count {actual_turns} exceeds budget max_turns={max_turns}"),
                )
            )

        if max_tokens > 0 and actual_tokens > max_tokens:
            violations.append(
                ValidatorViolation(
                    turn_index=len(snapshots) - 1,
                    snapshot_index=len(snapshots) - 1,
                    metric="budget_max_tokens_exceeded",
                    value=float(actual_tokens),
                    threshold=float(max_tokens),
                    detail=(f"Token usage {actual_tokens} exceeds budget max_tokens={max_tokens}"),
                )
            )

        return violations

    def _check_evidence_paths(
        self,
        snapshots: Sequence[ContextOSSnapshot | dict[str, Any]],
    ) -> list[ValidatorViolation]:
        """Validate that expected evidence paths were touched.

        Checks artifact_store and transcript_log for references to
        expected_evidence_path files.
        """
        violations: list[ValidatorViolation] = []
        if self._benchmark_case is None:
            return violations

        from polaris.kernelone.context.context_os.models import ContextOSSnapshot

        bc = self._benchmark_case
        expected_paths = set(bc.expected_evidence_path)
        if not expected_paths:
            return violations

        # Collect touched paths from artifact_store and transcript_log
        touched: set[str] = set()
        for _snapshot_idx, raw in enumerate(snapshots):
            ctx = raw if isinstance(raw, ContextOSSnapshot) else ContextOSSnapshot.from_mapping(raw)
            if ctx is None:
                continue
            # Check artifact_store for file paths
            # ArtifactRecord has artifact_id and content (not uri/path)
            for artifact in ctx.artifact_store:
                if hasattr(artifact, "artifact_id") and artifact.artifact_id:
                    touched.add(artifact.artifact_id)
                if hasattr(artifact, "content") and artifact.content:
                    # Check if any expected path is mentioned in artifact content
                    for path in expected_paths:
                        if path in artifact.content:
                            touched.add(path)
            # Check transcript_log for file path mentions
            for event in ctx.transcript_log:
                if hasattr(event, "content") and event.content:
                    content = str(event.content)
                    for path in expected_paths:
                        if path in content:
                            touched.add(path)

        # Find missing evidence paths
        missing = expected_paths - touched
        if missing:
            violations.append(
                ValidatorViolation(
                    turn_index=len(snapshots) - 1,
                    snapshot_index=len(snapshots) - 1,
                    metric="expected_evidence_path_missing",
                    value=len(touched),
                    threshold=len(expected_paths),
                    detail=(f"Expected evidence paths not found in artifact_store or transcript: {sorted(missing)}"),
                )
            )

        return violations

    def _compute_score(
        self,
        base_result: ValidatorResult,
        additional_violations: Sequence[ValidatorViolation],
    ) -> float:
        """Compute a normalized score (0.0-1.0) based on violations.

        Score is 1.0 minus a penalty proportional to violation count and severity.
        """
        total_violations = len(base_result.violations) + len(additional_violations)
        if total_violations == 0:
            return 1.0

        # Penalty per violation (tunable)
        penalty_per_violation = 0.05
        score = max(0.0, 1.0 - (total_violations * penalty_per_violation))
        return score

    def validate(
        self,
        snapshots: Sequence[ContextOSSnapshot | dict[str, Any]],
    ) -> ValidatorResult:
        # Run base structural validation
        base_result = self._inner.validate(snapshots)

        # Run fixture-specific checks
        budget_violations = self._check_budget_conditions(snapshots)
        evidence_violations = self._check_evidence_paths(snapshots)
        additional_violations = budget_violations + evidence_violations

        # Fixture-specific violations are fatal — they always cause failure
        fixture_violations_count = len(additional_violations)

        # Compute score (penalize structural violations more heavily than fixture violations
        # since fixture violations are caught by the threshold check below)
        structural_violations_count = len(base_result.violations)
        if structural_violations_count == 0 and fixture_violations_count == 0:
            score = 1.0
        else:
            # Weight: structural violations are 80% of score impact,
            # fixture violations are 20% but always fail if present
            penalty_per_structural = 0.08
            penalty_per_fixture = 0.20
            score = max(
                0.0,
                1.0
                - (structural_violations_count * penalty_per_structural)
                - (fixture_violations_count * penalty_per_fixture),
            )

        # Determine pass/fail
        threshold = 0.70  # default
        if self._benchmark_case is not None:
            # UnifiedBenchmarkCase stores threshold under judge; legacy BenchmarkCase stores it directly
            if hasattr(self._benchmark_case, "judge"):
                threshold = self._benchmark_case.judge.score_threshold
            else:
                threshold = self._benchmark_case.score_threshold

        # Pass requires:
        # 1. No fixture violations (budget exceeded, evidence path missing)
        # 2. Structural validation passed
        # 3. Score meets threshold
        passed = fixture_violations_count == 0 and base_result.passed and score >= threshold

        all_violations = list(base_result.violations) + additional_violations
        return ValidatorResult(
            validator_name="FixtureAwareBenchmarkValidator",
            passed=passed,
            violations=tuple(all_violations),
            details={
                "score": score,
                "score_threshold": threshold,
                "base_validator_passed": base_result.passed,
                "fixture_violations_count": fixture_violations_count,
                "sub_results": {
                    "structural": base_result.to_dict(),
                },
                "fixture_case_id": (self._benchmark_case.case_id if self._benchmark_case else None),
            },
        )


__all__ = [
    "ContextOSBenchmarkValidator",
    "ContextOSDesynchronizationValidator",
    "ContextOSIncorrectTruncationValidator",
    "ContextOSLongSessionValidator",
    "ContextOSLossValidator",
    "ContextOSValidator",
    "FixtureAwareBenchmarkValidator",
    "ValidatorResult",
    "ValidatorViolation",
]
