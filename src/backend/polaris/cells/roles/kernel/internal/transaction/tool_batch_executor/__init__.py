"""工具批次执行器 — 负责 TOOL_BATCH 决策的权威执行与最终化路由。

包含:
- ToolBatchExecutor: 主执行器类
- 路径重写与 receipt 记录等辅助函数

This package is the lossless successor of the former ``tool_batch_executor``
module. It re-exports every previously-public symbol from the same import path
so that ``import ...transaction.tool_batch_executor`` and
``from ...transaction.tool_batch_executor import X`` keep resolving identically
for all external importers.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, NoReturn, cast

from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    AppendToolCallLifecycleEventCommandV1,
    FailureClassV1,
    append_run_ledger_event,
    append_tool_call_lifecycle_event,
    build_tool_batch_lifecycle_receipt_from_sources,
    build_tool_dispatch_dropped_anomaly_from_lifecycle_receipt,
    effect_receipts_from_batch_receipts,
)
from polaris.cells.director.runtime.public import DirectedEffectImmutableItemsV1
from polaris.cells.roles.kernel.internal.deferred_repair_effects import (
    DeferredRepairEffectSynthesizer,
    DeferredRequestReplayFence,
)
from polaris.cells.roles.kernel.internal.directed_effect_lifecycle import (
    DirectedEffectLifecycleCandidateV1,
    DirectedEffectLifecycleService,
)
from polaris.cells.roles.kernel.internal.directed_effect_policy_guard import (
    DirectedEffectAuthoritativePolicyGuardRequestV1,
    DirectedEffectPolicyGuardResultV1,
)
from polaris.cells.roles.kernel.internal.speculation.models import CancelToken
from polaris.cells.roles.kernel.internal.speculation.write_phases import WriteToolPhases
from polaris.cells.roles.kernel.internal.speculative_flags import is_adoption_audit_enabled
from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolBatchRuntime, ToolExecutionContext
from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    batch_write_failure_error_types,
    batch_write_failures_require_llm_replan,
    batch_write_results_all_failed_on_argument_shape,
    extract_allowed_scope_paths_from_message,
    extract_invocation_tool_name,
    extract_target_file_from_invocation_args,
    extract_target_files_from_message,
    filter_out_of_scope_write_invocations,
    filter_scope_paths_for_explicit_targets,
    receipts_have_stale_edit_failure,
    resolve_mutation_target_guard_violation,
    tool_batch_has_authoritative_write_invocation,
)
from polaris.cells.roles.kernel.internal.transaction.deferred_repair_followup import (
    DeferredCommandEffectSynthesizer,
    build_deferred_repair_followup,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    BlockedReason,
    DeliveryMode,
)
from polaris.cells.roles.kernel.internal.transaction.effect_policy import (
    CompiledEffectPolicy,
    EffectPolicyViolationError,
    get_effect_policy_mode,
)
from polaris.cells.roles.kernel.internal.transaction.handoff_handlers import build_workflow_handoff_context
from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
    requires_mutation_intent as _default_requires_mutation_intent,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.transaction.phase_manager import (
    extract_tool_results_from_batch_receipt,
)
from polaris.cells.roles.kernel.internal.transaction.receipt_utils import (
    merge_batch_receipts,
    normalize_batch_receipts,
    record_receipts_to_ledger,
)
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    extract_latest_user_message,
    extract_platform_tool_contract_missing_target_files,
    extract_platform_tool_contract_scope_paths,
    extract_platform_tool_contract_target_files,
    platform_tool_contract_bypasses_read_write_barrier,
    platform_tool_contract_disables_phase_manager,
)
from polaris.cells.roles.kernel.internal.transaction.tool_call_audit_refs import tool_invocation_audit_ref
from polaris.cells.roles.kernel.internal.transaction.tool_failure_circuit_breaker import (
    ToolFailureCircuitBreaker,
)
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.public.directed_effect_contracts import (
    DeferredDirectorRepairEffectBindingV1,
    DirectedEffectRuntimeDependenciesV1,
    PreparedDirectedEffectBatchV1,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    ToolBatch,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    TurnDecision,
    TurnId,
    _infer_effect_type,
)
from polaris.cells.roles.kernel.public.turn_events import ErrorEvent, TurnEvent, TurnPhaseEvent
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptAuthorityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.kernelone.tools.tool_kinds import DEPRECATED_WRITE_TOOLS

from ._executor import ToolBatchExecutor
from ._helpers import (
    _DEO_PREPARE_LOCK_RETRY_ATTEMPTS,
    _DEO_PREPARE_LOCK_RETRY_BASE_SECONDS,
    _DIRECT_READ_TOOLS,
    _EDIT_FAILURE_MARKERS,
    _FILE_ARGUMENT_KEYS,
    _LINE_RANGE_REPLACEMENT_KEYS,
    _MUTATION_PATH_ARGUMENT_KEYS,
    _NO_WRITE_STRUCTURED_FLAG,
    _NO_WRITE_STRUCTURED_ROLES,
    _TOOL_NAME_CANONICAL_ALIASES,
    _TRANSIENT_DEO_PREPARE_UPSTREAM_CODES,
    _VALID_TOOL_EFFECT_TYPES,
    _VALID_TOOL_EXECUTION_MODES,
    _WRITE_FILE_AUTOFILL_BASIS,
    WRITE_FILE_AUTOFILL_EVIDENCE_KEY,
    WRITE_FILE_DUPLICATE_REJECTION_KEY,
    _append_tool_batch_receipts_to_run_ledger,
    _batch_has_authoritative_success,
    _batch_result_count,
    _canonical_single_target_file,
    _capability_token_from_effect_receipt,
    _capability_token_from_metadata,
    _collapse_last_write_wins_mutations,
    _deo_prepare_upstream_code,
    _effect_receipts_from_batch_receipts,
    _execution_envelope_hash_from_metadata,
    _invocation_arguments,
    _invocation_call_id,
    _is_deo_abort_error,
    _is_mutation_for_speculative_routing,
    _is_no_write_structured_turn,
    _is_path_within_workspace,
    _is_transient_deo_prepare_lock_failure,
    _is_valid_execution_mode,
    _job_token_from_capability_token,
    _mapping_value,
    _merge_batch_receipts,
    _mutation_target_path_key,
    _normalize_allowed_tool_name_alias,
    _normalize_capability_token,
    _normalize_file_reference_path,
    _normalize_write_content_for_duplicate_check,
    _PreparedDirectedEffectDispatchV1,
    _recent_edit_failure_in_context,
    _resolve_existing_workspace_file,
    _resolve_missing_execution_mode,
    _resolve_tool_batch_execution_identity,
    _safe_contract_target_files,
    _seal_deo_abort_tool_lifecycle,
    _set_invocation_effect_type,
    _set_invocation_execution_mode,
    _tool_name_allowed_by_alias,
    _tool_requires_existing_file,
    _with_invocation_arguments,
    _with_invocation_top_level_field,
    annotate_autofilled_write_receipts,
    diff_write_file_autofill_evidence,
    fill_content_only_write_file_from_remaining_targets,
    fill_single_target_line_range_edit_blocks,
    normalize_replay_execution_modes,
    rewrite_existing_file_paths_in_invocations,
    split_write_file_duplicate_content_rejections,
)

# Preserve historical logger identity (former single-module __name__).
logger = logging.getLogger(__name__)
