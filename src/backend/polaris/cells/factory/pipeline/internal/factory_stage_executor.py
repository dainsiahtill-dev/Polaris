"""Production factory stage executor backed by ``OrchestrationCommandService``.

Holds the standalone ``OrchestrationStageExecutor`` god-class extracted from
``factory_run_service``. Behavior is preserved verbatim: this module imports
the shared data-contracts and tuning constants from ``factory_run_models`` and
keeps all cross-cell edges lazy (in-function) exactly as before.
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
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
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
from polaris.cells.director.runtime.public.contracts import DirectorInterfaceDiscrepancyReceiptV1
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
    DEFAULT_DIRECTOR_MAX_PARALLELISM,
    MAX_LLM_PROVIDER_TIMEOUT_SECONDS,  # noqa: F401 — re-exported for characterization-test surface
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

from . import (
    factory_ce_evidence as ce_evidence,
    factory_deadline_calculations as deadline_calc,
    factory_pm_contract_normalization as pm_contract_norm,
    factory_stage_helpers as helpers,
)
from .factory_artifact_store import ArtifactStore
from .factory_deadline_calculations import (  # noqa: F401 — re-exported for characterization-test surface
    _CHIEF_ENGINEER_EXECUTION_ATTEMPT_SETTLEMENT_GRACE_SECONDS,
    _CHIEF_ENGINEER_LLM_TIMEOUT_ENV_KEYS,
    _DEFAULT_CHIEF_ENGINEER_LLM_TIMEOUT_SECONDS,
    ChiefEngineerExecutionAttemptLeaseBudget as _ChiefEngineerExecutionAttemptLeaseBudget,
)
from .factory_deadline_policy import (
    FactoryDeadlineAdmissionV1,
    FactoryDeadlineBudgetPolicyV1,
    FactoryDeadlineDispositionV1,
    TaskDependencyScheduleV1,
    build_task_dependency_schedule,
)
from .factory_role_evidence_authority import (
    FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY,
    FactoryRoleEvidenceAuthorityPort,
)
from .factory_run_completion import RunCompletionAuthority, RunCompletionWaiter
from .factory_run_models import (
    _PM_ARCHITECT_DOC_MAX_CHARS,
    _PM_DIRECTIVE_MAX_CHARS,
    _PM_ORIGINAL_DIRECTIVE_MAX_CHARS,
    _WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS,
    _WORKSPACE_VALIDATION_TIMEOUT_SECONDS,
    FactoryRun,
    StageResult,
)
from .factory_stage_artifact_bindings import (
    PM_STAGE_ARTIFACT_BINDING_CONTEXT_KEY,
    FactoryStageArtifactBindingError,
    RevalidatedPMStageArtifactBindingV1,
    parse_factory_stage_artifact_json,
    revalidate_pm_stage_artifact_binding,
)
from .factory_stage_persistence import reduce_factory_stage_persistence
from .factory_store import FactoryStore
from .factory_workspace_quality import WorkspaceQualityRunner
from .run_ledger import load_run_ledger_projection

logger = logging.getLogger(__name__)

_CHIEF_ENGINEER_SCHEMA_REPAIR_MAX_TOKENS = 8_192
_CHIEF_ENGINEER_PORTFOLIO_REASONING_BUDGET_TOKENS = 4_096
_CHIEF_ENGINEER_SCHEMA_REPAIR_REASONING_BUDGET_TOKENS = 2_048
_CHIEF_ENGINEER_SCHEMA_REPAIR_ERROR_MAX_CHARS = 2_000

# Language-to-extension mapping for PM plan language consistency validation.
# Used to detect when the PM model plans files in the wrong language
# (e.g. Java files for a JavaScript project — context bleed from other projects).
_LANGUAGE_SOURCE_EXTENSIONS: dict[str, frozenset[str]] = {
    "javascript": frozenset({".js", ".mjs", ".cjs", ".jsx"}),
    "typescript": frozenset({".ts", ".tsx", ".mts", ".cts"}),
    "python": frozenset({".py"}),
    "rust": frozenset({".rs"}),
    "go": frozenset({".go"}),
    "java": frozenset({".java"}),
    "cpp": frozenset({".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hxx"}),
    "csharp": frozenset({".cs"}),
    "ruby": frozenset({".rb"}),
    "swift": frozenset({".swift"}),
    "kotlin": frozenset({".kt", ".kts"}),
    "scala": frozenset({".scala"}),
}
_WORKSPACE_QUALITY_MUTATION_TOKENS = WRITE_TOOLS | frozenset({"create_file", "text_replace"})
_FACTORY_WORKSPACE_RUN_LEASE_METADATA_KEY = "factory_workspace_run_lease"
_PM_PLAN_ARTIFACT_MAX_BYTES = 4 * 1024 * 1024


def _call_accepts_keyword(callable_obj: Any, keyword: str) -> bool:
    """Return whether ``callable_obj`` can accept a keyword argument.

    Factory tests and downstream adapters monkeypatch selected executor methods
    as extension seams.  New control-plane parameters must therefore be added
    behind signature detection instead of leaking directly into every override.

    Complexity:
        O(p) time and O(1) extra memory over the callable signature parameter
        count.
    """

    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return False
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == keyword and parameter.kind in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }:
            return True
    return False


def _new_monotonic_deadline(timeout_seconds: float) -> float:
    """Return one absolute deadline for a bounded Factory operation."""

    return asyncio.get_running_loop().time() + max(0.0, float(timeout_seconds))


def _remaining_monotonic_seconds(deadline: float) -> float:
    """Return non-negative wall time left in an absolute operation lease."""

    return max(0.0, float(deadline) - asyncio.get_running_loop().time())


def _whole_wait_seconds(deadline: float) -> int:
    """Return whole seconds safe to pass to an integer-timeout dependency."""

    remaining_seconds = _remaining_monotonic_seconds(deadline)
    return 0 if remaining_seconds <= 0 else math.ceil(remaining_seconds)


# Extensions that are language-agnostic and should not trigger a mismatch.
_LANGUAGE_NEUTRAL_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".md",
        ".txt",
        ".html",
        ".css",
        ".xml",
        ".csv",
        ".lock",
        # Build/QA/run helper scripts: auxiliary automation that legitimately
        # appears in projects of ANY primary language (e.g. a Go project's
        # scripts/qa.sh QA verifier). These are not "wrong language" source
        # files, so they must not trip the language-consistency mismatch guard.
        ".sh",
        ".bash",
        ".zsh",
        ".ps1",
        ".bat",
    }
)
_LANGUAGE_NEUTRAL_FILENAMES: frozenset[str] = frozenset(
    {
        "go.mod",
        "go.sum",
        "package.json",
        "requirements.txt",
        "pyproject.toml",
        "cmakelists.txt",
    }
)

_WORKSPACE_QUALITY_REPAIR_MAX_ROUNDS = 3
_WORKSPACE_QUALITY_REPAIR_LLM_TIMEOUT_ENV = "KERNELONE_WORKSPACE_QUALITY_REPAIR_LLM_TIMEOUT_SECONDS"
_DEFAULT_WORKSPACE_QUALITY_REPAIR_LLM_TIMEOUT_SECONDS = 90.0
# Shares the single budget_policy constant with the chief-engineer copy below.
# Bench r46 lowered the CE min start budget 45.0 -> 40.0; this sibling had
# silently kept 45.0 (EXECUTION_BUDGET_POLICY_BLUEPRINT_20260703 §1) — now both
# read the same 40.0 fact from one place.
_WORKSPACE_QUALITY_REPAIR_MIN_LLM_START_BUDGET_SECONDS = FACTORY_LLM_STAGE_MIN_START_BUDGET_SECONDS
_WORKSPACE_QUALITY_REPAIR_SOURCE_SUFFIXES = frozenset(
    {
        ".css",
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".go",
        ".h",
        ".hpp",
        ".html",
        ".java",
        ".js",
        ".jsx",
        ".json",
        ".md",
        ".py",
        ".rs",
        ".ts",
        ".tsx",
    }
)
_DIRECTOR_PROVIDER_RATE_LIMIT_TOKENS: tuple[str, ...] = (
    "429",
    "rate_limit",
    "rate limit",
    "rate-limited",
    "too many requests",
    "token plan",
    "quota",
    "用量上限",
)
_DIRECTOR_PROVIDER_UNAVAILABLE_TOKENS: tuple[str, ...] = (
    "provider_timeout",
    "request timeout",
    "transport timeout",
    "timed out",
    "circuit_open",
    "circuit breaker is open",
    "circuitopenerror",
)
_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_ENV = "KERNELONE_FACTORY_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_COUNT"
_DEFAULT_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_COUNT = 4
_DIRECTOR_DISPATCH_TIMEOUT_GRACE_SECONDS = 60
_DIRECTOR_DISPATCH_DEADLINE_SAFETY_SECONDS = 5
_DIRECTOR_SETTLEMENT_BARRIER_BUDGET_SECONDS = 5
_DIRECTOR_FIRST_MATERIALIZATION_MIN_BUDGET_ENV = "KERNELONE_FACTORY_DIRECTOR_FIRST_MATERIALIZATION_MIN_BUDGET_SECONDS"
_DIRECTOR_FIRST_MATERIALIZATION_MIN_BUDGET_SECONDS = 90.0
_QUALITY_GATE_RESERVED_BUDGET_ENV = "KERNELONE_FACTORY_QUALITY_GATE_RESERVED_BUDGET_SECONDS"
_QUALITY_GATE_RESERVED_BUDGET_SECONDS = 120.0
_QUALITY_GATE_MIN_START_BUDGET_SECONDS = 15.0
_QUALITY_GATE_MIN_QA_START_BUDGET_SECONDS = FACTORY_LLM_STAGE_MIN_START_BUDGET_SECONDS
_QUALITY_GATE_QA_DEADLINE_SAFETY_SECONDS = 5.0
# Bench r46 evidence: 45.0 -> 40.0; single-sourced in budget_policy together
# with the workspace-quality-repair sibling above.
_CHIEF_ENGINEER_MIN_LLM_START_BUDGET_SECONDS = FACTORY_LLM_STAGE_MIN_START_BUDGET_SECONDS
_PRE_DIRECTOR_SNAPSHOT_RELATIVE_DIR = ".polaris/factory_snapshots/pre_director"
_PRE_DIRECTOR_SNAPSHOT_KIND = "pre_director_workspace"
_PRE_DIRECTOR_PLATFORM_PREFIXES = (
    ".git/",
    ".polaris/",
    ".polaris.kernelone.tags.cache.v1/",
    "runtime/",
    "node_modules/",
)
_DIRECTOR_TIMEOUT_ENV_KEYS = (
    "KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS",
    "KERNELONE_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS",
    "KERNELONE_DIRECTOR_LLM_TIMEOUT_MAX_SECONDS",
)
# CE LLM timeout constants now live in factory_deadline_calculations; they are
# re-imported above so the characterization-test surface (stage_executor_module.X)
# continues to resolve.
_CE_BLUEPRINT_OUTPUT_CONTRACT = """

Chief Engineer output contract:
- Call submit_structured_role_output exactly once with the complete result object. This is a
  provider response protocol, not an executable workspace tool and not a side effect.
- Required top-level keys: construction_plan, project_completion_contract, scope_for_apply, risk_flags.
- construction_plan must describe one coherent project architecture, not isolated task answers.
- construction_plan.task_plans must be an object keyed by every PM task id. Each task plan must name
  concrete files, public interfaces, dependencies, implementation phases, and verification evidence.
- construction_plan.project_interface_contract must contain provider_declarations and
  consumer_declarations arrays. Declarations must identify repository-relative owner/consumer files,
  symbol names, symbol kinds, callable/type signatures where applicable, and semantic roles.
- scope_for_apply must be an array of repository-relative paths or modules. It is advisory only and
  cannot expand the PM-authoritative target_files/scope_paths.
- risk_flags must be an array, even when empty.
- project_completion_contract must describe completion for the ENTIRE validated PM task set, never one task.
  It must contain obligations.artifacts, obligations.entrypoints, and
  obligations.verification. Every PM target file must be a required artifact. Active artifact and entrypoint
  paths must be exact PM target paths or component-safe descendants of PM scope_paths, and owner_task_id must be
  the PM task that owns that target/scope. Every verification must name covers_obligation_ids and select one exact
  PM-owned command_authority_hash supplied in project_completion_authority; never invent or rewrite a command. Every active
  obligation must name exact owner_task_id from the validated PM task set; not_applicable obligations must use
  owner_task_id=null. The immutable project_kind_authority in project_completion_authority decides application versus
  library; never emit or override project_kind. Every project kind requires at least one required build/test/lint
  verifier. Applications require a required test artifact and test verifier; only libraries may mark both
  not_applicable. Every project kind declares environment_prep: applications require it; libraries may mark it
  not_applicable. Applications also require an
  executable entrypoint with source_path/runtime_path plus an authorized entrypoint verifier. Libraries must mark
  entrypoint not_applicable.
- Do not emit project_id, run_id, project_kind, project_kind_authority, pm_contract_hash, covered_task_ids,
  completion_predicate_version, or verifier_policy_hash. Factory injects those authority fields from committed PM,
  catalog, and verifier-policy evidence.
- Do not call any other tool or emit code patches, <SESSION_PATCH>, or file edit instructions.
"""


@dataclass(frozen=True, slots=True)
class _ChiefEngineerPortfolioAuthorityV1:
    """Factory-owned identities injected into one project completion contract."""

    project_id: str
    pm_stage_event_id: str
    pm_contract_hash: str
    pm_task_ids: tuple[str, ...]
    catalog_snapshot: Mapping[str, Any]
    catalog_snapshot_hash: str
    project_kind_authority: ProjectKindAuthorityV1
    verifier_policy_hash: str
    verifier_policy: Mapping[str, Any]
    verifier_policy_snapshot_hash: str
    verification_command_authority: tuple[VerificationCommandAuthorityV1, ...]


class _ChiefEngineerPortfolioAuthorityError(RuntimeError):
    """Stable pre-provider authority failure surfaced verbatim as a stage signal code."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(detail or code)


@dataclass(frozen=True, slots=True)
class _ChiefEngineerExecutionAttemptHeartbeatFailure:
    """One observable CE lease-keeper incident or unresolved failure."""

    reason: str
    error_type: str
    error_message: str


@dataclass(frozen=True, slots=True)
class _ChiefEngineerExecutionAttemptKeeperStopResult:
    """Stop result that gates terminal settlement on confirmed thread exit."""

    thread_exited: bool
    failure: _ChiefEngineerExecutionAttemptHeartbeatFailure | None


class _ChiefEngineerExecutionAttemptLeaseKeeper:
    """Threaded TaskRuntime heartbeat covering async and synchronous CE work."""

    def __init__(
        self,
        *,
        workspace: str,
        task_id: int,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        budget: _ChiefEngineerExecutionAttemptLeaseBudget,
    ) -> None:
        if (
            execution_attempt.workspace != workspace
            or execution_attempt.task_id != task_id
            or not execution_attempt.session_id
        ):
            raise ValueError("chief_engineer_execution_attempt_lease_identity_mismatch")
        self._workspace = workspace
        self._task_id = task_id
        self._execution_attempt = execution_attempt
        self._budget = budget
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state = "new"
        self._current_failure: _ChiefEngineerExecutionAttemptHeartbeatFailure | None = None
        self._incidents: list[_ChiefEngineerExecutionAttemptHeartbeatFailure] = []
        self._heartbeat_count = 0

    @property
    def failure(self) -> _ChiefEngineerExecutionAttemptHeartbeatFailure | None:
        with self._state_lock:
            return self._current_failure

    @property
    def incidents(self) -> tuple[_ChiefEngineerExecutionAttemptHeartbeatFailure, ...]:
        """Return every keeper incident without retaining stale failure authority."""

        with self._state_lock:
            return tuple(self._incidents)

    @property
    def heartbeat_count(self) -> int:
        with self._state_lock:
            return self._heartbeat_count

    @property
    def task_id(self) -> int:
        return self._task_id

    @property
    def execution_attempt(self) -> TaskRuntimeExecutionAttemptIdentityV1:
        with self._state_lock:
            return self._execution_attempt

    @property
    def is_alive(self) -> bool:
        with self._state_lock:
            thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """Start exactly one bounded heartbeat thread for this claim."""

        with self._state_lock:
            if self._state == "running":
                return
            if self._state == "stopping":
                raise RuntimeError("chief_engineer_execution_attempt_keeper_stopping")
            if self._state == "stopped":
                raise RuntimeError("chief_engineer_execution_attempt_keeper_cannot_restart")
            self._stop_event.clear()
            thread = threading.Thread(
                target=self._thread_entry,
                name=(f"polaris-ce-attempt-lease-{self._execution_attempt.task_id}-{self._execution_attempt.attempt}"),
                daemon=True,
            )
            self._thread = thread
            self._state = "running"
        try:
            thread.start()
        except BaseException as exc:
            self._record_failure(
                reason="heartbeat_thread_start_failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            with self._state_lock:
                self._state = "stopped"
            raise

    def stop(self) -> _ChiefEngineerExecutionAttemptKeeperStopResult:
        """Request exit and return only after bounded confirmation or failure."""

        with self._state_lock:
            thread = self._thread
            if self._state in {"new", "stopped"} or thread is None:
                return _ChiefEngineerExecutionAttemptKeeperStopResult(
                    thread_exited=True,
                    failure=self._current_failure,
                )
            self._state = "stopping"
            self._stop_event.set()
        if thread is threading.current_thread():
            self._record_failure(
                reason="heartbeat_thread_stop_from_self",
                error_type="RuntimeError",
                error_message="lease keeper cannot join its own thread",
            )
            return _ChiefEngineerExecutionAttemptKeeperStopResult(
                thread_exited=False,
                failure=self.failure,
            )
        try:
            thread.join(timeout=self._budget.heartbeat_interval_seconds)
        except BaseException as exc:  # noqa: BLE001 - keeper shutdown containment boundary
            self._record_failure(
                reason="heartbeat_thread_stop_exception",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return _ChiefEngineerExecutionAttemptKeeperStopResult(
                thread_exited=False,
                failure=self.failure,
            )
        if thread.is_alive():
            self._record_failure(
                reason="heartbeat_thread_stop_timeout",
                error_type="TimeoutError",
                error_message="lease keeper did not exit before bounded stop deadline",
            )
            return _ChiefEngineerExecutionAttemptKeeperStopResult(
                thread_exited=False,
                failure=self.failure,
            )
        with self._state_lock:
            self._state = "stopped"
            self._thread = None
            failure = self._current_failure
        return _ChiefEngineerExecutionAttemptKeeperStopResult(thread_exited=True, failure=failure)

    def _thread_entry(self) -> None:
        """Contain every thread-boundary failure as an auditable keeper incident."""

        try:
            self._run_loop()
        except BaseException as exc:  # noqa: BLE001 - thread entry containment boundary
            self._record_failure(
                reason="heartbeat_thread_boundary_exception",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        finally:
            with self._state_lock:
                self._state = "stopped"

    def _run_loop(self) -> None:
        while not self._stop_event.wait(self._budget.heartbeat_interval_seconds):
            try:
                result = heartbeat_task_runtime_execution_attempt(
                    HeartbeatTaskRuntimeExecutionAttemptCommandV1(
                        workspace=self._workspace,
                        identity=self.execution_attempt,
                        lease_ttl_seconds=self._budget.lease_ttl_seconds,
                        lock_timeout_seconds=self._budget.heartbeat_interval_seconds,
                        context_summary="chief_engineer_portfolio_review_in_progress",
                    )
                )
            except BaseException as exc:  # noqa: BLE001 - worker-call containment boundary
                self._record_failure(
                    reason="heartbeat_exception",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                break
            if not result.success:
                self._record_failure(
                    reason=result.reason,
                    error_type="TaskRuntimeHeartbeatRejected",
                    error_message=result.reason,
                )
                if result.reason in {
                    "workspace_mismatch",
                    "session_not_found",
                    "session_task_mismatch",
                    "session_mismatch",
                    "attempt_mismatch",
                    "role_mismatch",
                    "worker_mismatch",
                    "run_mismatch",
                    "external_task_id_mismatch",
                    "lease_version_mismatch",
                    "session_not_active",
                    "session_lease_expired",
                    "session_terminal_preserved",
                }:
                    break
                continue
            renewed_identity = result.renewed_identity
            if renewed_identity is None:
                self._record_failure(
                    reason="invalid_heartbeat_result",
                    error_type="TaskRuntimeHeartbeatRejected",
                    error_message="successful heartbeat omitted renewed_identity",
                )
                break
            with self._state_lock:
                self._heartbeat_count += 1
                self._execution_attempt = renewed_identity
                self._current_failure = None

    def _record_failure(self, *, reason: str, error_type: str, error_message: str) -> None:
        failure = _ChiefEngineerExecutionAttemptHeartbeatFailure(
            reason=reason,
            error_type=error_type,
            error_message=error_message,
        )
        with self._state_lock:
            self._incidents.append(failure)
            self._current_failure = failure
        logger.error(
            "Chief Engineer execution attempt heartbeat failed: "
            "code=chief_engineer.execution_attempt_heartbeat_failed "
            "workspace=%s run_id=%s task_id=%s session_id=%s attempt=%s "
            "reason=%s error_type=%s error=%s",
            self._workspace,
            self._execution_attempt.run_id,
            self._task_id,
            self._execution_attempt.session_id,
            self._execution_attempt.attempt,
            failure.reason,
            failure.error_type,
            failure.error_message,
        )


class _ChiefEngineerExecutionAttemptLeaseScope:
    """Claim-bound keeper state enforcing stop-before-settle and settle-once."""

    def __init__(self) -> None:
        self._state_lock = threading.Lock()
        self.task_id: int | None = None
        self.execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None
        self.keeper: _ChiefEngineerExecutionAttemptLeaseKeeper | None = None
        self.settlement_started = False

    def bind_claim(
        self,
        *,
        task_id: int,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
    ) -> None:
        with self._state_lock:
            if self.task_id is not None or self.execution_attempt is not None:
                raise RuntimeError("chief_engineer_execution_attempt_claim_already_bound")
            self.task_id = task_id
            self.execution_attempt = execution_attempt

    def start_keeper(self, keeper: _ChiefEngineerExecutionAttemptLeaseKeeper) -> None:
        with self._state_lock:
            if self.task_id is None or self.execution_attempt is None:
                raise RuntimeError("chief_engineer_execution_attempt_claim_not_bound")
            if self.keeper is not None:
                raise RuntimeError("chief_engineer_execution_attempt_keeper_already_started")
            self.keeper = keeper
        keeper.start()

    def stop_keeper(self) -> _ChiefEngineerExecutionAttemptKeeperStopResult:
        with self._state_lock:
            keeper = self.keeper
        if keeper is None:
            return _ChiefEngineerExecutionAttemptKeeperStopResult(thread_exited=True, failure=None)
        return keeper.stop()

    def begin_settlement(
        self,
    ) -> tuple[bool, _ChiefEngineerExecutionAttemptHeartbeatFailure | None]:
        with self._state_lock:
            if self.settlement_started:
                failure = self.keeper.failure if self.keeper is not None else None
                return False, failure
            self.settlement_started = True
        stop_result = self.stop_keeper()
        if not stop_result.thread_exited:
            return False, stop_result.failure
        with self._state_lock:
            if self.keeper is not None:
                # Full settlement identity includes the lease version.  The
                # keeper owns renewals, so settlement must consume its final
                # persisted identity after the bounded stop barrier.
                self.execution_attempt = self.keeper.execution_attempt
        return True, stop_result.failure


_TASKBOARD_STATS_BASELINE_KEYS: tuple[str, ...] = (
    "total",
    "pending",
    "ready",
    "in_progress",
    "in_design",
    "in_execution",
    "in_qa",
    "running",
    "processing",
    "executing",
    "waiting_human",
    "completed",
    "failed",
    "blocked",
)


def _empty_taskboard_stats() -> dict[str, int]:
    return dict.fromkeys(_TASKBOARD_STATS_BASELINE_KEYS, 0)


def _safe_taskboard_stat(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _dedupe_workspace_repair_paths(paths: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        normalized = os.path.normpath(str(raw_path or "").strip().replace("\\", "/")).replace("\\", "/")
        if not normalized or normalized == "." or normalized.startswith("../") or normalized.startswith("/"):
            continue
        if not _is_workspace_quality_repair_path(normalized):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _is_workspace_quality_repair_path(path: str) -> bool:
    normalized = os.path.normpath(str(path or "").strip().replace("\\", "/")).replace("\\", "/")
    if not normalized or normalized == "." or normalized.startswith("../") or normalized.startswith("/"):
        return False
    candidate = Path(normalized)
    return (
        candidate.suffix.lower() in _WORKSPACE_QUALITY_REPAIR_SOURCE_SUFFIXES
        or candidate.name.lower() in _LANGUAGE_NEUTRAL_FILENAMES
    )


_LANGUAGE_NEUTRAL_REPAIR_FILENAMES: tuple[str, ...] = (
    "go.mod",
    "go.sum",
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "CMakeLists.txt",
)


class OrchestrationStageExecutor:
    """Production executor backed by OrchestrationCommandService."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self._fs = KernelFileSystem(str(workspace), get_default_adapter())
        self._artifact_store = ArtifactStore(self.workspace, self._fs)
        self._workspace_quality = WorkspaceQualityRunner(self.workspace)
        self._run_completion_waiter = RunCompletionWaiter(self.workspace)
        self._binding_timeout_counts: dict[str, int] = {}
        self._quarantined_bindings: set[str] = set()
        self._last_director_binding_skips: list[dict[str, Any]] = []
        self._binding_status_probe_seconds = 2.0

    async def execute(self, stage: str, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        handlers = {
            "docs_generation": self._execute_docs_generation,
            "pm_planning": self._execute_pm_planning,
            "chief_engineer_review": self._execute_chief_engineer_review,
            "director_dispatch": self._execute_director_dispatch,
            "quality_gate": self._execute_quality_gate,
        }
        handler = handlers.get(stage)
        if handler is None:
            return StageResult(stage=stage, status="skipped", output="No handler for this stage")
        return await handler(run, context)

    @staticmethod
    def _factory_role_evidence_cutoff_port(context: Mapping[str, Any]) -> FactoryRoleEvidenceAuthorityPort:
        port = context.get(FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY)
        if type(port) is not FactoryRoleEvidenceAuthorityPort:
            raise RuntimeError("factory_role_evidence_live_cutoff_port_required")
        return port

    @staticmethod
    async def _call_with_factory_role_evidence_authority(
        authority_port: FactoryRoleEvidenceAuthorityPort,
        role: str,
        operation: Callable[[], Awaitable[Any]],
        *,
        authority_binding: FactoryRoleEvidenceAuthorityBindingV1 | None = None,
    ) -> Any:
        """Bind one role-task grant and revoke it if task creation raises.

        A bounded retry for the same controlled child run may reuse the
        caller-owned binding.  This preserves the fixed per-stage grant
        cardinality while every physical request still consumes the grant's
        aggregate attempt budget under a distinct request freeze.
        """

        binding = authority_binding or authority_port.mint_authority_binding(role)
        if binding.role != role or binding.cutoff_port is not authority_port:
            raise RuntimeError("factory_role_evidence_authority_binding_scope_mismatch")
        try:
            with bind_factory_role_evidence_authority(binding):
                return await operation()
        except BaseException:
            authority_port.revoke_authority_binding(binding)
            raise

    def _artifact_path(self, relative_path: str) -> Path:
        return self._artifact_store.artifact_path(relative_path)

    def _write_json_artifact(self, relative_path: str, payload: dict[str, Any]) -> Path:
        return self._artifact_store.write_json_artifact(relative_path, payload)

    def _write_text_artifact(self, relative_path: str, content: str) -> Path:
        return self._artifact_store.write_text_artifact(relative_path, content)

    def _write_stage_signal_artifact(
        self,
        *,
        stage: str,
        run_id: str,
        signals: list[dict[str, Any]],
    ) -> str:
        return self._artifact_store.write_stage_signal_artifact(stage=stage, run_id=run_id, signals=signals)

    def _copy_text_artifact(self, source_relative_path: str, target_relative_path: str) -> str:
        return self._artifact_store.copy_text_artifact(source_relative_path, target_relative_path)

    def _copy_text_artifact_if_present(
        self,
        source_relative_path: str,
        target_relative_path: str,
        *,
        min_chars: int = 1,
    ) -> str:
        return self._artifact_store.copy_text_artifact_if_present(
            source_relative_path, target_relative_path, min_chars=min_chars
        )

    def _read_text_artifact(self, relative_path: str, *, min_chars: int = 1) -> str:
        return self._artifact_store.read_text_artifact(relative_path, min_chars=min_chars)

    def _emit_audit_event(self, event_type: str, **kwargs: Any) -> None:
        """Emit an audit event for tracking purposes."""
        self._artifact_store.emit_audit_event(event_type, **kwargs)

    @staticmethod
    def _extend_artifacts(artifacts: list[str], *paths: str) -> None:
        helpers.extend_artifacts(artifacts, *paths)

    @staticmethod
    def _normalize_declared_delivery_target(value: Any) -> str:
        return helpers.normalize_declared_delivery_target(value)

    @classmethod
    def _collect_declared_delivery_targets(cls, tasks: list[dict[str, Any]]) -> list[str]:
        return helpers.collect_declared_delivery_targets(tasks)

    def _missing_declared_delivery_targets(self, tasks: list[dict[str, Any]]) -> list[str]:
        workspace_root = self.workspace.resolve()
        missing: list[str] = []
        for target in self._collect_declared_delivery_targets(tasks):
            try:
                path = (workspace_root / target).resolve()
                path.relative_to(workspace_root)
                target_exists = path.exists()
            except (OSError, RuntimeError, ValueError):
                missing.append(target)
                continue
            if not target_exists:
                missing.append(target)
                continue
            if path.is_file():
                try:
                    if path.stat().st_size <= 0:
                        missing.append(target)
                except OSError:
                    missing.append(target)
        return missing

    def _mirror_docs_artifacts(self, run_id: str, artifacts: list[str]) -> None:
        self._artifact_store.mirror_docs_artifacts(run_id, artifacts)

    def _mirror_pm_plan_artifacts(self, run_id: str, artifacts: list[str]) -> None:
        self._artifact_store.mirror_pm_plan_artifacts(run_id, artifacts)

    def _mirror_chief_engineer_artifacts(
        self,
        run_id: str,
        blueprint_rows: list[dict[str, Any]],
        review_artifact: str,
        artifacts: list[str],
    ) -> None:
        self._artifact_store.mirror_chief_engineer_artifacts(run_id, blueprint_rows, review_artifact, artifacts)

    def _mirror_director_artifacts(self, run_id: str, artifacts: list[str]) -> None:
        self._artifact_store.mirror_director_artifacts(run_id, artifacts)

    def _mirror_quality_gate_artifacts(self, run_id: str, artifacts: list[str]) -> None:
        self._artifact_store.mirror_quality_gate_artifacts(run_id, artifacts)

    def _workspace_package_has_external_dependencies(self) -> bool:
        return self._workspace_quality.workspace_package_has_external_dependencies()

    def _workspace_quality_prepare_commands(
        self,
        commands: list[list[str]],
        context: dict[str, Any],
    ) -> list[list[str]]:
        return self._workspace_quality.workspace_quality_prepare_commands(commands, context)

    @staticmethod
    def _artifact_file_ready(target: Path) -> bool:
        """Return whether an expected stage artifact is present after upstream completion."""
        return helpers.artifact_file_ready(target)

    @staticmethod
    def _pre_director_snapshot_candidate(relative_path: str) -> bool:
        normalized = relative_path.replace("\\", "/").strip("/")
        if not normalized:
            return False
        if normalized in {".git", ".polaris", "runtime", "node_modules"}:
            return False
        parts = normalized.split("/")
        if any(part in {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"} for part in parts):
            return False
        if normalized.endswith((".pyc", ".pyo")):
            return False
        return not any(normalized.startswith(prefix) for prefix in _PRE_DIRECTOR_PLATFORM_PREFIXES)

    def _pre_director_snapshot_dir(self) -> Path:
        return self.workspace / _PRE_DIRECTOR_SNAPSHOT_RELATIVE_DIR

    def _iter_pre_director_snapshot_files(self) -> list[Path]:
        files: list[Path] = []
        for path in self.workspace.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                relative = path.relative_to(self.workspace).as_posix()
            except ValueError:
                continue
            if self._pre_director_snapshot_candidate(relative):
                files.append(path)
        return sorted(files)

    def _create_pre_director_snapshot(self, *, run_id: str) -> dict[str, Any]:
        snapshot_dir = self._pre_director_snapshot_dir()
        files_dir = snapshot_dir / "files"
        if snapshot_dir.exists():
            shutil.rmtree(snapshot_dir, ignore_errors=True)
        files_dir.mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, Any]] = []
        for source in self._iter_pre_director_snapshot_files():
            relative = source.relative_to(self.workspace).as_posix()
            target = files_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            entries.append({"path": relative, "size": source.stat().st_size})
        manifest = {
            "snapshot_kind": _PRE_DIRECTOR_SNAPSHOT_KIND,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "factory_run_id": str(run_id or "").strip(),
            "file_count": len(entries),
            "files": entries,
            "platform_excluded_prefixes": list(_PRE_DIRECTOR_PLATFORM_PREFIXES),
        }
        write_json_atomic(str(snapshot_dir / "manifest.json"), manifest)
        return manifest

    def _restore_pre_director_snapshot(self) -> dict[str, Any]:
        snapshot_dir = self._pre_director_snapshot_dir()
        manifest_path = snapshot_dir / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("pre-Director workspace snapshot is missing or invalid") from exc
        if not isinstance(manifest, dict) or manifest.get("snapshot_kind") != _PRE_DIRECTOR_SNAPSHOT_KIND:
            raise RuntimeError("pre-Director workspace snapshot manifest has invalid kind")
        entries_raw = manifest.get("files")
        entries = [item for item in entries_raw if isinstance(item, dict)] if isinstance(entries_raw, list) else []
        expected_paths = {
            str(item.get("path") or "").replace("\\", "/").strip("/")
            for item in entries
            if str(item.get("path") or "").strip()
        }

        removed: list[str] = []
        restored: list[str] = []
        for current in self._iter_pre_director_snapshot_files():
            relative = current.relative_to(self.workspace).as_posix()
            if relative not in expected_paths:
                current.unlink(missing_ok=True)
                removed.append(relative)

        files_dir = snapshot_dir / "files"
        for relative in sorted(expected_paths):
            source = files_dir / relative
            if not source.is_file():
                raise RuntimeError(f"pre-Director snapshot content missing for {relative}")
            target = self.workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            restored.append(relative)

        for directory in sorted(
            [path for path in self.workspace.rglob("*") if path.is_dir()],
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            try:
                relative = directory.relative_to(self.workspace).as_posix().strip("/")
            except ValueError:
                continue
            if not relative or not self._pre_director_snapshot_candidate(f"{relative}/placeholder"):
                continue
            with contextlib.suppress(OSError):
                directory.rmdir()

        return {
            "snapshot_kind": _PRE_DIRECTOR_SNAPSHOT_KIND,
            "removed_files": removed,
            "restored_files": restored,
            "file_count": len(restored),
            "snapshot_created_at": manifest.get("created_at"),
        }

    def _capture_workspace_delivery_state(self) -> dict[str, tuple[int, int]]:
        state: dict[str, tuple[int, int]] = {}
        for path in self._iter_pre_director_snapshot_files():
            try:
                relative = path.relative_to(self.workspace).as_posix()
                stat_result = path.stat()
            except OSError:
                continue
            state[relative] = (int(stat_result.st_size), int(stat_result.st_mtime_ns))
        return state

    @staticmethod
    def _workspace_delivery_delta(
        before: dict[str, tuple[int, int]],
        after: dict[str, tuple[int, int]],
        *,
        max_samples: int = 12,
    ) -> dict[str, Any]:
        before_paths = set(before)
        after_paths = set(after)
        added = sorted(after_paths - before_paths)
        deleted = sorted(before_paths - after_paths)
        changed = sorted(path for path in before_paths & after_paths if before[path] != after[path])
        return {
            "added_count": len(added),
            "changed_count": len(changed),
            "deleted_count": len(deleted),
            "delta_file_count": len(added) + len(changed),
            "added_sample": added[:max_samples],
            "changed_sample": changed[:max_samples],
            "deleted_sample": deleted[:max_samples],
        }

    @staticmethod
    def _workspace_delta_indicates_materialization_progress(delta: dict[str, Any]) -> bool:
        try:
            added = int(delta.get("added_count") or 0)
            changed = int(delta.get("changed_count") or 0)
        except (TypeError, ValueError):
            return False
        return (added + changed) > 0

    def _artifact_exists(self, relative_path: str, *, min_chars: int = 1) -> bool:
        target = self._artifact_path(relative_path)
        if not target.exists() or not target.is_file():
            return False
        if min_chars <= 0:
            return True
        try:
            return len(target.read_text(encoding="utf-8").strip()) >= min_chars
        except OSError:
            return False

    def _missing_artifacts(self, artifacts: list[str], *, min_chars: int = 1) -> list[str]:
        return [item for item in artifacts if not self._artifact_exists(item, min_chars=min_chars)]

    @staticmethod
    def _is_substantive_doc_text(text: str, *, min_chars: int = 200) -> bool:
        return helpers.is_substantive_doc_text(text, min_chars=min_chars)

    def _ensure_docs_artifacts(
        self,
        *,
        directive: str,
        summary: str,
    ) -> list[str]:
        expected = ["docs/plan.md", "docs/architecture.md"]
        missing = self._missing_artifacts(expected, min_chars=120)
        if not missing:
            return []

        design_path = self._artifact_path("docs/design.md")
        design_text = ""
        if design_path.exists() and design_path.is_file():
            try:
                design_text = design_path.read_text(encoding="utf-8").strip()
            except OSError:
                design_text = ""
        if design_text and not self._is_substantive_doc_text(design_text):
            design_text = ""

        for rel in list(missing):
            if self._artifact_exists(rel, min_chars=120):
                continue
            if design_text:
                header = "# 项目计划\n" if rel.endswith("plan.md") else "# 架构设计\n"
                self._write_text_artifact(
                    rel,
                    "\n".join(
                        [
                            header,
                            "",
                            f"来源: docs/design.md ({datetime.now(timezone.utc).isoformat()})",
                            "",
                            design_text,
                            "",
                        ]
                    ),
                )
        return self._missing_artifacts(expected, min_chars=120)

    def _validate_pm_plan_contract(self, relative_path: str = "tasks/plan.json") -> str:
        target = self._artifact_path(relative_path)
        if not target.exists():
            return "missing_tasks_plan"
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return "tasks_plan_invalid_json"
        if not isinstance(payload, dict):
            return "tasks_plan_invalid_type"
        tasks = payload.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            return "tasks_plan_empty_tasks"
        invalid = 0
        meta_diagnostic = 0
        for item in tasks:
            if not isinstance(item, dict):
                invalid += 1
                continue
            goal = str(item.get("goal") or item.get("title") or "").strip()
            scope = str(item.get("scope") or "").strip()
            steps = item.get("steps")
            acceptance = item.get("acceptance") or item.get("acceptance_criteria")
            has_steps = isinstance(steps, list) and len([s for s in steps if str(s).strip()]) > 0
            has_acceptance = isinstance(acceptance, list) and len([s for s in acceptance if str(s).strip()]) > 0
            if not (goal and scope and has_steps and has_acceptance):
                invalid += 1
            if self._is_pm_meta_diagnostic_task(item):
                meta_diagnostic += 1
        if invalid > 0:
            return f"tasks_plan_invalid_contract:{invalid}"
        if meta_diagnostic > 0:
            return f"tasks_plan_meta_diagnostic_tasks:{meta_diagnostic}"
        return ""

    @staticmethod
    def _is_pm_meta_diagnostic_task(task: dict[str, Any]) -> bool:
        return helpers.is_pm_meta_diagnostic_task(task)

    def _validate_pm_plan_language_consistency(self, relative_path: str = "tasks/plan.json") -> str:
        """Check that PM plan target_files match the catalog primary_language.

        Detects context bleed where the PM model plans files in the wrong
        language (e.g. ``.java`` files for a ``javascript`` project).
        Returns an empty string when consistent, or a diagnostic message.
        """
        catalog_path = self.workspace / ".polaris" / "catalog_contract.json"
        if not catalog_path.exists():
            return ""
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return ""
        primary_language = str(catalog.get("primary_language") or "").strip().lower()
        if not primary_language:
            return ""
        expected_extensions = _LANGUAGE_SOURCE_EXTENSIONS.get(primary_language)
        if not expected_extensions:
            return ""
        tasks = self._load_pm_plan_tasks(relative_path)
        if not tasks:
            return ""
        wrong_lang_files: list[str] = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            target_files = task.get("target_files")
            if not isinstance(target_files, list):
                continue
            for file_path in target_files:
                if not isinstance(file_path, str):
                    continue
                normalized = file_path.replace("\\", "/")
                filename = Path(normalized).name.lower()
                if filename in _LANGUAGE_NEUTRAL_FILENAMES:
                    continue
                ext = Path(normalized).suffix.lower()
                if ext in _LANGUAGE_NEUTRAL_EXTENSIONS or not ext:
                    continue
                # Bench injects tests/test_product.py as a validation script;
                # it is not project source code, so skip test directories.
                if normalized.startswith("tests/") or "/tests/" in normalized:
                    continue
                if ext not in expected_extensions:
                    wrong_lang_files.append(file_path)
        if not wrong_lang_files:
            return ""
        sample = wrong_lang_files[:5]
        return (
            f"pm_plan_language_mismatch: catalog primary_language={primary_language!r} "
            f"but {len(wrong_lang_files)} target_files use wrong extensions "
            f"(e.g. {sample}). "
            f"PM likely confused this project with a different language project."
        )

    def _read_catalog_contract(self) -> dict[str, Any]:
        return pm_contract_norm.read_catalog_contract(self.workspace)

    @staticmethod
    def _catalog_delivery_depth_contract(catalog: dict[str, Any]) -> dict[str, Any]:
        return pm_contract_norm.catalog_delivery_depth_contract(catalog)

    @staticmethod
    def _merge_string_list(*values: Any) -> list[str]:
        return pm_contract_norm.merge_string_list(*values)

    @staticmethod
    def _merge_catalog_delivery_depth_contract(
        existing: dict[str, Any],
        catalog_contract: dict[str, Any],
    ) -> dict[str, Any]:
        return pm_contract_norm.merge_catalog_delivery_depth_contract(existing, catalog_contract)

    def _inject_catalog_delivery_depth_contract(self, context: dict[str, Any]) -> None:
        pm_contract_norm.inject_catalog_delivery_depth_contract(
            context,
            self._read_catalog_contract(),
        )

    @staticmethod
    def _normalize_contract_path(value: Any) -> str:
        return pm_contract_norm.normalize_contract_path(value)

    @classmethod
    def _source_target_suffixes(cls) -> frozenset[str]:
        return pm_contract_norm.source_target_suffixes()

    @classmethod
    def _collect_pm_project_declared_target_files(cls, tasks: list[dict[str, Any]]) -> list[str]:
        """Collect write targets from PM task contracts.

        ``target_files`` is the write/materialization surface. ``context_files``
        remains read-only evidence and must not be promoted into this union.
        """

        return pm_contract_norm.collect_pm_project_declared_target_files(tasks)

    @classmethod
    def _filter_source_target_files(cls, paths: list[str]) -> list[str]:
        return pm_contract_norm.filter_source_target_files(paths)

    @staticmethod
    def _filter_entrypoint_like_targets(paths: list[str]) -> list[str]:
        return pm_contract_norm.filter_entrypoint_like_targets(paths)

    def _inject_project_declared_target_contract(
        self,
        context: dict[str, Any],
        *,
        project_declared_target_files: list[str],
    ) -> None:
        pm_contract_norm.inject_project_declared_target_contract(
            context,
            project_declared_target_files=project_declared_target_files,
        )

    def _enrich_pm_plan_contract_artifact(self, relative_path: str = "tasks/plan.json") -> dict[str, Any]:
        target = self._artifact_path(relative_path)
        if not target.exists() or not target.is_file():
            return {"changed": False, "task_count": 0, "declared_target_count": 0}
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError, UnicodeDecodeError):
            return {"changed": False, "task_count": 0, "declared_target_count": 0}
        if not isinstance(payload, dict):
            return {"changed": False, "task_count": 0, "declared_target_count": 0}
        raw_tasks = payload.get("tasks")
        if not isinstance(raw_tasks, list):
            return {"changed": False, "task_count": 0, "declared_target_count": 0}

        task_rows = [dict(item) for item in raw_tasks if isinstance(item, dict)]
        project_declared_targets = self._collect_pm_project_declared_target_files(task_rows)
        changed = False
        enriched_tasks: list[Any] = []
        dict_index = 0
        for item in raw_tasks:
            if not isinstance(item, dict):
                enriched_tasks.append(item)
                continue
            task = dict(task_rows[dict_index])
            dict_index += 1
            before = json.dumps(task, sort_keys=True, ensure_ascii=False)
            self._inject_catalog_delivery_depth_contract(task)
            self._inject_project_declared_target_contract(
                task,
                project_declared_target_files=project_declared_targets,
            )
            after = json.dumps(task, sort_keys=True, ensure_ascii=False)
            if before != after:
                changed = True
            enriched_tasks.append(task)

        if changed:
            updated_payload = dict(payload)
            updated_payload["tasks"] = enriched_tasks
            self._write_json_artifact(relative_path, updated_payload)

        return {
            "changed": changed,
            "task_count": len(task_rows),
            "declared_target_count": len(project_declared_targets),
            "source_target_count": len(self._filter_source_target_files(project_declared_targets)),
        }

    def _load_pm_plan_tasks(
        self,
        relative_path: str = "tasks/plan.json",
        *,
        include_mirrors: bool = True,
    ) -> list[dict[str, Any]]:
        candidates = [self._artifact_path(relative_path)]
        if include_mirrors and relative_path == "tasks/plan.json":
            candidates.extend(self._iter_pm_plan_contract_candidates())

        seen: set[str] = set()
        for target in candidates:
            key = target.resolve().as_posix()
            if key in seen:
                continue
            seen.add(key)
            if not target.exists():
                continue
            try:
                payload = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError, ValueError, UnicodeDecodeError):
                continue
            tasks = self._pm_plan_tasks_from_payload(payload)
            if tasks:
                return tasks
        return []

    def _persist_normalized_pm_plan_validation_contracts(
        self,
        relative_path: str = "tasks/plan.json",
    ) -> dict[str, Any]:
        """Persist the exact normalized PM tasks consumed by CE provenance.

        Normalization was historically applied only by the in-memory loader,
        so the CE context could differ from the immutable ``tasks/plan.json``
        later bound by Factory.  Persisting first makes repeated loads
        idempotent and gives PM binding/CE ``pm_task_contract`` one exact fact.
        """

        if relative_path != "tasks/plan.json":
            raise FactoryStageArtifactBindingError(
                "factory_stage_artifact_pm_plan_path_invalid",
                "PM validation-contract normalization only accepts tasks/plan.json",
            )

        runtime_root = resolve_storage_roots(str(self.workspace)).runtime_root
        try:
            source_snapshot = read_guarded_regular_file_snapshot(
                str(runtime_root),
                relative_path,
                _PM_PLAN_ARTIFACT_MAX_BYTES,
            )
        except GuardedRegularFileSnapshotError as exc:
            if exc.code == "guarded_snapshot_missing":
                return {"changed": False, "task_count": 0}
            raise

        payload = parse_factory_stage_artifact_json(source_snapshot.content)
        raw_tasks = payload.get("tasks")
        if type(raw_tasks) is not list:
            raise FactoryStageArtifactBindingError(
                "factory_stage_artifact_pm_tasks_invalid",
                "PM plan tasks must be an exact JSON list before normalization",
            )
        if any(type(item) is not dict for item in raw_tasks):
            raise FactoryStageArtifactBindingError(
                "factory_stage_artifact_pm_task_invalid",
                "Every PM plan task must be an exact JSON object before normalization",
            )

        task_rows = [deepcopy(item) for item in raw_tasks]
        normalized = self._normalize_pm_plan_validation_contracts(task_rows)
        changed = normalized != raw_tasks
        if changed:
            updated = deepcopy(payload)
            updated["tasks"] = normalized
            replacement = (canonical_role_final_request_json(updated) + "\n").encode("utf-8")
            committed_snapshot = guarded_compare_and_replace_regular_file(
                str(runtime_root),
                source_snapshot,
                replacement,
                max_bytes=_PM_PLAN_ARTIFACT_MAX_BYTES,
            )
            reread_snapshot = read_guarded_regular_file_snapshot(
                str(runtime_root),
                relative_path,
                _PM_PLAN_ARTIFACT_MAX_BYTES,
            )
            if (
                reread_snapshot.content != replacement
                or reread_snapshot.content != committed_snapshot.content
                or reread_snapshot.size != committed_snapshot.size
                or reread_snapshot.device != committed_snapshot.device
                or reread_snapshot.inode != committed_snapshot.inode
            ):
                raise FactoryStageArtifactBindingError(
                    "factory_stage_artifact_pm_plan_postread_mismatch",
                    "PM plan changed after guarded normalization commit",
                )
            reread_payload = parse_factory_stage_artifact_json(reread_snapshot.content)
            if reread_payload.get("tasks") != normalized:
                raise FactoryStageArtifactBindingError(
                    "factory_stage_artifact_pm_plan_postread_mismatch",
                    "Strict PM plan reread does not contain the normalized task vector",
                )
        return {"changed": changed, "task_count": len(normalized)}

    @staticmethod
    def _pm_plan_tasks_from_payload(payload: Any) -> list[dict[str, Any]]:
        return pm_contract_norm.pm_plan_tasks_from_payload(payload)

    _PM_TEST_COMMAND_RE = pm_contract_norm._PM_TEST_COMMAND_RE
    _PM_NON_TEST_COMMAND_RE = pm_contract_norm._PM_NON_TEST_COMMAND_RE

    @staticmethod
    def _normalize_pm_plan_validation_contracts(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep per-task test acceptance aligned with the task that owns test targets."""

        return pm_contract_norm.normalize_pm_plan_validation_contracts(tasks)

    @staticmethod
    def _is_pm_validation_target_path(path: str) -> bool:
        return pm_contract_norm.is_pm_validation_target_path(path)

    @staticmethod
    def _acceptance_without_test_commands(acceptance: list[str]) -> tuple[list[str], list[str]]:
        return pm_contract_norm.acceptance_without_test_commands(acceptance)

    def _iter_pm_plan_contract_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        latest_plan = self.workspace / ".polaris" / "plans" / "latest.plan.json"
        candidates.append(latest_plan)

        for pattern in (".polaris/plans/*.plan.json", ".polaris/roles/pm/*/plan.json"):
            candidates.extend(self.workspace.glob(pattern))

        deduped: dict[str, Path] = {}
        for candidate in candidates:
            deduped[candidate.resolve().as_posix()] = candidate

        def _mtime(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0.0

        return sorted(deduped.values(), key=_mtime, reverse=True)

    def _ensure_pm_plan_contract_available(self) -> str:
        """Copy PM's workspace mirror into the runtime artifact path consumed downstream."""

        if self._load_pm_plan_tasks("tasks/plan.json", include_mirrors=False):
            return ""

        for candidate in self._iter_pm_plan_contract_candidates():
            if not candidate.exists() or not candidate.is_file():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError, ValueError, UnicodeDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            tasks = payload.get("tasks")
            if not isinstance(tasks, list) or not any(isinstance(item, dict) for item in tasks):
                continue
            self._write_json_artifact("tasks/plan.json", payload)
            try:
                return candidate.relative_to(self.workspace).as_posix()
            except ValueError:
                return candidate.as_posix()
        return ""

    def _materialize_pm_plan_taskboard(
        self,
        tasks: list[dict[str, Any]],
        *,
        run_id: str,
        source_stage: str,
        run_metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not tasks:
            return {"ensured_count": 0, "created_count": 0, "task_ids": []}

        service = TaskRuntimeService(str(self.workspace))
        task_ids: list[str] = []
        created_count = 0
        bound_count = 0
        binding_failures: list[dict[str, str]] = []
        for index, task in enumerate(tasks, start=1):
            task_id = self._task_id(task, index)
            if not task_id:
                continue
            existing = service.get_task(task_id)
            metadata_raw = task.get("metadata")
            metadata: dict[str, Any] = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
            metadata.pop(_FACTORY_WORKSPACE_RUN_LEASE_METADATA_KEY, None)
            metadata.update(
                {
                    "external_task_id": task_id,
                    "pm_task_id": task_id,
                    "source_task_id": task_id,
                    "task_index": index,
                    "factory_run_id": str(run_id or "").strip(),
                    "factory_stage": str(source_stage or "").strip(),
                    "source_artifact": "tasks/plan.json",
                    "task_contract": dict(task),
                }
            )
            lease_task_metadata = self._factory_workspace_run_lease_task_metadata(run_metadata)
            metadata.update(lease_task_metadata)
            for key in ("scope", "target_files", "acceptance", "acceptance_criteria", "steps", "depends_on"):
                if key in task:
                    metadata.setdefault(key, task.get(key))
            description_parts = [
                self._task_string(task, "description"),
                "\n".join(self._task_string_list(task, "steps")),
                "\n".join(self._task_string_list(task, "acceptance", "acceptance_criteria")),
            ]
            description = "\n\n".join(part for part in description_parts if part.strip())
            ensured_row = service.ensure_task_row(
                external_task_id=task_id,
                subject=self._task_objective(task),
                description=description,
                metadata=metadata,
                priority=task.get("priority", index),
            )
            binding_result = bind_runtime_task_to_factory_run(
                BindRuntimeTaskToFactoryRunCommandV1(
                    workspace=str(self.workspace),
                    task_id=task_id,
                    factory_run_id=str(run_id or "").strip(),
                )
            )
            if binding_result.ok:
                bound_count += 1
                if existing is not None and lease_task_metadata:
                    existing_metadata_raw = existing.get("metadata")
                    existing_metadata = existing_metadata_raw if isinstance(existing_metadata_raw, Mapping) else {}
                    projected_lease = lease_task_metadata[_FACTORY_WORKSPACE_RUN_LEASE_METADATA_KEY]
                    if existing_metadata.get(_FACTORY_WORKSPACE_RUN_LEASE_METADATA_KEY) != projected_lease:
                        refreshed_row = service.update_task_row(
                            ensured_row.get("id"),
                            metadata=lease_task_metadata,
                        )
                        if refreshed_row is None:
                            binding_failures.append(
                                {
                                    "task_id": task_id,
                                    "code": "factory_workspace_run_lease_projection_failed",
                                    "reason": "TaskRuntime could not refresh Factory lease provenance",
                                    "existing_factory_run_id": str(run_id or "").strip(),
                                }
                            )
            else:
                binding_failures.append(
                    {
                        "task_id": task_id,
                        "code": binding_result.code,
                        "reason": binding_result.reason,
                        "existing_factory_run_id": binding_result.existing_factory_run_id,
                    }
                )
            if existing is None:
                created_count += 1
            task_ids.append(task_id)

        return {
            "ensured_count": len(task_ids),
            "created_count": created_count,
            "bound_count": bound_count,
            "binding_failures": binding_failures,
            "task_ids": task_ids,
        }

    @staticmethod
    def _factory_workspace_run_lease_task_metadata(
        run_metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Detach Factory-owned workspace authority for TaskRuntime facts.

        The lease remains owned by the Factory run/admission ledger. Task
        metadata carries an immutable-at-materialization projection so a later
        TaskRuntime terminal fact can identify the fencing authority used by
        its Factory run. PM task metadata with the same key is deliberately
        discarded by the caller and can never mint this projection.

        Complexity:
            O(n) time and memory over the lease metadata payload.
        """

        if not isinstance(run_metadata, Mapping):
            return {}
        lease_raw = run_metadata.get(_FACTORY_WORKSPACE_RUN_LEASE_METADATA_KEY)
        if not isinstance(lease_raw, Mapping):
            return {}
        lease_projection: dict[str, Any] = {str(key): deepcopy(value) for key, value in lease_raw.items()}
        if not lease_projection:
            return {}
        return {_FACTORY_WORKSPACE_RUN_LEASE_METADATA_KEY: lease_projection}

    @staticmethod
    def _compact_text_for_prompt(text: str, *, max_chars: int) -> str:
        return helpers.compact_text_for_prompt(text, max_chars=max_chars)

    @staticmethod
    def _compact_workspace_quality_evidence_for_qa(text: str) -> str:
        """Build a short, parseable workspace-quality JSON payload for QA."""

        try:
            payload = json.loads(str(text or ""))
        except (json.JSONDecodeError, TypeError, ValueError):
            return helpers.compact_text_for_prompt(str(text or ""), max_chars=6000)
        if not isinstance(payload, dict):
            return helpers.compact_text_for_prompt(str(text or ""), max_chars=6000)

        commands: list[dict[str, Any]] = []
        for item in list(payload.get("commands") or []):
            if not isinstance(item, dict):
                continue
            command = item.get("command")
            if isinstance(command, list):
                command_value: list[str] | str = [str(part) for part in command]
            else:
                command_value = str(command or "")
            row: dict[str, Any] = {
                "command": command_value,
                "phase": str(item.get("phase") or ""),
                "passed": bool(item.get("passed")),
                "exit_code": item.get("exit_code"),
            }
            stdout_tail = str(item.get("stdout_tail") or "").strip()
            stderr_tail = str(item.get("stderr_tail") or "").strip()
            if stdout_tail:
                row["stdout_tail"] = helpers.compact_text_for_prompt(stdout_tail, max_chars=700)
            if stderr_tail:
                row["stderr_tail"] = helpers.compact_text_for_prompt(stderr_tail, max_chars=700)
            commands.append(row)

        repair = payload.get("repair") if isinstance(payload.get("repair"), dict) else {}
        compact_payload: dict[str, Any] = {
            "schema_version": payload.get("schema_version"),
            "source": payload.get("source"),
            "factory_run_id": payload.get("factory_run_id"),
            "workspace": payload.get("workspace"),
            "passed": bool(payload.get("passed")),
            "commands": commands,
        }
        if isinstance(repair, dict) and repair:
            compact_payload["repair"] = {
                "attempted": bool(repair.get("attempted")),
                "success": bool(repair.get("success")),
                "source_tools": [str(item) for item in list(repair.get("source_tools") or [])[:6]],
                "evidence": [
                    helpers.compact_text_for_prompt(str(item or ""), max_chars=220)
                    for item in list(repair.get("evidence") or [])[:6]
                    if str(item or "").strip()
                ],
            }
        return json.dumps(compact_payload, ensure_ascii=False, indent=2)

    @staticmethod
    def _compact_blueprint_evidence_for_repair(text: str) -> str:
        try:
            payload = json.loads(str(text or ""))
        except (json.JSONDecodeError, TypeError, ValueError):
            return helpers.compact_text_for_prompt(str(text or ""), max_chars=6000)
        if not isinstance(payload, dict):
            return helpers.compact_text_for_prompt(str(text or ""), max_chars=6000)

        blueprints: list[dict[str, Any]] = []
        for item in list(payload.get("blueprints") or [])[:12]:
            if not isinstance(item, dict):
                continue
            compact_item: dict[str, Any] = {}
            for key in ("task_id", "status", "blueprint_id", "blueprint_path", "summary", "recommendations", "risks"):
                value = item.get(key)
                if value not in (None, "", [], {}):
                    compact_item[key] = value
            if compact_item:
                blueprints.append(compact_item)

        compact_payload: dict[str, Any] = {
            "schema_version": "factory.chief_engineer_review.evidence.v1",
            "generated_blueprints": int(payload.get("generated_blueprints") or len(blueprints)),
            "total_tasks": int(payload.get("total_tasks") or len(blueprints)),
            "blueprints": blueprints,
        }
        signals = [
            {
                key: item.get(key)
                for key in ("code", "severity", "detail", "task_id")
                if isinstance(item, dict) and item.get(key) not in (None, "", [], {})
            }
            for item in list(payload.get("signals") or [])[:8]
            if isinstance(item, dict)
        ]
        if signals:
            compact_payload["signals"] = signals
        return json.dumps(compact_payload, ensure_ascii=False, indent=2)

    @staticmethod
    def _strip_prompt_meta_lines(text: str) -> str:
        return helpers.strip_prompt_meta_lines(text)

    def _build_pm_planning_directive(self, raw_directive: Any) -> str:
        user_directive = self._strip_prompt_meta_lines(str(raw_directive or "").strip())
        sections = [
            "请基于 Architect 阶段产物生成 PM 执行任务合同。任务必须覆盖需求、实现、验证、QA 闭环；"
            "每个任务必须包含 goal、scope、steps、acceptance、depends_on，并能交给 Director 直接执行。"
        ]
        for rel_path, label in (
            ("docs/plan.md", "Architect Plan"),
            ("docs/architecture.md", "Architect Architecture"),
            ("docs/design.md", "Architect Design"),
        ):
            doc_text = self._read_text_artifact(rel_path, min_chars=120)
            if not doc_text:
                continue
            sections.extend(
                [
                    "",
                    f"## {label}",
                    self._compact_text_for_prompt(doc_text, max_chars=_PM_ARCHITECT_DOC_MAX_CHARS),
                ]
            )
        if user_directive:
            sections.extend(
                [
                    "",
                    "## Original Requirement Excerpt",
                    self._compact_text_for_prompt(user_directive, max_chars=_PM_ORIGINAL_DIRECTIVE_MAX_CHARS),
                ]
            )
        compacted = "\n".join(sections).strip()
        return self._compact_text_for_prompt(compacted, max_chars=_PM_DIRECTIVE_MAX_CHARS)

    def _build_director_task_filter(self, tasks: list[dict[str, Any]]) -> str:
        return helpers.build_director_task_filter(tasks)

    def _director_task_ids_from_pm_tasks(self, tasks: list[dict[str, Any]]) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for index, task in enumerate(tasks, start=1):
            task_id = self._task_id(task, index)
            if not task_id or task_id in seen:
                continue
            seen.add(task_id)
            ids.append(task_id)
        return ids

    def _director_requested_task_ids(self, context: dict[str, Any], pm_tasks: list[dict[str, Any]]) -> list[str] | None:
        explicit_tasks = context.get("tasks")
        if isinstance(explicit_tasks, list):
            ids: list[str] = []
            seen: set[str] = set()
            for index, item in enumerate(explicit_tasks, start=1):
                task_id = self._task_id(item, index) if isinstance(item, dict) else str(item or "").strip()
                if not task_id or task_id in seen:
                    continue
                seen.add(task_id)
                ids.append(task_id)
            return ids
        return self._director_task_ids_from_pm_tasks(pm_tasks) or None

    def _read_json_artifact_payload(self, relative_path: str) -> dict[str, Any]:
        target = self._artifact_path(relative_path)
        if not target.exists() or not target.is_file():
            return {}
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError, UnicodeDecodeError):
            return {}
        return dict(payload) if isinstance(payload, dict) else {}

    def _load_chief_engineer_review_payload(self, *, run_id: str = "") -> dict[str, Any]:
        resolved_run_id = str(run_id or "").strip()
        if not resolved_run_id:
            return {}
        payload = self._read_json_artifact_payload(f"runtime/state/blueprints/{resolved_run_id}.review.json")
        if not payload:
            return {}
        payload_run_id = str(payload.get("factory_run_id") or "").strip()
        if payload_run_id and payload_run_id != resolved_run_id:
            return {}
        return payload

    def _chief_engineer_handoff_signals_for_director(
        self,
        pm_tasks: list[dict[str, Any]],
        *,
        run_id: str = "",
    ) -> list[dict[str, Any]]:
        """Validate PM task contracts have handoff-ready CE blueprints."""

        expected_task_ids = [self._task_id(task, index) for index, task in enumerate(pm_tasks, start=1)]
        expected_task_ids = [task_id for task_id in expected_task_ids if task_id]
        if not expected_task_ids:
            return []

        review_payload = self._load_chief_engineer_review_payload(run_id=run_id)
        raw_rows = review_payload.get("blueprints") if isinstance(review_payload, dict) else None
        rows = [dict(item) for item in raw_rows if isinstance(item, dict)] if isinstance(raw_rows, list) else []

        rows_by_task: dict[str, dict[str, Any]] = {}
        for row in rows:
            task_id = str(row.get("task_id") or "").strip()
            if task_id:
                rows_by_task[task_id] = row

        signals: list[dict[str, Any]] = []
        if not rows_by_task:
            return [
                {
                    "code": "director.chief_engineer_handoff_missing",
                    "severity": "error",
                    "detail": "Director dispatch requires Chief Engineer review evidence before execution.",
                    "expected_task_ids": expected_task_ids,
                    "review_artifact_found": bool(review_payload),
                }
            ]

        for task_id in expected_task_ids:
            blueprint_row = rows_by_task.get(task_id)
            if blueprint_row is None:
                signals.append(
                    {
                        "code": "director.chief_engineer_blueprint_missing_for_task",
                        "severity": "error",
                        "detail": "No Chief Engineer blueprint row was found for PM task before Director dispatch.",
                        "task_id": task_id,
                    }
                )
                continue

            blueprint_id = str(blueprint_row.get("blueprint_id") or "").strip()
            if not blueprint_id:
                signals.append(
                    {
                        "code": "director.chief_engineer_blueprint_id_missing",
                        "severity": "error",
                        "detail": "Chief Engineer blueprint row is missing blueprint_id.",
                        "task_id": task_id,
                    }
                )
                continue

            validation = validate_director_handoff_from_payload(
                str(self.workspace),
                {"task_id": task_id, "blueprint_id": blueprint_id},
                require_strict=True,
            )
            handoff_payload_raw = validation.get("decision_payload")
            handoff_payload: dict[str, Any] = handoff_payload_raw if isinstance(handoff_payload_raw, dict) else {}
            if not validation.get("allowed") and not handoff_payload:
                signals.append(
                    {
                        "code": "director.chief_engineer_blueprint_unreadable",
                        "severity": "error",
                        "detail": str(
                            validation.get("reason")
                            or "Chief Engineer blueprint could not be loaded for handoff validation."
                        ),
                        "task_id": task_id,
                        "blueprint_id": blueprint_id,
                        "handoff_validation": validation,
                    }
                )
                continue
            if not validation.get("allowed"):
                signals.append(
                    {
                        "code": "director.chief_engineer_handoff_blocked",
                        "severity": "error",
                        "detail": str(validation.get("reason") or "Chief Engineer handoff blocked Director dispatch."),
                        "task_id": task_id,
                        "blueprint_id": blueprint_id,
                        "blockers": list(handoff_payload.get("blockers") or []),
                        "handoff_decision": handoff_payload,
                        "handoff_validation": validation,
                    }
                )
        return signals

    @staticmethod
    def _task_string(task: dict[str, Any], *keys: str) -> str:
        return helpers.task_string(task, *keys)

    @staticmethod
    def _task_string_list(task: dict[str, Any], *keys: str) -> list[str]:
        return helpers.task_string_list(task, *keys)

    def _task_id(self, task: dict[str, Any], index: int) -> str:
        return self._task_string(task, "id", "task_id", "uid") or f"task-{index}"

    def _task_objective(self, task: dict[str, Any]) -> str:
        return (
            self._task_string(task, "goal", "objective", "title", "subject", "description")
            or "Prepare Director implementation blueprint"
        )

    def _task_blueprint_context(self, task: dict[str, Any], *, run_id: str, index: int) -> dict[str, Any]:
        context = deepcopy(task)
        # Preserve the validated PM task as a named evidence slot. Flattened
        # task fields are useful prompt material, but they are not a provenance
        # reference and cannot satisfy final-request contract coverage.
        context["pm_task_contract"] = deepcopy(task)
        context["source_artifact"] = "tasks/plan.json"
        context["factory_run_id"] = run_id
        context["task_index"] = index
        title = self._task_string(task, "title", "subject", "goal")
        if title:
            context["task_title"] = title
        scope = self._task_string(task, "scope")
        if scope:
            context.setdefault("scope_paths", [scope])
        self._inject_catalog_delivery_depth_contract(context)
        # Inject existing target file contents so the CE blueprint (and Director)
        # can see the actual API of files created by earlier tasks. Without this,
        # test-generation tasks guess at class/function names and produce broken tests.
        existing_file_context = self._read_existing_target_file_summaries(task)
        if existing_file_context:
            context["existing_target_files"] = existing_file_context
        return context

    _EXISTING_SUMMARY_SOURCE_SUFFIXES = (".py", ".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx")
    _EXISTING_SUMMARY_MAX_FILES = 24

    def _read_existing_target_file_summaries(
        self, task: dict[str, Any], *, max_chars_per_file: int = 1500
    ) -> list[dict[str, str]]:
        """Summarize the export API of files this task depends on but does NOT own.

        A later task (e.g. the one writing ``main.py``) imports symbols from files
        an earlier task already created (e.g. ``src/models/mood.py``). Those
        dependency files are NOT in this task's own ``target_files``, so the
        Director would otherwise have to guess their API — and guessing wrong is
        exactly how ``main.py`` ended up calling ``Mood(mood=..., intensity=...)``
        on an ``enum`` (live L1-03: cross-file coherence break, entrypoint smoke
        TypeError). We therefore scan the workspace for already-existing source
        files OUTSIDE this task's targets and inject their compact export
        signatures so the Director's imports stay coherent with reality.

        The task's own existing targets are also summarized (harmless re-edit
        context); both sets are returned, de-duplicated, capped, and path-sorted
        for deterministic context.
        """
        own_targets: set[str] = set()
        raw_targets = task.get("target_files")
        if isinstance(raw_targets, list):
            for item in raw_targets:
                if isinstance(item, str) and item.strip():
                    own_targets.add(item.strip().replace("\\", "/").lstrip("./"))

        # Collect candidate relative paths: existing own targets first, then any
        # other existing workspace source file (the dependency surface).
        candidates: list[str] = []
        seen: set[str] = set()

        def _add(rel: str) -> None:
            norm = rel.replace("\\", "/")
            if norm and norm not in seen:
                seen.add(norm)
                candidates.append(norm)

        for rel in sorted(own_targets):
            if (self.workspace / rel).is_file():
                _add(rel)

        workspace_root = self.workspace.resolve()
        if workspace_root.is_dir():
            for suffix in self._EXISTING_SUMMARY_SOURCE_SUFFIXES:
                for full_path in sorted(workspace_root.rglob(f"*{suffix}")):
                    if not full_path.is_file():
                        continue
                    parts = set(full_path.relative_to(workspace_root).parts)
                    if parts & {".polaris", "runtime", "node_modules", "__pycache__", ".git", "dist", "build"}:
                        continue
                    try:
                        rel = str(full_path.relative_to(workspace_root))
                    except ValueError:
                        continue
                    norm = rel.replace("\\", "/")
                    if norm in own_targets:
                        continue  # the task is (re)writing this; not a frozen dependency
                    _add(rel)
                    if len(candidates) >= self._EXISTING_SUMMARY_MAX_FILES:
                        break
                if len(candidates) >= self._EXISTING_SUMMARY_MAX_FILES:
                    break

        summaries: list[dict[str, str]] = []
        for rel_path in candidates[: self._EXISTING_SUMMARY_MAX_FILES]:
            full_path = self.workspace / rel_path
            if not full_path.is_file():
                continue
            try:
                content = full_path.read_text(encoding="utf-8")
            except OSError:
                continue
            if not content.strip():
                continue
            suffix = full_path.suffix.lower()
            if suffix in (".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx"):
                summary = self._extract_js_export_summary(content)
            elif suffix == ".py":
                summary = self._extract_py_export_summary(content)
            else:
                summary = content[:max_chars_per_file]
            summaries.append({"path": rel_path, "exports": summary})
        return summaries

    @staticmethod
    def _extract_js_export_summary(content: str) -> str:
        """Extract JS/TS export signatures so dependent files reference real symbols.

        Captures classes, functions, const/let/var, TS enums (with members),
        interfaces, types, ``export { ... }`` lists, and CommonJS exports. Mirrors
        the Python extractor's enum-member coverage: a dependent TS file's Director
        must see enum members (e.g. ``SkyCondition.CALM``), not just the enum name,
        or it invents non-existent members — the cross-file coherence wall L4-L8
        React/Express projects hit.
        """
        import re as _re

        lines: list[str] = []

        # TS enums (incl. ``const enum``) with their members — the JS analog of the
        # Python enum-member gap. ``[^{}]`` spans newlines, so multi-line bodies match.
        for match in _re.finditer(r"(?:export\s+)?(?:const\s+)?enum\s+([A-Za-z_$][\w$]*)\s*\{([^{}]*)\}", content):
            name = match.group(1)
            members: list[str] = []
            seen_member: set[str] = set()
            for member in _re.findall(r"([A-Za-z_$][\w$]*)\s*(?==|,|\Z)", match.group(2)):
                if member not in seen_member:
                    seen_member.add(member)
                    members.append(member)
            lines.append(f"enum {name} {{ {', '.join(members[:40])} }}" if members else f"enum {name}")

        for raw_line in content.split("\n"):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
                continue
            if (
                _re.match(r"module\.exports\s*=", stripped)
                or _re.match(r"exports\.[A-Za-z_$]", stripped)
                or _re.match(r"(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+[A-Za-z_$]", stripped)
                or _re.match(r"(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*[A-Za-z_$]", stripped)
                or _re.match(r"(?:export\s+)?(?:const|let|var)\s+(?!enum\b)[A-Za-z_$]", stripped)
                or _re.match(r"(?:export\s+)?interface\s+[A-Za-z_$]", stripped)
                or _re.match(r"(?:export\s+)?type\s+[A-Za-z_$][\w$]*\s*=", stripped)
                or _re.match(r"export\s+\{", stripped)
                or _re.match(r"export\s+default\s+", stripped)
            ):
                lines.append(stripped[:200])

        # Dedupe preserving order (an enum's declaration line can also appear above).
        deduped: list[str] = []
        seen_line: set[str] = set()
        for line in lines:
            if line not in seen_line:
                seen_line.add(line)
                deduped.append(line)
        if not deduped:
            for raw_line in content.split("\n"):
                if raw_line.strip():
                    deduped.append(raw_line.strip()[:200])
                if len(deduped) >= 30:
                    break
        return "\n".join(deduped[:60])

    @staticmethod
    def _extract_py_export_summary(content: str) -> str:
        """Extract Python export signatures so a dependent file's Director sees the
        *valid* cross-file symbols, not just declaration names.

        Includes enum members and class attributes alongside class/function
        signatures. Without enum members, the Director receives only
        ``class SkyCondition(Enum):`` and guesses non-existent members like
        ``SkyCondition.CLEAR`` — the factory-bench L1-03 entrypoint crash
        (``AttributeError: type object 'SkyCondition' has no attribute 'CLEAR'``).
        Falls back to a line scan when the source does not parse.
        """
        import ast as _ast

        try:
            tree = _ast.parse(content)
        except (SyntaxError, ValueError):
            return OrchestrationStageExecutor._extract_py_export_summary_fallback(content)

        enum_bases = {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "ReprEnum"}
        lines: list[str] = []

        def _base_names(class_node: _ast.ClassDef) -> list[str]:
            names: list[str] = []
            for base in class_node.bases:
                if isinstance(base, _ast.Name):
                    names.append(base.id)
                elif isinstance(base, _ast.Attribute):
                    names.append(base.attr)
            return names

        def _func_signature(fn: _ast.FunctionDef | _ast.AsyncFunctionDef) -> str:
            params: list[str] = [a.arg for a in fn.args.posonlyargs] + [a.arg for a in fn.args.args]
            if fn.args.vararg is not None:
                params.append("*" + fn.args.vararg.arg)
            params.extend(a.arg for a in fn.args.kwonlyargs)
            if fn.args.kwarg is not None:
                params.append("**" + fn.args.kwarg.arg)
            keyword = "async def" if isinstance(fn, _ast.AsyncFunctionDef) else "def"
            return f"{keyword} {fn.name}({', '.join(params)})"

        for node in tree.body:
            if isinstance(node, _ast.ClassDef):
                bases = _base_names(node)
                header = f"class {node.name}({', '.join(bases)}):" if bases else f"class {node.name}:"
                is_enum = any(base in enum_bases for base in bases)
                members: list[str] = []
                methods: list[str] = []
                for item in node.body:
                    if isinstance(item, _ast.Assign):
                        members.extend(tgt.id for tgt in item.targets if isinstance(tgt, _ast.Name))
                    elif isinstance(item, _ast.AnnAssign) and isinstance(item.target, _ast.Name):
                        members.append(item.target.id)
                    elif isinstance(item, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                        methods.append(item.name)
                if is_enum and members:
                    lines.append(f"{header} members: {', '.join(members[:40])}")
                else:
                    detail: list[str] = []
                    if members:
                        detail.append("attrs: " + ", ".join(members[:24]))
                    if methods:
                        detail.append("methods: " + ", ".join(methods[:24]))
                    lines.append(f"{header} {' | '.join(detail)}" if detail else header)
            elif isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                lines.append(_func_signature(node))
            elif isinstance(node, _ast.Assign):
                lines.extend(
                    f"{tgt.id} = ..." for tgt in node.targets if isinstance(tgt, _ast.Name) and tgt.id.isupper()
                )

        if not lines:
            return OrchestrationStageExecutor._extract_py_export_summary_fallback(content)
        return "\n".join(lines[:60])

    @staticmethod
    def _extract_py_export_summary_fallback(content: str) -> str:
        """Line-scan fallback when the dependency source does not parse as Python."""
        import re as _re

        lines: list[str] = []
        for raw_line in content.split("\n"):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if _re.match(r"(?:class|def|async def)\s+\w+", stripped):
                lines.append(stripped[:200])
        if not lines:
            for raw_line in content.split("\n"):
                if raw_line.strip():
                    lines.append(raw_line.strip()[:200])
                if len(lines) >= 30:
                    break
        return "\n".join(lines[:50])

    def _task_blueprint_constraints(self, task: dict[str, Any]) -> dict[str, Any]:
        constraints: dict[str, Any] = {}
        acceptance = self._task_string_list(task, "acceptance", "acceptance_criteria")
        steps = self._task_string_list(task, "steps")
        scope = self._task_string(task, "scope")
        if acceptance:
            constraints["acceptance"] = acceptance
        if steps:
            constraints["steps"] = steps
        if scope:
            constraints["scope"] = scope
        return constraints

    def _read_taskboard_stats(self) -> dict[str, int]:
        try:
            payload = TaskRuntimeService(str(self.workspace)).get_observable_task_row_stats()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            logger.debug("Failed to read observable task stats for factory taskboard projection", exc_info=True)
            return _empty_taskboard_stats()
        if not isinstance(payload, dict):
            return _empty_taskboard_stats()
        stats = _empty_taskboard_stats()
        for key, value in payload.items():
            stats[str(key)] = _safe_taskboard_stat(value)
        return stats

    def _query_observable_task_rows(
        self,
        *,
        factory_run_id: str = "",
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """Return authoritative rows or one typed fail-closed diagnostic.

        Degraded transitional rows remain available to UI/diagnostic consumers
        through TaskRuntime, but Factory stage decisions fail closed instead of
        allowing file fallback to authorize execution or verification.
        """

        try:
            projection = TaskRuntimeService(str(self.workspace)).query_observable_task_rows_projection()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            return [], {
                "code": "director.task_runtime_fact_projection_unavailable",
                "severity": "error",
                "detail": (f"TaskRuntime fact-only observable projection is unavailable: {type(exc).__name__}: {exc}"),
                "failure_class": FailureClassV1.LEDGER_PROJECTION_INCOMPLETE.value,
                "responsible_layer": "task_runtime",
                "repairable_by_director": False,
            }
        if (
            projection.authoritative is not True
            or projection.degraded
            or projection.source != "task_runtime.execution_fact"
            or projection.readiness.get("ready") is not True
        ):
            readiness = dict(projection.readiness)
            return [], {
                "code": "director.task_runtime_fact_projection_not_ready",
                "severity": "error",
                "detail": (
                    "TaskRuntime fact-only observable projection is not ready: "
                    f"source={projection.source}; readiness={readiness}"
                ),
                "failure_class": FailureClassV1.LEDGER_PROJECTION_INCOMPLETE.value,
                "responsible_layer": "task_runtime",
                "repairable_by_director": False,
                "projection_source": projection.source,
                "projection_readiness": readiness,
            }
        rows = projection.rows_for_factory_run(factory_run_id) if str(factory_run_id or "").strip() else projection.rows
        return [dict(row) for row in rows if isinstance(row, Mapping)], None

    def _read_observable_task_rows(self, *, factory_run_id: str = "") -> list[dict[str, Any]]:
        """Return only authoritative TaskRuntime fact-projected rows."""

        rows, failure = self._query_observable_task_rows(factory_run_id=factory_run_id)
        if failure is not None:
            logger.warning("Factory rejected TaskRuntime projection: %s", failure)
        return rows

    def _read_claimable_director_task_ids(self, *, limit: int, factory_run_id: str = "") -> list[str]:
        """Return TaskBoard PM/external ids that can be claimed in this round."""
        if limit <= 0:
            return []
        rows = self._read_observable_task_rows(factory_run_id=factory_run_id)

        ids: list[str] = []
        seen: set[str] = set()
        for row in rows:
            if self._is_internal_chief_engineer_task_row(
                row,
                factory_run_id=factory_run_id,
            ):
                continue
            status = str(row.get("status") or "").strip().lower()
            if status not in {"pending", "ready"}:
                continue
            blocked_by = row.get("blocked_by") if isinstance(row.get("blocked_by"), list) else row.get("blockedBy")
            if blocked_by:
                continue
            task_id = self._task_projection_external_id(row)
            if not task_id or task_id in seen:
                continue
            seen.add(task_id)
            ids.append(task_id)
            if len(ids) >= limit:
                break
        return ids

    @staticmethod
    def _task_projection_external_id(row: Mapping[str, Any]) -> str:
        """Return the PM identity represented by one TaskRuntime projection.

        TaskRuntime owns a numeric storage identity while PM dependency graphs
        use stable external task ids.  Every Factory consumer must resolve the
        same identity precedence or its projections can disagree about which
        task is claimable, unresolved, or admitted by the deadline policy.
        """

        metadata_raw = row.get("metadata")
        metadata: Mapping[str, Any] = metadata_raw if isinstance(metadata_raw, Mapping) else {}
        return str(
            metadata.get("external_task_id")
            or metadata.get("pm_task_id")
            or metadata.get("source_task_id")
            or metadata.get("task_id")
            or row.get("task_id")
            or row.get("id")
            or ""
        ).strip()

    @staticmethod
    def _director_dependency_settle_grace_seconds(
        context: dict[str, Any],
    ) -> float:
        """Return the bounded grace for dependency-unblock fact propagation."""

        raw_value = context.get("director_dependency_settle_grace_seconds")
        try:
            parsed = float(raw_value) if raw_value is not None else 2.0
        except (TypeError, ValueError):
            parsed = 2.0
        return max(0.0, min(parsed, 10.0))

    async def _wait_for_claimable_director_tasks(
        self,
        *,
        limit: int,
        grace_seconds: float,
        factory_run_id: str = "",
    ) -> tuple[list[str], dict[str, int]]:
        """Wait briefly for completion-triggered dependency facts to settle.

        The wait is read-only and bounded. A newly claimable task causes the
        caller to start a fresh dispatch round so deadline admission is
        recalculated. No task state is inferred or mutated here.

        Complexity:
            O(p * r) time and O(r) memory for ``p`` projection polls over ``r``
            observable task rows.
        """

        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, grace_seconds)
        latest_stats = self._read_taskboard_stats()
        while True:
            task_ids = self._read_claimable_director_task_ids(
                limit=limit,
                factory_run_id=factory_run_id,
            )
            latest_stats = self._read_taskboard_stats()
            if task_ids or self._is_taskboard_converged(latest_stats):
                return task_ids, latest_stats
            remaining = deadline - loop.time()
            if remaining <= 0:
                return [], latest_stats
            await asyncio.sleep(min(0.1, remaining))

    @staticmethod
    def _remaining_director_task_count(stats: dict[str, int], *, fallback: int) -> int:
        """Return unresolved PM task owners from the observable projection."""

        total = max(0, _safe_taskboard_stat(stats.get("total")))
        terminal = sum(_safe_taskboard_stat(stats.get(key)) for key in ("completed", "failed", "cancelled"))
        if total > 0:
            return max(1, total - terminal)
        return max(1, int(fallback))

    @staticmethod
    def _is_taskboard_converged(stats: dict[str, int]) -> bool:
        return helpers.is_taskboard_converged(stats)

    @staticmethod
    def _taskboard_has_active_execution(stats: Mapping[str, Any]) -> bool:
        """Whether authoritative TaskRuntime facts prove a child is still active.

        The orchestration lifecycle may publish a non-success terminal result
        before the TaskRuntime-owned execution row reaches its terminal fact.
        In that interval the lifecycle progress marker can be absent even though
        the child is physically executing.  TaskRuntime is the canonical task
        authority, so any active row must preserve the already-admitted Director
        execution lease instead of collapsing the wait to the 5s settlement
        reserve.
        """

        return any(
            _safe_taskboard_stat(stats.get(key)) > 0
            for key in (
                "in_progress",
                "in_design",
                "in_execution",
                "in_qa",
                "running",
                "processing",
                "executing",
            )
        )

    @staticmethod
    def _has_director_progress(before: dict[str, int], after: dict[str, int]) -> bool:
        return helpers.has_director_progress(before, after)

    @staticmethod
    def _pm_deterministic_contract_metadata_for_context(
        run: FactoryRun,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Build PM run metadata for explicit/internal deterministic contract mode."""
        metadata_sources: list[dict[str, Any]] = []
        if isinstance(context, dict):
            context_metadata = context.get("metadata")
            if isinstance(context_metadata, dict):
                metadata_sources.append(context_metadata)
            metadata_sources.append(context)
        run_metadata = run.metadata if isinstance(run.metadata, dict) else {}
        start_request = run_metadata.get("factory_start_request")
        if isinstance(start_request, dict):
            start_metadata = start_request.get("metadata")
            if isinstance(start_metadata, dict):
                metadata_sources.append(start_metadata)

        explicit_deterministic = any(
            str(source.get("deterministic_pm_contracts") or "").strip().lower() in {"1", "true", "yes", "on"}
            for source in metadata_sources
        )
        bench_metadata = next(
            (source for source in metadata_sources if str(source.get("factory_bench_project_id") or "").strip()),
            {},
        )
        if not explicit_deterministic and not bench_metadata:
            return {}

        result: dict[str, Any] = {"deterministic_pm_contracts": True}
        if bench_metadata:
            result.update(
                {
                    "factory_bench_project_id": str(bench_metadata.get("factory_bench_project_id") or "").strip(),
                    "factory_bench_level": bench_metadata.get("factory_bench_level"),
                    "factory_bench_deterministic_pm": True,
                    "pm_route_audit_probe": True,
                    "factory_recovery": "bench_preemptive_deterministic_contracts",
                }
            )
        else:
            result["factory_recovery"] = "explicit_deterministic_contracts"
        return result

    @staticmethod
    def _director_dispatch_timeout_seconds(
        context: dict[str, Any],
        *,
        task_count: int,
        materialization_pending: bool = False,
    ) -> int:
        return deadline_calc.director_dispatch_timeout_seconds(
            context,
            task_count=task_count,
            materialization_pending=materialization_pending,
        )

    @staticmethod
    def _factory_deadline_budget_policy(
        context: dict[str, Any],
        *,
        chief_engineer_generation_floor_seconds: float = 0.0,
    ) -> FactoryDeadlineBudgetPolicyV1:
        """Resolve infrastructure configuration into the pure deadline policy."""

        return deadline_calc.factory_deadline_budget_policy(
            context,
            chief_engineer_generation_floor_seconds=chief_engineer_generation_floor_seconds,
            director_first_task_min_seconds=(
                OrchestrationStageExecutor._director_first_materialization_min_budget_seconds(context)
            ),
            quality_gate_reserved_seconds=OrchestrationStageExecutor._quality_gate_reserved_budget_seconds(context),
            director_settlement_barrier_seconds=(
                OrchestrationStageExecutor._director_dispatch_timeout_settle_grace_seconds(context)
            ),
        )

    @staticmethod
    def _unresolved_task_ids_from_rows(rows: list[dict[str, Any]]) -> tuple[str, ...]:
        """Return non-terminal task identifiers from authoritative projections."""

        terminal_statuses = {"completed", "completed_verified", "failed", "cancelled"}
        unresolved: list[str] = []
        for row in rows:
            task_id = OrchestrationStageExecutor._task_projection_external_id(row)
            status = str(row.get("status") or row.get("state") or "").strip().lower()
            if task_id and status not in terminal_statuses and task_id not in unresolved:
                unresolved.append(task_id)
        return tuple(unresolved)

    @staticmethod
    def _is_internal_chief_engineer_task_row(
        row: Mapping[str, Any],
        *,
        factory_run_id: str,
    ) -> bool:
        """Identify a trusted Factory-owned CE execution row.

        Director dependency admission is defined over PM task identities.  CE
        portfolio and schema-repair attempts are separate TaskRuntime facts and
        must remain observable, but they are not vertices in the PM dependency
        graph.  The exclusion is provenance-bound so an arbitrary unknown task
        id still invalidates the schedule fail-closed.
        """

        resolved_run_id = str(factory_run_id or "").strip()
        if not resolved_run_id:
            return False
        metadata_raw = row.get("metadata")
        metadata: Mapping[str, Any] = metadata_raw if isinstance(metadata_raw, Mapping) else {}
        external_task_id = OrchestrationStageExecutor._task_projection_external_id(row)
        return bool(
            external_task_id
            and str(metadata.get("factory_run_id") or "").strip() == resolved_run_id
            and str(metadata.get("factory_stage") or "").strip() == "chief_engineer_review"
            and str(metadata.get("role") or "").strip() == "chief_engineer"
            and str(metadata.get("external_task_id") or "").strip() == external_task_id
            and str(metadata.get("source_task_id") or "").strip() == external_task_id
            and str(metadata.get("materialized_by") or "").strip() == "runtime.task_runtime"
        )

    def _director_dependency_schedule(
        self,
        pm_tasks: list[dict[str, Any]],
        *,
        factory_run_id: str = "",
    ) -> TaskDependencyScheduleV1:
        """Project the remaining Director critical path from TaskRuntime facts."""

        observable_rows = self._read_observable_task_rows(factory_run_id=factory_run_id)
        dependency_rows = [
            row
            for row in observable_rows
            if not self._is_internal_chief_engineer_task_row(
                row,
                factory_run_id=factory_run_id,
            )
        ]
        active_task_ids = self._unresolved_task_ids_from_rows(dependency_rows)
        return build_task_dependency_schedule(
            pm_tasks,
            active_task_ids=active_task_ids if observable_rows else None,
        )

    @staticmethod
    def _director_admission_failure_projection(
        admission_decision: FactoryDeadlineAdmissionV1,
    ) -> tuple[str, str, str, str]:
        """Project one admission rejection without misreporting its cause."""

        reason = str(admission_decision.reason or "").strip()
        blockers = (
            admission_decision.dependency_schedule.blockers
            if reason == "invalid_pm_task_dependency_schedule"
            else admission_decision.budget_plan.blockers
        )
        blocker_detail = "; ".join(str(item) for item in blockers if str(item).strip())
        if reason == "invalid_pm_task_dependency_schedule":
            detail = "Director dispatch rejected an invalid PM task dependency schedule"
            if blocker_detail:
                detail = f"{detail}: {blocker_detail}"
            return (
                "director.dispatch_dependency_schedule_blocker",
                detail,
                "failed",
                "Director dispatch skipped because the PM task dependency schedule is invalid",
            )
        if reason == "no_active_director_tasks":
            # Empty remaining wave is success: PM tasks already terminal (or none
            # remain for Director). Treating this as failed caused stage
            # persistence quarantine + forever-RUNNING lease hangs (R56).
            return (
                "director.dispatch_no_active_tasks",
                "Director dispatch admission found no active PM tasks remaining",
                "completed",
                "Director dispatch complete: no active PM tasks remain to execute",
            )
        return (
            "director.dispatch_deadline_blocker",
            (
                "Factory deadline does not leave enough budget to start another Director "
                "LLM turn while preserving downstream quality-gate time"
            ),
            "timeout",
            "Director dispatch skipped because factory deadline budget is exhausted",
        )

    @staticmethod
    def _director_dispatch_deadline_admission_decision(
        context: dict[str, Any],
        *,
        requested_timeout_seconds: int,
        first_materialization_pending: bool,
        materialization_pending: bool,
        dependency_schedule: TaskDependencyScheduleV1,
    ) -> FactoryDeadlineAdmissionV1:
        """Return the canonical typed admission for one Director dispatch."""

        return deadline_calc.director_dispatch_deadline_admission_decision(
            context,
            requested_timeout_seconds=requested_timeout_seconds,
            first_materialization_pending=first_materialization_pending,
            materialization_pending=materialization_pending,
            dependency_schedule=dependency_schedule,
        )

    @staticmethod
    def _director_first_materialization_min_budget_seconds(context: dict[str, Any]) -> float:
        return deadline_calc.director_first_materialization_min_budget_seconds(context)

    @staticmethod
    def _quality_gate_reserved_budget_seconds(context: dict[str, Any]) -> float:
        return deadline_calc.quality_gate_reserved_budget_seconds(context)

    @staticmethod
    def _director_downstream_reserved_budget_seconds(
        context: dict[str, Any],
        *,
        materialization_pending: bool,
        remaining_task_count: int,
    ) -> float:
        """Reserve only executable downstream work at the Director boundary."""

        return deadline_calc.director_downstream_reserved_budget_seconds(
            context,
            materialization_pending=materialization_pending,
            remaining_task_count=remaining_task_count,
        )

    @staticmethod
    def _director_dispatch_timeout_settle_grace_seconds(context: dict[str, Any]) -> int:
        return deadline_calc.director_dispatch_timeout_settle_grace_seconds(context)

    @staticmethod
    def _chief_engineer_llm_timeout_seconds(context: dict[str, Any]) -> int:
        return deadline_calc.chief_engineer_llm_timeout_seconds(context)

    @staticmethod
    def _chief_engineer_execution_attempt_lease_budget(
        execution_timeout_seconds: int,
    ) -> _ChiefEngineerExecutionAttemptLeaseBudget:
        """Derive one bounded TaskRuntime TTL and heartbeat cadence."""

        return deadline_calc.chief_engineer_execution_attempt_lease_budget(execution_timeout_seconds)

    @staticmethod
    def _chief_engineer_deadline_projection_decision(
        context: dict[str, Any],
        *,
        requested_timeout_seconds: int,
        dependency_schedule: TaskDependencyScheduleV1,
        output_tokens: int | None = None,
    ) -> FactoryDeadlineAdmissionV1:
        """Return admission for one project-level Chief Engineer LLM call."""

        return deadline_calc.chief_engineer_deadline_projection_decision(
            context,
            requested_timeout_seconds=requested_timeout_seconds,
            dependency_schedule=dependency_schedule,
            output_tokens=output_tokens,
        )

    @staticmethod
    def _chief_engineer_projection_semantic_terms(task_context: dict[str, Any]) -> list[str]:
        return deadline_calc.chief_engineer_projection_semantic_terms(task_context)

    @staticmethod
    def _enrich_chief_engineer_projection_context(task_context: dict[str, Any]) -> None:
        deadline_calc.enrich_chief_engineer_projection_context(task_context)

    @staticmethod
    def _director_binding_timeout_quarantine_count() -> int:
        return deadline_calc.director_binding_timeout_quarantine_count()

    # ── Director binding fanout ────────────────────────────────────────────

    @staticmethod
    def _director_binding_identity(provider_id: str, model: str, binding_id: str = "") -> str:
        return deadline_calc.director_binding_identity(provider_id, model, binding_id)

    def _record_director_binding_skip(
        self,
        *,
        provider_id: str,
        model: str,
        binding_id: str,
        reason: str,
    ) -> None:
        skip = {
            "provider_id": str(provider_id or "").strip(),
            "model": str(model or "").strip(),
            "binding_id": str(binding_id or "").strip(),
            "reason": str(reason or "").strip() or "binding_unavailable",
        }
        if not skip["provider_id"] or not skip["model"]:
            return
        skips = getattr(self, "_last_director_binding_skips", [])
        identity = self._director_binding_identity(skip["provider_id"], skip["model"], skip["binding_id"])
        if any(
            self._director_binding_identity(
                str(item.get("provider_id") or ""),
                str(item.get("model") or ""),
                str(item.get("binding_id") or ""),
            )
            == identity
            for item in skips
            if isinstance(item, dict)
        ):
            return
        skips.append(skip)
        self._last_director_binding_skips = skips

    def _director_readiness_skip_reasons(self, context: dict[str, Any] | None = None) -> dict[str, str]:
        if context is None:
            context = {}
        try:
            from polaris.bootstrap.config import Settings
            from polaris.cells.runtime.projection.public import build_llm_status
        except ImportError as exc:
            logger.debug("Director readiness skip resolution unavailable: %s", exc)
            return {}
        try:
            settings = context.get("settings") or Settings(workspace=Path(self.workspace))
            status = build_llm_status(settings)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("Director readiness status unavailable: %s", exc)
            return {}
        roles = status.get("roles") if isinstance(status, dict) else {}
        director = roles.get("director") if isinstance(roles, dict) else {}
        skipped = director.get("skipped_bindings") if isinstance(director, dict) else None
        if not isinstance(skipped, list):
            return {}
        reasons: dict[str, str] = {}
        for item in skipped:
            if not isinstance(item, dict):
                continue
            provider_id = str(item.get("provider_id") or "").strip()
            model = str(item.get("model") or "").strip()
            binding_id = str(item.get("binding_id") or "").strip()
            reason = str(item.get("reason") or "readiness_skipped").strip()
            readiness_source = str(item.get("readiness_source") or item.get("source") or "").strip()
            if readiness_source == "runtime_dispatch":
                continue
            if not provider_id or not model:
                continue
            reasons[self._director_binding_identity(provider_id, model, binding_id)] = reason
            reasons.setdefault(self._director_binding_identity(provider_id, model, ""), reason)
        return reasons

    def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
        self._last_director_binding_skips = []
        try:
            from polaris.kernelone.llm.runtime_config import get_role_binding_slots, is_role_binding_healthy
        except (ImportError, RuntimeError) as exc:
            logger.debug("Director binding fanout resolution unavailable: %s", exc)
            return []
        try:
            slots = get_role_binding_slots("director")
        except (RuntimeError, ValueError, TypeError) as exc:
            logger.debug("Director binding slots unavailable: %s", exc)
            return []
        if len(slots) <= 1:
            return []
        readiness_skip_reasons = self._director_readiness_skip_reasons(context)
        try:
            from polaris.cells.orchestration.pm_dispatch.public.service import reachable_provider_pool

            provider_ids = tuple(dict.fromkeys(str(slot.provider_id) for slot in slots if slot.provider_id))
            live_providers = set(reachable_provider_pool(provider_ids))
        except (ImportError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("Director provider reachability probe failed: %s", exc)
            live_providers = {str(slot.provider_id) for slot in slots if slot.provider_id}
        bindings: list[dict[str, str]] = []
        cooldown_candidates: list[dict[str, str]] = []
        seen_keys: set[str] = set()

        def _append_binding(binding: dict[str, str]) -> None:
            key = f"{binding['provider_id']}|{binding['model']}"
            if key in seen_keys:
                return
            seen_keys.add(key)
            bindings.append(binding)

        for slot in slots:
            pid = str(slot.provider_id or "").strip()
            model = str(slot.model or "").strip()
            binding_id = str(slot.binding_id or "").strip()
            if not pid or pid not in live_providers:
                if pid and model:
                    self._record_director_binding_skip(
                        provider_id=pid,
                        model=model,
                        binding_id=binding_id,
                        reason="provider_unreachable",
                    )
                continue
            readiness_reason = readiness_skip_reasons.get(
                self._director_binding_identity(pid, model, binding_id)
            ) or readiness_skip_reasons.get(self._director_binding_identity(pid, model, ""))
            if readiness_reason:
                if readiness_reason == "role_binding_cooldown":
                    cooldown_candidates.append(
                        {
                            "provider_id": pid,
                            "model": model,
                            "binding_id": binding_id,
                        }
                    )
                    continue
                self._record_director_binding_skip(
                    provider_id=pid,
                    model=model,
                    binding_id=binding_id,
                    reason=readiness_reason,
                )
                continue
            if not is_role_binding_healthy(
                "director",
                provider_id=pid,
                model=model,
                binding_id=binding_id or None,
            ):
                cooldown_candidates.append(
                    {
                        "provider_id": pid,
                        "model": model,
                        "binding_id": binding_id,
                    }
                )
                continue
            _append_binding(
                {
                    "provider_id": pid,
                    "model": model,
                    "binding_id": binding_id,
                }
            )
        if not bindings and cooldown_candidates:
            logger.warning(
                "Director binding cooldown would starve dispatch; allowing %d cooled binding(s)",
                len(cooldown_candidates),
            )
            for binding in cooldown_candidates:
                _append_binding(binding)
        else:
            for binding in cooldown_candidates:
                self._record_director_binding_skip(
                    provider_id=binding["provider_id"],
                    model=binding["model"],
                    binding_id=binding.get("binding_id", ""),
                    reason="role_binding_cooldown",
                )
        if len(bindings) <= 1 and not getattr(self, "_last_director_binding_skips", []):
            return []
        logger.info("Director binding fanout: %d reachable binding(s)", len(bindings))
        return bindings

    async def _execute_director_binding_fanout(
        self,
        *,
        service: Any,
        workspace: str,
        tasks: list[str] | None,
        base_options: dict[str, Any],
        bindings: list[dict[str, str]],
        timeout_seconds: int = 600,
        deadline_monotonic: float | None = None,
        cancel_event: asyncio.Event | None = None,
        abort_checker: Any = None,
        skipped_bindings: list[dict[str, Any]] | None = None,
        authority_port: FactoryRoleEvidenceAuthorityPort,
    ) -> CommandResult:
        execution_deadline = (
            float(deadline_monotonic) if deadline_monotonic is not None else _new_monotonic_deadline(timeout_seconds)
        )
        submitted: list[tuple[dict[str, str], CommandResult]] = []
        readiness_skipped = [dict(item) for item in list(skipped_bindings or []) if isinstance(item, dict)]
        external_readiness_skipped_count = len(readiness_skipped)

        def _binding_key(binding: dict[str, str]) -> str:
            return f"{binding['provider_id']}:{binding['model']}:{binding.get('binding_id', '')}"

        def _backend_failure_reason(result: CommandResult) -> str:
            status = str(result.status or "").strip().lower()
            if status == "timeout":
                return "timeout"
            text = " ".join(
                str(item or "")
                for item in (
                    result.reason_code,
                    result.message,
                    (result.metadata or {}).get("error") if isinstance(result.metadata, dict) else "",
                )
            ).lower()
            backend_markers = (
                "provider_connectivity_unavailable",
                "connection refused",
                "cannot connect",
                "connect timeout",
                "read timeout",
                "timed out",
                "timeout",
                "circuit_open",
                "llm call error",
                "binding_fanout_error",
            )
            if any(marker in text for marker in backend_markers):
                return "provider_backend_failure"
            return ""

        active_bindings = []
        quarantined_skipped = []
        for binding in bindings:
            key = _binding_key(binding)
            if key in self._quarantined_bindings:
                quarantined_skipped.append(binding)
                logger.info("Skipping quarantined binding: %s", key)
            else:
                active_bindings.append(binding)

        requested_tasks = [str(item or "").strip() for item in list(tasks or []) if str(item or "").strip()]
        partition_tasks = bool(requested_tasks) and len(active_bindings) > 1
        assigned_tasks_by_key: dict[str, list[str] | None] = {}
        submission_bindings: list[dict[str, str]] = []
        if partition_tasks:
            for idx, binding in enumerate(active_bindings):
                assigned_tasks = requested_tasks[idx :: len(active_bindings)]
                if not assigned_tasks:
                    readiness_skipped.append({**binding, "reason": "no_assigned_tasks"})
                    continue
                assigned_tasks_by_key[_binding_key(binding)] = assigned_tasks
                submission_bindings.append(binding)
        else:
            for binding in active_bindings:
                assigned_tasks_by_key[_binding_key(binding)] = tasks
                submission_bindings.append(binding)
        active_bindings = submission_bindings
        authority_port.require_grant_capacity("director", len(active_bindings))

        async def _run_binding(binding: dict[str, str]) -> CommandResult:
            binding_tasks = assigned_tasks_by_key.get(_binding_key(binding))
            binding_opts = dict(base_options)
            binding_opts.setdefault("llm_call_timeout_seconds", int(timeout_seconds))
            binding_opts.setdefault("director_llm_timeout_seconds", int(timeout_seconds))
            raw_binding_metadata = base_options.get("metadata")
            binding_metadata: dict[str, Any] = (
                dict(raw_binding_metadata) if isinstance(raw_binding_metadata, dict) else {}
            )
            binding_opts["metadata"] = {
                **binding_metadata,
                "binding_override": {
                    "provider_id": binding["provider_id"],
                    "model": binding["model"],
                    "binding_id": binding.get("binding_id", ""),
                },
                "fanout_assigned_tasks": list(binding_tasks or []),
                "fanout_assigned_task_count": len(binding_tasks or []),
            }
            return cast(
                CommandResult,
                await self._call_with_factory_role_evidence_authority(
                    authority_port,
                    "director",
                    lambda: service.execute_director_run(
                        workspace=workspace,
                        tasks=binding_tasks,
                        options=binding_opts,
                    ),
                ),
            )

        submission_tasks: list[asyncio.Task[CommandResult]] = []
        done_submissions: set[asyncio.Task[CommandResult]] = set()
        try:
            for binding in active_bindings:
                pending_coroutine = _run_binding(binding)
                try:
                    submission_tasks.append(asyncio.create_task(pending_coroutine))
                except BaseException:
                    pending_coroutine.close()
                    raise
            if submission_tasks:
                done_submissions, _pending_submissions = await asyncio.wait(
                    submission_tasks,
                    timeout=_remaining_monotonic_seconds(execution_deadline),
                )
        finally:
            for task in submission_tasks:
                if not task.done():
                    task.cancel()
            if submission_tasks:
                await asyncio.gather(*submission_tasks, return_exceptions=True)

        for idx, task in enumerate(submission_tasks):
            if task not in done_submissions:
                item: CommandResult | BaseException = CommandResult(
                    run_id="",
                    status="timeout",
                    message="Director binding submission exceeded the execution lease",
                    reason_code="DIRECTOR_SUBMISSION_TIMEOUT",
                    metadata={
                        "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                        "responsible_layer": "execution_control_plane",
                        "submission_outcome_unknown": True,
                    },
                )
            elif task.cancelled():
                item = RuntimeError("Director binding submission was cancelled")
            else:
                item = task.exception() or task.result()
            if isinstance(item, Exception):
                logger.warning("Director binding fanout[%d] raised: %s", idx, item)
                submitted.append(
                    (
                        active_bindings[idx],
                        CommandResult(
                            run_id="", status="failed", message=str(item), reason_code="BINDING_FANOUT_ERROR"
                        ),
                    )
                )
            elif isinstance(item, CommandResult):
                submitted.append((active_bindings[idx], item))

        async def _wait_submitted_binding(
            binding: dict[str, str],
            sub_result: CommandResult,
        ) -> tuple[dict[str, str], CommandResult]:
            normalized_status = str(sub_result.status or "").strip().lower()
            if (
                normalized_status in {"failed", "cancelled", "timeout", "blocked"}
                or not str(sub_result.run_id or "").strip()
            ):
                return binding, sub_result
            wait_kwargs: dict[str, Any] = {
                "timeout_seconds": _whole_wait_seconds(execution_deadline),
                "cancel_event": cancel_event,
                "abort_checker": abort_checker,
            }
            remaining_seconds = _remaining_monotonic_seconds(execution_deadline)
            if wait_kwargs["timeout_seconds"] <= 0 or remaining_seconds <= 0:
                return binding, CommandResult(
                    run_id=sub_result.run_id,
                    status="timeout",
                    message="Director binding execution lease expired before completion wait",
                    reason_code="DIRECTOR_EXECUTION_LEASE_EXHAUSTED",
                    metadata={
                        "cancel_signal_sent": False,
                        "inflight_run_continues": True,
                        "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                        "responsible_layer": "execution_control_plane",
                    },
                )
            if _call_accepts_keyword(self._wait_run_completion, "cancel_on_timeout"):
                wait_kwargs["cancel_on_timeout"] = False
            if _call_accepts_keyword(self._wait_run_completion, "authority"):
                wait_kwargs["authority"] = RunCompletionAuthority.TASK_RUNTIME_EXECUTION_FACT
            try:
                return binding, await asyncio.wait_for(
                    self._wait_run_completion(
                        service,
                        sub_result,
                        **wait_kwargs,
                    ),
                    timeout=remaining_seconds,
                )
            except TimeoutError:
                return binding, CommandResult(
                    run_id=sub_result.run_id,
                    status="timeout",
                    message="Director binding completion wait exceeded the execution lease",
                    reason_code="DIRECTOR_EXECUTION_LEASE_EXHAUSTED",
                    metadata={
                        "cancel_signal_sent": False,
                        "inflight_run_continues": True,
                        "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                        "responsible_layer": "execution_control_plane",
                    },
                )
            except (RuntimeError, OSError, ValueError, TypeError) as exc:
                logger.warning("Director binding fanout wait failed for run %s: %s", sub_result.run_id, exc)
                return binding, CommandResult(run_id=sub_result.run_id, status="failed", message=f"Wait failed: {exc}")

        final_results: list[tuple[dict[str, str], CommandResult]] = list(
            await asyncio.gather(*[_wait_submitted_binding(binding, sub_result) for binding, sub_result in submitted])
        )

        quarantine_threshold = self._director_binding_timeout_quarantine_count()
        for binding, result in final_results:
            key = _binding_key(binding)
            if str(result.status or "").strip().lower() == "timeout":
                self._binding_timeout_counts[key] = self._binding_timeout_counts.get(key, 0) + 1
                if self._binding_timeout_counts[key] >= quarantine_threshold:
                    self._quarantined_bindings.add(key)
                    logger.warning(
                        "Quarantining binding %s after %d consecutive timeouts",
                        key,
                        self._binding_timeout_counts[key],
                    )
            else:
                self._binding_timeout_counts[key] = 0
            backend_failure_reason = _backend_failure_reason(result)
            if backend_failure_reason:
                with contextlib.suppress(ImportError, RuntimeError, TypeError, ValueError):
                    from polaris.kernelone.llm.runtime_config import mark_role_binding_unhealthy

                    mark_role_binding_unhealthy(
                        "director",
                        provider_id=binding["provider_id"],
                        model=binding["model"],
                        binding_id=binding.get("binding_id") or None,
                    )

        per_binding: list[dict[str, Any]] = []
        success_count = 0
        fail_count = 0
        first_run_id = ""
        for binding, result in final_results:
            if not first_run_id and result.run_id:
                first_run_id = result.run_id
            status = str(result.status or "").strip().lower()
            if status in {"completed", "success"}:
                success_count += 1
            else:
                fail_count += 1
            key = _binding_key(binding)
            entry: dict[str, Any] = {
                "provider_id": binding["provider_id"],
                "model": binding["model"],
                "binding_id": binding.get("binding_id", ""),
                "run_id": result.run_id or "",
                "status": result.status or "unknown",
                "message": result.message or "",
            }
            result_metadata = result.metadata if isinstance(result.metadata, dict) else {}
            entry_assigned_tasks = assigned_tasks_by_key.get(key)
            if entry_assigned_tasks is not None:
                entry["assigned_tasks"] = list(entry_assigned_tasks)
                entry["assigned_task_count"] = len(entry_assigned_tasks)
            for evidence_key in (
                "cancel_signal_sent",
                "cancel_reason",
                "inflight_run_continues",
                "terminal_source",
                "queried_status",
                "timeout_settle_grace_seconds",
                "active_task_count",
                "active_task_ids",
            ):
                if evidence_key in result_metadata:
                    entry[evidence_key] = result_metadata[evidence_key]
            if status == "timeout":
                entry["timeout_count"] = self._binding_timeout_counts.get(key, 0)
                if key in self._quarantined_bindings:
                    entry["quarantined"] = True
                    entry["quarantine_reason"] = "consecutive_timeout"
            backend_failure_reason = _backend_failure_reason(result)
            if backend_failure_reason:
                entry["backend_failure_reason"] = backend_failure_reason
            per_binding.append(entry)

        for binding in quarantined_skipped:
            key = _binding_key(binding)
            per_binding.append(
                {
                    "provider_id": binding["provider_id"],
                    "model": binding["model"],
                    "binding_id": binding.get("binding_id", ""),
                    "run_id": "",
                    "status": "quarantined",
                    "message": "Skipped due to consecutive timeouts",
                    "quarantined": True,
                    "quarantine_reason": "consecutive_timeout",
                    "timeout_count": self._binding_timeout_counts.get(key, 0),
                }
            )

        for binding in readiness_skipped:
            provider_id = str(binding.get("provider_id") or "").strip()
            model = str(binding.get("model") or "").strip()
            binding_id = str(binding.get("binding_id") or "").strip()
            if not provider_id or not model:
                continue
            per_binding.append(
                {
                    "provider_id": provider_id,
                    "model": model,
                    "binding_id": binding_id,
                    "run_id": "",
                    "status": "skipped",
                    "message": "Skipped by Director binding readiness filter",
                    "skipped": True,
                    "skip_reason": str(binding.get("reason") or "binding_unavailable").strip(),
                    "assigned_tasks": [],
                    "assigned_task_count": 0,
                }
            )

        quarantined_count = sum(1 for entry in per_binding if entry.get("quarantined"))
        skipped_count = len(quarantined_skipped)
        readiness_skipped_count = sum(
            1 for entry in per_binding if entry.get("skipped") and not entry.get("quarantined")
        )
        merged_status = "completed" if success_count > 0 and fail_count == 0 else "failed"
        total_binding_count = len(bindings) + external_readiness_skipped_count
        return CommandResult(
            run_id=first_run_id,
            status=merged_status,
            message=(
                f"Director binding fanout: {total_binding_count} bindings, {success_count} succeeded, "
                f"{fail_count} failed, {quarantined_count} quarantined, "
                f"{readiness_skipped_count} readiness-skipped"
            ),
            metadata={
                "binding_fanout": True,
                "binding_count": total_binding_count,
                "active_binding_count": len(active_bindings),
                "quarantined_binding_count": quarantined_count,
                "quarantined_skipped_count": skipped_count,
                "timeout_quarantine_threshold": quarantine_threshold,
                "readiness_skipped_count": readiness_skipped_count,
                "per_binding": per_binding,
                "task_assignment_mode": "partitioned" if partition_tasks else "shared",
                "requested_task_ids": requested_tasks,
                "execution_mode": str(base_options.get("execution_mode", "")).strip(),
                "max_workers": int(base_options.get("max_workers", 0)),
            },
        )

    @staticmethod
    def _build_per_binding_route_events(per_binding: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now_iso = datetime.now(timezone.utc).isoformat()
        events: list[dict[str, Any]] = []
        for entry in per_binding:
            if not isinstance(entry, dict):
                continue
            provider_id = str(entry.get("provider_id") or "").strip()
            model = str(entry.get("model") or "").strip()
            binding_id = str(entry.get("binding_id") or "").strip()
            run_id = str(entry.get("run_id") or "").strip()
            status = str(entry.get("status") or "").strip().lower()
            if not provider_id or not model:
                continue
            event: dict[str, Any] = {
                "event": "llm_route_terminal",
                "role": "director",
                "provider_id": provider_id,
                "model": model,
                "binding_id": binding_id,
                "run_id": run_id,
                "status": status,
                "source": "llm",
                "cache_hit": False,
                "invocation": True,
                "terminal": True,
                "fail_closed": False,
                "timestamp": now_iso,
            }
            if status == "timeout" or entry.get("quarantined"):
                event["timeout_count"] = entry.get("timeout_count", 0)
            if entry.get("quarantined"):
                event["quarantined"] = True
                event["quarantine_reason"] = entry.get("quarantine_reason", "")
            if entry.get("skipped"):
                event["skipped"] = True
                event["skip_reason"] = entry.get("skip_reason", "")
                event["invocation"] = False
                event["fail_closed"] = True
            events.append(event)
        return events

    @staticmethod
    def _build_fail_closed_director_route_events(
        *,
        attempts: list[dict[str, Any]],
        stage_signals: list[dict[str, Any]],
        per_binding_route_events: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        try:
            from polaris.cells.factory.pipeline.internal.bench_gates import _norm_text, resolve_expected_llm_bindings
        except (ImportError, RuntimeError):
            return []
        expected = resolve_expected_llm_bindings(("director",))
        configured = expected.get("director") or []
        if not configured:
            return []
        observed_providers: set[str] = set()
        for event in per_binding_route_events or []:
            if not isinstance(event, dict):
                continue
            provider = _norm_text(event.get("provider_id") or event.get("provider"))
            model = _norm_text(event.get("model"))
            if provider and model:
                observed_providers.add(f"{provider}|{model}")
        for attempt in attempts:
            metadata = attempt.get("metadata") if isinstance(attempt, dict) else {}
            if not isinstance(metadata, dict):
                continue
            provider = _norm_text(metadata.get("provider_id") or metadata.get("provider"))
            model = _norm_text(metadata.get("model"))
            if provider and model:
                observed_providers.add(f"{provider}|{model}")
        for signal in stage_signals:
            if not isinstance(signal, dict):
                continue
            detail = str(signal.get("detail") or "")
            for binding in configured:
                provider = _norm_text(binding.get("provider_id") or binding.get("provider"))
                model = _norm_text(binding.get("model"))
                if provider and model and provider in detail and model in detail:
                    observed_providers.add(f"{provider}|{model}")
        now_iso = datetime.now(timezone.utc).isoformat()
        fail_closed_events: list[dict[str, Any]] = []
        for binding in configured:
            provider = _norm_text(binding.get("provider_id") or binding.get("provider"))
            model = _norm_text(binding.get("model"))
            binding_id = _norm_text(binding.get("binding_id"))
            key = f"{provider}|{model}"
            if not provider or not model or key in observed_providers:
                continue
            fail_closed_events.append(
                {
                    "event": "llm_route_fail_closed",
                    "role": "director",
                    "provider_id": provider,
                    "model": model,
                    "binding_id": binding_id,
                    "source": "diagnostic",
                    "cache_hit": False,
                    "invocation": True,
                    "terminal": False,
                    "fail_closed": True,
                    "fail_closed_reason": "no_dispatch_evidence_for_binding",
                    "timestamp": now_iso,
                }
            )
        return fail_closed_events

    @staticmethod
    def _reclassify_binding_coverage_signals(
        stage_signals: list[dict[str, Any]],
        per_binding_route_events: list[dict[str, Any]],
    ) -> None:
        if not per_binding_route_events:
            return
        try:
            from polaris.cells.factory.pipeline.internal.bench_gates import _norm_text, resolve_expected_llm_bindings
        except (ImportError, RuntimeError):
            return
        expected = resolve_expected_llm_bindings(("director",))
        configured = expected.get("director") or []
        if not configured:
            return
        observed_loose: set[str] = set()
        for event in per_binding_route_events:
            if not isinstance(event, dict):
                continue
            provider = _norm_text(event.get("provider_id") or event.get("provider"))
            model = _norm_text(event.get("model"))
            if provider and model:
                observed_loose.add(f"{provider}|{model}")
        configured_loose: set[str] = set()
        for binding in configured:
            provider = _norm_text(binding.get("provider_id") or binding.get("provider"))
            model = _norm_text(binding.get("model"))
            if provider and model:
                configured_loose.add(f"{provider}|{model}")
        if not configured_loose or configured_loose != observed_loose:
            return
        has_timeout = any(
            str(ev.get("status") or "").strip().lower() == "timeout"
            for ev in per_binding_route_events
            if isinstance(ev, dict)
        )
        if not has_timeout:
            return
        for i, signal in enumerate(stage_signals):
            if not isinstance(signal, dict):
                continue
            if signal.get("code") != "director.binding_coverage_incomplete":
                continue
            timeout_bindings = [
                str(ev.get("binding_id") or f"{ev.get('provider_id')}|{ev.get('model')}")
                for ev in per_binding_route_events
                if isinstance(ev, dict) and str(ev.get("status") or "").strip().lower() == "timeout"
            ]
            stage_signals[i] = {
                "code": "director.binding_timeout",
                "severity": "error",
                "detail": f"All director bindings have terminal evidence but {len(timeout_bindings)} timed out: {', '.join(timeout_bindings[:8])}",
                "timeout_bindings": timeout_bindings,
                "observed_count": len(per_binding_route_events),
                "multi_route_required": True,
            }
            break

    def _validate_director_binding_coverage(
        self,
        additional_events: list[dict[str, Any]] | None = None,
    ) -> tuple[bool, list[dict[str, Any]]]:
        try:
            from polaris.cells.factory.pipeline.internal.bench_gates import (
                build_llm_route_audit,
                collect_llm_events,
                resolve_expected_llm_bindings,
            )
        except (ImportError, RuntimeError) as exc:
            return False, [
                {
                    "code": "director.binding_coverage_audit_unavailable",
                    "severity": "error",
                    "detail": f"Director binding coverage audit is unavailable: {exc}",
                }
            ]
        expected = resolve_expected_llm_bindings(("director",))
        configured = expected.get("director") or []
        if not configured:
            return True, []
        try:
            events = collect_llm_events(self.workspace, None)
        except (RuntimeError, OSError, ValueError, TypeError):
            events = []
        if additional_events:
            seen_keys: set[tuple[str, ...]] = set()
            for ev in events:
                key = (
                    str(ev.get("event") or ""),
                    str(ev.get("provider_id") or ""),
                    str(ev.get("model") or ""),
                    str(ev.get("binding_id") or ""),
                    str(ev.get("run_id") or ""),
                )
                seen_keys.add(key)
            for ev in additional_events:
                if not isinstance(ev, dict):
                    continue
                key = (
                    str(ev.get("event") or ""),
                    str(ev.get("provider_id") or ""),
                    str(ev.get("model") or ""),
                    str(ev.get("binding_id") or ""),
                    str(ev.get("run_id") or ""),
                )
                if key not in seen_keys:
                    events.append(ev)
                    seen_keys.add(key)
        audit = build_llm_route_audit(
            events, expected_bindings=expected, required_roles=("director",), require_all_director_routes=True
        )
        if audit.get("ok"):
            return True, []
        director_result = audit.get("roles", {}).get("director", {})
        missing = list(director_result.get("missing_bindings") or [])
        observed_count = int(director_result.get("observed_count") or 0)
        fail_closed_count = int(director_result.get("fail_closed_count") or 0)
        signals: list[dict[str, Any]] = []
        if missing:
            signals.append(
                {
                    "code": "director.binding_coverage_incomplete",
                    "severity": "error",
                    "detail": f"Not all configured director bindings produced real LLM evidence. Observed={observed_count}, missing={len(missing)}, fail_closed(diagnostic)={fail_closed_count}. Missing: {', '.join(missing[:8])}",
                    "missing_bindings": missing,
                    "observed_count": observed_count,
                    "fail_closed_count": fail_closed_count,
                    "multi_route_required": True,
                }
            )
        elif observed_count == 0:
            signals.append(
                {
                    "code": "director.no_real_llm_evidence",
                    "severity": "error",
                    "detail": "No real LLM terminal evidence found for any configured director binding.",
                    "observed_count": 0,
                    "fail_closed_count": fail_closed_count,
                }
            )
        else:
            signals.append(
                {
                    "code": "director.binding_coverage_failed",
                    "severity": "error",
                    "detail": str(audit.get("summary") or "Director binding coverage audit failed"),
                    "observed_count": observed_count,
                    "fail_closed_count": fail_closed_count,
                    "multi_route_required": True,
                }
            )
        return False, signals

    def _director_provider_health_failure_signal(self) -> dict[str, Any] | None:
        try:
            from polaris.cells.factory.pipeline.internal.bench_gates import collect_llm_events
        except (ImportError, RuntimeError):
            return None
        try:
            events = collect_llm_events(self.workspace, None)
        except (RuntimeError, OSError, ValueError, TypeError):
            return None
        return self._director_provider_health_failure_signal_from_events(events)

    @staticmethod
    def _director_provider_health_failure_signal_from_events(
        events: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        for event in reversed(events):
            if not isinstance(event, dict):
                continue
            if str(event.get("role") or "").strip().lower() != "director":
                continue
            if bool(event.get("skipped")):
                continue
            event_name = str(event.get("event") or "").strip().lower()
            if event_name not in {"llm_error", "call_error", "error"} and not bool(event.get("terminal")):
                continue
            error_text = OrchestrationStageExecutor._llm_event_error_text(event)
            if not error_text:
                continue
            lowered = error_text.lower()
            provider_id = str(event.get("provider_id") or "").strip()
            model = str(event.get("model") or "").strip()
            source_path = str(event.get("source_path") or "").strip()
            if any(token in lowered for token in _DIRECTOR_PROVIDER_RATE_LIMIT_TOKENS):
                return {
                    "code": "director.provider_rate_limit",
                    "severity": "error",
                    "detail": "Director LLM provider rate limit/quota failure before tool dispatch",
                    "provider_id": provider_id,
                    "model": model,
                    "source_path": source_path,
                    "error_excerpt": error_text[:600],
                    "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                    "responsible_layer": "model_provider",
                    "repairable_by_director": False,
                    "requires_ce_replan": False,
                    "requires_pm_revision": False,
                }
            if any(token in lowered for token in _DIRECTOR_PROVIDER_UNAVAILABLE_TOKENS):
                return {
                    "code": "director.provider_unavailable",
                    "severity": "error",
                    "detail": "Director LLM provider transport/circuit failure before tool dispatch",
                    "provider_id": provider_id,
                    "model": model,
                    "source_path": source_path,
                    "error_excerpt": error_text[:600],
                    "failure_class": FailureClassV1.TEST_ENVIRONMENT_FAILURE.value,
                    "responsible_layer": "model_provider",
                    "repairable_by_director": False,
                    "requires_ce_replan": False,
                    "requires_pm_revision": False,
                }
        return None

    @staticmethod
    def _llm_event_error_text(event: dict[str, Any]) -> str:
        return ce_evidence.llm_event_error_text(event)

    async def _execute_docs_generation(self, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        logger.info("Executing docs generation for run %s", run.id)
        abort_checker = self._resolve_abort_checker(context)
        authority_port = self._factory_role_evidence_cutoff_port(context)

        service = self._build_orchestration_service(context)
        command_result = cast(
            CommandResult,
            await self._call_with_factory_role_evidence_authority(
                authority_port,
                "architect",
                lambda: service.execute_pm_run(
                    workspace=str(self.workspace),
                    run_type="architect",
                    options={
                        "directive": context.get("directive", "Generate project documentation"),
                        "run_director": False,
                    },
                ),
            ),
        )
        final_result = await self._wait_run_completion(
            service,
            command_result,
            timeout_seconds=int(context.get("timeout", 600)),
            cancel_event=self._resolve_cancel_event(context),
            abort_checker=abort_checker,
        )
        if str(final_result.status or "").strip().lower() == "cancelled":
            return StageResult(
                stage="docs_generation",
                status="cancelled",
                output=f"Docs generation cancelled: {final_result.message or 'N/A'}",
                artifacts=[],
            )

        upstream_success = final_result.status in {"completed", "success"}
        stage_signals: list[dict[str, Any]] = []
        if not upstream_success:
            stage_signals.append(
                {
                    "code": "docs.run_status_non_success",
                    "severity": "error",
                    "detail": str(final_result.message or "").strip() or str(final_result.status or "unknown"),
                    "upstream_status": str(final_result.status or "").strip(),
                }
            )
        missing_artifacts: list[str] = []
        if upstream_success:
            missing_artifacts = self._ensure_docs_artifacts(
                directive=str(context.get("directive") or ""),
                summary=str(final_result.message or ""),
            )
            if missing_artifacts:
                stage_signals.append(
                    {
                        "code": "docs.required_artifacts_missing",
                        "severity": "error",
                        "detail": f"Missing docs artifacts: {missing_artifacts}",
                    }
                )
        artifacts: list[str] = []
        for candidate in ("docs/plan.md", "docs/architecture.md"):
            if self._artifact_exists(candidate, min_chars=1):
                artifacts.append(candidate)
        self._mirror_docs_artifacts(run.id, artifacts)
        if stage_signals:
            artifacts.append(
                self._write_stage_signal_artifact(
                    stage="docs_generation",
                    run_id=run.id,
                    signals=stage_signals,
                )
            )
        stage_status = "success" if (upstream_success and not missing_artifacts) else "failed"
        status_label = "completed" if stage_status == "success" else "failed"
        return StageResult(
            stage="docs_generation",
            status=stage_status,
            output=(f"Docs generation {status_label}: {final_result.message or 'N/A'}; signals={len(stage_signals)}"),
            artifacts=artifacts,
        )

    async def _execute_pm_planning(self, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        logger.info("Executing PM planning for run %s", run.id)
        abort_checker = self._resolve_abort_checker(context)
        authority_port = self._factory_role_evidence_cutoff_port(context)
        planning_directive = self._build_pm_planning_directive(
            context.get("directive", "Plan implementation tasks"),
        )
        reset_summary = TaskRuntimeService(str(self.workspace)).reset_records(
            keep_plan=True,
            factory_run_id=run.id,
        )
        if reset_summary.get("ok") is not True:
            return StageResult(
                stage="pm_planning",
                status="failed",
                output=(
                    "PM planning blocked by TaskRuntime reset authority: "
                    f"code={reset_summary.get('code') or 'task_runtime_reset_failed'}; "
                    f"conflicts={reset_summary.get('conflict_count') or 0}"
                ),
                artifacts=[],
            )

        service = self._build_orchestration_service(context)
        pm_run_metadata = self._pm_deterministic_contract_metadata_for_context(run, context)
        pm_run_options: dict[str, Any] = {
            "directive": planning_directive,
            "run_director": False,
        }
        if pm_run_metadata:
            pm_run_options["metadata"] = pm_run_metadata
        command_result = cast(
            CommandResult,
            await self._call_with_factory_role_evidence_authority(
                authority_port,
                "pm",
                lambda: service.execute_pm_run(
                    workspace=str(self.workspace),
                    run_type="pm",
                    options=pm_run_options,
                ),
            ),
        )
        final_result = await self._wait_run_completion(
            service,
            command_result,
            timeout_seconds=int(context.get("timeout", 600)),
            cancel_event=self._resolve_cancel_event(context),
            abort_checker=abort_checker,
        )
        if str(final_result.status or "").strip().lower() == "cancelled":
            return StageResult(
                stage="pm_planning",
                status="cancelled",
                output=f"PM planning cancelled: {final_result.message or 'N/A'}",
                artifacts=[],
            )

        stage_signals: list[dict[str, Any]] = [
            {
                "code": "pm.task_runtime_reset",
                "severity": "info",
                "detail": "Cleared stale executable task records before materializing the current PM plan.",
                "cleared_count": int(cast("int | str", reset_summary.get("cleared_count")) or 0),
                "failed_count": int(cast("int | str", reset_summary.get("failed_count")) or 0),
            }
        ]
        if pm_run_metadata:
            stage_signals.append(
                {
                    "code": "pm.deterministic_contracts_enabled",
                    "severity": "info",
                    "detail": "PM planning was started with deterministic contract metadata.",
                    "factory_recovery": str(pm_run_metadata.get("factory_recovery") or ""),
                    "factory_bench_project_id": str(pm_run_metadata.get("factory_bench_project_id") or ""),
                }
            )
        if str(final_result.status or "").strip().lower() == "timeout" and not self._artifact_exists(
            "tasks/plan.json", min_chars=1
        ):
            recovery_result = await self._run_pm_planning_deterministic_recovery(
                service=service,
                planning_directive=planning_directive,
                context=context,
                abort_checker=abort_checker,
                authority_port=authority_port,
            )
            if recovery_result.status in {"completed", "success"} or self._artifact_exists(
                "tasks/plan.json", min_chars=1
            ):
                stage_signals.append(
                    {
                        "code": "pm.timeout_recovered_by_deterministic_contracts",
                        "severity": "warning",
                        "detail": str(final_result.message or "").strip() or "PM LLM planning timed out",
                        "recovery_status": str(recovery_result.status or "").strip(),
                    }
                )
                final_result = recovery_result

        if final_result.status not in {"completed", "success"}:
            stage_signals.append(
                {
                    "code": "pm.run_status_non_success",
                    "severity": "error",
                    "detail": str(final_result.message or "").strip() or str(final_result.status or "unknown"),
                    "upstream_status": str(final_result.status or "").strip(),
                }
            )
        synced_plan_source = self._ensure_pm_plan_contract_available()
        if synced_plan_source:
            stage_signals.append(
                {
                    "code": "pm.plan_contract_synced_from_workspace_mirror",
                    "severity": "info",
                    "detail": "Copied PM workspace plan mirror into runtime tasks/plan.json for downstream stages.",
                    "source_path": synced_plan_source,
                }
            )
        enrichment_summary = self._enrich_pm_plan_contract_artifact("tasks/plan.json")
        if int(enrichment_summary.get("task_count") or 0) > 0:
            stage_signals.append(
                {
                    "code": "pm.plan_contract_enriched_with_catalog_depth_and_declared_targets",
                    "severity": "info",
                    "detail": (
                        "Merged catalog delivery depth contract and project declared target union into PM task contracts."
                    ),
                    **enrichment_summary,
                }
            )
        normalization_summary = self._persist_normalized_pm_plan_validation_contracts("tasks/plan.json")
        if int(normalization_summary.get("task_count") or 0) > 0:
            stage_signals.append(
                {
                    "code": "pm.plan_validation_contracts_persisted",
                    "severity": "info",
                    "detail": ("Persisted the exact PM validation contracts consumed by Chief Engineer provenance."),
                    **normalization_summary,
                }
            )
        contract_issue = self._validate_pm_plan_contract("tasks/plan.json")
        if contract_issue:
            stage_signals.append(
                {
                    "code": "pm.contract_issue_detected",
                    "severity": "error",
                    "detail": contract_issue,
                }
            )
        if not contract_issue:
            language_issue = self._validate_pm_plan_language_consistency("tasks/plan.json")
            if language_issue:
                contract_issue = language_issue
                stage_signals.append(
                    {
                        "code": "pm.language_mismatch_detected",
                        "severity": "error",
                        "detail": language_issue,
                    }
                )
        pm_tasks = self._load_pm_plan_tasks("tasks/plan.json")
        if not contract_issue and pm_tasks:
            materialize_summary = self._materialize_pm_plan_taskboard(
                pm_tasks,
                run_id=run.id,
                source_stage="pm_planning",
                run_metadata=run.metadata,
            )
            binding_failures = list(materialize_summary.get("binding_failures") or [])
            if binding_failures:
                contract_issue = "TaskRuntime rejected one or more Factory run bindings"
            stage_signals.append(
                {
                    "code": (
                        "pm.task_runtime_factory_binding_failed"
                        if binding_failures
                        else "pm.taskboard_materialized_from_plan"
                    ),
                    "severity": "error" if binding_failures else "info",
                    "detail": (
                        contract_issue
                        if binding_failures
                        else "Materialized PM plan tasks into canonical TaskBoard for Director claim enforcement."
                    ),
                    **materialize_summary,
                }
            )
        artifacts: list[str] = []
        if self._artifact_exists("tasks/plan.json", min_chars=1):
            artifacts.append("tasks/plan.json")
            self._mirror_pm_plan_artifacts(run.id, artifacts)
        if stage_signals:
            artifacts.append(
                self._write_stage_signal_artifact(
                    stage="pm_planning",
                    run_id=run.id,
                    signals=stage_signals,
                )
            )
        stage_status = "success"
        if final_result.status not in {"completed", "success"} or bool(contract_issue):
            stage_status = "failed"
        error_code = ""
        root_cause_hint = ""
        if stage_status == "failed":
            for signal in stage_signals:
                if not isinstance(signal, dict):
                    continue
                if str(signal.get("severity") or "").strip().lower() != "error":
                    continue
                error_code = str(signal.get("code") or "").strip()
                root_cause_hint = str(signal.get("detail") or "").strip()
                if error_code:
                    break
        return StageResult(
            stage="pm_planning",
            status=stage_status,
            output=(
                f"PM planning {final_result.status}: {final_result.message or 'N/A'}; "
                f"signals={len(stage_signals)}; "
                f"error_code={error_code or 'none'}; root_cause_hint={root_cause_hint or 'none'}"
            ),
            artifacts=artifacts,
        )

    async def _run_pm_planning_deterministic_recovery(
        self,
        *,
        service: Any,
        planning_directive: str,
        context: dict[str, Any],
        abort_checker: Callable[[], Awaitable[str | None]] | None,
        authority_port: FactoryRoleEvidenceAuthorityPort,
    ) -> CommandResult:
        recovery_timeout = int(context.get("pm_recovery_timeout", 120))
        command_result = cast(
            CommandResult,
            await self._call_with_factory_role_evidence_authority(
                authority_port,
                "pm",
                lambda: service.execute_pm_run(
                    workspace=str(self.workspace),
                    run_type="pm",
                    options={
                        "directive": planning_directive,
                        "run_director": False,
                        "metadata": {
                            "deterministic_pm_contracts": True,
                            "factory_recovery": "pm_timeout_without_plan",
                            "timeout_seconds": recovery_timeout,
                        },
                    },
                ),
            ),
        )
        return await self._wait_run_completion(
            service,
            command_result,
            timeout_seconds=recovery_timeout,
            cancel_event=self._resolve_cancel_event(context),
            abort_checker=abort_checker,
        )

    @staticmethod
    def _ce_extract_llm_evidence(ce_result: Any, *, task_id: str, run_id: str) -> dict[str, Any]:
        return ce_evidence.ce_extract_llm_evidence(ce_result, task_id=task_id, run_id=run_id)

    @staticmethod
    def _ce_review_schema_failure_is_recoverable(ce_result: Any, *, raw_output: str) -> bool:
        return ce_evidence.ce_review_schema_failure_is_recoverable(ce_result, raw_output=raw_output)

    @staticmethod
    def _ce_portfolio_result_allows_schema_repair(ce_result: Any) -> bool:
        """Whether one failed CE portfolio result may consume the single repair."""

        return ce_evidence.ce_portfolio_result_allows_schema_repair(ce_result)

    @staticmethod
    def _ce_schema_repair_failure_class(ce_result: Any) -> str:
        return ce_evidence.ce_schema_repair_failure_class(ce_result)

    @staticmethod
    def _attach_ce_llm_evidence(signal: dict[str, Any], evidence: dict[str, Any]) -> None:
        ce_evidence.attach_ce_llm_evidence(signal, evidence)

    @staticmethod
    def _ce_missing_final_request_evidence(evidence: dict[str, Any]) -> list[str]:
        return ce_evidence.ce_missing_final_request_evidence(evidence)

    @staticmethod
    def _architecture_decision_payloads(values: Any) -> list[dict[str, Any]]:
        return ce_evidence.architecture_decision_payloads(values)

    def _ensure_chief_engineer_blueprint_artifact_present(
        self,
        *,
        result: Any,
        task: dict[str, Any],
        task_context: dict[str, Any],
        constraints: dict[str, Any],
        run_id: str,
    ) -> bool:
        blueprint_path = str(getattr(result, "blueprint_path", "") or "").strip()
        if not blueprint_path or self._artifact_exists(blueprint_path, min_chars=2):
            return False

        now = datetime.now(timezone.utc).isoformat()
        blueprint_id = str(getattr(result, "blueprint_id", "") or Path(blueprint_path).stem).strip()
        payload = {
            "schema_version": "chief_engineer.blueprint.v1",
            "role": "chief_engineer",
            "blueprint_id": blueprint_id,
            "task_id": str(getattr(result, "task_id", "") or self._task_id(task, 0)).strip(),
            "run_id": str(run_id or "").strip(),
            "title": self._task_string(task, "title", "subject", "goal"),
            "objective": str(getattr(result, "objective", "") or "").strip() or self._task_objective(task),
            "summary": str(getattr(result, "summary", "") or "").strip(),
            "status": str(getattr(result, "status", "") or "generated").strip(),
            "source": "factory_stage_executor.ce_result_artifact_repair",
            "target_files": list(getattr(result, "target_files", ()) or []),
            "scope_paths": list(getattr(result, "scope_paths", ()) or []),
            "acceptance_criteria": list(getattr(result, "acceptance_criteria", ()) or []),
            "execution_checklist": list(getattr(result, "execution_checklist", ()) or []),
            "dependencies": list(getattr(result, "dependencies", ()) or []),
            "architecture_decisions": self._architecture_decision_payloads(
                getattr(result, "architecture_decisions", ())
            ),
            "selected_libraries": list(getattr(result, "selected_libraries", ()) or []),
            "constraints": dict(constraints),
            "context": dict(task_context),
            "pm_task": dict(task),
            "contract_completeness": {
                "reconstructed_from_result": True,
                "physical_artifact_missing_before_repair": True,
            },
            "handoff_ready": True,
            "recommendations": list(getattr(result, "recommendations", ()) or []),
            "risks": list(getattr(result, "risks", ()) or []),
            "created_at": now,
            "updated_at": now,
            "blueprint_hash": str(getattr(result, "blueprint_hash", "") or "").strip(),
        }
        self._write_json_artifact(blueprint_path, payload)
        return True

    def _chief_engineer_portfolio_tasks(
        self,
        pm_tasks: list[dict[str, Any]],
    ) -> tuple[ChiefEngineerPortfolioTaskV1, ...]:
        """Project validated PM facts into the CE portfolio contract."""

        portfolio_tasks: list[ChiefEngineerPortfolioTaskV1] = []
        for index, task in enumerate(pm_tasks, start=1):
            target_files = tuple(self._task_string_list(task, "target_files"))
            scope_paths = tuple(self._task_string_list(task, "scope_paths")) or target_files
            portfolio_tasks.append(
                ChiefEngineerPortfolioTaskV1(
                    task_id=self._task_id(task, index),
                    objective=self._task_objective(task),
                    target_files=target_files,
                    scope_paths=scope_paths,
                    dependencies=tuple(self._task_string_list(task, "depends_on", "dependencies")),
                )
            )
        return tuple(portfolio_tasks)

    def _chief_engineer_portfolio_context(
        self,
        pm_tasks: list[dict[str, Any]],
        *,
        run_id: str,
        failure_feedback: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build one structured final-request evidence payload for all PM tasks.

        The PM contracts remain authoritative.  This projection only gives one
        CE call enough product intent, task topology, and file ownership context
        to design interfaces consistently across task boundaries.

        Complexity:
            O(T + F) time and space for ``T`` tasks and ``F`` declared paths.
        """

        task_rows: list[dict[str, Any]] = []
        target_files: list[str] = []
        scope_paths: list[str] = []
        seen_targets: set[str] = set()
        seen_scope: set[str] = set()
        for index, task in enumerate(pm_tasks, start=1):
            task_context = self._task_blueprint_context(task, run_id=run_id, index=index)
            task_targets = self._task_string_list(task, "target_files")
            task_scope = self._task_string_list(task, "scope_paths") or task_targets
            for path in task_targets:
                if path not in seen_targets:
                    seen_targets.add(path)
                    target_files.append(path)
            for path in task_scope:
                if path not in seen_scope:
                    seen_scope.add(path)
                    scope_paths.append(path)
            task_rows.append(
                {
                    "task_id": self._task_id(task, index),
                    "title": self._task_string(task, "title", "subject", "goal"),
                    "objective": self._task_objective(task),
                    "target_files": task_targets,
                    "scope_paths": task_scope,
                    "depends_on": self._task_string_list(task, "depends_on", "dependencies"),
                    "acceptance_criteria": self._task_string_list(
                        task,
                        "acceptance",
                        "acceptance_criteria",
                    ),
                    "execution_checklist": self._task_string_list(task, "steps", "execution_checklist"),
                    "delivery_plan_document": task_context.get("delivery_plan_document", {}),
                    "delivery_depth_contract": task_context.get("delivery_depth_contract", {}),
                    "behavior_contract": task_context.get("behavior_contract", {}),
                    "existing_target_files": task_context.get("existing_target_files", []),
                }
            )

        pm_contract_set = {
            "schema_version": "polaris.validated_pm_contract_set.v1",
            "source_artifact": "tasks/plan.json",
            "tasks": [dict(task) for task in pm_tasks],
        }
        context = {
            "factory_run_id": run_id,
            "source_artifact": "tasks/plan.json",
            "pm_task_contract": pm_contract_set,
            "pm_task_contracts": [dict(task) for task in pm_tasks],
            "portfolio_tasks": task_rows,
            "project_task_graph": [
                {
                    "task_id": row["task_id"],
                    "depends_on": list(row["depends_on"]),
                    "target_files": list(row["target_files"]),
                }
                for row in task_rows
            ],
            "target_files": target_files,
            "scope_paths": scope_paths,
            "task_count": len(task_rows),
        }
        if failure_feedback:
            context["failure_feedback"] = deepcopy(dict(failure_feedback))
            context["chief_engineer_local_rework"] = True
        return context

    def _chief_engineer_portfolio_objective(self, pm_tasks: list[dict[str, Any]]) -> str:
        """Return a natural-language project design objective for one CE call."""

        task_lines = [
            f"- {self._task_id(task, index)}: {self._task_objective(task)}"
            for index, task in enumerate(pm_tasks, start=1)
        ]
        return (
            "Produce one coherent Chief Engineer project blueprint portfolio for the validated PM task graph. "
            "Define shared module boundaries and cross-file interfaces before projecting concrete plans for every "
            "task. Preserve PM target/scope authority and make each task independently executable by Director.\n\n"
            "Validated PM tasks:\n" + "\n".join(task_lines) + _CE_BLUEPRINT_OUTPUT_CONTRACT
        )

    def _chief_engineer_project_kind_authority(
        self,
        *,
        project_id: str,
        run_id: str,
        pm_contract_hash: str,
        catalog_snapshot: Mapping[str, Any],
        catalog_snapshot_hash: str,
    ) -> ProjectKindAuthorityV1:
        """Mirror the CE owner derivation for provider context; CE revalidates it."""

        try:
            return derive_project_kind_authority_from_catalog_snapshot(
                project_id=project_id,
                run_id=run_id,
                pm_contract_hash=pm_contract_hash,
                catalog_snapshot=catalog_snapshot,
                catalog_snapshot_hash=catalog_snapshot_hash,
            )
        except (TypeError, ValueError) as exc:
            raise _ChiefEngineerPortfolioAuthorityError(
                "chief_engineer.project_completion_project_kind_authority_invalid",
                str(exc),
            ) from exc

    def _chief_engineer_catalog_snapshot(self) -> dict[str, Any]:
        """Capture the exact platform catalog after PM artifact revalidation."""

        catalog_path = self.workspace / ".polaris" / "catalog_contract.json"
        if not catalog_path.exists():
            return {}
        try:
            payload = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise _ChiefEngineerPortfolioAuthorityError(
                "chief_engineer.project_completion_catalog_snapshot_invalid",
                "catalog_contract.json is unreadable or invalid JSON",
            ) from exc
        if type(payload) is not dict:
            raise _ChiefEngineerPortfolioAuthorityError(
                "chief_engineer.project_completion_catalog_snapshot_invalid",
                "catalog_contract.json must be an exact JSON object",
            )
        return payload

    def _chief_engineer_verification_command_authority(
        self,
        committed_pm_tasks: list[dict[str, Any]],
    ) -> tuple[VerificationCommandAuthorityV1, ...]:
        """Read exact structured verifier argv/cwd authority from the committed PM document.

        Natural-language acceptance criteria and generic verifier-policy modalities are deliberately
        not command authority.  Missing or malformed structured rows fail before any CE provider call.
        """

        authorities: list[VerificationCommandAuthorityV1] = []
        seen_hashes: set[str] = set()
        for index, task in enumerate(committed_pm_tasks, start=1):
            task_id = self._task_id(task, index)
            if "verification_commands" not in task:
                raise _ChiefEngineerPortfolioAuthorityError(
                    "chief_engineer.project_completion_verification_command_authority_missing",
                    f"committed PM task {task_id!r} is missing verification_commands",
                )
            rows = task.get("verification_commands")
            if type(rows) is not list:
                raise _ChiefEngineerPortfolioAuthorityError(
                    "chief_engineer.project_completion_verification_command_authority_invalid",
                    f"committed PM task {task_id!r} verification_commands must be a JSON array",
                )
            for row_index, raw_row in enumerate(rows):
                if not isinstance(raw_row, Mapping) or set(raw_row) != {"modality", "argv", "cwd"}:
                    raise _ChiefEngineerPortfolioAuthorityError(
                        "chief_engineer.project_completion_verification_command_authority_invalid",
                        f"committed PM task {task_id!r} verification_commands[{row_index}] "
                        "must contain exactly modality, argv, cwd",
                    )
                try:
                    authority = VerificationCommandAuthorityV1(
                        task_id=task_id,
                        modality=raw_row["modality"],
                        argv=raw_row["argv"],
                        cwd=raw_row["cwd"],
                    )
                except (TypeError, ValueError) as exc:
                    raise _ChiefEngineerPortfolioAuthorityError(
                        "chief_engineer.project_completion_verification_command_authority_invalid",
                        f"committed PM task {task_id!r} verification_commands[{row_index}] is invalid: {exc}",
                    ) from exc
                if authority.authority_hash in seen_hashes:
                    continue
                seen_hashes.add(authority.authority_hash)
                authorities.append(authority)
        if not authorities:
            raise _ChiefEngineerPortfolioAuthorityError(
                "chief_engineer.project_completion_verification_command_authority_missing",
                "committed PM task set contains no structured verification command authority",
            )
        if not any(authority.modality in {"build", "test", "lint"} for authority in authorities):
            raise _ChiefEngineerPortfolioAuthorityError(
                "chief_engineer.project_completion_delivery_verifier_authority_missing",
                "committed PM task set requires at least one build/test/lint command authority",
            )
        return tuple(sorted(authorities, key=lambda item: item.authority_hash))

    async def _load_chief_engineer_portfolio_authority(
        self,
        *,
        run: FactoryRun,
        pm_tasks: list[dict[str, Any]],
        portfolio_tasks: tuple[ChiefEngineerPortfolioTaskV1, ...],
    ) -> _ChiefEngineerPortfolioAuthorityV1:
        """Bind CE completion authority to committed PM and verifier-policy evidence."""

        catalog_snapshot = self._chief_engineer_catalog_snapshot()
        catalog_project_id = str(catalog_snapshot.get("project_id") or "").strip()
        # ``FactoryConfig.name`` is a human-facing run label (the HTTP/bench
        # caller commonly sets it to ``Factory Run - pm``), not project
        # identity authority.  Prefer the catalog identity captured after PM
        # artifact revalidation; retain the display-name fallback only for
        # legacy/non-catalog workspaces.
        project_id = catalog_project_id or str(run.config.name)
        if not project_id or project_id != project_id.strip():
            raise RuntimeError("chief_engineer_project_completion_project_id_missing")
        if any(unicodedata.category(character).startswith("C") for character in project_id):
            raise RuntimeError("chief_engineer_project_completion_project_id_invalid")
        if len(project_id.encode("utf-8")) > 128:
            raise RuntimeError("chief_engineer_project_completion_project_id_invalid")
        expected_task_ids = tuple(sorted(task.task_id for task in portfolio_tasks))
        if not expected_task_ids:
            raise RuntimeError("chief_engineer_project_completion_task_set_missing")

        runtime_root = Path(resolve_storage_roots(str(self.workspace)).runtime_root)
        factory_store = FactoryStore(runtime_root / "factory", create_root=False)
        events = await factory_store.get_authoritative_events(run.id)
        persistence = reduce_factory_stage_persistence(events, factory_run_id=run.id)
        pm_commits = tuple(commit for commit in persistence.commits if commit.stage == "pm_planning")
        if not pm_commits:
            raise RuntimeError("chief_engineer_project_completion_pm_commit_missing")
        pm_commit = pm_commits[-1]
        pm_stage_event = next(
            (
                event
                for event in events
                if event.get("type") == "stage_completed"
                and event.get("event_id") == pm_commit.stage_completed_event_id
            ),
            None,
        )
        if pm_stage_event is None:
            raise RuntimeError("chief_engineer_project_completion_pm_stage_event_missing")
        proof = revalidate_pm_stage_artifact_binding(
            factory_store=factory_store,
            factory_run_id=run.id,
            stage_event=pm_stage_event,
        )
        committed_pm_tasks_raw = proof.document.get("tasks")
        if type(committed_pm_tasks_raw) is not list or committed_pm_tasks_raw != pm_tasks:
            raise RuntimeError("chief_engineer_project_completion_pm_document_mismatch")
        committed_pm_tasks = cast(list[dict[str, Any]], committed_pm_tasks_raw)
        committed_portfolio_tasks = self._chief_engineer_portfolio_tasks(committed_pm_tasks)
        if committed_portfolio_tasks != portfolio_tasks:
            raise RuntimeError("chief_engineer_project_completion_pm_path_authority_mismatch")
        if proof.task_ids != expected_task_ids:
            raise RuntimeError(
                "chief_engineer_project_completion_pm_task_set_mismatch:"
                f"expected={list(expected_task_ids)}:actual={list(proof.task_ids)}"
            )

        target_files = tuple(path for task in portfolio_tasks for path in task.target_files)
        acceptance_criteria = tuple(
            criterion
            for task in committed_pm_tasks
            for criterion in self._task_string_list(task, "acceptance", "acceptance_criteria")
        )
        verification_command_authority = self._chief_engineer_verification_command_authority(committed_pm_tasks)
        try:
            catalog_snapshot_hash = project_completion_catalog_snapshot_hash(catalog_snapshot)
        except (TypeError, ValueError) as exc:
            raise _ChiefEngineerPortfolioAuthorityError(
                "chief_engineer.project_completion_catalog_snapshot_invalid",
                str(exc),
            ) from exc
        project_kind_authority = self._chief_engineer_project_kind_authority(
            project_id=project_id,
            run_id=run.id,
            pm_contract_hash=proof.item.canonical_json_sha256,
            catalog_snapshot=catalog_snapshot,
            catalog_snapshot_hash=catalog_snapshot_hash,
        )
        policy = dict(
            compile_evidence_policy(
                CompileEvidencePolicyCommandV1(
                    workspace=str(self.workspace),
                    task_id=f"CE-PORTFOLIO-{run.id}",
                    run_id=run.id,
                    target_files=target_files,
                    acceptance_criteria=acceptance_criteria,
                    explicit_required_modalities=("command",),
                )
            ).policy
        )
        verifier_policy_hash = str(policy.get("policy_hash") or "")
        if re.fullmatch(r"[0-9a-f]{64}", verifier_policy_hash) is None:
            raise RuntimeError("chief_engineer_project_completion_verifier_policy_hash_invalid")
        verifier_policy_snapshot_hash = project_completion_verifier_policy_snapshot_hash(policy)
        return _ChiefEngineerPortfolioAuthorityV1(
            project_id=project_id,
            pm_stage_event_id=str(pm_commit.stage_completed_event_id),
            pm_contract_hash=proof.item.canonical_json_sha256,
            pm_task_ids=proof.task_ids,
            catalog_snapshot=catalog_snapshot,
            catalog_snapshot_hash=catalog_snapshot_hash,
            project_kind_authority=project_kind_authority,
            verifier_policy_hash=verifier_policy_hash,
            verifier_policy=policy,
            verifier_policy_snapshot_hash=verifier_policy_snapshot_hash,
            verification_command_authority=verification_command_authority,
        )

    @staticmethod
    def _chief_engineer_structured_output_contract(
        portfolio_task_ids: tuple[str, ...],
    ) -> RoleStructuredOutputContractV1:
        """Build the caller-owned provider schema for one CE portfolio."""

        task_plan_properties = {
            task_id: {
                "type": "object",
                "minProperties": 1,
                "additionalProperties": True,
            }
            for task_id in portfolio_task_ids
        }
        nullable_string = {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]}
        completion_contract_schema = {
            "type": "object",
            "properties": {
                "obligations": {
                    "type": "object",
                    "properties": {
                        "artifacts": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "obligation_id": {"type": "string", "minLength": 1},
                                    "path": {"type": "string", "minLength": 1},
                                    "semantic_role": {
                                        "type": "string",
                                        "enum": [
                                            "source",
                                            "test",
                                            "manifest",
                                            "config",
                                            "docs",
                                            "entrypoint",
                                            "assets",
                                        ],
                                    },
                                    "applicability": {
                                        "type": "string",
                                        "enum": ["required", "optional", "not_applicable"],
                                    },
                                    "owner_task_id": nullable_string,
                                },
                                "required": [
                                    "obligation_id",
                                    "path",
                                    "semantic_role",
                                    "applicability",
                                    "owner_task_id",
                                ],
                                "additionalProperties": False,
                            },
                        },
                        "entrypoints": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "obligation_id": {"type": "string", "minLength": 1},
                                    "kind": {
                                        "type": "string",
                                        "enum": ["cli", "web", "api", "library"],
                                    },
                                    "applicability": {
                                        "type": "string",
                                        "enum": ["required", "optional", "not_applicable"],
                                    },
                                    "owner_task_id": nullable_string,
                                    "source_path": nullable_string,
                                    "runtime_path": nullable_string,
                                    "command": nullable_string,
                                },
                                "required": [
                                    "obligation_id",
                                    "kind",
                                    "applicability",
                                    "owner_task_id",
                                    "source_path",
                                    "runtime_path",
                                    "command",
                                ],
                                "additionalProperties": False,
                            },
                        },
                        "verification": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "obligation_id": {"type": "string", "minLength": 1},
                                    "modality": {
                                        "type": "string",
                                        "enum": ["environment_prep", "build", "test", "lint", "entrypoint"],
                                    },
                                    "command_authority_hash": nullable_string,
                                    "applicability": {
                                        "type": "string",
                                        "enum": ["required", "optional", "not_applicable"],
                                    },
                                    "owner_task_id": nullable_string,
                                    "covers_obligation_ids": {
                                        "type": "array",
                                        "items": {"type": "string", "minLength": 1},
                                    },
                                },
                                "required": [
                                    "obligation_id",
                                    "modality",
                                    "command_authority_hash",
                                    "applicability",
                                    "owner_task_id",
                                    "covers_obligation_ids",
                                ],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["artifacts", "entrypoints", "verification"],
                    "additionalProperties": False,
                },
            },
            "required": ["obligations"],
            "additionalProperties": False,
        }
        return RoleStructuredOutputContractV1(
            schema_name="chief_engineer_blueprint_portfolio",
            description=(
                "Submit the complete Chief Engineer portfolio for every validated PM task id, "
                "including the shared project interface contract."
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "construction_plan": {
                        "type": "object",
                        "properties": {
                            "task_plans": {
                                "type": "object",
                                "properties": task_plan_properties,
                                "required": list(portfolio_task_ids),
                                "additionalProperties": False,
                            },
                            "project_interface_contract": {
                                "type": "object",
                                "properties": {
                                    "provider_declarations": {
                                        "type": "array",
                                        "items": {"type": "object"},
                                    },
                                    "consumer_declarations": {
                                        "type": "array",
                                        "items": {"type": "object"},
                                    },
                                },
                                "required": [
                                    "provider_declarations",
                                    "consumer_declarations",
                                ],
                                "additionalProperties": False,
                            },
                        },
                        "required": [
                            "task_plans",
                            "project_interface_contract",
                        ],
                        "additionalProperties": True,
                    },
                    "scope_for_apply": {"type": "array", "items": {}},
                    "risk_flags": {"type": "array", "items": {}},
                    "project_completion_contract": completion_contract_schema,
                },
                "required": [
                    "construction_plan",
                    "project_completion_contract",
                    "risk_flags",
                ],
                "additionalProperties": False,
            },
        )

    def _claim_chief_engineer_execution_attempt(
        self,
        *,
        run_id: str,
        portfolio_task_id: str,
        objective: str,
        lease_budget: _ChiefEngineerExecutionAttemptLeaseBudget,
    ) -> tuple[int, TaskRuntimeExecutionAttemptIdentityV1]:
        """Claim TaskRuntime's durable owner for one CE portfolio execution.

        TaskRuntime is the sole source of execution-attempt identity. Replaying
        an active claim renews its persisted session, while a requeued claim
        receives a new session from TaskRuntime. Factory neither derives an
        identity from run/task identifiers nor generates UUIDs. The lease is
        derived only from the already-admitted CE execution timeout.
        """

        task_runtime = TaskRuntimeService(str(self.workspace))
        row = task_runtime.ensure_task_row(
            external_task_id=portfolio_task_id,
            subject="Chief Engineer portfolio review",
            description=objective,
            metadata={
                "factory_run_id": run_id,
                "factory_stage": "chief_engineer_review",
                "role": "chief_engineer",
                "execution_identity_required": True,
            },
        )
        task_row_id = task_runtime.normalize_task_id(row.get("id"))
        if task_row_id is None:
            raise RuntimeError("chief_engineer_execution_attempt_task_id_invalid")

        binding = bind_runtime_task_to_factory_run(
            BindRuntimeTaskToFactoryRunCommandV1(
                workspace=str(self.workspace),
                task_id=portfolio_task_id,
                factory_run_id=run_id,
            )
        )
        if not binding.ok:
            raise RuntimeError(f"chief_engineer_execution_attempt_binding_failed:{binding.code}")

        claim = task_runtime.claim_execution(
            task_row_id,
            worker_id="chief_engineer",
            role_id="chief_engineer",
            run_id=run_id,
            lease_ttl_seconds=lease_budget.lease_ttl_seconds,
            selection_source="factory_stage_executor.chief_engineer_portfolio_review",
            external_task_id=portfolio_task_id,
            context_summary=objective,
            metadata={
                "factory_run_id": run_id,
                "factory_stage": "chief_engineer_review",
                "execution_identity_required": True,
            },
        )
        session = claim.get("session") if isinstance(claim, dict) else None
        attempt_record = claim.get("execution_attempt") if isinstance(claim, dict) else None
        if (
            not isinstance(session, Mapping)
            or not isinstance(attempt_record, Mapping)
            or not bool(claim.get("success"))
        ):
            reason = str(claim.get("reason") or "unknown") if isinstance(claim, dict) else "invalid_claim_result"
            raise RuntimeError(f"chief_engineer_execution_attempt_claim_failed:{reason}")
        try:
            execution_attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(attempt_record)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"chief_engineer_execution_attempt_record_invalid:{type(exc).__name__}:{exc}") from exc
        if (
            execution_attempt.workspace != str(self.workspace)
            or execution_attempt.task_id != task_row_id
            or execution_attempt.external_task_id != portfolio_task_id
            or execution_attempt.role_id != "chief_engineer"
            or execution_attempt.run_id != run_id
            or execution_attempt.session_id != str(session.get("session_id") or "").strip()
            or execution_attempt.attempt != session.get("attempt")
        ):
            raise RuntimeError("chief_engineer_execution_attempt_session_mismatch")
        return task_row_id, execution_attempt

    def _settle_chief_engineer_execution_attempt(
        self,
        *,
        task_id: int,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        stage_status: str,
        summary: str,
    ) -> None:
        """Complete successful CE work or suspend it for a new TaskRuntime claim."""

        task_runtime = TaskRuntimeService(str(self.workspace))
        outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1 = (
            "completed" if stage_status == "success" else "suspended"
        )
        if task_id != execution_attempt.task_id:
            raise RuntimeError("chief_engineer_execution_attempt_task_id_mismatch")
        result = task_runtime.settle_execution_attempt(
            SettleTaskRuntimeExecutionAttemptCommandV1(
                workspace=execution_attempt.workspace,
                identity=execution_attempt,
                outcome=outcome,
                summary=summary,
                lock_timeout_seconds=5.0,
                metadata={"factory_stage": "chief_engineer_review"},
            )
        )
        if not bool(result.get("success")):
            reason = str(result.get("reason") or "unknown")
            raise RuntimeError(f"chief_engineer_execution_attempt_settlement_failed:{reason}")

    @staticmethod
    def _chief_engineer_portfolio_output_errors(
        payload: Mapping[str, Any],
        *,
        task_ids: tuple[str, ...],
    ) -> list[str]:
        """Validate the nested project-level CE output contract."""

        return ce_evidence.chief_engineer_portfolio_output_errors(payload, task_ids=task_ids)

    def _settle_chief_engineer_execution_attempt_after_exception(
        self,
        *,
        lease_scope: _ChiefEngineerExecutionAttemptLeaseScope,
        stage_status: str,
        summary: str,
        preserved_error: BaseException,
    ) -> None:
        should_settle, heartbeat_failure = lease_scope.begin_settlement()
        if not should_settle or lease_scope.task_id is None or lease_scope.execution_attempt is None:
            if heartbeat_failure is not None:
                logger.error(
                    "Chief Engineer exceptional-path settlement blocked by lease keeper: "
                    "run_id=%s task_id=%s reason=%s error_type=%s preserved_error_type=%s",
                    lease_scope.execution_attempt.run_id if lease_scope.execution_attempt is not None else "",
                    lease_scope.task_id,
                    heartbeat_failure.reason,
                    heartbeat_failure.error_type,
                    type(preserved_error).__name__,
                )
            return
        try:
            self._settle_chief_engineer_execution_attempt(
                task_id=lease_scope.task_id,
                execution_attempt=lease_scope.execution_attempt,
                stage_status=stage_status,
                summary=summary,
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            failure_kind = (
                "Chief Engineer cancellation settlement failed"
                if stage_status == "cancelled"
                else "Chief Engineer exceptional-path settlement failed"
            )
            logger.exception(
                "%s: run_id=%s task_id=%s session_id=%s preserved_error_type=%s",
                failure_kind,
                lease_scope.execution_attempt.run_id,
                lease_scope.task_id,
                lease_scope.execution_attempt.session_id,
                type(preserved_error).__name__,
            )

    @staticmethod
    def _chief_engineer_schema_repair_objective(
        *,
        prior_result: RoleExecutionResultV1,
        portfolio_task_ids: tuple[str, ...],
    ) -> str:
        """Build one bounded CE reconstruction objective without replaying corrupt bytes."""

        prior_error = str(prior_result.error_message or prior_result.error_code or "output validation failed").strip()[
            :_CHIEF_ENGINEER_SCHEMA_REPAIR_ERROR_MAX_CHARS
        ]
        prior_output = str(prior_result.output or "")
        prior_output_sha256 = hashlib.sha256(prior_output.encode("utf-8")).hexdigest()
        return (
            "Reconstruct a fresh, concise Chief Engineer portfolio as exactly one valid JSON object from the "
            "authoritative validated PM contracts, target_files, and scope_paths already attached to this request. "
            "Do not copy, quote, continue, or textually repair the previous malformed output; its bytes are "
            "intentionally excluded so corrupt placeholders and duplicated stream fragments cannot be replayed. "
            "Preserve every validated PM task id and remain inside PM-authoritative scope. Return JSON only: no "
            "markdown, prose, SESSION_PATCH wrapper, placeholder syntax, angle-bracket metavariables, comments, or "
            "trailing fragments. Keep the response under 8,000 output tokens.\n\n"
            "Required shape:\n"
            "- required top-level keys: construction_plan, project_completion_contract, risk_flags\n"
            "- construction_plan.task_plans: object keyed by every validated PM task id\n"
            "- construction_plan.project_interface_contract: object containing provider_declarations and "
            "consumer_declarations arrays\n"
            "- every task plan: concrete files, public interfaces, dependencies, implementation phases, "
            "verification evidence, and handoff criteria\n"
            "- project_completion_contract.obligations: object containing non-empty artifacts, entrypoints, "
            "and verification arrays that follow the active provider tool schema and PM authority\n"
            "- risk_flags: array; optional scope_for_apply, when present: array\n\n"
            f"Validated PM task ids: {json.dumps(list(portfolio_task_ids), ensure_ascii=False)}\n"
            f"Prior validation failure: {prior_error}\n"
            f"Excluded prior output SHA-256: {prior_output_sha256}\n"
            f"Excluded prior output UTF-8 character count: {len(prior_output)}"
        )

    def _settle_chief_engineer_attempt_before_schema_repair(
        self,
        *,
        lease_scope: _ChiefEngineerExecutionAttemptLeaseScope,
    ) -> None:
        """Suspend the invalid primary CE attempt before claiming its repair task."""

        should_settle, heartbeat_failure = lease_scope.begin_settlement()
        if not should_settle or lease_scope.task_id is None or lease_scope.execution_attempt is None:
            reason = heartbeat_failure.error_message if heartbeat_failure is not None else "settlement_not_started"
            raise RuntimeError(f"chief_engineer_schema_repair_primary_settlement_blocked:{reason}")
        self._settle_chief_engineer_execution_attempt(
            task_id=lease_scope.task_id,
            execution_attempt=lease_scope.execution_attempt,
            stage_status="failed",
            summary="chief_engineer_output_validation_failed_before_schema_repair",
        )

    async def _run_chief_engineer_schema_repair(
        self,
        *,
        run: FactoryRun,
        authority_port: FactoryRoleEvidenceAuthorityPort,
        authority_binding: FactoryRoleEvidenceAuthorityBindingV1,
        prior_result: RoleExecutionResultV1,
        portfolio_context: Mapping[str, Any],
        portfolio_task_ids: tuple[str, ...],
        deadline_decision: FactoryDeadlineAdmissionV1,
    ) -> RoleExecutionResultV1:
        """Run exactly one separately claimed, deadline-admitted CE schema repair."""

        repair_scope = _ChiefEngineerExecutionAttemptLeaseScope()
        repair_task_id = f"CE-PORTFOLIO-{run.id}-SCHEMA-REPAIR"
        repair_timeout_seconds = int(deadline_decision.timeout_seconds)
        repair_lease_budget = self._chief_engineer_execution_attempt_lease_budget(repair_timeout_seconds)
        repair_objective = self._chief_engineer_schema_repair_objective(
            prior_result=prior_result,
            portfolio_task_ids=portfolio_task_ids,
        )
        prior_error = str(prior_result.error_message or prior_result.error_code or "output validation failed").strip()[
            :_CHIEF_ENGINEER_SCHEMA_REPAIR_ERROR_MAX_CHARS
        ]
        prior_output = str(prior_result.output or "")
        repair_failure_feedback = {
            "schema_version": "factory.chief_engineer_schema_repair.failure_evidence.v1",
            "failure_class": self._ce_schema_repair_failure_class(prior_result),
            "failure_stage": "chief_engineer_review",
            "detail": prior_error,
            "prior_output_sha256": hashlib.sha256(prior_output.encode("utf-8")).hexdigest(),
            "prior_output_chars": len(prior_output),
            "evidence_refs": [],
        }
        try:
            runtime_task_id, execution_attempt = self._claim_chief_engineer_execution_attempt(
                run_id=run.id,
                portfolio_task_id=repair_task_id,
                objective=repair_objective,
                lease_budget=repair_lease_budget,
            )
            repair_scope.bind_claim(task_id=runtime_task_id, execution_attempt=execution_attempt)
            repair_scope.start_keeper(
                _ChiefEngineerExecutionAttemptLeaseKeeper(
                    workspace=str(self.workspace),
                    task_id=runtime_task_id,
                    execution_attempt=execution_attempt,
                    budget=repair_lease_budget,
                )
            )
            repair_context = deepcopy(dict(portfolio_context))
            repair_context.update(
                {
                    "chief_engineer_schema_repair": True,
                    "chief_engineer_schema_repair_of_task_id": f"CE-PORTFOLIO-{run.id}",
                    "chief_engineer_prior_error_code": str(prior_result.error_code or ""),
                    "chief_engineer_prior_error_message": prior_error,
                    "failure_feedback": repair_failure_feedback,
                    "chief_engineer_deadline_decision": deadline_decision.to_dict(),
                    "chief_engineer_llm_timeout_seconds": repair_timeout_seconds,
                    "llm_call_timeout_seconds": repair_timeout_seconds,
                    "request_timeout_seconds": repair_timeout_seconds,
                    "temperature": 0.0,
                    "llm_max_tokens": _CHIEF_ENGINEER_SCHEMA_REPAIR_MAX_TOKENS,
                    "reasoning_budget_tokens": _CHIEF_ENGINEER_SCHEMA_REPAIR_REASONING_BUDGET_TOKENS,
                    "response_format_mode": "json",
                    "chief_engineer_json_contract_required": True,
                    "chief_engineer_portfolio_required": True,
                }
            )
            command = ExecuteRoleTaskCommandV1(
                role="chief_engineer",
                task_id=repair_task_id,
                workspace=str(self.workspace),
                objective=repair_objective,
                run_id=run.id,
                stream=True,
                context=repair_context,
                timeout_seconds=repair_timeout_seconds,
                execution_attempt=execution_attempt,
                structured_output_contract=self._chief_engineer_structured_output_contract(portfolio_task_ids),
                metadata={
                    "pm_task_contract": dict(repair_context["pm_task_contract"]),
                    "pm_task_contracts": list(repair_context["pm_task_contracts"]),
                    "target_files": list(repair_context["target_files"]),
                    "scope_paths": list(repair_context["scope_paths"]),
                    "source": "factory_stage_executor.chief_engineer_schema_repair",
                    "schema_repair_of_task_id": f"CE-PORTFOLIO-{run.id}",
                    "cognitive_runtime_mode": "off",
                    "cognitive_runtime_enabled": False,
                    "cognitive_runtime_required": False,
                    "llm_call_timeout_seconds": repair_timeout_seconds,
                    "validate_output": True,
                    "max_retries": 0,
                    "temperature": 0.0,
                    "llm_max_tokens": _CHIEF_ENGINEER_SCHEMA_REPAIR_MAX_TOKENS,
                    "reasoning_budget_tokens": _CHIEF_ENGINEER_SCHEMA_REPAIR_REASONING_BUDGET_TOKENS,
                    "response_format_mode": "json",
                    "chief_engineer_json_contract_required": True,
                    "chief_engineer_portfolio_required": True,
                },
            )
            result = cast(
                RoleExecutionResultV1,
                await self._call_with_factory_role_evidence_authority(
                    authority_port,
                    "chief_engineer",
                    lambda: RoleRuntimeService().execute_role_task(command),
                    authority_binding=authority_binding,
                ),
            )
            should_settle, heartbeat_failure = repair_scope.begin_settlement()
            if not should_settle or repair_scope.execution_attempt is None:
                reason = heartbeat_failure.error_message if heartbeat_failure is not None else "settlement_not_started"
                raise RuntimeError(f"chief_engineer_schema_repair_settlement_blocked:{reason}")
            self._settle_chief_engineer_execution_attempt(
                task_id=runtime_task_id,
                execution_attempt=repair_scope.execution_attempt,
                stage_status="success" if result.ok else "failed",
                summary=(
                    "chief_engineer_schema_repair_completed"
                    if result.ok
                    else str(result.error_code or "chief_engineer_schema_repair_failed")
                ),
            )
            return result
        except asyncio.CancelledError as exc:
            self._settle_chief_engineer_execution_attempt_after_exception(
                lease_scope=repair_scope,
                stage_status="cancelled",
                summary="chief_engineer_schema_repair_cancelled",
                preserved_error=exc,
            )
            raise
        except BaseException as exc:
            self._settle_chief_engineer_execution_attempt_after_exception(
                lease_scope=repair_scope,
                stage_status="failed",
                summary=f"chief_engineer_schema_repair_exception:{type(exc).__name__}",
                preserved_error=exc,
            )
            raise
        finally:
            repair_scope.stop_keeper()

    async def _execute_chief_engineer_review(self, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        """Run CE review under one claim-bound heartbeat and settlement scope."""

        lease_scope = _ChiefEngineerExecutionAttemptLeaseScope()
        try:
            return await self._execute_chief_engineer_review_with_attempt_lease(
                run,
                context,
                lease_scope,
            )
        except asyncio.CancelledError as exc:
            self._settle_chief_engineer_execution_attempt_after_exception(
                lease_scope=lease_scope,
                stage_status="cancelled",
                summary="chief_engineer_portfolio_review_cancelled",
                preserved_error=exc,
            )
            raise
        except BaseException as exc:
            self._settle_chief_engineer_execution_attempt_after_exception(
                lease_scope=lease_scope,
                stage_status="failed",
                summary=f"chief_engineer_portfolio_review_exception:{type(exc).__name__}",
                preserved_error=exc,
            )
            raise
        finally:
            lease_scope.stop_keeper()

    async def _execute_chief_engineer_review_with_attempt_lease(
        self,
        run: FactoryRun,
        context: dict[str, Any],
        lease_scope: _ChiefEngineerExecutionAttemptLeaseScope,
    ) -> StageResult:
        """Create one CE project portfolio and project task-level handoffs."""

        logger.info("Executing Chief Engineer project review for run %s", run.id)
        authority_port = self._factory_role_evidence_cutoff_port(context)
        synced_plan_source = self._ensure_pm_plan_contract_available()
        self._enrich_pm_plan_contract_artifact("tasks/plan.json")
        stage_signals: list[dict[str, Any]] = []
        blueprint_rows: list[dict[str, Any]] = []
        portfolio: ChiefEngineerBlueprintPortfolioV1 | None = None
        ce_evidence: dict[str, Any] = {}
        llm_call_count = 0
        cancelled_by_factory = False
        ce_runtime_task_id: int | None = None
        ce_execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None

        if synced_plan_source:
            stage_signals.append(
                {
                    "code": "chief_engineer.plan_contract_synced_from_workspace_mirror",
                    "severity": "info",
                    "detail": "Copied PM workspace plan mirror into runtime tasks/plan.json before blueprint review.",
                    "source_path": synced_plan_source,
                }
            )

        pm_tasks = self._load_pm_plan_tasks("tasks/plan.json")
        if not pm_tasks:
            stage_signals.append(
                {
                    "code": "chief_engineer.plan_missing",
                    "severity": "error",
                    "detail": "tasks/plan.json missing or empty tasks array",
                }
            )

        cancel_event = self._resolve_cancel_event(context)
        abort_checker = self._resolve_abort_checker(context)
        if pm_tasks and cancel_event is not None and cancel_event.is_set():
            cancelled_by_factory = True
            stage_signals.append(
                {
                    "code": "chief_engineer.cancelled_before_portfolio",
                    "severity": "warning",
                    "detail": "Factory cancel event was set before the CE portfolio request.",
                }
            )
        if pm_tasks and not cancelled_by_factory and abort_checker is not None:
            abort_reason = ""
            with contextlib.suppress(AttributeError, OSError, RuntimeError, TypeError, ValueError):
                abort_reason = str(await abort_checker() or "").strip()
            if abort_reason:
                cancelled_by_factory = True
                stage_signals.append(
                    {
                        "code": "chief_engineer.cancelled_before_portfolio",
                        "severity": "warning",
                        "detail": f"Factory abort was requested before CE portfolio review: {abort_reason}",
                        "abort_reason": abort_reason,
                    }
                )

        portfolio_tasks: tuple[ChiefEngineerPortfolioTaskV1, ...] = ()
        portfolio_context: dict[str, Any] = {}
        portfolio_authority: _ChiefEngineerPortfolioAuthorityV1 | None = None
        deadline_decision: FactoryDeadlineAdmissionV1 | None = None
        if pm_tasks and not cancelled_by_factory:
            try:
                start_metadata_raw = context.get("metadata")
                start_metadata = start_metadata_raw if isinstance(start_metadata_raw, Mapping) else {}
                local_rework_raw = start_metadata.get("chief_engineer_local_rework_evidence")
                local_rework_evidence = local_rework_raw if isinstance(local_rework_raw, Mapping) else None
                portfolio_tasks = self._chief_engineer_portfolio_tasks(pm_tasks)
                portfolio_context = self._chief_engineer_portfolio_context(
                    pm_tasks,
                    run_id=run.id,
                    failure_feedback=local_rework_evidence,
                )
            except (TypeError, ValueError) as exc:
                stage_signals.append(
                    {
                        "code": "chief_engineer.portfolio_contract_invalid",
                        "severity": "error",
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )

        if portfolio_tasks:
            try:
                portfolio_authority = await self._load_chief_engineer_portfolio_authority(
                    run=run,
                    pm_tasks=pm_tasks,
                    portfolio_tasks=portfolio_tasks,
                )
                portfolio_context["project_completion_authority"] = {
                    "project_id": portfolio_authority.project_id,
                    "run_id": run.id,
                    "pm_contract_hash": portfolio_authority.pm_contract_hash,
                    "covered_task_ids": list(portfolio_authority.pm_task_ids),
                    "project_kind_authority": portfolio_authority.project_kind_authority.to_dict(),
                    "completion_predicate_version": "polaris.project_completion_predicate.v1",
                    "verifier_policy_hash": portfolio_authority.verifier_policy_hash,
                    "verifier_policy": dict(portfolio_authority.verifier_policy),
                    "verifier_policy_snapshot_hash": portfolio_authority.verifier_policy_snapshot_hash,
                    "verification_command_authority": [
                        item.to_dict() for item in portfolio_authority.verification_command_authority
                    ],
                    "authority": "factory_committed_pm_and_verifier_policy",
                    "llm_may_override": False,
                }
            except _ChiefEngineerPortfolioAuthorityError as exc:
                stage_signals.append(
                    {
                        "code": exc.code,
                        "severity": "error",
                        "detail": str(exc),
                    }
                )
                portfolio_tasks = ()
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                stage_signals.append(
                    {
                        "code": "chief_engineer.project_completion_authority_invalid",
                        "severity": "error",
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )
                portfolio_tasks = ()

        ce_result: RoleExecutionResultV1 | None = None
        if portfolio_tasks:
            dependency_schedule = build_task_dependency_schedule(pm_tasks)
            requested_timeout_seconds = self._chief_engineer_llm_timeout_seconds(context)
            deadline_decision = self._chief_engineer_deadline_projection_decision(
                context,
                requested_timeout_seconds=requested_timeout_seconds,
                dependency_schedule=dependency_schedule,
            )
            deadline_payload = deadline_decision.to_dict()
            if deadline_decision.disposition is FactoryDeadlineDispositionV1.BLOCK:
                stage_signals.append(
                    {
                        "code": "chief_engineer.deadline_admission_blocked",
                        "severity": "error",
                        "detail": (
                            "The CE portfolio request was not admitted because the remaining Factory lease "
                            "cannot preserve all mandatory Director, QA, finalization, and safety budgets."
                        ),
                        "deadline_decision": deadline_payload,
                        "reason": deadline_decision.reason,
                    }
                )
            else:
                ce_timeout_seconds = int(deadline_decision.timeout_seconds)
                ce_lease_budget = self._chief_engineer_execution_attempt_lease_budget(ce_timeout_seconds)
                portfolio_context.update(
                    {
                        "cognitive_runtime_mode": "off",
                        "cognitive_runtime_enabled": False,
                        "cognitive_runtime_required": False,
                        "suppress_working_memory_contract": True,
                        "suppress_tool_policy_prompt": True,
                        "disable_internal_tool_rounds": True,
                        "delivery_mode": "analyze_only",
                        "temperature": 0.2,
                        "response_format_mode": "json",
                        "chief_engineer_json_contract_required": True,
                        "chief_engineer_portfolio_required": True,
                        "llm_max_tokens": chief_engineer_portfolio_output_tokens(len(portfolio_tasks)),
                        "reasoning_budget_tokens": _CHIEF_ENGINEER_PORTFOLIO_REASONING_BUDGET_TOKENS,
                        "chief_engineer_llm_timeout_seconds": ce_timeout_seconds,
                        "llm_call_timeout_seconds": ce_timeout_seconds,
                        "request_timeout_seconds": ce_timeout_seconds,
                        "chief_engineer_deadline_decision": deadline_payload,
                    }
                )
                portfolio_task_id = f"CE-PORTFOLIO-{run.id}"
                try:
                    objective = self._chief_engineer_portfolio_objective(pm_tasks)
                    ce_runtime_task_id, ce_execution_attempt = self._claim_chief_engineer_execution_attempt(
                        run_id=run.id,
                        portfolio_task_id=portfolio_task_id,
                        objective=objective,
                        lease_budget=ce_lease_budget,
                    )
                    lease_scope.bind_claim(
                        task_id=ce_runtime_task_id,
                        execution_attempt=ce_execution_attempt,
                    )
                    lease_scope.start_keeper(
                        _ChiefEngineerExecutionAttemptLeaseKeeper(
                            workspace=str(self.workspace),
                            task_id=ce_runtime_task_id,
                            execution_attempt=ce_execution_attempt,
                            budget=ce_lease_budget,
                        )
                    )
                    command = ExecuteRoleTaskCommandV1(
                        role="chief_engineer",
                        task_id=portfolio_task_id,
                        workspace=str(self.workspace),
                        objective=objective,
                        run_id=run.id,
                        stream=True,
                        context=portfolio_context,
                        timeout_seconds=ce_timeout_seconds,
                        execution_attempt=ce_execution_attempt,
                        structured_output_contract=self._chief_engineer_structured_output_contract(
                            tuple(task.task_id for task in portfolio_tasks)
                        ),
                        metadata={
                            "pm_task_contract": dict(portfolio_context["pm_task_contract"]),
                            "pm_task_contracts": list(portfolio_context["pm_task_contracts"]),
                            "target_files": list(portfolio_context["target_files"]),
                            "scope_paths": list(portfolio_context["scope_paths"]),
                            "source": "factory_stage_executor.chief_engineer_portfolio_review",
                            "cognitive_runtime_mode": "off",
                            "cognitive_runtime_enabled": False,
                            "cognitive_runtime_required": False,
                            "llm_call_timeout_seconds": ce_timeout_seconds,
                            "validate_output": True,
                            "max_retries": 0,
                            "temperature": 0.2,
                            "reasoning_budget_tokens": _CHIEF_ENGINEER_PORTFOLIO_REASONING_BUDGET_TOKENS,
                            "response_format_mode": "json",
                            "chief_engineer_json_contract_required": True,
                            "chief_engineer_portfolio_required": True,
                            "project_completion_authority": dict(portfolio_context["project_completion_authority"]),
                        },
                    )
                    llm_call_count = 1
                    authority_binding = authority_port.mint_authority_binding("chief_engineer")
                    ce_result = cast(
                        RoleExecutionResultV1,
                        await self._call_with_factory_role_evidence_authority(
                            authority_port,
                            "chief_engineer",
                            lambda: RoleRuntimeService().execute_role_task(command),
                            authority_binding=authority_binding,
                        ),
                    )
                    if self._ce_portfolio_result_allows_schema_repair(ce_result):
                        initial_evidence = self._ce_extract_llm_evidence(
                            ce_result,
                            task_id=portfolio_task_id,
                            run_id=run.id,
                        )
                        repair_signal: dict[str, Any] = {
                            "code": "chief_engineer.output_schema_repair_started",
                            "severity": "warning",
                            "detail": str(
                                ce_result.error_message
                                or "CE stream output failed validation; one bounded schema repair was requested."
                            ),
                            "task_id": portfolio_task_id,
                            "repair_task_id": f"{portfolio_task_id}-SCHEMA-REPAIR",
                            "prior_error_code": ce_result.error_code,
                            "prior_failure_class": self._ce_schema_repair_failure_class(ce_result),
                        }
                        self._attach_ce_llm_evidence(repair_signal, initial_evidence)
                        stage_signals.append(repair_signal)
                        try:
                            self._settle_chief_engineer_attempt_before_schema_repair(lease_scope=lease_scope)
                        except (OSError, RuntimeError, TypeError, ValueError) as exc:
                            stage_signals.append(
                                {
                                    "code": "chief_engineer.output_schema_repair_settlement_failed",
                                    "severity": "error",
                                    "detail": f"{type(exc).__name__}: {exc}",
                                    "task_id": portfolio_task_id,
                                }
                            )
                            ce_result = None
                        else:
                            deadline_decision = self._chief_engineer_deadline_projection_decision(
                                context,
                                requested_timeout_seconds=requested_timeout_seconds,
                                dependency_schedule=dependency_schedule,
                                output_tokens=_CHIEF_ENGINEER_SCHEMA_REPAIR_MAX_TOKENS,
                            )
                            if deadline_decision.disposition is FactoryDeadlineDispositionV1.BLOCK:
                                stage_signals.append(
                                    {
                                        "code": "chief_engineer.output_schema_repair_deadline_blocked",
                                        "severity": "error",
                                        "detail": (
                                            "The CE schema repair was not admitted because the remaining Factory "
                                            "lease cannot preserve mandatory downstream budgets."
                                        ),
                                        "task_id": portfolio_task_id,
                                        "deadline_decision": deadline_decision.to_dict(),
                                        "reason": deadline_decision.reason,
                                    }
                                )
                                ce_result = None
                            else:
                                ce_result = await self._run_chief_engineer_schema_repair(
                                    run=run,
                                    authority_port=authority_port,
                                    authority_binding=authority_binding,
                                    prior_result=ce_result,
                                    portfolio_context=portfolio_context,
                                    portfolio_task_ids=tuple(task.task_id for task in portfolio_tasks),
                                    deadline_decision=deadline_decision,
                                )
                                llm_call_count = 2
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — contain provider/http failures as stage signals
                    # Provider/network failures (e.g. aiohttp.ClientResponseError on
                    # HTTP 403 quota) must become stage signals, not uncaught escapes
                    # that strand the Factory run before execute_stage can finish.
                    stage_signals.append(
                        {
                            "code": "chief_engineer.llm_review_failed",
                            "severity": "error",
                            "detail": f"{type(exc).__name__}: {exc}",
                            "task_id": portfolio_task_id,
                            "exception_type": type(exc).__name__,
                        }
                    )
                    ce_result = None

        ce_llm_blueprint: dict[str, Any] = {}
        if ce_result is not None:
            portfolio_task_id = f"CE-PORTFOLIO-{run.id}"
            ce_evidence = self._ce_extract_llm_evidence(
                ce_result,
                task_id=portfolio_task_id,
                run_id=run.id,
            )
            ce_provider = str(ce_evidence.get("provider") or "unknown")
            ce_model = str(ce_evidence.get("model") or "unknown")
            raw_output = str(ce_result.output or "")
            ce_result_metadata = dict(ce_result.metadata or {})

            if not ce_result.ok:
                error_signal: dict[str, Any] = {
                    "code": "chief_engineer.llm_review_failed",
                    "severity": "error",
                    "detail": ce_result.error_message or ce_result.error_code or "CE portfolio LLM call failed",
                    "task_id": portfolio_task_id,
                    "provider": ce_provider,
                    "model": ce_model,
                    "recoverable": False,
                }
                self._attach_ce_llm_evidence(error_signal, ce_evidence)
                stage_signals.append(error_signal)
            elif ce_evidence.get("provider_model_unknown"):
                stage_signals.append(
                    {
                        "code": "chief_engineer.llm_evidence_missing",
                        "severity": "error",
                        "detail": str(ce_evidence.get("provider_model_unknown_reason") or ""),
                        "task_id": portfolio_task_id,
                        "provider": ce_provider,
                        "model": ce_model,
                        "provider_model_unknown": True,
                    }
                )
            else:
                audit_payload: dict[str, Any] = {
                    "provider": ce_provider,
                    "model": ce_model,
                    "cache_hit": bool(ce_evidence.get("cache_hit")),
                    "task_id": portfolio_task_id,
                    "run_id": run.id,
                    "portfolio_task_ids": [task.task_id for task in portfolio_tasks],
                }
                self._attach_ce_llm_evidence(audit_payload, ce_evidence)
                self._emit_audit_event("chief_engineer.llm_call", **audit_payload)
                missing_final_request_evidence = self._ce_missing_final_request_evidence(ce_evidence)
                if missing_final_request_evidence:
                    missing_signal: dict[str, Any] = {
                        "code": "chief_engineer.final_request_audit_missing",
                        "severity": "error",
                        "detail": (
                            "CE LLM result did not expose required final provider-request evidence: "
                            + ", ".join(missing_final_request_evidence)
                        ),
                        "task_id": portfolio_task_id,
                        "provider": ce_provider,
                        "model": ce_model,
                        "missing": missing_final_request_evidence,
                    }
                    self._attach_ce_llm_evidence(missing_signal, ce_evidence)
                    stage_signals.append(missing_signal)

            call_error_count = sum(
                1 for signal in stage_signals if str(signal.get("severity") or "").strip().lower() == "error"
            )
            structured_output = ce_result_metadata.get("structured_output")
            if isinstance(structured_output, Mapping):
                ce_llm_blueprint = dict(structured_output)
            elif "<SESSION_PATCH" in raw_output or "</SESSION_PATCH>" in raw_output:
                stage_signals.append(
                    {
                        "code": "chief_engineer.session_patch_output_rejected",
                        "severity": "error",
                        "detail": "CE returned SESSION_PATCH content instead of the required portfolio JSON object",
                        "task_id": portfolio_task_id,
                        "provider": ce_provider,
                        "model": ce_model,
                    }
                )
            elif call_error_count == 0:
                quality_result = QualityChecker(str(self.workspace)).validate_output(
                    raw_output,
                    cast(Any, SimpleNamespace(role_id="chief_engineer")),
                )
                if not quality_result.success:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.output_schema_invalid",
                            "severity": "error",
                            "detail": "; ".join(str(item) for item in quality_result.errors)
                            or "CE portfolio output failed schema validation",
                            "task_id": portfolio_task_id,
                            "provider": ce_provider,
                            "model": ce_model,
                            "quality_score": float(quality_result.quality_score),
                            "suggestions": list(quality_result.suggestions),
                        }
                    )
                elif isinstance(quality_result.data, Mapping):
                    ce_llm_blueprint = dict(quality_result.data)

            if ce_llm_blueprint and call_error_count == 0:
                if "scope_for_apply" not in ce_llm_blueprint:
                    omission_signal: dict[str, Any] = {
                        "code": "chief_engineer.scope_advisory_omitted",
                        "severity": "warning",
                        "detail": (
                            "CE omitted non-authoritative scope_for_apply advice; "
                            "PM target_files and scope_paths remain the apply authority."
                        ),
                        "task_id": portfolio_task_id,
                        "provider": ce_provider,
                        "model": ce_model,
                        "pm_authority_preserved": True,
                        "scope_expansion_allowed": False,
                    }
                    self._attach_ce_llm_evidence(omission_signal, ce_evidence)
                    stage_signals.append(omission_signal)
                output_errors = self._chief_engineer_portfolio_output_errors(
                    ce_llm_blueprint,
                    task_ids=tuple(task.task_id for task in portfolio_tasks),
                )
                if output_errors:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.portfolio_output_invalid",
                            "severity": "error",
                            "detail": "; ".join(output_errors),
                            "task_id": portfolio_task_id,
                            "provider": ce_provider,
                            "model": ce_model,
                            "errors": output_errors,
                        }
                    )
            elif not ce_llm_blueprint and call_error_count == 0:
                stage_signals.append(
                    {
                        "code": "chief_engineer.output_schema_invalid",
                        "severity": "error",
                        "detail": "CE portfolio output did not contain a JSON object",
                        "task_id": portfolio_task_id,
                        "provider": ce_provider,
                        "model": ce_model,
                    }
                )

        has_pre_projection_errors = any(
            str(signal.get("severity") or "").strip().lower() == "error" for signal in stage_signals
        )
        if portfolio_tasks and portfolio_authority is not None and ce_llm_blueprint and not has_pre_projection_errors:
            try:
                portfolio = build_chief_engineer_blueprint_portfolio(
                    BuildChiefEngineerBlueprintPortfolioCommandV1(
                        workspace=str(self.workspace),
                        run_id=run.id,
                        tasks=portfolio_tasks,
                        authority_carrier=_issue_chief_engineer_portfolio_authority_carrier(
                            workspace=str(self.workspace),
                            run_id=run.id,
                            project_id=portfolio_authority.project_id,
                            pm_stage_event_id=portfolio_authority.pm_stage_event_id,
                            pm_contract_hash=portfolio_authority.pm_contract_hash,
                            tasks=portfolio_tasks,
                            catalog_snapshot=portfolio_authority.catalog_snapshot,
                            catalog_snapshot_hash=portfolio_authority.catalog_snapshot_hash,
                            verifier_policy_hash=portfolio_authority.verifier_policy_hash,
                            verifier_policy_snapshot=portfolio_authority.verifier_policy,
                            verifier_policy_snapshot_hash=portfolio_authority.verifier_policy_snapshot_hash,
                            verification_command_authority=(portfolio_authority.verification_command_authority),
                        ),
                        llm_blueprint=ce_llm_blueprint,
                    )
                )
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                stage_signals.append(
                    {
                        "code": "chief_engineer.portfolio_generation_failed",
                        "severity": "error",
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )

        if portfolio is not None:
            portfolio_reference = portfolio.to_reference()
            portfolio_context_evidence = portfolio.to_task_blueprint_context()
            for index, task in enumerate(pm_tasks, start=1):
                task_id = self._task_id(task, index)
                objective = self._task_objective(task)
                task_constraints = self._task_blueprint_constraints(task)
                task_context = self._task_blueprint_context(task, run_id=run.id, index=index)
                task_context.update(portfolio_context_evidence)
                task_context["chief_engineer_blueprint_portfolio"] = dict(portfolio_reference)
                if deadline_decision is not None:
                    task_context["chief_engineer_deadline_decision"] = deadline_decision.to_dict()
                try:
                    task_llm_blueprint = project_chief_engineer_task_blueprint(portfolio, task_id)
                    result = generate_task_blueprint(
                        GenerateTaskBlueprintCommandV1(
                            task_id=task_id,
                            workspace=str(self.workspace),
                            objective=objective,
                            run_id=run.id,
                            constraints=task_constraints,
                            context=task_context,
                            llm_blueprint=task_llm_blueprint,
                        )
                    )
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.blueprint_generation_failed",
                            "severity": "error",
                            "detail": f"{type(exc).__name__}: {exc}",
                            "task_id": task_id,
                        }
                    )
                    continue

                if not result.ok or not result.blueprint_id or not result.blueprint_path:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.blueprint_result_invalid",
                            "severity": "error",
                            "detail": result.summary or result.status,
                            "task_id": task_id,
                        }
                    )
                    continue

                repaired_missing_artifact = self._ensure_chief_engineer_blueprint_artifact_present(
                    result=result,
                    task=task,
                    task_context=task_context,
                    constraints=task_constraints,
                    run_id=run.id,
                )
                if repaired_missing_artifact:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.blueprint_artifact_rewritten_from_result",
                            "severity": "warning",
                            "detail": (
                                "CE returned a valid blueprint result but the physical blueprint artifact was "
                                "missing; rewrote the handoff artifact from structured result fields."
                            ),
                            "task_id": task_id,
                            "blueprint_id": result.blueprint_id,
                            "blueprint_path": result.blueprint_path,
                        }
                    )

                handoff_validation = validate_director_handoff_from_payload(
                    str(self.workspace),
                    {"task_id": task_id, "blueprint_id": result.blueprint_id},
                    require_strict=True,
                )
                handoff_payload_raw = handoff_validation.get("decision_payload")
                handoff_payload: dict[str, Any] = handoff_payload_raw if isinstance(handoff_payload_raw, dict) else {}
                if not handoff_validation.get("allowed") and not handoff_payload:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.handoff_decision_unreadable",
                            "severity": "error",
                            "detail": str(
                                handoff_validation.get("reason")
                                or "Generated CE blueprint could not be loaded for handoff validation."
                            ),
                            "task_id": task_id,
                            "blueprint_id": result.blueprint_id,
                            "handoff_validation": handoff_validation,
                        }
                    )
                elif not handoff_validation.get("allowed"):
                    stage_signals.append(
                        {
                            "code": "chief_engineer.handoff_blocked",
                            "severity": "error",
                            "detail": str(handoff_validation.get("reason") or "Chief Engineer handoff blocked."),
                            "task_id": task_id,
                            "blueprint_id": result.blueprint_id,
                            "blockers": list(handoff_payload.get("blockers") or []),
                            "handoff_decision": handoff_payload,
                            "handoff_validation": handoff_validation,
                        }
                    )

                row_evidence = {
                    **ce_evidence,
                    "portfolio_id": portfolio.portfolio_id,
                    "portfolio_path": portfolio.portfolio_path,
                    "portfolio_hash": portfolio.portfolio_hash,
                    "project_interface_contract_ref": portfolio.project_interface_contract_ref,
                    "project_interface_contract_hash": portfolio.project_interface_contract_hash,
                }
                blueprint_rows.append(
                    {
                        "task_id": result.task_id,
                        "status": result.status,
                        "blueprint_id": result.blueprint_id,
                        "blueprint_path": result.blueprint_path,
                        "summary": result.summary,
                        "recommendations": list(result.recommendations),
                        "risks": list(result.risks),
                        "handoff_ready": bool(handoff_validation.get("allowed")),
                        "handoff_decision": handoff_payload,
                        "llm_evidence": row_evidence,
                        "llm_blueprint_consumed": True,
                        "llm_blueprint_keys": sorted(task_llm_blueprint),
                        "portfolio_reference": dict(portfolio_reference),
                    }
                )

        review_artifact = ""
        if blueprint_rows or stage_signals or portfolio is not None:
            review_artifact = f"runtime/state/blueprints/{run.id}.review.json"
            self._write_json_artifact(
                review_artifact,
                {
                    "schema_version": "factory.chief_engineer_review.v2",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "source": "factory_stage_executor.chief_engineer_portfolio_review",
                    "factory_run_id": run.id,
                    "task_plan": "tasks/plan.json",
                    "total_tasks": len(pm_tasks),
                    "generated_blueprints": len(blueprint_rows),
                    "llm_call_count": llm_call_count,
                    "portfolio": portfolio.to_reference() if portfolio is not None else {},
                    "project_interface_contract": (
                        portfolio.project_interface_contract.to_reference() if portfolio is not None else {}
                    ),
                    "blueprints": blueprint_rows,
                    "signals": stage_signals,
                },
            )

        keeper_stop = lease_scope.stop_keeper()
        heartbeat_failure = keeper_stop.failure
        if not keeper_stop.thread_exited:
            stage_signals.append(
                {
                    "code": "chief_engineer.execution_attempt_keeper_stop_failed",
                    "severity": "error",
                    "detail": (
                        f"{heartbeat_failure.error_type}: {heartbeat_failure.error_message}"
                        if heartbeat_failure is not None
                        else "lease keeper did not confirm thread exit"
                    ),
                    "reason": heartbeat_failure.reason if heartbeat_failure is not None else "unknown",
                }
            )
        elif heartbeat_failure is not None:
            stage_signals.append(
                {
                    "code": "chief_engineer.execution_attempt_heartbeat_failed",
                    "severity": "error",
                    "detail": (f"{heartbeat_failure.error_type}: {heartbeat_failure.error_message}"),
                    "reason": heartbeat_failure.reason,
                    "task_id": (
                        lease_scope.execution_attempt.external_task_id
                        if lease_scope.execution_attempt is not None
                        else ""
                    ),
                    "session_id": (
                        lease_scope.execution_attempt.session_id if lease_scope.execution_attempt is not None else ""
                    ),
                }
            )

        has_errors = any(
            str(item.get("severity") or "").strip().lower() == "error"
            for item in stage_signals
            if isinstance(item, dict)
        )
        stage_status = "cancelled" if cancelled_by_factory else "failed" if has_errors else "success"
        error_code = ""
        root_cause_hint = ""
        failure_recoverable = True
        if has_errors:
            for signal in stage_signals:
                if str(signal.get("severity") or "").strip().lower() != "error":
                    continue
                error_code = str(signal.get("code") or "").strip()
                root_cause_hint = str(signal.get("detail") or "").strip()
                if isinstance(signal.get("recoverable"), bool):
                    failure_recoverable = bool(signal["recoverable"])
                if error_code:
                    break

        if ce_runtime_task_id is not None and ce_execution_attempt is not None:
            should_settle, heartbeat_failure = lease_scope.begin_settlement()
            if should_settle:
                settlement_attempt = lease_scope.execution_attempt
                if settlement_attempt is None:
                    raise RuntimeError("chief_engineer_execution_attempt_settlement_identity_missing")
                try:
                    self._settle_chief_engineer_execution_attempt(
                        task_id=ce_runtime_task_id,
                        execution_attempt=settlement_attempt,
                        stage_status=stage_status,
                        summary=error_code or "chief_engineer_portfolio_review_completed",
                    )
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.execution_attempt_settlement_failed",
                            "severity": "error",
                            "detail": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    stage_status = "failed"
                    error_code = "chief_engineer.execution_attempt_settlement_failed"
                    root_cause_hint = str(exc)
            elif heartbeat_failure is not None and not any(
                str(signal.get("code") or "") == "chief_engineer.execution_attempt_keeper_stop_failed"
                for signal in stage_signals
                if isinstance(signal, dict)
            ):
                stage_signals.append(
                    {
                        "code": "chief_engineer.execution_attempt_settlement_blocked",
                        "severity": "error",
                        "detail": f"{heartbeat_failure.error_type}: {heartbeat_failure.error_message}",
                        "reason": heartbeat_failure.reason,
                    }
                )
                stage_status = "failed"
                error_code = "chief_engineer.execution_attempt_settlement_blocked"
                root_cause_hint = heartbeat_failure.error_message

        stage_signal_path = ""
        if stage_signals:
            stage_signal_path = self._write_stage_signal_artifact(
                stage="chief_engineer_review",
                run_id=run.id,
                signals=stage_signals,
            )

        artifacts = [row["blueprint_path"] for row in blueprint_rows if row.get("blueprint_path")]
        if portfolio is not None:
            artifacts.append(portfolio.portfolio_path)
        if review_artifact:
            artifacts.append(review_artifact)
        self._mirror_chief_engineer_artifacts(run.id, blueprint_rows, review_artifact, artifacts)
        if stage_signal_path:
            artifacts.append(stage_signal_path)

        return StageResult(
            stage="chief_engineer_review",
            status=stage_status,
            output=(
                f"Chief Engineer portfolio review generated {len(blueprint_rows)}/{len(pm_tasks)} blueprints; "
                f"llm_calls={llm_call_count}; signals={len(stage_signals)}; "
                f"error_code={error_code or 'none'}; root_cause_hint={root_cause_hint or 'none'}"
            ),
            artifacts=artifacts,
            metadata={
                "error_code": error_code,
                "failure_class": (
                    "ROLE_LLM_REVIEW_FAILED"
                    if error_code == "chief_engineer.llm_review_failed"
                    else "CHIEF_ENGINEER_REVIEW_FAILED"
                    if stage_status == "failed"
                    else ""
                ),
                "responsible_layer": "chief_engineer" if stage_status == "failed" else "",
                "root_cause_hint": root_cause_hint,
                "recoverable": failure_recoverable if stage_status == "failed" else False,
            },
        )

    async def _execute_director_dispatch(self, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        logger.info("Executing Director dispatch for run %s", run.id)
        abort_checker = self._resolve_abort_checker(context)
        authority_port = self._factory_role_evidence_cutoff_port(context)

        synced_plan_source = self._ensure_pm_plan_contract_available()
        self._enrich_pm_plan_contract_artifact("tasks/plan.json")
        pm_tasks = self._load_pm_plan_tasks("tasks/plan.json")
        plan_task_filter = self._build_director_task_filter(pm_tasks)
        configured_task_filter = str(context.get("task_filter") or "").strip()
        effective_task_filter = configured_task_filter or plan_task_filter
        requested_task_ids = self._director_requested_task_ids(context, pm_tasks)

        service = self._build_orchestration_service(context)
        stage_signals: list[dict[str, Any]] = []
        if synced_plan_source:
            stage_signals.append(
                {
                    "code": "director.plan_contract_synced_from_workspace_mirror",
                    "severity": "info",
                    "detail": "Copied PM workspace plan mirror into runtime tasks/plan.json before Director dispatch.",
                    "source_path": synced_plan_source,
                }
            )
        if pm_tasks:
            materialize_summary = self._materialize_pm_plan_taskboard(
                pm_tasks,
                run_id=run.id,
                source_stage="director_dispatch",
                run_metadata=run.metadata,
            )
            binding_failures = list(materialize_summary.get("binding_failures") or [])
            if binding_failures:
                stage_signals.append(
                    {
                        "code": "director.task_runtime_factory_binding_failed",
                        "severity": "error",
                        "detail": "TaskRuntime rejected one or more Factory run bindings before Director dispatch.",
                        **materialize_summary,
                    }
                )
                signal_artifact = self._write_stage_signal_artifact(
                    stage="director_dispatch",
                    run_id=run.id,
                    signals=stage_signals,
                )
                return StageResult(
                    stage="director_dispatch",
                    status="failed",
                    output=("Director dispatch blocked before LLM execution: TaskRuntime Factory run binding failed"),
                    artifacts=[signal_artifact],
                )
            if int(materialize_summary.get("created_count") or 0) > 0:
                stage_signals.append(
                    {
                        "code": "director.taskboard_materialized_from_plan",
                        "severity": "info",
                        "detail": "Materialized missing PM plan tasks into TaskBoard before Director dispatch.",
                        **materialize_summary,
                    }
                )
        snapshot_signals: list[dict[str, Any]] = []
        raw_start_metadata = context.get("metadata")
        start_metadata: dict[str, Any] = dict(raw_start_metadata) if isinstance(raw_start_metadata, dict) else {}
        start_from_hint = str(context.get("factory_start_from") or start_metadata.get("factory_start_from") or "")
        director_only_resume = start_from_hint.strip().lower() == "director_resume"
        if director_only_resume:
            try:
                restore_payload = self._restore_pre_director_snapshot()
                snapshot_signals.append(
                    {
                        "code": "director.pre_director_snapshot_restored",
                        "severity": "info",
                        "detail": "Restored workspace delivery files from pre-Director snapshot before resume",
                        **restore_payload,
                    }
                )
            except RuntimeError as exc:
                stage_signals.append(
                    {
                        "code": "director.pre_director_snapshot_restore_failed",
                        "severity": "error",
                        "detail": str(exc),
                    }
                )
        else:
            try:
                snapshot_payload = self._create_pre_director_snapshot(run_id=run.id)
                snapshot_signals.append(
                    {
                        "code": "director.pre_director_snapshot_created",
                        "severity": "info",
                        "detail": "Captured workspace delivery-file snapshot before Director dispatch",
                        "file_count": snapshot_payload.get("file_count"),
                        "snapshot_path": _PRE_DIRECTOR_SNAPSHOT_RELATIVE_DIR,
                    }
                )
            except (OSError, RuntimeError, ValueError) as exc:
                stage_signals.append(
                    {
                        "code": "director.pre_director_snapshot_create_failed",
                        "severity": "error",
                        "detail": str(exc),
                    }
                )
        initial_stats = self._read_taskboard_stats()
        _observable_rows, task_runtime_projection_failure = self._query_observable_task_rows(factory_run_id=run.id)
        if task_runtime_projection_failure is not None:
            stage_signals.append(task_runtime_projection_failure)
        attempts: list[dict[str, Any]] = []
        last_command_result: CommandResult | None = None
        last_director_execution_deadline_monotonic: float | None = None
        final_result: CommandResult | None = None
        max_rounds = int(context.get("director_max_rounds") or 0)
        if max_rounds <= 0:
            active_rounds = (
                int(initial_stats.get("pending") or 0)
                + int(initial_stats.get("ready") or 0)
                + int(initial_stats.get("in_progress") or 0)
                + 2
            )
            total_rounds = int(initial_stats.get("total") or 0) + 2
            dynamic_rounds = max(active_rounds, total_rounds)
            max_rounds = max(2, min(dynamic_rounds, 12))
        idle_budget = max(1, int(context.get("director_idle_budget") or 2))
        idle_rounds = 0
        requires_taskboard_convergence = True
        execution_barrier_timeout_observed = False

        # Enforce mainline-full: no silent single-worker fallback
        execution_mode = str(context.get("execution_mode", "parallel")).strip().lower()
        if execution_mode not in ("parallel", "serial"):
            stage_signals.append(
                {
                    "code": "director.invalid_execution_mode",
                    "severity": "error",
                    "detail": f"Invalid execution_mode: {execution_mode}; must be 'parallel' or 'serial'",
                }
            )
            execution_mode = "parallel"

        # Enforce worker count matches configured bindings
        max_workers = int(context.get("max_workers", DEFAULT_DIRECTOR_MAX_PARALLELISM))
        if max_workers < 1:
            stage_signals.append(
                {
                    "code": "director.invalid_worker_count",
                    "severity": "error",
                    "detail": f"Invalid max_workers: {max_workers}; must be >= 1",
                }
            )
            max_workers = DEFAULT_DIRECTOR_MAX_PARALLELISM

        if not pm_tasks:
            stage_signals.append(
                {
                    "code": "director.task_lineage_missing",
                    "severity": "error",
                    "detail": "tasks/plan.json missing or empty tasks array",
                }
            )
        if int(initial_stats.get("total") or 0) <= 0:
            stage_signals.append(
                {
                    "code": "director.taskboard_empty",
                    "severity": "error",
                    "detail": "TaskBoard has no executable task records",
                }
            )
        stage_signals.extend(
            self._chief_engineer_handoff_signals_for_director(
                pm_tasks,
                run_id=run.id,
            )
        )

        if not any(str(item.get("severity") or "").strip().lower() == "error" for item in stage_signals):
            director_binding_fanout = self._resolve_director_binding_fanout(context)
            director_binding_skips = list(getattr(self, "_last_director_binding_skips", []))

            for round_index in range(1, max_rounds + 1):
                before_stats = self._read_taskboard_stats()
                workspace_state_before = self._capture_workspace_delivery_state()
                if self._is_taskboard_converged(before_stats):
                    stage_signals.append(
                        {
                            "code": "director.already_converged",
                            "severity": "info",
                            "detail": "TaskBoard already converged before dispatch round",
                            "round": round_index,
                        }
                    )
                    final_result = CommandResult(
                        run_id="",
                        status="completed",
                        message="TaskBoard already converged",
                    )
                    break

                raw_context_metadata = context.get("metadata")
                context_metadata: dict[str, Any] = (
                    dict(raw_context_metadata) if isinstance(raw_context_metadata, dict) else {}
                )
                base_options: dict[str, Any] = {
                    "task_filter": effective_task_filter,
                    "max_workers": max_workers,
                    "execution_mode": execution_mode,
                    "dispatch_mode": "mainline-full",
                    "metadata": {
                        **context_metadata,
                        "factory_run_id": str(context.get("factory_run_id") or run.id or "").strip(),
                        "factory_stage": "director_dispatch",
                        "director_binding_skips": director_binding_skips,
                    },
                }
                missing_declared_targets = self._missing_declared_delivery_targets(pm_tasks)
                materialization_pending = bool(missing_declared_targets)
                first_materialization_pending = (
                    materialization_pending and not attempts and int(before_stats.get("completed") or 0) == 0
                )
                remaining_task_count = self._remaining_director_task_count(
                    before_stats,
                    fallback=len(pm_tasks),
                )
                dependency_schedule = self._director_dependency_schedule(
                    pm_tasks,
                    factory_run_id=run.id,
                )
                critical_path_task_count = max(1, dependency_schedule.critical_path_task_count)
                requested_director_dispatch_timeout_seconds = self._director_dispatch_timeout_seconds(
                    context,
                    task_count=critical_path_task_count,
                    materialization_pending=materialization_pending,
                )
                admission_decision = self._director_dispatch_deadline_admission_decision(
                    context,
                    requested_timeout_seconds=requested_director_dispatch_timeout_seconds,
                    first_materialization_pending=first_materialization_pending,
                    materialization_pending=materialization_pending,
                    dependency_schedule=dependency_schedule,
                )
                admission_payload = admission_decision.to_dict()
                if not admission_decision.executable:
                    error_code, error_detail, result_status, result_message = (
                        self._director_admission_failure_projection(admission_decision)
                    )
                    no_active_tasks = (
                        str(admission_decision.reason or "").strip() == "no_active_director_tasks"
                        and result_status == "completed"
                    )
                    signal_payload: dict[str, Any] = {
                        "code": error_code,
                        "severity": "info" if no_active_tasks else "error",
                        "detail": error_detail,
                        "round": round_index,
                        "responsible_layer": "execution_control_plane",
                        "repairable_by_director": False,
                        "requires_ce_replan": False,
                        "requires_pm_revision": False,
                        **admission_payload,
                    }
                    if not no_active_tasks:
                        signal_payload["failure_class"] = FailureClassV1.TASKBOARD_DEADLOCK.value
                    stage_signals.append(signal_payload)
                    final_result = CommandResult(
                        run_id="",
                        status=result_status,
                        message=result_message,
                        metadata={
                            "deadline_admission": admission_payload,
                        },
                    )
                    break
                base_options["metadata"].update(
                    {
                        "director_dispatch_timeout_seconds": admission_decision.timeout_seconds,
                        "director_dispatch_execution_timeout_seconds": (admission_decision.execution_timeout_seconds),
                        "director_dispatch_settlement_timeout_seconds": (admission_decision.settlement_timeout_seconds),
                        "director_dispatch_requested_timeout_seconds": (requested_director_dispatch_timeout_seconds),
                        "director_deadline_admission": admission_payload,
                        "director_first_materialization_pending": first_materialization_pending,
                        "director_remaining_task_count": remaining_task_count,
                        "director_critical_path_task_count": critical_path_task_count,
                        "director_missing_declared_target_count": len(missing_declared_targets),
                        "director_missing_declared_target_sample": missing_declared_targets[:12],
                    }
                )
                director_lease_timeout_seconds = admission_decision.timeout_seconds
                director_execution_timeout_seconds = admission_decision.execution_timeout_seconds
                director_settlement_timeout_seconds = admission_decision.settlement_timeout_seconds
                requested_llm_timeout = int(
                    context.get("llm_call_timeout_seconds") or director_execution_timeout_seconds
                )
                requested_director_timeout = int(
                    context.get("director_llm_timeout_seconds")
                    or context.get("llm_call_timeout_seconds")
                    or director_execution_timeout_seconds
                )
                admitted_timeout_seconds = director_execution_timeout_seconds
                base_options["llm_call_timeout_seconds"] = min(requested_llm_timeout, admitted_timeout_seconds)
                base_options["director_llm_timeout_seconds"] = min(
                    requested_director_timeout,
                    admitted_timeout_seconds,
                )
                base_options["metadata"].update(
                    {
                        "llm_call_timeout_seconds": base_options["llm_call_timeout_seconds"],
                        "director_llm_timeout_seconds": base_options["director_llm_timeout_seconds"],
                        "request_timeout_seconds": base_options["llm_call_timeout_seconds"],
                        "timeout_seconds": base_options["llm_call_timeout_seconds"],
                    }
                )
                round_requested_task_ids = self._read_claimable_director_task_ids(
                    limit=max_workers,
                    factory_run_id=run.id,
                )
                if not round_requested_task_ids and attempts:
                    inflight_run_id = str((last_command_result.run_id if last_command_result else "") or "").strip()
                    active_execution_observed = bool(
                        inflight_run_id
                        and (
                            self._active_director_execution_progress_marker(run_id=inflight_run_id)
                            or self._taskboard_has_active_execution(before_stats)
                        )
                    )
                    carried_execution_lease_seconds = (
                        _whole_wait_seconds(last_director_execution_deadline_monotonic)
                        if active_execution_observed and last_director_execution_deadline_monotonic is not None
                        else 0
                    )
                    inflight_settlement_wait_seconds = (
                        carried_execution_lease_seconds + director_settlement_timeout_seconds
                    )
                    settle_result = await self._settle_inflight_director_run_after_timeout(
                        service,
                        run_id=inflight_run_id,
                        grace_seconds=inflight_settlement_wait_seconds,
                        cancel_event=self._resolve_cancel_event(context),
                        abort_checker=abort_checker,
                    )
                    if settle_result is not None:
                        final_result = settle_result
                        settled_stats = self._read_taskboard_stats()
                        workspace_delta = self._workspace_delivery_delta(
                            workspace_state_before,
                            self._capture_workspace_delivery_state(),
                        )
                        workspace_delta_progress = self._workspace_delta_indicates_materialization_progress(
                            workspace_delta
                        )
                        settled_metadata = settle_result.metadata if isinstance(settle_result.metadata, dict) else {}
                        settled_status = str(settle_result.status or "").strip().lower()
                        attempts.append(
                            {
                                "round": round_index,
                                "run_id": str(settle_result.run_id or "").strip(),
                                "status": str(settle_result.status or "").strip(),
                                "message": str(settle_result.message or "").strip(),
                                "metadata": settled_metadata,
                                "taskboard_before": before_stats,
                                "taskboard_after": settled_stats,
                                "progress_made": self._has_director_progress(before_stats, settled_stats),
                                "workspace_delta_progress": workspace_delta_progress,
                                "workspace_delta": workspace_delta,
                                "active_execution_observed": active_execution_observed,
                                "carried_execution_lease_seconds": carried_execution_lease_seconds,
                                "settlement_timeout_seconds": director_settlement_timeout_seconds,
                                "execution_barrier_wait_seconds": inflight_settlement_wait_seconds,
                                "settled_after_timeout": True,
                            }
                        )
                        if workspace_delta_progress:
                            stage_signals.append(
                                {
                                    "code": "director.workspace_delta_progress_detected",
                                    "severity": "info",
                                    "detail": (
                                        "Detected added or changed delivery files while settling Director run "
                                        "after timeout"
                                    ),
                                    "round": round_index,
                                    **workspace_delta,
                                }
                            )
                        stage_signals.append(
                            {
                                "code": "director.inflight_timeout_settled",
                                "severity": "info" if settled_status in {"completed", "success"} else "warning",
                                "authoritative": False,
                                "authority_source": "orchestration_lifecycle_diagnostic",
                                "detail": (
                                    "Director run reached terminal status during timeout settle grace: "
                                    f"{settled_status or 'unknown'}"
                                ),
                                "round": round_index,
                                "run_id": str(settle_result.run_id or "").strip(),
                                "taskboard_after": settled_stats,
                                "active_execution_observed": active_execution_observed,
                                "carried_execution_lease_seconds": carried_execution_lease_seconds,
                                "settlement_timeout_seconds": director_settlement_timeout_seconds,
                                "execution_barrier_wait_seconds": inflight_settlement_wait_seconds,
                            }
                        )
                        if self._is_taskboard_converged(settled_stats):
                            break
                        claimable_after_settle, settled_stats = await self._wait_for_claimable_director_tasks(
                            limit=max_workers,
                            grace_seconds=self._director_dependency_settle_grace_seconds(context),
                            factory_run_id=run.id,
                        )
                        if claimable_after_settle:
                            stage_signals.append(
                                {
                                    "code": "director.dependencies_settled_for_next_round",
                                    "severity": "info",
                                    "detail": (
                                        "TaskRuntime dependency facts exposed new claimable tasks; "
                                        "starting a fresh deadline-admitted dispatch round"
                                    ),
                                    "round": round_index,
                                    "taskboard_after": settled_stats,
                                    "claimable_task_ids": claimable_after_settle,
                                }
                            )
                            continue
                        stage_signals.append(
                            {
                                "code": "director.no_claimable_tasks_after_progress",
                                "severity": "warning",
                                "detail": (
                                    "TaskBoard has no claimable Director tasks after previous dispatch attempt "
                                    "settled; stopping dispatch instead of replaying terminal or blocked PM tasks"
                                ),
                                "round": round_index,
                                "taskboard_before": before_stats,
                                "taskboard_after": settled_stats,
                                "failure_class": FailureClassV1.TASKBOARD_DEADLOCK.value,
                                "responsible_layer": "execution_control_plane",
                            }
                        )
                        break
                    claimable_after_grace, grace_stats = await self._wait_for_claimable_director_tasks(
                        limit=max_workers,
                        grace_seconds=self._director_dependency_settle_grace_seconds(context),
                        factory_run_id=run.id,
                    )
                    if claimable_after_grace:
                        stage_signals.append(
                            {
                                "code": "director.dependencies_settled_for_next_round",
                                "severity": "info",
                                "detail": (
                                    "TaskRuntime dependency facts exposed new claimable tasks; "
                                    "starting a fresh deadline-admitted dispatch round"
                                ),
                                "round": round_index,
                                "taskboard_after": grace_stats,
                                "claimable_task_ids": claimable_after_grace,
                            }
                        )
                        continue
                    stage_signals.append(
                        {
                            "code": "director.no_claimable_tasks_after_progress",
                            "severity": "warning",
                            "detail": (
                                "TaskBoard has no claimable Director tasks after previous dispatch attempt; "
                                "stopping dispatch instead of replaying terminal or blocked PM tasks"
                            ),
                            "round": round_index,
                            "taskboard_before": before_stats,
                        }
                    )
                    break
                if not round_requested_task_ids:
                    round_requested_task_ids = list(requested_task_ids or [])
                base_options["metadata"]["director_claimable_task_ids"] = list(round_requested_task_ids)
                execution_deadline_monotonic = _new_monotonic_deadline(director_execution_timeout_seconds)
                last_director_execution_deadline_monotonic = execution_deadline_monotonic
                director_execution_deadline_epoch_seconds = datetime.now(
                    timezone.utc
                ).timestamp() + _remaining_monotonic_seconds(execution_deadline_monotonic)
                base_options["metadata"]["factory_director_execution_deadline_epoch_seconds"] = (
                    director_execution_deadline_epoch_seconds
                )
                if director_binding_fanout:
                    command_result = await self._execute_director_binding_fanout(
                        service=service,
                        workspace=str(self.workspace),
                        tasks=round_requested_task_ids,
                        base_options=base_options,
                        bindings=director_binding_fanout,
                        timeout_seconds=director_execution_timeout_seconds,
                        cancel_event=self._resolve_cancel_event(context),
                        abort_checker=abort_checker,
                        skipped_bindings=director_binding_skips,
                        deadline_monotonic=execution_deadline_monotonic,
                        authority_port=authority_port,
                    )
                    last_command_result = command_result
                    director_result = command_result
                elif director_binding_skips:
                    per_binding = [
                        {
                            "provider_id": str(binding.get("provider_id") or "").strip(),
                            "model": str(binding.get("model") or "").strip(),
                            "binding_id": str(binding.get("binding_id") or "").strip(),
                            "run_id": "",
                            "status": "skipped",
                            "message": "Skipped by Director binding readiness filter",
                            "skipped": True,
                            "skip_reason": str(binding.get("reason") or "binding_unavailable").strip(),
                        }
                        for binding in director_binding_skips
                        if isinstance(binding, dict)
                    ]
                    command_result = CommandResult(
                        run_id="",
                        status="failed",
                        message="No available Director binding after readiness filtering",
                        reason_code="DIRECTOR_BINDINGS_UNAVAILABLE",
                        metadata={
                            "binding_fanout": True,
                            "binding_count": len(per_binding),
                            "active_binding_count": 0,
                            "readiness_skipped_count": len(per_binding),
                            "per_binding": per_binding,
                            "execution_mode": execution_mode,
                            "max_workers": max_workers,
                        },
                    )
                    last_command_result = command_result
                    director_result = command_result
                else:
                    submission_remaining_seconds = _remaining_monotonic_seconds(execution_deadline_monotonic)
                    try:
                        command_result = await asyncio.wait_for(
                            self._call_with_factory_role_evidence_authority(
                                authority_port,
                                "director",
                                partial(
                                    service.execute_director_run,
                                    workspace=str(self.workspace),
                                    tasks=round_requested_task_ids,
                                    options=base_options,
                                ),
                            ),
                            timeout=submission_remaining_seconds,
                        )
                    except TimeoutError:
                        command_result = CommandResult(
                            run_id="",
                            status="timeout",
                            message="Director submission exceeded the execution lease",
                            reason_code="DIRECTOR_SUBMISSION_TIMEOUT",
                            metadata={
                                "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                                "responsible_layer": "execution_control_plane",
                                "submission_outcome_unknown": True,
                            },
                        )
                    last_command_result = command_result
                    command_status = str(command_result.status or "").strip().lower()
                    wait_timeout_seconds = _whole_wait_seconds(execution_deadline_monotonic)
                    wait_remaining_seconds = _remaining_monotonic_seconds(execution_deadline_monotonic)
                    if (
                        command_status in {"blocked", "cancelled", "failed", "timeout"}
                        or not str(command_result.run_id or "").strip()
                    ):
                        director_result = command_result
                    elif wait_timeout_seconds <= 0 or wait_remaining_seconds <= 0:
                        director_result = CommandResult(
                            run_id=command_result.run_id,
                            status="timeout",
                            message="Director execution lease expired before completion wait",
                            reason_code="DIRECTOR_EXECUTION_LEASE_EXHAUSTED",
                            metadata={
                                "cancel_signal_sent": False,
                                "inflight_run_continues": True,
                                "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                                "responsible_layer": "execution_control_plane",
                            },
                        )
                    else:
                        director_wait_kwargs: dict[str, Any] = {
                            "timeout_seconds": wait_timeout_seconds,
                            "cancel_event": self._resolve_cancel_event(context),
                            "abort_checker": abort_checker,
                            "cancel_on_timeout": False,
                        }
                        if _call_accepts_keyword(self._wait_run_completion, "authority"):
                            director_wait_kwargs["authority"] = RunCompletionAuthority.TASK_RUNTIME_EXECUTION_FACT
                        try:
                            director_result = await asyncio.wait_for(
                                self._wait_run_completion(
                                    service,
                                    command_result,
                                    **director_wait_kwargs,
                                ),
                                timeout=wait_remaining_seconds,
                            )
                        except TimeoutError:
                            director_result = CommandResult(
                                run_id=command_result.run_id,
                                status="timeout",
                                message="Director completion wait exceeded the execution lease",
                                reason_code="DIRECTOR_EXECUTION_LEASE_EXHAUSTED",
                                metadata={
                                    "cancel_signal_sent": False,
                                    "inflight_run_continues": True,
                                    "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                                    "responsible_layer": "execution_control_plane",
                                },
                            )
                # A lifecycle result can report ``inflight_run_continues``
                # before the execution lease has actually elapsed (for
                # example while TaskRuntime's fact projection is still
                # catching up).  Spending only the settlement reserve here
                # lets Factory return and close its stage-bound role-evidence
                # authority while the admitted Director child is still
                # approaching Provider transport.  Preserve the one parent / one
                # child execution boundary by consuming the unused execution
                # lease first, then the dedicated settlement reserve.
                director_barrier_wait_seconds = director_settlement_timeout_seconds
                if self._inflight_director_run_ids(director_result):
                    director_barrier_wait_seconds += _whole_wait_seconds(execution_deadline_monotonic)
                director_result, barrier_observed = await self._settle_inflight_director_result(
                    service,
                    result=director_result,
                    grace_seconds=director_barrier_wait_seconds,
                    cancel_event=self._resolve_cancel_event(context),
                    abort_checker=abort_checker,
                )
                final_result = director_result

                after_stats = self._read_taskboard_stats()
                workspace_delta = self._workspace_delivery_delta(
                    workspace_state_before,
                    self._capture_workspace_delivery_state(),
                )
                workspace_delta_progress = self._workspace_delta_indicates_materialization_progress(workspace_delta)
                metadata_payload = director_result.metadata if isinstance(director_result.metadata, dict) else {}
                progress_made = self._has_director_progress(before_stats, after_stats)
                attempt_entry = {
                    "round": round_index,
                    "run_id": str(command_result.run_id or "").strip(),
                    "status": str(director_result.status or "").strip(),
                    "message": str(director_result.message or "").strip(),
                    "metadata": metadata_payload,
                    "taskboard_before": before_stats,
                    "taskboard_after": after_stats,
                    "timeout_seconds": director_lease_timeout_seconds,
                    "execution_timeout_seconds": director_execution_timeout_seconds,
                    "settlement_timeout_seconds": director_settlement_timeout_seconds,
                    "execution_barrier_wait_seconds": director_barrier_wait_seconds,
                    "materialization_pending": materialization_pending,
                    "missing_declared_target_count": len(missing_declared_targets),
                    "progress_made": progress_made,
                    "workspace_delta_progress": workspace_delta_progress,
                    "workspace_delta": workspace_delta,
                    "settlement_attempted": barrier_observed,
                    "settled_after_timeout": barrier_observed
                    and not bool(metadata_payload.get("inflight_run_continues")),
                }
                attempts.append(attempt_entry)
                if barrier_observed:
                    barrier_still_active = bool(metadata_payload.get("inflight_run_continues"))
                    stage_signals.append(
                        {
                            "code": (
                                "director.execution_barrier_timeout"
                                if barrier_still_active
                                else "director.inflight_timeout_settled"
                            ),
                            "severity": "error" if barrier_still_active else "info",
                            "detail": (
                                "Director child execution remained active after the settlement barrier"
                                if barrier_still_active
                                else "Director child execution reached a terminal fact before the next dispatch round"
                            ),
                            "round": round_index,
                            "run_id": str(director_result.run_id or command_result.run_id or "").strip(),
                            "failure_class": (
                                FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value if barrier_still_active else ""
                            ),
                            "responsible_layer": "execution_control_plane",
                            "inflight_run_continues": barrier_still_active,
                            "settled_after_timeout": not barrier_still_active,
                        }
                    )
                    if barrier_still_active:
                        execution_barrier_timeout_observed = True
                        break
                if workspace_delta_progress:
                    stage_signals.append(
                        {
                            "code": "director.workspace_delta_progress_detected",
                            "severity": "info",
                            "detail": "Detected added or changed delivery files during Director dispatch",
                            "round": round_index,
                            **workspace_delta,
                        }
                    )

                round_authority = helpers.evaluate_canonical_factory_authority(
                    self._canonical_factory_projection(run, context)
                )
                director_status = str(director_result.status or "").strip().lower()
                if round_authority.director_stage_authorized:
                    director_status = "completed"
                    progress_made = True
                    attempt_entry["progress_made"] = True
                    attempt_entry["canonical_task_boundary_authorized"] = True
                elif director_status in {"completed", "success"}:
                    director_status = "failed"
                    stage_signals.append(
                        {
                            "code": "director.canonical_task_boundary_missing",
                            "severity": "error",
                            "detail": round_authority.detail,
                            "round": round_index,
                            "reason_code": round_authority.reason_code,
                            "failure_class": round_authority.failure_class
                            or FailureClassV1.LEDGER_PROJECTION_INCOMPLETE.value,
                            "responsible_layer": round_authority.responsible_layer or "execution_control_plane",
                            "incomplete_task_ids": list(round_authority.incomplete_task_ids),
                        }
                    )
                if director_status not in {"completed", "success"}:
                    if progress_made:
                        idle_rounds = 0
                        if self._is_taskboard_converged(after_stats):
                            stage_signals.append(
                                {
                                    "code": "director.dispatch_converged_after_partial_failure",
                                    "severity": "info",
                                    "detail": f"Director dispatch converged after partial failure in round {round_index}",
                                    "round": round_index,
                                    "upstream_status": director_status,
                                }
                            )
                            break
                        stage_signals.append(
                            {
                                "code": "director.partial_failure_progress_continued",
                                "severity": "warning",
                                "detail": (
                                    "Director run returned a non-success status after material progress; "
                                    "continuing remaining dispatch rounds until TaskBoard convergence"
                                ),
                                "upstream_status": director_status,
                                "round": round_index,
                            }
                        )
                        continue
                    if director_status == "timeout":
                        attempt_timeout_seconds = director_execution_timeout_seconds
                        stage_signals.append(
                            {
                                "code": "director.dispatch_timeout",
                                "severity": "error",
                                "detail": (
                                    "Director dispatch timed out after "
                                    f"{attempt_timeout_seconds} "
                                    "seconds; "
                                    "no further progress possible"
                                ),
                                "upstream_status": director_status,
                                "round": round_index,
                                "timeout_seconds": attempt_timeout_seconds,
                                "stage_lease_seconds": director_lease_timeout_seconds,
                                "settlement_timeout_seconds": director_settlement_timeout_seconds,
                                "materialization_pending": materialization_pending,
                                "missing_declared_target_count": len(missing_declared_targets),
                            }
                        )
                    else:
                        stage_signals.append(
                            {
                                "code": "director.run_status_non_success",
                                "severity": "error",
                                "detail": str(director_result.message or "").strip()
                                or str(director_result.status or "unknown"),
                                "upstream_status": str(director_result.status or "").strip(),
                                "round": round_index,
                            }
                        )
                    break

                if progress_made:
                    idle_rounds = 0
                else:
                    idle_rounds += 1
                    stage_signals.append(
                        {
                            "code": "director.no_progress_round",
                            "severity": "warning",
                            "detail": f"No TaskBoard progress in dispatch round {round_index}",
                            "round": round_index,
                            "idle_rounds": idle_rounds,
                        }
                    )

                if self._is_taskboard_converged(after_stats):
                    stage_signals.append(
                        {
                            "code": "director.dispatch_converged",
                            "severity": "info",
                            "detail": f"Director dispatch converged in {round_index} rounds",
                            "round": round_index,
                        }
                    )
                    break

                if idle_rounds > idle_budget:
                    stage_signals.append(
                        {
                            "code": "director.dispatch_stalled",
                            "severity": "error",
                            "detail": (
                                "Director dispatch exceeded idle progress budget; "
                                f"idle_rounds={idle_rounds}, idle_budget={idle_budget}"
                            ),
                            "round": round_index,
                        }
                    )
                    break

        final_stats = self._read_taskboard_stats()
        converged = self._is_taskboard_converged(final_stats)
        final_authority = helpers.evaluate_canonical_factory_authority(self._canonical_factory_projection(run, context))
        if not final_authority.director_stage_authorized and not any(
            str(item.get("code") or "") == "director.canonical_task_boundary_missing"
            for item in stage_signals
            if isinstance(item, dict)
        ):
            stage_signals.append(
                {
                    "code": "director.canonical_task_boundary_missing",
                    "severity": "error",
                    "detail": final_authority.detail,
                    "reason_code": final_authority.reason_code,
                    "failure_class": final_authority.failure_class or FailureClassV1.LEDGER_PROJECTION_INCOMPLETE.value,
                    "responsible_layer": final_authority.responsible_layer or "execution_control_plane",
                    "incomplete_task_ids": list(final_authority.incomplete_task_ids),
                }
            )

        provider_health_signal = self._director_provider_health_failure_signal()
        if provider_health_signal and not any(
            str(item.get("code") or "") == str(provider_health_signal.get("code") or "")
            for item in stage_signals
            if isinstance(item, dict)
        ):
            stage_signals.append(provider_health_signal)
        stage_signals.extend(snapshot_signals)
        if (
            requires_taskboard_convergence
            and not converged
            and not execution_barrier_timeout_observed
            and not any(
                str(item.get("code") or "") == "director.taskboard_not_converged"
                for item in stage_signals
                if isinstance(item, dict)
            )
        ):
            stage_signals.append(
                {
                    "code": "director.taskboard_not_converged",
                    "severity": "warning",
                    "detail": f"TaskBoard not converged after dispatch rounds; final_stats={final_stats}",
                    "authoritative": False,
                    "authority_source": "task_runtime_diagnostic_projection",
                }
            )

        stage_status = "success" if final_authority.director_stage_authorized else "failed"
        if not final_authority.director_stage_authorized and (
            str((final_result or CommandResult(run_id="", status="", message="")).status or "").strip().lower()
            == "cancelled"
        ):
            stage_status = "cancelled"

        # Generate per-binding terminal route events from fanout results
        per_binding_route_events: list[dict[str, Any]] = []
        for attempt in attempts:
            metadata = attempt.get("metadata") if isinstance(attempt, dict) else {}
            if not isinstance(metadata, dict):
                continue
            per_binding_raw = metadata.get("per_binding")
            if isinstance(per_binding_raw, list):
                per_binding_items = [item for item in per_binding_raw if isinstance(item, dict)]
                per_binding_route_events.extend(
                    self._build_per_binding_route_events(cast(list[dict[str, Any]], per_binding_items))
                )

        if stage_status != "cancelled":
            _binding_ok, binding_signals = self._validate_director_binding_coverage(
                additional_events=per_binding_route_events,
            )
            for signal in binding_signals:
                if str(signal.get("severity") or "").strip().lower() == "error":
                    signal["severity"] = "warning"
                    signal["authoritative"] = False
                    signal["authority_source"] = "binding_coverage_diagnostic"
            stage_signals.extend(binding_signals)

        error_code = ""
        root_cause_hint = ""
        for signal in stage_signals:
            if not isinstance(signal, dict):
                continue
            if str(signal.get("severity") or "").strip().lower() != "error":
                continue
            error_code = str(signal.get("code") or "").strip()
            root_cause_hint = str(signal.get("detail") or "").strip()
            if error_code:
                break

        if per_binding_route_events:
            self._reclassify_binding_coverage_signals(
                stage_signals,
                per_binding_route_events,
            )

        for signal in stage_signals:
            if not isinstance(signal, dict):
                continue
            if str(signal.get("severity") or "").strip().lower() != "error":
                continue
            error_code = str(signal.get("code") or "").strip()
            root_cause_hint = str(signal.get("detail") or "").strip()
            if error_code:
                break

        if final_authority.director_stage_authorized:
            error_code = ""
            root_cause_hint = ""

        fail_closed_events = self._build_fail_closed_director_route_events(
            attempts=attempts,
            stage_signals=stage_signals,
            per_binding_route_events=per_binding_route_events,
        )
        if fail_closed_events:
            stage_signals.append(
                {
                    "code": "director.fail_closed_route_evidence",
                    "severity": "info",
                    "detail": f"Recorded fail-closed diagnostics for {len(fail_closed_events)} missing director route(s)",
                    "count": len(fail_closed_events),
                }
            )

        stage_signal_path = ""
        if stage_signals:
            stage_signal_path = self._write_stage_signal_artifact(
                stage="director_dispatch",
                run_id=run.id,
                signals=stage_signals,
            )

        dispatch_payload: dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "factory_stage_executor",
            "factory_run_id": run.id,
            "orchestration_run_id": str((last_command_result.run_id if last_command_result else "") or "").strip(),
            "status": str((final_result.status if final_result else stage_status) or "").strip(),
            "message": str((final_result.message if final_result else "") or "").strip(),
            "metadata": final_result.metadata if (final_result and isinstance(final_result.metadata, dict)) else {},
            "taskboard": {
                "initial": initial_stats,
                "final": final_stats,
                "converged": converged,
                "requires_convergence": requires_taskboard_convergence,
            },
            "attempts": attempts,
            "signals": stage_signals,
            "fail_closed_route_events": fail_closed_events,
            "per_binding_route_events": per_binding_route_events,
            "quality_gate_handoff": False,
            "canonical_authority": {
                "source": "run_ledger_projection",
                "authorized": final_authority.director_stage_authorized,
                "reason_code": final_authority.reason_code,
                "detail": final_authority.detail,
                "task_count": final_authority.task_count,
                "incomplete_task_ids": list(final_authority.incomplete_task_ids),
            },
            "failure_stage": "director_dispatch" if stage_status == "failed" else "",
            "error_code": error_code or None,
            "root_cause_hint": root_cause_hint or None,
            "evidence_paths": {
                "plan": "tasks/plan.json" if self._artifact_exists("tasks/plan.json", min_chars=1) else "",
                "dispatch_log": "dispatch/log.json",
                "stage_signals": stage_signal_path,
            },
        }
        self._write_json_artifact("dispatch/log.json", dispatch_payload)
        artifacts = ["dispatch/log.json"]
        self._mirror_director_artifacts(run.id, artifacts)
        if stage_signal_path:
            artifacts.append(stage_signal_path)
        inflight_run_continues = execution_barrier_timeout_observed or any(
            bool(metadata.get("inflight_run_continues"))
            for attempt in attempts
            if isinstance(attempt, dict)
            for metadata in [attempt.get("metadata")]
            if isinstance(metadata, dict)
        )
        settlement_metadata: dict[str, Any] = {
            "child_sessions_settled": not inflight_run_continues,
            "inflight_run_continues": inflight_run_continues,
            "settlement_source": "director_dispatch_settlement_barrier",
        }
        # R165/M06: multi-task Director often times out with partial files on disk
        # (package.json + src) while quality_gate never runs because the stage
        # failed. Run materialization-quality schedule once before leaving
        # director_dispatch so smoke tests and covered tsc repairs still land.
        if stage_status != "cancelled":
            materialization_settle = await self._run_director_stage_materialization_quality_settle(
                run=run,
                stage_status=stage_status,
                error_code=error_code,
            )
            if materialization_settle:
                settlement_metadata["director_stage_materialization_quality_settle"] = materialization_settle
                stage_signals.append(
                    {
                        "code": "director.stage_materialization_quality_settle",
                        "severity": "info",
                        "detail": str(materialization_settle.get("detail") or "materialization quality settle ran"),
                        "ok": bool(materialization_settle.get("ok")),
                        "tool_result_count": int(materialization_settle.get("tool_result_count") or 0),
                        "diagnostic_count": int(materialization_settle.get("diagnostic_count") or 0),
                        "reason": str(materialization_settle.get("reason") or ""),
                    }
                )
            # R177/M06: multi-task timeout claims materialization for TASK-N (lifecycle
            # requirement) but never reaches execute_method's no-tools seal path →
            # TOOL_LIFECYCLE_MISSING. Seal blocked incomplete receipts for missing
            # required tasks after settle so ledger integrity distinguishes incomplete
            # work from true missing evidence.
            lifecycle_seal = self._seal_director_stage_missing_tool_lifecycles(
                run=run,
                incomplete_task_ids=list(final_authority.incomplete_task_ids),
            )
            if lifecycle_seal:
                settlement_metadata["director_stage_missing_tool_lifecycle_seal"] = lifecycle_seal
                stage_signals.append(
                    {
                        "code": "director.stage_missing_tool_lifecycle_seal",
                        "severity": "info",
                        "detail": str(lifecycle_seal.get("detail") or "sealed missing tool lifecycles"),
                        "ok": bool(lifecycle_seal.get("ok")),
                        "sealed_count": int(lifecycle_seal.get("sealed_count") or 0),
                        "missing_before": list(lifecycle_seal.get("missing_before") or ()),
                    }
                )
            if materialization_settle or lifecycle_seal:
                # R181/M06: settle can complete on-disk delivery after authority was
                # evaluated. Reconcile boundary against workspace + re-evaluate so
                # false task_runtime_not_converged / canonical_task_boundary_missing
                # does not terminal-fail a stage that already real-runs green.
                recovered = self._recover_director_stage_authority_after_delivery_settle(
                    run=run,
                    context=context,
                    prior_authority=final_authority,
                )
                if recovered is not None and recovered.director_stage_authorized:
                    final_authority = recovered
                    stage_status = "success"
                    error_code = ""
                    root_cause_hint = ""
                    dispatch_payload["status"] = stage_status
                    dispatch_payload["error_code"] = None
                    dispatch_payload["root_cause_hint"] = None
                    dispatch_payload["canonical_authority"] = {
                        "source": "run_ledger_projection",
                        "authorized": True,
                        "reason_code": final_authority.reason_code,
                        "detail": final_authority.detail,
                        "task_count": final_authority.task_count,
                        "incomplete_task_ids": list(final_authority.incomplete_task_ids),
                        "recovered_after_delivery_settle": True,
                    }
                    stage_signals.append(
                        {
                            "code": "director.stage_authority_recovered_after_delivery_settle",
                            "severity": "info",
                            "detail": (
                                "Canonical director authority recovered after materialization "
                                "settle reconciled on-disk delivery with task-boundary verdicts"
                            ),
                            "reason_code": final_authority.reason_code,
                        }
                    )
                if stage_signal_path or stage_signals:
                    # Refresh signal artifact with settle / seal evidence.
                    stage_signal_path = self._write_stage_signal_artifact(
                        stage="director_dispatch",
                        run_id=run.id,
                        signals=stage_signals,
                    )
                    dispatch_payload["signals"] = stage_signals
                    dispatch_payload["evidence_paths"]["stage_signals"] = stage_signal_path
                    self._write_json_artifact("dispatch/log.json", dispatch_payload)
        if stage_status == "cancelled":
            return StageResult(
                stage="director_dispatch",
                status="cancelled",
                output=f"Director dispatch cancelled: {(final_result.message if final_result else 'N/A')}",
                artifacts=artifacts,
                metadata=settlement_metadata,
            )
        return StageResult(
            stage="director_dispatch",
            status=stage_status,
            output=(
                f"Director dispatch {(final_result.status if final_result else 'unknown')}: "
                f"{(final_result.message if final_result else 'N/A')}; "
                f"signals={len(stage_signals)}; "
                f"error_code={error_code or 'none'}; root_cause_hint={root_cause_hint or 'none'}"
            ),
            artifacts=artifacts,
            metadata=settlement_metadata,
        )

    @staticmethod
    def _bool_from_context_or_env(
        context: dict[str, Any],
        *keys: str,
        env_var: str = "",
        default: bool = True,
    ) -> bool:
        return helpers.bool_from_context_or_env(context, *keys, env_var=env_var, default=default)

    def _load_package_scripts(self) -> dict[str, str]:
        return self._workspace_quality.load_package_scripts()

    def _workspace_quality_commands(self, context: dict[str, Any]) -> list[list[str]]:
        return self._workspace_quality.workspace_quality_commands(context)

    @staticmethod
    def _canonical_project_id(context: dict[str, Any]) -> str:
        return str(
            context.get("project_id")
            or context.get("requested_project_id")
            or context.get("factory_bench_project_id")
            or ""
        ).strip()

    def _canonical_factory_projection(
        self,
        run: FactoryRun,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Load the canonical Factory run-tree projection.

        The same-cell adapter owns workspace/factory/project scoping. Missing
        or malformed facts return an empty projection so all callers fail
        closed through the pure authority evaluator.
        """

        try:
            projection = load_run_ledger_projection(
                self.workspace,
                run_id=run.id,
                factory_run_id=run.id,
                project_id=self._canonical_project_id(context),
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("Canonical Factory projection unavailable for run %s: %s", run.id, exc)
            return {}
        try:
            task_runtime_projection = TaskRuntimeService(str(self.workspace)).query_observable_task_rows_projection()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning(
                "Canonical TaskRuntime projection unavailable for run %s: %s",
                run.id,
                exc,
            )
            return projection
        task_runtime_authority = task_runtime_projection.to_authority_dict(factory_run_id=run.id)
        # Factory portfolios also create internal settlement / verification
        # TaskRuntime rows under the same factory_run_id. Director completion
        # authority owns only the immutable, committed PM contract tasks.
        # Mutable workspace mirrors are never completion authority.
        proof = context.get(PM_STAGE_ARTIFACT_BINDING_CONTEXT_KEY)
        contract_task_ids: list[str] = []
        if (
            isinstance(proof, RevalidatedPMStageArtifactBindingV1)
            and proof.binding.factory_run_id == run.id
            and proof.binding.stage == "pm_planning"
        ):
            contract_task_ids = [
                helpers._canonical_task_id_token(task_id)
                for task_id in proof.task_ids
                if helpers._canonical_task_id_token(task_id)
            ]
        expected_task_ids = set(contract_task_ids)
        authority_rows = task_runtime_authority.get("rows")
        scoped_rows = (
            [
                row
                for row in authority_rows
                if isinstance(row, dict) and helpers._canonical_task_id_token(row.get("task_id")) in expected_task_ids
            ]
            if isinstance(authority_rows, list)
            else []
        )
        task_runtime_authority["rows"] = scoped_rows
        task_runtime_authority["row_count"] = len(scoped_rows)
        task_runtime_authority["owner_scope"] = (
            "pm_contract_tasks" if contract_task_ids else "pm_contract_binding_invalid"
        )
        # Keep duplicates so the pure evaluator can reject PM aliases such as
        # ``1`` plus ``TASK-1`` instead of silently collapsing obligations.
        task_runtime_authority["owned_task_ids"] = sorted(contract_task_ids)
        projection["task_runtime_projection"] = task_runtime_authority
        return projection

    def _workspace_quality_task_boundary_blocker(
        self,
        run: FactoryRun,
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Block workspace validation until canonical task boundaries settle."""

        authority = helpers.evaluate_canonical_factory_authority(self._canonical_factory_projection(run, context))
        if authority.director_stage_authorized:
            return None
        failure_class = authority.failure_class or (
            FailureClassV1.DEPENDENCY_NOT_UNLOCKED.value
            if not authority.task_boundary_present
            else FailureClassV1.INCOMPLETE_MATERIALIZATION.value
        )
        return {
            "schema_version": "factory.workspace_quality.task_boundary_blocker.v2",
            "reason_code": authority.reason_code,
            "failure_class": failure_class,
            "responsible_layer": authority.responsible_layer or "task_boundary",
            "task_count": authority.task_count,
            "incomplete_task_ids": list(authority.incomplete_task_ids),
            "detail": authority.detail,
            "authority_source": "run_ledger_projection",
        }

    @staticmethod
    def _trim_command_output(text: str, limit: int = _WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS) -> str:
        return helpers.trim_command_output(text, limit)

    def _run_workspace_quality_command(self, command: list[str], timeout_seconds: float) -> dict[str, Any]:
        return self._workspace_quality.run_command(command, timeout_seconds)

    @staticmethod
    def _resolve_workspace_quality_command(command: list[str]) -> list[str]:
        return helpers.resolve_workspace_quality_command(command)

    def _workspace_quality_repair_errors(self, results: list[dict[str, Any]]) -> list[str]:
        errors: list[str] = []
        for result in results:
            if bool(result.get("passed")):
                continue
            output_parts = [
                str(result.get(key) or "").strip()
                for key in ("error", "stdout_tail", "stderr_tail")
                if str(result.get(key) or "").strip()
            ]
            if not output_parts:
                continue
            command = result.get("command")
            command_text = " ".join(str(part) for part in command) if isinstance(command, list) else str(command or "")
            output = self._trim_command_output("\n".join(output_parts))
            # The command row is durable verifier evidence, but its wrapper is
            # not itself a repair diagnostic.  Feeding the entire wrapper into
            # Director Runtime makes the actionable nested compiler/runtime
            # diagnostic compete with generic ``workspace_validation_failed``
            # rows.  Coverage then fails closed even when an executable repair
            # binding exists.  Project through the public Director diagnostic
            # normalizer and transport only actionable raw diagnostics.  Keep
            # the wrapper as a fail-closed fallback when no actionable signal
            # can be extracted; command/phase/stdout/stderr provenance remains
            # authoritative in ``workspace-validation.json.commands``.
            try:
                from polaris.cells.director.runtime.public import normalize_director_repair_diagnostics

                diagnostics = normalize_director_repair_diagnostics((output,))
            except (ImportError, RuntimeError, TypeError, ValueError):
                diagnostics = ()
            actionable = [
                diagnostic
                for diagnostic in diagnostics
                if str(diagnostic.code or "").strip()
                not in {"artifact_quality_error", "workspace_validation_failed"}
            ]
            if actionable:
                errors.extend(
                    str(diagnostic.metadata.get("raw") or diagnostic.message or "").strip()
                    for diagnostic in actionable
                    if str(diagnostic.metadata.get("raw") or diagnostic.message or "").strip()
                )
            else:
                errors.append(
                    "Artifact quality scan failed: workspace validation command failed"
                    f" ({command_text or 'unknown command'}): {output}"
                )

        try:
            from polaris.kernelone.quality import scan_workspace_artifact_quality_evidence

            evidence = scan_workspace_artifact_quality_evidence(str(self.workspace))
            errors.extend(evidence.errors)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            errors.append(f"Artifact quality scan failed: workspace quality repair scan failed: {exc}")

        deduped: list[str] = []
        seen: set[str] = set()
        for error in errors:
            normalized = str(error or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    def _workspace_quality_repair_issue_payloads(
        self,
        artifact_quality_errors: list[str],
    ) -> tuple[dict[str, Any], ...]:
        if not artifact_quality_errors:
            return ()
        try:
            from polaris.kernelone.quality import (
                artifact_quality_issues_for_errors,
                scan_workspace_artifact_quality_evidence,
            )

            evidence = scan_workspace_artifact_quality_evidence(str(self.workspace))
            return artifact_quality_issues_for_errors(artifact_quality_errors, evidence.issues)
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):
            pass
        try:
            from polaris.kernelone.quality import artifact_quality_issues_from_errors

            return artifact_quality_issues_from_errors(str(item) for item in artifact_quality_errors or [])
        except (ImportError, RuntimeError, TypeError, ValueError):
            return ()

    def _workspace_quality_repair_coverage_report(self, artifact_quality_errors: list[str]) -> dict[str, Any]:
        if not artifact_quality_errors:
            return {}
        try:
            from polaris.cells.director.runtime.public import (
                QueryDirectorRepairCoverageV1,
                query_director_repair_coverage,
            )

            return query_director_repair_coverage(
                QueryDirectorRepairCoverageV1(
                    artifact_quality_errors=tuple(str(item) for item in artifact_quality_errors),
                    artifact_quality_issues=self._workspace_quality_repair_issue_payloads(artifact_quality_errors),
                )
            ).to_dict()
        except (ImportError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "schema_version": "factory.workspace_quality_repair_coverage_query_error.v1",
                "source": "factory_stage_executor",
                "access": "read_only",
                "coverage_query_failed": True,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "total_diagnostics": len(artifact_quality_errors),
                "coverage_gap_count": 0,
                "coverage_gaps": [],
            }

    def _workspace_quality_repair_plan_probe_report(self, artifact_quality_errors: list[str]) -> dict[str, Any]:
        if not artifact_quality_errors:
            return {}
        try:
            from polaris.cells.director.runtime.public import (
                QueryDirectorRepairPlanProbeV1,
                query_director_repair_plan_probe,
            )

            return query_director_repair_plan_probe(
                QueryDirectorRepairPlanProbeV1(
                    artifact_quality_errors=tuple(str(item) for item in artifact_quality_errors),
                    artifact_quality_issues=self._workspace_quality_repair_issue_payloads(artifact_quality_errors),
                    base_files=self._workspace_quality_repair_plan_probe_base_files(artifact_quality_errors),
                    metadata={
                        "source": "factory_stage_executor.workspace_quality",
                        "coverage_is_not_planning": True,
                    },
                )
            ).to_dict()
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "schema_version": "factory.workspace_quality_repair_plan_probe_query_error.v1",
                "source": "factory_stage_executor",
                "access": "read_only",
                "plan_probe_query_failed": True,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "total_diagnostics": len(artifact_quality_errors),
                "status": "plan_probe_unavailable",
                "coverage_is_not_planning": True,
            }

    def _workspace_quality_repair_plan_probe_base_files(self, artifact_quality_errors: list[str]) -> dict[str, str]:
        workspace_root = self.workspace.resolve()
        candidates: list[str] = []
        candidates.extend(self._workspace_quality_repair_diagnostic_target_files(artifact_quality_errors))
        candidates.extend(self._workspace_quality_repair_target_files())
        base_files: dict[str, str] = {}
        for raw_candidate in candidates:
            normalized = os.path.normpath(str(raw_candidate or "").strip().replace("\\", "/")).replace("\\", "/")
            if not normalized or normalized in base_files or not _is_workspace_quality_repair_path(normalized):
                continue
            path = (workspace_root / normalized).resolve()
            try:
                if not path.is_relative_to(workspace_root) or not path.is_file():
                    continue
                if path.stat().st_size > 256_000:
                    continue
                base_files[normalized] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            if len(base_files) >= 64:
                break
        return base_files

    def _director_stage_should_run_materialization_quality_settle(
        self,
        *,
        stage_status: str,
        error_code: str,
    ) -> bool:
        """Whether director_dispatch should run a final materialization settle pass.

        Always run when the workspace already has delivery scaffolding (package /
        sources), including failed/timeout multi-task stages. Cancelled stages skip.
        """

        if str(stage_status or "").strip().lower() == "cancelled":
            return False
        if (self.workspace / "package.json").is_file():
            return True
        if any(self.workspace.rglob("*.ts")) or any(self.workspace.rglob("*.tsx")):
            return True
        if any(self.workspace.rglob("*.py")) or any(self.workspace.rglob("*.go")):
            return True
        # Still settle on explicit multi-task incompleteness even if scan is empty
        # (defensive: path may be mid-write).
        code = str(error_code or "").strip().lower()
        return code in {
            "director.canonical_task_boundary_missing",
            "director.dispatch_timeout",
            "director.taskboard_not_converged",
            "director.execution_barrier_timeout",
        }

    def _workspace_has_delivery_surface(self) -> bool:
        """True when package + source surface exists (real-run-capable scaffold)."""

        if not (self.workspace / "package.json").is_file():
            return False
        return (
            any(self.workspace.rglob("*.ts")) or any(self.workspace.rglob("*.tsx")) or any(self.workspace.rglob("*.py"))
        )

    def _recover_director_stage_authority_after_delivery_settle(
        self,
        *,
        run: FactoryRun,
        context: dict[str, Any],
        prior_authority: helpers.CanonicalFactoryAuthority,
    ) -> helpers.CanonicalFactoryAuthority | None:
        """Re-evaluate Director authority from canonical post-settle facts.

        TaskRuntime history remains immutable. Recovery is allowed only when
        every non-completed PM-contract task is terminal and canonical
        TaskBoundary evidence independently proves ``completed_verified``.
        Active, blocked, disk-only, or synthetic evidence remains fail-closed.
        """

        if prior_authority.director_stage_authorized:
            return prior_authority
        # Re-read only canonical owner facts after settle. TaskRuntime history
        # remains immutable; recovery is allowed solely when every contract task
        # has a canonical completed_verified boundary with ledger coordinates and
        # evidence, while every non-completed runtime row is terminal. No disk
        # scan or synthetic verdict may authorize this transition.
        try:
            projection = self._canonical_factory_projection(run, context)
            latest_authority = helpers.evaluate_canonical_factory_authority(projection)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("Director stage authority re-eval after settle failed: %s", exc)
            return None
        return helpers.recover_terminal_runtime_delivery_authority(
            projection,
            latest_authority,
        )

    def _seal_director_stage_missing_tool_lifecycles(
        self,
        *,
        run: FactoryRun,
        incomplete_task_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """R177/M06: seal blocked lifecycle for claimed materialization without tools.

        Multi-task timeout leaves TASK-N claimed in TaskRuntime (tool-lifecycle
        requirement via director_materialization_claimed) but never reaches
        execute_method's no_materialized_changes seal. Projection then reports
        TOOL_LIFECYCLE_MISSING even though claim/fail facts exist. Append one
        blocked incomplete receipt per missing required task so integrity can
        distinguish incomplete work from true missing evidence.

        Complexity:
            O(t + o) over tool-lifecycle events and requirement obligations.
        """

        from polaris.cells.control_plane.run_ledger.public import (
            AppendToolCallLifecycleEventCommandV1,
            append_tool_call_lifecycle_event,
            build_claimed_materialization_without_tool_lifecycle_receipt,
        )

        try:
            projection = load_run_ledger_projection(
                self.workspace,
                run_id=str(run.id or "").strip(),
                factory_run_id=str(run.id or "").strip(),
                project_id="",
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning(
                "Director stage lifecycle seal skipped: projection unavailable for run %s: %s",
                run.id,
                exc,
            )
            return {
                "ok": False,
                "reason": "projection_unavailable",
                "detail": f"{type(exc).__name__}: {exc}",
                "sealed_count": 0,
                "missing_before": [],
            }

        tool_lifecycle = projection.get("tool_lifecycle")
        lifecycle_map = tool_lifecycle if isinstance(tool_lifecycle, Mapping) else {}
        requirement_projection = lifecycle_map.get("requirement_projection")
        requirement_map = requirement_projection if isinstance(requirement_projection, Mapping) else {}
        missing_raw = lifecycle_map.get("missing_required_task_keys")
        if not isinstance(missing_raw, list) or not missing_raw:
            missing_raw = requirement_map.get("missing_required_task_keys")
        missing_keys = [
            str(item or "").strip()
            for item in (missing_raw if isinstance(missing_raw, list) else [])
            if str(item or "").strip()
        ]
        if not missing_keys:
            return {
                "ok": True,
                "reason": "no_missing_required_task_keys",
                "detail": "all claimed materialization tasks already have lifecycle evidence",
                "sealed_count": 0,
                "missing_before": [],
            }

        obligations_raw = requirement_map.get("obligations")
        obligations = (
            [dict(item) for item in obligations_raw if isinstance(item, Mapping)]
            if isinstance(obligations_raw, list)
            else []
        )
        obligation_by_key: dict[str, dict[str, Any]] = {}
        for obligation in obligations:
            task_key = str(obligation.get("task_key") or obligation.get("task_id") or "").strip()
            if task_key:
                obligation_by_key[task_key] = obligation

        incomplete_tokens = {
            str(item or "").strip().lower() for item in (incomplete_task_ids or ()) if str(item or "").strip()
        }
        sealed: list[dict[str, str]] = []
        for task_key in missing_keys:
            obligation = obligation_by_key.get(task_key) or {}
            task_id = str(obligation.get("task_id") or task_key or "").strip()
            run_id = str(obligation.get("run_id") or "").strip() or f"director-stage-{run.id}"
            if not task_id:
                continue
            # Prefer sealing incomplete multi-task claims; still seal any missing
            # required key so TOOL_LIFECYCLE_MISSING cannot stick after stage exit.
            task_token = task_id.lower().removeprefix("task-").removeprefix("task_")
            if incomplete_tokens and task_token not in incomplete_tokens and task_id.lower() not in incomplete_tokens:
                # Still seal: missing required is itself the defect to close.
                pass
            lifecycle = build_claimed_materialization_without_tool_lifecycle_receipt(
                run_id=run_id,
                task_id=task_id,
                turn_id="",
                role="director",
                reason="director_stage_incomplete_without_tools",
                failure_class=FailureClassV1.INCOMPLETE_MATERIALIZATION.value,
            )
            try:
                append_tool_call_lifecycle_event(
                    AppendToolCallLifecycleEventCommandV1(
                        workspace=str(self.workspace),
                        run_id=run_id,
                        task_id=task_id,
                        turn_id="",
                        role="director",
                        lifecycle_receipt=lifecycle,
                        stage="director_dispatch",
                        project_id=task_id,
                        ok=False,
                    )
                )
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning(
                    "Director stage failed to seal missing tool lifecycle task=%s run=%s: %s",
                    task_id,
                    run_id,
                    exc,
                )
                continue
            sealed.append({"task_id": task_id, "run_id": run_id, "task_key": task_key})

        return {
            "ok": bool(sealed),
            "reason": "director_stage_incomplete_without_tools",
            "detail": (
                f"sealed {len(sealed)} missing tool lifecycle receipt(s) for claimed "
                f"materialization without tools (missing_before={missing_keys})"
            ),
            "sealed_count": len(sealed),
            "missing_before": missing_keys,
            "sealed": sealed,
        }

    def _collect_director_stage_materialization_diagnostics(self) -> list[str]:
        """Collect physical settle-time diagnostics from source and real verifiers.

        Compiler-only revalidation is not convergence.  A Director candidate may
        make ``tsc`` green while leaving the declared package test or static HTML
        entrypoint physically broken.  Collect all three surfaces up front so the
        existing repair schedule can admit the corresponding deterministic
        candidates in one same-task settle attempt.

        R167/M10: when package.json declares typescript but ``node_modules/.bin/tsc``
        is absent (quality_gate never ran after director fail), best-effort
        ``npm install`` so settle can feed real TS diagnostics into the schedule.

        R184/M06: also surface missing package.json test entrypoints so the
        materialization schedule can plan smoke tests even when tsc is clean
        (L1-01 residual: real_run green, test_files=0).
        """

        diagnostics: list[str] = []
        package_json = self.workspace / "package.json"
        if not package_json.is_file():
            return []
        try:
            from polaris.kernelone.quality import scan_workspace_artifact_quality

            diagnostics.extend(scan_workspace_artifact_quality(str(self.workspace)))
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning(
                "Director stage materialization artifact scan skipped for %s: %s",
                self.workspace,
                exc,
            )
        # Missing on-disk tests referenced by package.json scripts.test is a
        # first-class settle diagnostic (not a compiler error).
        try:
            payload = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            payload = None
        test_script = ""
        if isinstance(payload, Mapping):
            scripts = payload.get("scripts")
            if isinstance(scripts, Mapping):
                test_script = str(scripts.get("test") or "").strip()
            has_test_files = False
            tests_root = self.workspace / "tests"
            if tests_root.is_dir():
                has_test_files = any(
                    path.is_file()
                    and path.suffix.lower() in {".ts", ".tsx", ".js", ".mjs", ".cjs"}
                    and "test" in path.name.lower()
                    for path in tests_root.rglob("*")
                    if "node_modules" not in path.parts
                )
            if not has_test_files:
                has_test_files = any(
                    path.is_file()
                    and path.suffix.lower() in {".ts", ".tsx", ".js", ".mjs", ".cjs"}
                    and path.name.endswith((".test.ts", ".test.tsx", ".test.js", ".spec.ts", ".spec.js"))
                    for path in self.workspace.rglob("*")
                    if "node_modules" not in path.parts and path.parts[:1] != (".git",)
                )
            if test_script and not has_test_files:
                diagnostics.append(
                    "artifact_quality_error: missing test source files required by package.json "
                    f"scripts.test ({test_script[:160]}); expected tests/verify.test.ts or equivalent"
                )
        node_modules = self.workspace / "node_modules"
        tsc_bin = node_modules / ".bin" / "tsc"
        tsconfig = self.workspace / "tsconfig.json"
        if tsconfig.is_file():
            if not tsc_bin.is_file():
                self._ensure_director_stage_materialization_typescript_toolchain()
                tsc_bin = node_modules / ".bin" / "tsc"
            if tsc_bin.is_file():
                try:
                    completed = subprocess.run(
                        [str(tsc_bin), "-p", "tsconfig.json", "--noEmit"],
                        cwd=str(self.workspace),
                        capture_output=True,
                        text=True,
                        timeout=90,
                        check=False,
                    )
                except (OSError, TimeoutError, ValueError):
                    completed = None
                if completed is not None:
                    combined = f"{completed.stdout or ''}\n{completed.stderr or ''}"
                    diagnostics.extend(
                        line.strip()
                        for line in combined.splitlines()
                        if "error TS" in line or ": error " in line.lower()
                    )

        if test_script:
            try:
                test_result = subprocess.run(
                    ["npm", "test"],
                    cwd=str(self.workspace),
                    capture_output=True,
                    text=True,
                    timeout=_WORKSPACE_VALIDATION_TIMEOUT_SECONDS,
                    check=False,
                    env={**os.environ, "CI": "1"},
                )
            except subprocess.TimeoutExpired:
                diagnostics.append(
                    f"artifact_quality_error: npm test timed out after {_WORKSPACE_VALIDATION_TIMEOUT_SECONDS}s"
                )
            except (OSError, TimeoutError, ValueError) as exc:
                diagnostics.append(f"artifact_quality_error: npm test could not execute: {type(exc).__name__}: {exc}")
            else:
                if int(test_result.returncode or 0) != 0:
                    combined = f"{test_result.stdout or ''}\n{test_result.stderr or ''}".strip()
                    # Coverage normalisation treats each line as a separate
                    # diagnostic. Keep the command identity (``npm test``) and
                    # terminal error (for example ``Could not find ...``) in
                    # one record so an existing executable rule can match the
                    # complete verifier fact instead of seeing unrelated lines.
                    signal_lines = [
                        line.strip()
                        for line in combined.splitlines()
                        if line.strip() and not line.lstrip().startswith(">")
                    ]
                    compact = " ".join(signal_lines[-40:]) or " ".join(combined.split())
                    bounded = compact[-_WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS:]
                    diagnostics.append(
                        f"artifact_quality_error: npm test failed (exit={test_result.returncode}): {bounded}"
                    )

        return list(dict.fromkeys(item.strip() for item in diagnostics if str(item or "").strip()))[:200]

    def _ensure_director_stage_materialization_typescript_toolchain(self) -> None:
        """Best-effort npm install so settle can collect tsc diagnostics (R167)."""

        package_json = self.workspace / "package.json"
        if not package_json.is_file():
            return
        try:
            payload = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return
        if not isinstance(payload, Mapping):
            return
        deps: dict[str, Any] = {}
        for key in ("dependencies", "devDependencies"):
            raw = payload.get(key)
            if isinstance(raw, Mapping):
                deps.update(raw)
        has_typescript = any(str(name).lower() == "typescript" for name in deps)
        if not has_typescript:
            return
        try:
            subprocess.run(
                ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except (OSError, TimeoutError, ValueError) as exc:
            logger.warning(
                "Director stage materialization settle npm install skipped: %s",
                exc,
            )

    def _claim_director_stage_materialization_settle_attempt(
        self,
        *,
        run_id: str,
    ) -> tuple[str, int, TaskRuntimeExecutionAttemptIdentityV1]:
        """Claim a short director TaskRuntime attempt so settle repairs can write.

        Repair execution is DEO-gated: without a canonical attempt identity the
        schedule only projects ``deo_deferred_repair_attempt_required`` and never
        materializes smoke/tsc patches (R165/r166 residual).
        """

        external_task_id = f"factory-director-mat-settle:{run_id}:{uuid.uuid4().hex[:12]}"
        # R190/M06: each settle attempt needs a fresh TaskRuntime row. A fixed
        # external_task_id was terminal-closed (completed/failed) after the first
        # director wave; QA rework → second director_dispatch then failed claim with
        # ``task_terminal`` and skipped deferred DEO commits (L1-01 r10:
        # diagnostics=5, tools=0, committed=0, settle_exception task_terminal).
        task_runtime = TaskRuntimeService(str(self.workspace))
        row = task_runtime.ensure_task_row(
            external_task_id=external_task_id,
            subject="Director stage materialization quality settle",
            description=(
                "End-of-director_dispatch materialization quality settle for partial "
                "multi-task completion / stage timeout"
            ),
            metadata={
                "factory_run_id": run_id,
                "factory_stage": "director_dispatch",
                "role": "director",
                "execution_identity_required": True,
                "materialization_quality_settle": True,
                "settle_attempt_id": external_task_id,
            },
        )
        task_row_id = task_runtime.normalize_task_id(row.get("id"))
        if task_row_id is None:
            raise RuntimeError("director_stage_materialization_settle_task_id_invalid")
        binding = bind_runtime_task_to_factory_run(
            BindRuntimeTaskToFactoryRunCommandV1(
                workspace=str(self.workspace),
                task_id=external_task_id,
                factory_run_id=run_id,
            )
        )
        if not binding.ok:
            raise RuntimeError(f"director_stage_materialization_settle_binding_failed:{binding.code}")
        claim = task_runtime.claim_execution(
            task_row_id,
            worker_id="director",
            role_id="director",
            run_id=run_id,
            lease_ttl_seconds=300,
            selection_source="factory_stage_executor.director_stage_materialization_settle",
            external_task_id=external_task_id,
            context_summary="director_stage_materialization_quality_settle",
            metadata={
                "factory_run_id": run_id,
                "factory_stage": "director_dispatch",
                "materialization_quality_settle": True,
                "execution_identity_required": True,
            },
        )
        session = claim.get("session") if isinstance(claim, dict) else None
        attempt_record = claim.get("execution_attempt") if isinstance(claim, dict) else None
        if (
            not isinstance(session, Mapping)
            or not isinstance(attempt_record, Mapping)
            or not bool(claim.get("success"))
        ):
            reason = str(claim.get("reason") or "unknown") if isinstance(claim, dict) else "invalid_claim_result"
            raise RuntimeError(f"director_stage_materialization_settle_claim_failed:{reason}")
        execution_attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(attempt_record)
        return external_task_id, task_row_id, execution_attempt

    @staticmethod
    def _materialization_settle_attempt_outcome(stage_status: str) -> TaskRuntimeExecutionAttemptSettlementOutcomeV1:
        """Map settle procedure stage_status to a terminal TaskRuntime outcome.

        R184/M06: never return suspended for factory-owned settle helper claims.
        """

        normalized = str(stage_status or "").strip().lower()
        if normalized in {"success", "completed", "ok", "passed"}:
            return "completed"
        return "failed"

    def _settle_director_stage_materialization_attempt(
        self,
        *,
        task_row_id: int,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        stage_status: str,
        summary: str,
    ) -> dict[str, Any]:
        """Terminal-close settle claim and return authoritative TaskRuntime result.

        R184/M06: when settle finished without file mutations the previous path
        mapped non-success ``stage_status`` to outcome=``suspended``. That left
        the helper TaskRuntime row pending (L1-01 incomplete_task_ids=['5']) and
        blocked ``task_runtime_not_completed`` even after solid delivery + boundary
        recovery. Factory-owned settle claims must terminal-close:
        success → completed, failure → failed. Never suspend.
        """

        del task_row_id  # identity carries the private row id
        try:
            normalized = str(stage_status or "").strip().lower()
            outcome = self._materialization_settle_attempt_outcome(stage_status)
            result = TaskRuntimeService(str(self.workspace)).settle_execution_attempt(
                SettleTaskRuntimeExecutionAttemptCommandV1(
                    workspace=execution_attempt.workspace,
                    identity=execution_attempt,
                    outcome=outcome,
                    summary=str(summary or "director_stage_materialization_quality_settle")[:500],
                    lock_timeout_seconds=5.0,
                    metadata={
                        "factory_stage": "director_dispatch",
                        "materialization_quality_settle": True,
                        "settle_stage_status": normalized or "unknown",
                    },
                )
            )
            if not bool(result.get("success")):
                logger.warning(
                    "Director stage materialization settle attempt close failed: %s",
                    result.get("reason") or "unknown",
                )
            return dict(result)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning(
                "Director stage materialization settle attempt close failed: %s",
                exc,
            )
            return {
                "success": False,
                "reason": f"settle_exception:{type(exc).__name__}",
                "detail": str(exc)[:500],
            }

    def _director_stage_materialization_settle_target_files(
        self,
        *,
        diagnostics: list[str],
    ) -> list[str]:
        """Resolve DEO write scope from owner targets plus plannable repairs.

        Some legitimate deterministic repairs create a derived target that is
        absent from the CE target list (for example
        ``dist/tests/verify.test.js`` referenced by the package verifier).  The
        repair-kernel plan probe is read-only authority for those changed paths;
        include them before minting the JobToken instead of letting DEO reject a
        valid existing repair as out of scope.
        """

        target_files = self._workspace_quality_repair_target_files()
        if not target_files:
            target_files = self._workspace_quality_repair_diagnostic_target_files(diagnostics)
        if not target_files:
            target_files = self._workspace_quality_repair_changed_files()
        planned_paths: list[str] = []
        probe = self._workspace_quality_repair_plan_probe_report(diagnostics)
        probe_items = probe.get("items") if isinstance(probe, Mapping) else None
        if isinstance(probe_items, list):
            for item in probe_items:
                if not isinstance(item, Mapping) or str(item.get("status") or "") != "covered_plannable":
                    continue
                changed_paths = item.get("changed_paths")
                if not isinstance(changed_paths, list):
                    continue
                for raw_path in changed_paths:
                    normalized = os.path.normpath(str(raw_path or "").strip().replace("\\", "/")).replace("\\", "/")
                    if normalized and _is_workspace_quality_repair_path(normalized):
                        planned_paths.append(normalized)
        extras: list[str] = []
        for candidate in (
            "package.json",
            "tsconfig.json",
            "tests/verify.test.ts",
            "tests/smoke.test.ts",
            "tests/unit/smoke.test.ts",
        ):
            if candidate not in target_files:
                extras.append(candidate)
        return list(dict.fromkeys([*target_files, *planned_paths, *extras]))

    def _director_stage_materialization_settle_commit_context(
        self,
        *,
        run: FactoryRun,
        run_id: str,
        diagnostics: list[str],
    ) -> dict[str, Any]:
        """Build DEO commit context with control-plane JobToken evidence (M06).

        Deferred materialization commit refuses synthetic attempt-only tokens.
        Mint a stage-scoped JobToken from factory run + CE blueprint surface so
        capability_audit.ok is true and execution_envelope_hash is bound.
        """

        from polaris.cells.control_plane.run_ledger.public import stable_hash
        from polaris.cells.factory.pipeline.internal.run_ledger import build_job_token_from_record

        target_files = self._director_stage_materialization_settle_target_files(diagnostics=diagnostics)
        blueprint_artifact, blueprint_text = self._workspace_quality_repair_blueprint_evidence(run_id=run_id)
        project_id = str(getattr(run.config, "name", "") or "").strip() or run_id
        token_record: dict[str, Any] = {
            "target_files": target_files,
            "allowed_paths": target_files,
            "code_files": [
                path for path in target_files if path not in {"tests/verify.test.ts", "tests/smoke.test.ts"}
            ],
            "contract_goal": f"director_stage_materialization_settle:{run_id}",
            "brief": "Factory director_dispatch materialization quality settle",
            "factory_run_id": run_id,
            "run_id": run_id,
            "project_id": project_id,
            "factory_workspace_quality_repair": {
                "run_id": run_id,
                "target_files": target_files,
                "ce_blueprint_artifact": blueprint_artifact,
            },
        }
        if blueprint_text:
            token_record["blueprint_id"] = blueprint_artifact or f"factory-blueprint:{run_id}"
            token_record["blueprints"] = [
                {
                    "id": token_record["blueprint_id"],
                    "artifact": blueprint_artifact,
                    "evidence_chars": len(blueprint_text),
                }
            ]
            token_record["chief_engineer"] = {
                "blueprint_id": token_record["blueprint_id"],
                "artifact": blueprint_artifact,
            }
        else:
            # Still satisfy capability_audit CE source when live blueprint artifact
            # is unavailable at settle (multi-task timeout residual path).
            token_record["blueprint_id"] = f"factory-director-mat-settle:{run_id}"
            token_record["blueprints"] = [{"id": token_record["blueprint_id"], "source": "settle_stage"}]
            token_record["chief_engineer"] = {
                "blueprint_id": token_record["blueprint_id"],
                "source": "director_stage_materialization_settle",
            }

        job_token = build_job_token_from_record(
            token_record,
            run_id=run_id,
            project_id=project_id,
            stage="director_materialization_settle",
        ).to_dict()
        envelope_hash = stable_hash(
            {
                "schema_version": "factory.director_stage_materialization_settle_envelope.v1",
                "run_id": run_id,
                "stage": "director_materialization_settle",
                "target_files": target_files,
                "token_id": str(job_token.get("token_id") or ""),
            }
        )
        job_token["execution_envelope_hash"] = envelope_hash
        token_hash = stable_hash(job_token)
        # Deferred DEO commit (_capability_token_from_context) requires root
        # capability_token_hash + envelope.authorization.capability_token_hash
        # matching stable_hash(token). Missing hash caused committed=0 with
        # silent skip ("authoritative write capability missing") on L1-01 R184.
        capability_audit = job_token.get("capability_audit")
        if not (isinstance(capability_audit, Mapping) and capability_audit.get("ok") is True):
            logger.warning(
                "Director stage materialization settle JobToken capability_audit not ok run=%s audit=%s",
                run_id,
                capability_audit,
            )

        write_paths = list(job_token.get("allowed_write_paths") or target_files)
        read_paths = list(job_token.get("allowed_read_paths") or write_paths)
        if not write_paths:
            write_paths = list(target_files)
        if not read_paths:
            read_paths = list(write_paths)
        # Keep token path lists authoritative for DEO capability equality checks.
        job_token["allowed_write_paths"] = write_paths
        job_token["allowed_read_paths"] = read_paths
        # Re-hash after path normalization so root hash matches the final token body.
        token_hash = stable_hash(job_token)
        authorization = {
            "capability_token_ref": str(job_token.get("token_id") or ""),
            "capability_token_hash": token_hash,
            "allowed_write_paths": list(write_paths),
            "allowed_read_paths": list(read_paths),
        }
        execution_envelope = {
            "envelope_hash": envelope_hash,
            "authorization": authorization,
            "stage": "director_materialization_settle",
            "run_id": run_id,
        }
        return {
            "target_files": target_files,
            "allowed_paths": list(write_paths),
            "allowed_write_paths": list(write_paths),
            "allowed_read_paths": list(read_paths),
            "delivery_mode": "materialize_changes",
            "factory_stage": "director_dispatch",
            "materialization_quality_settle": True,
            "capability_token_hash": token_hash,
            "job_token": job_token,
            "control_plane_job_token": job_token,
            "capability_token": job_token,
            "execution_envelope_hash": envelope_hash,
            "execution_envelope": execution_envelope,
            "director_execution_envelope": dict(execution_envelope),
            "task_execution_envelope": dict(execution_envelope),
        }

    @staticmethod
    def _director_stage_materialization_receipt_succeeded(receipt: Mapping[str, Any]) -> bool:
        """Interpret both legacy tool rows and canonical ``BatchReceipt`` rows.

        The DEO bridge returns normalized batch receipts whose authoritative
        outcome is expressed by ``success_count`` / ``failure_count`` and the
        nested result statuses.  It does not add a top-level ``success`` flag.
        Treating those rows like legacy tool-result dictionaries turned real
        ``RECEIPT_COMMITTED(succeeded)`` effects into Factory failures.
        """

        if "success" in receipt:
            return receipt.get("success") is True
        try:
            success_count = int(receipt.get("success_count", 0) or 0)
            failure_count = int(receipt.get("failure_count", 0) or 0)
        except (TypeError, ValueError):
            return False
        if failure_count > 0:
            return False
        if success_count > 0:
            return True
        results = receipt.get("results")
        if not isinstance(results, list) or not results:
            return False
        statuses = [str(item.get("status") or "").strip().lower() for item in results if isinstance(item, Mapping)]
        return len(statuses) == len(results) and bool(statuses) and all(status == "success" for status in statuses)

    async def _run_director_stage_materialization_quality_settle(
        self,
        *,
        run: FactoryRun,
        stage_status: str,
        error_code: str,
    ) -> dict[str, Any]:
        """Run materialization quality once at the end of director_dispatch (R165/M06).

        Live residual: Director multi-task timeout left package.json + src on disk
        but skipped quality_gate, so smoke/tests and covered tsc repairs never ran.
        Writes require a claimed TaskRuntime execution attempt + DEO commit of
        deferred repair effects, plus control-plane JobToken evidence.
        """

        if not self._director_stage_should_run_materialization_quality_settle(
            stage_status=stage_status,
            error_code=error_code,
        ):
            return {
                "ok": False,
                "reason": "settle_not_applicable",
                "detail": "workspace has no materializable surface or stage cancelled",
                "tool_result_count": 0,
                "diagnostic_count": 0,
            }
        diagnostics = self._collect_director_stage_materialization_diagnostics()
        run_id = str(run.id or "").strip() or "director-stage-settle"
        external_task_id = ""
        task_row_id: int | None = None
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None
        committed_receipts: list[dict[str, Any]] = []
        post_commit_diagnostics = list(diagnostics)
        deferred_candidates: list[Mapping[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        summary: Mapping[str, Any] = {}
        repair_round_count = 0
        try:
            from polaris.cells.roles.adapters.public import (
                commit_materialization_deferred_repairs,
            )
            from polaris.cells.runtime.task_runtime.public import (
                create_task_runtime_execution_attempt_authority,
            )

            external_task_id, task_row_id, execution_attempt = (
                self._claim_director_stage_materialization_settle_attempt(run_id=run_id)
            )
            authority = create_task_runtime_execution_attempt_authority(execution_attempt)
            current_diagnostics = list(diagnostics)
            seen_diagnostic_signatures = {tuple(current_diagnostics)}
            for round_index in range(_WORKSPACE_QUALITY_REPAIR_MAX_ROUNDS):
                repair_round_count += 1
                round_tool_results, summary = self._apply_workspace_quality_repairs(
                    run_id=run_id,
                    artifact_quality_errors=current_diagnostics,
                    task_id=external_task_id,
                    execution_attempt=execution_attempt,
                )
                tool_results.extend(round_tool_results)
                commit_context = self._director_stage_materialization_settle_commit_context(
                    run=run,
                    run_id=run_id,
                    diagnostics=current_diagnostics,
                )
                round_candidates = [
                    item
                    for item in round_tool_results
                    if isinstance(item, Mapping)
                    and isinstance(item.get("result"), Mapping)
                    and (
                        item["result"].get("deferred_request") is not None
                        or str(item["result"].get("status") or "").strip()
                        in {"deferred_repair_effects_pending", "deferred_command_effect_pending"}
                    )
                ]
                deferred_candidates.extend(round_candidates)
                if not round_candidates:
                    post_commit_diagnostics = current_diagnostics
                    break

                # Revalidate after each effect. A newly exposed verifier layer
                # is replanned inside this same TaskRuntime attempt; PM/CE do not
                # restart for ordinary code/test defects. Repeated diagnostic
                # signatures and the shared round cap stop no-progress loops.
                round_committed = False
                for candidate_index, candidate in enumerate(round_candidates):
                    candidate_receipts = await commit_materialization_deferred_repairs(
                        workspace=str(execution_attempt.workspace),
                        tool_results=[candidate],
                        execution_attempt=execution_attempt,
                        execution_attempt_authority=authority,
                        turn_id=(f"director-stage-mat-settle-{run_id}:round{round_index}:candidate{candidate_index}"),
                        context=commit_context,
                    )
                    committed_receipts.extend(candidate_receipts)
                    if not any(
                        isinstance(item, Mapping) and self._director_stage_materialization_receipt_succeeded(item)
                        for item in candidate_receipts
                    ):
                        continue
                    round_committed = True
                    self._ensure_director_stage_materialization_typescript_toolchain()
                    post_commit_diagnostics = self._collect_director_stage_materialization_diagnostics()
                    if not post_commit_diagnostics:
                        break
                if not post_commit_diagnostics:
                    break
                post_signature = tuple(post_commit_diagnostics)
                if not round_committed or post_signature in seen_diagnostic_signatures:
                    break
                seen_diagnostic_signatures.add(post_signature)
                current_diagnostics = list(post_commit_diagnostics)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning(
                "Director stage materialization quality settle failed for run %s: %s",
                run.id,
                exc,
            )
            if task_row_id is not None and execution_attempt is not None:
                self._settle_director_stage_materialization_attempt(
                    task_row_id=task_row_id,
                    execution_attempt=execution_attempt,
                    stage_status="failed",
                    summary=f"settle_exception:{type(exc).__name__}",
                )
            return {
                "ok": False,
                "reason": "settle_exception",
                "detail": f"{type(exc).__name__}: {exc}",
                "tool_result_count": 0,
                "diagnostic_count": len(diagnostics),
            }
        summary_dict = dict(summary) if isinstance(summary, Mapping) else {}
        deferred_expected = bool(deferred_candidates)
        successful_receipts = [
            dict(item)
            for item in committed_receipts
            if isinstance(item, Mapping) and self._director_stage_materialization_receipt_succeeded(item)
        ]
        failed_receipts = [
            dict(item)
            for item in committed_receipts
            if not isinstance(item, Mapping) or not self._director_stage_materialization_receipt_succeeded(item)
        ]
        # Partial DEO failures remain evidence, but must not erase a verified
        # successful repair candidate.  The post-commit verifier is the
        # authority for whether the same Director task still needs local rework.
        missing_commit_receipt = deferred_expected and not successful_receipts
        verifier_residual = bool(post_commit_diagnostics)
        commit_failed = missing_commit_receipt or verifier_residual
        mutated = bool(successful_receipts) or any(
            self._workspace_quality_repair_result_has_mutation(dict(item))
            for item in tool_results
            if isinstance(item, Mapping)
        )
        settlement_result: dict[str, Any] = {"success": True}
        if task_row_id is not None and execution_attempt is not None:
            settlement_result = self._settle_director_stage_materialization_attempt(
                task_row_id=task_row_id,
                execution_attempt=execution_attempt,
                stage_status="failed" if commit_failed else "success",
                summary=(
                    "director_stage_materialization_quality_settle "
                    f"mutated={mutated} committed={len(successful_receipts)} "
                    f"failed={len(failed_receipts)} tools={len(tool_results)}"
                ),
            )
        settlement_failed = settlement_result.get("success") is not True
        if commit_failed or settlement_failed:
            failure_reason = (
                "deferred_repair_commit_failed"
                if missing_commit_receipt
                else "materialization_verifier_residual"
                if verifier_residual
                else "settle_attempt_close_failed"
            )
            return {
                "ok": False,
                "reason": failure_reason,
                "detail": (
                    "materialization settle did not reach a verifier-clean terminal state "
                    f"(expected={deferred_expected}, receipts={len(committed_receipts)}, "
                    f"failed={len(failed_receipts)}, residual={len(post_commit_diagnostics)}, settle_reason="
                    f"{settlement_result.get('reason') or 'unknown'!s})"
                ),
                "tool_result_count": len(tool_results),
                "committed_receipt_count": len(successful_receipts),
                "failed_receipt_count": len(failed_receipts),
                "diagnostic_count": len(diagnostics),
                "post_commit_diagnostic_count": len(post_commit_diagnostics),
                "post_commit_diagnostics": post_commit_diagnostics[:20],
                "repair_round_count": repair_round_count,
                "mutated": mutated,
                "external_task_id": external_task_id,
            }
        return {
            "ok": True,
            "reason": "director_stage_settle",
            "detail": (
                "materialization quality schedule + deferred DEO commit at end of director_dispatch "
                f"(diagnostics={len(diagnostics)}, tools={len(tool_results)}, "
                f"committed={len(committed_receipts)}, mutated={mutated})"
            ),
            "tool_result_count": len(tool_results),
            "committed_receipt_count": len(successful_receipts),
            "failed_receipt_count": len(failed_receipts),
            "diagnostic_count": len(diagnostics),
            "post_commit_diagnostic_count": len(post_commit_diagnostics),
            "repair_round_count": repair_round_count,
            "mutated": mutated,
            "external_task_id": external_task_id,
            "summary_keys": sorted(str(key) for key in summary_dict)[:24],
        }

    def _apply_workspace_quality_repairs(
        self,
        *,
        run_id: str,
        artifact_quality_errors: list[str],
        task_id: str | None = None,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        from polaris.cells.roles.adapters.public.service import (
            run_director_materialization_quality_repair_schedule,
        )

        class _QualityRepairAdapter:
            def __init__(self, workspace: Path) -> None:
                self.workspace = str(workspace)
                self._execution = SimpleNamespace(_message_bus=None)

            def _update_task_progress(
                self,
                task_id: str,
                phase: str,
                current_file: str | None = None,
                event_code: str | None = None,
                event_status: str | None = None,
                event_reason: str | None = None,
                event_detail: str | None = None,
                event_refs: dict[str, Any] | None = None,
            ) -> None:
                del task_id, phase, current_file, event_code, event_status, event_reason, event_detail, event_refs

        target_files = self._workspace_quality_repair_target_files()
        if not target_files:
            target_files = self._workspace_quality_repair_diagnostic_target_files(artifact_quality_errors)
        if not target_files:
            target_files = self._workspace_quality_repair_changed_files()
        if "package.json" not in target_files and (self.workspace / "package.json").is_file():
            target_files = [*target_files, "package.json"]
        metadata: dict[str, Any] = {
            "target_files": target_files,
            "delivery_mode": "materialize_changes",
        }
        blueprint_artifact, blueprint_text = self._workspace_quality_repair_blueprint_evidence(run_id=run_id)
        # Always mark factory workspace-quality authority so multi-file smoke/tsc
        # plans are not strangled by per-task write scope (M06 settle + quality_gate).
        metadata["factory_workspace_quality_repair"] = {
            "ce_blueprint_artifact": blueprint_artifact,
            "target_files": target_files,
            "run_id": run_id,
        }
        if blueprint_text:
            blueprint_payload = {
                "schema_version": "factory.workspace_quality_repair.ce_blueprint_context.v1",
                "artifact": blueprint_artifact,
                "evidence": blueprint_text,
            }
            metadata["ce_blueprint"] = blueprint_payload
            metadata["chief_engineer_blueprint"] = blueprint_payload
            metadata["chief_engineer_blueprint_evidence"] = blueprint_text
        resolved_task_id = str(task_id or "").strip() or f"factory-quality-gate:{run_id}"
        return run_director_materialization_quality_repair_schedule(
            _QualityRepairAdapter(self.workspace),
            task={"target_files": target_files, "metadata": metadata},
            task_id=resolved_task_id,
            artifact_quality_errors=artifact_quality_errors,
            execution_attempt=execution_attempt,
        )

    def _apply_workspace_quality_cpp_post_repairs(self) -> list[dict[str, Any]]:
        has_cpp_project = any(self.workspace.rglob("*.cpp")) or (self.workspace / "CMakeLists.txt").is_file()
        if not has_cpp_project:
            return []
        try:
            from polaris.cells.roles.adapters.public.service import (
                run_director_post_execution_repair_schedule,
            )

            results, _summary = run_director_post_execution_repair_schedule(
                self.workspace,
                task_id="factory-workspace-quality-post-execution-repair",
            )
            return results
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return [
                {
                    "tool": "deterministic_cpp_post_repair",
                    "success": False,
                    "result": {
                        "source_tool": "deterministic_cpp_post_repair",
                        "error": str(exc),
                    },
                }
            ]

    def _workspace_quality_repair_target_files(self) -> list[str]:
        return self._collect_declared_delivery_targets(self._load_pm_plan_tasks("tasks/plan.json"))

    def _workspace_quality_repair_diagnostic_target_files(self, artifact_quality_errors: list[str]) -> list[str]:
        from polaris.cells.director.runtime.public import normalize_director_repair_diagnostics

        workspace_root = self.workspace.resolve()
        candidates: list[str] = []
        diagnostics = normalize_director_repair_diagnostics([str(item) for item in artifact_quality_errors or []])
        for diagnostic in diagnostics:
            path = str(diagnostic.path or "").strip().replace("\\", "/")
            if path and _is_workspace_quality_repair_path(path):
                candidates.append(path)
        joined_errors = "\n".join(str(item or "") for item in artifact_quality_errors).lower()
        for filename in _LANGUAGE_NEUTRAL_REPAIR_FILENAMES:
            if filename.lower() in joined_errors and (workspace_root / filename).is_file():
                candidates.append(filename)
        if ("include 'dom'" in joined_errors or "compiler option" in joined_errors or "tsconfig" in joined_errors) and (
            workspace_root / "tsconfig.json"
        ).is_file():
            candidates.append("tsconfig.json")
        if "package.json" in joined_errors and (workspace_root / "package.json").is_file():
            candidates.append("package.json")
        for source_path in list(candidates):
            candidates.extend(self._workspace_quality_relative_import_targets(source_path))
        return _dedupe_workspace_repair_paths(candidates)

    def _workspace_quality_relative_import_targets(self, source_path: str) -> list[str]:
        workspace_root = self.workspace.resolve()
        normalized_source = str(source_path or "").strip().replace("\\", "/")
        source = (workspace_root / normalized_source).resolve()
        try:
            if not source.is_relative_to(workspace_root) or not source.is_file():
                return []
        except ValueError:
            return []
        if source.suffix.lower() not in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
            return []
        with contextlib.suppress(OSError, UnicodeDecodeError):
            text = source.read_text(encoding="utf-8")
            targets: list[str] = []
            for match in re.finditer(
                r"(?:\bfrom\s+|\brequire\s*\(\s*|\bimport\s*\(\s*)['\"](?P<module>\.{1,2}/[^'\"]+)['\"]",
                text,
            ):
                targets.extend(
                    self._workspace_quality_resolve_relative_module(normalized_source, match.group("module"))
                )
            return targets
        return []

    def _workspace_quality_resolve_relative_module(self, importer: str, module_ref: str) -> list[str]:
        workspace_root = self.workspace.resolve()
        importer_dir = Path(importer).parent
        raw = (importer_dir / module_ref).as_posix()
        root, suffix = os.path.splitext(raw)
        candidates = [raw] if suffix else []
        candidates.extend(f"{root}{ext}" for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))
        candidates.extend(f"{root}/index{ext}" for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))
        resolved: list[str] = []
        for candidate in candidates:
            normalized = os.path.normpath(candidate).replace("\\", "/")
            path = (workspace_root / normalized).resolve()
            try:
                if path.is_relative_to(workspace_root) and path.is_file():
                    resolved.append(path.relative_to(workspace_root).as_posix())
            except ValueError:
                continue
        return resolved

    def _workspace_quality_repair_changed_files(self) -> list[str]:
        workspace_root = self.workspace.resolve()
        if not workspace_root.is_dir():
            return []
        ignored_parts = {".git", ".polaris", ".pytest_cache", "dist", "build", "coverage", "node_modules"}
        changed: list[str] = []
        for path in sorted(workspace_root.rglob("*"), key=lambda item: item.as_posix()):
            if not path.is_file():
                continue
            try:
                rel_path = path.relative_to(workspace_root)
            except ValueError:
                continue
            if any(part in ignored_parts for part in rel_path.parts):
                continue
            if path.suffix.lower() not in _WORKSPACE_QUALITY_REPAIR_SOURCE_SUFFIXES:
                continue
            changed.append(rel_path.as_posix())
            if len(changed) >= 120:
                break
        return changed

    def _workspace_quality_repair_blueprint_evidence(self, *, run_id: str) -> tuple[str, str]:
        if not run_id:
            return "", ""
        for candidate in (
            f"runtime/state/blueprints/{run_id}.review.json",
            f"runtime/blueprints/{run_id}.review.json",
            f"workspace/.polaris/blueprints/{run_id}.review.json",
            "workspace/.polaris/blueprints/latest.review.json",
        ):
            with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
                text = self._read_text_artifact(candidate, min_chars=2)
            if text:
                return candidate, self._compact_blueprint_evidence_for_repair(text)
        for candidate in (
            f".polaris/blueprints/{run_id}.review.json",
            ".polaris/blueprints/latest.review.json",
            f".polaris/roles/chief_engineer/{run_id}/review.json",
        ):
            text = ""
            with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError, UnicodeDecodeError):
                target = (self.workspace / candidate).resolve()
                if not target.is_relative_to(self.workspace.resolve()) or not target.is_file():
                    continue
                text = target.read_text(encoding="utf-8").strip()
            if len(text) >= 2:
                return f"workspace-local:{candidate}", self._compact_blueprint_evidence_for_repair(text)
        return "", ""

    def _workspace_quality_repair_original_message(self, *, run_id: str, target_files: list[str]) -> str:
        tasks = self._load_pm_plan_tasks("tasks/plan.json")
        lines: list[str] = [
            "Factory workspace quality repair contract:",
            "- Delivery mode: materialize changes into the workspace.",
        ]
        if target_files:
            lines.append("- Target files:")
            lines.extend(f"  - {item}" for item in target_files[:80])

        blueprint_artifact, blueprint_text = self._workspace_quality_repair_blueprint_evidence(run_id=run_id)
        if blueprint_text:
            lines.extend(
                [
                    "- Chief Engineer blueprint evidence:",
                    f"  artifact: {blueprint_artifact}",
                    blueprint_text,
                ]
            )
        else:
            lines.append("- Chief Engineer blueprint evidence: unavailable for this repair turn.")

        if tasks:
            lines.append("- PM task contract summary:")
        for index, task in enumerate(tasks[:20], start=1):
            title = str(task.get("title") or task.get("id") or f"TASK-{index}").strip()
            goal = str(task.get("goal") or task.get("description") or "").strip()
            scope = str(task.get("scope") or "").strip()
            task_targets = self._task_string_list(task, "target_files", "scope_paths")
            steps = self._task_string_list(task, "steps")
            acceptance = self._task_string_list(task, "acceptance", "acceptance_criteria")
            lines.append(f"  {index}. {title}")
            if goal:
                lines.append(f"     goal: {goal}")
            if scope:
                lines.append(f"     scope: {scope}")
            if task_targets:
                lines.append(f"     targets: {', '.join(task_targets[:16])}")
            if steps:
                lines.append(f"     steps: {'; '.join(steps[:4])}")
            if acceptance:
                lines.append(f"     acceptance: {'; '.join(acceptance[:4])}")
        return "\n".join(lines)[:12000]

    @staticmethod
    def _workspace_quality_llm_repair_timeout_seconds(context: dict[str, Any]) -> float:
        raw = context.get("workspace_quality_repair_llm_timeout_seconds")
        if raw is None:
            raw = os.environ.get(_WORKSPACE_QUALITY_REPAIR_LLM_TIMEOUT_ENV)
        try:
            value = float(str(raw))
        except (TypeError, ValueError):
            value = _DEFAULT_WORKSPACE_QUALITY_REPAIR_LLM_TIMEOUT_SECONDS
        configured = max(30.0, min(value, 3600.0))
        remaining_seconds = OrchestrationStageExecutor._factory_deadline_remaining_seconds(context)
        if remaining_seconds is None:
            return configured
        capped = max(1.0, remaining_seconds - _QUALITY_GATE_QA_DEADLINE_SAFETY_SECONDS)
        return max(1.0, min(configured, capped))

    async def _apply_workspace_quality_llm_repairs(
        self,
        *,
        run_id: str,
        context: dict[str, Any],
        artifact_quality_errors: list[str],
        repair_attempt: int,
        interface_discrepancy_evidence: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        changed_files = self._workspace_quality_repair_changed_files()
        if not changed_files:
            return [], {
                "attempted": False,
                "repair_mode": "director_llm",
                "reason": "no_workspace_source_files_for_repair",
                "source_tools": [],
                "tool_results": 0,
            }
        target_files = self._workspace_quality_repair_target_files()
        repair_context: dict[str, Any] = {
            "delivery_mode": "materialize_changes",
            "target_files": (target_files or changed_files)[:80],
            "changed_files": changed_files[:80],
            "factory_workspace_quality_repair": {
                "changed_files": changed_files[:80],
                "target_files": target_files[:80],
            },
        }
        catalog = self._read_catalog_contract()
        primary_language = str(catalog.get("primary_language") or "").strip()
        project_type = str(catalog.get("project_type") or "").strip()
        if primary_language:
            repair_context.setdefault("language", primary_language)
            repair_context.setdefault("programming_language", primary_language)
            repair_context.setdefault("tech_stack", {"language": primary_language})
        if project_type:
            repair_context.setdefault("project_type", project_type)
            repair_context.setdefault("project_kind", project_type)
        blueprint_artifact, blueprint_text = self._workspace_quality_repair_blueprint_evidence(run_id=run_id)
        if blueprint_text:
            blueprint_payload = {
                "schema_version": "factory.workspace_quality_repair.ce_blueprint_context.v1",
                "artifact": blueprint_artifact,
                "evidence": blueprint_text,
            }
            repair_context["ce_blueprint"] = blueprint_payload
            repair_context["chief_engineer_blueprint"] = blueprint_payload
            repair_context["chief_engineer_blueprint_evidence"] = blueprint_text
            repair_context["factory_workspace_quality_repair"]["ce_blueprint_artifact"] = blueprint_artifact
        if interface_discrepancy_evidence:
            repair_context["director_interface_discrepancy_retry"] = {
                "authorized": self._workspace_quality_interface_discrepancy_allows_director_retry(
                    interface_discrepancy_evidence
                ),
                "recommended_owner": interface_discrepancy_evidence.get("recommended_owner"),
                "recommended_route": interface_discrepancy_evidence.get("recommended_route"),
                "reason": interface_discrepancy_evidence.get("reason"),
                "interface_discrepancy_evidence": interface_discrepancy_evidence,
            }
            repair_context["factory_task_boundary_interface_discrepancy"] = interface_discrepancy_evidence
            repair_context["factory_workspace_quality_repair"]["interface_discrepancy_evidence"] = (
                interface_discrepancy_evidence
            )
        for key in (
            "language",
            "prompt_language",
            "programming_language",
            "artifact",
            "artifact_type",
            "project_kind",
            "prompt_profile_ids",
            "prompt_profiles",
            "prompt_profile",
            "prompt_profile_id",
        ):
            if key in context:
                repair_context[key] = context[key]
        task_metadata = dict(repair_context)
        task_metadata["target_files"] = target_files or changed_files
        try:
            from polaris.cells.roles.adapters.public.service import run_director_materialization_quality_repair

            results, summary = await run_director_materialization_quality_repair(
                str(self.workspace),
                task={"target_files": target_files or changed_files, "metadata": task_metadata},
                target_task_id=f"factory-quality-gate:{run_id}:llm-repair",
                run_id=run_id,
                context=repair_context,
                original_message=self._workspace_quality_repair_original_message(
                    run_id=run_id,
                    target_files=target_files,
                ),
                llm_call_timeout=self._workspace_quality_llm_repair_timeout_seconds(context),
                artifact_quality_errors=artifact_quality_errors,
                changed_files=changed_files,
                repair_attempt=repair_attempt,
            )
        except Exception as exc:  # noqa: BLE001 - fail closed around external LLM repair boundary.
            return [], {
                "attempted": True,
                "repair_mode": "director_llm",
                "success": False,
                "error": str(exc),
                "source_tools": ["director_materialization_quality_repair_error"],
                "tool_results": 0,
            }
        normalized_summary = dict(summary)
        normalized_summary["repair_mode"] = "director_llm"
        raw_source_tools = normalized_summary.get("source_tools")
        source_tool_items = raw_source_tools if isinstance(raw_source_tools, list | tuple | set) else []
        source_tools = [str(item) for item in source_tool_items if str(item or "").strip()]
        if results and "director_materialization_quality_repair" not in source_tools:
            source_tools.append("director_materialization_quality_repair")
        normalized_summary["source_tools"] = source_tools
        normalized_summary.setdefault("tool_results", len(results))
        normalized_summary.setdefault("attempted", True)
        return [dict(item) for item in results], normalized_summary

    @staticmethod
    def _workspace_quality_repair_result_has_mutation(item: dict[str, Any]) -> bool:
        if not isinstance(item, dict) or not bool(item.get("success")):
            return False
        raw_result = item.get("result")
        result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
        tool_name = str(
            item.get("tool")
            or item.get("tool_name")
            or result.get("tool")
            or result.get("tool_name")
            or result.get("operation")
            or ""
        ).strip()
        operation = str(result.get("operation") or "").strip()
        if tool_name in _WORKSPACE_QUALITY_MUTATION_TOKENS or operation in _WORKSPACE_QUALITY_MUTATION_TOKENS:
            return True
        before_hash = str(result.get("before_sha256") or "").strip()
        after_hash = str(result.get("after_sha256") or "").strip()
        return bool(before_hash and after_hash and before_hash != after_hash)

    @staticmethod
    def _workspace_quality_repair_evidence(repair_results: list[dict[str, Any]]) -> list[str]:
        evidence: list[str] = []
        for item in repair_results:
            if not isinstance(item, dict) or not bool(item.get("success")):
                continue
            raw_result = item.get("result")
            result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
            source_tool = str(result.get("source_tool") or item.get("source_tool") or "").strip()
            file_name = str(result.get("file") or result.get("path") or "").strip()
            operation = str(result.get("operation") or "").strip()
            if source_tool or file_name:
                evidence.append(
                    "repair_write:"
                    f"tool={source_tool or str(item.get('tool') or item.get('tool_name') or 'unknown')};"
                    f"file={file_name or 'unknown'};"
                    f"operation={operation or 'unknown'}"
                )
            before_hash = str(result.get("before_sha256") or "").strip()
            after_hash = str(result.get("after_sha256") or "").strip()
            if before_hash or after_hash:
                evidence.append(
                    f"repair_hash:file={file_name or 'unknown'};before={before_hash[:16]};after={after_hash[:16]}"
                )
            diff_excerpt = str(result.get("diff_excerpt") or "").strip()
            if diff_excerpt:
                compact_diff = " ".join(diff_excerpt.split())
                evidence.append(f"repair_diff:file={file_name or 'unknown'};excerpt={compact_diff[:360]}")
            if len(evidence) >= 12:
                break
        return evidence

    @staticmethod
    def _workspace_quality_summary_requires_task_boundary_triage(summary: dict[str, Any]) -> bool:
        if bool(summary.get("task_boundary_interface_discrepancy_retry_authorized")):
            return False
        stage = str(summary.get("stage") or "").strip()
        if stage == "runtime_plan_probe_unplannable":
            return True
        evidence = summary.get("interface_discrepancy_evidence")
        if (
            isinstance(evidence, dict)
            and str(evidence.get("reason") or "") == "coverage_matched_but_unplannable"
            and not bool(evidence.get("director_retry_allowed"))
        ):
            return True
        plan_probe = summary.get("plan_probe_preaudit")
        if not isinstance(plan_probe, dict):
            return False
        return str(plan_probe.get("status") or "").strip() == "coverage_matched_but_unplannable" and not bool(
            plan_probe.get("plannable_source_tools")
        )

    @staticmethod
    def _workspace_quality_interface_discrepancy_evidence(
        summary: dict[str, Any],
        artifact_quality_errors: list[str] | None = None,
    ) -> dict[str, Any]:
        raw_evidence = summary.get("interface_discrepancy_evidence")
        evidence: dict[str, Any] = dict(raw_evidence) if isinstance(raw_evidence, dict) else {}
        plan_probe = summary.get("plan_probe_preaudit")
        plan_probe_payload = plan_probe if isinstance(plan_probe, dict) else {}
        covered_unplannable_source_tools = [
            str(item)
            for item in plan_probe_payload.get(
                "covered_unplannable_source_tools",
                evidence.get("covered_unplannable_source_tools", []),
            )
            if str(item or "").strip()
        ]
        if not evidence:
            evidence = {
                "schema_version": "director.interface_discrepancy_receipt.v1",
                "route": "task_boundary_quality_loop",
                "plan_probe_status": str(plan_probe_payload.get("status") or ""),
                "covered_unplannable_source_tools": covered_unplannable_source_tools,
                "covered_unplannable_diagnostic_count": int(
                    plan_probe_payload.get("covered_unplannable_diagnostic_count") or 0
                ),
                "coverage_gap_count": int(plan_probe_payload.get("coverage_gap_count") or 0),
                "reason": "coverage_matched_but_unplannable",
            }
        diagnostic_blob = "\n".join(
            [
                json.dumps(plan_probe_payload, ensure_ascii=False, sort_keys=True),
                json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                *[str(item or "") for item in artifact_quality_errors or []],
            ]
        ).lower()
        cross_artifact_markers = (
            "unresolved import",
            "unresolved relative import",
            "cannot find module",
            "has no exported member",
            "module has no exported member",
            "does not provide an export",
            "sibling module does not define",
            "is not exported",
            "undefined:",
            "undefined symbol",
            "unresolved external symbol",
            "undefined reference",
            "cannot find symbol",
            "cannot find type",
            "could not find",
            "no such file or directory",
            "file not found for module",
            "unresolved import `",
            "no `",
            "not found in",
            "was not declared in this scope",
            "no member named",
            "has no member named",
            "ts2305",
            "ts2306",
            "ts2307",
            "ts2459",
            "e0432",
            "e0583",
            "e0761",
        )
        local_implementation_markers = (
            "ts2322",
            "ts2339",
            "ts2345",
            "ts2552",
            "property ",
            "does not exist on type",
            "cannot find name",
            "type ",
            "is not assignable to type",
        )
        cross_artifact = any(marker in diagnostic_blob for marker in cross_artifact_markers)
        local_implementation = any(marker in diagnostic_blob for marker in local_implementation_markers)
        if cross_artifact:
            recommended_owner = "chief_engineer"
            recommended_route = "pending_design_interface_contract"
            cross_artifact_route = "contract_amendment_request"
        elif local_implementation:
            recommended_owner = "director"
            recommended_route = "director_retry_with_interface_discrepancy_context"
            cross_artifact_route = "director_repair_within_contract"
        else:
            recommended_owner = str(evidence.get("recommended_owner") or "chief_engineer")
            recommended_route = str(evidence.get("recommended_route") or "pending_design_interface_contract")
            cross_artifact_route = (
                "director_repair_within_contract" if recommended_owner == "director" else "contract_amendment_request"
            )
        director_retry_allowed = (
            recommended_owner == "director" and recommended_route == "director_retry_with_interface_discrepancy_context"
        )
        plan_probe_status = str(evidence.get("plan_probe_status") or plan_probe_payload.get("status") or "")
        covered_unplannable_diagnostic_count = int(
            plan_probe_payload.get(
                "covered_unplannable_diagnostic_count",
                evidence.get("covered_unplannable_diagnostic_count") or 0,
            )
            or 0
        )
        coverage_gap_count = int(
            plan_probe_payload.get("coverage_gap_count", evidence.get("coverage_gap_count") or 0) or 0
        )
        metadata_raw = evidence.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
        metadata.update(
            {
                "route": "task_boundary_quality_loop",
                "cross_artifact_route": cross_artifact_route,
                "coverage_gap_count": coverage_gap_count,
            }
        )
        canonical = DirectorInterfaceDiscrepancyReceiptV1.from_mapping(
            {
                **evidence,
                "task_id": str(
                    summary.get("task_id")
                    or summary.get("target_task_id")
                    or summary.get("run_id")
                    or "workspace-quality"
                ),
                "source": evidence.get("source") or "factory.pipeline.workspace_quality",
                "plan_probe_status": plan_probe_status,
                "covered_unplannable_source_tools": covered_unplannable_source_tools,
                "recommended_owner": recommended_owner,
                "recommended_route": recommended_route,
                "director_retry_allowed": director_retry_allowed,
                "llm_fallback_blocked": not director_retry_allowed,
                "reason": "coverage_matched_but_unplannable",
                "metadata": metadata,
            },
        ).to_dict()
        canonical.update(
            {
                "route": "task_boundary_quality_loop",
                "cross_artifact_route": cross_artifact_route,
                "coverage_gap_count": coverage_gap_count,
                "covered_unplannable_diagnostic_count": covered_unplannable_diagnostic_count,
            }
        )
        return canonical

    @staticmethod
    def _workspace_quality_interface_discrepancy_allows_director_retry(evidence: dict[str, Any]) -> bool:
        return bool(evidence.get("director_retry_allowed")) and (
            str(evidence.get("recommended_owner") or "") == "director"
            and str(evidence.get("recommended_route") or "") == "director_retry_with_interface_discrepancy_context"
        )

    @staticmethod
    def _workspace_quality_repair_summary_projection(
        summary: dict[str, Any],
        artifact_quality_errors: list[str] | None = None,
    ) -> dict[str, Any]:
        projected: dict[str, Any] = {}
        for key in (
            "stage",
            "attempt",
            "success",
            "success_reason",
            "reason",
            "error_code",
            "error",
            "repair_mode",
            "missing_target_files",
            "runtime_smoke_target_files",
            "semantic_quality_target_files",
            "explicit_quality_target_files",
            "repair_target_files",
            "rotated_repair_targets",
            "plan_probe_preaudit",
            "interface_discrepancy_evidence",
            "deterministic_no_materialized_evidence",
            "repair_kernel",
            "deadline_decision",
        ):
            if key in summary:
                projected[key] = summary[key]
        if projected:
            task_boundary_triage_required = (
                OrchestrationStageExecutor._workspace_quality_summary_requires_task_boundary_triage(summary)
            )
            projected["task_boundary_triage_required"] = task_boundary_triage_required
            if task_boundary_triage_required:
                projected["triage_stage"] = "runtime_plan_probe_unplannable"
                projected["interface_discrepancy_evidence"] = (
                    OrchestrationStageExecutor._workspace_quality_interface_discrepancy_evidence(
                        summary,
                        artifact_quality_errors,
                    )
                )
        return projected

    async def _run_workspace_quality_checks(self, run: FactoryRun, context: dict[str, Any]) -> tuple[bool, str]:
        commands = self._workspace_quality_commands(context)
        task_boundary_blocker = self._workspace_quality_task_boundary_blocker(run, context)
        depth_result = (
            None if task_boundary_blocker else self._workspace_quality.delivery_depth_contract_result(context)
        )
        if not task_boundary_blocker and not commands and depth_result is None:
            return True, ""

        configured_timeout_seconds = float(
            context.get("workspace_validation_timeout_seconds") or _WORKSPACE_VALIDATION_TIMEOUT_SECONDS
        )
        results: list[dict[str, Any]] = []
        repair_summary: dict[str, Any] = {
            "attempted": False,
            "success": False,
            "source_tools": [],
            "tool_results": 0,
            "rounds": [],
        }

        def write_workspace_validation_failure(
            reason_code: str,
            detail: str,
            *,
            repair_override: dict[str, Any] | None = None,
            extra_payload: dict[str, Any] | None = None,
        ) -> tuple[bool, str]:
            payload = {
                "schema_version": "factory.workspace_quality_checks.v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source": "factory_stage_executor",
                "factory_run_id": run.id,
                "workspace": str(self.workspace),
                "passed": False,
                "commands": results,
                "repair": repair_override if repair_override is not None else repair_summary,
                "warnings": [reason_code],
                "error": detail,
                "deadline": {
                    "remaining_seconds": self._factory_deadline_remaining_seconds(context),
                    "deadline_epoch_seconds": context.get("factory_run_deadline_epoch_seconds"),
                    "timeout_seconds": context.get("factory_run_timeout_seconds"),
                    "source": context.get("factory_run_deadline_source"),
                },
            }
            if extra_payload:
                payload.update(dict(extra_payload))
            artifact = self._write_workspace_validation_artifact(run, context, payload)
            return False, artifact

        if task_boundary_blocker:
            reason_code = str(
                task_boundary_blocker.get("reason_code")
                or "factory_quality_gate_task_boundary_incomplete_materialization"
            )
            detail = str(task_boundary_blocker.get("detail") or reason_code)
            repair_override = {
                "attempted": False,
                "success": False,
                "source_tools": [],
                "tool_results": 0,
                "reason": "task_boundary_not_ready",
                "task_boundary_blocker": task_boundary_blocker,
            }
            return write_workspace_validation_failure(
                reason_code,
                detail,
                repair_override=repair_override,
                extra_payload={
                    "failure_class": task_boundary_blocker.get("failure_class"),
                    "responsible_layer": task_boundary_blocker.get("responsible_layer"),
                    "task_boundary_blocker": task_boundary_blocker,
                    "commands_skipped": True,
                    "skip_reason": reason_code,
                },
            )

        def workspace_checks_deadline_blocker(phase: str) -> str:
            remaining_seconds = self._factory_deadline_remaining_seconds(context)
            if remaining_seconds is None:
                return ""
            minimum_remaining = _QUALITY_GATE_MIN_QA_START_BUDGET_SECONDS + _QUALITY_GATE_QA_DEADLINE_SAFETY_SECONDS
            if remaining_seconds >= minimum_remaining:
                return ""
            return (
                f"Workspace quality checks stopped at {phase} because the factory run deadline has only "
                f"{remaining_seconds:.1f}s remaining and QA requires at least {minimum_remaining:.1f}s"
            )

        def workspace_quality_command_timeout_seconds() -> float:
            remaining_seconds = self._factory_deadline_remaining_seconds(context)
            if remaining_seconds is None:
                return configured_timeout_seconds
            reserved_for_qa = _QUALITY_GATE_MIN_QA_START_BUDGET_SECONDS + _QUALITY_GATE_QA_DEADLINE_SAFETY_SECONDS
            available_for_command = max(1.0, remaining_seconds - reserved_for_qa)
            return max(1.0, min(configured_timeout_seconds, available_for_command))

        async def run_workspace_quality_command_with_deadline(
            command: list[str],
            phase: str,
        ) -> tuple[dict[str, Any], str]:
            deadline_detail = workspace_checks_deadline_blocker(f"before_{phase}")
            if deadline_detail:
                return {}, deadline_detail
            command_timeout = workspace_quality_command_timeout_seconds()
            result = await asyncio.to_thread(self._run_workspace_quality_command, command, command_timeout)
            result["phase"] = phase
            if command_timeout < configured_timeout_seconds:
                result["deadline_capped_timeout_seconds"] = command_timeout
                result["configured_timeout_seconds"] = configured_timeout_seconds
            return result, ""

        def workspace_repair_deadline_blocker(phase: str) -> str:
            remaining_seconds = self._factory_deadline_remaining_seconds(context)
            if remaining_seconds is None:
                return ""
            if remaining_seconds >= _WORKSPACE_QUALITY_REPAIR_MIN_LLM_START_BUDGET_SECONDS:
                return ""
            return (
                f"Workspace quality repair skipped at {phase} because the factory run deadline has only "
                f"{remaining_seconds:.1f}s remaining"
            )

        initial_deadline_detail = workspace_checks_deadline_blocker("before_prepare")
        if initial_deadline_detail:
            return write_workspace_validation_failure(
                "factory_quality_gate_workspace_checks_deadline_insufficient",
                initial_deadline_detail,
            )

        prepare_commands = self._workspace_quality_prepare_commands(commands, context)
        prepare_failed = False
        for command in prepare_commands:
            result, deadline_detail = await run_workspace_quality_command_with_deadline(command, "prepare")
            if deadline_detail:
                return write_workspace_validation_failure(
                    "factory_quality_gate_workspace_checks_deadline_insufficient",
                    deadline_detail,
                )
            results.append(result)
            if not bool(result.get("passed")):
                prepare_failed = True

        run_commands = [] if prepare_failed else commands
        for command in run_commands:
            result, deadline_detail = await run_workspace_quality_command_with_deadline(command, "check")
            if deadline_detail:
                return write_workspace_validation_failure(
                    "factory_quality_gate_workspace_checks_deadline_insufficient",
                    deadline_detail,
                )
            results.append(result)
        if not prepare_failed and depth_result is not None:
            depth_result["phase"] = "check"
            results.append(depth_result)
        if prepare_failed:
            for command in commands:
                results.append(
                    {
                        "command": command,
                        "phase": "check",
                        "exit_code": None,
                        "passed": False,
                        "error": "skipped because workspace validation preparation failed",
                        "stdout_tail": "",
                        "stderr_tail": "",
                    }
                )

        repair_errors: list[str] = []
        repair_results: list[dict[str, Any]] = []

        rerun_results: list[dict[str, Any]] = []
        if run_commands and not prepare_failed and not all(bool(item.get("passed")) for item in results):
            max_rounds = int(context.get("workspace_quality_repair_max_rounds") or _WORKSPACE_QUALITY_REPAIR_MAX_ROUNDS)
            max_rounds = max(1, min(max_rounds, _WORKSPACE_QUALITY_REPAIR_MAX_ROUNDS))
            latest_check_results = [item for item in results if str(item.get("phase") or "") == "check"]
            repair_rounds: list[dict[str, Any]] = []
            source_tools: list[str] = []
            evidence: list[str] = []
            write_tool_evidence = False
            task_boundary_triage_required = False
            task_boundary_triage_summary: dict[str, Any] = {}

            def current_workspace_repair_summary(
                *,
                residual_errors: list[str] | None = None,
                deadline_detail: str = "",
            ) -> dict[str, Any]:
                partial_summary = {
                    "attempted": bool(repair_rounds),
                    "success": False,
                    "revalidated": bool(rerun_results),
                    "residual_error_count": len(residual_errors or []),
                    "residual_errors": (residual_errors or [])[:10],
                    "director_runtime_repair_coverage": self._workspace_quality_repair_coverage_report(
                        residual_errors or []
                    ),
                    "plan_probe_preaudit": self._workspace_quality_repair_plan_probe_report(residual_errors or []),
                    "source_tools": list(dict.fromkeys(source_tools)),
                    "tool_results": len(repair_results),
                    "write_tool_evidence": write_tool_evidence,
                    "artifact_quality_errors": repair_errors[:10],
                    "evidence": evidence[:12],
                    "max_rounds": max_rounds,
                    "rounds": repair_rounds,
                }
                if deadline_detail:
                    partial_summary["deadline_blocker"] = deadline_detail
                if task_boundary_triage_required:
                    partial_summary.update(
                        {
                            "task_boundary_triage_required": True,
                            "success_reason": "task_boundary_interface_discrepancy_required",
                            "plan_probe_preaudit": task_boundary_triage_summary.get("plan_probe_preaudit"),
                            "interface_discrepancy_evidence": task_boundary_triage_summary.get(
                                "interface_discrepancy_evidence"
                            ),
                        }
                    )
                return partial_summary

            for round_index in range(max_rounds):
                if latest_check_results and all(bool(item.get("passed")) for item in latest_check_results):
                    break
                repair_errors = self._workspace_quality_repair_errors(latest_check_results or results)
                if not repair_errors:
                    break
                round_repair_results, round_summary = await asyncio.to_thread(
                    self._apply_workspace_quality_repairs,
                    run_id=run.id,
                    artifact_quality_errors=repair_errors,
                )
                round_requires_task_boundary_triage = self._workspace_quality_summary_requires_task_boundary_triage(
                    dict(round_summary)
                )
                round_repair_evidence = self._workspace_quality_repair_evidence(round_repair_results)
                round_write_tool_evidence = any(
                    self._workspace_quality_repair_result_has_mutation(item) for item in round_repair_results
                )
                if round_requires_task_boundary_triage:
                    interface_discrepancy_evidence = self._workspace_quality_interface_discrepancy_evidence(
                        dict(round_summary),
                        repair_errors,
                    )
                    if self._workspace_quality_interface_discrepancy_allows_director_retry(
                        interface_discrepancy_evidence
                    ):
                        deterministic_noop_summary = dict(round_summary)
                        deadline_detail = workspace_repair_deadline_blocker(
                            f"before_interface_discrepancy_llm_repair_round_{round_index + 1}"
                        )
                        if deadline_detail:
                            return write_workspace_validation_failure(
                                "factory_quality_gate_workspace_repair_deadline_insufficient",
                                deadline_detail,
                                repair_override=current_workspace_repair_summary(
                                    residual_errors=repair_errors,
                                    deadline_detail=deadline_detail,
                                ),
                            )
                        round_repair_results, round_summary = await self._apply_workspace_quality_llm_repairs(
                            run_id=run.id,
                            context=context,
                            artifact_quality_errors=repair_errors,
                            repair_attempt=round_index + 1,
                            interface_discrepancy_evidence=interface_discrepancy_evidence,
                        )
                        if not round_repair_results:
                            round_summary = dict(round_summary)
                            round_summary["deterministic_no_materialized_evidence"] = deterministic_noop_summary
                        round_requires_task_boundary_triage = (
                            self._workspace_quality_summary_requires_task_boundary_triage(dict(round_summary))
                        )
                        round_repair_evidence = self._workspace_quality_repair_evidence(round_repair_results)
                        round_write_tool_evidence = any(
                            self._workspace_quality_repair_result_has_mutation(item) for item in round_repair_results
                        )
                if (
                    not round_requires_task_boundary_triage
                    and round_repair_results
                    and not round_write_tool_evidence
                    and not round_repair_evidence
                ):
                    deterministic_noop_summary = dict(round_summary)
                    deadline_detail = workspace_repair_deadline_blocker(f"before_llm_repair_round_{round_index + 1}")
                    if deadline_detail:
                        return write_workspace_validation_failure(
                            "factory_quality_gate_workspace_repair_deadline_insufficient",
                            deadline_detail,
                            repair_override=current_workspace_repair_summary(
                                residual_errors=repair_errors,
                                deadline_detail=deadline_detail,
                            ),
                        )
                    round_repair_results, round_summary = await self._apply_workspace_quality_llm_repairs(
                        run_id=run.id,
                        context=context,
                        artifact_quality_errors=repair_errors,
                        repair_attempt=round_index + 1,
                    )
                    if not round_repair_results:
                        round_summary = dict(round_summary)
                        round_summary["deterministic_no_materialized_evidence"] = deterministic_noop_summary
                    round_requires_task_boundary_triage = self._workspace_quality_summary_requires_task_boundary_triage(
                        dict(round_summary)
                    )
                elif not round_requires_task_boundary_triage and not round_repair_results:
                    deadline_detail = workspace_repair_deadline_blocker(f"before_llm_repair_round_{round_index + 1}")
                    if deadline_detail:
                        return write_workspace_validation_failure(
                            "factory_quality_gate_workspace_repair_deadline_insufficient",
                            deadline_detail,
                            repair_override=current_workspace_repair_summary(
                                residual_errors=repair_errors,
                                deadline_detail=deadline_detail,
                            ),
                        )
                    round_repair_results, round_summary = await self._apply_workspace_quality_llm_repairs(
                        run_id=run.id,
                        context=context,
                        artifact_quality_errors=repair_errors,
                        repair_attempt=round_index + 1,
                    )
                    round_requires_task_boundary_triage = self._workspace_quality_summary_requires_task_boundary_triage(
                        dict(round_summary)
                    )
                cpp_post_repair_results: list[dict[str, Any]] = []
                if not round_requires_task_boundary_triage:
                    cpp_post_repair_results = await asyncio.to_thread(self._apply_workspace_quality_cpp_post_repairs)
                if cpp_post_repair_results:
                    round_repair_results.extend(cpp_post_repair_results)
                    round_summary = dict(round_summary)
                    round_summary_tools = [
                        str(item) for item in round_summary.get("source_tools", []) if str(item or "").strip()
                    ]
                    if "deterministic_cpp_post_repair" not in round_summary_tools:
                        round_summary_tools.append("deterministic_cpp_post_repair")
                    round_summary["source_tools"] = round_summary_tools
                repair_results.extend(round_repair_results)
                normalized_round_summary = dict(round_summary)
                round_source_tools = [
                    str(item) for item in normalized_round_summary.get("source_tools", []) if str(item or "").strip()
                ]
                round_evidence = self._workspace_quality_repair_evidence(round_repair_results)
                round_write_tool_evidence = any(
                    self._workspace_quality_repair_result_has_mutation(item) for item in round_repair_results
                )
                source_tools.extend(round_source_tools)
                evidence.extend(round_evidence)
                write_tool_evidence = write_tool_evidence or round_write_tool_evidence
                summary_projection = self._workspace_quality_repair_summary_projection(
                    normalized_round_summary,
                    repair_errors,
                )
                round_payload = {
                    "round": round_index + 1,
                    "attempted": True,
                    "artifact_quality_errors": repair_errors[:10],
                    "director_runtime_repair_coverage": self._workspace_quality_repair_coverage_report(repair_errors),
                    "plan_probe_preaudit": self._workspace_quality_repair_plan_probe_report(repair_errors),
                    "tool_results": len(round_repair_results),
                    "source_tools": round_source_tools,
                    "write_tool_evidence": round_write_tool_evidence,
                    "evidence": round_evidence,
                }
                if summary_projection:
                    round_payload["repair_summary"] = summary_projection
                    if round_requires_task_boundary_triage:
                        task_boundary_triage_required = True
                        task_boundary_triage_summary = summary_projection
                        round_payload["task_boundary_triage_required"] = True
                repair_rounds.append(round_payload)
                if round_requires_task_boundary_triage:
                    break
                if not round_repair_results:
                    break
                latest_check_results = []
                rerun_results = []
                round_prepare_failed = False
                prepare_phase = (
                    "prepare_after_repair" if round_index == 0 else f"prepare_after_repair_{round_index + 1}"
                )
                for command in prepare_commands:
                    result, deadline_detail = await run_workspace_quality_command_with_deadline(command, prepare_phase)
                    if deadline_detail:
                        return write_workspace_validation_failure(
                            "factory_quality_gate_workspace_checks_deadline_insufficient",
                            deadline_detail,
                            repair_override=current_workspace_repair_summary(residual_errors=repair_errors),
                        )
                    results.append(result)
                    if not bool(result.get("passed")):
                        round_prepare_failed = True
                phase = "check_after_repair" if round_index == 0 else f"check_after_repair_{round_index + 1}"
                if round_prepare_failed:
                    for command in run_commands:
                        result = {
                            "command": command,
                            "phase": phase,
                            "exit_code": None,
                            "passed": False,
                            "error": "skipped because workspace validation preparation failed after repair",
                            "stdout_tail": "",
                            "stderr_tail": "",
                        }
                        results.append(result)
                        latest_check_results.append(result)
                        rerun_results.append(result)
                    break
                else:
                    for command in run_commands:
                        result, deadline_detail = await run_workspace_quality_command_with_deadline(command, phase)
                        if deadline_detail:
                            return write_workspace_validation_failure(
                                "factory_quality_gate_workspace_checks_deadline_insufficient",
                                deadline_detail,
                                repair_override=current_workspace_repair_summary(residual_errors=repair_errors),
                            )
                        results.append(result)
                        latest_check_results.append(result)
                        rerun_results.append(result)
                    round_depth_result = self._workspace_quality.delivery_depth_contract_result(context)
                    if round_depth_result is not None:
                        round_depth_result["phase"] = phase
                        results.append(round_depth_result)
                        latest_check_results.append(round_depth_result)
                        rerun_results.append(round_depth_result)
            residual_failures = [item for item in latest_check_results if not bool(item.get("passed"))]
            residual_errors = self._workspace_quality_repair_errors(residual_failures) if residual_failures else []
            residual_coverage_report = self._workspace_quality_repair_coverage_report(residual_errors)
            repair_revalidated = bool(rerun_results)
            repair_summary = {
                "attempted": bool(repair_rounds),
                "success": repair_revalidated and not residual_failures,
                "revalidated": repair_revalidated,
                "residual_error_count": len(residual_failures),
                "residual_errors": residual_errors[:10],
                "director_runtime_repair_coverage": residual_coverage_report,
                "plan_probe_preaudit": self._workspace_quality_repair_plan_probe_report(residual_errors),
                "source_tools": list(dict.fromkeys(source_tools)),
                "tool_results": len(repair_results),
                "write_tool_evidence": write_tool_evidence,
                "artifact_quality_errors": repair_errors[:10],
                "evidence": evidence[:12],
                "max_rounds": max_rounds,
                "rounds": repair_rounds,
            }
            if task_boundary_triage_required:
                repair_summary.update(
                    {
                        "task_boundary_triage_required": True,
                        "success_reason": "task_boundary_interface_discrepancy_required",
                        "plan_probe_preaudit": task_boundary_triage_summary.get("plan_probe_preaudit"),
                        "interface_discrepancy_evidence": task_boundary_triage_summary.get(
                            "interface_discrepancy_evidence"
                        ),
                    }
                )

        effective_results = rerun_results if rerun_results else results
        if rerun_results:
            effective_results = [item for item in results if str(item.get("phase") or "") == "prepare"] + rerun_results

        payload_warnings = []
        if bool(repair_summary.get("task_boundary_triage_required")):
            payload_warnings.append("task_boundary_interface_discrepancy_required")

        payload = {
            "schema_version": "factory.workspace_quality_checks.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "factory_stage_executor",
            "factory_run_id": run.id,
            "workspace": str(self.workspace),
            "passed": all(bool(item.get("passed")) for item in effective_results),
            "commands": results,
            "repair": repair_summary,
        }
        if payload_warnings:
            payload["warnings"] = payload_warnings
        artifact = self._write_workspace_validation_artifact(run, context, payload)
        return bool(payload["passed"]), artifact

    def _write_workspace_validation_artifact(
        self,
        run: FactoryRun,
        context: dict[str, Any],
        payload: dict[str, Any],
    ) -> str:
        artifact = "runtime/qa/workspace-validation.json"
        self._write_json_artifact(artifact, payload)
        self._persist_workspace_validation_ledger(run, context, payload)
        return artifact

    def _persist_workspace_validation_ledger(
        self,
        run: FactoryRun,
        context: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        raw_commands = payload.get("commands")
        commands = (
            [dict(item) for item in raw_commands if isinstance(item, dict)] if isinstance(raw_commands, list) else []
        )
        for command in commands:
            if "ok" not in command and "passed" in command:
                command["ok"] = bool(command.get("passed"))
        passed = bool(payload.get("passed"))
        detail = str(
            payload.get("error") or ("workspace validation passed" if passed else "workspace validation failed")
        )
        target_files = self._merge_string_list(
            context.get("target_files")
            or context.get("declared_source_targets")
            or context.get("code_files")
            or context.get("scope_paths")
        )
        scope_paths = self._merge_string_list(context.get("scope_paths") or target_files)
        record = {
            "id": str(context.get("project_id") or context.get("requested_project_id") or run.id),
            "project_id": str(context.get("project_id") or context.get("requested_project_id") or run.id),
            "run_id": run.id,
            "target_files": target_files,
            "scope_paths": scope_paths,
            "acceptance_criteria": self._merge_string_list(
                context.get("acceptance_criteria") or context.get("acceptance") or context.get("qa_contract")
            ),
            "required_evidence_modalities": ["command"] if commands else [],
            "enabled_evidence_modalities": ["command"] if commands else [],
            "chain": {"run_id": run.id},
            "factory_workspace_quality_repair": payload.get("repair")
            if isinstance(payload.get("repair"), dict)
            else {},
        }
        gate = {
            "ok": passed,
            "summary": detail,
            "command_count_total": len(commands),
            "commands": commands,
            "requirements": {"workspace_validation": {"ok": passed, "detail": detail}},
            "repair_result": payload.get("repair") if isinstance(payload.get("repair"), dict) else {},
        }
        try:
            from .run_ledger import persist_real_run_gate_ledger

            persist_real_run_gate_ledger(
                self.workspace,
                record,
                gate,
                run_id=run.id,
                project_id=str(record["project_id"]),
                stage="workspace_validation",
                gate_name="workspace_validation",
            )
        except Exception as exc:  # noqa: BLE001 - ledger evidence must not mask the validation verdict.
            logger.debug("workspace validation ledger persistence failed for %s: %s", run.id, exc)

    @staticmethod
    def _qa_report_has_warning(payload: dict[str, Any], warning: str) -> bool:
        return helpers.qa_report_has_warning(payload, warning)

    @staticmethod
    def _factory_deadline_remaining_seconds(context: dict[str, Any]) -> float | None:
        return deadline_calc.factory_deadline_remaining_seconds(context)

    @staticmethod
    async def _quality_gate_abort_reason(
        abort_checker: Callable[[], Awaitable[str | None]] | None,
    ) -> str:
        if abort_checker is None:
            return ""
        try:
            return str(await abort_checker() or "").strip()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("Factory quality gate abort checker failed: %s", exc)
            return ""

    def _build_quality_gate_failure_stage(
        self,
        run: FactoryRun,
        *,
        reason_code: str,
        detail: str,
        context: dict[str, Any],
        workspace_checks_artifact: str = "",
        workspace_checks_passed: bool | None = None,
        status: str = "failed",
    ) -> StageResult:
        target = str(context.get("qa_target") or "Quality gate")
        remaining_seconds = self._factory_deadline_remaining_seconds(context)
        warnings = [reason_code]
        payload: dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "factory_stage_executor",
            "review_type": "quality_gate",
            "target": target,
            "runtime_hard_gate_passed": False,
            "verdict": "FAIL",
            "passed": False,
            "score": 0,
            "critical_issue_count": 1,
            "critical_issues": [detail],
            "major_issues": [],
            "warnings": warnings,
            "evidence": [
                f"factory_run_id={run.id}",
                f"reason_code={reason_code}",
            ],
            "suggestions": [],
            "raw_excerpt": detail[:2000],
            "deadline": {
                "remaining_seconds": remaining_seconds,
                "deadline_epoch_seconds": context.get("factory_run_deadline_epoch_seconds"),
                "timeout_seconds": context.get("factory_run_timeout_seconds"),
                "source": context.get("factory_run_deadline_source"),
            },
        }
        if workspace_checks_passed is not None:
            payload["workspace_checks_passed"] = workspace_checks_passed
            payload["evidence"].append(f"workspace_checks_passed={workspace_checks_passed}")
        if workspace_checks_artifact:
            payload["workspace_checks_artifact"] = workspace_checks_artifact
            payload["evidence"].append(f"workspace_checks_artifact={workspace_checks_artifact}")
        self._write_json_artifact("runtime/qa/report.json", payload)
        artifacts = ["runtime/qa/report.json"]
        if workspace_checks_artifact:
            artifacts.append(workspace_checks_artifact)
        self._mirror_quality_gate_artifacts(run.id, artifacts)
        return StageResult(
            stage="quality_gate",
            status=status,
            output=f"Quality gate {status}: {reason_code}; {detail}",
            artifacts=artifacts,
        )

    def _workspace_quality_failure_detail(self, workspace_checks_artifact: str) -> str:
        detail = "Workspace validation failed"
        if not workspace_checks_artifact:
            return detail
        artifact_path = self._artifact_path(workspace_checks_artifact)
        try:
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return f"{detail}; see {workspace_checks_artifact}"
        if not isinstance(payload, dict):
            return f"{detail}; see {workspace_checks_artifact}"
        evidence: list[str] = []
        repair = payload.get("repair")
        if isinstance(repair, dict):
            for raw in repair.get("residual_errors") or ():
                text = str(raw or "").strip()
                if text:
                    evidence.append(text[:500])
                if len(evidence) >= 2:
                    break
        for item in payload.get("commands") or ():
            if len(evidence) >= 3:
                break
            if not isinstance(item, dict) or bool(item.get("passed")):
                continue
            command = item.get("command")
            command_text = " ".join(str(part) for part in command) if isinstance(command, list) else str(command or "")
            stderr_tail = str(item.get("stderr_tail") or item.get("error") or "").strip()
            if command_text or stderr_tail:
                evidence.append(f"{command_text}: {stderr_tail[:400]}".strip(": "))
        if not evidence:
            return f"{detail}; see {workspace_checks_artifact}"
        return f"{detail}: {'; '.join(evidence)}; see {workspace_checks_artifact}"

    def _quality_gate_qa_wait_timeout_seconds(self, context: dict[str, Any]) -> int:
        try:
            configured = int(context.get("timeout", 600))
        except (TypeError, ValueError):
            configured = 600
        configured = max(1, configured)
        remaining_seconds = self._factory_deadline_remaining_seconds(context)
        if remaining_seconds is None:
            return configured
        capped = max(1, int(remaining_seconds - _QUALITY_GATE_QA_DEADLINE_SAFETY_SECONDS))
        return max(1, min(configured, capped))

    def _build_qa_input_with_workspace_quality_evidence(
        self,
        qa_input: object,
        workspace_checks_artifact: str,
        *,
        run_id: str = "",
    ) -> str:
        base_input = str(qa_input or "").strip()
        sections = [base_input] if base_input else []

        if workspace_checks_artifact:
            evidence_text = self._read_text_artifact(workspace_checks_artifact, min_chars=2)
            if evidence_text:
                compact_evidence = self._compact_workspace_quality_evidence_for_qa(evidence_text)
                sections.append(
                    "\n".join(
                        [
                            "Workspace quality evidence collected before QA judgement:",
                            f"- artifact: {workspace_checks_artifact}",
                            "- content:",
                            compact_evidence,
                        ]
                    )
                )

        ce_review_artifact = ""
        ce_review_text = ""
        if run_id:
            for candidate in (
                f"runtime/state/blueprints/{run_id}.review.json",
                f"runtime/blueprints/{run_id}.review.json",
                f"workspace/.polaris/blueprints/{run_id}.review.json",
                "workspace/.polaris/blueprints/latest.review.json",
            ):
                with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
                    ce_review_text = self._read_text_artifact(candidate, min_chars=2)
                if ce_review_text:
                    ce_review_artifact = candidate
                    break
        if ce_review_text:
            sections.append(
                "\n".join(
                    [
                        "Chief Engineer blueprint evidence collected before QA judgement:",
                        f"- artifact: {ce_review_artifact}",
                        "- content:",
                        self._compact_text_for_prompt(ce_review_text, max_chars=6000),
                    ]
                )
            )
        return "\n\n".join(sections)

    async def _wait_for_canonical_quality_authority(
        self,
        run: FactoryRun,
        context: dict[str, Any],
    ) -> helpers.CanonicalFactoryAuthority:
        """Wait until the QA fact is visible behind the sequence barrier.

        TaskBoundary ``completed_verified`` proves Director settlement. The
        final ``qa_verdict`` gate's append/content coordinates prove the QA
        consumer barrier. Both facts must be visible in the same Run Ledger
        projection; a report file or orchestration status cannot substitute.
        """

        raw_timeout = context.get("canonical_projection_settlement_timeout_seconds", 2.0)
        try:
            timeout_seconds = float(raw_timeout)
        except (TypeError, ValueError):
            timeout_seconds = 2.0
        timeout_seconds = max(0.1, min(timeout_seconds, 10.0))
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        latest = helpers.evaluate_canonical_factory_authority({})
        while True:
            latest = helpers.evaluate_canonical_factory_authority(
                self._canonical_factory_projection(run, context),
            )
            if latest.quality_stage_authorized:
                return latest
            if latest.qa_verdict_present or latest.reason_code in {
                "canonical_sequence_barrier_unsatisfied",
                "qa_verdict_failed",
                "evidence_policy_failed",
                "run_ledger_projection_failed",
                "task_boundary_not_completed_verified",
            }:
                return latest
            if asyncio.get_running_loop().time() >= deadline:
                return latest
            await asyncio.sleep(0.05)

    async def _execute_quality_gate(self, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        logger.info("Executing quality gate for run %s", run.id)
        abort_checker = self._resolve_abort_checker(context)
        authority_port = self._factory_role_evidence_cutoff_port(context)

        abort_reason = await self._quality_gate_abort_reason(abort_checker)
        if abort_reason:
            return self._build_quality_gate_failure_stage(
                run,
                reason_code="factory_quality_gate_cancelled_before_checks",
                detail=f"Quality gate cancelled before workspace checks: {abort_reason}",
                context=context,
                status="cancelled",
            )

        remaining_seconds = self._factory_deadline_remaining_seconds(context)
        if remaining_seconds is not None and remaining_seconds < _QUALITY_GATE_MIN_START_BUDGET_SECONDS:
            return self._build_quality_gate_failure_stage(
                run,
                reason_code="factory_quality_gate_deadline_insufficient_before_checks",
                detail=(
                    "Quality gate skipped before workspace checks because the factory run deadline "
                    f"has only {remaining_seconds:.1f}s remaining"
                ),
                context=context,
            )

        workspace_checks_passed, workspace_checks_artifact = await self._run_workspace_quality_checks(run, context)
        qa_input = self._build_qa_input_with_workspace_quality_evidence(
            context.get("qa_input"),
            workspace_checks_artifact,
            run_id=run.id,
        )

        abort_reason = await self._quality_gate_abort_reason(abort_checker)
        if abort_reason:
            return self._build_quality_gate_failure_stage(
                run,
                reason_code="factory_quality_gate_cancelled_before_qa",
                detail=f"Quality gate cancelled before QA judgement: {abort_reason}",
                context=context,
                workspace_checks_artifact=workspace_checks_artifact,
                workspace_checks_passed=workspace_checks_passed,
                status="cancelled",
            )

        remaining_seconds = self._factory_deadline_remaining_seconds(context)
        if remaining_seconds is not None and remaining_seconds < _QUALITY_GATE_MIN_QA_START_BUDGET_SECONDS:
            return self._build_quality_gate_failure_stage(
                run,
                reason_code="factory_quality_gate_deadline_insufficient_before_qa",
                detail=(
                    "Quality gate did not start QA LLM judgement because the factory run deadline "
                    f"has only {remaining_seconds:.1f}s remaining"
                ),
                context=context,
                workspace_checks_artifact=workspace_checks_artifact,
                workspace_checks_passed=workspace_checks_passed,
            )

        service = self._build_orchestration_service(context)
        command_result = cast(
            CommandResult,
            await self._call_with_factory_role_evidence_authority(
                authority_port,
                "qa",
                lambda: service.execute_qa_run(
                    workspace=str(self.workspace),
                    target=context.get("qa_target", "Quality gate"),
                    options={
                        "input": qa_input,
                    },
                ),
            ),
        )
        final_result = await self._wait_run_completion(
            service,
            command_result,
            timeout_seconds=self._quality_gate_qa_wait_timeout_seconds(context),
            cancel_event=self._resolve_cancel_event(context),
            abort_checker=abort_checker,
        )
        final_status = str(final_result.status or "").strip().lower()
        if final_status == "cancelled":
            return self._build_quality_gate_failure_stage(
                run,
                reason_code="factory_quality_gate_qa_cancelled",
                detail=f"Quality gate QA run cancelled: {final_result.message or 'N/A'}",
                context=context,
                workspace_checks_artifact=workspace_checks_artifact,
                workspace_checks_passed=workspace_checks_passed,
                status="cancelled",
            )
        if final_status == "timeout":
            return self._build_quality_gate_failure_stage(
                run,
                reason_code="factory_quality_gate_qa_timeout",
                detail=f"Quality gate QA run timed out: {final_result.message or 'N/A'}",
                context=context,
                workspace_checks_artifact=workspace_checks_artifact,
                workspace_checks_passed=workspace_checks_passed,
            )

        qa_report_path = self._artifact_path("runtime/qa/report.json")
        loaded: dict[str, Any] | Any = {}
        parse_error: Exception | None = None
        report_ready = self._artifact_file_ready(qa_report_path)
        if report_ready:
            for _attempt in range(5):
                try:
                    report_text = await asyncio.to_thread(qa_report_path.read_text, encoding="utf-8")
                    loaded = json.loads(report_text)
                    parse_error = None
                    break
                except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                    parse_error = exc
                    await asyncio.sleep(0.2)
        qa_payload: dict[str, Any] = loaded if isinstance(loaded, dict) else {}

        canonical_authority = await self._wait_for_canonical_quality_authority(
            run,
            context,
        )
        is_success = canonical_authority.quality_stage_authorized
        qa_report_passed = bool(qa_payload.get("passed")) if qa_payload else None
        report_consistent = qa_report_passed is None or qa_report_passed == canonical_authority.qa_verdict_passed
        output_suffix = (
            f"task_boundary_completed_verified={canonical_authority.task_boundary_completed_verified}; "
            f"qa_verdict_passed={canonical_authority.qa_verdict_passed}; "
            f"sequence_barrier_satisfied={canonical_authority.sequence_barrier_satisfied}; "
            f"evidence_policy_passed={canonical_authority.evidence_policy_passed}; "
            f"workspace_checks_diagnostic={workspace_checks_passed}; "
            f"report_ready={report_ready}; report_parse_error={parse_error or 'none'}; "
            f"report_consistent={report_consistent}; "
            f"canonical_authorized={is_success}; "
            f"canonical_reason={canonical_authority.reason_code}"
        )
        artifacts = ["runtime/qa/report.json"] if report_ready else []
        if workspace_checks_artifact:
            artifacts.append(workspace_checks_artifact)
        self._mirror_quality_gate_artifacts(run.id, artifacts)
        return StageResult(
            stage="quality_gate",
            status="success" if is_success else "failed",
            output=(f"Quality gate {final_result.status}: {final_result.message or 'N/A'}; {output_suffix}"),
            artifacts=artifacts,
        )

    def _build_orchestration_service(self, context: dict[str, Any]) -> Any:
        return self._run_completion_waiter.build_orchestration_service(context)

    async def _wait_run_completion(
        self,
        service: OrchestrationCommandService,
        initial_result: CommandResult,
        timeout_seconds: int = 300,
        *,
        cancel_event: asyncio.Event | None = None,
        abort_checker: Callable[[], Awaitable[str | None]] | None = None,
        cancel_on_timeout: bool = True,
        authority: RunCompletionAuthority = RunCompletionAuthority.ROLE_LIFECYCLE,
    ) -> CommandResult:
        return await self._run_completion_waiter.wait(
            service,
            initial_result,
            timeout_seconds,
            cancel_event=cancel_event,
            abort_checker=abort_checker,
            cancel_on_timeout=cancel_on_timeout,
            authority=authority,
        )

    @staticmethod
    def _inflight_director_run_ids(result: CommandResult) -> tuple[str, ...]:
        """Return child run ids that explicitly crossed the soft-timeout barrier."""

        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        run_ids: list[str] = []
        if bool(metadata.get("inflight_run_continues")):
            run_id = str(result.run_id or "").strip()
            if run_id:
                run_ids.append(run_id)
        per_binding = metadata.get("per_binding")
        if isinstance(per_binding, list):
            for entry in per_binding:
                if not isinstance(entry, dict) or not bool(entry.get("inflight_run_continues")):
                    continue
                run_id = str(entry.get("run_id") or "").strip()
                if run_id:
                    run_ids.append(run_id)
        return tuple(dict.fromkeys(run_ids))

    async def _settle_inflight_director_result(
        self,
        service: OrchestrationCommandService,
        *,
        result: CommandResult,
        grace_seconds: int,
        cancel_event: asyncio.Event | None,
        abort_checker: Callable[[], Awaitable[str | None]] | None,
    ) -> tuple[CommandResult, bool]:
        """Settle every child run named by a soft-timeout result before reuse.

        The provider response and tool batch belong to one execution attempt.
        Once a wait result says that attempt is still in flight, starting a new
        Director turn would create two writers for the same task boundary. This
        method therefore acts as the parent-side commit barrier and returns only
        after each named child is terminal or the barrier itself times out.

        Complexity:
            O(b) time and memory over the number of active Director bindings;
            waits execute concurrently and are bounded by ``grace_seconds``.
        """

        run_ids = self._inflight_director_run_ids(result)
        if not run_ids:
            return result, False

        settled_results = await asyncio.gather(
            *(
                self._settle_inflight_director_run_after_timeout(
                    service,
                    run_id=run_id,
                    grace_seconds=grace_seconds,
                    cancel_event=cancel_event,
                    abort_checker=abort_checker,
                )
                for run_id in run_ids
            )
        )
        settlements: dict[str, CommandResult] = {}
        for run_id, settled in zip(run_ids, settled_results, strict=True):
            if settled is None:
                settled = CommandResult(
                    run_id=run_id,
                    status="timeout",
                    message="Director execution barrier produced no terminal result",
                    metadata={
                        "barrier_state": "timeout",
                        "barrier_timeout": True,
                        "inflight_run_continues": True,
                        "cancel_signal_sent": False,
                        "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                        "responsible_layer": "execution_control_plane",
                    },
                )
            settlements[run_id] = settled

        original_metadata = result.metadata if isinstance(result.metadata, dict) else {}
        per_binding = original_metadata.get("per_binding")
        if isinstance(per_binding, list):
            updated_bindings: list[dict[str, Any]] = []
            for raw_entry in per_binding:
                if not isinstance(raw_entry, dict):
                    continue
                entry = dict(raw_entry)
                run_id = str(entry.get("run_id") or "").strip()
                settled = settlements.get(run_id)
                if settled is not None:
                    settled_metadata = settled.metadata if isinstance(settled.metadata, dict) else {}
                    entry.update(
                        {
                            "status": str(settled.status or "").strip(),
                            "message": str(settled.message or "").strip(),
                            "settled_after_timeout": not bool(settled_metadata.get("inflight_run_continues")),
                            **settled_metadata,
                        }
                    )
                updated_bindings.append(entry)

            active = any(bool(item.get("inflight_run_continues")) for item in updated_bindings)
            failed = any(
                str(item.get("status") or "").strip().lower() in {"failed", "blocked", "cancelled", "timeout"}
                for item in updated_bindings
                if str(item.get("run_id") or "").strip()
            )
            merged_status = "timeout" if active else ("failed" if failed else "completed")
            merged_metadata = {
                **original_metadata,
                "per_binding": updated_bindings,
                "settlement_attempted": True,
                "settled_run_count": len(settlements),
                "inflight_run_continues": active,
                "barrier_state": "timeout" if active else "settled",
                "barrier_timeout": active,
            }
            if active:
                merged_metadata.update(
                    {
                        "cancel_signal_sent": False,
                        "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                        "responsible_layer": "execution_control_plane",
                    }
                )
            return (
                CommandResult(
                    run_id=str(result.run_id or run_ids[0]).strip(),
                    status=merged_status,
                    message=(
                        "Director binding execution barrier timed out"
                        if active
                        else "Director binding execution barrier settled"
                    ),
                    reason_code=result.reason_code,
                    stage_results=result.stage_results,
                    started_at=result.started_at,
                    completed_at=result.completed_at,
                    artifacts=result.artifacts,
                    metadata=merged_metadata,
                ),
                True,
            )

        settled = settlements[run_ids[0]]
        settled_metadata = settled.metadata if isinstance(settled.metadata, dict) else {}
        active = bool(settled_metadata.get("inflight_run_continues"))
        merged_metadata = {
            **original_metadata,
            **settled_metadata,
            "settlement_attempted": True,
            "settled_run_count": 1,
            "inflight_run_continues": active,
            "barrier_state": "timeout" if active else "settled",
            "barrier_timeout": active,
        }
        return (
            CommandResult(
                run_id=str(settled.run_id or result.run_id or "").strip(),
                status=str(settled.status or result.status or "").strip(),
                message=settled.message or result.message,
                reason_code=settled.reason_code or result.reason_code,
                stage_results=settled.stage_results or result.stage_results,
                started_at=settled.started_at or result.started_at,
                completed_at=settled.completed_at or result.completed_at,
                artifacts=settled.artifacts or result.artifacts,
                metadata=merged_metadata,
            ),
            True,
        )

    async def _settle_inflight_director_run_after_timeout(
        self,
        service: OrchestrationCommandService,
        *,
        run_id: str,
        grace_seconds: int,
        cancel_event: asyncio.Event | None = None,
        abort_checker: Callable[[], Awaitable[str | None]] | None = None,
    ) -> CommandResult | None:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return None
        if grace_seconds <= 0:
            barrier_result = self._active_director_task_barrier_result(
                run_id=normalized_run_id,
                reason="factory_stage_timeout",
                grace_seconds=0,
            )
            if barrier_result is not None:
                return self._execution_barrier_timeout_result(
                    barrier_result,
                    grace_seconds=0,
                )
            return CommandResult(
                run_id=normalized_run_id,
                status="timeout",
                message="Director run timed out before timeout settle grace",
                metadata={
                    "cancel_signal_sent": False,
                    "cancel_reason": "factory_stage_timeout",
                    "timeout_settle_grace_seconds": 0,
                    "inflight_run_continues": True,
                    "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                    "responsible_layer": "execution_control_plane",
                },
            )
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        hard_limit_seconds = max(0.0, float(grace_seconds))
        hard_deadline = started_at + hard_limit_seconds
        cancellation_reserve_seconds = min(0.25, hard_limit_seconds * 0.25)
        observation_deadline = hard_deadline - cancellation_reserve_seconds
        progress_marker = self._active_director_execution_progress_marker(run_id=normalized_run_id)
        progress_extensions = 0
        deferred_cancel_reason = ""

        async def _cancel_within_hard_deadline(reason: str) -> tuple[CommandResult | None, bool]:
            remaining_seconds = _remaining_monotonic_seconds(hard_deadline)
            if remaining_seconds <= 0:
                return None, False
            try:
                return (
                    await asyncio.wait_for(
                        self._run_completion_waiter.cancel_active_run(
                            normalized_run_id,
                            reason=reason,
                        ),
                        timeout=remaining_seconds,
                    ),
                    True,
                )
            except TimeoutError:
                return None, False

        while True:
            canonical_probe = self._run_completion_waiter.canonical_terminal_result(
                run_id=normalized_run_id,
                process_terminal=False,
            )
            if canonical_probe is not None:
                return self._with_execution_barrier_progress(
                    canonical_probe,
                    progress_extensions=progress_extensions,
                    elapsed_seconds=loop.time() - started_at,
                    max_total_seconds=hard_limit_seconds,
                    deferred_cancel_reason=deferred_cancel_reason,
                )
            if cancel_event is not None and cancel_event.is_set() and not deferred_cancel_reason:
                barrier_result = self._active_director_task_barrier_result(
                    run_id=normalized_run_id,
                    reason="factory_cancelled",
                    grace_seconds=grace_seconds,
                )
                if barrier_result is not None:
                    deferred_cancel_reason = "factory_cancelled"
                else:
                    barrier_result, cancel_completed = await _cancel_within_hard_deadline("factory_cancelled")
                    if barrier_result is not None:
                        return barrier_result
                    return CommandResult(
                        run_id=normalized_run_id,
                        status="cancelled",
                        message="Run cancelled: factory_cancelled",
                        metadata={
                            "cancel_signal_sent": cancel_completed,
                            "inflight_run_continues": not cancel_completed,
                        },
                    )
            if abort_checker is not None and not deferred_cancel_reason:
                with contextlib.suppress(AttributeError, OSError, RuntimeError, TypeError, ValueError):
                    abort_remaining_seconds = _remaining_monotonic_seconds(observation_deadline)
                    abort_reason = (
                        await asyncio.wait_for(
                            abort_checker(),
                            timeout=abort_remaining_seconds,
                        )
                        if abort_remaining_seconds > 0
                        else None
                    )
                    if abort_reason:
                        barrier_result = self._active_director_task_barrier_result(
                            run_id=normalized_run_id,
                            reason=abort_reason,
                            grace_seconds=grace_seconds,
                        )
                        if barrier_result is not None:
                            deferred_cancel_reason = abort_reason
                        else:
                            barrier_result, cancel_completed = await _cancel_within_hard_deadline(abort_reason)
                            if barrier_result is not None:
                                return barrier_result
                            return CommandResult(
                                run_id=normalized_run_id,
                                status="cancelled",
                                message=f"Run cancelled: {abort_reason}",
                                metadata={
                                    "cancel_signal_sent": cancel_completed,
                                    "inflight_run_continues": not cancel_completed,
                                },
                            )

            process_terminal = False
            query_remaining_seconds = _remaining_monotonic_seconds(observation_deadline)
            if query_remaining_seconds > 0:
                with contextlib.suppress(
                    AttributeError,
                    OSError,
                    RuntimeError,
                    TimeoutError,
                    TypeError,
                    ValueError,
                ):
                    lifecycle_probe = await asyncio.wait_for(
                        service.query_run_status(normalized_run_id),
                        timeout=query_remaining_seconds,
                    )
                    process_terminal = str(lifecycle_probe.status or "").strip().lower() in {
                        "blocked",
                        "cancelled",
                        "completed",
                        "failed",
                        "success",
                        "timeout",
                    }
            canonical_probe = self._run_completion_waiter.canonical_terminal_result(
                run_id=normalized_run_id,
                process_terminal=process_terminal,
            )
            if canonical_probe is not None:
                return self._with_execution_barrier_progress(
                    canonical_probe,
                    progress_extensions=progress_extensions,
                    elapsed_seconds=loop.time() - started_at,
                    max_total_seconds=hard_limit_seconds,
                    deferred_cancel_reason=deferred_cancel_reason,
                )

            next_progress_marker = self._active_director_execution_progress_marker(run_id=normalized_run_id)
            if next_progress_marker and next_progress_marker != progress_marker:
                progress_marker = next_progress_marker
                progress_extensions += 1

            remaining = observation_deadline - loop.time()
            if remaining <= 0:
                barrier_result = self._active_director_task_barrier_result(
                    run_id=normalized_run_id,
                    reason="factory_stage_timeout",
                    grace_seconds=grace_seconds,
                )
                if barrier_result is not None:
                    timeout_result = self._execution_barrier_timeout_result(
                        barrier_result,
                        grace_seconds=grace_seconds,
                    )
                    return self._with_execution_barrier_progress(
                        timeout_result,
                        progress_extensions=progress_extensions,
                        elapsed_seconds=loop.time() - started_at,
                        max_total_seconds=hard_limit_seconds,
                        deferred_cancel_reason=deferred_cancel_reason,
                    )
                barrier_result, cancel_completed = await _cancel_within_hard_deadline("factory_stage_timeout")
                if barrier_result is not None:
                    return barrier_result
                return CommandResult(
                    run_id=normalized_run_id,
                    status="timeout",
                    message="Director run timed out after timeout settle grace",
                    metadata={
                        "cancel_signal_sent": cancel_completed,
                        "cancel_reason": "factory_stage_timeout",
                        "timeout_settle_grace_seconds": grace_seconds,
                        "inflight_run_continues": not cancel_completed,
                    },
                )
            await asyncio.sleep(min(2.0, remaining))

    def _active_director_execution_progress_marker(
        self,
        *,
        run_id: str,
    ) -> tuple[tuple[str, str, str, str], ...]:
        """Read the TaskRuntime-owned progress marker for a child run."""

        progress_probe = getattr(self._run_completion_waiter, "active_execution_progress_marker", None)
        if not callable(progress_probe):
            return ()
        with contextlib.suppress(RuntimeError, OSError, TypeError, ValueError):
            marker = progress_probe(run_id=run_id)
            if isinstance(marker, tuple):
                return tuple(item for item in marker if isinstance(item, tuple) and len(item) == 4)
        return ()

    @staticmethod
    def _with_execution_barrier_progress(
        result: CommandResult,
        *,
        progress_extensions: int,
        elapsed_seconds: float,
        max_total_seconds: float,
        deferred_cancel_reason: str = "",
    ) -> CommandResult:
        """Attach hard-deadline and progress evidence to a barrier result."""

        normalized_cancel_reason = str(deferred_cancel_reason or "").strip()
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        progress_metadata: dict[str, Any] = {
            "barrier_elapsed_seconds": round(max(0.0, elapsed_seconds), 3),
            "barrier_max_total_seconds": round(max(0.0, max_total_seconds), 3),
        }
        if progress_extensions > 0:
            progress_metadata.update(
                {
                    "barrier_progress_extensions": progress_extensions,
                    "barrier_progress_source": "task_runtime_execution_fact",
                }
            )
        if normalized_cancel_reason:
            progress_metadata.update(
                {
                    "barrier_cancel_deferred": True,
                    "deferred_cancel_reason": normalized_cancel_reason,
                    "cancel_signal_sent": False,
                }
            )
        return CommandResult(
            run_id=str(result.run_id or "").strip(),
            status=str(result.status or "").strip(),
            message=result.message,
            reason_code=result.reason_code,
            stage_results=result.stage_results,
            started_at=result.started_at,
            completed_at=result.completed_at,
            artifacts=result.artifacts,
            metadata={
                **metadata,
                **progress_metadata,
            },
        )

    @staticmethod
    def _execution_barrier_timeout_result(
        result: CommandResult,
        *,
        grace_seconds: int,
    ) -> CommandResult:
        """Project a still-active child as an explicit control-plane timeout."""

        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        return CommandResult(
            run_id=str(result.run_id or "").strip(),
            status="timeout",
            message="Director child execution remained active after settlement barrier timeout",
            reason_code=result.reason_code,
            stage_results=result.stage_results,
            started_at=result.started_at,
            completed_at=result.completed_at,
            artifacts=result.artifacts,
            metadata={
                **metadata,
                "cancel_signal_sent": False,
                "inflight_run_continues": True,
                "timeout_settle_grace_seconds": grace_seconds,
                "barrier_state": "timeout",
                "barrier_timeout": True,
                "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                "responsible_layer": "execution_control_plane",
            },
        )

    def _active_director_task_barrier_result(
        self,
        *,
        run_id: str,
        reason: str,
        grace_seconds: int,
    ) -> CommandResult | None:
        """Leave an active Director lease intact while external cancellation settles.

        Factory deadlines are outside the Director tool-dispatch transaction. If
        TaskRuntime still reports active work, suspending the child lease creates
        a secondary ``session_not_active`` failure and hides the actual
        execution-control-plane condition. The factory stage may stop waiting,
        but the child execution remains valid so tool/effect receipts can settle
        into the ledger.
        """

        barrier_probe = getattr(self._run_completion_waiter, "active_execution_barrier_result", None)
        if not callable(barrier_probe):
            return None
        with contextlib.suppress(RuntimeError, OSError, TypeError, ValueError):
            result = barrier_probe(run_id=run_id, reason=reason)
            if isinstance(result, CommandResult):
                metadata = result.metadata if isinstance(result.metadata, dict) else {}
                return CommandResult(
                    run_id=str(result.run_id or run_id).strip(),
                    status=str(result.status or "").strip(),
                    message=result.message,
                    reason_code=result.reason_code,
                    stage_results=result.stage_results,
                    started_at=result.started_at,
                    completed_at=result.completed_at,
                    artifacts=result.artifacts,
                    metadata={
                        **metadata,
                        "timeout_settle_grace_seconds": grace_seconds,
                    },
                )
        return None

    @staticmethod
    def _resolve_cancel_event(context: dict[str, Any]) -> asyncio.Event | None:
        return RunCompletionWaiter.resolve_cancel_event(context)

    @staticmethod
    def _resolve_abort_checker(
        context: dict[str, Any],
    ) -> Callable[[], Awaitable[str | None]] | None:
        return RunCompletionWaiter.resolve_abort_checker(context)
