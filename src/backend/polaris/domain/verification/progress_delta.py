"""Progress Delta - Track progress to detect stalled AI loops.

Prevents AI from infinitely looping without making progress.
Compares current state with previous state to detect improvement.

Migrated from: scripts/director/iteration/verification.py
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressDelta:
    """Change in progress between iterations."""

    files_created: int
    missing_targets_reduced: int
    errors_reduced: int
    unresolved_imports_reduced: int
    trend: str  # "improving", "degrading", "stable"
    is_stalled: bool
    stall_rounds: int

    def get_summary(self) -> str:
        """Get human-readable summary."""
        if self.is_stalled:
            return f"STALLED ({self.stall_rounds} rounds)"
        return (
            f"Trend: {self.trend} (+{self.files_created} files, "
            f"-{self.missing_targets_reduced} missing, "
            f"-{self.errors_reduced} errors)"
        )


class ProgressTracker:
    """Tracks progress across iterations to detect stalls."""

    def __init__(self, stall_threshold: int = 2) -> None:
        self.stall_threshold = stall_threshold
        self._previous: dict | None = None
        self._stall_count = 0
        self._round = 0

    def update(
        self,
        files_created: int,
        missing_targets: list[str],
        errors: list[str],
        unresolved_imports: list[str],
    ) -> ProgressDelta:
        """Update progress and compute delta.

        Args:
            files_created: Number of new files created
            missing_targets: Current list of missing target files
            errors: Current list of errors
            unresolved_imports: Current list of unresolved imports

        Returns:
            ProgressDelta with trend analysis
        """
        self._round += 1

        # First iteration - no comparison
        if self._previous is None:
            self._previous = {
                "files_created": files_created,
                "missing_targets": set(missing_targets),
                "errors": set(errors),
                "unresolved_imports": set(unresolved_imports),
            }
            return ProgressDelta(
                files_created=files_created,
                missing_targets_reduced=0,
                errors_reduced=0,
                unresolved_imports_reduced=0,
                trend="initial",
                is_stalled=False,
                stall_rounds=0,
            )

        # Compute changes
        prev = self._previous
        missing_reduced = len(prev["missing_targets"] - set(missing_targets))
        errors_reduced = len(prev["errors"] - set(errors))
        imports_reduced = len(prev["unresolved_imports"] - set(unresolved_imports))

        # Determine trend
        improvements = missing_reduced + errors_reduced + imports_reduced + files_created
        if improvements > 0:
            trend = "improving"
            self._stall_count = 0
        elif files_created < 0 or missing_reduced < 0 or errors_reduced < 0:
            trend = "degrading"
            self._stall_count += 1
        else:
            trend = "stable"
            self._stall_count += 1

        # Check if stalled
        is_stalled = self._stall_count >= self.stall_threshold

        # Update state
        self._previous = {
            "files_created": files_created,
            "missing_targets": set(missing_targets),
            "errors": set(errors),
            "unresolved_imports": set(unresolved_imports),
        }

        return ProgressDelta(
            files_created=files_created,
            missing_targets_reduced=missing_reduced,
            errors_reduced=errors_reduced,
            unresolved_imports_reduced=imports_reduced,
            trend=trend,
            is_stalled=is_stalled,
            stall_rounds=self._stall_count,
        )

    def should_escalate(self) -> bool:
        """Check if we should escalate due to prolonged stall."""
        return self._stall_count >= self.stall_threshold * 2


def compute_progress_delta(
    previous_missing: list[str],
    current_missing: list[str],
    previous_errors: list[str],
    current_errors: list[str],
    files_created: int = 0,
) -> ProgressDelta:
    """Compute progress delta between two states.

    Args:
        previous_missing: Missing targets in previous iteration
        current_missing: Missing targets in current iteration
        previous_errors: Errors in previous iteration
        current_errors: Errors in current iteration
        files_created: Number of files created

    Returns:
        ProgressDelta with trend
    """
    missing_reduced = len(set(previous_missing) - set(current_missing))
    errors_reduced = len(set(previous_errors) - set(current_errors))

    improvements = missing_reduced + errors_reduced + files_created

    if improvements > 0:
        trend = "improving"
        is_stalled = False
    elif improvements < 0:
        trend = "degrading"
        is_stalled = True
    else:
        trend = "stable"
        is_stalled = True

    return ProgressDelta(
        files_created=files_created,
        missing_targets_reduced=missing_reduced,
        errors_reduced=errors_reduced,
        unresolved_imports_reduced=0,  # Not tracked in this simplified version
        trend=trend,
        is_stalled=is_stalled,
        stall_rounds=0,  # Would be tracked by caller
    )


def detect_stall(
    progress_history: list[ProgressDelta],
    threshold: int = 2,
) -> bool:
    """Detect if execution is stalled based on recent history.

    Args:
        progress_history: List of recent progress deltas (oldest first)
        threshold: Number of consecutive non-improving rounds to consider stalled

    Returns:
        True if execution appears stalled
    """
    if len(progress_history) < threshold:
        return False

    # Check last N rounds
    recent = progress_history[-threshold:]
    return all(p.trend != "improving" for p in recent)
