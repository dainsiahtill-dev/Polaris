"""Final provider-request audit evidence projection helpers.

This package is the lossless successor of the former ``final_request_evidence``
module. It re-exports every previously-public symbol from the same import path so
that ``import polaris.kernelone.events.final_request_evidence`` and
``from polaris.kernelone.events.final_request_evidence import X`` keep resolving
identically for all external importers.

Import-time construction of ``_ROLE_FINAL_REQUEST_POLICIES`` (via
``RoleFinalRequestPolicyV1`` instances in ``_policy``) runs exactly once when
this package is first imported.
"""

from __future__ import annotations

from polaris.kernelone.events.final_request_evidence._audit_pin import (
    ContextSnapshotAuditPinV1,
)
from polaris.kernelone.events.final_request_evidence._constants import (
    AUDIT_REFS_SCHEMA,
    FINAL_REQUEST_EVIDENCE_ANCHOR_SCHEMA,
    FINAL_REQUEST_EVIDENCE_AUTHORITY_SCHEMA,
    FINAL_REQUEST_EVIDENCE_PROMPT_ANCHOR_SCHEMA,
    FINAL_REQUEST_EVIDENCE_SCHEMA,
    FINAL_REQUEST_EVIDENCE_SLOT_SCHEMA,
    ROLE_FINAL_REQUEST_EVIDENCE_PROMPT_SLOT_SCHEMA,
    ROLE_FINAL_REQUEST_EVIDENCE_SLOT_SCHEMA,
    ROLE_FINAL_REQUEST_POLICY_FACTS_SCHEMA,
    ROLE_FINAL_REQUEST_POLICY_PROMPT_SCHEMA,
    ROLE_FINAL_REQUEST_POLICY_SCHEMA,
)
from polaris.kernelone.events.final_request_evidence._coverage import (
    build_final_request_coverage_sources,
    final_request_evidence_ref_for_coverage_flag,
    final_request_evidence_ref_for_requirement,
    final_request_evidence_refs_for_coverage_flags,
    final_request_evidence_refs_for_metadata_summary,
    final_request_included_evidence_refs,
    final_request_structured_evidence_from_metadata_summary,
)
from polaris.kernelone.events.final_request_evidence._evidence import (
    attach_final_request_evidence,
    build_final_request_evidence,
    canonical_final_request_hash,
)
from polaris.kernelone.events.final_request_evidence._payloads import (
    looks_like_ce_blueprint_payload,
    looks_like_failed_gate_evidence_context_payload,
    looks_like_pm_contract_payload,
    looks_like_target_scope_payload,
    looks_like_workspace_quality_evidence_payload,
    normalize_context_snapshot_ref,
    structured_context_coverage_flags,
    summarize_target_scope_evidence_payload,
    summarize_workspace_quality_evidence_context_slot,
    target_scope_evidence_entry,
)
from polaris.kernelone.events.final_request_evidence._policy import (
    RoleFinalRequestPolicyV1,
    canonical_role_final_request_hash,
    canonical_role_final_request_json,
    role_final_request_policy,
    role_final_request_source_keys,
)
from polaris.kernelone.events.final_request_evidence._redact import (
    redact_provider_transport,
)
from polaris.kernelone.events.final_request_evidence._role_contracts import (
    RoleFinalRequestEvidenceAnchorV1,
    RoleFinalRequestEvidenceSlotV1,
    RoleFinalRequestPolicyFactsV1,
    render_role_final_request_policy_facts,
    validate_role_final_request_policy_prompt_projection,
)
from polaris.kernelone.events.final_request_evidence._slots import (
    build_final_request_evidence_slots,
    build_final_request_tool_slots,
    missing_required_refs_from_evidence_coverage,
    missing_required_refs_from_evidence_slots,
    missing_required_tools_from_evidence_coverage,
    missing_required_tools_from_tool_slots,
)

__all__ = [
    "AUDIT_REFS_SCHEMA",
    "FINAL_REQUEST_EVIDENCE_ANCHOR_SCHEMA",
    "FINAL_REQUEST_EVIDENCE_AUTHORITY_SCHEMA",
    "FINAL_REQUEST_EVIDENCE_PROMPT_ANCHOR_SCHEMA",
    "FINAL_REQUEST_EVIDENCE_SCHEMA",
    "FINAL_REQUEST_EVIDENCE_SLOT_SCHEMA",
    "ROLE_FINAL_REQUEST_EVIDENCE_PROMPT_SLOT_SCHEMA",
    "ROLE_FINAL_REQUEST_EVIDENCE_SLOT_SCHEMA",
    "ROLE_FINAL_REQUEST_POLICY_FACTS_SCHEMA",
    "ROLE_FINAL_REQUEST_POLICY_PROMPT_SCHEMA",
    "ROLE_FINAL_REQUEST_POLICY_SCHEMA",
    "ContextSnapshotAuditPinV1",
    "RoleFinalRequestEvidenceAnchorV1",
    "RoleFinalRequestEvidenceSlotV1",
    "RoleFinalRequestPolicyFactsV1",
    "RoleFinalRequestPolicyV1",
    "attach_final_request_evidence",
    "build_final_request_coverage_sources",
    "build_final_request_evidence",
    "build_final_request_evidence_slots",
    "build_final_request_tool_slots",
    "canonical_final_request_hash",
    "canonical_role_final_request_hash",
    "canonical_role_final_request_json",
    "final_request_evidence_ref_for_coverage_flag",
    "final_request_evidence_ref_for_requirement",
    "final_request_evidence_refs_for_coverage_flags",
    "final_request_evidence_refs_for_metadata_summary",
    "final_request_included_evidence_refs",
    "final_request_structured_evidence_from_metadata_summary",
    "looks_like_ce_blueprint_payload",
    "looks_like_failed_gate_evidence_context_payload",
    "looks_like_pm_contract_payload",
    "looks_like_target_scope_payload",
    "looks_like_workspace_quality_evidence_payload",
    "missing_required_refs_from_evidence_coverage",
    "missing_required_refs_from_evidence_slots",
    "missing_required_tools_from_evidence_coverage",
    "missing_required_tools_from_tool_slots",
    "normalize_context_snapshot_ref",
    "redact_provider_transport",
    "render_role_final_request_policy_facts",
    "role_final_request_policy",
    "role_final_request_source_keys",
    "structured_context_coverage_flags",
    "summarize_target_scope_evidence_payload",
    "summarize_workspace_quality_evidence_context_slot",
    "target_scope_evidence_entry",
    "validate_role_final_request_policy_prompt_projection",
]
