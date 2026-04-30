"""Text-to-structure port helpers for State-First Context OS runtime.

This module provides bridge functions that convert raw text and working state
into structured decisions, constraints, and classifications.
"""

from __future__ import annotations

import re

from ..helpers import _normalize_text, _trim_text
from ..models_v2 import WorkingStateV2 as WorkingState
from ..patterns import _ASSISTANT_FOLLOWUP_PATTERNS, _CONSTRAINT_PREFIX_RE, _NEGATIVE_RESPONSE_PATTERNS

# Artifact offloading constants
MAX_INLINE_CHARS: int = 500  # Artifacts larger than this use stubs
MAX_STUB_CHARS: int = 200  # Stub content is capped


def _extract_assistant_followup_action(text: str) -> str:
    content = _normalize_text(text)
    if not content:
        return ""
    for pattern in _ASSISTANT_FOLLOWUP_PATTERNS:
        match = pattern.search(content)
        if match is None:
            continue
        action = _normalize_text(match.group("action"))
        if not action:
            continue
        action = re.sub(r"^[,\uFF0C\u3002:\uFF1A;\-\s]+", "", action).strip()
        action = re.sub(r"[?\uFF1F!\uFF01\u3002]+$", "", action).strip()
        if action:
            return _trim_text(action, max_chars=220)
    return ""


def _is_negative_response(text: str) -> bool:
    content = _normalize_text(text)
    if not content:
        return False
    return any(pattern.fullmatch(content) for pattern in _NEGATIVE_RESPONSE_PATTERNS)


def _decision_kind(summary: str) -> str:
    lowered = _normalize_text(summary).lower()
    if not lowered:
        return "decision"
    if any(token in lowered for token in ("plan", "blueprint", "方案", "计划", "蓝图")):
        return "accepted_plan"
    if any(token in lowered for token in ("must", "must not", "do not", "禁止", "不要", "必须")):
        return "constraint"
    if any(token in lowered for token in ("blocked", "阻塞", "等待", "依赖")):
        return "blocked_on"
    return "decision"


def _extract_hard_constraints(working_state: WorkingState) -> tuple[str, ...]:
    values: list[str] = []
    for collection in (
        working_state.user_profile.preferences,
        working_state.user_profile.persistent_facts,
        working_state.task_state.blocked_on,
    ):
        for item in collection:
            if _CONSTRAINT_PREFIX_RE.search(item.value):
                values.append(item.value)
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return tuple(deduped[:6])


def _is_affirmative_response(text: str) -> bool:
    """Check if text is an affirmative response (imported from classifier patterns)."""
    from ..patterns import _AFFIRMATIVE_RESPONSE_PATTERNS

    content = _normalize_text(text)
    if not content:
        return False
    return any(pattern.fullmatch(content) for pattern in _AFFIRMATIVE_RESPONSE_PATTERNS)
