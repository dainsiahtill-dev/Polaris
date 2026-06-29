"""Final provider-request audit evidence projection helpers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, MutableMapping
from typing import Any

FINAL_REQUEST_EVIDENCE_SCHEMA = "llm.final_request_evidence.v1"
AUDIT_REFS_SCHEMA = "llm.final_request_audit_refs.v1"


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    token = str(value or "").strip()
    return token


def _first_text(*values: Any) -> str:
    for value in values:
        token = _text(value)
        if token:
            return token
    return ""


def _first_mapping(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [token for item in value if (token := _text(item))]


def _stable_hash(value: Any) -> str:
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        payload = str(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_final_request_evidence(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return the canonical lightweight projection for final-request audit evidence.

    The returned payload is derived from already emitted LLM event metadata. It is
    not a new authority source; it makes the existing final provider request audit
    directly discoverable from JSONL/runtime events without role-specific reverse
    lookups.
    """

    root = _as_mapping(data)
    metadata = _as_mapping(root.get("metadata"))
    extra_fields = _as_mapping(metadata.get("extra_fields"))
    request_context = _as_mapping(root.get("context"))
    existing_evidence = _as_mapping(root.get("final_request_evidence"))

    final_request_context_audit = _first_mapping(
        root.get("final_request_context_audit"),
        metadata.get("final_request_context_audit"),
        extra_fields.get("final_request_context_audit"),
        existing_evidence.get("final_request_context_audit"),
    )
    context_snapshot_ref = _first_text(
        root.get("context_snapshot_ref"),
        root.get("contextSnapshotRef"),
        metadata.get("context_snapshot_ref"),
        metadata.get("contextSnapshotRef"),
        extra_fields.get("context_snapshot_ref"),
        extra_fields.get("contextSnapshotRef"),
        request_context.get("context_snapshot_ref"),
        request_context.get("contextSnapshotRef"),
        existing_evidence.get("context_snapshot_ref"),
    )

    if not final_request_context_audit and not context_snapshot_ref:
        return {}

    evidence_coverage = _first_mapping(
        existing_evidence.get("final_request_evidence_coverage"),
        final_request_context_audit.get("final_request_evidence_coverage"),
    )
    context_quality = _first_mapping(
        final_request_context_audit.get("context_quality"), existing_evidence.get("context_quality")
    )
    coverage_flags = _first_mapping(final_request_context_audit.get("coverage"), existing_evidence.get("coverage"))
    audit_hash = _first_text(existing_evidence.get("final_request_context_audit_hash"))
    if not audit_hash and final_request_context_audit:
        audit_hash = _stable_hash(final_request_context_audit)
    request_hash = _first_text(
        existing_evidence.get("request_hash"),
        evidence_coverage.get("request_hash"),
        final_request_context_audit.get("request_hash"),
    )
    missing_required_refs = _string_list(
        evidence_coverage.get("missing_required_refs") or existing_evidence.get("missing_required_refs")
    )
    missing_required_tools = _string_list(
        evidence_coverage.get("missing_required_tools") or existing_evidence.get("missing_required_tools")
    )
    coverage_pass = (
        evidence_coverage.get("pass")
        if evidence_coverage
        else existing_evidence.get("final_request_evidence_coverage_pass")
    )

    payload: dict[str, Any] = {
        "schema_version": FINAL_REQUEST_EVIDENCE_SCHEMA,
        "context_snapshot_ref": context_snapshot_ref,
        "final_request_context_audit_present": bool(
            final_request_context_audit or existing_evidence.get("final_request_context_audit_present")
        ),
        "final_request_context_audit_hash": audit_hash,
        "request_hash": request_hash,
        "final_request_evidence_coverage_pass": coverage_pass,
        "missing_required_refs": missing_required_refs,
        "missing_required_tools": missing_required_tools,
        "coverage": coverage_flags,
        "context_underutilized": final_request_context_audit.get("context_underutilized")
        if final_request_context_audit
        else None,
        "context_window_utilization": final_request_context_audit.get("context_window_utilization")
        if final_request_context_audit
        else None,
        "final_request_token_estimate": final_request_context_audit.get("final_request_token_estimate")
        if final_request_context_audit
        else None,
    }
    if context_quality:
        payload["context_quality"] = {
            "context_needs_review": bool(context_quality.get("context_needs_review")),
            "missing_coverage": _string_list(context_quality.get("missing_coverage")),
            "missing_required_refs": _string_list(context_quality.get("missing_required_refs")),
            "missing_required_tools": _string_list(context_quality.get("missing_required_tools")),
        }

    payload["final_request_evidence_hash"] = _stable_hash(
        {key: value for key, value in payload.items() if key != "final_request_evidence_hash"}
    )
    return payload


def attach_final_request_evidence(payload: MutableMapping[str, Any], data: Mapping[str, Any]) -> dict[str, Any]:
    """Attach final-request audit refs to a durable or realtime LLM event payload."""

    evidence = build_final_request_evidence(data)
    if not evidence:
        return {}

    context_snapshot_ref = _text(evidence.get("context_snapshot_ref"))
    audit_hash = _text(evidence.get("final_request_context_audit_hash"))
    evidence_hash = _text(evidence.get("final_request_evidence_hash"))
    if context_snapshot_ref:
        payload["context_snapshot_ref"] = context_snapshot_ref
    if audit_hash:
        payload["final_request_context_audit_hash"] = audit_hash
    if evidence_hash:
        payload["final_request_evidence_hash"] = evidence_hash

    final_request_context_audit = _first_mapping(
        _as_mapping(data).get("final_request_context_audit"),
        _as_mapping(_as_mapping(data).get("metadata")).get("final_request_context_audit"),
        _as_mapping(_as_mapping(_as_mapping(data).get("metadata")).get("extra_fields")).get(
            "final_request_context_audit"
        ),
    )
    if final_request_context_audit:
        payload["final_request_context_audit"] = final_request_context_audit
    payload["final_request_evidence"] = evidence

    audit_refs = dict(_as_mapping(payload.get("audit_refs")))
    audit_refs.update(
        {
            "schema_version": AUDIT_REFS_SCHEMA,
            "context_snapshot_ref": context_snapshot_ref,
            "final_request_context_audit_hash": audit_hash,
            "final_request_evidence_hash": evidence_hash,
            "request_hash": _text(evidence.get("request_hash")),
        }
    )
    payload["audit_refs"] = audit_refs
    return evidence
