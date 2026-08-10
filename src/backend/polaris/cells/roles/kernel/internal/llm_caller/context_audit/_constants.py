from __future__ import annotations

import re

_UNDERUTILIZED_WINDOW_THRESHOLD = 8192

_UNDERUTILIZED_RATIO = 0.15

_UNTRUSTED_USER_MESSAGE_RE = re.compile(r"\[UNTRUSTED_USER_MESSAGE\].*", re.IGNORECASE | re.DOTALL)

_REF_BASED_SUPERSEDED_FINDING_CODES = frozenset(
    {
        "missing_context_coverage",
        "underutilized_with_missing_context",
    }
)

_OPTIONAL_CONTEXT_QUALITY_FLAGS = frozenset(
    {
        # Only tasks with prior sibling modules can provide actual export evidence.
        # Explicit evidence requirements still fail closed through
        # final_request_evidence_coverage.
        "has_actual_sibling_exports",
    }
)

_TOOL_REGISTRY_SOURCE = "polaris.kernelone.tool_execution.ToolSpecRegistry"

_PROVIDER_PROTOCOL_COVERAGE_SCHEMA = "polaris.provider_protocol_schema_coverage.v1"

_PROVIDER_PROTOCOL_SOURCE = "roles.kernel.structured_output_transport"

_EXECUTION_PROFILE_SUMMARY_KEYS = (
    "schema_version",
    "source",
    "dispatch_type",
    "task_type",
    "phase",
    "project_type",
    "artifact_type",
    "language",
    "language_version",
    "runtime",
    "framework",
    "file_role",
    "task_focus",
    "sampling_mode",
    "temperature_phase",
    "temperature_source",
    "output_contract_id",
)

_MODULE_INTERFACE_CONTRACT_KEYS = (
    "module_interface_contract",
    "cross_file_interface_contract",
    "cross_artifact_interface_contract",
    "interface_contract",
)

_ARCHITECTURE_OR_FILE_PLAN_KEYS = (
    "architecture_or_file_plan",
    "architecture_plan",
    "file_plan",
    "construction_plan",
    "scope_for_apply",
    "architecture_decisions",
    "implementation_phases",
    "module_boundaries",
    "scope_for_apply_advisory",
)

_PM_CONTRACT_CONTEXT_KEYS = (
    "pm_contract",
    "pm_task_contract",
    "task_contract",
    "execution_task_contract",
)

_CE_BLUEPRINT_CONTEXT_KEYS = (
    "ce_blueprint",
    "chief_engineer_blueprint",
    "blueprint",
    "blueprint_payload",
    "task_blueprint",
)

_INTERFACE_DISCREPANCY_CONTEXT_KEYS = (
    "interface_discrepancy_context",
    "interface_discrepancy_evidence",
    "interface_discrepancy_receipt",
    "interface_discrepancy_receipts",
    "director_interface_discrepancy_receipt",
    "director_interface_discrepancy_receipts",
    "task_boundary_interface_discrepancy",
    "task_boundary_interface_discrepancy_retry",
    "director_interface_discrepancy_retry",
)

_FAILED_GATE_EVIDENCE_CONTEXT_KEYS = (
    "failed_gate_evidence",
    "failed_gate_or_verification_evidence",
    "failure_evidence",
    "failure_evidence_summary",
    "verification_failure_evidence",
    "verification_evidence",
    "failure_feedback",
    "qa_failure_evidence",
)

_WORKSPACE_QUALITY_EVIDENCE_CONTEXT_KEYS = (
    "workspace_quality_evidence",
    "factory_workspace_quality",
    "artifact_quality_evidence",
    "quality_gate_evidence",
    "workspace_quality",
    "real_run_gate",
)

_NO_TOOL_CONTRACT_CONTEXT_KEYS = (
    "tool_contract_require_no_tool_calls",
    "require_no_tool_calls",
    "no_tool_calls",
)
