"""Pattern Analyzer - extracts patterns from session structured_findings."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ExtractedPattern:
    """An extracted pattern from session findings."""

    pattern_type: str  # error_pattern | success_pattern | stagnation_pattern | generic_pattern
    summary: str
    insight: str
    confidence: float  # 0.0 - 1.0
    related_files: list[str]
    error_signature: str | None = None
    prevention_hint: str | None = None
    success_factors: list[str] | None = None


class PatternAnalyzer:
    """Analyzes session structured_findings to extract distillable patterns.

    Uses heuristics and pattern matching to identify:
    - Error patterns (recurring bugs, common mistakes)
    - Success patterns (effective strategies)
    - Stagnation patterns (what causes delays)
    """

    # Patterns that indicate an error/bug
    ERROR_INDICATORS = {
        "error_summary",
        "error_type",
        "failed",
        "exception",
        "crash",
        "bug",
        "timeout",
        "null_pointer",
        "undefined",
    }

    # Patterns that indicate success
    SUCCESS_INDICATORS = {
        "verified_results",
        "patched_files",
        "completed",
        "resolved",
        "fixed",
        "passed",
    }

    # Patterns that indicate stagnation
    STAGNATION_INDICATORS = {
        "stagnation",
        "loop",
        "repeated",
        "same_error",
        "no_progress",
        "blocked",
    }

    def analyze(self, structured_findings: dict[str, Any], session_id: str, outcome: str) -> list[ExtractedPattern]:
        """Analyze structured_findings and extract patterns.

        Args:
            structured_findings: The session's structured_findings dict
            session_id: Session identifier for tracking
            outcome: Session outcome (completed | failed | stagnation)

        Returns:
            List of extracted patterns
        """
        patterns: list[ExtractedPattern] = []

        # Check for error patterns
        if outcome in ("failed", "stagnation") or self._has_error_indicators(structured_findings):
            error_pattern = self._extract_error_pattern(structured_findings, session_id)
            if error_pattern:
                patterns.append(error_pattern)

        # Check for success patterns
        if outcome == "completed" or self._has_success_indicators(structured_findings):
            success_pattern = self._extract_success_pattern(structured_findings, session_id)
            if success_pattern:
                patterns.append(success_pattern)

        # Check for stagnation patterns
        if outcome == "stagnation" or self._has_stagnation_indicators(structured_findings):
            stagnation_pattern = self._extract_stagnation_pattern(structured_findings, session_id)
            if stagnation_pattern:
                patterns.append(stagnation_pattern)

        # Extract generic patterns from findings trajectory
        trajectory = structured_findings.get("_findings_trajectory", [])
        if trajectory and not patterns:
            generic = self._extract_generic_pattern(structured_findings, session_id)
            if generic:
                patterns.append(generic)

        return patterns

    def _has_error_indicators(self, findings: dict[str, Any]) -> bool:
        """Check if findings contain error indicators."""
        for key in findings:
            key_lower = key.lower()
            if any(indicator in key_lower for indicator in self.ERROR_INDICATORS):
                return True
            if "error" in str(findings.get(key, "")).lower():
                return True
        return False

    def _has_success_indicators(self, findings: dict[str, Any]) -> bool:
        """Check if findings contain success indicators."""
        for key in findings:
            key_lower = key.lower()
            if any(indicator in key_lower for indicator in self.SUCCESS_INDICATORS):
                return True
        return False

    def _has_stagnation_indicators(self, findings: dict[str, Any]) -> bool:
        """Check if findings contain stagnation indicators."""
        for key in findings:
            key_lower = key.lower()
            if any(indicator in key_lower for indicator in self.STAGNATION_INDICATORS):
                return True
        return False

    def _extract_error_pattern(self, findings: dict[str, Any], session_id: str) -> ExtractedPattern | None:
        """Extract error pattern from findings."""
        # Get error summary
        error_summary = findings.get("error_summary", "")
        if not error_summary:
            error_summary = findings.get("error_type", "")

        if not error_summary:
            return None

        # Get suspected files
        suspected = findings.get("suspected_files", [])
        if isinstance(suspected, str):
            suspected = [suspected]
        related_files = list(suspected) if suspected else []

        # Get action taken
        action = findings.get("action_taken", "")

        # Extract error signature (first line or key phrase)
        error_signature = self._extract_error_signature(error_summary)

        # Generate prevention hint
        prevention_hint = self._generate_error_prevention(action, related_files)

        # Calculate confidence based on trajectory depth
        trajectory = findings.get("_findings_trajectory", [])
        confidence = min(0.5 + len(trajectory) * 0.05, 0.95)

        return ExtractedPattern(
            pattern_type="error_pattern",
            summary=self._summarize_error(error_summary, related_files),
            insight=f"Error in session {session_id}: {error_summary}. Files involved: {', '.join(related_files) if related_files else 'unknown'}",
            confidence=confidence,
            related_files=related_files,
            error_signature=error_signature,
            prevention_hint=prevention_hint,
        )

    def _extract_success_pattern(self, findings: dict[str, Any], session_id: str) -> ExtractedPattern | None:
        """Extract success pattern from findings."""
        verified = findings.get("verified_results", [])
        if isinstance(verified, str):
            verified = [verified]

        patched = findings.get("patched_files", [])
        if isinstance(patched, str):
            patched = [patched]

        if not verified and not patched:
            return None

        # Build success factors
        success_factors = []
        if verified:
            success_factors.append(f"Verified: {', '.join(str(v) for v in verified[:3])}")
        if patched:
            success_factors.append(f"Modified: {', '.join(str(p) for p in patched[:3])}")

        # Calculate confidence
        confidence = min(0.6 + len(verified) * 0.1 + len(patched) * 0.05, 0.95)

        return ExtractedPattern(
            pattern_type="success_pattern",
            summary=f"Successfully completed with {len(verified)} verifications, {len(patched)} files modified",
            insight=f"Session {session_id} completed successfully. Files verified: {verified[:5]}, Files patched: {patched[:5]}",
            confidence=confidence,
            related_files=list(patched) if patched else [],
            success_factors=success_factors,
        )

    def _extract_stagnation_pattern(self, findings: dict[str, Any], session_id: str) -> ExtractedPattern | None:
        """Extract stagnation pattern from findings."""
        # Check trajectory for stagnation signals
        trajectory = findings.get("_findings_trajectory", [])
        if len(trajectory) < 2:
            return None

        # Look for repeated task_progress
        progresses = [t.get("task_progress") for t in trajectory[-4:] if isinstance(t, dict) and t.get("task_progress")]
        if not progresses or len(set(progresses)) == 1:
            # Same progress for multiple turns
            current_progress = findings.get("task_progress", "unknown")
            summary = f"Stagnation at {current_progress} for {len(trajectory)} turns"
        else:
            summary = f"Complex stagnation with {len(trajectory)} turns of exploration"

        # Calculate confidence
        confidence = min(0.4 + len(trajectory) * 0.1, 0.85)

        return ExtractedPattern(
            pattern_type="stagnation_pattern",
            summary=summary,
            insight=f"Session {session_id} stagnated. Trajectory length: {len(trajectory)}. Last progress: {progresses[-1] if progresses else 'unknown'}",
            confidence=confidence,
            related_files=findings.get("suspected_files", []),
            prevention_hint="Consider trying a different approach or breaking down the task further.",
        )

    def _extract_generic_pattern(self, findings: dict[str, Any], session_id: str) -> ExtractedPattern | None:
        """Extract generic pattern when no specific type matches."""
        task_progress = findings.get("task_progress", "unknown")

        summary = f"Generic pattern: {task_progress} phase in session {session_id[-8:]}"

        return ExtractedPattern(
            pattern_type="generic_pattern",
            summary=summary,
            insight=f"Session {session_id} reached {task_progress} phase",
            confidence=0.3,
            related_files=findings.get("suspected_files", []) + findings.get("patched_files", []),
        )

    def _extract_error_signature(self, error_summary: str) -> str:
        """Extract a short error signature from error summary."""
        # Get first sentence or line
        lines = error_summary.split("\n")
        first_line = lines[0].strip() if lines else error_summary

        # Truncate if too long
        if len(first_line) > 100:
            first_line = first_line[:100] + "..."

        # Remove file paths for generalization
        signature = re.sub(r"[/\\][a-zA-Z0-9_.-]+", "<file>", first_line)
        # Remove line numbers
        signature = re.sub(r":\d+", "", signature)
        # Remove hex addresses
        signature = re.sub(r"0x[a-fA-F0-9]+", "<addr>", signature)

        return signature.strip()

    def _summarize_error(self, error_summary: str, related_files: list[str]) -> str:
        """Create a summary of the error."""
        file_str = f" in {', '.join(related_files[:2])}" if related_files else ""
        error_type = error_summary.split(":")[0] if ":" in error_summary else error_summary
        error_type = error_type[:50] + "..." if len(error_type) > 50 else error_type
        return f"{error_type}{file_str}"

    def _generate_error_prevention(self, action: str, related_files: list[str]) -> str:
        """Generate a prevention hint based on action taken."""
        if not action:
            return "Review the error context and consider adding validation or error handling."

        # Try to infer prevention from action
        action_lower = action.lower()

        if "test" in action_lower:
            return "Add tests to catch this error earlier in the development cycle."
        if "validate" in action_lower or "check" in action_lower:
            return "Consider adding pre-execution validation to catch this issue proactively."
        if "retry" in action_lower or "repeat" in action_lower:
            return "This error may be transient. Consider adding retry logic with backoff."
        if "ignore" in action_lower or "skip" in action_lower:
            return "Verify this is safe to skip and document the reason."

        return f"Action taken: {action[:100]}. Review if this approach is optimal."
