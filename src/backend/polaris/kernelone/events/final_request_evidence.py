"""Final provider-request audit evidence projection helpers."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import asdict, dataclass
from typing import Any

FINAL_REQUEST_EVIDENCE_SCHEMA = "llm.final_request_evidence.v1"
FINAL_REQUEST_EVIDENCE_AUTHORITY_SCHEMA = "polaris.final_request_evidence_authority.v1"
AUDIT_REFS_SCHEMA = "llm.final_request_audit_refs.v1"
ROLE_FINAL_REQUEST_POLICY_PROMPT_SCHEMA = "polaris.role_final_request_evidence_prompt.v1"
ROLE_FINAL_REQUEST_EVIDENCE_PROMPT_SLOT_SCHEMA = "polaris.role_final_request_evidence_slot_prompt.v1"
FINAL_REQUEST_EVIDENCE_PROMPT_ANCHOR_SCHEMA = "polaris.final_request_evidence_anchor_prompt.v1"
_ROLE_FINAL_REQUEST_POLICY_PROMPT_FIELDS = frozenset({"schema_version", "role", "slots"})
_ROLE_FINAL_REQUEST_PROMPT_SLOT_FIELDS = frozenset(
    {
        "schema_version",
        "ref_kind",
        "state",
        "canonical_source_ref",
        "source_fact_schema",
        "source_fact_version",
        "items",
    }
)
_ROLE_FINAL_REQUEST_PROMPT_ANCHOR_FIELDS = frozenset(
    {
        "schema_version",
        "ref_kind",
        "canonical_source_ref",
        "canonical_ref",
        "canonical_hash",
        "source_fact_schema",
        "source_fact_version",
    }
)
_CONTEXT_SNAPSHOT_HASH_RE = re.compile(r"(?<![0-9A-Fa-f])([0-9A-Fa-f]{24})(?![0-9A-Fa-f])")
_EXACT_HASH_64_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTEXT_SNAPSHOT_AUDIT_PIN_FIELDS = frozenset(
    {
        "schema_version",
        "workspace_abs",
        "runtime_root",
        "snapshot_logical_path",
        "snapshot_absolute_path",
        "snapshot_source",
        "factory_run_id",
        "role",
        "verification_scope",
        "request_freeze_id",
        "provider_request_id",
        "context_snapshot_ref",
        "storage_identity_token",
        "snapshot_content_hash",
        "composite_request_hash",
        "retention",
        "pin_hash",
    }
)


def _validate_exact_context_snapshot_hash(value: str) -> str:
    """Lazily consume the canonical validator without creating an import cycle.

    ``context_store_retention`` owns the audit-pin repository and imports this
    event contract. Importing the engine package while this module is still
    initializing makes the canonical Run Ledger public import order-dependent.
    The validator is a pure leaf, so deferring its import until validation keeps
    one hash authority while preserving a cold-import-safe module graph.
    """

    from polaris.kernelone.llm.engine.internal.context_hash import validate_context_hash

    return validate_context_hash(value)


_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "cookie",
        "credential",
        "credentials",
        "password",
        "proxy_authorization",
        "refresh_token",
        "secret",
        "set_cookie",
        "token",
        "x_api_key",
    }
)
_SECRET_KEY_SUFFIXES = ("_access_token", "_api_key", "_credential", "_password", "_secret", "_token")
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
_INCLUDED_EVIDENCE_COVERAGE_EXCLUDED_FLAGS = frozenset(
    {
        "has_pm_contract",
        "has_chief_engineer_blueprint",
        "has_target_files",
    }
)
_EVIDENCE_REQUIREMENT_TO_REF = {
    "pm_raw_intent": "pm_raw_intent",
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


def final_request_included_evidence_refs(
    *,
    coverage: Mapping[str, Any],
    request_metadata_summary: Mapping[str, Any],
    receipt_refs: Iterable[Any] = (),
) -> list[str]:
    """Return canonical evidence refs present in the final provider request.

    Boundary:
        Included evidence is a KernelOne final-request projection. Role callers
        may provide coverage flags, structured metadata summary, and receipt
        references, but should not locally duplicate how those inputs become
        canonical included refs.
    """

    refs = ["final_provider_request"]
    refs.extend(
        final_request_evidence_refs_for_coverage_flags(
            coverage,
            require_present=True,
            excluded_flags=_INCLUDED_EVIDENCE_COVERAGE_EXCLUDED_FLAGS,
        )
    )
    refs.extend(final_request_evidence_refs_for_metadata_summary(request_metadata_summary))
    if list(receipt_refs):
        refs.append("receipt_store_refs")
    return _unique_texts(refs)


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
        "chief_engineer" in schema_version or "ce_blueprint" in schema_version or "blueprint" in schema_version
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
    if not has_blueprint_schema and (
        looks_like_pm_contract_payload(value)
        or looks_like_failed_gate_evidence_context_payload(value)
        or looks_like_workspace_quality_evidence_payload(value)
    ):
        return False
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
    target_files = _string_sequence(
        payload.get("target_files") or payload.get("targets") or payload.get("target_paths")
    )
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
            if isinstance(item, Mapping) and (entry := target_scope_evidence_entry(_text(item.get("source")), item))
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


def structured_context_coverage_flags(context: Any) -> dict[str, bool]:
    """Return final-request coverage flags from structured context payloads.

    Boundary:
        This helper is the KernelOne owner for low-level final-request context
        coverage discovery. It recursively scans structured context containers
        and applies the same evidence-slot predicates used by final-request
        audit. It deliberately does not parse prompt or diagnostic prose.

    Complexity:
        O(n) time over nested context mappings up to a fixed depth; O(n) memory
        for traversal results.
    """

    flags = {
        "has_pm_contract": False,
        "has_chief_engineer_blueprint": False,
        "has_target_files": False,
        "has_failure_feedback": False,
        "has_workspace_quality_evidence": False,
    }
    for payload in _iter_context_mappings(context):
        if looks_like_pm_contract_payload(payload):
            flags["has_pm_contract"] = True
        if looks_like_ce_blueprint_payload(payload):
            flags["has_chief_engineer_blueprint"] = True
        if looks_like_target_scope_payload(payload):
            flags["has_target_files"] = True
        if looks_like_failed_gate_evidence_context_payload(payload):
            flags["has_failure_feedback"] = True
        if looks_like_workspace_quality_evidence_payload(payload):
            flags["has_workspace_quality_evidence"] = True
    return flags


def _iter_context_mappings(value: Any, *, depth: int = 0) -> list[Mapping[str, Any]]:
    if depth > 5:
        return []
    if isinstance(value, Mapping):
        mappings: list[Mapping[str, Any]] = [value]
        for nested in value.values():
            mappings.extend(_iter_context_mappings(nested, depth=depth + 1))
        return mappings
    if isinstance(value, (list, tuple)):
        nested_mappings: list[Mapping[str, Any]] = []
        for item in value:
            nested_mappings.extend(_iter_context_mappings(item, depth=depth + 1))
        return nested_mappings
    return []


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


def canonical_final_request_hash(value: Any) -> str:
    """Hash one detached JSON-safe evidence value deterministically."""

    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("final request evidence must be JSON safe") from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


ROLE_FINAL_REQUEST_POLICY_SCHEMA = "polaris.role_final_request_policy.v1"
ROLE_FINAL_REQUEST_POLICY_FACTS_SCHEMA = "polaris.role_final_request_policy_facts.v1"
FINAL_REQUEST_EVIDENCE_ANCHOR_SCHEMA = "polaris.final_request_evidence_anchor.v1"
FINAL_REQUEST_EVIDENCE_SLOT_SCHEMA = "polaris.final_request_evidence_slot.v1"
ROLE_FINAL_REQUEST_EVIDENCE_SLOT_SCHEMA = "polaris.role_final_request_evidence_slot.v1"
_ROLE_FINAL_REQUEST_STATES = frozenset({"present", "absent_at_request_time"})
_ROLE_FINAL_REQUEST_ANCHOR_FIELDS = frozenset(
    {
        "schema_version",
        "ref_kind",
        "canonical_source_ref",
        "canonical_ref",
        "canonical_hash",
        "source_fact_schema",
        "source_fact_version",
        "factory_run_id",
        "run_id",
        "role",
        "request_freeze_id",
        "cutoff_fact_id",
        "cutoff_fact_sequence",
        "cutoff_fact_hash",
        "source_fact_id",
        "source_fact_sequence",
        "source_fact_hash",
        "source_head_sequence",
        "source_head_hash",
        "execution_authority_hash",
    }
)
_ROLE_FINAL_REQUEST_SLOT_FIELDS = frozenset(
    {
        "schema_version",
        "ref_kind",
        "state",
        "canonical_source_ref",
        "source_fact_schema",
        "source_fact_version",
        "factory_run_id",
        "run_id",
        "role",
        "request_freeze_id",
        "cutoff_fact_id",
        "cutoff_fact_sequence",
        "cutoff_fact_hash",
        "source_head_sequence",
        "source_head_hash",
        "execution_authority_hash",
        "items",
    }
)
_ROLE_FINAL_REQUEST_POLICY_FACTS_FIELDS = frozenset({"schema_version", "role", "slots"})
_ROLE_FINAL_REQUEST_ANCHOR_STRING_FIELDS = (
    "schema_version",
    "ref_kind",
    "canonical_source_ref",
    "canonical_ref",
    "canonical_hash",
    "source_fact_schema",
    "source_fact_version",
    "factory_run_id",
    "run_id",
    "role",
    "request_freeze_id",
    "cutoff_fact_id",
    "cutoff_fact_hash",
    "source_fact_id",
    "source_fact_hash",
    "source_head_hash",
    "execution_authority_hash",
)
_ROLE_FINAL_REQUEST_SLOT_STRING_FIELDS = (
    "schema_version",
    "ref_kind",
    "state",
    "canonical_source_ref",
    "source_fact_schema",
    "source_fact_version",
    "factory_run_id",
    "run_id",
    "role",
    "request_freeze_id",
    "cutoff_fact_id",
    "cutoff_fact_hash",
    "source_head_hash",
    "execution_authority_hash",
)
_ROLE_FINAL_REQUEST_POLICY_FACTS_STRING_FIELDS = ("schema_version", "role")


def _require_role_final_request_string(field_name: str, value: Any) -> str:
    """Return an authority string without coercing runtime values."""

    if not isinstance(value, str):
        raise ValueError(f"{field_name}_must_be_string")
    return value


def _validate_role_final_request_json(value: Any, *, path: str = "$") -> None:
    """Reject non-JSON and unstable values without repr/default fallbacks."""

    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"canonical_json_non_finite_float:{path}")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"canonical_json_non_string_key:{path}")
            _validate_role_final_request_json(item, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_role_final_request_json(item, path=f"{path}[{index}]")
        return
    raise ValueError(f"canonical_json_unsupported_type:{path}:{type(value).__name__}")


def canonical_role_final_request_json(value: Any) -> str:
    """Return strict UTF-8 canonical JSON for provider-visible role facts."""

    _validate_role_final_request_json(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_role_final_request_hash(value: Any) -> str:
    """Return SHA-256 of strict role-fact canonical JSON."""

    return hashlib.sha256(canonical_role_final_request_json(value).encode("utf-8")).hexdigest()


_ROLE_FINAL_REQUEST_POLICY_FIELDS = frozenset(
    {"schema_version", "role", "slot_order", "required_present_slots", "policy_hash"}
)
_ROLE_FINAL_REQUEST_POLICY_SPECS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "pm": (("pm_raw_intent",), ("pm_raw_intent",)),
    "architect": (("pm_raw_intent",), ("pm_raw_intent",)),
    "chief_engineer": (
        ("pm_contract", "target_files", "workspace_quality"),
        ("pm_contract", "target_files"),
    ),
    "director": (
        ("pm_contract", "ce_blueprint", "target_files", "failure_feedback", "workspace_quality"),
        ("pm_contract", "ce_blueprint", "target_files"),
    ),
    "qa": (
        (
            "pm_contract",
            "ce_blueprint",
            "target_files",
            "verifier_receipts",
            "failure_feedback",
            "workspace_quality",
        ),
        (
            "pm_contract",
            "ce_blueprint",
            "target_files",
            "verifier_receipts",
            "workspace_quality",
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class RoleFinalRequestPolicyV1:
    """Exact ordered evidence policy for one canonical role."""

    schema_version: str
    role: str
    slot_order: tuple[str, ...]
    required_present_slots: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_role_final_request_string("schema_version", self.schema_version)
        _require_role_final_request_string("role", self.role)
        if self.schema_version != ROLE_FINAL_REQUEST_POLICY_SCHEMA:
            raise ValueError("role_final_request_policy_schema_mismatch")
        expected = _ROLE_FINAL_REQUEST_POLICY_SPECS.get(self.role)
        if expected is None:
            raise ValueError(f"role_final_request_policy_unknown_role:{self.role or '<empty>'}")
        if (self.slot_order, self.required_present_slots) != expected:
            raise ValueError("role_final_request_policy_definition_mismatch")

    def _hash_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "role": self.role,
            "slot_order": list(self.slot_order),
            "required_present_slots": list(self.required_present_slots),
        }

    @property
    def policy_hash(self) -> str:
        return canonical_role_final_request_hash(self._hash_payload())

    def to_record(self) -> dict[str, Any]:
        return {**self._hash_payload(), "policy_hash": self.policy_hash}

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> RoleFinalRequestPolicyV1:
        if not isinstance(record, Mapping) or frozenset(record) != _ROLE_FINAL_REQUEST_POLICY_FIELDS:
            raise ValueError("role_final_request_policy_fields_mismatch")
        schema_version = _require_role_final_request_string("schema_version", record.get("schema_version"))
        role = _require_role_final_request_string("role", record.get("role"))
        policy_hash = _require_role_final_request_string("policy_hash", record.get("policy_hash"))
        raw_slots = record.get("slot_order")
        raw_required = record.get("required_present_slots")
        if not isinstance(raw_slots, (list, tuple)) or not isinstance(raw_required, (list, tuple)):
            raise ValueError("role_final_request_policy_definition_mismatch")
        if any(not isinstance(item, str) for item in (*raw_slots, *raw_required)):
            raise ValueError("role_final_request_policy_definition_mismatch")
        created = cls(
            schema_version=schema_version,
            role=role,
            slot_order=tuple(raw_slots),
            required_present_slots=tuple(raw_required),
        )
        if policy_hash != created.policy_hash:
            raise ValueError("role_final_request_policy_hash_mismatch")
        return created


_ROLE_FINAL_REQUEST_POLICIES: dict[str, RoleFinalRequestPolicyV1] = {
    role: RoleFinalRequestPolicyV1(
        schema_version=ROLE_FINAL_REQUEST_POLICY_SCHEMA,
        role=role,
        slot_order=spec[0],
        required_present_slots=spec[1],
    )
    for role, spec in _ROLE_FINAL_REQUEST_POLICY_SPECS.items()
}
_ROLE_FINAL_REQUEST_SOURCE_KEYS: dict[str, tuple[str, ...]] = {
    "pm_raw_intent": ("pm_raw_intent",),
    "pm_contract": ("pm_contract", "pm_task_contract", "pm_task_contracts"),
    "ce_blueprint": ("ce_blueprint", "chief_engineer_blueprint"),
    "target_files": ("target_files", "scope_paths"),
    "verifier_receipts": ("verifier_receipts",),
    "failure_feedback": ("failure_feedback", "failed_gate_evidence"),
    "workspace_quality": ("workspace_quality", "workspace_quality_evidence"),
}


def role_final_request_policy(role: str) -> RoleFinalRequestPolicyV1:
    """Return exact policy; unknown roles fail closed."""

    normalized = _require_role_final_request_string("role", role).strip()
    policy = _ROLE_FINAL_REQUEST_POLICIES.get(normalized)
    if policy is None:
        raise ValueError(f"role_final_request_policy_unknown_role:{normalized or '<empty>'}")
    return policy


def role_final_request_source_keys(ref_kind: str) -> tuple[str, ...]:
    """Return allowlisted structured source keys for one canonical slot."""

    normalized = _require_role_final_request_string("ref_kind", ref_kind).strip()
    keys = _ROLE_FINAL_REQUEST_SOURCE_KEYS.get(normalized)
    if keys is None:
        raise ValueError(f"role_final_request_source_unknown_slot:{normalized or '<empty>'}")
    return keys


@dataclass(frozen=True, slots=True)
class RoleFinalRequestEvidenceAnchorV1:
    """One immutable provider-visible item reference; never raw fact payload."""

    schema_version: str
    ref_kind: str
    canonical_source_ref: str
    canonical_ref: str
    canonical_hash: str
    source_fact_schema: str
    source_fact_version: str
    factory_run_id: str
    run_id: str
    role: str
    request_freeze_id: str
    cutoff_fact_id: str
    cutoff_fact_sequence: int
    cutoff_fact_hash: str
    source_fact_id: str
    source_fact_sequence: int
    source_fact_hash: str
    source_head_sequence: int
    source_head_hash: str
    execution_authority_hash: str

    def __post_init__(self) -> None:
        for field_name in _ROLE_FINAL_REQUEST_ANCHOR_STRING_FIELDS:
            _require_role_final_request_string(field_name, getattr(self, field_name))
        policy = role_final_request_policy(self.role)
        if self.ref_kind not in policy.slot_order:
            raise ValueError("role_final_request_anchor_ref_kind_mismatch")
        text_fields = (
            self.ref_kind,
            self.canonical_source_ref,
            self.canonical_ref,
            self.source_fact_schema,
            self.source_fact_version,
            self.factory_run_id,
            self.run_id,
            self.request_freeze_id,
            self.cutoff_fact_id,
            self.source_fact_id,
        )
        if any(not value.strip() for value in text_fields):
            raise ValueError("role_final_request_anchor_empty_binding")
        if isinstance(self.cutoff_fact_sequence, bool) or self.cutoff_fact_sequence <= 0:
            raise ValueError("cutoff_fact_sequence_must_be_positive")
        if isinstance(self.source_fact_sequence, bool) or not isinstance(self.source_fact_sequence, int):
            raise ValueError("source_fact_sequence_must_be_integer")
        if isinstance(self.source_head_sequence, bool) or not isinstance(self.source_head_sequence, int):
            raise ValueError("source_head_sequence_must_be_non_negative")
        if self.source_head_sequence < 0:
            raise ValueError("source_head_sequence_must_be_non_negative")
        if not _EXACT_HASH_64_RE.fullmatch(self.source_head_hash):
            raise ValueError("source_head_hash_must_be_64_lowercase_hex")
        if self.source_fact_sequence <= 0:
            raise ValueError("source_fact_sequence_must_be_positive")
        if self.source_fact_sequence > self.source_head_sequence:
            raise ValueError("source_fact_sequence_exceeds_head")
        for field_name, value in (
            ("canonical_hash", self.canonical_hash),
            ("cutoff_fact_hash", self.cutoff_fact_hash),
            ("source_fact_hash", self.source_fact_hash),
            ("source_head_hash", self.source_head_hash),
            ("execution_authority_hash", self.execution_authority_hash),
        ):
            if not _EXACT_HASH_64_RE.fullmatch(value):
                raise ValueError(f"{field_name}_must_be_64_lowercase_hex")
        if self.schema_version != FINAL_REQUEST_EVIDENCE_ANCHOR_SCHEMA:
            raise ValueError("role_final_request_anchor_schema_mismatch")

    @classmethod
    def create(
        cls,
        *,
        ref_kind: str,
        canonical_source_ref: str,
        canonical_ref: str,
        canonical_hash: str,
        source_fact_schema: str,
        source_fact_version: str,
        factory_run_id: str,
        run_id: str,
        role: str,
        request_freeze_id: str,
        cutoff_fact_id: str,
        cutoff_fact_sequence: int,
        cutoff_fact_hash: str,
        source_fact_id: str,
        source_fact_sequence: int,
        source_fact_hash: str,
        source_head_sequence: int,
        source_head_hash: str,
        execution_authority_hash: str,
    ) -> RoleFinalRequestEvidenceAnchorV1:
        return cls(
            schema_version=FINAL_REQUEST_EVIDENCE_ANCHOR_SCHEMA,
            ref_kind=_require_role_final_request_string("ref_kind", ref_kind).strip(),
            canonical_source_ref=_require_role_final_request_string(
                "canonical_source_ref", canonical_source_ref
            ).strip(),
            canonical_ref=_require_role_final_request_string("canonical_ref", canonical_ref).strip(),
            canonical_hash=_require_role_final_request_string("canonical_hash", canonical_hash).strip(),
            source_fact_schema=_require_role_final_request_string("source_fact_schema", source_fact_schema).strip(),
            source_fact_version=_require_role_final_request_string("source_fact_version", source_fact_version).strip(),
            factory_run_id=_require_role_final_request_string("factory_run_id", factory_run_id).strip(),
            run_id=_require_role_final_request_string("run_id", run_id).strip(),
            role=_require_role_final_request_string("role", role).strip(),
            request_freeze_id=_require_role_final_request_string("request_freeze_id", request_freeze_id).strip(),
            cutoff_fact_id=_require_role_final_request_string("cutoff_fact_id", cutoff_fact_id).strip(),
            cutoff_fact_sequence=cutoff_fact_sequence,
            cutoff_fact_hash=_require_role_final_request_string("cutoff_fact_hash", cutoff_fact_hash).strip(),
            source_fact_id=_require_role_final_request_string("source_fact_id", source_fact_id).strip(),
            source_fact_sequence=source_fact_sequence,
            source_fact_hash=_require_role_final_request_string("source_fact_hash", source_fact_hash).strip(),
            source_head_sequence=source_head_sequence,
            source_head_hash=_require_role_final_request_string("source_head_hash", source_head_hash).strip(),
            execution_authority_hash=_require_role_final_request_string(
                "execution_authority_hash", execution_authority_hash
            ).strip(),
        )

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> RoleFinalRequestEvidenceAnchorV1:
        if not isinstance(record, Mapping) or frozenset(record) != _ROLE_FINAL_REQUEST_ANCHOR_FIELDS:
            raise ValueError("role_final_request_anchor_fields_mismatch")
        string_fields = {
            field_name: _require_role_final_request_string(field_name, record.get(field_name))
            for field_name in _ROLE_FINAL_REQUEST_ANCHOR_STRING_FIELDS
        }
        cutoff_fact_sequence = record.get("cutoff_fact_sequence")
        if isinstance(cutoff_fact_sequence, bool) or not isinstance(cutoff_fact_sequence, int):
            raise ValueError("cutoff_fact_sequence_must_be_positive")
        source_fact_sequence = record.get("source_fact_sequence")
        if isinstance(source_fact_sequence, bool) or not isinstance(source_fact_sequence, int):
            raise ValueError("source_fact_sequence_must_be_integer")
        source_head_sequence = record.get("source_head_sequence")
        if isinstance(source_head_sequence, bool) or not isinstance(source_head_sequence, int):
            raise ValueError("source_head_sequence_must_be_non_negative")
        return cls(
            schema_version=string_fields["schema_version"],
            ref_kind=string_fields["ref_kind"],
            canonical_source_ref=string_fields["canonical_source_ref"],
            canonical_ref=string_fields["canonical_ref"],
            canonical_hash=string_fields["canonical_hash"],
            source_fact_schema=string_fields["source_fact_schema"],
            source_fact_version=string_fields["source_fact_version"],
            factory_run_id=string_fields["factory_run_id"],
            run_id=string_fields["run_id"],
            role=string_fields["role"],
            request_freeze_id=string_fields["request_freeze_id"],
            cutoff_fact_id=string_fields["cutoff_fact_id"],
            cutoff_fact_sequence=cutoff_fact_sequence,
            cutoff_fact_hash=string_fields["cutoff_fact_hash"],
            source_fact_id=string_fields["source_fact_id"],
            source_fact_sequence=source_fact_sequence,
            source_fact_hash=string_fields["source_fact_hash"],
            source_head_sequence=source_head_sequence,
            source_head_hash=string_fields["source_head_hash"],
            execution_authority_hash=string_fields["execution_authority_hash"],
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ref_kind": self.ref_kind,
            "canonical_source_ref": self.canonical_source_ref,
            "canonical_ref": self.canonical_ref,
            "canonical_hash": self.canonical_hash,
            "source_fact_schema": self.source_fact_schema,
            "source_fact_version": self.source_fact_version,
            "factory_run_id": self.factory_run_id,
            "run_id": self.run_id,
            "role": self.role,
            "request_freeze_id": self.request_freeze_id,
            "cutoff_fact_id": self.cutoff_fact_id,
            "cutoff_fact_sequence": self.cutoff_fact_sequence,
            "cutoff_fact_hash": self.cutoff_fact_hash,
            "source_fact_id": self.source_fact_id,
            "source_fact_sequence": self.source_fact_sequence,
            "source_fact_hash": self.source_fact_hash,
            "source_head_sequence": self.source_head_sequence,
            "source_head_hash": self.source_head_hash,
            "execution_authority_hash": self.execution_authority_hash,
        }


@dataclass(frozen=True, slots=True)
class RoleFinalRequestEvidenceSlotV1:
    """One policy slot at the Factory-issued source-head cut."""

    schema_version: str
    ref_kind: str
    state: str
    canonical_source_ref: str
    source_fact_schema: str
    source_fact_version: str
    factory_run_id: str
    run_id: str
    role: str
    request_freeze_id: str
    cutoff_fact_id: str
    cutoff_fact_sequence: int
    cutoff_fact_hash: str
    source_head_sequence: int
    source_head_hash: str
    execution_authority_hash: str
    items: tuple[RoleFinalRequestEvidenceAnchorV1, ...]

    def __post_init__(self) -> None:
        for field_name in _ROLE_FINAL_REQUEST_SLOT_STRING_FIELDS:
            _require_role_final_request_string(field_name, getattr(self, field_name))
        policy = role_final_request_policy(self.role)
        if self.schema_version != ROLE_FINAL_REQUEST_EVIDENCE_SLOT_SCHEMA:
            raise ValueError("role_final_request_slot_schema_mismatch")
        if self.ref_kind not in policy.slot_order:
            raise ValueError("role_final_request_slot_ref_kind_mismatch")
        text_fields = (
            self.ref_kind,
            self.canonical_source_ref,
            self.source_fact_schema,
            self.source_fact_version,
            self.factory_run_id,
            self.run_id,
            self.request_freeze_id,
            self.cutoff_fact_id,
        )
        if any(not value.strip() for value in text_fields):
            raise ValueError("role_final_request_slot_empty_binding")
        if isinstance(self.cutoff_fact_sequence, bool) or not isinstance(self.cutoff_fact_sequence, int):
            raise ValueError("cutoff_fact_sequence_must_be_positive")
        if self.cutoff_fact_sequence <= 0:
            raise ValueError("cutoff_fact_sequence_must_be_positive")
        if isinstance(self.source_head_sequence, bool) or not isinstance(self.source_head_sequence, int):
            raise ValueError("source_head_sequence_must_be_non_negative")
        if self.source_head_sequence < 0:
            raise ValueError("source_head_sequence_must_be_non_negative")
        for field_name, value in (
            ("cutoff_fact_hash", self.cutoff_fact_hash),
            ("source_head_hash", self.source_head_hash),
            ("execution_authority_hash", self.execution_authority_hash),
        ):
            if not _EXACT_HASH_64_RE.fullmatch(value):
                raise ValueError(f"{field_name}_must_be_64_lowercase_hex")
        if self.state not in _ROLE_FINAL_REQUEST_STATES:
            raise ValueError("role_final_request_slot_invalid_state")
        if not isinstance(self.items, tuple):
            raise ValueError("role_final_request_slot_items_must_be_tuple")
        if any(not isinstance(item, RoleFinalRequestEvidenceAnchorV1) for item in self.items):
            raise ValueError("role_final_request_slot_items_must_be_typed_anchor")
        if self.state == "present" and not self.items:
            raise ValueError("present_slot_items_must_not_be_empty")
        if self.state == "absent_at_request_time" and self.items:
            raise ValueError("absent_slot_items_must_be_empty")
        expected_binding = (
            self.ref_kind,
            self.canonical_source_ref,
            self.source_fact_schema,
            self.source_fact_version,
            self.factory_run_id,
            self.run_id,
            self.role,
            self.request_freeze_id,
            self.cutoff_fact_id,
            self.cutoff_fact_sequence,
            self.cutoff_fact_hash,
            self.source_head_sequence,
            self.source_head_hash,
            self.execution_authority_hash,
        )
        for item in self.items:
            item_binding = (
                item.ref_kind,
                item.canonical_source_ref,
                item.source_fact_schema,
                item.source_fact_version,
                item.factory_run_id,
                item.run_id,
                item.role,
                item.request_freeze_id,
                item.cutoff_fact_id,
                item.cutoff_fact_sequence,
                item.cutoff_fact_hash,
                item.source_head_sequence,
                item.source_head_hash,
                item.execution_authority_hash,
            )
            if item_binding != expected_binding:
                raise ValueError("role_final_request_slot_item_binding_mismatch")
            if item.source_fact_sequence > self.source_head_sequence:
                raise ValueError("source_fact_sequence_exceeds_head")

    @classmethod
    def create(
        cls,
        *,
        ref_kind: str,
        state: str,
        canonical_source_ref: str,
        source_fact_schema: str,
        source_fact_version: str,
        factory_run_id: str,
        run_id: str,
        role: str,
        request_freeze_id: str,
        cutoff_fact_id: str,
        cutoff_fact_sequence: int,
        cutoff_fact_hash: str,
        source_head_sequence: int,
        source_head_hash: str,
        execution_authority_hash: str,
        items: tuple[RoleFinalRequestEvidenceAnchorV1, ...],
    ) -> RoleFinalRequestEvidenceSlotV1:
        return cls(
            schema_version=ROLE_FINAL_REQUEST_EVIDENCE_SLOT_SCHEMA,
            ref_kind=_require_role_final_request_string("ref_kind", ref_kind).strip(),
            state=_require_role_final_request_string("state", state).strip(),
            canonical_source_ref=_require_role_final_request_string(
                "canonical_source_ref", canonical_source_ref
            ).strip(),
            source_fact_schema=_require_role_final_request_string("source_fact_schema", source_fact_schema).strip(),
            source_fact_version=_require_role_final_request_string("source_fact_version", source_fact_version).strip(),
            factory_run_id=_require_role_final_request_string("factory_run_id", factory_run_id).strip(),
            run_id=_require_role_final_request_string("run_id", run_id).strip(),
            role=_require_role_final_request_string("role", role).strip(),
            request_freeze_id=_require_role_final_request_string("request_freeze_id", request_freeze_id).strip(),
            cutoff_fact_id=_require_role_final_request_string("cutoff_fact_id", cutoff_fact_id).strip(),
            cutoff_fact_sequence=cutoff_fact_sequence,
            cutoff_fact_hash=_require_role_final_request_string("cutoff_fact_hash", cutoff_fact_hash).strip(),
            source_head_sequence=source_head_sequence,
            source_head_hash=_require_role_final_request_string("source_head_hash", source_head_hash).strip(),
            execution_authority_hash=_require_role_final_request_string(
                "execution_authority_hash", execution_authority_hash
            ).strip(),
            items=items,
        )

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> RoleFinalRequestEvidenceSlotV1:
        if not isinstance(record, Mapping) or frozenset(record) != _ROLE_FINAL_REQUEST_SLOT_FIELDS:
            raise ValueError("role_final_request_slot_fields_mismatch")
        string_fields = {
            field_name: _require_role_final_request_string(field_name, record.get(field_name))
            for field_name in _ROLE_FINAL_REQUEST_SLOT_STRING_FIELDS
        }
        raw_items = record.get("items")
        if not isinstance(raw_items, (list, tuple)):
            raise ValueError("role_final_request_slot_items_must_be_sequence")
        cutoff_fact_sequence = record.get("cutoff_fact_sequence")
        if isinstance(cutoff_fact_sequence, bool) or not isinstance(cutoff_fact_sequence, int):
            raise ValueError("cutoff_fact_sequence_must_be_positive")
        source_head_sequence = record.get("source_head_sequence")
        if isinstance(source_head_sequence, bool) or not isinstance(source_head_sequence, int):
            raise ValueError("source_head_sequence_must_be_non_negative")
        return cls(
            schema_version=string_fields["schema_version"],
            ref_kind=string_fields["ref_kind"],
            state=string_fields["state"],
            canonical_source_ref=string_fields["canonical_source_ref"],
            source_fact_schema=string_fields["source_fact_schema"],
            source_fact_version=string_fields["source_fact_version"],
            factory_run_id=string_fields["factory_run_id"],
            run_id=string_fields["run_id"],
            role=string_fields["role"],
            request_freeze_id=string_fields["request_freeze_id"],
            cutoff_fact_id=string_fields["cutoff_fact_id"],
            cutoff_fact_sequence=cutoff_fact_sequence,
            cutoff_fact_hash=string_fields["cutoff_fact_hash"],
            source_head_sequence=source_head_sequence,
            source_head_hash=string_fields["source_head_hash"],
            execution_authority_hash=string_fields["execution_authority_hash"],
            items=tuple(RoleFinalRequestEvidenceAnchorV1.from_record(item) for item in raw_items),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ref_kind": self.ref_kind,
            "state": self.state,
            "canonical_source_ref": self.canonical_source_ref,
            "source_fact_schema": self.source_fact_schema,
            "source_fact_version": self.source_fact_version,
            "factory_run_id": self.factory_run_id,
            "run_id": self.run_id,
            "role": self.role,
            "request_freeze_id": self.request_freeze_id,
            "cutoff_fact_id": self.cutoff_fact_id,
            "cutoff_fact_sequence": self.cutoff_fact_sequence,
            "cutoff_fact_hash": self.cutoff_fact_hash,
            "source_head_sequence": self.source_head_sequence,
            "source_head_hash": self.source_head_hash,
            "execution_authority_hash": self.execution_authority_hash,
            "items": [item.to_record() for item in self.items],
        }


@dataclass(frozen=True, slots=True)
class RoleFinalRequestPolicyFactsV1:
    """Validated ordered role slots for one frozen provider request."""

    schema_version: str
    role: str
    slots: tuple[RoleFinalRequestEvidenceSlotV1, ...]

    def __post_init__(self) -> None:
        for field_name in _ROLE_FINAL_REQUEST_POLICY_FACTS_STRING_FIELDS:
            _require_role_final_request_string(field_name, getattr(self, field_name))
        policy = role_final_request_policy(self.role)
        if self.schema_version != ROLE_FINAL_REQUEST_POLICY_FACTS_SCHEMA:
            raise ValueError("role_final_request_policy_facts_schema_mismatch")
        if not isinstance(self.slots, tuple) or any(
            not isinstance(slot, RoleFinalRequestEvidenceSlotV1) for slot in self.slots
        ):
            raise ValueError("role_final_request_policy_facts_slots_must_be_typed")
        kinds = tuple(slot.ref_kind for slot in self.slots)
        if kinds != policy.slot_order:
            raise ValueError("role_final_request_policy_facts_slot_order_mismatch")
        first = self.slots[0]
        for slot in self.slots:
            if slot.role != self.role:
                raise ValueError("role_final_request_policy_facts_role_mismatch")
            if (
                slot.factory_run_id != first.factory_run_id
                or slot.run_id != first.run_id
                or slot.request_freeze_id != first.request_freeze_id
                or slot.cutoff_fact_id != first.cutoff_fact_id
                or slot.cutoff_fact_sequence != first.cutoff_fact_sequence
                or slot.cutoff_fact_hash != first.cutoff_fact_hash
                or slot.execution_authority_hash != first.execution_authority_hash
            ):
                raise ValueError("role_final_request_policy_facts_binding_mismatch")
        required = frozenset(policy.required_present_slots)
        absent_required = [
            slot.ref_kind for slot in self.slots if slot.ref_kind in required and slot.state != "present"
        ]
        if absent_required:
            raise ValueError(f"role_final_request_policy_facts_required_slot_absent:{','.join(absent_required)}")

    @classmethod
    def create(
        cls,
        *,
        role: str,
        slots: Iterable[RoleFinalRequestEvidenceSlotV1],
    ) -> RoleFinalRequestPolicyFactsV1:
        return cls(
            schema_version=ROLE_FINAL_REQUEST_POLICY_FACTS_SCHEMA,
            role=_require_role_final_request_string("role", role).strip(),
            slots=tuple(slots),
        )

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> RoleFinalRequestPolicyFactsV1:
        if not isinstance(record, Mapping) or frozenset(record) != _ROLE_FINAL_REQUEST_POLICY_FACTS_FIELDS:
            raise ValueError("role_final_request_policy_facts_fields_mismatch")
        string_fields = {
            field_name: _require_role_final_request_string(field_name, record.get(field_name))
            for field_name in _ROLE_FINAL_REQUEST_POLICY_FACTS_STRING_FIELDS
        }
        raw_slots = record.get("slots")
        if not isinstance(raw_slots, (list, tuple)):
            raise ValueError("role_final_request_policy_facts_slots_must_be_sequence")
        return cls(
            schema_version=string_fields["schema_version"],
            role=string_fields["role"],
            slots=tuple(RoleFinalRequestEvidenceSlotV1.from_record(item) for item in raw_slots),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "role": self.role,
            "slots": [slot.to_record() for slot in self.slots],
        }


def render_role_final_request_policy_facts(facts: RoleFinalRequestPolicyFactsV1) -> str:
    """Render a prompt-safe projection of validated authority facts.

    ``RoleFinalRequestPolicyFactsV1.to_record()`` is the durable control-plane
    authority record.  Provider prompts need only evidence meaning and immutable
    content references; attempt, cutoff, source-head, and execution-authority
    identities must remain outside the data plane.
    """

    if not isinstance(facts, RoleFinalRequestPolicyFactsV1):
        raise ValueError("role_final_request_policy_facts_typed_value_required")
    prompt_projection = {
        "schema_version": ROLE_FINAL_REQUEST_POLICY_PROMPT_SCHEMA,
        "role": facts.role,
        "slots": [
            {
                "schema_version": ROLE_FINAL_REQUEST_EVIDENCE_PROMPT_SLOT_SCHEMA,
                "ref_kind": slot.ref_kind,
                "state": slot.state,
                "canonical_source_ref": slot.canonical_source_ref,
                "source_fact_schema": slot.source_fact_schema,
                "source_fact_version": slot.source_fact_version,
                "items": [
                    {
                        "schema_version": FINAL_REQUEST_EVIDENCE_PROMPT_ANCHOR_SCHEMA,
                        "ref_kind": item.ref_kind,
                        "canonical_source_ref": item.canonical_source_ref,
                        "canonical_ref": item.canonical_ref,
                        "canonical_hash": item.canonical_hash,
                        "source_fact_schema": item.source_fact_schema,
                        "source_fact_version": item.source_fact_version,
                    }
                    for item in slot.items
                ],
            }
            for slot in facts.slots
        ],
    }
    return canonical_role_final_request_json(prompt_projection)


def validate_role_final_request_policy_prompt_projection(
    record: Mapping[str, Any],
    *,
    expected_role: str,
) -> None:
    """Validate provider-visible evidence meaning without recreating authority.

    The prompt projection is deliberately incapable of reconstructing cutoff,
    execution, or attempt authority.  Runtime authorization must come from the
    separately carried ``RoleFinalRequestPolicyFactsV1`` binding; this validator
    checks only the closed, prompt-safe schema and its semantic invariants.
    """

    if not isinstance(record, Mapping) or frozenset(record) != _ROLE_FINAL_REQUEST_POLICY_PROMPT_FIELDS:
        raise ValueError("role_final_request_prompt_fields_mismatch")
    role = _require_role_final_request_string("role", record.get("role")).strip()
    expected = _require_role_final_request_string("expected_role", expected_role).strip()
    if role != expected:
        raise ValueError("role_final_request_prompt_role_mismatch")
    if record.get("schema_version") != ROLE_FINAL_REQUEST_POLICY_PROMPT_SCHEMA:
        raise ValueError("role_final_request_prompt_schema_mismatch")
    policy = role_final_request_policy(role)
    slots = record.get("slots")
    if not isinstance(slots, list) or len(slots) != len(policy.slot_order):
        raise ValueError("role_final_request_prompt_slot_order_mismatch")

    for expected_kind, slot in zip(policy.slot_order, slots, strict=True):
        if not isinstance(slot, Mapping) or frozenset(slot) != _ROLE_FINAL_REQUEST_PROMPT_SLOT_FIELDS:
            raise ValueError("role_final_request_prompt_slot_fields_mismatch")
        if slot.get("schema_version") != ROLE_FINAL_REQUEST_EVIDENCE_PROMPT_SLOT_SCHEMA:
            raise ValueError("role_final_request_prompt_slot_schema_mismatch")
        ref_kind = _require_role_final_request_string("ref_kind", slot.get("ref_kind")).strip()
        if ref_kind != expected_kind:
            raise ValueError("role_final_request_prompt_slot_order_mismatch")
        state = _require_role_final_request_string("state", slot.get("state")).strip()
        if state not in _ROLE_FINAL_REQUEST_STATES:
            raise ValueError("role_final_request_prompt_slot_state_invalid")
        canonical_source_ref = _require_role_final_request_string(
            "canonical_source_ref", slot.get("canonical_source_ref")
        ).strip()
        source_fact_schema = _require_role_final_request_string(
            "source_fact_schema", slot.get("source_fact_schema")
        ).strip()
        source_fact_version = _require_role_final_request_string(
            "source_fact_version", slot.get("source_fact_version")
        ).strip()
        if not canonical_source_ref or not source_fact_schema or not source_fact_version:
            raise ValueError("role_final_request_prompt_slot_empty_binding")
        items = slot.get("items")
        if not isinstance(items, list):
            raise ValueError("role_final_request_prompt_items_must_be_list")
        if state == "present" and not items:
            raise ValueError("role_final_request_prompt_present_items_missing")
        if state == "absent_at_request_time" and items:
            raise ValueError("role_final_request_prompt_absent_items_present")
        for item in items:
            if not isinstance(item, Mapping) or frozenset(item) != _ROLE_FINAL_REQUEST_PROMPT_ANCHOR_FIELDS:
                raise ValueError("role_final_request_prompt_anchor_fields_mismatch")
            if item.get("schema_version") != FINAL_REQUEST_EVIDENCE_PROMPT_ANCHOR_SCHEMA:
                raise ValueError("role_final_request_prompt_anchor_schema_mismatch")
            item_binding = (
                _require_role_final_request_string("ref_kind", item.get("ref_kind")).strip(),
                _require_role_final_request_string("canonical_source_ref", item.get("canonical_source_ref")).strip(),
                _require_role_final_request_string("source_fact_schema", item.get("source_fact_schema")).strip(),
                _require_role_final_request_string("source_fact_version", item.get("source_fact_version")).strip(),
            )
            if item_binding != (ref_kind, canonical_source_ref, source_fact_schema, source_fact_version):
                raise ValueError("role_final_request_prompt_anchor_binding_mismatch")
            canonical_ref = _require_role_final_request_string("canonical_ref", item.get("canonical_ref")).strip()
            canonical_hash = _require_role_final_request_string("canonical_hash", item.get("canonical_hash")).strip()
            if not canonical_ref or not _EXACT_HASH_64_RE.fullmatch(canonical_hash):
                raise ValueError("role_final_request_prompt_anchor_identity_invalid")


def redact_provider_transport(value: Any, *, key: str = "") -> Any:
    """Recursively redact transport secrets while preserving unknown semantics."""

    normalized_key = str(key).strip().lower().replace("-", "_")
    if normalized_key in _SECRET_KEYS or normalized_key.endswith(_SECRET_KEY_SUFFIXES):
        return {"redacted": True, "kind": "secret"}
    if value is None or isinstance(value, (bool, int, float, str)):
        if key == "endpoint" and isinstance(value, str) and "?" in value:
            return value.split("?", 1)[0]
        return value
    if isinstance(value, Mapping):
        return {str(item_key): redact_provider_transport(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_provider_transport(item, key=key) for item in value]
    raise ValueError("provider_config_not_snapshot_safe")


@dataclass(frozen=True, slots=True)
class ContextSnapshotAuditPinV1:
    schema_version: str
    workspace_abs: str
    runtime_root: str
    snapshot_logical_path: str
    snapshot_absolute_path: str
    snapshot_source: str
    factory_run_id: str
    role: str
    verification_scope: str
    request_freeze_id: str
    provider_request_id: str
    context_snapshot_ref: str
    storage_identity_token: str
    snapshot_content_hash: str
    composite_request_hash: str
    retention: str
    pin_hash: str

    @classmethod
    def create(
        cls,
        *,
        workspace_abs: str,
        runtime_root: str,
        snapshot_logical_path: str,
        snapshot_absolute_path: str,
        snapshot_source: str,
        factory_run_id: str,
        role: str,
        verification_scope: str,
        request_freeze_id: str,
        provider_request_id: str,
        context_snapshot_ref: str,
        storage_identity_token: str,
        snapshot_content_hash: str,
        composite_request_hash: str,
    ) -> ContextSnapshotAuditPinV1:
        try:
            ref = _validate_exact_context_snapshot_hash(str(context_snapshot_ref or ""))
        except ValueError as exc:
            raise ValueError("context_snapshot_ref must be exactly 24 lowercase hex") from exc
        payload = {
            "schema_version": "llm.context_snapshot_audit_pin.v1",
            "workspace_abs": str(workspace_abs or "").strip(),
            "runtime_root": str(runtime_root or "").strip(),
            "snapshot_logical_path": str(snapshot_logical_path or "").strip(),
            "snapshot_absolute_path": str(snapshot_absolute_path or "").strip(),
            "snapshot_source": str(snapshot_source or "").strip(),
            "factory_run_id": str(factory_run_id or "").strip(),
            "role": str(role or "").strip(),
            "verification_scope": str(verification_scope or "").strip(),
            "request_freeze_id": str(request_freeze_id or "").strip(),
            "provider_request_id": str(provider_request_id or "").strip(),
            "context_snapshot_ref": ref,
            "storage_identity_token": str(storage_identity_token or "").strip(),
            "snapshot_content_hash": str(snapshot_content_hash or "").strip(),
            "composite_request_hash": str(composite_request_hash or "").strip(),
            "retention": "pinned_audit_no_delete",
        }
        if any(not value for key_name, value in payload.items() if key_name not in {"schema_version", "retention"}):
            raise ValueError("context snapshot audit pin bindings must be non-empty")
        for field_name in ("snapshot_content_hash", "composite_request_hash"):
            if not _EXACT_HASH_64_RE.fullmatch(str(payload[field_name])):
                raise ValueError(f"{field_name} must be exactly 64 lowercase hex")
        try:
            _validate_exact_context_snapshot_hash(str(payload["storage_identity_token"]))
        except ValueError as exc:
            raise ValueError("storage_identity_token must be exactly 24 lowercase hex") from exc
        return cls(**payload, pin_hash=canonical_final_request_hash(payload))

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> ContextSnapshotAuditPinV1:
        if not isinstance(record, Mapping):
            raise ValueError("context snapshot audit pin must be a mapping")
        if frozenset(record) != _CONTEXT_SNAPSHOT_AUDIT_PIN_FIELDS:
            raise ValueError("context snapshot audit pin fields mismatch")
        if any(not isinstance(value, str) for value in record.values()):
            raise ValueError("context snapshot audit pin fields must be strings")
        created = cls.create(
            workspace_abs=str(record.get("workspace_abs") or ""),
            runtime_root=str(record.get("runtime_root") or ""),
            snapshot_logical_path=str(record.get("snapshot_logical_path") or ""),
            snapshot_absolute_path=str(record.get("snapshot_absolute_path") or ""),
            snapshot_source=str(record.get("snapshot_source") or ""),
            factory_run_id=str(record.get("factory_run_id") or ""),
            role=str(record.get("role") or ""),
            verification_scope=str(record.get("verification_scope") or ""),
            request_freeze_id=str(record.get("request_freeze_id") or ""),
            provider_request_id=str(record.get("provider_request_id") or ""),
            context_snapshot_ref=str(record.get("context_snapshot_ref") or ""),
            storage_identity_token=str(record.get("storage_identity_token") or ""),
            snapshot_content_hash=str(record.get("snapshot_content_hash") or ""),
            composite_request_hash=str(record.get("composite_request_hash") or ""),
        )
        if record.get("schema_version") != created.schema_version:
            raise ValueError("context snapshot audit pin schema mismatch")
        if record.get("retention") != created.retention:
            raise ValueError("context snapshot audit pin retention mismatch")
        if record.get("pin_hash") != created.pin_hash:
            raise ValueError("context snapshot audit pin hash mismatch")
        return created

    def to_record(self) -> dict[str, Any]:
        return asdict(self)
