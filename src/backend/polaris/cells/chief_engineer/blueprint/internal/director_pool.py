"""DirectorPool: CE-managed pool of Director workers.

Provides task assignment, global file conflict detection, real-time
status dashboard, and failure recovery for multi-Director workflows.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from polaris.cells.chief_engineer.blueprint.internal.event_bus import EventBus
from polaris.kernelone.telemetry.metrics import Counter, Gauge, Histogram

logger = logging.getLogger(__name__)

# Prometheus-compatible metrics (using KernelOne telemetry primitives)
_METRIC_ACTIVE_TASKS = Gauge("director_pool_active_tasks_total", "Number of active Director tasks")
_METRIC_CONFLICT_EVENTS = Counter("director_pool_conflict_events_total", "Total file conflict events detected")
_METRIC_REASSIGN_EVENTS = Counter("director_pool_reassign_events_total", "Total task reassignments")
_METRIC_DEGRADE_EVENTS = Counter("director_pool_degrade_events_total", "Total degrade events to single-Director mode")
_METRIC_PHASE_DURATION = Histogram(
    "director_phase_duration_seconds",
    "Director phase duration in seconds",
    buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0),
)


class DirectorPoolConflictError(Exception):
    """Raised when a task cannot be assigned without file conflicts."""

    def __init__(self, task_id: str, conflicts: list[str]) -> None:
        super().__init__(f"Task {task_id} conflicts on files: {conflicts}")
        self.task_id = task_id
        self.conflicts = conflicts


class DirectorPhase(str, Enum):
    """Lifecycle phases of a single Director task execution.

    Extended with two-phase review from Superpowers integration (Phase 2).
    Separation of concerns: execution (SubagentSpawner) vs orchestration (DirectorPool).
    """

    IDLE = "idle"
    PREPARE = "prepare"
    VALIDATE = "validate"
    IMPLEMENT = "implement"
    # Phase 2 Extension: Two-phase review states (Superpowers essence)
    SPEC_REVIEW = "spec_review"  # Spec compliance review
    QUALITY_REVIEW = "quality_review"  # Code quality review
    VERIFY = "verify"
    REPORT = "report"


@dataclass
class DirectorStatus:
    """Real-time snapshot of one Director's state.

    Extended with review metrics from Superpowers integration (Phase 2).
    """

    director_id: str
    phase: DirectorPhase
    current_task_id: str | None = None
    active_files: list[str] = field(default_factory=list)
    started_at_ms: int | None = None
    progress_pct: float = 0.0
    last_heartbeat_ms: int = 0
    capabilities: list[str] = field(default_factory=list)
    # Phase 2 Extension: Two-phase review metrics (Superpowers essence)
    review_status: str | None = None  # "pending" | "in_spec_review" | "in_quality_review" | "approved"
    spec_compliance_score: float = 0.0  # 0.0-1.0, spec compliance rating
    code_quality_score: float = 0.0  # 0.0-1.0, code quality rating
    review_iterations: int = 0  # Number of review-fix cycles


@dataclass
class DirectorPoolStatus:
    """Aggregate snapshot of the entire Director pool."""

    directors: dict[str, DirectorStatus] = field(default_factory=dict)
    global_conflicts: list[dict[str, Any]] = field(default_factory=list)
    pending_assignments: list[str] = field(default_factory=list)
    estimated_completion_ms: int | None = None


@dataclass
class RecoveryDecision:
    """Recovery action chosen after a Director failure."""

    action: str  # "retry" | "reassign" | "split" | "abort"
    target_director_id: str | None = None
    max_retries: int = 1
    reason: str = ""


class ScopeConflictDetector:
    """Pool-level global file conflict detector."""

    def __init__(self) -> None:
        self._active_files: dict[str, str] = {}  # file_path -> director_id
        self._lock = threading.Lock()

    def detect(self, director_id: str, files: list[str]) -> list[str]:
        """Return files that conflict with another active Director."""
        with self._lock:
            conflicts: list[str] = []
            for f in files:
                owner = self._active_files.get(f)
                if owner and owner != director_id:
                    conflicts.append(f)
            return conflicts

    def acquire(self, director_id: str, files: list[str]) -> None:
        """Register file ownership for a Director."""
        with self._lock:
            for f in files:
                self._active_files[f] = director_id

    def release(self, director_id: str) -> None:
        """Release all file ownerships held by a Director."""
        with self._lock:
            to_remove = [f for f, d in self._active_files.items() if d == director_id]
            for f in to_remove:
                del self._active_files[f]

    def active_snapshot(self) -> dict[str, str]:
        """Return a copy of active files for read-only access."""
        with self._lock:
            return dict(self._active_files)


class DirectorPool:
    """Chief Engineer's directly-managed Director instance pool.

    Responsibilities:
    1. Manage lifecycle of multiple Directors.
    2. Global file conflict detection at assignment time.
    3. Intelligent task assignment (idle first, conflict-free, load-balanced).
    4. Real-time status dashboard.
    5. Failure recovery (retry / reassign / split / abort).
    """

    def __init__(
        self,
        workspace: str,
        max_directors: int = 3,
        auto_scale: bool = False,
    ) -> None:
        self._workspace = workspace
        self._max_directors = max(1, int(max_directors))
        self._auto_scale = bool(auto_scale)
        self._directors: dict[str, DirectorStatus] = {}
        self._task_assignments: dict[str, str] = {}  # task_id -> director_id
        self._task_files_map: dict[str, list[str]] = {}  # task_id -> files
        self._conflict_detector = ScopeConflictDetector()
        self._event_bus = EventBus()
        self._lock = asyncio.Lock()
        self._director_phase_start_ms: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize_directors(self) -> None:
        """Initialize the Director pool. If all initializations fail,
        gracefully degrade to a single fallback Director.
        """
        initialized = 0
        for i in range(self._max_directors):
            did = f"director-{i + 1}"
            try:
                self._directors[did] = DirectorStatus(
                    director_id=did,
                    phase=DirectorPhase.IDLE,
                    last_heartbeat_ms=_now_epoch_ms(),
                )
                initialized += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to initialize %s: %s", did, exc)

        if initialized == 0:
            _METRIC_DEGRADE_EVENTS.inc()
            logger.warning(
                "DirectorPool degraded to single-Director sequential mode because no Directors could be initialized."
            )
            self._max_directors = 1
            self._directors["director-fallback"] = DirectorStatus(
                director_id="director-fallback",
                phase=DirectorPhase.IDLE,
                last_heartbeat_ms=_now_epoch_ms(),
            )

    # ------------------------------------------------------------------
    # Task assignment
    # ------------------------------------------------------------------

    async def assign_task(
        self,
        task: Any,
        blueprint: Any,
    ) -> str:
        """Select the best Director and assign the task.

        Selection policy:
        1. Idle and conflict-free Director preferred.
        2. Any idle Director second.
        3. Lowest progress percentage as fallback.

        If the chosen Director has file conflicts, we attempt to find an
        alternative idle Director with no conflicts. If none exists, the
        task is queued (the caller should retry later).
        """
        task_id = _task_id(task)
        task_files = _task_files(task)

        async with self._lock:
            if not self._directors:
                self.initialize_directors()

            director_id = self._select_best_director(task_files)

            # Conflict resolution: try to find a conflict-free alternative
            conflicts = self._conflict_detector.detect(director_id, task_files)
            if conflicts:
                _METRIC_CONFLICT_EVENTS.inc()
                alt_id = self._find_conflict_free_director(task_files)
                if alt_id:
                    director_id = alt_id
                else:
                    raise DirectorPoolConflictError(task_id, conflicts)

            # Register assignment and file locks
            self._task_assignments[task_id] = director_id
            self._task_files_map[task_id] = list(task_files)
            self._conflict_detector.acquire(director_id, task_files)

            # Update Director status
            now = _now_epoch_ms()
            self._director_phase_start_ms[director_id] = now
            old_status = self._directors.get(director_id)
            capabilities = old_status.capabilities if old_status else []
            self._directors[director_id] = DirectorStatus(
                director_id=director_id,
                phase=DirectorPhase.PREPARE,
                current_task_id=task_id,
                active_files=list(task_files),
                started_at_ms=now,
                progress_pct=0.0,
                last_heartbeat_ms=now,
                capabilities=list(capabilities),
            )

        self._event_bus.publish(
            "director.assigned",
            {
                "task_id": task_id,
                "director_id": director_id,
                "progress": 0.0,
            },
        )
        try:
            await self._submit_director_task_workflow(task, blueprint)
        except Exception:
            await self._release_assignment(task_id, director_id)
            raise
        return director_id

    # ------------------------------------------------------------------
    # Status queries
    # ------------------------------------------------------------------

    def get_live_dashboard(self) -> DirectorPoolStatus:
        """Return a real-time snapshot of the pool."""
        active_count = sum(1 for s in self._directors.values() if s.phase != DirectorPhase.IDLE)
        _METRIC_ACTIVE_TASKS.set(active_count)
        return DirectorPoolStatus(
            directors=dict(self._directors),
            global_conflicts=self._current_global_conflicts(),
            pending_assignments=list(self._task_assignments.keys()),
            estimated_completion_ms=self._estimate_remaining_time(),
        )

    def get_director_for_task(self, task_id: str) -> str | None:
        """Return the Director ID currently assigned to a task."""
        return self._task_assignments.get(task_id)

    # ------------------------------------------------------------------
    # Failure recovery
    # ------------------------------------------------------------------

    def handle_failure(
        self,
        task_id: str,
        error: Exception,
    ) -> RecoveryDecision:
        """Decide how to recover when a Director fails a task."""
        director_id = self._task_assignments.get(task_id)
        if not director_id:
            return RecoveryDecision(action="abort", reason="unknown task")

        self._conflict_detector.release(director_id)
        self._task_files_map.pop(task_id, None)
        self._task_assignments.pop(task_id, None)
        self._director_phase_start_ms.pop(director_id, None)

        # Reset the failed Director to IDLE so it can be reused
        status = self._directors.get(director_id)
        if status is not None:
            self._directors[director_id] = DirectorStatus(
                director_id=director_id,
                phase=DirectorPhase.IDLE,
                current_task_id=None,
                active_files=[],
                started_at_ms=None,
                progress_pct=status.progress_pct,
                last_heartbeat_ms=_now_epoch_ms(),
                capabilities=list(status.capabilities),
            )

        error_name = type(error).__name__
        if "Timeout" in error_name:
            return RecoveryDecision(
                action="reassign",
                target_director_id=self._find_idle_director(),
                reason=f"timeout on {director_id}",
            )
        if "Memory" in error_name or "OOM" in error_name:
            return RecoveryDecision(
                action="split",
                reason=f"OOM on {director_id}, split into smaller tasks",
            )
        return RecoveryDecision(
            action="retry",
            max_retries=1,
            reason=f"recoverable error: {error_name}",
        )

    async def reassign(self, task_id: str, target_director_id: str) -> None:
        """Move a task from its current Director to another."""
        _METRIC_REASSIGN_EVENTS.inc()
        task_files = list(self._task_files_map.get(task_id, []))
        async with self._lock:
            old_director = self._task_assignments.get(task_id)
            if old_director:
                self._conflict_detector.release(old_director)
                self._director_phase_start_ms.pop(old_director, None)
                old_status = self._directors.get(old_director)
                if old_status is not None:
                    self._directors[old_director] = DirectorStatus(
                        director_id=old_director,
                        phase=DirectorPhase.IDLE,
                        current_task_id=None,
                        active_files=[],
                        started_at_ms=None,
                        progress_pct=old_status.progress_pct,
                        last_heartbeat_ms=_now_epoch_ms(),
                        capabilities=list(old_status.capabilities),
                    )

            self._task_assignments[task_id] = target_director_id
            self._conflict_detector.acquire(target_director_id, task_files)

            now = _now_epoch_ms()
            self._director_phase_start_ms[target_director_id] = now
            target_status = self._directors.get(target_director_id)
            capabilities = target_status.capabilities if target_status else []
            self._directors[target_director_id] = DirectorStatus(
                director_id=target_director_id,
                phase=DirectorPhase.PREPARE,
                current_task_id=task_id,
                active_files=list(task_files),
                started_at_ms=now,
                progress_pct=0.0,
                last_heartbeat_ms=now,
                capabilities=list(capabilities),
            )

        self._event_bus.publish(
            "director.reassigned",
            {
                "task_id": task_id,
                "from_director": old_director,
                "to_director": target_director_id,
            },
        )

    # ------------------------------------------------------------------
    # Completion
    # ------------------------------------------------------------------

    async def transition_to_review(
        self,
        task_id: str,
        review_type: str,  # "spec" | "quality"
        reviewer_config: dict[str, Any] | None = None,
    ) -> str:
        """Transition a Director to review phase.

        Phase 2 Extension: Two-phase review orchestration (Superpowers essence).
        DirectorPool orchestrates the review workflow using SubagentSpawner
        with ModelSelectionStrategy.PREMIUM for high-quality review.

        Args:
            task_id: The task to review
            review_type: "spec" for spec compliance, "quality" for code quality
            reviewer_config: Optional configuration for the reviewer subagent

        Returns:
            reviewer_director_id: The Director assigned to perform review
        """
        director_id = self._task_assignments.get(task_id)
        if not director_id:
            raise ValueError(f"Task {task_id} not found in pool")

        status = self._directors.get(director_id)
        if not status:
            raise ValueError(f"Director {director_id} not found")

        # Determine review phase
        review_phase = DirectorPhase.SPEC_REVIEW if review_type == "spec" else DirectorPhase.QUALITY_REVIEW

        async with self._lock:
            now = _now_epoch_ms()
            self._director_phase_start_ms[director_id] = now
            self._directors[director_id] = DirectorStatus(
                director_id=director_id,
                phase=review_phase,
                current_task_id=task_id,
                active_files=list(status.active_files),
                started_at_ms=now,
                progress_pct=status.progress_pct,
                last_heartbeat_ms=now,
                capabilities=list(status.capabilities),
                review_status="in_review",
                spec_compliance_score=status.spec_compliance_score,
                code_quality_score=status.code_quality_score,
                review_iterations=status.review_iterations + 1,
            )

        self._event_bus.publish(
            f"director.review_{review_type}.started",
            {
                "task_id": task_id,
                "director_id": director_id,
                "review_config": reviewer_config,
            },
        )

        return director_id

    async def submit_review_result(
        self,
        task_id: str,
        review_type: str,
        score: float,
        passed: bool,
        feedback: str,
    ) -> None:
        """Submit review result and update metrics.

        Args:
            task_id: The reviewed task
            review_type: "spec" | "quality"
            score: 0.0-1.0 score
            passed: Whether review passed
            feedback: Review feedback text
        """
        director_id = self._task_assignments.get(task_id)
        if not director_id:
            return

        status = self._directors.get(director_id)
        if not status:
            return

        async with self._lock:
            now = _now_epoch_ms()
            new_status = DirectorStatus(
                director_id=director_id,
                phase=DirectorPhase.VERIFY if passed else DirectorPhase.IMPLEMENT,
                current_task_id=task_id,
                active_files=list(status.active_files),
                started_at_ms=status.started_at_ms,
                progress_pct=status.progress_pct,
                last_heartbeat_ms=now,
                capabilities=list(status.capabilities),
                review_status="approved" if passed else "needs_revision",
                spec_compliance_score=score if review_type == "spec" else status.spec_compliance_score,
                code_quality_score=score if review_type == "quality" else status.code_quality_score,
                review_iterations=status.review_iterations,
            )
            self._directors[director_id] = new_status

        self._event_bus.publish(
            f"director.review_{review_type}.completed",
            {
                "task_id": task_id,
                "director_id": director_id,
                "score": score,
                "passed": passed,
                "feedback": feedback,
            },
        )

    def mark_completed(self, task_id: str, success: bool) -> None:
        """Release a Director after task completion."""
        director_id = self._task_assignments.pop(task_id, None)
        self._task_files_map.pop(task_id, None)
        if not director_id:
            return

        self._conflict_detector.release(director_id)
        self._director_phase_start_ms.pop(director_id, None)
        status = self._directors.get(director_id)
        if status is not None:
            self._directors[director_id] = DirectorStatus(
                director_id=director_id,
                phase=DirectorPhase.IDLE,
                current_task_id=None,
                active_files=[],
                started_at_ms=None,
                progress_pct=1.0 if success else 0.0,
                last_heartbeat_ms=_now_epoch_ms(),
                capabilities=list(status.capabilities),
                review_status=None,
                spec_compliance_score=0.0,
                code_quality_score=0.0,
                review_iterations=0,
            )

        self._event_bus.publish(
            "director.completed",
            {
                "task_id": task_id,
                "director_id": director_id,
                "success": success,
            },
        )

    def update_progress(
        self,
        director_id: str,
        *,
        phase: DirectorPhase | None = None,
        progress_pct: float | None = None,
    ) -> None:
        """Update the progress of an active Director."""
        status = self._directors.get(director_id)
        if status is None:
            return
        now = _now_epoch_ms()
        new_phase = phase if phase is not None else status.phase
        new_progress = max(0.0, min(1.0, float(progress_pct))) if progress_pct is not None else status.progress_pct
        old_phase = status.phase
        if new_phase != old_phase:
            start_ms = self._director_phase_start_ms.get(director_id)
            if start_ms is not None:
                duration_sec = (now - start_ms) / 1000.0
                if duration_sec >= 0:
                    _METRIC_PHASE_DURATION.observe(duration_sec)
            self._director_phase_start_ms[director_id] = now
        self._directors[director_id] = DirectorStatus(
            director_id=status.director_id,
            phase=new_phase,
            current_task_id=status.current_task_id,
            active_files=list(status.active_files),
            started_at_ms=status.started_at_ms,
            progress_pct=new_progress,
            last_heartbeat_ms=now,
            capabilities=list(status.capabilities),
            review_status=status.review_status,
            spec_compliance_score=status.spec_compliance_score,
            code_quality_score=status.code_quality_score,
            review_iterations=status.review_iterations,
        )
        if new_phase != old_phase:
            self._event_bus.publish(
                "director.phase_changed",
                {
                    "director_id": director_id,
                    "old_phase": old_phase.value,
                    "new_phase": new_phase.value,
                },
            )

    # ------------------------------------------------------------------
    # EventBus access
    # ------------------------------------------------------------------

    def event_bus(self) -> EventBus:
        """Return the internal event bus for external subscribers."""
        return self._event_bus

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _release_assignment(self, task_id: str, director_id: str) -> None:
        """Reset a director to IDLE and release all assignment state after a workflow submission failure."""
        async with self._lock:
            self._conflict_detector.release(director_id)
            self._task_assignments.pop(task_id, None)
            self._task_files_map.pop(task_id, None)
            self._director_phase_start_ms.pop(director_id, None)
            status = self._directors.get(director_id)
            if status is not None:
                self._directors[director_id] = DirectorStatus(
                    director_id=director_id,
                    phase=DirectorPhase.IDLE,
                    current_task_id=None,
                    active_files=[],
                    started_at_ms=None,
                    progress_pct=status.progress_pct,
                    last_heartbeat_ms=_now_epoch_ms(),
                    capabilities=list(status.capabilities),
                )
        self._event_bus.publish(
            "director.assignment_failed",
            {
                "task_id": task_id,
                "director_id": director_id,
            },
        )

    async def _submit_director_task_workflow(self, task: Any, blueprint: Any) -> None:
        """Submit DirectorTaskWorkflow via the embedded workflow runtime."""
        try:
            from polaris.cells.orchestration.workflow_runtime.public.service import (
                get_adapter,
            )
        except ImportError:
            logger.warning("Workflow runtime adapter not available; skipping workflow submission")
            return

        task_id = _task_id(task)
        run_id = _task_run_id(task) or "unknown"
        title = _task_title(task) or task_id
        goal = _task_goal(task)
        task_payload = _task_payload(task)
        blueprint_id = str(getattr(blueprint, "blueprint_id", "") or "").strip()

        workflow_payload: dict[str, Any] = {
            "workspace": self._workspace,
            "run_id": run_id,
            "task": {
                "id": task_id,
                "title": title,
                "goal": goal,
                "payload": task_payload,
            },
            "phases": ["prepare", "validate", "implement", "verify", "report"],
            "metadata": {
                "blueprint_id": blueprint_id,
                "source": "director_pool",
            },
        }

        adapter = await get_adapter()
        try:
            await adapter.start()
        except Exception:
            logger.exception("Failed to start workflow runtime adapter")

        workflow_id = f"director-task-{run_id}-{task_id}"
        try:
            result = await adapter.submit_workflow(
                workflow_name="DirectorTaskWorkflow",
                workflow_id=workflow_id,
                payload=workflow_payload,
            )
            logger.info(
                "Submitted DirectorTaskWorkflow %s for task %s: status=%s",
                workflow_id,
                task_id,
                result.status,
            )
        except Exception as exc:
            logger.exception(
                "Failed to submit DirectorTaskWorkflow for task %s: %s",
                task_id,
                exc,
            )

    def _select_best_director(self, task_files: list[str]) -> str:
        """Choose the best Director according to the assignment policy."""
        # Priority 1: idle and conflict-free
        for did, status in self._directors.items():
            if status.phase == DirectorPhase.IDLE:
                conflicts = self._conflict_detector.detect(did, task_files)
                if not conflicts:
                    return did
        # Priority 2: any idle
        for did, status in self._directors.items():
            if status.phase == DirectorPhase.IDLE:
                return did
        # Priority 3: lowest progress
        return min(
            self._directors.keys(),
            key=lambda d: self._directors[d].progress_pct,
        )

    def _find_conflict_free_director(self, files: list[str]) -> str | None:
        for did, status in self._directors.items():
            if status.phase == DirectorPhase.IDLE:
                conflicts = self._conflict_detector.detect(did, files)
                if not conflicts:
                    return did
        return None

    def _find_idle_director(self) -> str | None:
        for did, status in self._directors.items():
            if status.phase == DirectorPhase.IDLE:
                return did
        return None

    def _current_global_conflicts(self) -> list[dict[str, Any]]:
        active_files = self._conflict_detector.active_snapshot()
        conflicts: list[dict[str, Any]] = []
        seen_pairs: set[str] = set()
        seen_files: set[str] = set()
        files_by_director: dict[str, list[str]] = {}
        for f, d in active_files.items():
            files_by_director.setdefault(d, []).append(f)

        directors = list(files_by_director.keys())
        for i in range(len(directors)):
            d1 = directors[i]
            for j in range(i + 1, len(directors)):
                d2 = directors[j]
                shared_files = [f for f in files_by_director[d1] if f in files_by_director[d2]]
                if shared_files:
                    pair_key = ":".join(sorted([d1, d2]))
                    if pair_key not in seen_pairs:
                        seen_pairs.add(pair_key)
                        for f in shared_files:
                            if f not in seen_files:
                                seen_files.add(f)
                                conflicts.append({"file": f, "directors": sorted([d1, d2])})
        return conflicts

    def _estimate_remaining_time(self) -> int | None:
        active = [s for s in self._directors.values() if s.phase != DirectorPhase.IDLE and s.started_at_ms is not None]
        if not active:
            return 0
        remaining_times: list[int] = []
        for s in active:
            if s.progress_pct > 0 and s.started_at_ms is not None:
                elapsed = _now_epoch_ms() - s.started_at_ms
                estimated_total = elapsed / s.progress_pct
                remaining = max(0, int(estimated_total - elapsed))
                remaining_times.append(remaining)
        return max(remaining_times) if remaining_times else None


# ------------------------------------------------------------------
# Utility functions
# ------------------------------------------------------------------


def _now_epoch_ms() -> int:
    return int(time.time() * 1000)


def _task_id(task: Any) -> str:
    return str(getattr(task, "id", "") or getattr(task, "task_id", "") or "").strip()


def _task_files(task: Any) -> list[str]:
    raw = getattr(task, "target_files", None)
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    payload = getattr(task, "payload", None)
    if isinstance(payload, dict):
        raw = payload.get("target_files")
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
    return []


def _task_title(task: Any) -> str:
    return str(getattr(task, "title", "") or getattr(task, "name", "") or "").strip()


def _task_goal(task: Any) -> str:
    return str(getattr(task, "goal", "") or getattr(task, "description", "") or "").strip()


def _task_payload(task: Any) -> dict[str, Any]:
    payload = getattr(task, "payload", None)
    return dict(payload) if isinstance(payload, dict) else {}


def _task_run_id(task: Any) -> str:
    return str(getattr(task, "run_id", "") or "").strip()
