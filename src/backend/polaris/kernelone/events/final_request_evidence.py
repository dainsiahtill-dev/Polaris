"""Final provider-request audit evidence projection helpers."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, MutableMapping
from typing import Any

FINAL_REQUEST_EVIDENCE_SCHEMA = "llm.final_request_evidence.v1"
FINAL_REQUEST_EVIDENCE_AUTHORITY_SCHEMA = "polaris.final_request_evidence_authority.v1"
AUDIT_REFS_SCHEMA = "llm.final_request_audit_refs.v1"
_CONTEXT_SNAPSHOT_HASH_RE = re.compile(r"(?<![0-9A-Fa-f])([0-9A-Fa-f]{24})(?![0-9A-Fa-f])")
_COVERAGE_FLAG_TO_REF = {
    "has_pm_contract": "pm_contract",
    "has_chief_engineer_blueprint": "ce_blueprint",
    "has_module_interface_contract": "module_interface_contract",
    "has_actual_sibling_exports": "actual_sibling_exports",
    "has_architecture_or_file_plan": "architecture_or_file_plan",
    "has_target_files": "target_files",
    "has_failure_feedback": "failed_gate_evidence",
    "has_workspace_quality_evidence": "workspace_quality_evidence",
    "has_resident_agi_decision_trace": "resident_agi_decision_trace",
    "has_resident_agi_capability_surface": "resident_agi_capability_surface",
    "has_resident_agi_decision_boundary": "resident_agi_decision_boundary",
}
_METADATA_SUMMARY_FLAG_TO_REF: tuple[tuple[str, str], ...] = (
    ("has_execution_profile", "execution_profile"),
    ("has_execution_strategy", "execution_strategy"),
    ("has_execution_contract", "execution_contract"),
    ("has_execution_envelope", "execution_envelope"),
    ("has_delivery_plan_document", "delivery_plan_document"),
    ("has_delivery_depth_contract", "delivery_depth_contract"),
    ("has_pm_contract", "pm_contract"),
    ("has_chief_engineer_blueprint", "ce_blueprint"),
    ("has_target_scope", "target_files"),
    ("has_language_guidance", "language_guidance"),
    ("has_output_contract", "output_contract"),
    ("has_task_metadata", "task_metadata"),
    ("has_module_interface_contract", "module_interface_contract"),
    ("has_actual_sibling_exports", "actual_sibling_exports"),
    ("has_interface_discrepancy_context", "interface_discrepancy_context"),
    ("has_architecture_or_file_plan", "architecture_or_file_plan"),
    ("has_failed_gate_evidence", "failed_gate_evidence"),
    ("has_workspace_quality_evidence", "workspace_quality_evidence"),
)
_STRUCTURED_EVIDENCE_FLAG_TO_KEY: tuple[tuple[str, str], ...] = (
    ("has_pm_contract", "pm_contract"),
    ("has_chief_engineer_blueprint", "ce_blueprint"),
    ("has_execution_envelope", "execution_envelope"),
    ("has_module_interface_contract", "module_interface_contract"),
    ("has_actual_sibling_exports", "actual_sibling_exports"),
    ("has_interface_discrepancy_context", "interface_discrepancy_context"),
    ("has_architecture_or_file_plan", "architecture_or_file_plan"),
    ("has_failed_gate_evidence", "failed_gate_evidence"),
    ("has_failed_gate_evidence", "failure_evidence"),
    ("has_workspace_quality_evidence", "workspace_quality_evidence"),
    ("has_workspace_quality_evidence", "quality_evidence"),
    ("has_target_scope", "target_files"),
)
_COVERAGE_SOURCE_HASH_KEYS: dict[str, tuple[str, str]] = {
    "pm_contract": ("pm_contract_hash", "pm_contract_hash"),
    "ce_blueprint": ("chief_engineer_blueprint_hash", "ce_blueprint_hash"),
    "handoff_decision": ("", "handoff_decision_hash"),
    "module_interface_contract": ("module_interface_contract_hash", ""),
    "actual_sibling_exports": ("actual_sibling_exports_hash", ""),
    "interface_discrepancy_context": ("interface_discrepancy_context_hash", ""),
    "architecture_or_file_plan": ("architecture_or_file_plan_hash", ""),
    "failed_gate_evidence": ("failed_gate_evidence_hash", ""),
    "workspace_quality_evidence": ("workspace_quality_evidence_hash", ""),
    "target_files": ("target_scope_hash", ""),
    "execution_profile": ("", "execution_profile_hash"),
    "execution_envelope": ("", "execution_envelope_hash"),
    "execution_contract": ("execution_contract_hash", ""),
    "task_metadata": ("task_metadata_hash", ""),
}
_COVERAGE_SOURCE_STRUCTURED_REFS = frozenset(
    {
        "execution_profile",
        "execution_strategy",
        "execution_contract",
        "execution_envelope",
        "task_metadata",
        "language_guidance",
        "output_contract",
    }
)
_COVERAGE_SOURCE_METADATA_FLAGS = {
    "pm_contract": "has_pm_contract",
    "ce_blueprint": "has_chief_engineer_blueprint",
    "module_interface_contract": "has_module_interface_contract",
    "actual_sibling_exports": "has_actual_sibling_exports",
    "interface_discrepancy_context": "has_interface_discrepancy_context",
    "architecture_or_file_plan": "has_architecture_or_file_plan",
    "failed_gate_evidence": "has_failed_gate_evidence",
    "workspace_quality_evidence": "has_workspace_quality_evidence",
    "target_files": "has_target_scope",
}
_COVERAGE_SOURCE_DETAIL_KEYS = {
    "module_interface_contract": "module_interface_contract_summary",
    "actual_sibling_exports": "actual_sibling_exports_summary",
    "interface_discrepancy_context": "interface_discrepancy_context_summary",
    "architecture_or_file_plan": "architecture_or_file_plan_summary",
    "failed_gate_evidence": "failed_gate_evidence_summary",
    "workspace_quality_evidence": "workspace_quality_evidence_summary",
    "target_files": "target_scope_summary",
}
_EVIDENCE_REQUIREMENT_TO_REF = {
    "pm_task_contract": "pm_contract",
    "pm_contract": "pm_contract",
    "pm_delivery_plan_document": "delivery_plan_document",
    "delivery_plan_document": "delivery_plan_document",
    "delivery_plan": "delivery_plan_document",
    "design_intent": "delivery_plan_document",
    "pm_delivery_depth_contract": "delivery_depth_contract",
    "delivery_depth_contract": "delivery_depth_contract",
    "behavior_contract": "delivery_depth_contract",
    "behavior_matrix": "delivery_depth_contract",
    "chief_engineer_blueprint": "ce_blueprint",
    "ce_blueprint": "ce_blueprint",
    "module_interface_contract": "module_interface_contract",
    "cross_file_interface_contract": "module_interface_contract",
    "cross_artifact_interface_contract": "module_interface_contract",
    "cross_artifact.interface_contract.v1": "module_interface_contract",
    "public_symbols": "module_interface_contract",
    "consumes_symbols": "module_interface_contract",
    "actual_sibling_exports": "actual_sibling_exports",
    "actual_export_summary": "actual_sibling_exports",
    "actual_public_symbols": "actual_sibling_exports",
    "existing_target_files": "actual_sibling_exports",
    "interface_discrepancy_context": "interface_discrepancy_context",
    "interface_discrepancy_evidence": "interface_discrepancy_context",
    "interface_discrepancy_receipt": "interface_discrepancy_context",
    "interface_discrepancy_receipts": "interface_discrepancy_context",
    "interface_delta": "interface_discrepancy_context",
    "interface_delta_receipt": "interface_discrepancy_context",
    "interface_discrepancy_triage": "interface_discrepancy_context",
    "task_boundary_interface_discrepancy": "interface_discrepancy_context",
    "task_boundary_interface_discrepancy_retry": "interface_discrepancy_context",
    "director_interface_discrepancy_retry": "interface_discrepancy_context",
    "pending_design_interface_contract": "interface_discrepancy_context",
    "director_retry_with_interface_discrepancy_context": "interface_discrepancy_context",
    "target_files_or_declared_scopes": "target_files",
    "target_files": "target_files",
    "declared_scopes": "target_files",
    "language_best_practices": "language_guidance",
    "execution_profile": "execution_profile",
    "execution_strategy": "execution_strategy",
    "execution_envelope": "execution_envelope",
    "final_provider_request": "final_provider_request",
    "final_provider_request_audit": "final_provider_request",
    "run_ledger": "run_ledger",
    "workspace_quality_evidence": "workspace_quality_evidence",
    "quality_evidence": "workspace_quality_evidence",
    "quality_gate_verdict": "workspace_quality_evidence",
    "failed_gate_evidence": "failed_gate_evidence",
    "failure_evidence": "failed_gate_evidence",
    "failed_gate_or_verification_evidence": "failed_gate_evidence",
    "verification_evidence": "failed_gate_evidence",
    "verification_failure_evidence": "failed_gate_evidence",
    "verifier_failure_evidence": "failed_gate_evidence",
    "architecture_or_file_plan": "architecture_or_file_plan",
    "architecture_plan": "architecture_or_file_plan",
    "file_plan": "architecture_or_file_plan",
    "construction_plan": "architecture_or_file_plan",
    "scope_for_apply": "architecture_or_file_plan",
}


def final_request_evidence_ref_for_requirement(value: Any) -> str:
    """Return the canonical evidence ref for a requirement or slot alias."""

    token = _text(value)
    return _EVIDENCE_REQUIREMENT_TO_REF.get(token.lower(), token)


def final_request_evidence_ref_for_coverage_flag(value: Any) -> str:
    """Return the canonical evidence ref represented by a coverage flag."""

    return _COVERAGE_FLAG_TO_REF.get(_text(value), "")


def final_request_evidence_refs_for_coverage_flags(
    coverage: Mapping[str, Any],
    *,
    require_present: bool = False,
    excluded_flags: Iterable[Any] = (),
) -> list[str]:
    """Project coverage flags to canonical evidence refs.

    `require_present` is used for included evidence. Required-ref fallback can
    intentionally project the configured coverage surface regardless of value.
    """

    excluded = {_text(flag) for flag in excluded_flags if _text(flag)}
    refs: list[str] = []
    for flag, present in coverage.items():
        normalized_flag = _text(flag)
        if not normalized_flag or normalized_flag in excluded:
            continue
        if require_present and not bool(present):
            continue
        ref = final_request_evidence_ref_for_coverage_flag(normalized_flag)
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def final_request_evidence_refs_for_metadata_summary(summary: Mapping[str, Any]) -> list[str]:
    """Project request metadata summary flags to canonical evidence refs."""

    refs: list[str] = []
    for flag, ref in _METADATA_SUMMARY_FLAG_TO_REF:
        if summary.get(flag) and ref not in refs:
            refs.append(ref)
    return refs


def final_request_structured_evidence_from_metadata_summary(summary: Mapping[str, Any]) -> dict[str, bool]:
    """Project request metadata summary flags to structured evidence booleans."""

    return {key: bool(summary.get(flag)) for flag, key in _STRUCTURED_EVIDENCE_FLAG_TO_KEY}


def build_final_request_coverage_sources(
    *,
    refs: Iterable[Any],
    included_refs: Iterable[Any],
    workflow_chain: Mapping[str, Any],
    request_metadata_summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Build structured coverage-source records for final-request evidence slots.

    Boundary:
        KernelOne owns how final-request evidence refs map to provenance hashes,
        detail summaries, and confidence labels. Callers provide the refs that
        are required or present for the current request; they should not
        duplicate this ref-to-source projection locally.
    """

    included = {final_request_evidence_ref_for_requirement(ref) for ref in included_refs}
    sources: list[dict[str, Any]] = []
    for raw_ref in refs:
        ref_type = final_request_evidence_ref_for_requirement(raw_ref)
        if not ref_type:
            continue
        present = ref_type in included
        source: dict[str, Any] = {
            "ref_type": ref_type,
            "present": present,
            "source": "final_provider_request",
            "confidence": _coverage_source_confidence(
                ref_type=ref_type,
                present=present,
                request_metadata_summary=request_metadata_summary,
            ),
            "freshness": "current_turn" if present else "unknown",
        }
        hash_value = _coverage_source_hash(
            ref_type=ref_type,
            workflow_chain=workflow_chain,
            request_metadata_summary=request_metadata_summary,
        )
        if hash_value:
            source["hash"] = hash_value
        details = _coverage_source_details(ref_type=ref_type, request_metadata_summary=request_metadata_summary)
        if details:
            source["details"] = details
        sources.append(source)
    return sources


