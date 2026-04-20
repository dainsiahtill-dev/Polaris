"""PM系统集成模块

将新的PM系统(StateManager, RequirementsTracker, DocumentManager,
TaskOrchestrator, ExecutionTracker)集成到现有的编排引擎中。
"""

from __future__ import annotations

import json
import os
from typing import Any

from polaris.delivery.cli.pm.document_manager import DocumentManager, get_document_manager
from polaris.delivery.cli.pm.execution_tracker import ExecutionTracker, get_execution_tracker
from polaris.delivery.cli.pm.requirements_tracker import (
    RequirementsTracker,
    get_requirements_tracker,
)
from polaris.delivery.cli.pm.state_manager import PMStateManager, get_state_manager
from polaris.delivery.cli.pm.task_orchestrator import (
    AssigneeType,
    TaskOrchestrator,
    TaskPriority,
    TaskStatus,
    TaskVerification,
    get_task_orchestrator,
)


class PM:
    """PM - 项目管理系统

    这是PM系统的统一入口，整合了所有子系统：
    - 数据空间管理 (StateManager)
    - 需求追踪 (RequirementsTracker)
    - 文档管理 (DocumentManager)
    - 任务编排 (TaskOrchestrator)
    - 执行追踪 (ExecutionTracker)
    """

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self._state_manager: PMStateManager | None = None
        self._requirements_tracker: RequirementsTracker | None = None
        self._document_manager: DocumentManager | None = None
        self._task_orchestrator: TaskOrchestrator | None = None
        self._execution_tracker: ExecutionTracker | None = None

    @property
    def state(self) -> PMStateManager:
        """Get state manager."""
        if self._state_manager is None:
            self._state_manager = get_state_manager(self.workspace)
        return self._state_manager

    @property
    def requirements(self) -> RequirementsTracker:
        """Get requirements tracker."""
        if self._requirements_tracker is None:
            self._requirements_tracker = get_requirements_tracker(self.workspace)
        return self._requirements_tracker

    @property
    def documents(self) -> DocumentManager:
        """Get document manager."""
        if self._document_manager is None:
            self._document_manager = get_document_manager(self.workspace)
        return self._document_manager

    @property
    def tasks(self) -> TaskOrchestrator:
        """Get task orchestrator."""
        if self._task_orchestrator is None:
            self._task_orchestrator = get_task_orchestrator(self.workspace)
        return self._task_orchestrator

    @property
    def execution(self) -> ExecutionTracker:
        """Get execution tracker."""
        if self._execution_tracker is None:
            self._execution_tracker = get_execution_tracker(self.workspace)
        return self._execution_tracker

    def initialize(self, project_name: str = "", description: str = "") -> dict[str, Any]:
        """Initialize PM system.

        Args:
            project_name: Project name
            description: Project description

        Returns:
            Initialization result
        """
        # Initialize state manager
        state = self.state.initialize(project_name, description)

        # Other subsystems will auto-initialize

        return {
            "initialized": True,
            "workspace": self.workspace,
            "project_name": state.metadata.name,
            "pm_version": state.version,
        }

    def is_initialized(self) -> bool:
        """Check if PM is initialized."""
        return self.state.is_initialized()

    def get_status(self) -> dict[str, Any]:
        """Get full PM status."""
        pm_state = self.state.get_state()
        task_stats = self.tasks.get_stats_summary()
        req_coverage = self.requirements.get_coverage_report()

        return {
            "initialized": self.is_initialized(),
            "project": pm_state.metadata.name if pm_state else None,
            "version": pm_state.version if pm_state else None,
            "stats": {
                "tasks": task_stats,
                "requirements": req_coverage,
            },
            "storage": self.state.get_storage_summary() if pm_state else None,
        }

    def sync_from_legacy_tasks(self, legacy_tasks: list[dict[str, Any]]) -> int:
        """Sync tasks from legacy format to PM registry.

        Args:
            legacy_tasks: Legacy task list

        Returns:
            Number of tasks synced
        """
        if not isinstance(legacy_tasks, list):
            return 0

        synced = 0
        legacy_index = self._build_legacy_task_index()

        for task_data in legacy_tasks:
            if not isinstance(task_data, dict):
                continue

            legacy_id = str(task_data.get("id") or "").strip()
            existing_task_id = legacy_index.get(legacy_id) if legacy_id else None
            normalized_status = self._map_legacy_status(task_data.get("status"))
            normalized_priority = self._map_legacy_priority(task_data.get("priority"))
            normalized_assignee_type = self._map_assignee_type(task_data.get("assignee_type"))
            assignee = str(task_data.get("assignee") or "").strip()

            raw_metadata = task_data.get("metadata")
            metadata: dict[str, Any] = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
            if legacy_id:
                metadata["legacy_id"] = legacy_id
            metadata["legacy_task"] = dict(task_data)
            metadata["last_synced_at"] = self._now_iso_compact()

            if existing_task_id:
                existing_task = self.tasks.get_task(existing_task_id)
                if existing_task is None:
                    continue

                target_status = self._preserve_terminal_status(existing_task.status, normalized_status)
                self.tasks.update_task(
                    existing_task_id,
                    changed_by="legacy_sync",
                    title=str(task_data.get("title") or existing_task.title or "Untitled").strip() or "Untitled",
                    description=str(task_data.get("description") or existing_task.description or "").strip(),
                    priority=normalized_priority,
                    requirements=task_data.get("requirements", []),
                    dependencies=task_data.get("dependencies", []),
                    estimated_effort=int(task_data.get("estimated_effort") or 0),
                    status=target_status,
                    metadata=metadata,
                )
                self._sync_assignment(
                    task_id=existing_task_id,
                    assignee=assignee,
                    assignee_type=normalized_assignee_type,
                )
                synced += 1
                continue

            created_task = self.tasks.register_task(
                title=str(task_data.get("title") or "Untitled").strip() or "Untitled",
                description=str(task_data.get("description") or "").strip(),
                priority=normalized_priority,
                requirements=task_data.get("requirements", []),
                dependencies=task_data.get("dependencies", []),
                estimated_effort=int(task_data.get("estimated_effort") or 0),
                metadata=metadata,
            )
            if legacy_id:
                legacy_index[legacy_id] = created_task.id

            self.tasks.update_task(
                created_task.id,
                changed_by="legacy_sync",
                status=normalized_status,
            )
            self._sync_assignment(
                task_id=created_task.id,
                assignee=assignee,
                assignee_type=normalized_assignee_type,
            )
            synced += 1

        return synced

    def get_ready_tasks_for_director(self, limit: int = 6) -> list[dict[str, Any]]:
        """Get tasks ready for Director execution.

        This is the main interface for the orchestration engine to get tasks.
        PM维护的真相源，智能选择最佳任务分配给Director。

        Args:
            limit: Maximum number of tasks

        Returns:
            List of tasks ready for execution
        """
        # Get ready tasks (dependencies satisfied)
        ready_tasks = self.tasks.get_ready_tasks()

        # Sort by priority and topological order
        sorted_ids = self.tasks.topological_sort()
        task_order = {tid: idx for idx, tid in enumerate(sorted_ids)}
        ready_tasks.sort(key=lambda t: (-self._priority_value(t.priority), task_order.get(t.id, 9999)))

        # Convert to legacy format for compatibility
        result = []
        for task in ready_tasks[:limit]:
            metadata = task.metadata if isinstance(task.metadata, dict) else {}
            legacy_payload = metadata.get("legacy_task")
            if isinstance(legacy_payload, dict):
                payload = dict(legacy_payload)
                payload["id"] = str(metadata.get("legacy_id") or payload.get("id") or task.id)
                payload["_shangshuling_task_id"] = task.id
                payload["_shangshuling_status"] = (
                    task.status.value if hasattr(task.status, "value") else str(task.status)
                )
                result.append(payload)
                continue

            result.append(
                {
                    "id": str(metadata.get("legacy_id") or task.id),
                    "title": task.title,
                    "description": task.description,
                    "status": task.status.value if hasattr(task.status, "value") else task.status,
                    "priority": task.priority.value if hasattr(task.priority, "value") else task.priority,
                    "assignee": task.assignee,
                    "requirements": task.requirements,
                    "dependencies": task.dependencies,
                    "_shangshuling_task_id": task.id,
                }
            )

        return result

    def _priority_value(self, priority: TaskPriority) -> int:
        """Convert priority to numeric value."""
        values = {
            TaskPriority.CRITICAL: 4,
            TaskPriority.HIGH: 3,
            TaskPriority.MEDIUM: 2,
            TaskPriority.LOW: 1,
        }
        return values.get(priority, 0)

    def record_task_completion(
        self,
        task_id: str,
        executor: str,
        success: bool,
        result: dict[str, Any],
    ) -> bool:
        """Record task completion from Director/ChiefEngineer.

        This is the key interface for executors to report completion.
        PM会验证完成并维护真相源。

        Args:
            task_id: Task ID
            executor: Executor ID
            success: Whether execution succeeded
            result: Execution result

        Returns:
            True if recorded successfully
        """
        resolved_task_id = self.resolve_task_id(task_id)
        if not resolved_task_id:
            return False

        task = self.tasks.get_task(resolved_task_id)
        if task is None:
            return False

        if success:
            # Create verification
            verification = TaskVerification(
                method=result.get("verification_method", "auto_check"),
                evidence=result.get("evidence", "No evidence provided"),
                verified_by=executor,
                details=result.get("details", {}),
            )

            # Complete task
            updated = self.tasks.complete_task(
                resolved_task_id,
                executor,
                verification,
                result_summary=result.get("summary", ""),
                artifacts=result.get("artifacts", []),
            )
            if updated is None:
                updated = self.tasks.update_task(
                    resolved_task_id,
                    changed_by=executor,
                    status=TaskStatus.COMPLETED,
                    result_summary=str(result.get("summary") or ""),
                    verification={
                        "method": verification.method,
                        "evidence": verification.evidence,
                        "verified_by": verification.verified_by,
                        "details": verification.details,
                    },
                )
            return updated is not None
        else:
            # Fail task
            updated = self.tasks.fail_task(
                resolved_task_id,
                executor,
                result.get("error", "Unknown error"),
                result.get("retry_allowed", True),
            )
            if updated is None:
                updated = self.tasks.update_task(
                    resolved_task_id,
                    changed_by=executor,
                    status=TaskStatus.FAILED,
                    result_summary=str(result.get("error") or "Unknown error"),
                )
            return updated is not None

    def resolve_task_id(self, task_id: str) -> str | None:
        """Resolve either canonical task id or legacy id to canonical id."""
        token = str(task_id or "").strip()
        if not token:
            return None

        if self.tasks.get_task(token) is not None:
            return token

        legacy_index = self._build_legacy_task_index()
        return legacy_index.get(token)

    def analyze_project_health(self) -> dict[str, Any]:
        """Analyze overall project health.

        Returns:
            Health analysis report
        """
        # Task health
        task_stats = self.tasks.get_stats_summary()
        task_health = "healthy"
        if task_stats.get("completion_rate", 0) < 0.5:
            task_health = "at_risk"
        elif task_stats.get("failed", 0) > task_stats.get("completed", 0):
            task_health = "critical"

        # Requirements health
        req_coverage = self.requirements.get_coverage_report()
        req_health = "healthy"
        if req_coverage.get("coverage", 0) < 0.3:
            req_health = "at_risk"
        elif req_coverage.get("coverage", 0) < 0.1:
            req_health = "critical"

        # Execution health
        exec_summary = self.execution.get_execution_summary(7)
        exec_health = "healthy"
        if exec_summary.get("success_rate", 1.0) < 0.6:
            exec_health = "at_risk"
        elif exec_summary.get("success_rate", 1.0) < 0.3:
            exec_health = "critical"

        # Overall health
        health_scores = {
            "tasks": task_health,
            "requirements": req_health,
            "execution": exec_health,
        }

        overall = "healthy"
        if "critical" in health_scores.values():
            overall = "critical"
        elif "at_risk" in health_scores.values():
            overall = "at_risk"

        return {
            "overall": overall,
            "components": health_scores,
            "metrics": {
                "task_completion_rate": task_stats.get("completion_rate", 0),
                "requirement_coverage": req_coverage.get("coverage", 0),
                "execution_success_rate": exec_summary.get("success_rate", 0),
            },
            "recommendations": self._generate_health_recommendations(task_health, req_health, exec_health),
        }

    def _generate_health_recommendations(self, task_health: str, req_health: str, exec_health: str) -> list[str]:
        """Generate health recommendations."""
        recommendations = []

        if task_health == "critical":
            recommendations.append("任务失败率过高，建议暂停新增任务，先解决阻塞问题")
        elif task_health == "at_risk":
            recommendations.append("任务完成率偏低，建议审查任务复杂度")

        if req_health == "critical":
            recommendations.append("需求覆盖率极低，需要补充需求文档")
        elif req_health == "at_risk":
            recommendations.append("需求实现进度落后，建议优先实现高优先级需求")

        if exec_health == "critical":
            recommendations.append("执行成功率极低，建议检查执行环境或任务定义")
        elif exec_health == "at_risk":
            recommendations.append("执行成功率偏低，建议优化任务分配策略")

        return recommendations

    def _build_legacy_task_index(self) -> dict[str, str]:
        """Build mapping legacy_id -> canonical task id."""
        index: dict[str, str] = {}
        registry_loader = getattr(self.tasks, "_load_registry", None)
        if not callable(registry_loader):
            return index
        registry = registry_loader()
        tasks = registry.get("tasks", {}) if isinstance(registry, dict) else {}
        for task_id, task_data in tasks.items():
            if not isinstance(task_data, dict):
                continue
            metadata = task_data.get("metadata")
            metadata_map = metadata if isinstance(metadata, dict) else {}
            legacy_id = str(metadata_map.get("legacy_id") or "").strip()
            if legacy_id:
                index[legacy_id] = str(task_id)
        return index

    def _map_legacy_status(self, status: Any) -> TaskStatus:
        token = str(status or "").strip().lower()
        status_map = {
            "todo": TaskStatus.PENDING,
            "pending": TaskStatus.PENDING,
            "assigned": TaskStatus.ASSIGNED,
            "in_progress": TaskStatus.IN_PROGRESS,
            "review": TaskStatus.REVIEW,
            "done": TaskStatus.COMPLETED,
            "completed": TaskStatus.COMPLETED,
            "failed": TaskStatus.FAILED,
            "blocked": TaskStatus.BLOCKED,
            "cancelled": TaskStatus.CANCELLED,
        }
        return status_map.get(token, TaskStatus.PENDING)

    def _map_legacy_priority(self, priority: Any) -> TaskPriority:
        token = str(priority or "").strip().lower()
        for candidate in TaskPriority:
            if candidate.value == token:
                return candidate
        return TaskPriority.MEDIUM

    def _map_assignee_type(self, assignee_type: Any) -> AssigneeType:
        token = str(assignee_type or "").strip()
        for candidate in AssigneeType:
            if candidate.value == token:
                return candidate
        return AssigneeType.DIRECTOR

    def _preserve_terminal_status(
        self,
        current_status: Any,
        target_status: TaskStatus,
    ) -> TaskStatus:
        current = current_status if isinstance(current_status, TaskStatus) else self._map_legacy_status(current_status)
        if current in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED} and target_status in {
            TaskStatus.PENDING,
            TaskStatus.ASSIGNED,
            TaskStatus.IN_PROGRESS,
            TaskStatus.REVIEW,
        }:
            return current
        return target_status

    def _sync_assignment(
        self,
        *,
        task_id: str,
        assignee: str,
        assignee_type: AssigneeType,
    ) -> None:
        if not assignee:
            return

        current = self.tasks.get_task(task_id)
        if current is None:
            return
        if current.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            return
        if current.assignee == assignee and current.assignee_type == assignee_type:
            return
        self.tasks.assign_task(task_id, assignee, assignee_type, assigned_by="legacy_sync")

    def _now_iso_compact(self) -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def generate_comprehensive_report(self, output_dir: str | None = None) -> str:
        """Generate comprehensive project report.

        Args:
            output_dir: Output directory

        Returns:
            Report file path
        """
        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        if output_dir is None:
            output_dir = os.path.join(self.workspace, get_workspace_metadata_dir_name(), "reports")
        os.makedirs(output_dir, exist_ok=True)

        timestamp = json.dumps({}).split(".")[0].replace('"', "")
        report_file = os.path.join(output_dir, f"shangshuling_report_{timestamp}.json")

        report = {
            "generated_at": json.dumps({}).split(".")[0].replace('"', ""),
            "project": self.get_status(),
            "health": self.analyze_project_health(),
            "requirements_coverage": self.requirements.get_coverage_report(),
            "task_stats": self.tasks.get_stats_summary(),
            "execution_summary": self.execution.get_execution_summary(30),
        }

        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        return report_file

    # ===== Document Management API =====

    def list_documents(
        self,
        doc_type: str | None = None,
        pattern: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List all tracked documents.

        Args:
            doc_type: Filter by document type
            pattern: Glob pattern to filter paths
            limit: Maximum number of results
            offset: Pagination offset

        Returns:
            Dictionary with documents list and pagination info
        """
        return self.documents.list_documents(doc_type, pattern, limit, offset)

    def get_document(self, doc_path: str) -> dict[str, Any] | None:
        """Get document information including versions and analysis.

        Args:
            doc_path: Document path

        Returns:
            Document info or None
        """
        return self.documents.get_document_info(doc_path)

    def get_document_content(self, doc_path: str, version: str | None = None) -> str | None:
        """Get document content.

        Args:
            doc_path: Document path
            version: Specific version (default: current)

        Returns:
            Document content or None
        """
        return self.documents.get_version_content(doc_path, version)

    def create_or_update_document(
        self,
        doc_path: str,
        content: str,
        updated_by: str = "pm",
        change_summary: str = "",
    ) -> Any | None:
        """Create or update a document.

        Args:
            doc_path: Document path
            content: Document content
            updated_by: Who made the update
            change_summary: Summary of changes

        Returns:
            Version info or None
        """
        return self.documents.update_document(doc_path, content, updated_by, change_summary)

    def delete_document(self, doc_path: str, delete_file: bool = True) -> bool:
        """Delete a document and its version history.

        Args:
            doc_path: Document path
            delete_file: Whether to delete the actual file

        Returns:
            True if deleted successfully
        """
        return self.documents.delete_document(doc_path, delete_file)

    def search_documents(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search documents by content or path.

        Args:
            query: Search query
            limit: Maximum results

        Returns:
            List of matching documents
        """
        return self.documents.search_documents(query, search_content=True, limit=limit)

    def get_document_versions(self, doc_path: str) -> list[Any]:
        """Get all versions of a document.

        Args:
            doc_path: Document path

        Returns:
            List of versions
        """
        return self.documents.list_versions(doc_path)

    def compare_document_versions(self, doc_path: str, old_version: str, new_version: str) -> Any:
        """Compare two document versions.

        Args:
            doc_path: Document path
            old_version: Old version number
            new_version: New version number

        Returns:
            Diff result
        """
        return self.documents.compare_versions(doc_path, old_version, new_version)

    # ===== Task History API =====

    def get_task_history(
        self,
        task_id: str | None = None,
        assignee: str | None = None,
        status: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Get task history with filtering and pagination.

        Args:
            task_id: Filter by specific task ID
            assignee: Filter by assignee
            status: Filter by status string
            start_date: Start date filter (ISO format)
            end_date: End date filter (ISO format)
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Dictionary with tasks and pagination info
        """
        from polaris.delivery.cli.pm.task_orchestrator import TaskStatus

        task_status = TaskStatus(status) if status else None
        return self.tasks.get_task_history(
            task_id=task_id,
            assignee=assignee,
            status=task_status,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )

    def get_director_task_history(
        self,
        iteration: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Get tasks that were dispatched to Director.

        This retrieves the task list sent to Director in each orchestration iteration.

        Args:
            iteration: Filter by specific PM iteration number
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Dictionary with director tasks and pagination info
        """
        return self.tasks.get_director_task_history(iteration, limit, offset)

    def get_task_assignments(
        self,
        task_id: str | None = None,
        assignee: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get task assignment history.

        Args:
            task_id: Filter by task ID
            assignee: Filter by assignee
            limit: Maximum results

        Returns:
            List of assignment records
        """
        return self.tasks.get_task_assignments(task_id, assignee, limit)

    def search_tasks(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search tasks by title or description.

        Args:
            query: Search query
            limit: Maximum results

        Returns:
            List of matching tasks
        """
        return self.tasks.search_tasks(query, search_description=True, limit=limit)

    def get_task(self, task_id: str) -> Any | None:
        """Get a specific task by ID.

        Args:
            task_id: Task ID

        Returns:
            Task object or None
        """
        return self.tasks.get_task(task_id)

    def list_tasks(
        self,
        status: str | None = None,
        assignee: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List tasks with optional filtering.

        Args:
            status: Filter by status
            assignee: Filter by assignee
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Dictionary with tasks and pagination info
        """

        registry = self.tasks._load_registry()
        tasks = []

        for task_data in registry["tasks"].values():
            if status and task_data["status"] != status:
                continue
            if assignee and task_data.get("assignee") != assignee:
                continue
            tasks.append(dict(task_data))

        # Sort by created_at descending
        tasks.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        total = len(tasks)
        paginated = tasks[offset : offset + limit]

        return {
            "tasks": paginated,
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total,
            },
        }

    # ===== Requirements API =====

    def list_requirements(
        self,
        status: str | None = None,
        priority: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List requirements with optional filtering.

        Args:
            status: Filter by status
            priority: Filter by priority
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Dictionary with requirements and pagination info
        """
        all_reqs = self.requirements.list_requirements()

        filtered = []
        for req in all_reqs:
            if status and req.status.value != status:
                continue
            if priority and req.priority.value != priority:
                continue
            filtered.append(
                {
                    "id": req.id,
                    "title": req.title,
                    "description": req.description,
                    "status": req.status.value,
                    "priority": req.priority.value,
                    "source_doc": req.source,
                    "created_at": req.created_at,
                    "updated_at": req.updated_at,
                    "tasks": req.tasks,
                }
            )

        total = len(filtered)
        paginated = filtered[offset : offset + limit]

        return {
            "requirements": paginated,
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total,
            },
        }

    def get_requirement(self, req_id: str) -> dict[str, Any] | None:
        """Get a specific requirement by ID.

        Args:
            req_id: Requirement ID

        Returns:
            Requirement dict or None
        """
        req = self.requirements.get_requirement(req_id)
        if req is None:
            return None

        return {
            "id": req.id,
            "title": req.title,
            "description": req.description,
            "status": req.status.value,
            "priority": req.priority.value,
            "type": req.req_type.value if req.req_type else None,
            "source_doc": req.source,
            "source_section": req.source_section,
            "created_at": req.created_at,
            "updated_at": req.updated_at,
            "tasks": req.tasks,
            "verification_criteria": req.metadata.get("verification_criteria") if req.metadata else None,
        }


# Global instance cache
_pm_instances: dict[str, PM] = {}


def get_pm(workspace: str) -> PM:
    """Get or create PM instance.

    Args:
        workspace: Workspace path

    Returns:
        PM instance
    """
    workspace_abs = os.path.abspath(workspace)
    if workspace_abs not in _pm_instances:
        _pm_instances[workspace_abs] = PM(workspace_abs)
    return _pm_instances[workspace_abs]


def reset_pm(workspace: str) -> None:
    """Reset PM instance.

    Args:
        workspace: Workspace path
    """
    workspace_abs = os.path.abspath(workspace)
    _pm_instances.pop(workspace_abs, None)
