"""Tool-call lifecycle receipt contracts for Run Ledger projections.

This package is the lossless successor of the former ``tool_lifecycle`` module.
It re-exports every previously-public symbol from the same import path so that
``import ...public.tool_lifecycle`` and ``from ...public.tool_lifecycle import X``
keep resolving identically for all external importers.
"""

from __future__ import annotations

# Backward-compatible re-export of the standard-library / typing / sibling names
# that were module-level attributes of the former ``tool_lifecycle`` module.
# Keeping them bound here preserves the exact importable attribute surface
# after the split (``tool_lifecycle.Mapping``, ``tool_lifecycle.hashlib``, ...).
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from polaris.cells.control_plane.run_ledger.public.directed_effect_receipt_validation import (
    directed_effect_receipt_v2_errors,
)
from polaris.cells.control_plane.run_ledger.public.failure_evidence import (
    FailureClassV1,
    FailureEvidenceV1,
    append_failure_evidence_to_metadata,
    normalize_failure_class,
)
from polaris.cells.control_plane.run_ledger.public.tool_lifecycle._anomaly import (
    build_tool_dispatch_dropped_anomaly_from_lifecycle_receipt,
    build_tool_dispatch_dropped_anomaly_from_sources,
    build_tool_dispatch_dropped_anomaly_projection,
    build_tool_dispatch_dropped_lifecycle_from_anomaly_flags,
    build_tool_dispatch_dropped_lifecycle_from_observed_calls,
    project_completion_audit_evidence_to_metadata,
    project_completion_dispatch_evidence_to_metadata,
    project_lifecycle_failure_evidence_to_metadata,
    project_tool_lifecycle_metadata,
    project_tool_lifecycle_receipt_to_metadata,
    tool_dispatch_dropped_error_message,
)
from polaris.cells.control_plane.run_ledger.public.tool_lifecycle._helpers import (
    _effect_receipt_from_result,
)
from polaris.cells.control_plane.run_ledger.public.tool_lifecycle._projection import (
    ToolLifecycleRequirementV1,
    build_tool_lifecycle_requirement_run_ledger_event,
    empty_tool_lifecycle_summary,
    merge_tool_lifecycle_summaries,
    project_tool_lifecycle_event,
    project_tool_lifecycle_failure_status,
    project_tool_lifecycle_requirement,
    project_tool_lifecycle_summary,
    summarize_tool_lifecycle_events,
)
from polaris.cells.control_plane.run_ledger.public.tool_lifecycle._receipts import (
    NativeToolCallEnvelopeV1,
    ToolCallLifecycleReceiptV1,
    batch_receipt_has_dispatch_evidence,
    build_claimed_materialization_without_tool_lifecycle_receipt,
    build_missing_dispatch_lifecycle_receipt,
    build_native_tool_call_envelope_payloads,
    build_native_tool_call_envelopes,
    build_tool_batch_lifecycle_receipt,
    build_tool_batch_lifecycle_receipt_from_sources,
    build_tool_call_lifecycle_receipt,
    build_tool_call_lifecycle_run_ledger_event,
    build_verified_existing_artifact_lifecycle_receipt,
    effect_receipts_from_batch_receipts,
    failure_evidence_from_lifecycle_receipt,
    native_tool_call_count_from_facts,
    native_tool_call_count_from_metadata,
    native_tool_call_envelope_refs_from_metadata,
    native_tool_call_facts_from_lifecycle_receipt,
    native_tool_call_facts_from_metadata,
    native_tool_call_facts_from_raw_calls,
    native_tool_call_facts_from_sources,
    native_tool_call_names_from_facts,
    normalize_native_tool_call_envelope_refs,
    normalize_tool_call_lifecycle_receipt,
    observed_tool_call_names_from_sources,
    project_native_tool_call_envelopes_to_metadata,
    project_native_tool_call_facts_from_evidence_to_metadata,
    project_native_tool_call_facts_to_metadata,
    task_boundary_tool_dispatch_from_lifecycle_metadata,
    task_boundary_tool_dispatch_from_lifecycle_receipt,
    tool_call_lifecycle_receipts_from_metadata,
    tool_dispatch_dropped_guard_applies,
)
from polaris.kernelone.tools.tool_kinds import is_write_tool_name

__all__ = [
    "NativeToolCallEnvelopeV1",
    "ToolCallLifecycleReceiptV1",
    "ToolLifecycleRequirementV1",
    "batch_receipt_has_dispatch_evidence",
    "build_claimed_materialization_without_tool_lifecycle_receipt",
    "build_missing_dispatch_lifecycle_receipt",
    "build_native_tool_call_envelope_payloads",
    "build_native_tool_call_envelopes",
    "build_tool_batch_lifecycle_receipt",
    "build_tool_batch_lifecycle_receipt_from_sources",
    "build_tool_call_lifecycle_receipt",
    "build_tool_call_lifecycle_run_ledger_event",
    "build_tool_dispatch_dropped_anomaly_from_lifecycle_receipt",
    "build_tool_dispatch_dropped_anomaly_from_sources",
    "build_tool_dispatch_dropped_anomaly_projection",
    "build_tool_dispatch_dropped_lifecycle_from_anomaly_flags",
    "build_tool_dispatch_dropped_lifecycle_from_observed_calls",
    "build_tool_lifecycle_requirement_run_ledger_event",
    "effect_receipts_from_batch_receipts",
    "empty_tool_lifecycle_summary",
    "failure_evidence_from_lifecycle_receipt",
    "merge_tool_lifecycle_summaries",
    "native_tool_call_count_from_facts",
    "native_tool_call_count_from_metadata",
    "native_tool_call_envelope_refs_from_metadata",
    "native_tool_call_facts_from_lifecycle_receipt",
    "native_tool_call_facts_from_metadata",
    "native_tool_call_facts_from_raw_calls",
    "native_tool_call_facts_from_sources",
    "native_tool_call_names_from_facts",
    "normalize_native_tool_call_envelope_refs",
    "normalize_tool_call_lifecycle_receipt",
    "observed_tool_call_names_from_sources",
    "project_completion_audit_evidence_to_metadata",
    "project_completion_dispatch_evidence_to_metadata",
    "project_lifecycle_failure_evidence_to_metadata",
    "project_native_tool_call_envelopes_to_metadata",
    "project_native_tool_call_facts_from_evidence_to_metadata",
    "project_native_tool_call_facts_to_metadata",
    "project_tool_lifecycle_event",
    "project_tool_lifecycle_failure_status",
    "project_tool_lifecycle_metadata",
    "project_tool_lifecycle_receipt_to_metadata",
    "project_tool_lifecycle_requirement",
    "project_tool_lifecycle_summary",
    "summarize_tool_lifecycle_events",
    "task_boundary_tool_dispatch_from_lifecycle_metadata",
    "task_boundary_tool_dispatch_from_lifecycle_receipt",
    "tool_call_lifecycle_receipts_from_metadata",
    "tool_dispatch_dropped_error_message",
    "tool_dispatch_dropped_guard_applies",
]