def _coverage_source_hash(
    *,
    ref_type: str,
    workflow_chain: Mapping[str, Any],
    request_metadata_summary: Mapping[str, Any],
) -> str:
    summary_key, workflow_key = _COVERAGE_SOURCE_HASH_KEYS.get(ref_type, ("", ""))
    return _first_text(
        request_metadata_summary.get(summary_key) if summary_key else "",
        workflow_chain.get(workflow_key) if workflow_key else "",
    )


def _coverage_source_confidence(
    *,
    ref_type: str,
    present: bool,
    request_metadata_summary: Mapping[str, Any],
) -> str:
    structured_flag = _COVERAGE_SOURCE_METADATA_FLAGS.get(ref_type)
    if (structured_flag and request_metadata_summary.get(structured_flag)) or (
        present and ref_type in _COVERAGE_SOURCE_STRUCTURED_REFS
    ):
        return "structured_metadata"
    if present:
        return "text_heuristic"
    return "absent"


def _coverage_source_details(
    *,
    ref_type: str,
    request_metadata_summary: Mapping[str, Any],
) -> dict[str, Any]:
    detail_key = _COVERAGE_SOURCE_DETAIL_KEYS.get(ref_type, "")
    details = request_metadata_summary.get(detail_key) if detail_key else None
    return dict(details) if isinstance(details, Mapping) and details else {}


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


