"""Task service for managing task lifecycle.

Migrated from ``polaris.cells.director.execution.internal.task_lifecycle_service``.

Handles task creation, scheduling, dependency resolution, and completion tracking.
Integrated with 4-phase state machine for governance.

All text operations MUST explicitly use UTF-8 encoding.

Phase 4 note:
    RepairService and RepairContext belong to director.runtime (Phase 4).
    These are deferred via TYPE_CHECKING for type hints and lazy class resolution
    for runtime instantiation, avoiding import-time failures until Phase 4 migration.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from polaris.cells.audit.verdict.public.service import IndependentAuditService  # type: ignore[type-arg]
from polaris.domain.entities import (
    Task,
    TaskEvidence,
    TaskPriority,
    TaskResult,
    TaskStateError,
    TaskStatus,
)
from polaris.domain.entities.policy import Policy
from polaris.domain.state_machine import (
    PhaseContext,
    PhaseExecutor,
    PhaseResult,
    TaskPhase,
    TaskStateMachine,
)
from polaris.domain.verification import (
    EvidenceCollector,
    EvidencePackage,
    ExistenceGate,
    ImpactAnalyzer,
    ImpactResult,
    ProgressDelta,
    ProgressTracker,
    SoftCheck,
    SoftCheckResult,
    WriteGate,
    create_evidence_collector,
)
from polaris.infrastructure.persistence import EvidenceStore, LogStore, StateStore
from polaris.kernelone.storage import StorageLayout

if TYPE_CHECKING:
    from polaris.cells.audit.verdict.public.service import AuditContext

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Phase 4 dependency deferral: RepairService + RepairContext
# Belongs to director.runtime (Phase 4). Deferred here to avoid import-time
# failures during Phase 3 migration.
# -----------------------------------------------------------------------

# Module-level placeholder for runtime repair service (defined in else block below)
_RepairContext: type | None = None
_RepairService: type | None = None

if TYPE_CHECKING:
    from collections.abc import Callable

    pass

else:
    _rs_mod = None

    with contextlib.suppress(ImportError):
        from polaris.cells.director.tasking.internal import repair_service as _rs_mod

    if _rs_mod is not None:
        _RepairContext = getattr(_rs_mod, "RepairContext", None)
        _RepairService = getattr(_rs_mod, "RepairService", None)

    class _RepairContextPlaceholder:
        """Placeholder until Phase 4 migration provides the real RepairContext."""

        task_id: str = ""
        build_round: int = 0
        max_build_rounds: int = 4
        stall_rounds: int = 0

    if _RepairContext is None:
        _RepairContext = _RepairContextPlaceholder  # type: ignore[assignment,misc]


# -----------------------------------------------------------------------
# Dependency Injection Protocols
# -----------------------------------------------------------------------


class _AuditServiceProvider(Protocol):
    """Protocol for audit service provider."""

    async def run_audit(self, context: AuditContext) -> Any: ...  # type: ignore[valid-type,name-defined]
    def get_stats(self) -> dict[str, Any]: ...


class _RepairServiceProvider(Protocol):
    """Protocol for repair service provider."""

    def should_attempt_repair(
        self,
        audit_accepted: bool,
        soft_check: SoftCheckResult,
        progress: ProgressDelta,
        context: Any,
    ) -> tuple[bool, str]: ...

    async def run_repair_loop(
        self,
        qa_feedback: str,
        context: Any,
        max_repair_rounds: int,
        evidence_collector: EvidenceCollector | None,
    ) -> tuple[bool, list[Any], str]: ...


class _ImpactAnalyzerProvider(Protocol):
    """Protocol for impact analyzer provider."""

    def analyze(
        self,
        changed_files: list[str],
        file_contents: dict[str, str] | None = None,
    ) -> ImpactResult: ...


class _EvidenceStoreProvider(Protocol):
    """Protocol for evidence store provider."""

    def save_evidence(
        self,
        package: dict[str, Any],
        *,
        run_id: str,
        stage: str,
    ) -> dict[str, Any]: ...

    def load_evidence(self, task_id: str, iteration: int) -> dict[str, Any]: ...
    def export_for_role_agent(self, task_id: str, role: str) -> str: ...


class _StateStoreProvider(Protocol):
    """Protocol for state store provider."""

    def save_state(
        self,
        payload: dict[str, Any],
        *,
        run_id: str,
        phase: str,
        status: str,
    ) -> dict[str, Any]: ...

    def load_state(self, task_id: str) -> dict[str, Any]: ...
    def load_lifecycle(self, task_id: str) -> dict[str, Any]: ...
    def load_trajectory(self, task_id: str) -> list[dict[str, Any]]: ...


class _LogStoreProvider(Protocol):
    """Protocol for log store provider."""

    def write_task_log(
        self,
        task_id: str,
        message: str,
        level: str = "INFO",
        source: str = "",
    ) -> None: ...


@dataclass
class TaskQueueConfig:
    """Configuration for task queue."""

    max_queue_size: int = 1000
    default_timeout_seconds: int = 300
    enable_priority_scheduling: bool = True
    enable_dependency_tracking: bool = True


@dataclass
class TaskServiceDeps:
    """Injectable dependencies for TaskService.

    Groups all external service dependencies to enable:
    - Easy testing with mock implementations
    - Clean separation of concerns
    - Dependency injection without 10+ constructor parameters
    """

    impact_analyzer: _ImpactAnalyzerProvider
    evidence_store: _EvidenceStoreProvider
    state_store: _StateStoreProvider
    log_store: _LogStoreProvider
    storage: StorageLayout
    audit_service: _AuditServiceProvider = field(default_factory=lambda: IndependentAuditService())  # type: ignore[misc]
    repair_service: _RepairServiceProvider | None = field(default=None)

    @classmethod
    def create(cls, workspace: str) -> TaskServiceDeps:
        """Factory method to create dependencies from workspace.

        This is the convenience constructor for production use.
        Tests should construct the dataclass directly with mocks.

        Args:
            workspace: Workspace path

        Returns:
            Configured TaskServiceDeps instance
        """
        from polaris.kernelone.storage import resolve_storage_roots

        roots = resolve_storage_roots(workspace)
        storage = StorageLayout(
            workspace=Path(workspace),
            runtime_base=Path(roots.runtime_base),
        )

        # RepairService is a Phase 4 dep — lazily resolved here
        _repair_svc: _RepairServiceProvider | None = None
        _rs_class = _RepairService
        if _rs_class is not None:
            _repair_svc = _rs_class()  # type: ignore[operator]

        return cls(
            audit_service=IndependentAuditService(),  # type: ignore[misc]
            repair_service=_repair_svc,
            impact_analyzer=ImpactAnalyzer(workspace),
            evidence_store=EvidenceStore(storage.runtime_root),
            state_store=StateStore(storage.runtime_root),
            log_store=LogStore(storage.runtime_root),
            storage=storage,
        )


# ==============================================================================
# Main Service
# ==============================================================================


class TaskService:
    """Service for managing tasks.

    Responsibilities:
    - Create and track tasks
    - Manage task dependencies
    - Schedule ready tasks
    - Handle task completion/failure
    - Trigger dependency resolution
    - Execute 4-phase state machine for governance
    """

    def __init__(
        self,
        config: TaskQueueConfig,
        policy: Policy | None = None,
        workspace: str = ".",
        deps: TaskServiceDeps | None = None,
    ) -> None:
        """Initialize TaskService.

        Args:
            config: Queue configuration
            policy: Optional policy settings
            workspace: Workspace path
            deps: Optional injected dependencies. If not provided, creates
                  default implementations internally for backward compatibility.
        """
        self.config = config
        self.policy = policy or Policy()
        self.workspace = workspace
        self._tasks: dict[str, Task] = {}
        self._ready_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=config.max_queue_size)
        self._lock = asyncio.Lock()
        self._completion_callbacks: list[Callable[[Task], None]] = []
        self._failure_callbacks: list[Callable[[Task, Exception], None]] = []
        # State machines for tasks
        self._state_machines: dict[str, TaskStateMachine] = {}
        self._phase_executors: dict[str, PhaseExecutor] = {}

        # Progress trackers - properly initialized (not lazy)
        self._progress_trackers: dict[str, ProgressTracker] = {}

        # Evidence collectors by task_id
        self._evidence_collectors: dict[str, EvidenceCollector] = {}

        # Dependencies - use injected or create defaults
        if deps is not None:
            self._deps = deps
        else:
            # Backward-compatible: create default dependencies
            self._deps = TaskServiceDeps.create(workspace)

        # Queue backpressure config
        self._put_timeout = 5.0  # 最多等待5秒

    @property
    def _audit_service(self) -> _AuditServiceProvider:
        return self._deps.audit_service

    @property
    def _repair_service(self) -> _RepairServiceProvider | None:
        return self._deps.repair_service

    @property
    def _impact_analyzer(self) -> _ImpactAnalyzerProvider:
        return self._deps.impact_analyzer

    @property
    def _evidence_store(self) -> _EvidenceStoreProvider:
        return self._deps.evidence_store

    @property
    def _state_store(self) -> _StateStoreProvider:
        return self._deps.state_store

    @property
    def _log_store(self) -> _LogStoreProvider:
        return self._deps.log_store

    @property
    def _storage(self) -> StorageLayout:
        return self._deps.storage

    async def _enqueue_task(self, task_id: str) -> bool:
        """入队任务，带超时和背压处理"""
        try:
            await asyncio.wait_for(
                self._ready_queue.put(task_id),
                timeout=self._put_timeout,
            )
            return True
        except asyncio.TimeoutError:
            # Queue is full, task stays in READY but not enqueued
            return False

    async def create_task(
        self,
        subject: str,
        description: str = "",
        command: str | None = None,
        priority: TaskPriority = TaskPriority.MEDIUM,
        blocked_by: list[int | str] | None = None,
        timeout_seconds: int | None = None,
        metadata: dict | None = None,
    ) -> Task:
        """Create a new task.

        Args:
            subject: Brief task description
            description: Detailed description
            command: Optional command to execute
            priority: Task priority
            blocked_by: List of task IDs this task depends on
            timeout_seconds: Execution timeout
            metadata: Additional task metadata

        Returns:
            Created task
        """
        async with self._lock:
            task_id = f"task-{len(self._tasks) + 1}"
            task = Task(
                id=task_id,
                subject=subject,
                description=description,
                command=command,
                priority=priority,
                blocked_by=blocked_by or [],
                timeout_seconds=timeout_seconds or self.config.default_timeout_seconds,
                metadata=metadata or {},
            )

            self._tasks[task_id] = task

            # Check if ready immediately
            if self._is_task_ready(task):
                task.mark_ready()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        self._ready_queue.put(task_id),
                        timeout=self._put_timeout,
                    )  # Queue full — task stays READY in memory

            return task

    async def get_task(self, task_id: str) -> Task | None:
        """Get a task by ID."""
        async with self._lock:
            return self._tasks.get(task_id)

    async def get_tasks(
        self,
        status: TaskStatus | None = None,
        priority: TaskPriority | None = None,
    ) -> list[Task]:
        """Get tasks with optional filtering."""
        async with self._lock:
            tasks = list(self._tasks.values())
            if status:
                tasks = [t for t in tasks if t.status == status]
            if priority:
                tasks = [t for t in tasks if t.priority == priority]
            return tasks

    async def cancel_task(self, task_id: str, reason: str = "") -> bool:
        """Cancel a pending or ready task.

        Returns True if cancelled, False if task was already running/completed.
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False

            if task.status in (TaskStatus.PENDING, TaskStatus.READY):
                task.cancel()
                return True
            return False

    async def on_task_claimed(self, task_id: str, worker_id: str) -> bool:
        """Mark task as claimed by a worker."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False

            try:
                task.claim(worker_id)
                return True
            except TaskStateError:
                return False

    async def on_task_started(self, task_id: str) -> bool:
        """Mark task as started."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False

            try:
                task.start()
                return True
            except TaskStateError:
                return False

    async def on_task_completed(
        self,
        task_id: str,
        result: TaskResult,
        evidence: TaskEvidence | None = None,
    ) -> list[str]:
        """Handle task completion.

        Returns list of newly unblocked task IDs.
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return []

            if evidence is not None:
                result = TaskResult(
                    success=result.success,
                    output=result.output,
                    exit_code=result.exit_code,
                    duration_ms=result.duration_ms,
                    evidence=(evidence, *result.evidence),
                    error=result.error,
                )
            task.complete(result)
            # Task entity auto-retry transitions failed tasks back to READY.
            # Re-enqueue immediately, otherwise task stays READY forever and stalls.
            if task.status == TaskStatus.READY:
                await self._enqueue_task(task_id)
                return [task_id]

            # Notify callbacks
            for callback in self._completion_callbacks:
                try:
                    callback(task)
                except (RuntimeError, ValueError) as exc:
                    logger.debug("Completion callback failed for task %s: %s", task.id, exc)

            # Check for newly unblocked tasks
            unblocked: list[str] = []
            for dependent_id in task.blocks:
                dependent_id_str = str(dependent_id)
                dependent = self._tasks.get(dependent_id_str)
                if dependent and self._is_task_ready(dependent):
                    dependent.mark_ready()
                    await self._enqueue_task(dependent_id_str)
                    unblocked.append(dependent_id_str)

            return unblocked

    async def on_task_failed(
        self,
        task_id: str,
        error: str,
        recoverable: bool = False,
    ) -> None:
        """Handle task failure."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return

            if task.status in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                return

            if task.status == TaskStatus.IN_PROGRESS:
                result = TaskResult(
                    success=False,
                    output="",
                    error=error,
                    duration_ms=0,
                )
                task.complete(result)

                # Task entity may auto-retry by moving FAILED -> READY.
                # Re-enqueue immediately, otherwise task can stay READY forever
                # with an empty queue and stall Director progression.
                if task.status == TaskStatus.READY:
                    await self._enqueue_task(task_id)
                    return
            elif recoverable and task.retry_count < task.max_retries:
                # Recoverable failure before entering IN_PROGRESS (rare path):
                # normalize back to READY and retry.
                task.status = TaskStatus.READY
                task.retry_count += 1
                task.claimed_by = None
                task.claimed_at = None
                task.started_at = None
                task.completed_at = None
                task._result = None
                await self._enqueue_task(task_id)
                return
            else:
                # Non-recoverable pre-execution failures (e.g. deadlocked PENDING)
                # must fail permanently instead of calling task.complete(),
                # which only accepts IN_PROGRESS state.
                task.status = TaskStatus.FAILED
                task.error_message = error
                task.completed_at = datetime.now(timezone.utc).timestamp()

            # Notify callbacks
            exc = Exception(error)
            for callback in self._failure_callbacks:
                try:
                    callback(task, exc)
                except (RuntimeError, ValueError) as cb_exc:
                    logger.debug(
                        "Failure callback failed for task %s: %s",
                        task.id,
                        cb_exc,
                    )

    async def get_next_ready_task(
        self,
        timeout: float | None = None,
    ) -> str | None:
        """Get the next ready task ID from the queue.

        Args:
            timeout: How long to wait for a task (None = no timeout)

        Returns:
            Task ID or None if timeout
        """
        # Use get_nowait for non-blocking check
        if timeout == 0:
            try:
                return self._ready_queue.get_nowait()
            except asyncio.QueueEmpty:
                return None

        try:
            return await asyncio.wait_for(
                self._ready_queue.get(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return None

    async def get_ready_task_count(self) -> int:
        """Get number of tasks currently ready to execute."""
        return self._ready_queue.qsize()

    async def get_dependency_graph(self, task_id: str) -> dict[str, Any] | None:
        """Get dependency graph for a task."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None

            def get_task_info(tid: str) -> dict:
                t = self._tasks.get(tid)
                if not t:
                    return {"id": tid, "status": "UNKNOWN"}
                return {
                    "id": t.id,
                    "subject": t.subject,
                    "status": t.status.name,
                    "priority": t.priority.name,
                }

            return {
                "task": get_task_info(task_id),
                "depends_on": [get_task_info(str(tid)) for tid in task.blocked_by],
                "blocks": [get_task_info(str(tid)) for tid in task.blocks],
            }

    async def add_dependency(self, task_id: str, depends_on_id: str) -> bool:
        """Add a dependency between tasks.

        Args:
            task_id: The task that depends on another
            depends_on_id: The task that must complete first

        Returns:
            True if dependency was added
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            dependency = self._tasks.get(depends_on_id)

            if not task or not dependency:
                return False

            # Check for circular dependency
            if task_id in dependency.blocked_by:
                return False

            if depends_on_id not in task.blocked_by:
                task.blocked_by.append(depends_on_id)
                dependency.blocks.append(task_id)

            # Update task status if needed
            if task.status == TaskStatus.READY and not self._is_task_ready(task):
                task.status = TaskStatus.PENDING

            return True

    def on_task_complete(self, callback: Callable[[Task], None]) -> None:
        """Register a callback for task completion."""
        self._completion_callbacks.append(callback)

    def on_task_fail(self, callback: Callable[[Task, Exception], None]) -> None:
        """Register a callback for task failure."""
        self._failure_callbacks.append(callback)

    def _is_task_ready(self, task: Task) -> bool:
        """Check if all dependencies are satisfied."""
        if not self.config.enable_dependency_tracking:
            return True

        for dep_id in task.blocked_by:
            dep = self._tasks.get(str(dep_id))
            if not dep or dep.status != TaskStatus.COMPLETED:
                return False
        return True

    async def get_statistics(self) -> dict[str, Any]:
        """Get task statistics."""
        async with self._lock:
            stats: dict[str, Any] = {
                "total": len(self._tasks),
                "by_status": {},
                "by_priority": {},
            }

            for status in TaskStatus:
                count = len([t for t in self._tasks.values() if t.status == status])
                stats["by_status"][status.name] = count

            for priority in TaskPriority:
                count = len([t for t in self._tasks.values() if t.priority == priority])
                stats["by_priority"][priority.name] = count

            return stats

    # ==============================================================================
    # 4-Phase State Machine Integration
    # ==============================================================================

    async def initialize_state_machine(
        self,
        task_id: str,
        initial_context: PhaseContext | None = None,
    ) -> TaskStateMachine | None:
        """Initialize state machine for a task."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None

            # Create state machine
            sm = TaskStateMachine(
                task_id=task_id,
                initial_context=initial_context
                or PhaseContext(
                    task_id=task_id,
                    workspace=self.workspace,
                    metadata=task.metadata,
                ),
            )
            self._state_machines[task_id] = sm

            # Create phase executor
            executor = PhaseExecutor(
                workspace=self.workspace,
                policy=self.policy,
                snapshot_enabled=True,
            )
            self._phase_executors[task_id] = executor

            return sm

    async def execute_task_phase(
        self,
        task_id: str,
        planning_fn: Callable[[PhaseContext], PhaseResult] | None = None,
        execution_fn: Callable[[PhaseContext], PhaseResult] | None = None,
    ) -> PhaseResult:
        """Execute current phase of task state machine.

        This is the core method for 4-phase workflow execution:
        1. PLANNING -> 2. VALIDATION -> 3. EXECUTION -> 4. VERIFICATION

        Args:
            task_id: Task ID
            planning_fn: Custom planning logic
            execution_fn: Custom execution logic

        Returns:
            Phase execution result
        """
        sm = self._state_machines.get(task_id)
        executor = self._phase_executors.get(task_id)

        if not sm or not executor:
            return PhaseResult(
                success=False,
                phase=TaskPhase.FAILED,
                message="State machine not initialized",
                error_code="NOT_INITIALIZED",
            )

        # Initialize if needed
        if sm.current_phase == TaskPhase.PENDING:
            sm.transition_to(TaskPhase.PLANNING, "Starting task execution")

        current = sm.current_phase

        # Execute current phase
        result = executor.execute_phase(
            phase=current,
            context=sm.context,
            planning_fn=planning_fn,
            execution_fn=execution_fn,
        )

        # Record result
        sm.record_phase_result(result)

        # Update task status based on phase
        await self._update_task_from_phase(task_id, current, result)

        return result

    async def _update_task_from_phase(
        self,
        task_id: str,
        phase: TaskPhase,
        result: PhaseResult,
    ) -> None:
        """Update task status based on phase result."""
        task = self._tasks.get(task_id)
        if not task:
            return

        if phase == TaskPhase.PLANNING:
            if result.success:
                task.status = TaskStatus.IN_PROGRESS
            else:
                task.status = TaskStatus.FAILED

        elif phase == TaskPhase.EXECUTION:
            if not result.success:
                if result.should_rollback:
                    # Attempt rollback
                    executor = self._phase_executors.get(task_id)
                    if executor:
                        executor.rollback(self._state_machines[task_id].context)
                task.status = TaskStatus.FAILED

        elif phase == TaskPhase.VERIFICATION:
            if result.success:
                task_result = TaskResult(
                    success=True,
                    output="Task completed successfully",
                    error="",
                    duration_ms=0,
                )
                task.complete(task_result)
            elif not result.can_retry:
                task.status = TaskStatus.FAILED

        elif phase == TaskPhase.VALIDATION:
            if not result.success:
                task.status = TaskStatus.FAILED
                task.error_message = result.message
            else:
                task.status = TaskStatus.IN_PROGRESS

    async def get_task_state_machine(self, task_id: str) -> TaskStateMachine | None:
        """Get state machine for a task."""
        return self._state_machines.get(task_id)

    async def get_task_trajectory(self, task_id: str) -> list[dict[str, Any]]:
        """Get execution trajectory for a task."""
        sm = self._state_machines.get(task_id)
        if not sm:
            return []
        return sm.get_trajectory()

    async def should_retry_task(self, task_id: str) -> bool:
        """Check if task should be retried based on verification."""
        sm = self._state_machines.get(task_id)
        if not sm:
            return False
        return sm.should_retry()

    # ==============================================================================
    # Anti-Hallucination Verification
    # ==============================================================================

    async def verify_existence(
        self,
        task_id: str,
        target_files: list[str],
    ) -> tuple[bool, str]:
        """Verify target files exist (Existence Gate).

        Zero-cost check to prevent AI from claiming "modified" when
        file doesn't exist.
        """
        result = ExistenceGate.check(target_files, self.workspace)

        if result.all_missing and len(target_files) > 0:
            return False, f"All target files missing: {result.missing}"

        return True, f"Mode: {result.mode}, existing: {len(result.existing)}, missing: {len(result.missing)}"

    async def soft_verify(
        self,
        task_id: str,
        target_files: list[str],
        changed_files: list[str] | None = None,
    ) -> SoftCheckResult:
        """Run soft check verification.

        Detects:
        - Missing target files (AI claims to have created them)
        - Unresolved imports (AI generates broken dependencies)
        """
        checker = SoftCheck(self.workspace)
        return checker.check(target_files, changed_files)

    async def validate_write_scope(
        self,
        task_id: str,
        changed_files: list[str],
        allowed_scope: list[str],
    ) -> tuple[bool, str]:
        """Validate write scope (Write Gate).

        Prevents AI from modifying files outside declared scope.
        """
        result = WriteGate.validate(
            changed_files=changed_files,
            act_files=allowed_scope,
            pm_target_files=allowed_scope,
        )
        return result.allowed, result.reason

    async def check_progress(
        self,
        task_id: str,
        files_created: int,
        missing_targets: list[str],
        errors: list[str],
        unresolved_imports: list[str] | None = None,
    ) -> ProgressDelta:
        """Check progress to detect stalled execution."""
        tracker_key = f"progress_{task_id}"

        if tracker_key not in self._progress_trackers:
            self._progress_trackers[tracker_key] = ProgressTracker(
                stall_threshold=self.policy.build_loop.stall_round_threshold
            )

        tracker = self._progress_trackers[tracker_key]
        return tracker.update(
            files_created=files_created,
            missing_targets=missing_targets,
            errors=errors,
            unresolved_imports=unresolved_imports or [],
        )

    # ==============================================================================
    # Impact Analysis
    # ==============================================================================

    async def analyze_impact(
        self,
        task_id: str,
        changed_files: list[str],
        file_contents: dict[str, str] | None = None,
    ) -> ImpactResult:
        """Analyze impact of file changes.

        Args:
            task_id: Task ID
            changed_files: List of changed file paths
            file_contents: Optional map of file path to content

        Returns:
            ImpactResult with risk assessment
        """
        return self._impact_analyzer.analyze(changed_files, file_contents)

    async def get_impact_recommendations(
        self,
        task_id: str,
        changed_files: list[str],
    ) -> list[str]:
        """Get verification recommendations based on impact."""
        result = self._impact_analyzer.analyze(changed_files)
        return result.recommendations

    # ==============================================================================
    # Evidence Collection
    # ==============================================================================

    async def create_evidence_collector(
        self,
        task_id: str,
        iteration: int = 0,
    ) -> EvidenceCollector:
        """Create evidence collector for a task."""
        collector = create_evidence_collector(task_id, iteration)
        self._evidence_collectors[task_id] = collector
        return collector

    async def get_evidence_package(
        self,
        task_id: str,
    ) -> EvidencePackage | None:
        """Get evidence package for a task."""
        collector = self._evidence_collectors.get(task_id)
        if collector:
            return collector.get_package()
        return None

    async def record_file_change(
        self,
        task_id: str,
        path: str,
        change_type: str,
        size_before: int | None = None,
        size_after: int | None = None,
        content_before: str | None = None,
        content_after: str | None = None,
    ) -> bool:
        """Record a file change in evidence."""
        collector = self._evidence_collectors.get(task_id)
        if collector:
            collector.record_file_change(
                path=path,
                change_type=change_type,
                size_before=size_before,
                size_after=size_after,
                content_before=content_before,
                content_after=content_after,
            )
            return True
        return False

    # ==============================================================================
    # File Persistence (Evidence, State, Logs)
    # ==============================================================================

    async def save_evidence_to_file(
        self,
        task_id: str,
        run_id: str = "",
        stage: str = "execution",
    ) -> dict[str, Any]:
        """Save evidence package to file for audit trail."""
        collector = self._evidence_collectors.get(task_id)
        if not collector:
            raise ValueError(f"No evidence collector for task {task_id}")

        package = collector.get_package()
        result = self._evidence_store.save_evidence(package.to_dict(), run_id=run_id, stage=stage)

        # Also log the save
        self._log_store.write_task_log(
            task_id=task_id,
            message=f"Evidence saved to {result['evidence_path']}",
            source="persistence",
        )

        return result

    async def load_evidence_from_file(
        self,
        task_id: str,
        iteration: int = 0,
    ) -> dict[str, Any]:
        """Load evidence package from file."""
        return self._evidence_store.load_evidence(task_id, iteration)

    async def save_state_to_file(
        self,
        task_id: str,
        run_id: str = "",
        phase: str = "",
        status: str = "",
    ) -> dict[str, Any]:
        """Save state machine state to file."""
        sm = self._state_machines.get(task_id)
        if not sm:
            raise ValueError(f"No state machine for task {task_id}")

        result = self._state_store.save_state(
            sm.to_dict(),
            run_id=run_id,
            phase=phase,
            status=status,
        )

        return result

    async def load_state_from_file(
        self,
        task_id: str,
    ) -> dict[str, Any]:
        """Load state machine state from file."""
        return self._state_store.load_state(task_id)

    async def get_task_lifecycle(
        self,
        task_id: str,
    ) -> dict[str, Any]:
        """Get task lifecycle."""
        return self._state_store.load_lifecycle(task_id)

    async def get_task_trajectory_from_store(
        self,
        task_id: str,
    ) -> list[dict[str, Any]]:
        """Get task execution trajectory from state store."""
        sm = self._state_machines.get(task_id)
        if sm:
            return sm.get_trajectory()
        return self._state_store.load_trajectory(task_id)

    async def write_task_log(
        self,
        task_id: str,
        message: str,
        level: str = "INFO",
        source: str = "",
    ) -> None:
        """Write to task log file."""
        self._log_store.write_task_log(task_id, message, level, source)

    async def export_evidence_for_role(
        self,
        task_id: str,
        role: str,
    ) -> str:
        """Export evidence package for another role agent."""
        return self._evidence_store.export_for_role_agent(task_id, role)

    # ==============================================================================
    # Independent Audit (Chancellery)
    # ==============================================================================

    async def run_independent_audit(
        self,
        task_id: str,
        audit_context: AuditContext,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        """Run independent audit for a task."""
        verdict = await self._audit_service.run_audit(audit_context)
        return verdict.to_dict()

    def set_audit_llm_caller(
        self,
        llm_caller: Any,
    ) -> None:
        """Set LLM caller for audit service."""
        self._audit_service._llm_caller = llm_caller  # type: ignore[attr-defined]

    async def get_audit_stats(self) -> dict[str, Any]:
        """Get audit statistics."""
        return self._audit_service.get_stats()

    # ==============================================================================
    # Repair Loop
    # ==============================================================================

    async def should_attempt_repair(
        self,
        task_id: str,
        audit_accepted: bool,
        soft_check: SoftCheckResult,
        progress: ProgressDelta,
        repair_context: Any,
    ) -> tuple[bool, str]:
        """Determine if repair should be attempted.

        Args:
            task_id: Task ID
            audit_accepted: Whether audit passed
            soft_check: Current soft check result
            progress: Progress delta from previous iteration
            repair_context: Repair context (RepairContext from Phase 4)

        Returns:
            Tuple of (should_repair, reason)
        """
        if self._repair_service is None:
            return False, "RepairService not available (Phase 4 pending)"
        return self._repair_service.should_attempt_repair(
            audit_accepted,
            soft_check,
            progress,
            repair_context,
        )

    def set_repair_executor(
        self,
        executor: Any,
    ) -> None:
        """Set repair executor function."""
        if self._repair_service is not None:
            self._repair_service._repair_executor = executor  # type: ignore[attr-defined]

    async def run_repair_loop(
        self,
        task_id: str,
        qa_feedback: str,
        repair_context: Any,
        max_repair_rounds: int = 2,
    ) -> tuple[bool, list[dict[str, Any]], str]:
        """Run repair loop until success or exhaustion.

        Args:
            task_id: Task ID
            qa_feedback: Initial QA feedback
            repair_context: Repair context (RepairContext from Phase 4)
            max_repair_rounds: Maximum repair attempts

        Returns:
            Tuple of (final_success, all_results, final_message)
        """
        if self._repair_service is None:
            return False, [], "RepairService not available (Phase 4 pending)"

        # Get evidence collector if exists
        evidence_collector = self._evidence_collectors.get(task_id)

        success, results, message = await self._repair_service.run_repair_loop(
            qa_feedback=qa_feedback,
            context=repair_context,
            max_repair_rounds=max_repair_rounds,
            evidence_collector=evidence_collector,
        )

        # Convert results to dicts
        results_dict = [r.to_dict() for r in results]

        return success, results_dict, message

    # ==============================================================================
    # Integrated 4-Phase with Full Verification
    # ==============================================================================

    async def execute_phase_with_verification(
        self,
        task_id: str,
        planning_fn: Any | None = None,
        execution_fn: Any | None = None,
    ) -> PhaseResult:
        """Execute phase with full verification pipeline.

        This is the comprehensive method that integrates:
        - 4-phase state machine
        - Impact analysis
        - Evidence collection
        - Soft check
        - Independent audit
        - Repair loop

        Args:
            task_id: Task ID
            planning_fn: Custom planning logic
            execution_fn: Custom execution logic

        Returns:
            Phase execution result
        """
        # Get or create evidence collector
        evidence_collector = self._evidence_collectors.get(task_id)
        if not evidence_collector:
            evidence_collector = await self.create_evidence_collector(task_id)

        # Execute the phase
        result = await self.execute_task_phase(task_id, planning_fn, execution_fn)

        # Record phase execution in evidence
        phase_name = str(result.phase.value) if hasattr(result.phase, "value") else str(result.phase)
        evidence_collector.record_audit_entry(
            {
                "type": "phase_execution",
                "phase": phase_name,
                "success": result.success,
                "message": result.message,
            }
        )

        # Save state to file for recovery and audit
        await self.save_state_to_file(
            task_id=task_id,
            phase=phase_name,
            status="success" if result.success else "failed",
        )

        # Save evidence to file for cross-agent access
        if evidence_collector.is_complete() or result.success:
            with contextlib.suppress(ValueError):
                await self.save_evidence_to_file(
                    task_id=task_id,
                    stage=phase_name.lower(),
                )  # No evidence to save yet

        # Log phase completion
        await self.write_task_log(
            task_id=task_id,
            message=f"Phase {phase_name} completed: {result.message}",
            level="INFO" if result.success else "WARNING",
            source="phase_executor",
        )

        # If in verification phase, run independent audit
        sm = self._state_machines.get(task_id)
        if sm and sm.current_phase == TaskPhase.VERIFICATION:
            # Get task info
            task = self._tasks.get(task_id)
            if task:
                # Import AuditContext at runtime to avoid TYPE_CHECKING issues
                from polaris.cells.audit.verdict.internal.independent_audit_service import (
                    AuditContext,
                )

                # Build audit context
                audit_context = AuditContext(
                    task_id=task_id,
                    plan_text=task.description,
                    changed_files=result.artifacts.get("changed_files", []) if result.artifacts else [],
                )

                # Run audit
                verdict = await self.run_independent_audit(task_id, audit_context)

                # Record audit in result
                if result.artifacts is None:
                    result.artifacts = {}
                result.artifacts["audit_verdict"] = verdict

        return result


# Backward-compatible alias
TaskLifecycleService = TaskService
