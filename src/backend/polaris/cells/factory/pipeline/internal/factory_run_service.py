"""Factory Run Service - formal service for unattended development with persistence."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from polaris.cells.orchestration.orchestration_engine.public.service import OrchestrationCommandService

from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.constants import DEFAULT_DIRECTOR_MAX_PARALLELISM
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.storage import resolve_logical_path
from polaris.kernelone.utils import utc_now_iso

logger = logging.getLogger(__name__)


class FactoryRunStatus(str, Enum):
    """Factory run lifecycle status."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERING = "recovering"
    CANCELLED = "cancelled"


TERMINAL_RUN_STATUSES = {
    FactoryRunStatus.COMPLETED,
    FactoryRunStatus.FAILED,
    FactoryRunStatus.CANCELLED,
}

SUPPORTED_FACTORY_STAGES = {
    "docs_generation",
    "pm_planning",
    "director_dispatch",
    "quality_gate",
}

DEFAULT_STAGE_HEARTBEAT_INTERVAL_SECONDS = 15.0


@dataclass
class FactoryConfig:
    """Factory run configuration."""

    name: str
    description: str | None = None
    stages: list[str] = field(default_factory=list)
    auto_dispatch: bool = True
    checkpoint_interval: int = 300


@dataclass
class StageResult:
    """Result of a stage execution."""

    stage: str
    status: str
    output: str | None = None
    artifacts: list[str] = field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FactoryRun:
    """A factory run with full audit trail."""

    id: str
    config: FactoryConfig
    status: FactoryRunStatus
    created_at: str
    updated_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    stages_completed: list[str] = field(default_factory=list)
    stages_failed: list[str] = field(default_factory=list)
    recovery_point: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "config": asdict(self.config),
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "stages_completed": self.stages_completed,
            "stages_failed": self.stages_failed,
            "recovery_point": self.recovery_point,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FactoryRun:
        config = FactoryConfig(**data.get("config", {}))
        return cls(
            id=data["id"],
            config=config,
            status=FactoryRunStatus(data.get("status", "pending")),
            created_at=data["created_at"],
            updated_at=data.get("updated_at"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            stages_completed=data.get("stages_completed", []),
            stages_failed=data.get("stages_failed", []),
            recovery_point=data.get("recovery_point"),
            metadata=data.get("metadata", {}),
        )


class FactoryStageExecutor(Protocol):
    """Execution adapter for concrete factory stages."""

    async def execute(self, stage: str, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        """Execute *stage* for *run* with *context*."""


class OrchestrationStageExecutor:
    """Production executor backed by OrchestrationCommandService."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self._fs = KernelFileSystem(str(workspace), get_default_adapter())

    async def execute(self, stage: str, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        handlers = {
            "docs_generation": self._execute_docs_generation,
            "pm_planning": self._execute_pm_planning,
            "director_dispatch": self._execute_director_dispatch,
            "quality_gate": self._execute_quality_gate,
        }
        handler = handlers.get(stage)
        if handler is None:
            return StageResult(stage=stage, status="skipped", output="No handler for this stage")
        return await handler(run, context)

    def _artifact_path(self, relative_path: str) -> Path:
        rel = str(relative_path or "").replace("\\", "/").strip().lstrip("/")
        # 使用逻辑路径解析：workspace/* -> runtime/workspace/*, runtime/* -> runtime/...
        resolved = resolve_logical_path(str(self.workspace), rel)
        return Path(resolved).resolve()

    def _write_json_artifact(self, relative_path: str, payload: dict[str, Any]) -> Path:
        target = self._artifact_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._fs.write_json(str(target), payload)
        return target

    def _write_text_artifact(self, relative_path: str, content: str) -> Path:
        target = self._artifact_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._fs.write_text(str(target), str(content or ""))
        return target

    def _write_stage_signal_artifact(
        self,
        *,
        stage: str,
        run_id: str,
        signals: list[dict[str, Any]],
    ) -> str:
        target_rel = f"runtime/signals/{stage}.signals.json"
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "factory_stage_executor",
            "factory_run_id": run_id,
            "stage": stage,
            "signals": signals,
        }
        self._write_json_artifact(target_rel, payload)
        return target_rel

    @staticmethod
    async def _wait_for_artifact_file(
        target: Path,
        *,
        timeout_seconds: float = 8.0,
        poll_interval: float = 0.2,
    ) -> bool:
        """等待异步阶段产物落盘，避免完成信号与文件写入存在短暂竞态。"""
        timeout = max(float(timeout_seconds or 0.0), 0.0)
        interval = max(float(poll_interval or 0.0), 0.05)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        while True:
            try:
                if target.exists() and target.is_file() and target.stat().st_size > 0:
                    return True
            except OSError:
                pass
            if timeout <= 0:
                return False
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(interval)

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
        normalized = str(text or "").strip()
        if len(normalized) < min_chars:
            return False
        heading_count = len([line for line in normalized.splitlines() if str(line or "").strip().startswith("#")])
        return heading_count >= 2

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
        if invalid > 0:
            return f"tasks_plan_invalid_contract:{invalid}"
        return ""

    def _load_pm_plan_tasks(self, relative_path: str = "tasks/plan.json") -> list[dict[str, Any]]:
        target = self._artifact_path(relative_path)
        if not target.exists():
            return []
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return []
        if not isinstance(payload, dict):
            return []
        tasks = payload.get("tasks")
        if not isinstance(tasks, list):
            return []
        return [item for item in tasks if isinstance(item, dict)]

    def _build_director_task_filter(self, tasks: list[dict[str, Any]]) -> str:
        if not tasks:
            return "Execute ready tasks from PM contract"
        lines: list[str] = []
        for task in tasks[:4]:
            title = str(task.get("title") or task.get("goal") or "").strip()
            scope = str(task.get("scope") or "").strip()
            if not title:
                continue
            if scope:
                lines.append(f"- {title} [scope: {scope}]")
            else:
                lines.append(f"- {title}")
        if not lines:
            return "Execute ready tasks from PM contract"
        return "Execute PM tasks strictly in order:\n" + "\n".join(lines)

    def _read_taskboard_stats(self) -> dict[str, int]:
        baseline = {
            "total": 0,
            "pending": 0,
            "ready": 0,
            "in_progress": 0,
            "completed": 0,
            "failed": 0,
            "blocked": 0,
        }
        try:
            payload = TaskRuntimeService(str(self.workspace)).get_stats()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return baseline
        if not isinstance(payload, dict):
            return baseline
        for key in tuple(baseline.keys()):
            try:
                baseline[key] = int(payload.get(key) or 0)
            except (TypeError, ValueError):
                baseline[key] = 0
        return baseline

    @staticmethod
    def _is_taskboard_converged(stats: dict[str, int]) -> bool:
        return (
            int(stats.get("pending") or 0) <= 0
            and int(stats.get("ready") or 0) <= 0
            and int(stats.get("in_progress") or 0) <= 0
            and int(stats.get("blocked") or 0) <= 0
        )

    @staticmethod
    def _has_director_progress(before: dict[str, int], after: dict[str, int]) -> bool:
        return any(
            int(after.get(key) or 0) != int(before.get(key) or 0)
            for key in ("pending", "ready", "in_progress", "completed", "failed", "blocked")
        )

    @staticmethod
    def _has_director_execution_evidence(
        *,
        attempts: list[dict[str, Any]],
        initial_stats: dict[str, int],
        final_stats: dict[str, int],
        converged: bool,
    ) -> bool:
        completed_delta = int(final_stats.get("completed") or 0) - int(initial_stats.get("completed") or 0)
        failed_delta = int(final_stats.get("failed") or 0) - int(initial_stats.get("failed") or 0)
        if completed_delta > 0 or failed_delta > 0:
            return True

        for attempt in attempts:
            if bool(attempt.get("progress_made")):
                return True
            metadata = attempt.get("metadata")
            if not isinstance(metadata, dict):
                continue
            counts = metadata.get("task_status_counts")
            if not isinstance(counts, dict):
                continue
            try:
                total = sum(int(value or 0) for value in counts.values())
            except (TypeError, ValueError):
                total = 0
            if total > 0:
                return True

        return bool(converged and int(final_stats.get("completed") or 0) > 0)

    @staticmethod
    def _metadata_indicates_execution(metadata: dict[str, Any]) -> bool:
        if not isinstance(metadata, dict):
            return False
        counts = metadata.get("task_status_counts")
        if not isinstance(counts, dict):
            return False
        try:
            completed = int(counts.get("completed") or 0)
            failed = int(counts.get("failed") or 0)
            blocked = int(counts.get("blocked") or 0)
            cancelled = int(counts.get("cancelled") or 0)
        except (TypeError, ValueError):
            return False
        return (completed + failed + blocked + cancelled) > 0

    async def _execute_docs_generation(self, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        logger.info("Executing docs generation for run %s", run.id)
        abort_checker = self._resolve_abort_checker(context)

        service = self._build_orchestration_service(context)
        command_result = await service.execute_pm_run(
            workspace=str(self.workspace),
            run_type="architect",
            options={
                "directive": context.get("directive", "Generate project documentation"),
                "run_director": False,
            },
        )
        final_result = await self._poll_run_completion(
            service,
            command_result,
            timeout_seconds=int(context.get("timeout", 600)),
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

        service = self._build_orchestration_service(context)
        command_result = await service.execute_pm_run(
            workspace=str(self.workspace),
            run_type="pm",
            options={
                "directive": context.get("directive", "Plan implementation tasks"),
                "run_director": False,
            },
        )
        final_result = await self._poll_run_completion(
            service,
            command_result,
            timeout_seconds=int(context.get("timeout", 600)),
            abort_checker=abort_checker,
        )
        if str(final_result.status or "").strip().lower() == "cancelled":
            return StageResult(
                stage="pm_planning",
                status="cancelled",
                output=f"PM planning cancelled: {final_result.message or 'N/A'}",
                artifacts=[],
            )

        stage_signals: list[dict[str, Any]] = []
        if final_result.status not in {"completed", "success"}:
            stage_signals.append(
                {
                    "code": "pm.run_status_non_success",
                    "severity": "error",
                    "detail": str(final_result.message or "").strip() or str(final_result.status or "unknown"),
                    "upstream_status": str(final_result.status or "").strip(),
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
        artifacts: list[str] = []
        if self._artifact_exists("tasks/plan.json", min_chars=1):
            artifacts.append("tasks/plan.json")
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

    async def _execute_director_dispatch(self, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        logger.info("Executing Director dispatch for run %s", run.id)
        abort_checker = self._resolve_abort_checker(context)

        pm_tasks = self._load_pm_plan_tasks("tasks/plan.json")
        plan_task_filter = self._build_director_task_filter(pm_tasks)
        configured_task_filter = str(context.get("task_filter") or "").strip()
        effective_task_filter = configured_task_filter or plan_task_filter

        service = self._build_orchestration_service(context)
        stage_signals: list[dict[str, Any]] = []
        initial_stats = self._read_taskboard_stats()
        attempts: list[dict[str, Any]] = []
        last_command_result: CommandResult | None = None
        final_result: CommandResult | None = None
        max_rounds = int(context.get("director_max_rounds") or 0)
        if max_rounds <= 0:
            dynamic_rounds = (
                int(initial_stats.get("pending") or 0)
                + int(initial_stats.get("ready") or 0)
                + int(initial_stats.get("in_progress") or 0)
                + 2
            )
            max_rounds = max(2, min(dynamic_rounds, 12))
        idle_budget = max(1, int(context.get("director_idle_budget") or 2))
        idle_rounds = 0
        requires_taskboard_convergence = True

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

        if not stage_signals:
            for round_index in range(1, max_rounds + 1):
                before_stats = self._read_taskboard_stats()
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
                        metadata={"task_status_counts": dict(before_stats)},
                    )
                    break

                command_result = await service.execute_director_run(
                    workspace=str(self.workspace),
                    tasks=context.get("tasks"),
                    options={
                        "task_filter": effective_task_filter,
                        "max_workers": context.get("max_workers", DEFAULT_DIRECTOR_MAX_PARALLELISM),
                        "execution_mode": context.get("execution_mode", "parallel"),
                    },
                )
                last_command_result = command_result
                polled_result = await self._poll_run_completion(
                    service,
                    command_result,
                    timeout_seconds=int(context.get("timeout", 600)),
                    abort_checker=abort_checker,
                )
                final_result = polled_result
                if str(polled_result.status or "").strip().lower() == "cancelled":
                    break

                after_stats = self._read_taskboard_stats()
                metadata_payload = polled_result.metadata if isinstance(polled_result.metadata, dict) else {}
                metadata_progress = self._metadata_indicates_execution(metadata_payload)
                if metadata_progress:
                    requires_taskboard_convergence = False
                progress_made = self._has_director_progress(before_stats, after_stats) or metadata_progress
                attempt_entry = {
                    "round": round_index,
                    "run_id": str(command_result.run_id or "").strip(),
                    "status": str(polled_result.status or "").strip(),
                    "message": str(polled_result.message or "").strip(),
                    "metadata": metadata_payload,
                    "taskboard_before": before_stats,
                    "taskboard_after": after_stats,
                    "progress_made": progress_made,
                    "metadata_progress": metadata_progress,
                }
                attempts.append(attempt_entry)

                if polled_result.status not in {"completed", "success"}:
                    stage_signals.append(
                        {
                            "code": "director.run_status_non_success",
                            "severity": "error",
                            "detail": str(polled_result.message or "").strip()
                            or str(polled_result.status or "unknown"),
                            "upstream_status": str(polled_result.status or "").strip(),
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

                if metadata_progress:
                    stage_signals.append(
                        {
                            "code": "director.dispatch_evidence_confirmed",
                            "severity": "info",
                            "detail": f"Director execution evidence confirmed in round {round_index}",
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
        execution_evidence_ok = self._has_director_execution_evidence(
            attempts=attempts,
            initial_stats=initial_stats,
            final_stats=final_stats,
            converged=converged,
        )

        stage_status = "success"
        if (
            str((final_result or CommandResult(run_id="", status="", message="")).status or "").strip().lower()
            == "cancelled"
        ):
            stage_status = "cancelled"
        elif any(
            str(item.get("severity") or "").strip().lower() == "error"
            for item in stage_signals
            if isinstance(item, dict)
        ):
            stage_status = "failed"
        elif not attempts and not converged:
            stage_status = "failed"
            stage_signals.append(
                {
                    "code": "director.no_dispatch_attempt",
                    "severity": "error",
                    "detail": "No director dispatch attempt executed before stage termination",
                }
            )
        elif not execution_evidence_ok:
            stage_status = "failed"
            stage_signals.append(
                {
                    "code": "director.execution_evidence_missing",
                    "severity": "error",
                    "detail": "No valid director execution evidence found from taskboard or run metadata",
                }
            )
        elif requires_taskboard_convergence and not converged:
            stage_status = "failed"
            stage_signals.append(
                {
                    "code": "director.taskboard_not_converged",
                    "severity": "error",
                    "detail": f"TaskBoard not converged after dispatch rounds; final_stats={final_stats}",
                }
            )

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

        stage_signal_path = ""
        if stage_signals:
            stage_signal_path = self._write_stage_signal_artifact(
                stage="director_dispatch",
                run_id=run.id,
                signals=stage_signals,
            )

        dispatch_payload = {
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
        if stage_signal_path:
            artifacts.append(stage_signal_path)
        if stage_status == "cancelled":
            return StageResult(
                stage="director_dispatch",
                status="cancelled",
                output=f"Director dispatch cancelled: {(final_result.message if final_result else 'N/A')}",
                artifacts=artifacts,
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
        )

    async def _execute_quality_gate(self, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        logger.info("Executing quality gate for run %s", run.id)
        abort_checker = self._resolve_abort_checker(context)

        service = self._build_orchestration_service(context)
        command_result = await service.execute_qa_run(
            workspace=str(self.workspace),
            target=context.get("qa_target", "Quality gate"),
            options={
                "input": context.get("qa_input"),
            },
        )
        final_result = await self._poll_run_completion(
            service,
            command_result,
            timeout_seconds=int(context.get("timeout", 600)),
            abort_checker=abort_checker,
        )
        if str(final_result.status or "").strip().lower() == "cancelled":
            return StageResult(
                stage="quality_gate",
                status="cancelled",
                output=f"Quality gate cancelled: {final_result.message or 'N/A'}",
                artifacts=[],
            )

        qa_report_path = self._artifact_path("runtime/qa/report.json")
        report_ready = await self._wait_for_artifact_file(
            qa_report_path,
            timeout_seconds=float(context.get("artifact_wait_seconds", 8.0) or 8.0),
            poll_interval=0.2,
        )
        if not report_ready:
            raise RuntimeError(f"Quality gate report missing: {qa_report_path}")
        loaded: dict[str, Any] | Any = {}
        parse_error: Exception | None = None
        for _attempt in range(5):
            try:
                loaded = json.loads(qa_report_path.read_text(encoding="utf-8"))
                parse_error = None
                break
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                parse_error = exc
                await asyncio.sleep(0.2)
        if parse_error is not None:
            raise RuntimeError(f"Quality gate report parse failed: {qa_report_path}") from parse_error
        if not isinstance(loaded, dict):
            raise RuntimeError(f"Quality gate report payload must be JSON object: {qa_report_path}")
        qa_payload: dict[str, Any] = loaded

        qa_passed = bool(qa_payload.get("passed"))
        qa_score = int(qa_payload.get("score") or 0)
        qa_critical = int(qa_payload.get("critical_issue_count") or 0)
        is_success = final_result.status in {"completed", "success"} and qa_passed
        output_suffix = f"qa_passed={qa_passed}; qa_score={qa_score}; qa_critical={qa_critical}"
        return StageResult(
            stage="quality_gate",
            status="success" if is_success else "failed",
            output=(f"Quality gate {final_result.status}: {final_result.message or 'N/A'}; {output_suffix}"),
            artifacts=["runtime/qa/report.json"],
        )

    def _build_orchestration_service(self, context: dict[str, Any]):
        from polaris.bootstrap.config import Settings
        from polaris.cells.orchestration.pm_dispatch.public.service import OrchestrationCommandService

        settings = context.get("settings") or Settings(workspace=Path(self.workspace))
        return OrchestrationCommandService(settings)

    async def _poll_run_completion(
        self,
        service: OrchestrationCommandService,
        initial_result: CommandResult,
        timeout_seconds: int = 300,
        poll_interval: float = 2.0,
        abort_checker: Callable[[], Awaitable[str | None]] | None = None,
    ) -> CommandResult:
        start_time = datetime.now(timezone.utc)
        timeout = timedelta(seconds=timeout_seconds)
        terminal_statuses = {"completed", "failed", "cancelled", "timeout", "blocked"}
        run_id = str(initial_result.run_id or "").strip()

        if initial_result.status in terminal_statuses or not run_id:
            return initial_result

        while datetime.now(timezone.utc) - start_time < timeout:
            if abort_checker is not None:
                try:
                    abort_reason = await abort_checker()
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    logger.debug("Factory abort checker failed for run %s: %s", run_id, exc)
                    abort_reason = None
                if abort_reason:
                    return CommandResult(
                        run_id=run_id,
                        status="cancelled",
                        message=f"Run cancelled: {abort_reason}",
                    )
            result = await service.query_run_status(run_id)
            if result.status in terminal_statuses:
                return result
            await asyncio.sleep(poll_interval)

        return CommandResult(
            run_id=run_id,
            status="timeout",
            message=f"Run timed out after {timeout_seconds} seconds",
        )

    @staticmethod
    def _resolve_abort_checker(
        context: dict[str, Any],
    ) -> Callable[[], Awaitable[str | None]] | None:
        checker = context.get("_factory_abort_checker")
        if callable(checker):
            return checker
        return None


class FactoryRunService:
    """Formal service for Factory runs with persistence and recovery."""

    # 细粒度锁桶数量 - 减少跨 run 的竞争
    _LOCK_BUCKETS = 64

    def __init__(
        self,
        workspace: Path,
        cache_root: Path | None = None,
        executor: FactoryStageExecutor | None = None,
    ) -> None:
        from .factory_store import FactoryStore

        self.workspace = Path(workspace)
        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        self.cache_root = cache_root or self.workspace / get_workspace_metadata_dir_name()
        self.store = FactoryStore(self.cache_root / "factory")
        # 细粒度锁: 按 run_id 哈希分片，减少竞争
        self._run_locks: list[asyncio.Lock] = [asyncio.Lock() for _ in range(self._LOCK_BUCKETS)]
        self._executor: FactoryStageExecutor = executor or OrchestrationStageExecutor(self.workspace)

    def _get_run_lock(self, run_id: str) -> asyncio.Lock:
        """获取 run_id 对应的细粒度锁。

        使用哈希分片确保同一 run 的操作串行化，不同 run 可并行。
        """
        bucket = hash(run_id) % self._LOCK_BUCKETS
        return self._run_locks[bucket]

    async def create_run(self, config: FactoryConfig) -> FactoryRun:
        """Create a new factory run with directory structure."""
        run = FactoryRun(
            id=f"factory_{uuid.uuid4().hex[:12]}",
            config=config,
            status=FactoryRunStatus.PENDING,
            created_at=self._now(),
            metadata={
                "current_stage": None,
                "last_stage": None,
                "last_successful_stage": None,
                "last_failed_stage": None,
            },
        )

        run_dir = self.store.get_run_dir(run.id)
        (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        (run_dir / "events").mkdir(parents=True, exist_ok=True)
        (run_dir / "checkpoints").mkdir(parents=True, exist_ok=True)

        await self.store.save_run(run)
        logger.info("Created factory run %s", run.id)
        return run

    async def execute_stage(
        self,
        run_id: str,
        stage: str,
        context: dict[str, Any] | None = None,
    ) -> StageResult:
        """Execute a single stage with durable lifecycle updates."""
        normalized_context = dict(context or {})
        normalized_context["_factory_abort_checker"] = self._build_abort_checker(run_id)
        heartbeat_interval = self._resolve_heartbeat_interval_seconds(normalized_context)

        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            if run.status not in {FactoryRunStatus.RUNNING, FactoryRunStatus.RECOVERING}:
                raise ValueError(f"Run {run_id} is not executable in status {run.status.value}")
            started_at = self._now()
            await self._mark_stage_started(run, stage, started_at)

        heartbeat_task: asyncio.Task[None] | None = None
        if heartbeat_interval > 0:
            heartbeat_task = asyncio.create_task(
                self._run_stage_heartbeat(run_id, stage, heartbeat_interval),
                name=f"factory_stage_heartbeat:{run_id}:{stage}",
            )

        try:
            result = await self._execute_stage_logic(run, stage, normalized_context)
        except (
            AttributeError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            asyncio.TimeoutError,
        ) as exc:
            result = StageResult(
                stage=stage,
                status="failed",
                output=f"{stage} failed: {exc}",
                artifacts=[],
                started_at=started_at,
                completed_at=self._now(),
            )
            async with run_lock:
                await self._mark_stage_finished(run, result, error=exc)
            logger.error("Stage %s failed for run %s: %s", stage, run_id, exc)
            raise
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    try:
                        await heartbeat_task
                    except (
                        AttributeError,
                        OSError,
                        RuntimeError,
                        TypeError,
                        ValueError,
                        asyncio.TimeoutError,
                    ) as heartbeat_exc:
                        logger.warning(
                            "Factory heartbeat task failed for run %s stage %s: %s",
                            run_id,
                            stage,
                            heartbeat_exc,
                        )

        result.started_at = result.started_at or started_at
        result.completed_at = result.completed_at or self._now()
        async with run_lock:
            await self._mark_stage_finished(run, result)
        return result

    async def _run_stage_heartbeat(
        self,
        run_id: str,
        stage: str,
        interval_seconds: float,
    ) -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            await self._emit_stage_heartbeat(run_id, stage)

    async def _emit_stage_heartbeat(self, run_id: str, stage: str) -> None:
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                return
            if run.status in TERMINAL_RUN_STATUSES:
                return
            current_stage = str(run.metadata.get("current_stage") or "").strip()
            if current_stage != stage:
                return

            timestamp = self._now()
            run.updated_at = timestamp
            run.metadata["last_stage_heartbeat_at"] = timestamp
            await self.store.save_run(run)
            await self._append_event(
                run_id,
                {
                    "type": "stage_heartbeat",
                    "stage": stage,
                    "message": f"Stage {stage} is still running",
                    "timestamp": timestamp,
                },
            )

    def _build_abort_checker(self, run_id: str) -> Callable[[], Awaitable[str | None]]:
        async def _checker() -> str | None:
            current_run = await self.store.get_run(run_id)
            if current_run is None:
                return "run_not_found"
            if current_run.status == FactoryRunStatus.CANCELLED:
                return str(current_run.metadata.get("cancel_reason") or "run_cancelled")
            return None

        return _checker

    @staticmethod
    def _resolve_heartbeat_interval_seconds(context: dict[str, Any]) -> float:
        raw_value = context.get("heartbeat_interval_seconds")
        if raw_value is None:
            return DEFAULT_STAGE_HEARTBEAT_INTERVAL_SECONDS
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return DEFAULT_STAGE_HEARTBEAT_INTERVAL_SECONDS
        if value <= 0:
            return 0.0
        return max(0.05, min(value, 300.0))

    async def recover_run(self, run_id: str) -> FactoryRun:
        """Recover a run from durable storage."""
        run = await self.store.get_run(run_id)
        if run is None:
            raise ValueError(f"Run {run_id} not found")

        if run.status in TERMINAL_RUN_STATUSES:
            return run

        last_successful_stage = (
            str(run.metadata.get("last_successful_stage") or "").strip()
            or str(run.recovery_point or "").strip()
            or str(await self._find_last_successful_stage(run_id) or "").strip()
            or None
        )
        run.recovery_point = last_successful_stage
        run.status = FactoryRunStatus.RECOVERING
        run.updated_at = self._now()
        run.metadata["current_stage"] = last_successful_stage
        run.metadata["last_stage"] = last_successful_stage
        await self.store.save_run(run)
        await self._append_event(
            run_id,
            {
                "type": "recovered",
                "stage": last_successful_stage,
                "message": f"Recovered run at {last_successful_stage or 'start'}",
                "timestamp": run.updated_at,
            },
        )
        logger.info("Run %s recovered at stage %s", run_id, last_successful_stage)
        return run

    async def execute_pause(self, run_id: str) -> FactoryRun:
        """Pause a running factory run."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")

            if run.status == FactoryRunStatus.RUNNING:
                run.status = FactoryRunStatus.PAUSED
                run.updated_at = self._now()
                await self.store.save_run(run)
                await self._append_event(
                    run_id,
                    {
                        "type": "paused",
                        "message": "Run paused",
                        "timestamp": run.updated_at,
                    },
                )
                logger.info("Run %s paused", run_id)
            return run

    async def execute_resume(self, run_id: str) -> FactoryRun:
        """Resume a paused factory run."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")

            if run.status == FactoryRunStatus.PAUSED:
                run.status = FactoryRunStatus.RUNNING
                run.updated_at = self._now()
                await self.store.save_run(run)
                await self._append_event(
                    run_id,
                    {
                        "type": "resumed",
                        "message": "Run resumed",
                        "timestamp": run.updated_at,
                    },
                )
                logger.info("Run %s resumed", run_id)
            return run

    async def start_run(self, run_id: str) -> FactoryRun:
        """Start a pending factory run."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")

            if run.status == FactoryRunStatus.PENDING:
                started_at = self._now()
                run.status = FactoryRunStatus.RUNNING
                run.started_at = started_at
                run.updated_at = started_at
                await self.store.save_run(run)
                await self._append_event(
                    run_id,
                    {
                        "type": "started",
                        "message": "Run started",
                        "timestamp": started_at,
                    },
                )
                logger.info("Run %s started", run_id)
            return run

    async def cancel_run(self, run_id: str, reason: str | None = None) -> FactoryRun:
        """Cancel a factory run and keep a distinct terminal status."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            if run.status in TERMINAL_RUN_STATUSES:
                return run

            timestamp = self._now()
            run.status = FactoryRunStatus.CANCELLED
            run.completed_at = timestamp
            run.updated_at = timestamp
            if reason:
                run.metadata["cancel_reason"] = reason
            await self.store.save_run(run)
            await self._append_event(
                run_id,
                {
                    "type": "cancelled",
                    "message": reason or "Run cancelled",
                    "reason": reason,
                    "timestamp": timestamp,
                },
            )
            logger.info("Run %s cancelled", run_id)

            # Trigger history archiving (async, non-blocking)
            self._trigger_archive(run_id, "cancelled")

            return run

    async def complete_run(self, run_id: str, success: bool = True) -> FactoryRun:
        """Complete a factory run."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")

            if run.status == FactoryRunStatus.CANCELLED:
                if run.completed_at is None:
                    run.completed_at = self._now()
                    run.updated_at = run.completed_at
                    await self.store.save_run(run)
                return run

            timestamp = self._now()
            run.status = FactoryRunStatus.COMPLETED if success else FactoryRunStatus.FAILED
            run.completed_at = timestamp
            run.updated_at = timestamp
            await self.store.save_run(run)
            await self._append_event(
                run_id,
                {
                    "type": "completed" if success else "failed",
                    "message": "Run completed" if success else "Run failed",
                    "timestamp": timestamp,
                    "success": success,
                },
            )
            logger.info("Run %s completed with success=%s", run_id, success)

            # Trigger history archiving (async, non-blocking)
            self._trigger_archive(run_id, "completed" if success else "failed")

            return run

    async def list_runs(self) -> list[dict[str, Any]]:
        """List all factory runs with basic info."""
        run_ids = self.store.list_runs()
        runs: list[dict[str, Any]] = []
        for run_id in run_ids:
            run = await self.store.get_run(run_id)
            if run is None:
                continue
            runs.append(
                {
                    "id": run.id,
                    "name": run.config.name,
                    "status": run.status.value,
                    "created_at": run.created_at,
                    "updated_at": run.updated_at,
                    "current_stage": run.metadata.get("current_stage"),
                    "last_successful_stage": run.metadata.get("last_successful_stage"),
                    "stages_completed": len(run.stages_completed),
                    "stages_failed": len(run.stages_failed),
                }
            )
        return runs

    async def get_run(self, run_id: str) -> FactoryRun | None:
        """Get a factory run by ID."""
        return await self.store.get_run(run_id)

    async def get_run_events(self, run_id: str) -> list[dict[str, Any]]:
        """Get all events for a run."""
        return await self.store.get_events(run_id)

    async def _execute_stage_logic(
        self,
        run: FactoryRun,
        stage: str,
        context: dict[str, Any],
    ) -> StageResult:
        if stage not in SUPPORTED_FACTORY_STAGES:
            return StageResult(stage=stage, status="skipped", output="No handler for this stage")
        return await self._executor.execute(stage, run, context)

    async def _find_last_successful_stage(self, run_id: str) -> str | None:
        """Find the last successful stage from events."""
        events = await self.store.get_events(run_id)
        for event in reversed(events):
            if event.get("type") != "stage_completed":
                continue
            result = event.get("result", {})
            if result.get("status") == "success":
                return result.get("stage")
        return None

    async def _mark_stage_started(self, run: FactoryRun, stage: str, started_at: str) -> None:
        run.metadata["current_stage"] = stage
        run.metadata["current_stage_started_at"] = started_at
        run.metadata["last_stage"] = stage
        run.updated_at = started_at
        await self.store.save_run(run)
        await self._append_event(
            run.id,
            {
                "type": "stage_started",
                "stage": stage,
                "message": f"Started stage {stage}",
                "timestamp": started_at,
            },
        )

    async def _mark_stage_finished(
        self,
        run: FactoryRun,
        result: StageResult,
        error: Exception | None = None,
    ) -> None:
        completed_at = result.completed_at or self._now()
        result.completed_at = completed_at
        latest_run = await self.store.get_run(run.id)
        target_run = latest_run or run

        target_run.metadata["last_stage"] = result.stage
        target_run.metadata["current_stage_completed_at"] = completed_at

        cancelled_externally = (
            target_run.status == FactoryRunStatus.CANCELLED or str(result.status or "").strip().lower() == "cancelled"
        )
        if cancelled_externally:
            result.status = "cancelled"
            if not str(result.output or "").strip():
                reason = str(target_run.metadata.get("cancel_reason") or "Run cancelled").strip()
                result.output = f"Stage {result.stage} cancelled: {reason}"
            target_run.status = FactoryRunStatus.CANCELLED
            target_run.metadata["last_cancelled_stage"] = result.stage
        elif result.status == "success":
            self._append_unique(target_run.stages_completed, result.stage)
            target_run.recovery_point = result.stage
            target_run.metadata["last_successful_stage"] = result.stage
        elif result.status == "failed":
            self._append_unique(target_run.stages_failed, result.stage)
            target_run.status = FactoryRunStatus.FAILED
            target_run.metadata["last_failed_stage"] = result.stage
            target_run.metadata["failure"] = {
                "stage": result.stage,
                "code": "FACTORY_STAGE_FAILED",
                "detail": result.output or str(error or "Stage failed"),
                "recoverable": True,
                "timestamp": completed_at,
            }

        target_run.updated_at = completed_at
        await self.store.save_run(target_run)
        await self._append_event(
            target_run.id,
            {
                "type": "stage_completed",
                "stage": result.stage,
                "message": result.output or f"Completed stage {result.stage}",
                "result": result.to_dict(),
                "timestamp": completed_at,
            },
        )
        await self.store.checkpoint(target_run)

    async def _append_event(self, run_id: str, event: dict[str, Any]) -> None:
        payload = dict(event)
        payload.setdefault("event_id", f"evt_{uuid.uuid4().hex[:12]}")
        payload.setdefault("timestamp", self._now())
        await self.store.append_event(run_id, payload)

    def _trigger_archive(self, run_id: str, reason: str) -> None:
        """Trigger async archiving of factory run to history.

        This is non-blocking - archiving happens in background.
        """
        try:
            from polaris.cells.archive.factory_archive.public.service import trigger_factory_archive

            workspace = str(self.workspace) if hasattr(self, "workspace") else ""
            if workspace:
                trigger_factory_archive(
                    workspace=workspace,
                    factory_run_id=run_id,
                    reason=reason,
                )
                logger.debug("Triggered archive for factory run %s", run_id)
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            # Log error but don't block the main flow
            logger.warning("Failed to trigger archive for factory run %s: %s", run_id, exc)

    @staticmethod
    def _append_unique(target: list[str], value: str) -> None:
        if value and value not in target:
            target.append(value)

    @staticmethod
    def _now() -> str:
        return utc_now_iso()
