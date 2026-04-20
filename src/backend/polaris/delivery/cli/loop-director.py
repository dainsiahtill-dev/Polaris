#!/usr/bin/env python3
"""Polaris Director v2 Integration - Full Architecture Integration.

This module integrates Polaris PM with Director v2 service architecture,
using Polaris's application layer services and domain entities for
seamless task execution.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _bootstrap_backend_import_path() -> None:
    """Ensure backend package path when running file directly."""
    if __package__:
        return
    backend_root = Path(__file__).resolve().parents[3]
    backend_root_str = str(backend_root)
    if backend_root_str not in sys.path:
        sys.path.insert(0, backend_root_str)


_bootstrap_backend_import_path()

# Polaris execution/runtime services
from polaris.cells.director.execution.public.service import (
    DirectorConfig,
    DirectorService,
)

# Polaris domain entities
from polaris.domain.entities import TaskPriority, TaskStatus
from polaris.kernelone.constants import DEFAULT_DIRECTOR_MAX_PARALLELISM, DEFAULT_OPERATION_TIMEOUT_SECONDS

# Polaris infrastructure


def load_pm_task_contract(pm_task_path: str) -> dict:
    """Load PM task contract from JSON file."""
    with open(pm_task_path, encoding="utf-8") as f:
        return json.load(f)


def map_pm_priority_to_director(priority: Any) -> TaskPriority:
    """Map PM task priority to Director v2 TaskPriority."""
    if isinstance(priority, int):
        if priority <= 1:
            return TaskPriority.CRITICAL
        elif priority <= 3:
            return TaskPriority.HIGH
        elif priority <= 6:
            return TaskPriority.MEDIUM
        else:
            return TaskPriority.LOW
    # Handle string priorities
    priority_str = str(priority).upper()
    priority_map = {
        "CRITICAL": TaskPriority.CRITICAL,
        "URGENT": TaskPriority.CRITICAL,
        "HIGHEST": TaskPriority.CRITICAL,
        "HIGH": TaskPriority.HIGH,
        "NORMAL": TaskPriority.MEDIUM,
        "MEDIUM": TaskPriority.MEDIUM,
        "LOW": TaskPriority.LOW,
    }
    return priority_map.get(priority_str, TaskPriority.MEDIUM)


def _format_construction_plan_for_description(construction_plan: dict) -> str:
    """Render concise ChiefEngineer construction plan text for Director."""
    if not isinstance(construction_plan, dict):
        return ""

    file_plans_raw = construction_plan.get("file_plans")
    file_plans = file_plans_raw if isinstance(file_plans_raw, list) else []
    if not file_plans:
        return ""

    lines: list[str] = ["ChiefEngineer Construction Plan:"]
    for item in file_plans[:8]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        method_names = item.get("method_names")
        methods = (
            [str(token).strip() for token in method_names if str(token or "").strip()]
            if isinstance(method_names, list)
            else []
        )
        if methods:
            lines.append(f"- {path}: implement {', '.join(methods[:8])}")
        else:
            lines.append(f"- {path}: implement file-level plan")
    return "\n".join(lines).strip()


def extract_pm_tasks(pm_contract: dict) -> list[dict]:
    """Extract executable tasks from PM contract."""
    raw_tasks = pm_contract.get("tasks", [])
    if not raw_tasks:
        raise ValueError("No tasks found in PM contract")

    # Handle both dict format (registry.json) and list format (legacy)
    if isinstance(raw_tasks, dict):
        tasks = list(raw_tasks.values())
    elif isinstance(raw_tasks, list):
        tasks = raw_tasks
    else:
        raise ValueError(f"Unexpected tasks format: {type(raw_tasks)}")

    extracted: list[dict[str, Any]] = []
    for task in tasks:
        # Build task description from PM payload with priority on PM-provided description.
        # Handle new registry format where details are in metadata.legacy_task
        metadata = dict(task.get("metadata")) if isinstance(task.get("metadata"), dict) else {}
        legacy_task = metadata.get("legacy_task", {}) if isinstance(metadata, dict) else {}

        # Try to get fields from legacy_task first (new format), then from task directly (old format)
        spec = legacy_task.get("spec", task.get("spec", ""))
        goal = legacy_task.get("goal", task.get("goal", ""))
        title = legacy_task.get("title", task.get("title", "Untitled"))
        raw_description = str(legacy_task.get("description") or task.get("description") or "").strip()
        acceptance = legacy_task.get("acceptance_criteria") or task.get("acceptance_criteria")
        if acceptance is None:
            acceptance = legacy_task.get("acceptance") or task.get("acceptance")
        constraints = legacy_task.get("constraints") or task.get("constraints")
        chief_engineer = (
            legacy_task.get("chief_engineer")
            if isinstance(legacy_task.get("chief_engineer"), dict)
            else task.get("chief_engineer")
            if isinstance(task.get("chief_engineer"), dict)
            else {}
        )
        construction_plan = (
            legacy_task.get("construction_plan")
            if isinstance(legacy_task.get("construction_plan"), dict)
            else task.get("construction_plan")
            if isinstance(task.get("construction_plan"), dict)
            else chief_engineer.get("construction_plan")
            if isinstance(chief_engineer.get("construction_plan"), dict)
            else {}
        )
        plan_text = _format_construction_plan_for_description(
            construction_plan if isinstance(construction_plan, dict) else {}
        )
        description_parts: list[str] = []
        if raw_description:
            description_parts.append(raw_description)
        if goal:
            description_parts.append(f"Goal: {goal}")
        if spec:
            description_parts.append(f"Spec: {spec}")
        if acceptance:
            description_parts.append(f"Acceptance: {acceptance}")
        if isinstance(constraints, list):
            normalized_constraints = [str(item).strip() for item in constraints if str(item or "").strip()]
            if normalized_constraints:
                description_parts.append("Constraints:\n- " + "\n- ".join(normalized_constraints[:16]))
        if plan_text:
            description_parts.append(plan_text)
        description = "\n\n".join(part for part in description_parts if str(part).strip()).strip()
        # metadata already extracted above
        phase_hint = str(legacy_task.get("phase") or task.get("phase") or "").strip().lower()
        if phase_hint:
            metadata.setdefault("phase", phase_hint)
        if isinstance(construction_plan, dict) and construction_plan:
            metadata["construction_plan"] = construction_plan
        if isinstance(chief_engineer, dict) and chief_engineer:
            metadata["chief_engineer"] = chief_engineer
        # Extract target_files and scope_paths from legacy_task or task
        target_files = (
            legacy_task.get("target_files")
            if isinstance(legacy_task.get("target_files"), list)
            else task.get("target_files")
            if isinstance(task.get("target_files"), list)
            else []
        )
        scope_paths = (
            legacy_task.get("scope_paths")
            if isinstance(legacy_task.get("scope_paths"), list)
            else task.get("scope_paths")
            if isinstance(task.get("scope_paths"), list)
            else []
        )
        if target_files:
            metadata.setdefault("target_files", target_files)
        if scope_paths:
            metadata.setdefault("scope_paths", scope_paths)
        if isinstance(constraints, list) and constraints:
            metadata.setdefault("constraints", constraints)
        # Add tech_stack detection for WorkerExecutor
        tech_stack = legacy_task.get("tech_stack") if isinstance(legacy_task.get("tech_stack"), dict) else {}
        if not tech_stack:
            # Try to detect from constraints and context
            all_text = " ".join(constraints) if isinstance(constraints, list) else ""
            all_text += " " + goal + " " + spec + " " + title
            if "express" in all_text.lower() or "typescript" in all_text.lower() or ".ts" in " ".join(target_files):
                tech_stack = {"language": "typescript", "framework": "express"}
            elif "python" in all_text.lower() or ".py" in " ".join(target_files):
                tech_stack = {"language": "python"}
        if tech_stack:
            metadata.setdefault("tech_stack", tech_stack)

        blocked_by_raw = legacy_task.get("depends_on")
        if not isinstance(blocked_by_raw, list):
            blocked_by_raw = task.get("depends_on") if isinstance(task.get("depends_on"), list) else []
        blocked_by = _normalize_dependency_ids(blocked_by_raw)

        extracted.append(
            {
                "subject": title,
                "description": description,
                "priority": map_pm_priority_to_director(task.get("priority", 5)),
                "blocked_by": blocked_by,
                "task_id": task.get("id", f"task-{len(extracted)}"),
                "scope_paths": scope_paths,
                "target_files": target_files,
                "metadata": metadata,
            }
        )

    return extracted


def _normalize_dependency_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def _plan_task_execution_order(
    tasks: list[dict[str, Any]],
    limit: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Plan an executable task order and prevent dependency deadlocks.

    Root-cause fix:
    - PM `depends_on` IDs are PM IDs, while Director runtime IDs are generated (`task-*`).
    - Executing `tasks[:iterations]` directly can pick a task with unresolved blockers and
      leave it pending until timeout.
    - We now select tasks in topological order and ignore unknown blockers with warnings.
    """
    warnings: list[str] = []
    capped_limit = max(1, int(limit or 1))

    by_id: dict[str, dict[str, Any]] = {}
    order_index: dict[str, int] = {}
    for index, item in enumerate(tasks):
        task = dict(item) if isinstance(item, dict) else {}
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            continue
        if task_id in by_id:
            warnings.append(f"duplicate_task_id_ignored:{task_id}")
            continue
        task["blocked_by"] = _normalize_dependency_ids(task.get("blocked_by"))
        by_id[task_id] = task
        order_index[task_id] = index

    if not by_id:
        return [], warnings

    indegree: dict[str, int] = dict.fromkeys(by_id, 0)
    outgoing: dict[str, list[str]] = {task_id: [] for task_id in by_id}

    for task_id, task in by_id.items():
        raw_deps = _normalize_dependency_ids(task.get("blocked_by"))
        resolved_deps: list[str] = []
        unknown_deps: list[str] = []
        for dep in raw_deps:
            if dep == task_id:
                warnings.append(f"self_dependency_ignored:{task_id}")
                continue
            if dep not in by_id:
                unknown_deps.append(dep)
                continue
            resolved_deps.append(dep)
            outgoing[dep].append(task_id)

        if unknown_deps:
            preview = ",".join(unknown_deps[:5])
            warnings.append(f"unknown_dependencies_ignored:{task_id}:{preview}")

        task["blocked_by"] = resolved_deps
        indegree[task_id] = len(resolved_deps)

    ready = sorted(
        [task_id for task_id, degree in indegree.items() if degree == 0],
        key=lambda task_id: order_index.get(task_id, 0),
    )
    ordered: list[dict[str, Any]] = []

    while ready and len(ordered) < min(capped_limit, len(by_id)):
        current = ready.pop(0)
        ordered.append(by_id[current])
        for dependent in sorted(outgoing[current], key=lambda task_id: order_index.get(task_id, 0)):
            indegree[dependent] = max(0, indegree[dependent] - 1)
            if indegree[dependent] == 0 and dependent not in ready:
                ready.append(dependent)
        ready.sort(key=lambda task_id: order_index.get(task_id, 0))

    remaining_blocked = [task_id for task_id, degree in indegree.items() if degree > 0]
    if remaining_blocked:
        preview = ",".join(remaining_blocked[:5])
        warnings.append(f"dependency_cycle_or_unsatisfied:{preview}")

    return ordered, warnings


