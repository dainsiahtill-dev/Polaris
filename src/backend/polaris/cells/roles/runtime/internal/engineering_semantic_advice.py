"""Validated contracts for per-node distiller summaries.

These validators are host boundaries. They never execute a model or grant any
engineering effect; a summary only reaches the request through the host's fold
projection.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


def _tokens(value: str) -> int:
    return max(1, (len(value) + 3) // 4) if value else 0


class CandidateRefs:
    """Authorized references available to one bounded distiller input package."""

    def __init__(self, *, node_ids: set[str], slice_ids: set[str], evidence_refs: set[str]) -> None:
        self.node_ids = node_ids
        self.slice_ids = slice_ids
        self.evidence_refs = evidence_refs


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _text(value: object, label: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} must be nonempty text of at most {maximum} characters")
    return value


def _boolean(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be boolean")
    return value


def _refs(value: object, label: str, allowed: set[str], *, maximum: int) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{label} must be a list of at most {maximum} references")
    result: list[str] = []
    for item in value:
        reference = _text(item, label, maximum=160)
        if reference not in allowed:
            raise ValueError(f"{label} contains an unauthorized reference: {reference}")
        if reference in result:
            raise ValueError(f"{label} contains duplicate references")
        result.append(reference)
    return result


_STOCK_SUMMARY_MARKERS = frozenset(
    {
        "completed",
        "done",
        "ok",
        "success",
        "failed",
        "command failed",
        "passed",
        "error",
        "unknown",
        "skipped",
        "skip",
    }
)
_MIN_INFORMATIVE_SUMMARY_CHARS = 4


def _summary_is_informative(summary: str, content: object) -> bool:
    """Require a summary that explains the payload, never a status word or excerpt.

    Folding replaces the payload with this text in the next request, so a bare
    status (``completed``) or a verbatim fragment of the input destroys evidence
    without buying understanding. Rejecting here keeps the non-explanation out
    of the distiller store so the host structural fallback is used instead.
    """

    stripped = summary.strip()
    if len(stripped) < _MIN_INFORMATIVE_SUMMARY_CHARS:
        return False
    lowered = stripped.lower().rstrip(".")
    if lowered in _STOCK_SUMMARY_MARKERS:
        return False
    # "completed successfully" is still a status, not an explanation.
    if len(stripped.split()) <= 3 and any(lowered.startswith(marker) for marker in _STOCK_SUMMARY_MARKERS):
        return False
    if isinstance(content, str) and content.strip():
        normalized = re.sub(r"\s+", " ", stripped).lower()
        original = re.sub(r"\s+", " ", content).lower()
        if normalized in original:
            return False
    return True


def validate_node_summary(payload: object, request: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one NodeSummary response against its exact snapshot and coverage."""

    root = _object(payload, "node summary")
    required = {"node_id", "snapshot_id", "summary", "summary_kind", "coverage", "evidence_refs", "uncertain", "reason"}
    if set(root) != required:
        raise ValueError("node summary has missing or additional fields")
    for key in ("node_id", "snapshot_id"):
        if root[key] != request.get(key):
            raise ValueError(f"node summary {key} does not match the requested snapshot")
    summary = _text(root["summary"], "summary", maximum=1024)
    if not _summary_is_informative(summary, request.get("content")):
        raise ValueError("summary must describe the payload, not a status word or a verbatim excerpt")
    max_tokens = request.get("max_summary_tokens", 192)
    if type(max_tokens) is not int or max_tokens < 1 or _tokens(summary) > max_tokens:
        raise ValueError("summary exceeds max_summary_tokens")
    if root["summary_kind"] != "model_inference":
        raise ValueError("summary_kind must be model_inference")
    coverage = _object(root["coverage"], "coverage")
    if set(coverage) != {"observed_line_ranges", "summary_complete"}:
        raise ValueError("coverage has missing or additional fields")
    if coverage["observed_line_ranges"] != request.get("coverage", {}).get("observed_line_ranges"):
        raise ValueError("summary coverage differs from the requested source coverage")
    _boolean(coverage["summary_complete"], "summary_complete")
    allowed = set(request.get("allowed_evidence_refs") or []) | {str(request["snapshot_id"])}
    return {
        "schema_version": "node-summary.v1",
        "node_id": root["node_id"],
        "snapshot_id": root["snapshot_id"],
        "summary": summary,
        "summary_kind": "model_inference",
        "coverage": {
            "observed_line_ranges": coverage["observed_line_ranges"],
            "summary_complete": coverage["summary_complete"],
        },
        "evidence_refs": _refs(root["evidence_refs"], "summary.evidence_refs", allowed, maximum=12),
        "uncertain": _boolean(root["uncertain"], "uncertain"),
        "reason": _text(root["reason"], "reason", maximum=160),
    }
