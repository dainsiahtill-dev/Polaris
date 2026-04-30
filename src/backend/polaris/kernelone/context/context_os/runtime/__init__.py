"""State-First Context OS runtime package.

This package splits the monolithic runtime into focused modules:
- engine: Main runtime execution engine (StateFirstContextOS)
- state: State management and transitions
- ports: Text-to-structure port helpers
- scheduler: Task scheduling and queue management

All public symbols are re-exported here for backward compatibility.
"""

from __future__ import annotations

from .engine import StateFirstContextOS
from .ports import (
    MAX_INLINE_CHARS,
    MAX_STUB_CHARS,
    _decision_kind,
    _extract_assistant_followup_action,
    _extract_hard_constraints,
    _is_affirmative_response,
    _is_negative_response,
)

__all__ = [
    "MAX_INLINE_CHARS",
    "MAX_STUB_CHARS",
    "StateFirstContextOS",
    "_decision_kind",
    "_extract_assistant_followup_action",
    "_extract_hard_constraints",
    "_is_affirmative_response",
    "_is_negative_response",
]
