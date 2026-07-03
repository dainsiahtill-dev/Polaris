"""Run-ledger failure evidence taxonomy.

This module is the platform-level failure-class vocabulary for execution
control-plane evidence. It intentionally lives in ``control_plane.run_ledger``
because Run Ledger projections, QA verdicts and runtime result mapping all need
the same terms without importing role-runtime internals.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FailureClassV1(str, Enum):
    """Canonical failure classes carried through run-ledger evidence."""

    TOOL_DISPATCH_DROPPED = "TOOL_DISPATCH_DROPPED"
    MISSING_BATCH_RECEIPT = "MISSING_BATCH_RECEIPT"
    MISSING_EFFECT_RECEIPT = "MISSING_EFFECT_RECEIPT"
    MISSING_TOOL_RESULT = "MISSING_TOOL_RESULT"
    TOOL_RESULT_FAILED = "TOOL_RESULT_FAILED"
    TOOL_LIFECYCLE_FAILED = "TOOL_LIFECYCLE_FAILED"
    TOOL_LIFECYCLE_UNKNOWN = "TOOL_LIFECYCLE_UNKNOWN"
    TOOL_LIFECYCLE_MISSING = "TOOL_LIFECYCLE_MISSING"


def _failure_class_key(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip()).strip("_").casefold()


_FAILURE_CLASS_BY_KEY: dict[str, FailureClassV1] = {_failure_class_key(item.value): item for item in FailureClassV1}


def normalize_failure_class(value: Any, *, default: str | FailureClassV1 = "") -> str:
    """Normalize *value* to a canonical failure-class string when known.

    Unknown non-empty classes are preserved so older or experiment-specific
    evidence remains visible instead of being collapsed into an opaque bucket.
    """
    if isinstance(value, FailureClassV1):
        return value.value
    raw = str(value or "").strip()
    if not raw:
        return str(default.value if isinstance(default, FailureClassV1) else default)
    known = _FAILURE_CLASS_BY_KEY.get(_failure_class_key(raw))
    return known.value if known is not None else raw


def is_failure_class(value: Any, expected: FailureClassV1) -> bool:
    """Return whether *value* denotes *expected*, case-insensitively."""
    return normalize_failure_class(value).casefold() == expected.value.casefold()


@dataclass(frozen=True)
class FailureEvidenceV1:
    """Structured failure evidence suitable for Run Ledger and QA projections."""

    failure_class: str
    responsible_layer: str
    reason: str
    evidence_refs: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "failure_evidence.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "failure_class": normalize_failure_class(self.failure_class),
            "responsible_layer": str(self.responsible_layer or "").strip(),
            "reason": str(self.reason or "").strip(),
            "evidence_refs": [str(item).strip() for item in self.evidence_refs if str(item).strip()],
            "metadata": dict(self.metadata),
        }


__all__ = [
    "FailureClassV1",
    "FailureEvidenceV1",
    "is_failure_class",
    "normalize_failure_class",
]
