"""Production factory stage executor backed by ``OrchestrationCommandService``.

Holds the standalone ``OrchestrationStageExecutor`` god-class extracted from
``factory_run_service``. Behavior is preserved verbatim: this package imports
the shared data-contracts and tuning constants from ``factory_run_models`` and
keeps all cross-cell edges lazy (in-function) exactly as before.

This package is the lossless successor of the former ``factory_stage_executor``
module. It re-exports every previously-public symbol from the same import path
so that ``import ...factory_stage_executor`` and
``from ...factory_stage_executor import X`` keep resolving identically for all
external importers.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import json
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import unicodedata
import uuid
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from polaris.cells.orchestration.orchestration_engine.public.service import OrchestrationCommandService

from polaris.cells.chief_engineer.blueprint.public import (
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerBlueprintPortfolioV1,
    ChiefEngineerPortfolioTaskV1,
    GenerateTaskBlueprintCommandV1,
    ProjectKindAuthorityV1,
    VerificationCommandAuthorityV1,
    build_chief_engineer_blueprint_portfolio,
    derive_project_kind_authority_from_catalog_snapshot,
    generate_task_blueprint,
    project_chief_engineer_task_blueprint,
    project_completion_catalog_snapshot_hash,
    project_completion_verifier_policy_snapshot_hash,
    validate_director_handoff_from_payload,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    _issue_chief_engineer_portfolio_authority_carrier,
)
from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.control_plane.verifier_policy.public import (
    CompileEvidencePolicyCommandV1,
    compile_evidence_policy,
)
from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FactoryRoleEvidenceAuthorityBindingV1,
    bind_factory_role_evidence_authority,
)
from polaris.cells.roles.kernel.public.service import QualityChecker
from polaris.cells.roles.kernel.public.structured_output_contracts import (
    RoleStructuredOutputContractV1,
)
from polaris.cells.roles.runtime.public.contracts import (
    ExecuteRoleTaskCommandV1,
    RoleExecutionResultV1,
)
from polaris.cells.roles.runtime.public.service import RoleRuntimeService
from polaris.cells.runtime.task_runtime.public import (
    BindRuntimeTaskToFactoryRunCommandV1,
    HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementOutcomeV1,
)
from polaris.cells.runtime.task_runtime.public.service import (
    TaskRuntimeService,
    bind_runtime_task_to_factory_run,
    heartbeat_task_runtime_execution_attempt,
)
from polaris.kernelone.constants import (
    MAX_LLM_PROVIDER_TIMEOUT_SECONDS,
)
from polaris.kernelone.events.final_request_evidence import canonical_role_final_request_json
from polaris.kernelone.fs import (
    GuardedRegularFileSnapshotError,
    KernelFileSystem,
    get_default_adapter,
    guarded_compare_and_replace_regular_file,
    read_guarded_regular_file_snapshot,
)
from polaris.kernelone.fs.text_ops import write_json_atomic
from polaris.kernelone.llm.budget_policy import (
    FACTORY_LLM_STAGE_MIN_START_BUDGET_SECONDS,
    chief_engineer_portfolio_output_tokens,
)
from polaris.kernelone.storage import resolve_storage_roots
from polaris.kernelone.tools.tool_kinds import WRITE_TOOLS

from .. import (
    factory_ce_evidence as ce_evidence,
    factory_deadline_calculations as deadline_calc,
    factory_director_dispatch_impl as director_dispatch_impl,
    factory_director_route_audit as route_audit,
    factory_materialization_impl as materialization_impl,
    factory_pm_contract_normalization as pm_contract_norm,
    factory_prompt_compaction as prompt_compaction,
    factory_stage_helpers as helpers,
    factory_target_file_summaries as target_summaries,
    factory_workspace_quality_evidence as wq_evidence,
    factory_workspace_quality_impl as workspace_quality_impl,
)
from ..factory_artifact_store import ArtifactStore
from ..factory_deadline_calculations import (
    _CHIEF_ENGINEER_EXECUTION_ATTEMPT_SETTLEMENT_GRACE_SECONDS,
    _CHIEF_ENGINEER_LLM_TIMEOUT_ENV_KEYS,
    _DEFAULT_CHIEF_ENGINEER_LLM_TIMEOUT_SECONDS,
    ChiefEngineerExecutionAttemptLeaseBudget as _ChiefEngineerExecutionAttemptLeaseBudget,
)
from ..factory_deadline_policy import (
    FactoryDeadlineAdmissionV1,
    FactoryDeadlineBudgetPolicyV1,
    FactoryDeadlineDispositionV1,
    TaskDependencyScheduleV1,
    build_task_dependency_schedule,
)
from ..factory_role_evidence_authority import (
    FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY,
    FactoryRoleEvidenceAuthorityPort,
)
from ..factory_run_completion import RunCompletionAuthority, RunCompletionWaiter
from ..factory_run_models import (
    _PM_ARCHITECT_DOC_MAX_CHARS,
    _PM_DIRECTIVE_MAX_CHARS,
    _PM_ORIGINAL_DIRECTIVE_MAX_CHARS,
    _WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS,
    FactoryRun,
    StageResult,
)
from ..factory_stage_artifact_bindings import (
    PM_STAGE_ARTIFACT_BINDING_CONTEXT_KEY,
    FactoryStageArtifactBindingError,
    RevalidatedPMStageArtifactBindingV1,
    parse_factory_stage_artifact_json,
    revalidate_pm_stage_artifact_binding,
)
from ..factory_stage_persistence import reduce_factory_stage_persistence
from ..factory_store import FactoryStore
from ..factory_workspace_quality import WorkspaceQualityRunner
from ..run_ledger import load_run_ledger_projection
from ._executor import OrchestrationStageExecutor
from ._helpers import (
    _CE_BLUEPRINT_OUTPUT_CONTRACT,
    _CHIEF_ENGINEER_MIN_LLM_START_BUDGET_SECONDS,
    _CHIEF_ENGINEER_PORTFOLIO_REASONING_BUDGET_TOKENS,
    _CHIEF_ENGINEER_SCHEMA_REPAIR_ERROR_MAX_CHARS,
    _CHIEF_ENGINEER_SCHEMA_REPAIR_MAX_TOKENS,
    _CHIEF_ENGINEER_SCHEMA_REPAIR_REASONING_BUDGET_TOKENS,
    _DEFAULT_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_COUNT,
    _DEFAULT_WORKSPACE_QUALITY_REPAIR_LLM_TIMEOUT_SECONDS,
    _DIRECTOR_BINDING_TIMEOUT_QUARANTINE_ENV,
    _DIRECTOR_DISPATCH_DEADLINE_SAFETY_SECONDS,
    _DIRECTOR_DISPATCH_TIMEOUT_GRACE_SECONDS,
    _DIRECTOR_FIRST_MATERIALIZATION_MIN_BUDGET_ENV,
    _DIRECTOR_FIRST_MATERIALIZATION_MIN_BUDGET_SECONDS,
    _DIRECTOR_PROVIDER_RATE_LIMIT_TOKENS,
    _DIRECTOR_PROVIDER_UNAVAILABLE_TOKENS,
    _DIRECTOR_SETTLEMENT_BARRIER_BUDGET_SECONDS,
    _DIRECTOR_TIMEOUT_ENV_KEYS,
    _FACTORY_WORKSPACE_RUN_LEASE_METADATA_KEY,
    _LANGUAGE_NEUTRAL_EXTENSIONS,
    _LANGUAGE_NEUTRAL_FILENAMES,
    _LANGUAGE_NEUTRAL_REPAIR_FILENAMES,
    _LANGUAGE_SOURCE_EXTENSIONS,
    _PM_PLAN_ARTIFACT_MAX_BYTES,
    _PRE_DIRECTOR_PLATFORM_PREFIXES,
    _PRE_DIRECTOR_SNAPSHOT_KIND,
    _PRE_DIRECTOR_SNAPSHOT_RELATIVE_DIR,
    _QUALITY_GATE_MIN_QA_START_BUDGET_SECONDS,
    _QUALITY_GATE_MIN_START_BUDGET_SECONDS,
    _QUALITY_GATE_QA_DEADLINE_SAFETY_SECONDS,
    _QUALITY_GATE_RESERVED_BUDGET_ENV,
    _QUALITY_GATE_RESERVED_BUDGET_SECONDS,
    _TASKBOARD_STATS_BASELINE_KEYS,
    _WORKSPACE_QUALITY_MUTATION_TOKENS,
    _WORKSPACE_QUALITY_REPAIR_LLM_TIMEOUT_ENV,
    _WORKSPACE_QUALITY_REPAIR_MAX_ROUNDS,
    _WORKSPACE_QUALITY_REPAIR_MIN_LLM_START_BUDGET_SECONDS,
    _WORKSPACE_QUALITY_REPAIR_SOURCE_SUFFIXES,
    _call_accepts_keyword,
    _ChiefEngineerExecutionAttemptHeartbeatFailure,
    _ChiefEngineerExecutionAttemptKeeperStopResult,
    _ChiefEngineerExecutionAttemptLeaseKeeper,
    _ChiefEngineerExecutionAttemptLeaseScope,
    _ChiefEngineerPortfolioAuthorityError,
    _ChiefEngineerPortfolioAuthorityV1,
    _dedupe_workspace_repair_paths,
    _empty_taskboard_stats,
    _is_workspace_quality_repair_path,
    _new_monotonic_deadline,
    _remaining_monotonic_seconds,
    _safe_taskboard_stat,
    _whole_wait_seconds,
    _workspace_quality_repair_external_task_id,
)

logger = logging.getLogger(__name__)

# Bind characterization-surface / TYPE_CHECKING symbols so they remain attributes.
_ = (
    TYPE_CHECKING,
    _issue_chief_engineer_portfolio_authority_carrier,
    _PM_ARCHITECT_DOC_MAX_CHARS,
    _PM_DIRECTIVE_MAX_CHARS,
    _PM_ORIGINAL_DIRECTIVE_MAX_CHARS,
    _WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS,
    dataclass,
    inspect,
    hashlib,
    math,
    unicodedata,
    uuid,
    deepcopy,
    cast,
    contextlib,
    asyncio,
    json,
    os,
    re,
    shutil,
    threading,
)
