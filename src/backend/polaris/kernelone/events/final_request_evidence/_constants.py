"""Schema constants and pure mapping tables for final-request evidence."""

from __future__ import annotations

import re

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

_ROLE_FINAL_REQUEST_SOURCE_KEYS: dict[str, tuple[str, ...]] = {
    "pm_raw_intent": ("pm_raw_intent",),
    "pm_contract": ("pm_contract", "pm_task_contract", "pm_task_contracts"),
    "ce_blueprint": ("ce_blueprint", "chief_engineer_blueprint"),
    "target_files": ("target_files", "scope_paths"),
    "verifier_receipts": ("verifier_receipts",),
    "failure_feedback": ("failure_feedback", "failed_gate_evidence"),
    "workspace_quality": ("workspace_quality", "workspace_quality_evidence"),
}