def _string_sequence(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [token for item in value if (token := _text(item))]


def _has_non_empty_text_sequence(value: Any) -> bool:
    return bool(_string_sequence(value))


def _has_non_empty_mapping(value: Any) -> bool:
    return isinstance(value, Mapping) and bool(value)


def _has_structural_field(payload: Mapping[str, Any], keys: tuple[str, ...]) -> bool:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and _text(value):
            return True
        if isinstance(value, (list, tuple, set)) and _string_sequence(value):
            return True
        if _has_non_empty_mapping(value):
            return True
    return False


def _unique_texts(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values if isinstance(values, (list, tuple, set)) else []:
        token = _text(value)
        if token and token not in seen:
            seen.add(token)
            result.append(token)
    return result


def looks_like_pm_contract_payload(value: Any) -> bool:
    """Return whether *value* is structured PM task-contract evidence."""

    if not isinstance(value, Mapping):
        return False
    for key in ("pm_contract", "pm_task_contract", "task_contract"):
        if looks_like_pm_contract_payload(value.get(key)):
            return True
    schema_version = _text(value.get("schema_version")).lower()
    has_contract_schema = (
        "pm." in schema_version
        or "pm_" in schema_version
        or "task_contract" in schema_version
        or "task.contract" in schema_version
    )
    has_task_identity = bool(_first_text(value.get("task_id"), value.get("id")))
    has_structured_contract = _has_structural_field(
        value,
        (
            "target_files",
            "targets",
            "target_paths",
            "scope_paths",
            "scope",
            "steps",
            "acceptance",
            "acceptance_criteria",
            "depends_on",
            "dependencies",
            "deterministic_checks",
            "execution_checklist",
            "delivery_plan_document",
            "delivery_depth_contract",
            "behavior_contract",
            "acceptance_contract",
        ),
    )
    if has_contract_schema:
        return has_structured_contract
    return has_task_identity and has_structured_contract


def looks_like_ce_blueprint_payload(value: Any) -> bool:
    """Return whether *value* is structured Chief Engineer blueprint evidence."""

    if not isinstance(value, Mapping):
        return False
    for key in ("ce_blueprint", "chief_engineer_blueprint", "blueprint_payload"):
        if looks_like_ce_blueprint_payload(value.get(key)):
            return True
    schema_version = _text(value.get("schema_version")).lower()
    has_blueprint_schema = (
        "chief_engineer" in schema_version
        or "ce_blueprint" in schema_version
        or "blueprint" in schema_version
    )
    has_blueprint_identity = bool(_first_text(value.get("blueprint_id"), value.get("id"), value.get("task_id")))
    has_structured_blueprint = _has_structural_field(
        value,
        (
            "module_interface_contract",
            "cross_file_interface_contract",
            "public_symbols",
            "consumes_symbols",
            "construction_plan",
            "execution_checklist",
            "architecture_decisions",
            "generated_blueprints",
            "target_files",
            "scope_for_apply",
            "scope_paths",
            "acceptance",
            "acceptance_criteria",
            "verification_steps",
            "handoff_evidence",
        ),
    )
    if has_blueprint_schema:
        return has_structured_blueprint
    return has_blueprint_identity and has_structured_blueprint


def looks_like_target_scope_payload(value: Any) -> bool:
    """Return whether *value* is structured target/scope evidence."""

    return bool(target_scope_evidence_entry("", value))


def target_scope_evidence_entry(source: str, payload: Any) -> dict[str, Any]:
    """Return one normalized target-scope evidence source entry."""

    if not isinstance(payload, Mapping):
        return {}
    authorization = payload.get("authorization")
    if isinstance(authorization, Mapping):
        authorized = target_scope_evidence_entry(source, authorization)
        if authorized:
            return authorized
    target_files = _string_sequence(payload.get("target_files") or payload.get("targets") or payload.get("target_paths"))
    scope_paths = _string_sequence(payload.get("scope_paths") or payload.get("declared_scopes") or payload.get("scope"))
    allowed_write_paths = _string_sequence(
        payload.get("allowed_write_paths") or payload.get("allowed_paths") or payload.get("write_scope")
    )
    allowed_read_paths = _string_sequence(payload.get("allowed_read_paths") or payload.get("read_scope"))
    if not target_files and not scope_paths and not allowed_write_paths and not allowed_read_paths:
        return {}
    return {
        "source": _text(source),
        "target_files": target_files,
        "scope_paths": scope_paths,
        "allowed_write_paths": allowed_write_paths,
        "allowed_read_paths": allowed_read_paths,
    }


def summarize_target_scope_evidence_payload(value: Any) -> dict[str, Any]:
    """Project target-scope evidence into the final-request summary shape."""

    found = _as_mapping(value)
    if not found:
        return {}
    raw_sources = found.get("sources")
    if isinstance(raw_sources, list):
        entries = [
            entry
            for item in raw_sources
            if isinstance(item, Mapping)
            and (entry := target_scope_evidence_entry(_text(item.get("source")), item))
        ]
    else:
        entry = target_scope_evidence_entry(_text(found.get("source") or "target_scope"), found)
        entries = [entry] if entry else []
    target_files: list[str] = []
    scope_paths: list[str] = []
    allowed_write_paths: list[str] = []
    allowed_read_paths: list[str] = []
    source_summaries: list[dict[str, Any]] = []
    for entry in entries:
        item_target_files = _string_sequence(entry.get("target_files"))
        item_scope_paths = _string_sequence(entry.get("scope_paths"))
        item_allowed_write_paths = _string_sequence(entry.get("allowed_write_paths"))
        item_allowed_read_paths = _string_sequence(entry.get("allowed_read_paths"))
        target_files.extend(item_target_files)
        scope_paths.extend(item_scope_paths)
        allowed_write_paths.extend(item_allowed_write_paths)
        allowed_read_paths.extend(item_allowed_read_paths)
        source_summaries.append(
            {
                "source": _text(entry.get("source")),
                "target_file_count": len(item_target_files),
                "scope_path_count": len(item_scope_paths),
                "allowed_write_path_count": len(item_allowed_write_paths),
                "allowed_read_path_count": len(item_allowed_read_paths),
            }
        )
    return {
        "schema_version": "polaris.target_scope.evidence.context_slot.v1",
        "source_schema_version": _text(found.get("schema_version")),
        "source_count": len(source_summaries),
        "target_file_count": len(_unique_texts(target_files)),
        "scope_path_count": len(_unique_texts(scope_paths)),
        "allowed_write_path_count": len(_unique_texts(allowed_write_paths)),
        "allowed_read_path_count": len(_unique_texts(allowed_read_paths)),
        "sources": source_summaries,
        "payload_hash": _stable_hash(value),
    }

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


def missing_required_refs_from_evidence_coverage(
    evidence_coverage: Mapping[str, Any],
    existing_evidence: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return missing required evidence refs from coverage slots or legacy fields.

    Boundary:
        This owns the precedence for final-request missing-ref projection:
        structured evidence slots are authoritative when present; otherwise the
        legacy ``missing_required_refs`` field is used for compatibility.
    """

    slot_refs = missing_required_refs_from_evidence_slots(evidence_coverage)
    if slot_refs:
        return slot_refs
    existing = _as_mapping(existing_evidence)
    return _string_list(evidence_coverage.get("missing_required_refs") or existing.get("missing_required_refs"))


def missing_required_tools_from_evidence_coverage(
    evidence_coverage: Mapping[str, Any],
    existing_evidence: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return missing required tools from tool slots or legacy fields.

    Boundary:
        Structured tool slots carry the current authority. A present slot list
        may intentionally produce an empty result; only when slots are absent do
        we fall back to legacy ``missing_required_tools`` fields.
    """

    slot_tools = missing_required_tools_from_tool_slots(evidence_coverage)
    if slot_tools is not None:
        return slot_tools
    existing = _as_mapping(existing_evidence)
    return _string_list(evidence_coverage.get("missing_required_tools") or existing.get("missing_required_tools"))


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


def _looks_like_command_failure_record(value: Mapping[str, Any]) -> bool:
    if "exit_code" not in value:
        return False
    return any(key in value for key in ("command", "stderr", "stdout", "script", "tool"))


def _looks_like_failed_gate_item(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    schema_version = _text(value.get("schema_version")).lower()
    if (
        "failed_gate" in schema_version
        or "verification_failure" in schema_version
        or "failure_evidence" in schema_version
    ):
        return True
    if _looks_like_command_failure_record(value):
        return True
    return any(
        key in value
        for key in (
            "failure_class",
            "responsible_layer",
            "repairable_by_director",
            "requires_ce_replan",
            "requires_pm_revision",
            "evidence_refs",
            "failed_required_modalities",
            "failed_checks",
            "verifier_results",
            "quality_errors",
            "diagnostics",
        )
    )


def looks_like_failed_gate_evidence_context_payload(value: Any) -> bool:
    """Return whether *value* is structured failed-gate evidence.

    Boundary:
        This predicate recognizes already-shaped failure evidence for
        final-request context-slot discovery. It checks schema and structural
        keys only; it does not parse diagnostic prose.

    Complexity:
        O(k) time over a fixed key set; O(1) memory.
    """

    if not isinstance(value, Mapping):
        return False
    schema_version = _text(value.get("schema_version")).lower()
    if (
        "failed_gate" in schema_version
        or "verification_failure" in schema_version
        or "failure_evidence" in schema_version
    ):
        return True
    items = value.get("items")
    if isinstance(items, (list, tuple)) and items:
        return any(_looks_like_failed_gate_item(item) for item in items)
    return _looks_like_failed_gate_item(value)


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
        "missing_required_refs": missing_required_refs_from_evidence_coverage(
            evidence_coverage,
            existing_authority,
        ),
        "required_tools": _string_list(
            existing_authority.get("required_tools") or evidence_coverage.get("required_tools")
        ),
        "available_tools": _string_list(
            existing_authority.get("available_tools") or evidence_coverage.get("available_tools")
        ),
        "missing_required_tools": missing_required_tools_from_evidence_coverage(
            evidence_coverage,
            existing_authority,
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
    missing_required_refs = missing_required_refs_from_evidence_coverage(evidence_coverage, existing_evidence)
    missing_required_tools = missing_required_tools_from_evidence_coverage(evidence_coverage, existing_evidence)
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