class DirectorV2Runner:
    """Runner that integrates PM tasks with Director v2 service."""

    def __init__(self, workspace: str, config: DirectorConfig) -> None:
        self.workspace = workspace
        self.config = config
        self.director: DirectorService | None = None
        self.results: dict[str, Any] = {
            "success": False,
            "tasks_executed": 0,
            "files_created": [],
            "errors": [],
        }

    async def initialize(self) -> None:
        """Initialize Director service."""
        self.director = DirectorService(self.config)
        await self.director.start()
        logger.info("[DirectorV2] Service initialized: %s", self.director.state.name)

    async def execute_task(self, task_data: dict, timeout: int = DEFAULT_OPERATION_TIMEOUT_SECONDS) -> bool:
        """Execute a single task through Director v2."""
        if self.director is None:
            logger.error("[DirectorV2] Director service not initialized")
            return False
        logger.info("\n[DirectorV2] Creating task: %s", task_data["subject"])

        try:
            runtime_blocked_by = _normalize_dependency_ids(
                task_data.get("blocked_by_runtime") or task_data.get("blocked_by")
            )
            # Submit task to Director v2
            task = await self.director.submit_task(
                subject=task_data["subject"],
                description=task_data["description"],
                priority=task_data["priority"],
                blocked_by=runtime_blocked_by,  # type: ignore[arg-type]
                metadata=task_data.get("metadata", {}),
            )
            task_data["_runtime_task_id"] = task.id

            logger.info("[DirectorV2] Task created: %s (status: %s)", task.id, task.status.name)

            # Wait for task completion
            start_time = time.time()
            poll_interval = 1.0
            pre_execution_stall_timeout = max(20, min(int(timeout * 0.25), 90))
            last_status: TaskStatus | None = None
            last_status_changed_at = start_time

            while time.time() - start_time < timeout:
                # Get current task status
                current_task = await self.director.get_task(str(task.id))
                if current_task is None:
                    error_msg = f"Task {task.id} not found in DirectorService"
                    logger.error("[DirectorV2] %s", error_msg)
                    self.results["errors"].append(error_msg)
                    return False

                if current_task.status != last_status:
                    last_status = current_task.status
                    last_status_changed_at = time.time()
                    logger.info("[DirectorV2] Task %s status -> %s", task.id, current_task.status.name)

                if current_task.status == TaskStatus.COMPLETED:
                    logger.info("[DirectorV2] Task completed: %s", task.id)
                    self.results["tasks_executed"] += 1

                    # Collect evidence/files from result
                    if current_task.result:
                        for evidence in current_task.result.evidence:
                            if evidence.type == "file" and evidence.path:
                                self.results["files_created"].append(evidence.path)

                    return True

                elif current_task.status == TaskStatus.FAILED:
                    error_msg = f"Task {task.id} failed"
                    if current_task.result and current_task.result.error:
                        error_msg += f": {current_task.result.error}"
                    logger.error("[DirectorV2] %s", error_msg)
                    self.results["errors"].append(error_msg)
                    return False

                elif current_task.status == TaskStatus.CANCELLED:
                    logger.info("[DirectorV2] Task %s was cancelled", task.id)
                    self.results["errors"].append(f"Task {task.id} cancelled")
                    return False

                elif current_task.status in {TaskStatus.PENDING, TaskStatus.READY, TaskStatus.CLAIMED}:
                    if time.time() - last_status_changed_at >= pre_execution_stall_timeout:
                        blocked_by = _normalize_dependency_ids(getattr(current_task, "blocked_by", []))
                        blocked_hint = f" blocked_by={blocked_by}" if blocked_by else ""
                        error_msg = (
                            f"Task {task.id} stalled in {current_task.status.name} "
                            f"for >{pre_execution_stall_timeout}s.{blocked_hint}"
                        )
                        logger.error("[DirectorV2] %s", error_msg)
                        self.results["errors"].append(error_msg)
                        return False

                await asyncio.sleep(poll_interval)

            # Timeout
            logger.error("[DirectorV2] Task %s timed out after %ss", task.id, timeout)
            self.results["errors"].append(f"Task {task.id} timeout")
            return False

        except (RuntimeError, ValueError) as e:
            error_msg = f"Error executing task: {e}"
            logger.error("[DirectorV2] %s", error_msg)
            self.results["errors"].append(error_msg)
            return False

    async def run(self, pm_task_path: str, iterations: int, timeout: int) -> dict:
        """Run Director v2 with PM tasks."""
        if self.director is None:
            self.results["errors"].append("Director service not initialized")
            return self.results
        try:
            # Initialize
            await self.initialize()

            # Load PM tasks
            pm_contract = load_pm_task_contract(pm_task_path)
            tasks = extract_pm_tasks(pm_contract)

            logger.info("[DirectorV2] Loaded %s tasks from PM contract", len(tasks))

            execution_limit = max(1, min(int(iterations or 1), len(tasks)))
            planned_tasks, planning_warnings = _plan_task_execution_order(tasks, execution_limit)
            for warning in planning_warnings:
                logger.warning("[DirectorV2] Dependency warning: %s", warning)

            if not planned_tasks:
                self.results["errors"].append("No executable tasks after dependency planning")
                return self.results

            runtime_id_by_pm_id: dict[str, str] = {}

            # Execute tasks
            for i, task_data in enumerate(planned_tasks, 1):
                logger.info("\n[DirectorV2] === Task %s/%s ===", i, len(planned_tasks))

                runtime_blocked_by: list[str] = []
                unresolved: list[str] = []
                for dep in _normalize_dependency_ids(task_data.get("blocked_by")):
                    mapped = runtime_id_by_pm_id.get(dep)
                    if mapped:
                        runtime_blocked_by.append(mapped)
                    else:
                        unresolved.append(dep)
                if unresolved:
                    error_msg = f"Task {task_data.get('task_id')} has unresolved runtime dependencies: " + ",".join(
                        unresolved[:5]
                    )
                    logger.error("[DirectorV2] %s", error_msg)
                    self.results["errors"].append(error_msg)
                    break

                task_data["blocked_by_runtime"] = runtime_blocked_by

                success = await self.execute_task(task_data, timeout)
                runtime_task_id = str(task_data.get("_runtime_task_id") or "").strip()
                pm_task_id = str(task_data.get("task_id") or "").strip()
                if success and pm_task_id and runtime_task_id:
                    runtime_id_by_pm_id[pm_task_id] = runtime_task_id

                if not success:
                    logger.error("[DirectorV2] Task failed, stopping")
                    break

            # Determine overall success
            self.results["success"] = self.results["tasks_executed"] > 0 and len(self.results["errors"]) == 0

        except (RuntimeError, ValueError) as e:
            logger.error("[DirectorV2] Fatal error: %s", e)
            self.results["errors"].append(f"Fatal: {e}")

        finally:
            # Cleanup
            if self.director:
                try:
                    await self.director.stop()
                    logger.info("[DirectorV2] Service stopped")
                except (RuntimeError, ValueError) as e:
                    logger.error("[DirectorV2] Error stopping service: %s", e)

        return self.results


