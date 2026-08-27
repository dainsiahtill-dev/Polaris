"""Payload detectors and context-slot summaries for final-request evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polaris.kernelone.events.final_request_evidence._constants import (
    _CONTEXT_SNAPSHOT_HASH_RE,
)
from polaris.kernelone.events.final_request_evidence._helpers import (
    _as_mapping,
    _bool_value,
    _first_text,
    _has_structural_field,
    _iter_context_mappings,
    _stable_hash,
    _string_sequence,
    _string_tokens,
    _text,
    _unique_texts,
)


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
    quality_metrics = found.get("quality_metrics")
    quality_minimums = found.get("quality_minimums")
    summary = {
        "schema_version": "polaris.workspace_quality_evidence.context_slot.v1",
        "source_schema_version": _text(found.get("schema_version")),
        "source": _text(found.get("source") or found.get("modality") or "workspace_quality_evidence"),
        "all_checks_passed": _bool_value(found.get("all_checks_passed")),
        "quality_error_count": len(found.get("quality_errors") or [])
        if isinstance(found.get("quality_errors"), (list, tuple))
        else 0,
        "quality_metrics": dict(quality_metrics) if isinstance(quality_metrics, Mapping) else {},
        "quality_minimums": dict(quality_minimums) if isinstance(quality_minimums, Mapping) else {},
        "deterministic_check_count": len(_string_tokens(found.get("deterministic_checks"))),
        "failed_required_modalities": _string_tokens(found.get("failed_required_modalities")),
        "missing_required_modalities": _string_tokens(found.get("missing_required_modalities")),
    }
    raw_failed_quality_metrics = found.get("failed_quality_metrics")
    if isinstance(raw_failed_quality_metrics, (list, tuple)):
        summary["failed_quality_metrics"] = _string_tokens(raw_failed_quality_metrics)
    return summary
