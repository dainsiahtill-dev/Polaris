"""Director role adapter package.

This package is the lossless successor of the former ``adapter`` module.
It re-exports every previously-public symbol from the same import path so that
``import ...director.adapter`` and ``from ...director.adapter import X`` keep
resolving identically for all external importers.
"""

from __future__ import annotations
import __future__ as _future_mod

# Backward-compatible re-export of stdlib / typing names that were module-level
# attributes of the former ``adapter`` module (surface oracle / dir() parity).
import asyncio
import hashlib
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, cast

from polaris.cells.director.tasking.public.execution_guidance import (
    apply_task_execution_strategy_overrides,
    build_task_language_section,
    coerce_task_execution_profile,
    resolve_task_execution_profile,
    resolve_task_execution_strategy,
)
from polaris.kernelone.events.final_request_evidence import (
    looks_like_ce_blueprint_payload,
    looks_like_pm_contract_payload,
)
from polaris.kernelone.llm.budget_policy import (
    FORCED_WRITE_CONTEXT_KEYS,
    FORCED_WRITE_OUTPUT_TOKEN_FLOOR,
    FORCED_WRITE_STAGE_MARKERS,
    OUTPUT_BUDGET_CONTEXT_KEYS,
    TIMEOUT_CEILING_CONTEXT_KEYS,
    TIMEOUT_OVERRIDE_CONTEXT_KEYS,
    forced_write_output_token_ceiling,
    forced_write_retry_timeout_seconds,
)

from ...base import BaseRoleAdapter
from ...director_execution_backend import (
    DirectorExecutionBackendRequest,
    resolve_director_execution_backend,
)
from ..adapter_sequential import (
    build_sequential_config,
    execute_hybrid,
    execute_sequential,
)
from ..dependency_artifact_evidence import (
    DIRECTOR_DEPENDENCY_ARTIFACT_SNAPSHOT_CONTEXT_KEY,
    DirectorDependencyArtifactEvidenceError,
    TrustedDirectorDependencyArtifactSnapshotV2,
    build_director_dependency_artifact_snapshot,
    project_director_dependency_artifact_snapshot,
    query_project_artifact_receipt_payload,
)
from ..dialogue import get_settings_safe
from ..execute_method import execute_director_task
from ..execution import DirectorPatchExecutor
from ..helpers import (
    is_empty_role_response,
    taskboard_snapshot_brief,
)
from ..state_tracking import DirectorStateTracker
from ..state_utils import (
    compose_projection_requirement,
    default_projection_slug,
)
from ._core import DirectorAdapter
from ._payload import (
    _EXECUTION_AUTHORITY_ENVELOPE_KEYS,
    _copy_dict_list_payload,
    _copy_mapping_payload,
    _first_dict_list_payload,
    _first_mapping_payload,
    _is_lower_sha256,
    _project_director_execution_authority_evidence,
    _string_list_payload,
)
from ._role_response import (
    _BACKTICK_VERIFICATION_COMMAND_RE,
    _VERIFICATION_COMMAND_MARKERS,
    _extract_director_role_runtime_error,
    _extract_director_verification_commands,
    _flatten_verification_command_sources,
    _normalize_director_role_response,
)
from ._task_contract import (
    _AUTHORITATIVE_TASK_BOUNDARY_LIST_KEYS,
    _ROLE_RUNTIME_METADATA_CONTEXT_EVIDENCE_KEYS,
    _STRUCTURED_TASK_CONTRACT_SLOT_KEYS,
    _TASK_CONTRACT_LIST_KEYS,
    _TASK_CONTRACT_MAPPING_KEYS,
    _TASK_CONTRACT_SCALAR_KEYS,
    _TASK_RUNTIME_GOVERNANCE_SCALAR_KEYS,
    _build_director_blueprint_handoff_lines,
    _contract_list,
    _director_actual_interface_injection_enabled,
    _first_contract_value,
    _has_contract_value,
    _load_ce_blueprint_contract_payload,
    _looks_like_module_interface_contract_payload,
    _merge_ce_blueprint_contract_payload,
    _merge_contract_lists,
    _normalize_contract_task_token,
    _promoted_task_contract_payload,
    _set_structured_task_contract_slot,
    _structured_task_contract_slot_is_authoritative,
    _task_contract_sources,
)
from ._timeout_budget import (
    _DIRECTOR_ROLE_SUBINVOCATION_SCHEMA,
    _FORCED_WRITE_CONTEXT_KEYS,
    _FORCED_WRITE_STAGE_MARKERS,
    _OUTPUT_BUDGET_CONTEXT_KEYS,
    _ROLE_CALL_TIMEOUT_CEILING_KEYS,
    _ROLE_CALL_TIMEOUT_KEYS,
    _ROLE_DIALOGUE_SETTLEMENT_GRACE_SECONDS,
    _TRANSACTION_EXECUTION_SCOPE_KEYS,
    _bind_director_role_subinvocation,
    _coerce_positive_float,
    _coerce_positive_int,
    _context_has_forced_write_retry,
    _context_timeout_seconds_for_runtime_command,
    _current_task_write_boundary_context,
    _forced_write_effective_output_budget,
    _join_limited_values,
    _path_looks_like_test_target,
    _prepare_role_dialogue_context,
    _resolve_role_call_timeout,
    _role_call_timeout_ceiling_from_context,
    _role_call_timeout_from_context,
    _role_dialogue_watchdog_timeout_seconds,
)

# Preserve the exact logger identity name used by the former module.
logger = logging.getLogger(__name__)

# Bind future feature for dir() surface parity (``annotations``).
annotations = _future_mod.annotations