def write_director_result(result_path: str, results: dict, start_time: datetime) -> None:
    """Write Director result JSON in legacy-compatible format."""
    end_time = datetime.now(timezone.utc)
    duration_ms = int((end_time - start_time).total_seconds() * 1000)

    result_data = {
        "schema_version": 1,
        "status": "success" if results["success"] else "blocked",
        "exit_code": 0 if results["success"] else 1,
        "timestamp": end_time.isoformat(),
        "timestamp_epoch": time.time(),
        "changed_files": results["files_created"],
        "tasks_executed": results["tasks_executed"],
        "duration_ms": duration_ms,
        "qa_verdict": "PASS" if results["success"] else "FAIL",
        "qa_failed_gates": [] if results["success"] else ["execution_failed"],
        "qa_missing_evidence": [],
        "qa_diagnostics": (
            "execution_successful" if results["success"] else "; ".join(results["errors"]) or "execution_failed"
        ),
        "qa_plugin": "rules_v1",
        "qa_plugin_hint": "rules_v1",
        "qa_task_type": "generic",
        "qa_mode": "blocking",
        "qa_retry_count": 0,
        "qa_failed_final": not results["success"],
        "qa_coordination_pending": False,
        "tri_council": {},
    }

    os.makedirs(os.path.dirname(result_path), exist_ok=True)
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)

    logger.info("[DirectorV2] Result written to: %s", result_path)


