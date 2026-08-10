"""Director consumer for PENDING_EXEC tasks with Safe Parallel support.

This package is the lossless successor of the former ``director_consumer`` module.
It re-exports every previously-public symbol from the same import path so
that ``import ...internal.director_consumer`` and
``from ...internal.director_consumer import X`` keep resolving identically for
all external importers and characterization tests.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Coroutine

from polaris.cells.chief_engineer.blueprint.public import validate_director_handoff_from_payload
from polaris.cells.director.task_consumer.public.project_verification import (
    ProjectVerificationReceiptV1,
    QueryProjectVerificationReceiptV1,
    ResolveProjectVerificationAuthorityQueryV1,
    authorize_project_verification_command,
    query_project_verification_receipt,
    run_project_verification,
)
from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    FailTaskStageCommandV1,
    QueryTaskMarketStatusV1,
    RenewTaskLeaseCommandV1,
    TaskMarketError,
)
from polaris.cells.runtime.task_market.public.service import get_task_market_service
from polaris.kernelone.fs.materialization import materialized_file_paths
from polaris.kernelone.quality import resolve_owner_handoff_routing, task_record_routing_key

from ._consumer import DirectorExecutionConsumer
from ._helpers import (
    _DEFAULT_REPAIR_SHRINK_GUARD_RATIO,
    _MAX_STRUCTURED_FAILURE_MAPPINGS,
    _NO_CHANGE_FLAGS,
    _NO_CHANGE_MODES,
    _OWNER_HANDOFF_REQUEST_KEYS,
    _OWNER_HANDOFF_TASK_RECORD_LIMIT,
    _REPAIR_SHRINK_GUARD_RATIO_ENV,
    _REPAIR_SHRINK_MIN_PRIOR_BYTES,
    _ROUTE_CHIEF_BLUEPRINT_REQUIRED,
    _ROUTE_DIRECT_TO_DIRECTOR,
    _STRUCTURED_FAILURE_MAPPING_KEYS,
    _STRUCTURED_FAILURE_SEQUENCE_KEYS,
    _VERIFIED_EXISTING_SCOPE_MODES,
    DirectorTaskExecutor,
    InterfaceContractAmendmentRequiredError,
    InterfaceContractRepairRequiredError,
    UnrecoverableExecutionError,
    _adapter_failure_message,
    _allows_no_execution_evidence,
    _append_normalized_paths,
    _attach_handoff_validation_payload,
    _await_with_optional_timeout,
    _build_director_adapter_input,
    _changed_files_cover_target,
    _compact_director_adapter_summary,
    _contains_owner_handoff_requests,
    _contract_amendment_scan_scope,
    _contract_authority_blocker,
    _dedupe_normalized_paths,
    _director_evidence_status,
    _director_execution_timeout_seconds,
    _extract_changed_files_from_mapping,
    _extract_director_changed_files,
    _extract_director_side_effects,
    _fill_assembly_baseline,
    _fill_assembly_drift_error,
    _fill_assembly_owned_anchors,
    _final_convergence_failure,
    _final_convergence_scan_scope,
    _first_failure_evidence_rows,
    _first_structured_failure_token,
    _has_verified_existing_scope_evidence,
    _interface_contract_amendment_from_adapter_failure,
    _interface_contract_repair_from_adapter_failure,
    _job_token_from_payload,
    _mapping_copy,
    _normalize_handoff_validation_result,
    _normalize_string_list,
    _normalize_task_market_route,
    _owner_handoff_evidence_metadata,
    _owner_handoff_failure_from_adapter_failure,
    _owner_handoff_failure_metadata,
    _owner_handoff_failure_projection,
    _OwnerHandoffFailure,
    _OwnerHandoffRoutingRequiredError,
    _pre_state_punch_list,
    _QaLocalRepairAuthority,
    _read_consumed_interfaces,
    _read_target_file_content,
    _repair_prior_target_size,
    _repair_shrink_error,
    _repair_shrink_guard_ratio,
    _resolve_qa_local_repair_authority,
    _revalidate_qa_exact_verifier,
    _run_coroutine_sync,
    _scan_director_artifact_quality_evidence,
    _step_target_file,
    _structured_adapter_failure_mappings,
    _task_projection_artifact_state,
    _truthy_payload_flag,
    _validated_blueprint_handoff,
    _verification_receipt_query_from_command,
    _verified_existing_scope_covers_target,
)
from ._scope_lease import (
    ScopeConflictDetector,
    _LeaseHeartbeat,
)

# Preserve original module logger identity surface (public dir() includes ``logger``).
logger = logging.getLogger(__name__)

__all__ = [
    "DirectorExecutionConsumer",
    "InterfaceContractAmendmentRequiredError",
    "InterfaceContractRepairRequiredError",
    "UnrecoverableExecutionError",
]
