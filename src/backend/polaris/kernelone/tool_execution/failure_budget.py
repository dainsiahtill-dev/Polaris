"""Failure Budget Controller - Failure Budget Controller.

Set failure budgets for each tool and error pattern to prevent infinite loop calls.
Adopts three-level decision: ALLOW -> ESCALATE -> BLOCK

Usage:
    >>> from polaris.kernelone.tool_execution.error_classifier import ToolErrorClassifier
    >>> from polaris.kernelone.tool_execution.failure_budget import FailureBudget
    >>> budget = FailureBudget()
    >>> classifier = ToolErrorClassifier()
    >>> pattern = classifier.classify("precision_edit", "no matches found")
    >>> result = budget.record_failure(pattern)
    >>> print(result.decision)
    ALLOW
    >>> result2 = budget.record_failure(pattern)  # Second same-type error
    >>> print(result2.decision)
    ESCALATE
    >>> result3 = budget.record_failure(pattern)  # Third same-type error
    >>> print(result3.decision)
    BLOCK
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from polaris.kernelone.constants import (
    FAILURE_BUDGET_MAX_PER_TOOL,
    FAILURE_BUDGET_MAX_SAME_PATTERN,
    FAILURE_BUDGET_MAX_TOTAL_PER_TURN,
)
from polaris.kernelone.tool_execution.error_classifier import ToolErrorClassifier, ToolErrorPattern

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class FailureDecision:
    """Failure decision result constants."""

    # Allow execution without extra intervention
    ALLOW = "ALLOW"
    # Escalate: attach stronger suggestion but still allow execution
    ESCALATE = "ESCALATE"
    # Block execution: too many failures, stop calling
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class FailureResult:
    """Failure decision result - carries full error context for Workflow.

    Attributes:
        decision: Decision result (ALLOW / ESCALATE / BLOCK)
        suggestion: Fix suggestion (if any)
        error_type: Error type (from ToolErrorPattern)
        retryable: Whether retryable (Workflow uses this to decide)
        blocked: Whether blocked
        tool_name: Tool name
        pattern_signature: Error pattern signature
        loop_break: Whether loop break triggered (Context Pruning signal)
    """

    decision: str
    suggestion: str | None
    error_type: str  # 来自 pattern.error_type
    retryable: bool  # 是否可重试
    blocked: bool
    tool_name: str
    pattern_signature: str
    loop_break: bool = False  # Context Pruning signal: break loop when consecutive identical search fails


@dataclass
class FailureBudget:
    """Failure budget controller for each tool and error pattern (session-scoped).

    Maintains two dimensions of failure counts:
    1. per-tool: failure count per tool
    2. per-pattern: failure count per error pattern

    Three-level decision mechanism:
    - ALLOW: failures within budget, execute normally
    - ESCALATE: same error pattern repeats, attach escalation suggestion
    - BLOCK: tool failure count exceeded, block the call

    Attributes:
        max_failures_per_tool: Max failures per tool (default 3)
        max_same_pattern: Max repeats for the same error pattern (default 2)
        max_total_per_turn: Max total failures per turn (default 10)
    """

    max_failures_per_tool: ClassVar[int] = FAILURE_BUDGET_MAX_PER_TOOL
    max_same_pattern: ClassVar[int] = FAILURE_BUDGET_MAX_SAME_PATTERN
    max_total_per_turn: ClassVar[int] = FAILURE_BUDGET_MAX_TOTAL_PER_TURN

    _session_registry_var: ClassVar[ContextVar[dict[str, FailureBudget] | None]] = ContextVar(
        "failure_budget_session_registry", default=None
    )

    session_id: str | None = field(default=None)

    _tool_failures: dict[str, int] = field(default_factory=dict)
    _pattern_failures: dict[str, int] = field(default_factory=dict)
    _total_failures: int = 0
    _classifier: ToolErrorClassifier = field(default_factory=ToolErrorClassifier)
    _recent_patterns: list[str] = field(default_factory=list)
    _consecutive_search_failures: dict[str, int] = field(default_factory=dict)
    _loop_break_tools: set[str] = field(default_factory=set)
    _file_read_history: dict[str, int] = field(default_factory=dict)
    _read_sequence: int = 0

    @classmethod
    def _get_session_registry(cls) -> dict[str, FailureBudget]:
        val = cls._session_registry_var.get()
        if val is None:
            val = {}
            cls._session_registry_var.set(val)
        return val

    @classmethod
    def for_session(cls, session_id: str) -> FailureBudget:
        """Factory: get or create a FailureBudget for the given session (persistent across turns)."""
        registry = cls._get_session_registry()
        if session_id not in registry:
            registry[session_id] = cls(session_id=session_id)
        return registry[session_id]

    def record_failure(self, pattern: ToolErrorPattern, *, search_fingerprint: str | None = None) -> FailureResult:
        """Record a failure and return the decision.

        Args:
            pattern: Error pattern

        Returns:
            FailureResult: Structure containing decision, suggestion, and error context
        """
        tool_key = pattern.tool_name
        pattern_key = pattern.error_signature

        # Update counters
        self._tool_failures[tool_key] = self._tool_failures.get(tool_key, 0) + 1
        self._pattern_failures[pattern_key] = self._pattern_failures.get(pattern_key, 0) + 1
        self._total_failures += 1

        # Track recent patterns (for recurrence detection)
        self._recent_patterns.append(pattern_key)
        if len(self._recent_patterns) > 20:
            self._recent_patterns = self._recent_patterns[-20:]

        tool_count = self._tool_failures[tool_key]
        pattern_count = self._pattern_failures[pattern_key]

        # Decision logic
        if tool_count > self.max_failures_per_tool:
            logger.warning(
                "[FailureBudget] BLOCK tool=%s (failures=%d, max=%d)", tool_key, tool_count, self.max_failures_per_tool
            )
            suggestion = self._block_suggestion(pattern)
            return FailureResult(
                decision=FailureDecision.BLOCK,
                suggestion=suggestion,
                error_type=pattern.error_type,
                retryable=False,  # Should not retry after BLOCK
                blocked=True,
                tool_name=tool_key,
                pattern_signature=pattern_key,
            )

        if pattern_count > self.max_same_pattern:
            logger.warning(
                "[FailureBudget] ESCALATE pattern=%s (count=%d, max=%d)",
                pattern_key[:60],
                pattern_count,
                self.max_same_pattern,
            )
            suggestion = self._escalate_suggestion(pattern)
            return FailureResult(
                decision=FailureDecision.ESCALATE,
                suggestion=suggestion,
                error_type=pattern.error_type,
                retryable=self._is_retryable_error_type(pattern.error_type, tool_name=tool_key),
                blocked=False,
                tool_name=tool_key,
                pattern_signature=pattern_key,
            )

        # Fix 2: Search-string sequence detection - 2 consecutive identical search string failures = force re-read
        # Note: Only effective when pattern_count has not exceeded the limit (escalation not started),
        # to avoid the same SEQUENCE_BREAK being triggered repeatedly on every subsequent call.
        if search_fingerprint and pattern_count <= self.max_same_pattern:
            seq_count = self._consecutive_search_failures.get(search_fingerprint, 0) + 1
            self._consecutive_search_failures[search_fingerprint] = seq_count
            if seq_count >= 2:
                logger.warning(
                    "[FailureBudget] SEQUENCE_BREAK: search='%s...' failed %d times consecutively",
                    search_fingerprint[:50],
                    seq_count,
                )
                suggestion = self._sequence_break_suggestion(pattern, search_fingerprint)
                self._loop_break_tools.add(tool_key)
                return FailureResult(
                    decision=FailureDecision.ESCALATE,
                    suggestion=suggestion,
                    error_type=pattern.error_type,
                    retryable=True,
                    blocked=False,
                    tool_name=tool_key,
                    pattern_signature=pattern_key,
                    loop_break=True,
                )
        else:
            # If no fingerprint provided, clear sequence count (search changed)
            self._consecutive_search_failures.clear()

        if self._total_failures > self.max_total_per_turn:
            logger.warning("[FailureBudget] BLOCK - total failures exhausted (%d)", self._total_failures)
            suggestion = "Total failure budget exhausted. Stop current operation and report status to user."
            return FailureResult(
                decision=FailureDecision.BLOCK,
                suggestion=suggestion,
                error_type=pattern.error_type,
                retryable=False,
                blocked=True,
                tool_name=tool_key,
                pattern_signature=pattern_key,
            )

        # ALLOW - log silently to avoid noise
        return FailureResult(
            decision=FailureDecision.ALLOW,
            suggestion=None,
            error_type=pattern.error_type,
            retryable=self._is_retryable_error_type(pattern.error_type, tool_name=tool_key),
            blocked=False,
            tool_name=tool_key,
            pattern_signature=pattern_key,
        )

    def _is_retryable_error_type(self, error_type: str, *, tool_name: str = "") -> bool:
        """Determine whether the error type is retryable.

        Args:
            error_type: Error type string
            tool_name: Tool name (used to differentiate no_match retry policy for read vs write tools)

        Returns:
            True if the error is transient and may succeed on retry
        """
        # Retryable error types: transient issues that may succeed on retry
        retryable_types = {"timeout", "transient", "resource", "rate_limit"}
        if error_type in retryable_types:
            return True
        if error_type == "no_match":
            from polaris.kernelone.tool_execution.constants import WRITE_TOOLS

            return tool_name not in WRITE_TOOLS
        return False

    def _escalate_suggestion(self, pattern: ToolErrorPattern) -> str:
        """Generate escalation suggestion for ESCALATE decision."""
        suggestions_by_type = {
            "no_match": (
                f"WARNING: Tool '{pattern.tool_name}' failing repeatedly with 'no match' errors. "
                "MANDATORY: You MUST call read_file() to verify the EXACT content before retrying. "
                "Copy search strings character-by-character from the file output - do NOT guess. "
                "Check for missing spaces (e.g., 'return0' vs 'return 0') and indentation differences."
            ),
            "not_found": (
                f"WARNING: Tool '{pattern.tool_name}' failing with 'not found'. "
                "MANDATORY: Use repo_tree() or glob('**/*.py') to explore workspace structure. "
                "Verify file path is correct before retrying."
            ),
            "invalid_arg": (
                f"WARNING: Tool '{pattern.tool_name}' has invalid arguments. "
                "MANDATORY: Check tool parameter documentation and verify argument types before retrying."
            ),
            "syntax": (
                f"WARNING: Tool '{pattern.tool_name}' encountered syntax error. "
                "MANDATORY: Read the file to verify content correctness before editing."
            ),
            "permission": (
                f"WARNING: Tool '{pattern.tool_name}' permission denied. "
                "MANDATORY: Check file permissions or try running with appropriate access rights."
            ),
        }

        base = suggestions_by_type.get(
            pattern.error_type,
            f"WARNING: Tool '{pattern.tool_name}' failing repeatedly. "
            f"MANDATORY: Verify target exists and parameters are correct before retrying.",
        )

        return base

    def _sequence_break_suggestion(self, pattern: ToolErrorPattern, search_fingerprint: str) -> str:
        """Generate sequence-break suggestion - force LLM to re-read the file.

        Triggered when the same search string fails 2+ times consecutively.
        Indicates the LLM is stuck in a repetitive error loop and must re-verify file content.
        """
        return (
            f"HALLUCINATION_LOOP DETECTED: search string has failed {self._consecutive_search_failures.get(search_fingerprint, 0)} times with identical parameters. "
            "You are repeatedly submitting the same incorrect search string. "
            "FORCED ACTION: You MUST call read_file() now to verify the EXACT file content character-by-character. "
            "Copy every space, indent, and newline exactly as shown in the file. "
            "Do NOT guess or infer the content - you must read it directly. "
            "After confirming the exact content, retry precision_edit with the verified string."
        )

    def _block_suggestion(self, pattern: ToolErrorPattern) -> str:
        """Generate block suggestion for BLOCK decision."""
        return (
            f"TOOL BLOCKED: '{pattern.tool_name}' has exceeded failure budget "
            f"({self.max_failures_per_tool} failures). "
            f"STOP attempting this tool. "
            f"Error pattern: {pattern.error_type}. "
            f"Informed user of persistent failure and requested manual intervention or alternative approach."
        )

    def get_tool_failure_count(self, tool_name: str) -> int:
        """Get tool failure count."""
        return self._tool_failures.get(tool_name, 0)

    def get_pattern_failure_count(self, pattern_key: str) -> int:
        """Get error pattern failure count."""
        return self._pattern_failures.get(pattern_key, 0)

    def get_total_failure_count(self) -> int:
        """Get total failure count."""
        return self._total_failures

    def is_tool_blocked(self, tool_name: str) -> bool:
        """Check whether the tool has been blocked."""
        return self._tool_failures.get(tool_name, 0) > self.max_failures_per_tool

    def get_blocked_tools(self) -> list[str]:
        """Get list of all blocked tools."""
        return [tool for tool, count in self._tool_failures.items() if count > self.max_failures_per_tool]

    def reset(self) -> None:
        """Reset all counters (also cleans up self reference in session registry)."""
        self._tool_failures.clear()
        self._pattern_failures.clear()
        self._total_failures = 0
        self._recent_patterns.clear()
        self._consecutive_search_failures.clear()
        self._loop_break_tools.clear()
        registry = self._get_session_registry()
        if self.session_id and self.session_id in registry:
            del registry[self.session_id]
        logger.debug("[FailureBudget] Reset all failure counters")

    def is_loop_break_tool(self, tool_name: str) -> bool:
        """Check whether the tool has triggered Context Pruning (consecutive HALLUCINATION_LOOP)."""
        return tool_name in self._loop_break_tools

    def get_stats(self) -> dict[str, Any]:
        """Get current statistics (for debugging and monitoring)."""
        return {
            "total_failures": self._total_failures,
            "tool_failures": dict(self._tool_failures),
            "pattern_failures": len(self._pattern_failures),
            "blocked_tools": self.get_blocked_tools(),
        }

    def get_file_read_history(self) -> dict[str, int]:
        """Get file read history (maps file path to last read sequence).

        Used by executor to track which files have been recently read
        for mandatory read-before-edit enforcement.

        Returns:
            Dict mapping file paths to their last read sequence number.
        """
        return self._file_read_history

    def set_file_read_sequence(self, value: int) -> None:
        """Set the current read sequence counter.

        Used by executor to persist read sequence across tool calls.

        Args:
            value: The sequence value to set.
        """
        self._read_sequence = int(value)

    def get_file_read_sequence(self) -> int:
        """Get the current read sequence counter.

        Returns:
            The current read sequence number.
        """
        return self._read_sequence

    def __repr__(self) -> str:
        return (
            f"FailureBudget(total={self._total_failures}, "
            f"tools={self._tool_failures}, "
            f"blocked={self.get_blocked_tools()})"
        )
