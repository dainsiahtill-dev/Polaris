"""Constants, free helpers, and private support types for stage executor."""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import os
import re
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polaris.cells.chief_engineer.blueprint.public import (
    ProjectKindAuthorityV1,
    VerificationCommandAuthorityV1,
)
from polaris.cells.runtime.task_runtime.public import (
    HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.kernelone.constants import (
    MAX_LLM_PROVIDER_TIMEOUT_SECONDS,  # noqa: F401 — re-exported for characterization-test surface
)
from polaris.kernelone.llm.budget_policy import (
    FACTORY_LLM_STAGE_MIN_START_BUDGET_SECONDS,
)
from polaris.kernelone.tools.tool_kinds import WRITE_TOOLS

from ..factory_deadline_calculations import (  # noqa: F401 — re-exported for characterization-test surface
    _CHIEF_ENGINEER_EXECUTION_ATTEMPT_SETTLEMENT_GRACE_SECONDS,
    _CHIEF_ENGINEER_LLM_TIMEOUT_ENV_KEYS,
    _DEFAULT_CHIEF_ENGINEER_LLM_TIMEOUT_SECONDS,
    ChiefEngineerExecutionAttemptLeaseBudget as _ChiefEngineerExecutionAttemptLeaseBudget,
)
from ._pkg_proxy import pkg

logger = logging.getLogger("polaris.cells.factory.pipeline.internal.factory_stage_executor")
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
        "cargo.toml",
        "cargo.lock",
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
                result = pkg().heartbeat_task_runtime_execution_attempt(
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
    "Cargo.toml",
    "Cargo.lock",
)


def resolve_workspace_quality_existing_file(workspace_root: Path, relative: str) -> Path | None:
    """Resolve a workspace-relative repair file, including lowercase Cargo.toml."""

    normalized = os.path.normpath(str(relative or "").strip().replace("\\", "/")).replace("\\", "/")
    if not normalized or normalized == "." or normalized.startswith("../") or normalized.startswith("/"):
        return None
    direct = workspace_root / normalized
    try:
        if direct.is_file():
            return direct
    except OSError:
        return None
    wanted = Path(normalized).name.lower()
    parent = workspace_root / Path(normalized).parent
    try:
        if not parent.is_dir():
            return None
        for child in parent.iterdir():
            if child.is_file() and child.name.lower() == wanted:
                return child
    except OSError:
        return None
    return None


def workspace_quality_rust_plan_probe_companion_paths(
    workspace_root: Path,
    *,
    artifact_quality_errors: list[str],
) -> list[str]:
    """Include rust crate identity files that diagnostic paths omit.

    Live L2-14 quality plan_probe only loaded ``src/main.rs`` / ``tests/product.rs``.
    Crate rewrite then saw no ``Cargo.toml`` / ``src/lib.rs`` and stayed
    ``covered_unplannable`` despite E0433 ``pirate_treasure_budgeter``.
    """

    joined = "\n".join(str(item or "") for item in artifact_quality_errors).lower()
    has_rust = ".rs" in joined or "error[e0" in joined or "cargo" in joined
    if not has_rust:
        return []
    companions: list[str] = []
    manifest = resolve_workspace_quality_existing_file(workspace_root, "Cargo.toml")
    if manifest is not None:
        companions.append("Cargo.toml")
    lib_path = resolve_workspace_quality_existing_file(workspace_root, "src/lib.rs")
    if lib_path is not None:
        companions.append("src/lib.rs")
    return companions
