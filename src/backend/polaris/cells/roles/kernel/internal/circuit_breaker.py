"""Robust Circuit Breaker - Semantic-aware loop detection.

Architecture: Replaces the form-based (count/pattern) detection with
semantic-aware detection that tracks information gain.

Core Principles:
1. Semantic Equivalence: Same file/directory operations are "equivalent"
2. Information Gain: Track result fingerprints to detect stagnation
3. Progressive Warnings: L1→L2→L3 escalation before hard break
4. Scene Adaptation: Thresholds adapt to task complexity
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import hashlib
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Semantic Equivalence Normalization
# ─────────────────────────────────────────────────────────────────────────────

TOOL_SIGNATURE_NORMALIZERS = {}


def register_normalizer(tool_names: set[str]) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to register a signature normalizer for specific tools."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        for name in tool_names:
            TOOL_SIGNATURE_NORMALIZERS[name] = func
        return func

    return decorator


def _hash_path(path: str) -> str:
    """Normalize a path to a short hash for comparison."""
    return hashlib.md5(path.encode("utf-8")).hexdigest()[:8]


@register_normalizer({"read_file", "repo_read_head", "repo_read_tail", "repo_read_slice"})
def _normalize_read_file(tool_name: str, args: dict[str, Any]) -> tuple[str, str]:
    """Normalize read operations: same file = equivalent regardless of line range."""
    path = args.get("path") or args.get("file") or ""
    if path:
        return (tool_name, f"file:{_hash_path(path)}")
    return (tool_name, "unknown")


@register_normalizer({"repo_read_around"})
def _normalize_read_around(tool_name: str, args: dict[str, Any]) -> tuple[str, str]:
    """Normalize read_around: same target line = equivalent."""
    path = args.get("path") or ""
    lineno = args.get("lineno", 0)
    return (tool_name, f"around:{_hash_path(path)}:{lineno}")


@register_normalizer({"repo_rg", "search_code"})
def _normalize_search(tool_name: str, args: dict[str, Any]) -> tuple[str, str]:
    """Normalize search: same directory pattern = equivalent."""
    path = args.get("path", "")
    pattern = args.get("pattern", "")
    return (tool_name, f"search:{_hash_path(path)}:{pattern}")


@register_normalizer({"repo_tree", "list_directory"})
def _normalize_list_dir(tool_name: str, args: dict[str, Any]) -> tuple[str, str]:
    """Normalize directory listing: same path = equivalent."""
    path = args.get("path", "")
    return (tool_name, f"dir:{_hash_path(path)}")


@register_normalizer({"file_exists", "glob"})
def _normalize_file_query(tool_name: str, args: dict[str, Any]) -> tuple[str, str]:
    """Normalize file queries: same pattern = equivalent."""
    pattern = args.get("pattern", "")
    path = args.get("path", "")
    return (tool_name, f"query:{_hash_path(path)}:{pattern}")


def normalize_tool_signature(tool_name: str, args: dict[str, Any]) -> tuple[str, str]:
    """Normalize tool call to semantic equivalence key.

    Two calls with the same signature are semantically equivalent:
    - read_file("/src/main.py", lines="1-50") and
      read_file("/src/main.py", lines="51-100")
      → same signature → equivalent

    Args:
        tool_name: Name of the tool
        args: Tool arguments

    Returns:
        Tuple of (tool_name, semantic_key)
    """
    normalizer = TOOL_SIGNATURE_NORMALIZERS.get(tool_name)
    if normalizer:
        return normalizer(tool_name, args)

    # Default: use full args JSON hash (no normalization)
    args_str = json.dumps(args, sort_keys=True, ensure_ascii=False)
    return (tool_name, hashlib.md5(args_str.encode()).hexdigest()[:12])


# ─────────────────────────────────────────────────────────────────────────────
# Information Gain Tracker
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class InformationGainTracker:
    """Tracks whether tool executions are producing new information.

    Uses content fingerprints to detect when the agent is getting
    the same information repeatedly (loop) vs. making progress.
    """

    window_size: int = 5
    _fingerprints: list[str] = field(default_factory=list)
    _no_gain_streak: int = 0

    def check(self, tool_name: str, result: dict[str, Any]) -> bool:
        """Check if this result provides new information.

        Args:
            tool_name: Name of the executed tool
            result: Tool execution result

        Returns:
            True if new information detected (no loop)
            False if duplicate information (potential loop)
        """
        fingerprint = self._compute_fingerprint(tool_name, result)

        # Check if we've seen this fingerprint recently
        recent_window = self._fingerprints[-self.window_size :] if self._fingerprints else []
        if fingerprint in recent_window:
            self._no_gain_streak += 1
            logger.debug(
                "[CircuitBreaker] No gain detected: tool=%s streak=%d fingerprint=%s",
                tool_name,
                self._no_gain_streak,
                fingerprint[:8],
            )
            return False

        # New information
        self._fingerprints.append(fingerprint)
        if self._no_gain_streak > 0:
            logger.debug(
                "[CircuitBreaker] Information gain: tool=%s cleared streak=%d",
                tool_name,
                self._no_gain_streak,
            )
        self._no_gain_streak = 0
        return True

    def get_streak(self) -> int:
        """Get current no-gain streak."""
        return self._no_gain_streak

    def reset(self) -> None:
        """Reset the tracker."""
        self._fingerprints.clear()
        self._no_gain_streak = 0

    def _compute_fingerprint(self, tool_name: str, result: dict[str, Any]) -> str:
        """Compute semantic fingerprint of tool result."""
        try:
            if tool_name in {"repo_tree", "list_directory"}:
                return self._fingerprint_directory_list(result)
            elif tool_name in {"repo_rg", "search_code"}:
                return self._fingerprint_search_results(result)
            elif tool_name in {
                "read_file",
                "repo_read_head",
                "repo_read_tail",
                "repo_read_slice",
                "repo_read_around",
            }:
                return self._fingerprint_file_content(result)
            elif tool_name in {"file_exists", "glob"}:
                return self._fingerprint_file_query(result)
            else:
                return self._fingerprint_default(result)
        except (TypeError, ValueError, KeyError) as exc:
            logger.warning("[CircuitBreaker] Fingerprint computation failed: %s", exc)
            return self._fingerprint_default(result)

    def _fingerprint_directory_list(self, result: dict[str, Any]) -> str:
        """Fingerprint for directory listing results."""
        entries = result.get("result", {}).get("entries", [])
        if isinstance(entries, list):
            names = sorted([str(e.get("name", "")) for e in entries])
            return hashlib.md5(",".join(names).encode()).hexdigest()[:12]
        return hashlib.md5(str(entries).encode()).hexdigest()[:12]

    def _fingerprint_search_results(self, result: dict[str, Any]) -> str:
        """Fingerprint for search results."""
        matches = result.get("result", {}).get("matches", [])
        if isinstance(matches, list):
            # Use file:line:preview for uniqueness
            locs = []
            for m in matches[:50]:  # Limit to first 50 matches
                file = str(m.get("file", ""))
                line = m.get("line", 0)
                preview = str(m.get("preview", ""))[:30]
                locs.append(f"{file}:{line}:{preview}")
            return hashlib.md5(",".join(locs).encode()).hexdigest()[:12]
        return hashlib.md5(str(matches).encode()).hexdigest()[:12]

    def _fingerprint_file_content(self, result: dict[str, Any]) -> str:
        """Fingerprint for file content."""
        content = result.get("result", {}).get("content", "")
        if isinstance(content, str):
            # Use first 200 chars + length for quick fingerprint
            return hashlib.md5(f"{content[:200]}:{len(content)}".encode()).hexdigest()[:12]
        return hashlib.md5(str(content).encode()).hexdigest()[:12]

    def _fingerprint_file_query(self, result: dict[str, Any]) -> str:
        """Fingerprint for file existence queries."""
        files = result.get("result", {}).get("files", [])
        if isinstance(files, list):
            names = sorted([str(f) for f in files[:50]])
            return hashlib.md5(",".join(names).encode()).hexdigest()[:12]
        return hashlib.md5(str(files).encode()).hexdigest()[:12]

    def _fingerprint_default(self, result: dict[str, Any]) -> str:
        """Default fingerprint using result string representation."""
        result_str = json.dumps(result, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(result_str.encode()).hexdigest()[:12]


# ─────────────────────────────────────────────────────────────────────────────
# Progressive Circuit Breaker
# ─────────────────────────────────────────────────────────────────────────────


class CircuitBreakerLevel(Enum):
    """Circuit breaker warning levels."""

    OK = "ok"  # Normal operation
    WARNING = "warning"  # L1: Soft warning injected
    HARD = "hard"  # L2: Strong warning + suggestion
    BREAK = "break"  # L3: Hard stop


@dataclass(frozen=True)
class SceneProfile:
    """Threshold profile for a specific task scene."""

    name: str
    warning_threshold: int  # L1 triggers at this count
    hard_threshold: int  # L2 triggers at this count
    break_threshold: int  # L3 (hard break) triggers at this count
    read_stagnation_threshold: int  # Read-only streak triggers HARD at this count


# Scene profiles for different task types
SCENE_PROFILES: dict[str, SceneProfile] = {
    "quick_fix": SceneProfile(
        name="quick_fix",
        warning_threshold=2,
        hard_threshold=3,
        break_threshold=5,
        read_stagnation_threshold=6,
    ),
    "normal": SceneProfile(
        name="normal",
        warning_threshold=3,
        hard_threshold=5,
        break_threshold=7,
        read_stagnation_threshold=8,
    ),
    "deep_analysis": SceneProfile(
        name="deep_analysis",
        warning_threshold=5,
        hard_threshold=8,
        break_threshold=12,
        read_stagnation_threshold=15,
    ),
}


@dataclass
class ProgressiveCircuitBreaker:
    """Progressive circuit breaker with scene-adaptive thresholds.

    Three-level escalation:
    - L1 (WARNING): Soft reminder injected into transcript
    - L2 (HARD): Strong warning + specific suggestions
    - L3 (BREAK): Hard stop, recovery state machine triggered

    Triple detection tracks:
    1. Information gain: detects repeated identical results
    2. Semantic stagnation: detects repeated operations on same target
       (even when each call returns different content, e.g. reading
       different line ranges of the same file)
    3. Read-only stagnation: detects agent stuck in read-only mode
       with zero write operations, regardless of which files are read.
       This catches rotating-file patterns (ABCDABCD) where semantic
       stagnation per-file resets between targets.
    """

    scene: str = "normal"
    _gain_tracker: InformationGainTracker = field(default_factory=InformationGainTracker)
    _consecutive_no_gain: int = 0
    _last_signature: tuple[str, str] = ("", "")
    # Semantic stagnation: counts consecutive calls with the same semantic
    # signature (e.g. same file, same directory), regardless of information
    # gain.  This catches the case where the LLM reads different sections
    # of the same file 12 times — each read returns different content so
    # information gain never triggers, but the agent is still stuck in a
    # read loop producing zero value.
    _consecutive_same_signature: int = 0
    # Stagnation multiplier: when both no_gain AND same_signature are
    # elevated, the effective count is multiplied to escalate faster.
    _stagnation_multiplier: float = 1.0
    # Read-only stagnation: total consecutive read-only calls across ALL
    # targets. Resets to 0 on any write operation. When this exceeds
    # the scene-specific read_stagnation threshold, it forces HARD level
    # regardless of information gain. This catches the critical production
    # pattern where the LLM reads N different files repeatedly without
    # ever producing a write or edit operation.
    _read_only_streak: int = 0

    def __post_init__(self) -> None:
        self._profile = SCENE_PROFILES.get(self.scene, SCENE_PROFILES["normal"])

    @property
    def profile(self) -> SceneProfile:
        return self._profile

    def evaluate(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        *,
        is_read_only: bool = True,
    ) -> tuple[CircuitBreakerLevel, int]:
        """Evaluate whether circuit breaker should trigger.

        Uses triple detection:
        1. **No-gain streak**: Counts consecutive calls where the result
           content is identical to recent results (information gain = 0).
        2. **Semantic stagnation**: Counts consecutive calls targeting the
           same semantic resource (same file, same directory) even when
           each call returns different content. This catches the critical
           pattern where the LLM reads different line ranges of the same
           file repeatedly — information gain sees "new" content each
           time, but the agent is stuck in a read loop.
        3. **Read-only stagnation**: Counts total consecutive read-only
           calls across ALL targets (even different files). Resets on any
           write operation. Catches rotating-file patterns (ABCDABCD)
           where semantic stagnation per-file resets between targets.

        The effective escalation count is the **maximum** of the three
        streaks, so any detection path can independently trigger
        the circuit breaker.

        Args:
            tool_name: Name of the tool
            args: Tool arguments
            result: Tool execution result
            is_read_only: Whether this tool is a read-only operation.
                Defaults to True; caller should pass False for write/edit tools.

        Returns:
            Tuple of (level, consecutive_count)
        """
        # Track read-only stagnation across ALL targets
        if is_read_only:
            self._read_only_streak += 1
        else:
            self._read_only_streak = 0

        # Compute semantic signature
        signature = normalize_tool_signature(tool_name, args)

        # Track semantic stagnation (same target, regardless of content)
        is_same_target = signature == self._last_signature
        if is_same_target:
            self._consecutive_same_signature += 1
        else:
            self._consecutive_same_signature = 0
        self._last_signature = signature

        # Check information gain
        has_gain = self._gain_tracker.check(tool_name, result)

        if has_gain:
            # New content found — reset no-gain streak
            self._consecutive_no_gain = 0
        else:
            # Duplicate content — increment no-gain streak
            if is_same_target:
                self._consecutive_no_gain += 1
            # else: different target but no gain — don't inflate either counter

        # Compute stagnation multiplier: when both counters are elevated,
        # the agent is reading the same target AND getting redundant info
        if self._consecutive_same_signature >= 3 and self._consecutive_no_gain >= 2:
            self._stagnation_multiplier = 1.5
        elif self._consecutive_same_signature >= 5:
            # Pure semantic stagnation (different content, same target) —
            # this is the critical path for the 12-iteration read loop bug
            self._stagnation_multiplier = 1.2
        else:
            self._stagnation_multiplier = 1.0

        # Effective count: max of all three streaks, scaled by multiplier
        raw_count = max(
            self._consecutive_no_gain,
            self._consecutive_same_signature,
        )
        count = int(raw_count * self._stagnation_multiplier)

        # Progressive escalation
        if count >= self._profile.break_threshold:
            return (CircuitBreakerLevel.BREAK, count)
        elif count >= self._profile.hard_threshold:
            return (CircuitBreakerLevel.HARD, count)
        elif count >= self._profile.warning_threshold:
            return (CircuitBreakerLevel.WARNING, count)

        # Read-only stagnation check: even if individual counters are low,
        # a high read-only streak across different files still signals
        # the agent is stuck in an exploration loop with zero output.
        if self._read_only_streak >= self._profile.read_stagnation_threshold:
            return (CircuitBreakerLevel.HARD, self._read_only_streak)

        return (CircuitBreakerLevel.OK, count)

    def get_warning_message(self, level: CircuitBreakerLevel, tool_name: str, count: int) -> str:
        """Generate appropriate warning message for the level."""
        # Read-only stagnation message
        if self._read_only_streak >= self._profile.read_stagnation_threshold and level in (
            CircuitBreakerLevel.HARD,
            CircuitBreakerLevel.BREAK,
        ):
            return (
                f"[SYSTEM WARNING] {self._read_only_streak} consecutive READ-ONLY operations "
                "with ZERO write/edit output. The task requires making changes. "
                "You MUST now execute a write or edit operation to make progress. "
                "If you don't have enough information, summarize what you know and act on it."
            )
        if level == CircuitBreakerLevel.WARNING:
            return (
                f"[SYSTEM REMINDER] You have executed '{tool_name}' {count} times "
                f"with potentially duplicate information. "
                "Review the existing results before continuing."
            )
        elif level == CircuitBreakerLevel.HARD:
            return (
                f"[SYSTEM WARNING] Repeated '{tool_name}' operations ({count} times) "
                "without new information detected. "
                "Consider: 1) You have enough information to proceed "
                "2) Try a different approach "
                "3) Execute a write operation to make progress."
            )
        elif level == CircuitBreakerLevel.BREAK:
            return (
                f"[CIRCUIT BREAKER] Maximum repetition limit reached for '{tool_name}'. "
                "The system is stopping execution to prevent infinite loops. "
                "Please review your findings and either complete the task or escalate."
            )
        return ""

    def reset(self) -> None:
        """Reset breaker state."""
        self._consecutive_no_gain = 0
        self._consecutive_same_signature = 0
        self._stagnation_multiplier = 1.0
        self._read_only_streak = 0
        self._gain_tracker.reset()
        self._last_signature = ("", "")


# ─────────────────────────────────────────────────────────────────────────────
# Task Scene Inference
# ─────────────────────────────────────────────────────────────────────────────

DEEP_ANALYSIS_KEYWORDS = {
    "分析",
    "理解",
    "探索",
    "调研",
    "审计",
    "总结",
    "评估",
    "梳理",
    "归纳",
    "解析",
    "analyze",
    "understand",
    "explore",
    "investigate",
    "audit",
    "architecture",
    "structure",
    "dependencies",
    "comprehensive",
    "review",
    "assess",
    "summarize",
    "summarise",
}

QUICK_FIX_KEYWORDS = {
    "修复",
    "bug",
    "错误",
    "修改",
    "fix",
    "patch",
    "hotfix",
    "typo",
    "error",
}


def infer_task_scene(message: str) -> str:
    """Infer task scene from user message.

    Args:
        message: User's request message

    Returns:
        Scene name: "deep_analysis" | "quick_fix" | "normal"
    """
    if not message:
        return "normal"

    message_lower = message.lower()

    deep_score = sum(1 for kw in DEEP_ANALYSIS_KEYWORDS if kw in message_lower)
    quick_score = sum(1 for kw in QUICK_FIX_KEYWORDS if kw in message_lower)

    if deep_score > quick_score and deep_score >= 2:
        return "deep_analysis"
    elif quick_score > deep_score and quick_score >= 1:
        return "quick_fix"
    return "normal"


__all__ = [
    "SCENE_PROFILES",
    "CircuitBreakerLevel",
    "InformationGainTracker",
    "ProgressiveCircuitBreaker",
    "SceneProfile",
    "infer_task_scene",
    "normalize_tool_signature",
]
