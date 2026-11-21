"""Validated contracts for optional semantic advice and per-node summaries.

These validators are host boundaries. They never execute a model or grant any
engineering effect; output remains advice until the request compiler accepts it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Any


def _tokens(value: str) -> int:
    return max(1, (len(value) + 3) // 4) if value else 0


class AdviceRelation(StrEnum):
    EDIT_RELATED = "EDIT_RELATED"
    FAILURE_CANDIDATE = "FAILURE_CANDIDATE"
    REQUIREMENT_CANDIDATE = "REQUIREMENT_CANDIDATE"
    BACKGROUND = "BACKGROUND"
    UNKNOWN = "UNKNOWN"


class SuggestedView(StrEnum):
    KEEP_CURRENT = "KEEP_CURRENT"
    FULL = "FULL"
    SLICE = "SLICE"
    CARD = "CARD"
    REFERENCE = "REFERENCE"
    ABSTAIN = "ABSTAIN"


class ValidationResult(StrEnum):
    PENDING = "PENDING"
    VALID = "VALID"
    STALE = "STALE"
    REJECTED = "REJECTED"
    TIMED_OUT = "TIMED_OUT"


class CandidateRefs:
    """Authorized references available to one bounded advisor input package."""

    def __init__(self, *, node_ids: set[str], slice_ids: set[str], evidence_refs: set[str]) -> None:
        self.node_ids = node_ids
        self.slice_ids = slice_ids
        self.evidence_refs = evidence_refs


def build_advice_envelope(
    *,
    advice_job_id: str,
    input_package: Mapping[str, Any],
    source_snapshot_ids: list[str],
    related_requirement_versions: list[str],
    related_failure_versions: list[str],
    model_id: str,
    model_revision: str,
    prompt_version: str,
    created_at: float,
    finished_at: float,
    validation_result: ValidationResult,
) -> dict[str, Any]:
    """Attach host-owned provenance to a validated or rejected advisor result."""

    fingerprint = hashlib.sha256(
        json.dumps(input_package, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "advice_job_id": advice_job_id,
        "source_input_fingerprint": fingerprint,
        "source_snapshot_ids": source_snapshot_ids,
        "related_requirement_versions": related_requirement_versions,
        "related_failure_versions": related_failure_versions,
        "model_id": model_id,
        "model_revision": model_revision,
        "prompt_version": prompt_version,
        "schema_version": "semantic-advice.v1",
        "sampling_configuration": {"temperature": 0},
        "created_at": created_at,
        "finished_at": finished_at,
        "validation_result": validation_result.value,
    }


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


def _enum(value: object, enum_type: type[StrEnum], label: str) -> StrEnum:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string enum")
    try:
        return enum_type(value)
    except ValueError as error:
        raise ValueError(f"{label} has an unsupported value: {value}") from error


def validate_semantic_advice(payload: object, candidates: CandidateRefs) -> dict[str, Any]:
    """Validate SemanticAdvice v1 structure and every reference against the input."""

    root = _object(payload, "semantic advice")
    if set(root) != {"assessments", "need_evidence"}:
        raise ValueError("semantic advice must contain only assessments and need_evidence")
    raw_assessments = root["assessments"]
    if not isinstance(raw_assessments, list) or len(raw_assessments) > 8:
        raise ValueError("assessments must contain at most 8 entries")
    assessments: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()
    for raw in raw_assessments:
        item = _object(raw, "assessment")
        required = {"node_id", "relation", "suggested_view", "keep_slice_ids", "evidence_refs", "uncertain", "reason"}
        if set(item) != required:
            raise ValueError("assessment has missing or additional fields")
        node_id = _text(item["node_id"], "node_id", maximum=160)
        if node_id not in candidates.node_ids or node_id in seen_nodes:
            raise ValueError(f"assessment has an unauthorized or duplicate node_id: {node_id}")
        seen_nodes.add(node_id)
        relation = _enum(item["relation"], AdviceRelation, "relation").value
        view = _enum(item["suggested_view"], SuggestedView, "suggested_view").value
        slices = _refs(item["keep_slice_ids"], "keep_slice_ids", candidates.slice_ids, maximum=8)
        if view == SuggestedView.SLICE.value and not slices:
            raise ValueError("SLICE requires at least one keep_slice_id")
        if view != SuggestedView.SLICE.value and slices:
            raise ValueError("keep_slice_ids is only valid for SLICE")
        assessments.append(
            {
                "node_id": node_id,
                "relation": relation,
                "suggested_view": view,
                "keep_slice_ids": slices,
                "evidence_refs": _refs(item["evidence_refs"], "evidence_refs", candidates.evidence_refs, maximum=12),
                "uncertain": _boolean(item["uncertain"], "uncertain"),
                "reason": _text(item["reason"], "reason", maximum=160),
            }
        )
    raw_need = root["need_evidence"]
    if not isinstance(raw_need, list) or len(raw_need) > 4:
        raise ValueError("need_evidence must contain at most 4 entries")
    needs: list[dict[str, Any]] = []
    for raw in raw_need:
        item = _object(raw, "need_evidence")
        if set(item) != {"node_id", "requested_slice_ids", "reason"}:
            raise ValueError("need_evidence has missing or additional fields")
        node_id = _text(item["node_id"], "need_evidence.node_id", maximum=160)
        if node_id not in candidates.node_ids:
            raise ValueError(f"need_evidence has an unauthorized node_id: {node_id}")
        needs.append(
            {
                "node_id": node_id,
                "requested_slice_ids": _refs(
                    item["requested_slice_ids"], "requested_slice_ids", candidates.slice_ids, maximum=4
                ),
                "reason": _text(item["reason"], "need_evidence.reason", maximum=160),
            }
        )
    return {"schema_version": "semantic-advice.v1", "assessments": assessments, "need_evidence": needs}


def _normalize_advice_slices(payload: object, candidates: CandidateRefs) -> object:
    """Drop invented slice ids; never invent evidence or precise source coverage."""

    if not isinstance(payload, dict):
        return payload
    assessments = payload.get("assessments")
    if isinstance(assessments, list):
        for item in assessments:
            if not isinstance(item, dict) or not isinstance(item.get("keep_slice_ids"), list):
                continue
            slices = list(dict.fromkeys(ref for ref in item["keep_slice_ids"] if ref in candidates.slice_ids))
            item["keep_slice_ids"] = slices
            if item.get("suggested_view") == SuggestedView.SLICE.value and not slices:
                item["suggested_view"] = SuggestedView.CARD.value
    needs = payload.get("need_evidence")
    if isinstance(needs, list):
        normalized_needs: list[object] = []
        for item in needs:
            if not isinstance(item, dict) or not isinstance(item.get("requested_slice_ids"), list):
                normalized_needs.append(item)
                continue
            slices = list(dict.fromkeys(ref for ref in item["requested_slice_ids"] if ref in candidates.slice_ids))
            if slices:
                item["requested_slice_ids"] = slices
                normalized_needs.append(item)
        payload["need_evidence"] = normalized_needs
    return payload


def parse_semantic_advice(raw: str, candidates: CandidateRefs) -> dict[str, Any]:
    """Parse and validate a model response; malformed JSON never becomes advice."""

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("semantic advice is not valid JSON") from error
    return validate_semantic_advice(_normalize_advice_slices(payload, candidates), candidates)


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
