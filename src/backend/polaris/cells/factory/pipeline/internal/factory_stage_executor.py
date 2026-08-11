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
            # The heartbeat call itself may legitimately consume the full
            # lock-timeout interval. Joining for that exact same duration races
            # scheduler wake-up and falsely reports a live thread after the call
            # has already returned. Keep shutdown bounded, but allow a small
            # scheduling margin beyond the governed heartbeat interval.
            thread.join(timeout=self._budget.heartbeat_interval_seconds + 0.1)
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


def _workspace_quality_repair_external_task_id(run_id: str, repair_attempt: int) -> str:
    """Mint an EventStore-safe synthetic Director repair task identity."""

    safe_run_id = re.sub(r"[^0-9A-Za-z._-]+", "-", str(run_id or "")).strip("-._")[:96] or "run"
    return f"factory-quality-gate-{safe_run_id}-repair-{max(1, int(repair_attempt))}-{uuid.uuid4().hex[:12]}"


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

        return prompt_compaction.compact_workspace_quality_evidence_for_qa(text)

    @staticmethod
    def _compact_blueprint_evidence_for_repair(text: str) -> str:
        return prompt_compaction.compact_blueprint_evidence_for_repair(text)

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

    _EXISTING_SUMMARY_SOURCE_SUFFIXES = target_summaries._EXISTING_SUMMARY_SOURCE_SUFFIXES
    _EXISTING_SUMMARY_MAX_FILES = target_summaries._EXISTING_SUMMARY_MAX_FILES

    def _read_existing_target_file_summaries(
        self, task: dict[str, Any], *, max_chars_per_file: int = 1500
    ) -> list[dict[str, str]]:
        """Summarize the export API of files this task depends on but does NOT own."""

        return target_summaries.read_existing_target_file_summaries(
            self.workspace, task, max_chars_per_file=max_chars_per_file
        )

    @staticmethod
    def _extract_js_export_summary(content: str) -> str:
        """Extract JS/TS export signatures so dependent files reference real symbols."""

        return target_summaries.extract_js_export_summary(content)

    @staticmethod
    def _extract_py_export_summary(content: str) -> str:
        """Extract Python export signatures for cross-file coherence."""

        return target_summaries.extract_py_export_summary(content)

    @staticmethod
    def _extract_py_export_summary_fallback(content: str) -> str:
        """Line-scan fallback when the dependency source does not parse as Python."""

        return target_summaries.extract_py_export_summary_fallback(content)

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

    def _read_claimable_director_task_ids(
        self,
        *,
        limit: int,
        factory_run_id: str = "",
        allowed_task_ids: Iterable[str] | None = None,
    ) -> list[str]:
        """Return claimable PM ids confined to the admitted dependency wave.

        TaskRuntime readiness is the execution-state authority, while the
        immutable PM contract owns the dependency DAG.  ``blocked_by`` on
        legacy task rows is not a substitute for that contract: older rows may
        only carry ``depends_on`` in metadata.  The caller therefore supplies
        the currently admitted wave and this projection intersects both facts
        before any Director provider request can start.
        """
        if limit <= 0:
            return []
        allowed: set[str] | None = None
        if allowed_task_ids is not None:
            allowed = {str(item or "").strip() for item in allowed_task_ids if str(item or "").strip()}
            if not allowed:
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
            if allowed is not None and task_id not in allowed:
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
        dependency_tasks: list[dict[str, Any]] | None = None,
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
            allowed_task_ids: Iterable[str] | None = None
            if dependency_tasks is not None:
                schedule = self._director_dependency_schedule(
                    dependency_tasks,
                    factory_run_id=factory_run_id,
                )
                allowed_task_ids = schedule.waves[0] if schedule.valid and schedule.waves else ()
            claim_kwargs: dict[str, Any] = {
                "limit": limit,
                "factory_run_id": factory_run_id,
            }
            if _call_accepts_keyword(self._read_claimable_director_task_ids, "allowed_task_ids"):
                claim_kwargs["allowed_task_ids"] = allowed_task_ids
            task_ids = self._read_claimable_director_task_ids(**claim_kwargs)
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

        return route_audit.director_admission_failure_projection(admission_decision)

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
        return director_dispatch_impl._record_director_binding_skip(
            self, provider_id=provider_id, model=model, binding_id=binding_id, reason=reason
        )

    def _director_readiness_skip_reasons(self, context: dict[str, Any] | None = None) -> dict[str, str]:
        return director_dispatch_impl._director_readiness_skip_reasons(self, context)

    def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
        return director_dispatch_impl._resolve_director_binding_fanout(self, context)

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
        return await director_dispatch_impl._execute_director_binding_fanout(
            self,
            service=service,
            workspace=workspace,
            tasks=tasks,
            base_options=base_options,
            bindings=bindings,
            timeout_seconds=timeout_seconds,
            deadline_monotonic=deadline_monotonic,
            cancel_event=cancel_event,
            abort_checker=abort_checker,
            skipped_bindings=skipped_bindings,
            authority_port=authority_port,
        )

    @staticmethod
    def _build_per_binding_route_events(per_binding: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return route_audit.build_per_binding_route_events(per_binding)

    @staticmethod
    def _build_fail_closed_director_route_events(
        *,
        attempts: list[dict[str, Any]],
        stage_signals: list[dict[str, Any]],
        per_binding_route_events: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        return route_audit.build_fail_closed_director_route_events(
            attempts=attempts,
            stage_signals=stage_signals,
            per_binding_route_events=per_binding_route_events,
        )

    @staticmethod
    def _reclassify_binding_coverage_signals(
        stage_signals: list[dict[str, Any]],
        per_binding_route_events: list[dict[str, Any]],
    ) -> None:
        route_audit.reclassify_binding_coverage_signals(stage_signals, per_binding_route_events)

    def _validate_director_binding_coverage(
        self,
        additional_events: list[dict[str, Any]] | None = None,
    ) -> tuple[bool, list[dict[str, Any]]]:
        return route_audit.validate_director_binding_coverage(
            self.workspace,
            additional_events=additional_events,
        )

    def _director_provider_health_failure_signal(self) -> dict[str, Any] | None:
        return route_audit.director_provider_health_failure_signal(self.workspace)

    @staticmethod
    def _director_provider_health_failure_signal_from_events(
        events: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        return route_audit.director_provider_health_failure_signal_from_events(events)

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

    def _complete_chief_engineer_attempt_after_schema_repair(
        self,
        *,
        run_id: str,
        objective: str,
        lease_budget: _ChiefEngineerExecutionAttemptLeaseBudget,
    ) -> None:
        """Close the original CE helper after its bounded repair succeeds.

        The invalid primary response is suspended before the separately
        claimed schema-repair attempt.  A successful repair supersedes that
        response, so the original helper must be re-claimed and terminally
        completed; otherwise its pending row survives forever and makes an
        otherwise verified project fail ``task_runtime_not_completed``.
        """

        portfolio_task_id = f"CE-PORTFOLIO-{run_id}"
        task_id, execution_attempt = self._claim_chief_engineer_execution_attempt(
            run_id=run_id,
            portfolio_task_id=portfolio_task_id,
            objective=objective,
            lease_budget=lease_budget,
        )
        self._settle_chief_engineer_execution_attempt(
            task_id=task_id,
            execution_attempt=execution_attempt,
            stage_status="success",
            summary="chief_engineer_primary_attempt_superseded_by_schema_repair",
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
            if result.ok:
                self._complete_chief_engineer_attempt_after_schema_repair(
                    run_id=run.id,
                    objective=repair_objective,
                    lease_budget=repair_lease_budget,
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
        return await director_dispatch_impl._execute_director_dispatch(self, run, context)

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
        return workspace_quality_impl._workspace_quality_repair_errors(self, results)

    @staticmethod
    def _workspace_quality_diagnostic_signature(errors: Iterable[str]) -> tuple[str, ...]:
        """Return a stable verifier-diagnostic signature for convergence checks.

        Repair success is owned by the post-repair verifier, not by a write-tool
        receipt.  Normalize whitespace/case so formatting jitter does not buy
        another Provider attempt, while preserving paths/codes/symbols needed to
        distinguish a real diagnostic change.
        """

        normalized = {" ".join(str(error or "").split()).casefold() for error in errors if str(error or "").strip()}
        return tuple(sorted(normalized))

    @staticmethod
    def _workspace_quality_repair_effect(
        *,
        before_signature: tuple[str, ...],
        after_signature: tuple[str, ...],
        verifier_passed: bool,
        write_tool_evidence: bool,
    ) -> str:
        """Classify one local repair by verifier effect, never by model claim."""

        if verifier_passed:
            return "resolved"
        if not write_tool_evidence:
            return "no_op"
        if len(after_signature) < len(before_signature):
            return "progress"
        if len(after_signature) > len(before_signature):
            return "regression"
        if after_signature == before_signature:
            return "stagnant"
        # An equal-count diagnostic swap is not demonstrated progress.  Treat it
        # as stagnation so a model cannot burn the full budget by trading one
        # compiler error for another indefinitely.
        return "equal_count_swap"

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
        return materialization_impl._director_stage_should_run_materialization_quality_settle(
            self, stage_status=stage_status, error_code=error_code
        )

    def _workspace_has_delivery_surface(self) -> bool:
        return materialization_impl._workspace_has_delivery_surface(self)

    def _recover_director_stage_authority_after_delivery_settle(
        self,
        *,
        run: FactoryRun,
        context: dict[str, Any],
        prior_authority: helpers.CanonicalFactoryAuthority,
    ) -> helpers.CanonicalFactoryAuthority | None:
        return materialization_impl._recover_director_stage_authority_after_delivery_settle(
            self, run=run, context=context, prior_authority=prior_authority
        )

    def _seal_director_stage_missing_tool_lifecycles(
        self,
        *,
        run: FactoryRun,
        incomplete_task_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        return materialization_impl._seal_director_stage_missing_tool_lifecycles(
            self, run=run, incomplete_task_ids=incomplete_task_ids
        )

    def _collect_director_stage_materialization_diagnostics(self) -> list[str]:
        return materialization_impl._collect_director_stage_materialization_diagnostics(self)

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
        return materialization_impl._claim_director_stage_materialization_settle_attempt(self, run_id=run_id)

    @staticmethod
    def _workspace_quality_repair_owner_score(
        candidate: Mapping[str, Any],
        *,
        run_id: str,
        normalized_targets: set[str],
    ) -> tuple[int, int]:
        """Score only task-owned paths when selecting a verifier-repair owner.

        ``project_declared_target_files`` is a project-wide inventory copied to
        every Director task.  Treating it as ownership makes unrelated tasks tie
        for every project file; the failed/rework priority then selects the wrong
        task and the Director correctly refuses the out-of-scope edit.  Ownership
        is established only by the task-local ``target_files``/``scope_paths``.
        """

        metadata = candidate.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        candidate_factory_run_id = str(metadata.get("factory_run_id") or "").strip()
        external_id = str(metadata.get("external_task_id") or candidate.get("external_task_id") or "").strip()
        if candidate_factory_run_id != run_id or not external_id or external_id.startswith("factory-"):
            return (-1, -1)
        raw_paths: list[Any] = []
        for key in ("target_files", "scope_paths"):
            value = metadata.get(key)
            if isinstance(value, str):
                raw_paths.append(value)
            elif isinstance(value, list | tuple | set):
                raw_paths.extend(value)
        candidate_paths = {str(path or "").strip().replace("\\", "/") for path in raw_paths if str(path or "").strip()}
        overlap = len(normalized_targets.intersection(candidate_paths))
        status = str(candidate.get("status") or candidate.get("raw_status") or "").strip().lower()
        rework_priority = 1 if status in {"pending", "ready", "blocked", "failed"} else 0
        return (overlap, rework_priority)

    def _claim_workspace_quality_repair_attempt(
        self,
        *,
        run_id: str,
        repair_attempt: int,
        target_files: list[str],
    ) -> tuple[str, int, TaskRuntimeExecutionAttemptIdentityV1, dict[str, Any]]:
        return workspace_quality_impl._claim_workspace_quality_repair_attempt(
            self, run_id=run_id, repair_attempt=repair_attempt, target_files=target_files
        )

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
        return materialization_impl._settle_director_stage_materialization_attempt(
            self,
            task_row_id=task_row_id,
            execution_attempt=execution_attempt,
            stage_status=stage_status,
            summary=summary,
        )

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
        factory_stage: str = "director_dispatch",
    ) -> dict[str, Any]:
        return materialization_impl._director_stage_materialization_settle_commit_context(
            self, run=run, run_id=run_id, diagnostics=diagnostics, factory_stage=factory_stage
        )

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
        return await materialization_impl._run_director_stage_materialization_quality_settle(
            self, run=run, stage_status=stage_status, error_code=error_code
        )

    def _apply_workspace_quality_repairs(
        self,
        *,
        run_id: str,
        artifact_quality_errors: list[str],
        task_id: str | None = None,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return workspace_quality_impl._apply_workspace_quality_repairs(
            self,
            run_id=run_id,
            artifact_quality_errors=artifact_quality_errors,
            task_id=task_id,
            execution_attempt=execution_attempt,
        )

    async def _apply_workspace_quality_deterministic_repairs(
        self,
        *,
        run: FactoryRun,
        artifact_quality_errors: list[str],
        repair_attempt: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return await workspace_quality_impl._apply_workspace_quality_deterministic_repairs(
            self, run=run, artifact_quality_errors=artifact_quality_errors, repair_attempt=repair_attempt
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
        owner_target_files: list[str] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return await workspace_quality_impl._apply_workspace_quality_llm_repairs(
            self,
            run_id=run_id,
            context=context,
            artifact_quality_errors=artifact_quality_errors,
            repair_attempt=repair_attempt,
            interface_discrepancy_evidence=interface_discrepancy_evidence,
            owner_target_files=owner_target_files,
        )

    @staticmethod
    def _workspace_quality_repair_result_has_mutation(item: dict[str, Any]) -> bool:
        return wq_evidence.workspace_quality_repair_result_has_mutation(item)

    @staticmethod
    def _workspace_quality_repair_evidence(repair_results: list[dict[str, Any]]) -> list[str]:
        return wq_evidence.workspace_quality_repair_evidence(repair_results)

    @staticmethod
    def _workspace_quality_summary_requires_task_boundary_triage(summary: dict[str, Any]) -> bool:
        return wq_evidence.workspace_quality_summary_requires_task_boundary_triage(summary)

    @staticmethod
    def _workspace_quality_deferred_owner_targets(summary: dict[str, Any]) -> list[str]:
        """Return precise targets deferred because the first repair task did not own them."""

        return wq_evidence.workspace_quality_deferred_owner_targets(summary)

    @staticmethod
    def _workspace_quality_interface_discrepancy_evidence(
        summary: dict[str, Any],
        artifact_quality_errors: list[str] | None = None,
    ) -> dict[str, Any]:
        return wq_evidence.workspace_quality_interface_discrepancy_evidence(summary, artifact_quality_errors)

    @staticmethod
    def _workspace_quality_interface_discrepancy_allows_director_retry(evidence: dict[str, Any]) -> bool:
        return wq_evidence.workspace_quality_interface_discrepancy_allows_director_retry(evidence)

    @staticmethod
    def _workspace_quality_repair_summary_projection(
        summary: dict[str, Any],
        artifact_quality_errors: list[str] | None = None,
    ) -> dict[str, Any]:
        return wq_evidence.workspace_quality_repair_summary_projection(summary, artifact_quality_errors)

    async def _run_workspace_quality_checks(self, run: FactoryRun, context: dict[str, Any]) -> tuple[bool, str]:
        return await workspace_quality_impl._run_workspace_quality_checks(self, run, context)

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
        structured_authority = self._build_qa_final_request_metadata(
            run_id=run.id,
            workspace_checks_artifact="",
        )
        target_files = self._merge_string_list(
            context.get("target_files")
            or context.get("declared_source_targets")
            or context.get("code_files")
            or context.get("scope_paths")
            or structured_authority.get("target_files")
        )
        scope_paths = self._merge_string_list(
            context.get("scope_paths") or structured_authority.get("scope_paths") or target_files
        )
        record = {
            "id": str(context.get("project_id") or context.get("requested_project_id") or run.id),
            "project_id": str(context.get("project_id") or context.get("requested_project_id") or run.id),
            "run_id": run.id,
            "target_files": target_files,
            "scope_paths": scope_paths,
            "pm_task_contract": structured_authority.get("pm_task_contract") or {},
            "pm_task_contracts": structured_authority.get("pm_task_contracts") or [],
            "chief_engineer_blueprint": structured_authority.get("chief_engineer_blueprint") or {},
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
            "gate_obligation_id": f"factory:{run.id}:workspace_validation",
            "gate_subject_kind": "factory_run",
            "gate_subject_id": run.id,
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
        qa_invoked: bool = False,
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
            "verdict": "FAIL" if qa_invoked else "NOT_RUN",
            "qa_invoked": qa_invoked,
            "canonical_qa_verdict": False,
            "verdict_source": "qa_runtime" if qa_invoked else "deterministic_factory_gate",
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

    @staticmethod
    def _quality_gate_requires_llm_judgement(context: dict[str, Any]) -> bool:
        """Return whether QA needs non-physical semantic judgement.

        Build/test/lint/entrypoint receipts are authoritative physical
        evidence. Repeating them through a general QA LLM adds latency and
        failure surface without adding authority. LLM QA is opt-in for
        modalities that genuinely require semantic or visual judgement.
        """

        sources = [context]
        qa_input = context.get("qa_input")
        if isinstance(qa_input, Mapping):
            sources.append(dict(qa_input))

        explicit_keys = (
            "qa_llm_required",
            "qa_requires_llm_judgement",
            "qa_semantic_review_required",
            "qa_security_review_required",
            "qa_visual_required",
            "requires_visual_evidence",
        )
        if any(bool(source.get(key)) for source in sources for key in explicit_keys):
            return True

        modes = {
            str(source.get(key) or "").strip().lower()
            for source in sources
            for key in ("qa_mode", "qa_judgement_mode", "qa_review_mode")
        }
        if modes.intersection({"llm", "llm_required", "semantic", "visual", "security"}):
            return True

        required_modalities: set[str] = set()
        for source in sources:
            for key in ("required_evidence_modalities", "qa_required_modalities"):
                raw = source.get(key)
                if isinstance(raw, str):
                    required_modalities.update(item.strip().lower() for item in raw.split(",") if item.strip())
                elif isinstance(raw, (list, tuple, set)):
                    required_modalities.update(
                        str(item or "").strip().lower() for item in raw if str(item or "").strip()
                    )
        return bool(
            required_modalities.intersection(
                {"visual", "image", "semantic", "semantic_review", "security", "security_review"}
            )
        )

    def _write_physical_verifier_qa_report(
        self,
        *,
        run: FactoryRun,
        workspace_checks_artifact: str,
    ) -> dict[str, Any]:
        """Persist deterministic QA evidence after physical checks pass."""

        payload: dict[str, Any] = {
            "schema_version": "factory.qa_physical_verifier_report.v1",
            "source": "factory_physical_verifier",
            "factory_run_id": run.id,
            "passed": True,
            "verdict": "PASS",
            "score": 100.0,
            "critical_issue_count": 0,
            "critical_issues": [],
            "major_issues": [],
            "warnings": [],
            "workspace_checks_artifact": workspace_checks_artifact,
            "qa_invoked": False,
            "llm_invoked": False,
        }
        self._write_json_artifact("runtime/qa/report.json", payload)
        return payload

    def _build_qa_execution_metadata(
        self,
        *,
        run_id: str,
        workspace_checks_artifact: str,
        context: dict[str, Any],
    ) -> tuple[dict[str, Any], int]:
        """Bind structured QA evidence and its deadline-derived LLM budget."""

        qa_wait_timeout_seconds = self._quality_gate_qa_wait_timeout_seconds(context)
        qa_request_timeout_seconds = max(
            1,
            qa_wait_timeout_seconds - int(_QUALITY_GATE_QA_DEADLINE_SAFETY_SECONDS),
        )
        metadata = self._build_qa_final_request_metadata(
            run_id=run_id,
            workspace_checks_artifact=workspace_checks_artifact,
        )
        metadata.update(
            {
                "request_timeout_seconds": qa_request_timeout_seconds,
                "timeout_seconds": qa_request_timeout_seconds,
            }
        )
        return metadata, qa_wait_timeout_seconds

    def _build_qa_input_with_workspace_quality_evidence(
        self,
        qa_input: object,
        workspace_checks_artifact: str,
        *,
        run_id: str = "",
    ) -> str:
        base_input = str(qa_input or "").strip()
        sections = [base_input] if base_input else []
        if workspace_checks_artifact or run_id:
            # The actual evidence is attached as structured final-request slots
            # by ``_build_qa_final_request_metadata``.  Do not duplicate raw
            # audit JSON in the user prompt: it wastes tokens and may leak
            # control-plane run identifiers into ContextOS prompt content.
            sections.append(
                "Evaluate the structured PM contract, Chief Engineer blueprint, "
                "target files, verifier receipts, and workspace-quality evidence attached to this QA request."
            )
        return "\n\n".join(sections)

    def _build_qa_final_request_metadata(
        self,
        *,
        run_id: str,
        workspace_checks_artifact: str,
    ) -> dict[str, Any]:
        """Build QA's five required structured final-request evidence slots."""

        pm_tasks = self._load_pm_plan_tasks("tasks/plan.json")
        ce_blueprint = self._load_chief_engineer_review_payload(run_id=run_id)
        if not ce_blueprint:
            for candidate in (
                f"runtime/blueprints/{run_id}.review.json",
                f"workspace/.polaris/blueprints/{run_id}.review.json",
                "workspace/.polaris/blueprints/latest.review.json",
            ):
                ce_blueprint = self._read_json_artifact_payload(candidate)
                if ce_blueprint:
                    break
        workspace_quality = (
            self._read_json_artifact_payload(workspace_checks_artifact)
            if str(workspace_checks_artifact or "").strip()
            else {}
        )
        target_files = self._collect_declared_delivery_targets(pm_tasks)
        raw_receipts = workspace_quality.get("commands") if isinstance(workspace_quality, dict) else None
        verifier_receipts = (
            [dict(item) for item in raw_receipts if isinstance(item, dict)] if isinstance(raw_receipts, list) else []
        )

        metadata: dict[str, Any] = {
            "source": "factory_stage_executor.quality_gate",
            "pm_task_contracts": deepcopy(pm_tasks),
            "target_files": list(target_files),
            "scope_paths": list(target_files),
            "chief_engineer_blueprint": deepcopy(ce_blueprint),
            "verifier_receipts": verifier_receipts,
            "workspace_quality_evidence": deepcopy(workspace_quality),
        }
        if pm_tasks:
            metadata["pm_task_contract"] = deepcopy(pm_tasks[0])
        return metadata

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

    def _canonical_qa_commit_identity(
        self,
        *,
        run: FactoryRun,
        context: dict[str, Any],
    ) -> tuple[str, str]:
        """Return the final owned task and its authoritative child run.

        QA verdict envelopes are task-boundary scoped.  A Factory portfolio is
        a tree of Director child runs, so committing the verdict against the
        Factory root run would never match a TaskBoundary and would correctly
        fail closed.  Bind the final PM contract task to its actual child run;
        the Factory aggregate projection then observes the resulting gate.
        """

        projection = self._canonical_factory_projection(run, context)
        task_boundary = projection.get("task_boundary")
        task_boundary_map = task_boundary if isinstance(task_boundary, Mapping) else {}
        latest_by_task = task_boundary_map.get("latest_by_task")
        latest_by_task_map = latest_by_task if isinstance(latest_by_task, Mapping) else {}
        pm_tasks = self._load_pm_plan_tasks("tasks/plan.json")
        ordered_task_ids = [
            helpers._canonical_task_id_token(task.get("id") or task.get("task_id"))
            for task in pm_tasks
            if isinstance(task, dict)
        ]
        for task_id in reversed([item for item in ordered_task_ids if item]):
            boundary_raw = latest_by_task_map.get(task_id)
            boundary = boundary_raw if isinstance(boundary_raw, Mapping) else {}
            boundary_run_id = str(boundary.get("run_id") or "").strip()
            if (
                boundary_run_id
                and boundary.get("ok") is True
                and str(boundary.get("status") or "").strip().lower() == "completed_verified"
            ):
                return task_id, boundary_run_id
        return "", ""

    async def _commit_qa_role_report_authority(
        self,
        *,
        run: FactoryRun,
        context: dict[str, Any],
        qa_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Commit the completed QA role report through ``qa.audit_verdict``."""

        from polaris.cells.qa.audit_verdict.public import (
            CommitQaRoleVerdictCommandV1,
            commit_qa_role_verdict,
        )

        task_id, qa_run_id = self._canonical_qa_commit_identity(run=run, context=context)
        if not task_id or not qa_run_id:
            return {"success": False, "reason": "canonical_qa_task_boundary_identity_missing"}
        verdict = str(qa_payload.get("verdict") or "").strip().upper()
        passed = bool(qa_payload.get("passed"))
        if not verdict:
            verdict = "PASS" if passed else "FAIL"
        if passed != (verdict == "PASS"):
            return {"success": False, "reason": "qa_role_report_verdict_inconsistent"}
        raw_findings = [
            *(qa_payload.get("critical_issues") or []),
            *(qa_payload.get("major_issues") or []),
            *(qa_payload.get("warnings") or []),
        ]
        findings = tuple(str(item).strip() for item in raw_findings if str(item).strip())
        target_files = tuple(self._collect_declared_delivery_targets(self._load_pm_plan_tasks("tasks/plan.json")))
        commit_context = self._director_stage_materialization_settle_commit_context(
            run=run,
            run_id=qa_run_id,
            diagnostics=[],
            factory_stage="quality_gate",
        )
        job_token_raw = commit_context.get("job_token")
        job_token = dict(job_token_raw) if isinstance(job_token_raw, Mapping) else {}
        report_content_hash = hashlib.sha256(
            json.dumps(qa_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        try:
            score = float(qa_payload.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        try:
            critical_issue_count = int(qa_payload.get("critical_issue_count") or 0)
        except (TypeError, ValueError):
            critical_issue_count = len(qa_payload.get("critical_issues") or [])
        try:
            result = await asyncio.to_thread(
                commit_qa_role_verdict,
                CommitQaRoleVerdictCommandV1(
                    task_id=task_id,
                    workspace=str(self.workspace),
                    run_id=qa_run_id,
                    verdict=verdict,
                    passed=passed,
                    score=score,
                    critical_issue_count=critical_issue_count,
                    findings=findings,
                    target_files=target_files,
                    report_ref="runtime/qa/report.json",
                    report_content_hash=report_content_hash,
                    job_token=job_token,
                    metadata={
                        "source": str(qa_payload.get("source") or "factory_stage_executor.quality_gate"),
                        "factory_run_id": run.id,
                    },
                ),
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("Canonical QA role verdict commit failed for %s: %s", run.id, exc)
            return {"success": False, "reason": f"qa_role_verdict_commit_failed:{type(exc).__name__}:{exc}"}
        metadata = dict(result.metadata)
        return {
            "success": bool(metadata.get("qa_verdict_committed")),
            "verdict": result.verdict,
            "ok": result.ok,
            "task_id": task_id,
            "run_id": qa_run_id,
            "receipt": dict(metadata.get("qa_verdict_commit_receipt") or {}),
            "reason": "" if metadata.get("qa_verdict_committed") else "qa_verdict_commit_receipt_missing",
        }

    def _reconcile_verified_runtime_delivery(
        self,
        *,
        run: FactoryRun,
        authority: helpers.CanonicalFactoryAuthority,
    ) -> dict[str, Any]:
        """Settle exact failed PM rows whose canonical delivery is verified.

        This is not a disk-only success override.  ``recovered_runtime_task_ids``
        is produced only when the canonical Run Ledger has a terminal runtime
        fact plus an owned ``TaskBoundary completed_verified`` fact.  Quality
        reconciliation runs only after the same projection also contains a
        passing QA verdict, sequence barrier, and evidence-policy result.
        """

        recovered_ids = tuple(authority.recovered_runtime_task_ids)
        if not recovered_ids:
            return {"success": True, "reconciled_task_ids": []}
        if not authority.quality_stage_authorized:
            return {
                "success": False,
                "reason": "canonical_quality_authority_not_verified",
                "reconciled_task_ids": [],
            }

        runtime = TaskRuntimeService(str(self.workspace))
        rows = runtime.list_observable_task_rows()
        reconciled: list[str] = []
        for external_task_id in recovered_ids:
            matches: list[dict[str, Any]] = []
            for raw_row in rows:
                row = dict(raw_row) if isinstance(raw_row, dict) else {}
                raw_metadata = row.get("metadata")
                metadata: dict[str, Any] = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
                aliases = {
                    str(source.get(key) or "").strip()
                    for source in (row, metadata)
                    for key in ("external_task_id", "source_task_id", "pm_task_id")
                    if str(source.get(key) or "").strip()
                }
                factory_run_id = str(metadata.get("factory_run_id") or "").strip()
                if external_task_id in aliases and factory_run_id == run.id:
                    matches.append(row)
            if len(matches) != 1:
                return {
                    "success": False,
                    "reason": "verified_delivery_runtime_owner_not_unique",
                    "external_task_id": external_task_id,
                    "match_count": len(matches),
                    "reconciled_task_ids": reconciled,
                }

            task_id = int(matches[0]["id"])
            evidence = {
                "schema_version": "factory.verified_delivery_runtime_reconciliation.v1",
                "factory_run_id": run.id,
                "external_task_id": external_task_id,
                "source": "canonical_run_ledger",
                "task_boundary_completed_verified": authority.task_boundary_completed_verified,
                "qa_verdict_passed": authority.qa_verdict_passed,
                "sequence_barrier_satisfied": authority.sequence_barrier_satisfied,
                "evidence_policy_passed": authority.evidence_policy_passed,
            }
            reopened = runtime.reopen_task_row(
                task_id,
                reason="canonical_delivery_verified_after_terminal_director_attempt",
                metadata={"verified_delivery_reconciliation": evidence},
            )
            if not isinstance(reopened, dict):
                return {
                    "success": False,
                    "reason": "verified_delivery_runtime_reopen_failed",
                    "external_task_id": external_task_id,
                    "reconciled_task_ids": reconciled,
                }
            claim = runtime.claim_execution(
                task_id,
                worker_id=f"factory-quality-gate:{run.id}",
                role_id="qa",
                run_id=run.id,
                lease_ttl_seconds=120,
                selection_source="factory_verified_delivery_reconciliation",
                external_task_id=external_task_id,
                metadata={"verified_delivery_reconciliation": evidence},
            )
            attempt_record = claim.get("execution_attempt") if isinstance(claim, dict) else None
            if not bool(claim.get("success")) or not isinstance(attempt_record, dict):
                return {
                    "success": False,
                    "reason": str(claim.get("reason") or "verified_delivery_runtime_claim_failed"),
                    "external_task_id": external_task_id,
                    "reconciled_task_ids": reconciled,
                }
            execution_attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(attempt_record)
            settled = runtime.settle_execution_attempt(
                SettleTaskRuntimeExecutionAttemptCommandV1(
                    workspace=str(self.workspace),
                    identity=execution_attempt,
                    outcome="completed",
                    summary="canonical_delivery_completed_verified_and_qa_passed",
                    lock_timeout_seconds=5.0,
                    metadata={"verified_delivery_reconciliation": evidence},
                )
            )
            if not bool(settled.get("success")):
                return {
                    "success": False,
                    "reason": str(settled.get("reason") or "verified_delivery_runtime_settlement_failed"),
                    "external_task_id": external_task_id,
                    "reconciled_task_ids": reconciled,
                }
            reconciled.append(external_task_id)
        return {"success": True, "reconciled_task_ids": reconciled}

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
        if workspace_checks_passed is False:
            # Deterministic physical verification owns artifact/build/test/lint/
            # entrypoint failures. The workspace loop above already performs
            # bounded, same-Director-task repair and affected-command
            # revalidation. Sending the same hard failure to the advisory QA
            # LLM duplicates tokens, can overwrite the typed residual with a
            # subjective verdict, and cannot add authority. Preserve the
            # verifier artifact and stop this stage as a repairable delivery
            # failure; PM/CE remain out of the loop.
            return self._build_quality_gate_failure_stage(
                run,
                reason_code="factory_quality_gate_workspace_validation_failed",
                detail=(
                    self._workspace_quality_failure_detail(workspace_checks_artifact)
                    if workspace_checks_artifact
                    else "Workspace validation failed without an authoritative evidence artifact"
                ),
                context=context,
                workspace_checks_artifact=workspace_checks_artifact,
                workspace_checks_passed=False,
            )
        qa_input = self._build_qa_input_with_workspace_quality_evidence(
            context.get("qa_input"),
            workspace_checks_artifact,
            run_id=run.id,
        )
        physical_qa_task_id, physical_qa_run_id = self._canonical_qa_commit_identity(run=run, context=context)
        physical_verifier_qa = bool(
            workspace_checks_artifact
            and physical_qa_task_id
            and physical_qa_run_id
            and not self._quality_gate_requires_llm_judgement(context)
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
        if (
            not physical_verifier_qa
            and remaining_seconds is not None
            and remaining_seconds < _QUALITY_GATE_MIN_QA_START_BUDGET_SECONDS
        ):
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

        qa_invoked = not physical_verifier_qa
        if physical_verifier_qa:
            self._write_physical_verifier_qa_report(
                run=run,
                workspace_checks_artifact=workspace_checks_artifact,
            )
            final_result = CommandResult(
                run_id=run.id,
                status="completed",
                message="physical verifier evidence passed; advisory QA LLM not required",
            )
        else:
            service = self._build_orchestration_service(context)
            qa_request_metadata, qa_wait_timeout_seconds = self._build_qa_execution_metadata(
                run_id=run.id,
                workspace_checks_artifact=workspace_checks_artifact,
                context=context,
            )
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
                            "metadata": qa_request_metadata,
                        },
                    ),
                ),
            )
            final_result = await self._wait_run_completion(
                service,
                command_result,
                timeout_seconds=qa_wait_timeout_seconds,
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
                qa_invoked=qa_invoked,
            )
        if final_status == "timeout":
            return self._build_quality_gate_failure_stage(
                run,
                reason_code="factory_quality_gate_qa_timeout",
                detail=f"Quality gate QA run timed out: {final_result.message or 'N/A'}",
                context=context,
                workspace_checks_artifact=workspace_checks_artifact,
                workspace_checks_passed=workspace_checks_passed,
                qa_invoked=qa_invoked,
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

        preexisting_authority = helpers.evaluate_canonical_factory_authority(
            self._canonical_factory_projection(run, context)
        )
        qa_commit: dict[str, Any] = {"success": False, "reason": "qa_role_report_unavailable"}
        qa_commit_attempted = False
        if preexisting_authority.qa_verdict_present:
            # Idempotent recovery/replay: the ledger verdict is authority.  A
            # stale or diagnostic report mirror must not append a competing
            # revision or overwrite an already-committed canonical decision.
            qa_commit = {
                "success": True,
                "verdict": "PASS" if preexisting_authority.qa_verdict_passed else "FAIL",
                "ok": preexisting_authority.qa_verdict_passed,
                "reason": "canonical_qa_verdict_already_present",
            }
        elif report_ready and parse_error is None and qa_payload:
            qa_commit_attempted = True
            qa_commit = await self._commit_qa_role_report_authority(
                run=run,
                context=context,
                qa_payload=qa_payload,
            )
        if qa_commit_attempted and not bool(qa_commit.get("success")):
            return self._build_quality_gate_failure_stage(
                run,
                reason_code="factory_quality_gate_qa_verdict_commit_failed",
                detail=(
                    "QA role execution completed but canonical qa.audit_verdict commit failed: "
                    f"{qa_commit.get('reason') or 'unknown'}"
                ),
                context=context,
                workspace_checks_artifact=workspace_checks_artifact,
                workspace_checks_passed=workspace_checks_passed,
                qa_invoked=True,
            )

        canonical_authority = await self._wait_for_canonical_quality_authority(
            run,
            context,
        )
        runtime_reconciliation = self._reconcile_verified_runtime_delivery(
            run=run,
            authority=canonical_authority,
        )
        if not bool(runtime_reconciliation.get("success")):
            return self._build_quality_gate_failure_stage(
                run,
                reason_code="factory_quality_gate_runtime_reconciliation_failed",
                detail=(
                    "Quality evidence passed but exact TaskRuntime delivery reconciliation failed: "
                    f"{runtime_reconciliation.get('reason') or 'unknown'}"
                ),
                context=context,
                workspace_checks_artifact=workspace_checks_artifact,
                workspace_checks_passed=workspace_checks_passed,
                qa_invoked=True,
            )
        if runtime_reconciliation.get("reconciled_task_ids"):
            canonical_authority = await self._wait_for_canonical_quality_authority(run, context)
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
            f"; qa_commit_run={qa_commit.get('run_id') or ''}"
            f"; qa_commit_task={qa_commit.get('task_id') or ''}"
            f"; runtime_reconciled={runtime_reconciliation.get('reconciled_task_ids') or []}"
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
