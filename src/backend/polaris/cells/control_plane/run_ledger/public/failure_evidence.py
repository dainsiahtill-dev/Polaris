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


def merge_failure_evidence_rows(
    existing: Any,
    *new_rows: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return JSON-safe failure evidence rows with stable de-duplication.

    Existing metadata fields may carry a single mapping, a list/tuple of
    mappings, or malformed legacy values. Only mapping rows are authoritative
    evidence; malformed values are ignored instead of being string-parsed.
    Complexity is O(n*m) in row count and row width for equality checks; current
    evidence lists are tiny, and preserving exact row dictionaries avoids
    lossy hashes or ad-hoc identity keys.
    """

    rows: list[dict[str, Any]] = []
    if isinstance(existing, Mapping):
        rows.append(dict(existing))
    elif isinstance(existing, (list, tuple)):
        rows.extend(dict(item) for item in existing if isinstance(item, Mapping))
    for row in new_rows:
        candidate = dict(row)
        if candidate not in rows:
            rows.append(candidate)
    return rows


def _dedupe_text_tokens(values: Any) -> tuple[str, ...]:
    tokens: list[str] = []
    seen: set[str] = set()
    try:
        iterator = iter(values)
    except TypeError:
        return ()
    for item in iterator:
        token = str(item or "").strip()
        if token and token not in seen:
            tokens.append(token)
            seen.add(token)
    return tuple(tokens)


def merge_failure_evidence_payload(
    existing: Mapping[str, Any] | None,
    raw_evidence: Any,
) -> dict[str, Any]:
    """Merge aggregate/runtime failure-evidence payloads without prose parsing.

    Boundary:
        Mapping payloads are treated as already-shaped projections and overlay
        existing keys. List/tuple payloads are treated as structured evidence
        rows and projected into ``items``, ``failure_classes`` and
        ``evidence_refs``. Malformed values are ignored.

    Complexity:
        O(n*m) for row de-duplication plus O(n) for summary token projection.
    """

    payload = dict(existing) if isinstance(existing, Mapping) else {}
    if isinstance(raw_evidence, Mapping):
        raw_mapping = dict(raw_evidence)
        payload.update(raw_mapping)
        for nested_key in ("failure_evidence", "items"):
            payload = _merge_failure_evidence_rows_into_payload(payload, raw_mapping.get(nested_key))
        return payload
    return _merge_failure_evidence_rows_into_payload(payload, raw_evidence)


def _merge_failure_evidence_rows_into_payload(
    payload: dict[str, Any],
    raw_rows: Any,
) -> dict[str, Any]:
    if not isinstance(raw_rows, (list, tuple)):
        return payload
    rows = [dict(item) for item in raw_rows if isinstance(item, Mapping)]
    if not rows:
        return payload
    payload["items"] = merge_failure_evidence_rows(payload.get("items"), *rows)
    payload["failure_classes"] = _dedupe_text_tokens(
        [
            *list(payload.get("failure_classes") or ()),
            *(str(row.get("failure_class") or "") for row in rows),
        ]
    )
    payload["evidence_refs"] = _dedupe_text_tokens(
        [
            *list(payload.get("evidence_refs") or ()),
            *(
                str(ref or "")
                for row in rows
                for ref in (
                    row.get("evidence_refs")
                    if isinstance(row.get("evidence_refs"), (list, tuple))
                    else ()
                )
            ),
        ]
    )
    return payload


def summarize_failure_evidence_rows(
    rows: Any,
    *,
    existing_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the canonical metadata summary for failure evidence rows.

    Boundary:
        This helper summarizes already-structured evidence. It does not parse
        prose diagnostics and intentionally ignores malformed legacy rows.

    Complexity:
        O(n) time and memory for normalizing the row list.
    """

    evidence_rows = merge_failure_evidence_rows(rows)
    summary = dict(existing_summary) if isinstance(existing_summary, Mapping) else {}
    summary["count"] = len(evidence_rows)
    summary["latest_failure_class"] = evidence_rows[-1].get("failure_class") if evidence_rows else None
    return summary


def append_failure_evidence_to_metadata(
    metadata: dict[str, Any],
    *new_rows: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Append structured failure evidence rows and refresh metadata summary.

    Boundary:
        This is the single Run Ledger public projection helper for the legacy
        ``failure_evidence`` / ``failure_evidence_summary`` metadata pair. It
        does not parse prose diagnostics or infer failure classes.

    Complexity:
        O(n*m) time for stable row de-duplication, matching
        :func:`merge_failure_evidence_rows`; O(n) memory for the projected rows.
    """

    rows = merge_failure_evidence_rows(metadata.get("failure_evidence"), *new_rows)
    metadata["failure_evidence"] = rows
    summary = metadata.get("failure_evidence_summary")
    metadata["failure_evidence_summary"] = summarize_failure_evidence_rows(
        rows,
        existing_summary=summary if isinstance(summary, Mapping) else None,
    )
    return rows


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
    "append_failure_evidence_to_metadata",
    "is_failure_class",
    "merge_failure_evidence_payload",
    "merge_failure_evidence_rows",
    "normalize_failure_class",
    "summarize_failure_evidence_rows",
]
