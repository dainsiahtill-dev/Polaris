"""Dialog act classifier for conversation attention.

This module provides deterministic classification of dialog acts
without relying on LLM inference. It enables proper attention and
intent tracking for short user responses like "需要", "不用", "先别".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .helpers import _normalize_text
from .models_v2 import DialogAct, DialogActResultV2 as DialogActResult
from .patterns import (
    _DIALOG_ACT_AFFIRM_PATTERNS,
    _DIALOG_ACT_CANCEL_PATTERNS,
    _DIALOG_ACT_CLARIFY_PATTERNS,
    _DIALOG_ACT_COMMIT_PATTERNS,
    _DIALOG_ACT_DENY_PATTERNS,
    _DIALOG_ACT_NOISE_PATTERNS,
    _DIALOG_ACT_PAUSE_PATTERNS,
    _DIALOG_ACT_REDIRECT_PATTERNS,
    _DIALOG_ACT_STATUS_ACK_PATTERNS,
)

if TYPE_CHECKING:
    import re


class DialogActClassifier:
    """Deterministic dialog act classifier for conversation attention.

    This classifier identifies the semantic function of a message without
    relying on LLM inference. It enables proper attention and intent tracking
    for short user responses like "需要", "不用", "先别".

    Classification order matters: high-priority acts are checked first to ensure
    short confirmations/denials are never treated as low-signal noise.
    """

    # Use fullmatch for strict pattern matching
    _USE_FULLMATCH: bool = True

    def classify(self, text: str, role: str = "user") -> DialogActResult:
        """Classify the dialog act of a message.

        Args:
            text: The message content to classify
            role: The role of the speaker ("user" or "assistant")

        Returns:
            DialogActResult with act, confidence, triggers, and metadata
        """
        content = _normalize_text(text)
        if not content:
            return DialogActResult(act=DialogAct.UNKNOWN, confidence=0.0)

        # High-priority patterns checked FIRST to prevent short responses
        # from being misclassified as noise
        triggers: list[str] = []

        # Check for high-priority acts first (affirm, deny, pause, redirect)
        act, conf, matched = self._check_patterns(content, _DIALOG_ACT_AFFIRM_PATTERNS)
        if matched:
            triggers.extend(matched)
            return DialogActResult(
                act=act,
                confidence=conf,
                triggers=tuple(triggers),
                metadata=(("role", role), ("short_reply", len(content) <= 5)),
            )

        act, conf, matched = self._check_patterns(content, _DIALOG_ACT_DENY_PATTERNS)
        if matched:
            triggers.extend(matched)
            return DialogActResult(
                act=act,
                confidence=conf,
                triggers=tuple(triggers),
                metadata=(("role", role), ("short_reply", len(content) <= 5)),
            )

        act, conf, matched = self._check_patterns(content, _DIALOG_ACT_PAUSE_PATTERNS)
        if matched:
            triggers.extend(matched)
            return DialogActResult(
                act=act,
                confidence=conf,
                triggers=tuple(triggers),
                metadata=(("role", role), ("short_reply", len(content) <= 5)),
            )

        act, conf, matched = self._check_patterns(content, _DIALOG_ACT_REDIRECT_PATTERNS)
        if matched:
            triggers.extend(matched)
            return DialogActResult(
                act=act,
                confidence=conf,
                triggers=tuple(triggers),
                metadata=(("role", role), ("short_reply", len(content) <= 5)),
            )

        act, conf, matched = self._check_patterns(content, _DIALOG_ACT_CLARIFY_PATTERNS)
        if matched:
            triggers.extend(matched)
            return DialogActResult(
                act=act,
                confidence=conf,
                triggers=tuple(triggers),
                metadata=(("role", role),),
            )

        act, conf, matched = self._check_patterns(content, _DIALOG_ACT_COMMIT_PATTERNS)
        if matched:
            triggers.extend(matched)
            return DialogActResult(
                act=act,
                confidence=conf,
                triggers=tuple(triggers),
                metadata=(("role", role),),
            )

        act, conf, matched = self._check_patterns(content, _DIALOG_ACT_CANCEL_PATTERNS)
        if matched:
            triggers.extend(matched)
            return DialogActResult(
                act=act,
                confidence=conf,
                triggers=tuple(triggers),
                metadata=(("role", role),),
            )

        act, conf, matched = self._check_patterns(content, _DIALOG_ACT_STATUS_ACK_PATTERNS)
        if matched:
            triggers.extend(matched)
            return DialogActResult(
                act=act,
                confidence=conf,
                triggers=tuple(triggers),
                metadata=(("role", role),),
            )

        # Noise check (only for truly meaningless content)
        act, conf, matched = self._check_patterns(content, _DIALOG_ACT_NOISE_PATTERNS)
        if matched:
            triggers.extend(matched)
            return DialogActResult(
                act=DialogAct.NOISE,
                confidence=conf,
                triggers=tuple(triggers),
                metadata=(("role", role), ("is_noise", True)),
            )

        # Default: unknown (requires extended analysis or LLM fallback)
        return DialogActResult(
            act=DialogAct.UNKNOWN,
            confidence=0.3,
            metadata=(("role", role), ("requires_extended_analysis", True)),
        )

    def _check_patterns(
        self,
        content: str,
        patterns: tuple[re.Pattern[str], ...],
    ) -> tuple[str, float, list[str]]:
        """Check content against pattern list, return best match.

        Uses fullmatch for strict matching by default to avoid partial matches.

        Returns:
            Tuple of (act, confidence, matched_triggers)
        """
        content_lower = content.lower()
        for pattern in patterns:
            match = pattern.fullmatch(content_lower) if self._USE_FULLMATCH else pattern.search(content_lower)
            if match:
                return (
                    self._pattern_to_act(pattern),
                    0.95,
                    [match.group(0) if match else str(pattern.pattern)],
                )
        return (DialogAct.UNKNOWN, 0.0, [])

    def _pattern_to_act(self, pattern: re.Pattern[str]) -> str:
        """Map pattern type to dialog act."""
        if pattern in _DIALOG_ACT_AFFIRM_PATTERNS:
            return DialogAct.AFFIRM
        elif pattern in _DIALOG_ACT_DENY_PATTERNS:
            return DialogAct.DENY
        elif pattern in _DIALOG_ACT_PAUSE_PATTERNS:
            return DialogAct.PAUSE
        elif pattern in _DIALOG_ACT_REDIRECT_PATTERNS:
            return DialogAct.REDIRECT
        elif pattern in _DIALOG_ACT_CLARIFY_PATTERNS:
            return DialogAct.CLARIFY
        elif pattern in _DIALOG_ACT_COMMIT_PATTERNS:
            return DialogAct.COMMIT
        elif pattern in _DIALOG_ACT_CANCEL_PATTERNS:
            return DialogAct.CANCEL
        elif pattern in _DIALOG_ACT_STATUS_ACK_PATTERNS:
            return DialogAct.STATUS_ACK
        elif pattern in _DIALOG_ACT_NOISE_PATTERNS:
            return DialogAct.NOISE
        return DialogAct.UNKNOWN