async def async_main(args: argparse.Namespace) -> int:
    """Async main entry point."""
    start_time = datetime.now(timezone.utc)

    logger.info("\n%s", "=" * 60)
    logger.info("[DirectorV2] Polaris Director v2 Integration")
    logger.info("[DirectorV2] Started: %s", start_time.isoformat())
    logger.info("[DirectorV2] Workspace: %s", args.workspace)
    logger.info("[DirectorV2] PM Task: %s", args.pm_task_path)
    logger.info("%s\n", "=" * 60)

    # Validate PM task path
    if not args.pm_task_path or not os.path.exists(args.pm_task_path):
        logger.error("[DirectorV2] Error: PM task file not found: %s", args.pm_task_path)
        return 1

    # Create Director configuration
    config = DirectorConfig(
        workspace=args.workspace,
        max_workers=DEFAULT_DIRECTOR_MAX_PARALLELISM,
        task_poll_interval=1.0,
        enable_nag=True,
        enable_auto_compact=True,
        token_budget=None,
    )

    timeout_value = int(args.timeout or 600)
    if timeout_value <= 0:
        timeout_value = 600
    timeout_value = min(max(timeout_value, 30), 1800)

    # Run Director v2
    runner = DirectorV2Runner(args.workspace, config)
    results = await runner.run(
        pm_task_path=args.pm_task_path,
        iterations=args.iterations,
        timeout=timeout_value,
    )

    # Print summary
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()

    logger.info("\n%s", "=" * 60)
    logger.info("[DirectorV2] Finished: %s", end_time.isoformat())
    logger.info("[DirectorV2] Duration: %.2fs", duration)
    logger.info("[DirectorV2] Tasks executed: %s", results["tasks_executed"])
    logger.info("[DirectorV2] Files created: %s", len(results["files_created"]))
    logger.info("[DirectorV2] Errors: %s", len(results["errors"]))
    logger.info("[DirectorV2] Status: %s", "SUCCESS" if results["success"] else "FAILED")
    logger.info("%s\n", "=" * 60)

    # Write result JSON
    if args.director_result_path:
        write_director_result(args.director_result_path, results, start_time)

    return 0 if results["success"] else 1


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="loop-director",
        description="Polaris Director v2 - Full Architecture Integration",
    )

    # Core arguments
    parser.add_argument("--workspace", default=".", help="Workspace directory")
    parser.add_argument("--iterations", type=int, default=1, help="Number of tasks to execute")
    parser.add_argument("--no-rollback-on-fail", action="store_true", help="Continue on failure")

    # Paths
    parser.add_argument("--director-result-path", help="Path to write director result JSON")
    parser.add_argument("--pm-task-path", required=True, help="Path to PM task contract")
    parser.add_argument("--log-path", help="Path to write log file")
    parser.add_argument("--events-path", help="Path to events file")

    # Response paths (for compatibility)
    parser.add_argument("--planner-response-path", help=argparse.SUPPRESS)
    parser.add_argument("--ollama-response-path", help=argparse.SUPPRESS)
    parser.add_argument("--qa-response-path", help=argparse.SUPPRESS)
    parser.add_argument("--reviewer-response-path", help=argparse.SUPPRESS)

    # Execution options
    parser.add_argument("--model", help="Model to use")
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Timeout per task (seconds)",
    )
    parser.add_argument("--prompt-profile", help="Prompt profile")
    parser.add_argument("--show-output", action="store_true", help="Show detailed output")

    args = parser.parse_args()

    # Run async main
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    sys.exit(main())
