"""Final LLM request context audit helpers.

This package is the lossless successor of the former ``context_audit`` module.
It re-exports every previously-public symbol from the same import path so that
``import ...llm_caller.context_audit`` and
``from ...llm_caller.context_audit import X`` keep resolving identically for all
external importers. There are no import-time side effects beyond binding the
same names that the former single module exposed.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from polaris.cells.control_plane.run_ledger.public import (
    looks_like_failure_evidence_payload,
    merge_failure_evidence_payload,
    summarize_failed_gate_evidence_context_slot,
)
from polaris.kernelone.audit.context_os_prompt import compact_context_os_audit
from polaris.kernelone.context.projection_engine import is_empty_run_card_message
from polaris.kernelone.events.final_request_evidence import (
    build_final_request_coverage_sources,
    build_final_request_evidence_slots,
    build_final_request_tool_slots,
    final_request_evidence_ref_for_requirement,
    final_request_evidence_refs_for_coverage_flags,
    final_request_included_evidence_refs,
    final_request_structured_evidence_from_metadata_summary,
    looks_like_ce_blueprint_payload,
    looks_like_pm_contract_payload,
    looks_like_workspace_quality_evidence_payload,
    missing_required_refs_from_evidence_coverage,
    missing_required_tools_from_evidence_coverage,
    role_final_request_policy,
    structured_context_coverage_flags,
    summarize_target_scope_evidence_payload,
    summarize_workspace_quality_evidence_context_slot,
    target_scope_evidence_entry,
)
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

from ...structured_output_transport import (
    STRUCTURED_OUTPUT_TOOL_NAME,
    STRUCTURED_OUTPUT_TRANSPORT_SCHEMA,
    StructuredOutputTransportPlan,
)
from ..final_request_metrics import canonical_message_chars
from ..response_types import PreparedLLMRequest
from ._builders import (
    build_final_provider_request_snapshot,
    build_final_request_context_audit,
    build_final_request_context_audit_for_request,
)
from ._constants import (
    _ARCHITECTURE_OR_FILE_PLAN_KEYS,
    _CE_BLUEPRINT_CONTEXT_KEYS,
    _EXECUTION_PROFILE_SUMMARY_KEYS,
    _FAILED_GATE_EVIDENCE_CONTEXT_KEYS,
    _INTERFACE_DISCREPANCY_CONTEXT_KEYS,
    _MODULE_INTERFACE_CONTRACT_KEYS,
    _NO_TOOL_CONTRACT_CONTEXT_KEYS,
    _OPTIONAL_CONTEXT_QUALITY_FLAGS,
    _PM_CONTRACT_CONTEXT_KEYS,
    _PROVIDER_PROTOCOL_COVERAGE_SCHEMA,
    _PROVIDER_PROTOCOL_SOURCE,
    _REF_BASED_SUPERSEDED_FINDING_CODES,
    _TOOL_REGISTRY_SOURCE,
    _UNDERUTILIZED_RATIO,
    _UNDERUTILIZED_WINDOW_THRESHOLD,
    _UNTRUSTED_USER_MESSAGE_RE,
    _WORKSPACE_QUALITY_EVIDENCE_CONTEXT_KEYS,
)
from ._evidence import (
    FinalRequestEvidenceCoverageError,
    _add_context_os_audit_findings,
    _add_evidence_coverage_findings,
    _envelope_hash_for_ref,
    _final_request_evidence_coverage,
    _final_request_evidence_enforcement_source,
    _final_request_hash,
    _ledger_evidence,
    _mapped_evidence_requirements,
    _prepared_context_os_audit,
    _required_evidence_refs,
    _workflow_chain,
    enforce_final_request_evidence_coverage,
    final_request_evidence_coverage_violation,
)
from ._findings import (
    _context_quality_findings,
    _coverage_flags,
    _message_projection_findings,
)
from ._payloads import (
    _actual_sibling_exports_message_bound,
    _actual_sibling_exports_payload,
    _architecture_or_file_plan_payload,
    _architecture_or_file_plan_summary,
    _architecture_payload_from_blueprint,
    _architecture_payload_from_delivery_contracts,
    _ce_blueprint_payload,
    _ce_blueprint_summary,
    _context_slot_payload,
    _direct_actual_sibling_exports_payload,
    _evidence_mapping_for_keys,
    _evidence_ref,
    _failed_gate_evidence_payload,
    _find_interface_discrepancy_context,
    _find_module_interface_contract,
    _find_structured_evidence_context,
    _first_evidence_mapping,
    _first_interface_discrepancy_mapping,
    _interface_discrepancy_context_payload,
    _looks_like_actual_sibling_exports,
    _looks_like_architecture_or_file_plan_payload,
    _looks_like_interface_discrepancy_payload,
    _looks_like_module_interface_contract,
    _looks_like_pm_contract_evidence,
    _module_interface_contract_payload,
    _module_interface_contract_summary,
    _pm_contract_payload,
    _pm_contract_summary,
    _request_metadata_summary,
    _request_sampling_audit,
    _target_scope_payload,
    _workspace_quality_evidence_payload,
)
from ._primitives import (
    _bool_value,
    _canonical_actual_sibling_exports_hash,
    _coerce_float,
    _coerce_int,
    _estimate_tokens_from_chars,
    _int_value,
    _is_sha256,
    _json_canonical,
    _json_chars,
    _json_safe,
    _mapping,
    _message_chars,
    _message_content_chars,
    _non_empty_attr,
    _stable_digest,
    _string_list,
    _unique_strings,
)
from ._request_core import (
    _context_window_tokens,
    _delivery_contract_payload,
    _execution_contract,
    _execution_contract_summary,
    _execution_envelope,
    _execution_envelope_hash,
    _execution_envelope_summary,
    _execution_profile,
    _execution_profile_summary,
    _execution_strategy,
    _execution_strategy_consistency_findings,
    _final_request_receipt_refs,
    _final_request_redaction_safety,
    _prompt_profile_selection,
    _receipt_refs_from_payload,
    _request_context,
    _request_messages,
    _request_option_payloads,
    _request_options,
    _resident_agi_audit_context,
    _resident_agi_audit_context_summary,
    _resident_agi_coverage_flags,
    _task_metadata,
    _task_type_value,
    _tool_execution_surface_audit,
)
from ._tools import (
    _allowed_tool_names,
    _allowed_tool_names_from_payload,
    _available_tool_names,
    _canonical_tool_name,
    _canonical_tool_names,
    _provider_protocol_schema_coverage,
    _required_tool_names,
    _required_tool_names_from_payload,
    _required_tools_exempt_reason,
    _summarize_response_format,
    _summarize_tool_schema,
    _tool_name_from_schema,
    _tool_names_from_payload,
    _tool_schema_properties,
    _tool_schema_registry_coverage,
)
