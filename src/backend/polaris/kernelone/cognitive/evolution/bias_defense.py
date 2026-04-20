"""Bias Defense Engine - Detects and mitigates cognitive biases in reasoning."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Pattern-based bias detection keywords and thresholds
BIAS_PATTERNS: dict[str, dict[str, Any]] = {
    "confirmation_bias": {
        "keywords": [
            "obviously",
            "clearly",
            "of course",
            "everyone knows",
            "as expected",
            "naturally",
            "surely",
            "obviously correct",
            "should know",
            "must be",
            "always",
            "never",
        ],
        "indicator_threshold": 2,
        "weight": 1.2,
    },
    "anchoring_bias": {
        "keywords": [
            "first",
            "initial",
            "original",
            "starting point",
            "baseline",
            "primary",
            "main",
            "fundamental",
            "first impression",
            "since the beginning",
        ],
        "indicator_threshold": 2,
        "weight": 1.0,
    },
    "availability_heuristic": {
        "keywords": [
            "i remember",
            "recently",
            "in the news",
            "heard of",
            "came across",
            "i've seen",
            "typically",
            "usually",
            "often",
            "every time",
        ],
        "indicator_threshold": 2,
        "weight": 1.0,
    },
    "overconfidence": {
        "keywords": [
            "certain",
            "sure",
            "definitely",
            "no doubt",
            "guarantee",
            "absolutely",
            "100%",
            "without question",
            "i'm sure",
            "know for a fact",
            "will definitely",
            "certainly will",
            "obviously will",
        ],
        "indicator_threshold": 3,
        "weight": 1.3,
    },
    "hindsight_bias": {
        "keywords": [
            "i knew it",
            "should have known",
            "predicted",
            "expected",
            "saw it coming",
            "obvious in hindsight",
            "as we all know now",
            "looking back",
        ],
        "indicator_threshold": 2,
        "weight": 1.1,
    },
    "sunk_cost_fallacy": {
        "keywords": [
            "already invested",
            "spent too much",
            "can't give up now",
            "too far gone",
            "already done",
            "might as well",
            "continued",
            "keep going",
        ],
        "indicator_threshold": 2,
        "weight": 1.0,
    },
}

# Mitigation suggestions for each bias type
BIAS_MITIGATIONS: dict[str, tuple[str, ...]] = {
    "confirmation_bias": (
        "Actively seek contradictory evidence",
        "Consider alternative explanations",
        "Ask: What would convince me I'm wrong?",
    ),
    "anchoring_bias": (
        "Re-examine initial assumptions critically",
        "Consider how different the conclusion would be with different starting points",
        "Seek independent baseline assessment",
    ),
    "availability_heuristic": (
        "Check actual frequency data instead of recall",
        "Seek diverse sources beyond personal experience",
        "Consider statistical base rates",
    ),
    "overconfidence": (
        "Add uncertainty margins to predictions",
        "Seek expert disagreement",
        "Consider worst-case scenarios seriously",
    ),
    "hindsight_bias": (
        "Document predictions before knowing outcomes",
        "Consider alternative pasts that could have happened",
        "Recognize outcome bias in evaluation",
    ),
    "sunk_cost_fallacy": (
        "Evaluate decisions based on future value, not past investment",
        "Ask: Would I make the same decision if I started fresh today?",
        "Consider the option with minimum future regret",
    ),
}


@dataclass(frozen=True)
class BiasDetectionResult:
    """Result of bias detection analysis."""

    biases_detected: tuple[str, ...] = field(default_factory=tuple)
    mitigation_suggestions: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.0


class BiasDefenseEngine:
    """Detects cognitive biases in reasoning chains and provides mitigation."""

    def detect_bias(self, reasoning_content: str, context: dict[str, Any] | None = None) -> BiasDetectionResult:
        """Detect biases in reasoning content using pattern matching.

        Args:
            reasoning_content: The reasoning text to analyze
            context: Optional context including intent_type, role_id, etc.

        Returns:
            BiasDetectionResult with detected biases and mitigation suggestions
        """
        if not reasoning_content:
            return BiasDetectionResult(
                biases_detected=(),
                mitigation_suggestions=(),
                confidence=0.0,
            )

        content_lower = reasoning_content.lower()
        detected_biases: list[str] = []
        all_mitigations: list[str] = []

        # Score each bias pattern
        bias_scores: dict[str, int] = {}
        for bias_name, pattern_info in BIAS_PATTERNS.items():
            keywords = pattern_info["keywords"]
            threshold = pattern_info["indicator_threshold"]

            # Count keyword matches
            matches = 0
            for keyword in keywords:
                # Use word boundary matching for better accuracy
                if re.search(r"\b" + re.escape(keyword) + r"\b", content_lower, re.IGNORECASE):
                    matches += 1

            if matches >= threshold:
                detected_biases.append(bias_name)
                all_mitigations.extend(BIAS_MITIGATIONS.get(bias_name, ()))

            bias_scores[bias_name] = matches

        if not detected_biases:
            return BiasDetectionResult(
                biases_detected=(),
                mitigation_suggestions=(),
                confidence=0.0,
            )

        # Calculate confidence based on number and strength of detected biases
        total_matches = sum(bias_scores[b] for b in detected_biases)
        max_possible = sum(len(BIAS_PATTERNS[b]["keywords"]) for b in detected_biases)
        confidence = min(0.9, 0.3 + (total_matches / max_possible) * 0.6) if max_possible > 0 else 0.3

        # Remove duplicate mitigations while preserving order
        seen = set()
        unique_mitigations = []
        for m in all_mitigations:
            if m not in seen:
                seen.add(m)
                unique_mitigations.append(m)

        return BiasDetectionResult(
            biases_detected=tuple(detected_biases),
            mitigation_suggestions=tuple(unique_mitigations[:6]),  # Limit to top 6
            confidence=confidence,
        )

    def apply_mitigation(self, reasoning_content: str, detected_biases: tuple[str, ...]) -> str:
        """Apply bias mitigation to reasoning content.

        Args:
            reasoning_content: Original reasoning with detected biases
            detected_biases: Tuple of bias names to mitigate

        Returns:
            Reasoning content with bias-reducing phrases added
        """
        if not reasoning_content or not detected_biases:
            return reasoning_content

        # Build mitigation note based on detected biases
        mitigation_note = "\n\n[Bias Awareness]: "

        if "overconfidence" in detected_biases:
            mitigation_note += (
                "I recognize potential overconfidence. My assessment should be treated as probabilistic, not certain. "
            )
        if "confirmation_bias" in detected_biases:
            mitigation_note += (
                "I acknowledge I may be seeking confirming evidence. Alternative explanations deserve consideration. "
            )
        if "anchoring_bias" in detected_biases:
            mitigation_note += "My initial reference point may be unduly influencing my judgment. "
        if "availability_heuristic" in detected_biases:
            mitigation_note += "My reliance on easily recalled examples may not reflect statistical reality. "

        # Add general epistemic humility
        mitigation_note += "I commit to updating my beliefs when presented with contradictory evidence."

        return reasoning_content + mitigation_note
