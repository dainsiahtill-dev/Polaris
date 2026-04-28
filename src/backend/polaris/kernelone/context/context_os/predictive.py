"""Predictive Compression: anticipate future context needs.

This module implements predictive compression for ContextOS 3.0.
Instead of only compressing when budget is exceeded, it anticipates
future needs and pre-compresses strategically.

Key Design Principle:
    "Predictive Compression is pre-computation, not pre-deletion."
    Original content is NEVER deleted. Compression only creates new projections.

Predictive Strategies:
    1. Task Pattern Matching: Similar tasks needed certain content types
    2. Phase Transition Prediction: Upcoming phase will need specific content
    3. Explicit Forward References: LLM mentions future needs
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class PredictionStrategy(str, Enum):
    """Strategies for predicting future context needs."""

    TASK_PATTERN = "task_pattern"  # Based on similar task patterns
    PHASE_TRANSITION = "phase_transition"  # Based on upcoming phase
    FORWARD_REFERENCE = "forward_reference"  # Based on explicit references
    HISTORICAL = "historical"  # Based on historical usage


# Historical patterns: phase -> typical content types needed
PHASE_CONTENT_PATTERNS: dict[str, tuple[str, ...]] = {
    "intake": ("contract", "requirement", "goal"),
    "planning": ("architecture", "design", "constraint"),
    "exploration": ("file_tree", "search_result", "code_snippet"),
    "implementation": ("target_file", "interface", "test"),
    "verification": ("test_result", "diff", "error_log"),
    "debugging": ("error_log", "stack_trace", "recent_change"),
    "review": ("summary", "diff", "test_result"),
}

# Historical patterns: phase -> typical tools used
PHASE_TOOL_PATTERNS: dict[str, tuple[str, ...]] = {
    "intake": ("read_file", "search_code"),
    "planning": ("read_file", "search_code", "list_directory"),
    "exploration": ("read_file", "search_code", "list_directory", "repo_tree"),
    "implementation": ("write_file", "edit_file", "execute_command"),
    "verification": ("execute_command", "read_file"),
    "debugging": ("execute_command", "read_file", "search_code"),
    "review": ("read_file", "execute_command"),
}


@dataclass(frozen=True, slots=True)
class PredictionResult:
    """Result of a prediction."""

    strategy: PredictionStrategy
    confidence: float
    predicted_content_types: tuple[str, ...]
    predicted_tools: tuple[str, ...]
    reasoning: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy.value,
            "confidence": self.confidence,
            "predicted_content_types": list(self.predicted_content_types),
            "predicted_tools": list(self.predicted_tools),
            "reasoning": self.reasoning,
        }


@dataclass
class PredictiveCompressor:
    """Predicts future context needs and pre-compresses strategically.

    This class analyzes current state and predicts what content will be
    needed in future turns. It then pre-compresses non-essential content
    to create budget headroom.

    Usage:
        compressor = PredictiveCompressor()
        predictions = compressor.predict(
            current_phase=TaskPhase.IMPLMENTATION,
            recent_events=transcript[-10:],
            working_state=working_state,
        )
        # Use predictions to guide compression decisions
    """

    def predict(
        self,
        current_phase: str,
        recent_events: tuple[Any, ...] = (),
        working_state: Any = None,
    ) -> PredictionResult:
        """Predict future context needs.

        Args:
            current_phase: Current task phase
            recent_events: Recent transcript events
            working_state: Current working state

        Returns:
            PredictionResult with predicted needs
        """
        # Strategy 1: Phase-based prediction
        phase_prediction = self._predict_from_phase(current_phase)

        # Strategy 2: Forward reference detection
        forward_prediction = self._predict_from_forward_references(recent_events)

        # Strategy 3: Historical pattern matching
        historical_prediction = self._predict_from_historical(current_phase, recent_events)

        # Combine predictions (use highest confidence)
        predictions = [phase_prediction, forward_prediction, historical_prediction]
        best_prediction = max(predictions, key=lambda p: p.confidence)

        logger.debug(
            "Predictive compression: phase=%s, strategy=%s, confidence=%.2f",
            current_phase,
            best_prediction.strategy.value,
            best_prediction.confidence,
        )

        return best_prediction

    def _predict_from_phase(self, phase: str) -> PredictionResult:
        """Predict based on current phase."""
        content_types = PHASE_CONTENT_PATTERNS.get(phase, ())
        tools = PHASE_TOOL_PATTERNS.get(phase, ())

        return PredictionResult(
            strategy=PredictionStrategy.PHASE_TRANSITION,
            confidence=0.7,
            predicted_content_types=content_types,
            predicted_tools=tools,
            reasoning=f"Phase '{phase}' typically needs {', '.join(content_types)}",
        )

    def _predict_from_forward_references(self, events: tuple[Any, ...]) -> PredictionResult:
        """Predict based on explicit forward references in recent events."""
        if not events:
            return PredictionResult(
                strategy=PredictionStrategy.FORWARD_REFERENCE,
                confidence=0.0,
                predicted_content_types=(),
                predicted_tools=(),
                reasoning="No recent events to analyze",
            )

        # Look for forward reference patterns
        forward_patterns = [
            r"(?:will|need to|should|going to)\s+(?:check|read|look at|examine)\s+(\w+)",
            r"(?:next|then|after)\s+(?:I'll|we'll|let me)\s+(\w+)",
            r"(?:plan to|intend to)\s+(\w+)",
        ]

        content_types: set[str] = set()
        tools: set[str] = set()

        for event in events[-5:]:  # Last 5 events
            content = str(getattr(event, "content", "") or "").lower()
            for pattern in forward_patterns:
                matches = re.findall(pattern, content)
                for match in matches:
                    # Map to content types
                    if match in ("file", "code", "module"):
                        content_types.add("code_snippet")
                        tools.add("read_file")
                    elif match in ("test", "verify", "check"):
                        content_types.add("test_result")
                        tools.add("execute_command")
                    elif match in ("error", "bug", "issue"):
                        content_types.add("error_log")
                        tools.add("read_file")

        if content_types:
            return PredictionResult(
                strategy=PredictionStrategy.FORWARD_REFERENCE,
                confidence=0.8,
                predicted_content_types=tuple(content_types),
                predicted_tools=tuple(tools),
                reasoning=f"Forward references detected: {', '.join(content_types)}",
            )

        return PredictionResult(
            strategy=PredictionStrategy.FORWARD_REFERENCE,
            confidence=0.0,
            predicted_content_types=(),
            predicted_tools=(),
            reasoning="No forward references detected",
        )

    def _predict_from_historical(
        self,
        phase: str,
        events: tuple[Any, ...],
    ) -> PredictionResult:
        """Predict based on historical patterns."""
        # Analyze recent tool usage
        recent_tools: dict[str, int] = {}
        for event in events[-10:]:
            kind = str(getattr(event, "kind", "") or "").lower()
            if "tool" in kind:
                tool_name = str(getattr(event, "metadata", {}).get("tool_name", ""))
                if tool_name:
                    recent_tools[tool_name] = recent_tools.get(tool_name, 0) + 1

        # Predict next tools based on recent usage
        if recent_tools:
            most_used = max(recent_tools, key=recent_tools.get)  # type: ignore
            predicted_tools = (most_used,)
        else:
            predicted_tools = PHASE_TOOL_PATTERNS.get(phase, ())

        return PredictionResult(
            strategy=PredictionStrategy.HISTORICAL,
            confidence=0.6,
            predicted_content_types=PHASE_CONTENT_PATTERNS.get(phase, ()),
            predicted_tools=predicted_tools,
            reasoning=f"Historical pattern: recent tools = {list(recent_tools.keys())}",
        )
