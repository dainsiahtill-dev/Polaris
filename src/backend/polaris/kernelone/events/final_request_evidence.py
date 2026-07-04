"""Final provider-request audit evidence projection helpers."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, MutableMapping
from typing import Any

FINAL_REQUEST_EVIDENCE_SCHEMA = "llm.final_request_evidence.v1"
FINAL_REQUEST_EVIDENCE_AUTHORITY_SCHEMA = "polaris.final_request_evidence_authority.v1"
AUDIT_REFS_SCHEMA = "llm.final_request_audit_refs.v1"
_CONTEXT_SNAPSHOT_HASH_RE = re.compile(r"(?<![0-9A-Fa-f])([0-9A-Fa-f]{24})(?![0-9A-Fa-f])")


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

def normalize_context_snapshot_ref(value: Any) -> str:
    """Return the canonical ContextStore snapshot hash for final-request refs.

    New events must expose a single 24-hex ContextStore hash. Historical events
    may carry paths such as ``runtime/contexts/aa/<hash>.json`` or URI-like
    wrappers; those are accepted only when a valid hash can be extracted.
    """

    token = _text(value)
    if not token:
        return ""
    match = _CONTEXT_SNAPSHOT_HASH_RE.search(token)
    if not match:
        return ""
    return match.group(1).lower()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [token for item in value if (token := _text(item))]


def _string_tokens(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        return []
    return [token for item in raw_items if (token := _text(item))]


def _bool_value(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    return default


def _list_value(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def missing_required_refs_from_evidence_slots(evidence_coverage: Mapping[str, Any]) -> list[str]:
    """Return required evidence refs marked missing by structured evidence slots."""

    slots = evidence_coverage.get("evidence_slots")
    if not isinstance(slots, list):
        return []
    missing_refs: list[str] = []
    seen: set[str] = set()
    for item in slots:
        if not isinstance(item, Mapping):
            continue
        if _text(item.get("schema_version")) != "polaris.final_request_evidence_slot.v1":
            continue
        if item.get("required") is not True or item.get("missing") is not True:
            continue
        ref_type = _text(item.get("ref_type"))
        if ref_type and ref_type not in seen:
            seen.add(ref_type)
            missing_refs.append(ref_type)
    return missing_refs


def missing_required_tools_from_tool_slots(evidence_coverage: Mapping[str, Any]) -> list[str] | None:
    """Return required tools marked missing by structured tool-evidence slots."""

    slots = evidence_coverage.get("tool_evidence_slots")
    if not isinstance(slots, list):
        return None
    missing_tools: list[str] = []
    seen: set[str] = set()
    for item in slots:
        if not isinstance(item, Mapping):
            continue
        if _text(item.get("schema_version")) != "polaris.final_request_tool_slot.v1":
            continue
        if item.get("required") is not True or item.get("missing") is not True:
            continue
        tool_name = _text(item.get("tool_name"))
        if tool_name and tool_name not in seen:
            seen.add(tool_name)
            missing_tools.append(tool_name)
    return missing_tools


def build_final_request_evidence_slots(
    *,
    coverage_sources: list[dict[str, Any]],
    required_refs: list[str],
    included_refs: list[str],
    missing_required_refs: list[str],
) -> list[dict[str, Any]]:
    """Build typed final-request evidence slots from coverage refs.

    Boundary:
        The slot schema is the KernelOne final-request evidence contract.
        Callers may decide which refs are required/present/missing, but should
        not locally duplicate the slot shape.
    """

    required = set(required_refs)
    included = set(included_refs)
    missing = set(missing_required_refs)
    slots: list[dict[str, Any]] = []
    for source in coverage_sources:
        ref_type = _text(source.get("ref_type"))
        if not ref_type:
            continue
        slot = {
            "schema_version": "polaris.final_request_evidence_slot.v1",
            "ref_type": ref_type,
            "required": ref_type in required,
            "present": ref_type in included,
            "missing": ref_type in missing,
            "source": _text(source.get("source") or "final_provider_request"),
            "confidence": _text(source.get("confidence") or "absent"),
            "freshness": _text(source.get("freshness") or "unknown"),
        }
        hash_value = _text(source.get("hash"))
        if hash_value:
            slot["hash"] = hash_value
        details = source.get("details")
        if isinstance(details, Mapping) and details:
            slot["details"] = dict(details)
        slots.append(slot)
    return slots


def build_final_request_tool_slots(
    *,
    required_tools: list[str],
    available_tools: list[str],
    missing_required_tools: list[str],
) -> list[dict[str, Any]]:
    """Build typed final-request tool slots from tool coverage facts."""

    required = set(required_tools)
    available = set(available_tools)
    missing = set(missing_required_tools)
    slots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tool_name in [*required_tools, *available_tools]:
        token = _text(tool_name)
        if not token or token in seen:
            continue
        seen.add(token)
        slots.append(
            {
                "schema_version": "polaris.final_request_tool_slot.v1",
                "tool_name": token,
                "required": token in required,
                "present": token in available,
                "missing": token in missing,
                "source": "final_provider_request.tools",
                "confidence": "tool_schema" if token in available else "absent",
                "freshness": "current_turn" if token in available else "unknown",
            }
        )
    return slots


def looks_like_workspace_quality_evidence_payload(value: Any) -> bool:
    """Return whether *value* is structured workspace-quality evidence.

    Boundary:
        This predicate recognizes already-shaped workspace/artifact quality
        evidence for final-request context-slot discovery. It checks schema and
        structural keys only; it does not parse diagnostic prose.

    Complexity:
        O(k) time over a fixed key set; O(1) memory.
    """

    if not isinstance(value, Mapping):
        return False
    schema_version = _text(value.get("schema_version")).lower()
    if "workspace_quality" in schema_version or "artifact_quality" in schema_version:
        return True
    return any(
        key in value
        for key in (
            "all_checks_passed",
            "quality_errors",
            "deterministic_checks",
            "real_run_gate",
            "verifier_results",
            "failed_required_modalities",
            "missing_required_modalities",
        )
    )


def summarize_workspace_quality_evidence_context_slot(value: Any) -> dict[str, Any]:
    """Project workspace-quality context evidence into the final-request slot shape.

    Boundary:
        This helper owns the UI-facing summary shape for workspace/artifact
        quality evidence in final-request audit coverage. It consumes
        already-structured payloads and does not inspect raw diagnostic prose.

    Complexity:
        O(n) over small modality/check lists; O(n) memory for normalized tokens.
    """

    found = _as_mapping(value)
    if not found:
        return {}
    return {
        "schema_version": "polaris.workspace_quality_evidence.context_slot.v1",
        "source_schema_version": _text(found.get("schema_version")),
        "source": _text(found.get("source") or found.get("modality") or "workspace_quality_evidence"),
        "all_checks_passed": _bool_value(found.get("all_checks_passed")),
        "quality_error_count": len(found.get("quality_errors") or [])
        if isinstance(found.get("quality_errors"), (list, tuple))
        else 0,
        "deterministic_check_count": len(_string_tokens(found.get("deterministic_checks"))),
        "failed_required_modalities": _string_tokens(found.get("failed_required_modalities")),
        "missing_required_modalities": _string_tokens(found.get("missing_required_modalities")),
    }


def _stable_hash(value: Any) -> str:
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        payload = str(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_final_request_evidence_authority(
    *,
    evidence_coverage: Mapping[str, Any],
    existing_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    existing_authority = _as_mapping(existing_evidence.get("final_request_evidence_authority"))
    if not evidence_coverage and not existing_authority:
        return {}

    role_identity_ok_source = (
        existing_authority.get("role_identity_ok")
        if "role_identity_ok" in existing_authority
        else evidence_coverage.get("role_identity_ok")
    )
    pass_source = existing_authority.get("pass") if "pass" in existing_authority else evidence_coverage.get("pass")
    authority: dict[str, Any] = {
        "schema_version": FINAL_REQUEST_EVIDENCE_AUTHORITY_SCHEMA,
        "request_hash": _first_text(existing_authority.get("request_hash"), evidence_coverage.get("request_hash")),
        "role_id": _first_text(existing_authority.get("role_id"), evidence_coverage.get("role_id")),
        "expected_role_id": _first_text(
            existing_authority.get("expected_role_id"),
            evidence_coverage.get("expected_role_id"),
        ),
        "role_identity_ok": _bool_or_none(role_identity_ok_source),
        "required_refs": _string_list(
            existing_authority.get("required_refs") or evidence_coverage.get("required_refs")
        ),
        "included_refs": _string_list(
            existing_authority.get("included_refs") or evidence_coverage.get("included_refs")
        ),
        "missing_required_refs": missing_required_refs_from_evidence_slots(evidence_coverage)
        or _string_list(existing_authority.get("missing_required_refs") or evidence_coverage.get("missing_required_refs")),
        "required_tools": _string_list(
            existing_authority.get("required_tools") or evidence_coverage.get("required_tools")
        ),
        "available_tools": _string_list(
            existing_authority.get("available_tools") or evidence_coverage.get("available_tools")
        ),
        "missing_required_tools": (
            slot_missing_tools
            if (slot_missing_tools := missing_required_tools_from_tool_slots(evidence_coverage)) is not None
            else _string_list(
                existing_authority.get("missing_required_tools") or evidence_coverage.get("missing_required_tools")
            )
        ),
        "unexpected_tool_pruning": _list_value(
            existing_authority.get("unexpected_tool_pruning") or evidence_coverage.get("unexpected_tool_pruning")
        ),
        "tool_schema_registry_coverage": dict(
            _as_mapping(
                existing_authority.get("tool_schema_registry_coverage")
                or evidence_coverage.get("tool_schema_registry_coverage")
            )
        ),
        "workflow_chain": dict(
            _as_mapping(existing_authority.get("workflow_chain") or evidence_coverage.get("workflow_chain"))
        ),
        "coverage_ratio": existing_authority.get("coverage_ratio", evidence_coverage.get("coverage_ratio")),
        "pass": _bool_or_none(pass_source),
    }
    authority["final_request_evidence_authority_hash"] = _stable_hash(
        {key: value for key, value in authority.items() if key != "final_request_evidence_authority_hash"}
    )
    return authority


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
    context_snapshot_ref = normalize_context_snapshot_ref(
        _first_text(
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
    missing_required_refs = missing_required_refs_from_evidence_slots(evidence_coverage) or _string_list(
        evidence_coverage.get("missing_required_refs") or existing_evidence.get("missing_required_refs")
    )
    slot_missing_tools = missing_required_tools_from_tool_slots(evidence_coverage)
    missing_required_tools = (
        slot_missing_tools
        if slot_missing_tools is not None
        else _string_list(evidence_coverage.get("missing_required_tools") or existing_evidence.get("missing_required_tools"))
    )
    coverage_pass = (
        evidence_coverage.get("pass")
        if evidence_coverage
        else existing_evidence.get("final_request_evidence_coverage_pass")
    )
    evidence_authority = _build_final_request_evidence_authority(
        evidence_coverage=evidence_coverage,
        existing_evidence=existing_evidence,
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
        "role_id": evidence_authority.get("role_id", ""),
        "expected_role_id": evidence_authority.get("expected_role_id", ""),
        "role_identity_ok": evidence_authority.get("role_identity_ok"),
        "required_refs": evidence_authority.get("required_refs", []),
        "included_refs": evidence_authority.get("included_refs", []),
        "required_tools": evidence_authority.get("required_tools", []),
        "available_tools": evidence_authority.get("available_tools", []),
        "unexpected_tool_pruning": evidence_authority.get("unexpected_tool_pruning", []),
        "tool_schema_registry_coverage": evidence_authority.get("tool_schema_registry_coverage", {}),
        "workflow_chain": evidence_authority.get("workflow_chain", {}),
        "coverage_ratio": evidence_authority.get("coverage_ratio"),
        "final_request_evidence_authority_hash": evidence_authority.get("final_request_evidence_authority_hash", ""),
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
    if evidence_authority:
        payload["final_request_evidence_authority"] = evidence_authority

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
    authority_hash = _text(evidence.get("final_request_evidence_authority_hash"))
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
            "final_request_evidence_authority_hash": authority_hash,
            "request_hash": _text(evidence.get("request_hash")),
        }
    )
    payload["audit_refs"] = audit_refs
    return evidence
