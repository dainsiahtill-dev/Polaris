"""Requirements Tracker - PM需求追踪器

实现需求追踪矩阵，双向追踪需求↔文档↔任务↔代码，
维护需求实现状态和变更历史。
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from polaris.delivery.cli.pm.state_manager import (
    _generate_id,
    get_state_manager,
)
from polaris.delivery.cli.pm.task_orchestrator import TaskStatus
from polaris.kernelone.utils import utc_now_str

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Get current ISO format timestamp."""
    return utc_now_str()


class RequirementStatus(str, Enum):
    """需求实现状态"""

    PENDING = "pending"  # 待定义
    DEFINED = "defined"  # 已定义
    IN_PROGRESS = "in_progress"  # 实现中
    IMPLEMENTED = "implemented"  # 已实现
    VERIFIED = "verified"  # 已验证
    DEPRECATED = "deprecated"  # 已废弃


class RequirementPriority(str, Enum):
    """需求优先级"""

    CRITICAL = "critical"  # 关键
    HIGH = "high"  # 高
    MEDIUM = "medium"  # 中
    LOW = "low"  # 低


class RequirementType(str, Enum):
    """需求类型"""

    FUNCTIONAL = "functional"  # 功能性需求
    NON_FUNCTIONAL = "non_functional"  # 非功能性需求
    TECHNICAL = "technical"  # 技术需求
    BUSINESS = "business"  # 业务需求
    INTERFACE = "interface"  # 接口需求


@dataclass
class Requirement:
    """需求实体"""

    id: str  # 需求ID (如 REQ-001)
    title: str  # 需求标题
    description: str  # 需求描述
    status: RequirementStatus  # 实现状态
    priority: RequirementPriority  # 优先级
    req_type: RequirementType  # 需求类型
    source: str  # 来源文档路径
    source_section: str  # 文档章节
    tasks: list[str] = field(default_factory=list)  # 关联任务IDs
    code_refs: list[str] = field(default_factory=list)  # 代码引用
    dependencies: list[str] = field(default_factory=list)  # 依赖的需求IDs
    tags: list[str] = field(default_factory=list)  # 标签
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    implemented_at: str | None = None
    verified_at: str | None = None
    verified_by: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RequirementChange:
    """需求变更记录"""

    change_id: str
    req_id: str
    field: str  # 变更字段
    old_value: Any  # 旧值
    new_value: Any  # 新值
    changed_by: str  # 变更者
    changed_at: str = field(default_factory=_now_iso)
    reason: str = ""  # 变更原因


@dataclass
class TraceabilityLink:
    """追踪链路"""

    req_id: str
    target_type: str  # task, code, test, doc
    target_id: str  # 目标ID
    target_ref: str  # 目标引用(如文件路径)
    created_at: str = field(default_factory=_now_iso)


class RequirementsTracker:
    """PM需求追踪器

    核心功能：
    1. 需求注册表管理 - 完整的注册和查询接口
    2. 需求追踪矩阵 - 需求 ↔ 文档段落 ↔ 任务 ↔ 代码文件
    3. 需求实现状态 - pending/defined/in_progress/implemented/verified
    4. 需求变更历史 - 谁在何时改了什么
    5. 依赖管理 - 需求间的依赖关系
    6. 覆盖率分析 - 统计需求实现覆盖率

    存储结构：
        pm_data/requirements/
        ├── registry.json          # 需求注册表
        ├── matrix.json            # 需求追踪矩阵
        ├── dependencies.json      # 依赖关系图
        └── history/               # 变更历史
            └── YYYY-MM-DD.jsonl
    """

    REGISTRY_FILE = "registry.json"
    MATRIX_FILE = "matrix.json"
    DEPENDENCIES_FILE = "dependencies.json"

    def __init__(self, workspace: str) -> None:
        """Initialize Requirements Tracker.

        Args:
            workspace: Workspace root path
        """
        self.workspace = workspace
        self.state_manager = get_state_manager(workspace)
        self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        """Ensure requirements subsystem is initialized."""
        if not self.state_manager.is_initialized():
            self.state_manager.initialize()

    def _get_requirements_dir(self) -> str:
        """Get requirements data directory."""
        return self.state_manager.get_data_path("requirements", "")

    def _load_registry(self) -> dict[str, Any]:
        """Load requirements registry."""
        data = self.state_manager.read_subsystem_data("requirements", self.REGISTRY_FILE)
        if data is None:
            return {"version": "1.0", "requirements": {}, "next_id": 1}
        return data

    def _save_registry(self, registry: dict[str, Any]) -> None:
        """Save requirements registry."""
        self.state_manager.write_subsystem_data("requirements", self.REGISTRY_FILE, registry)

    def _load_matrix(self) -> dict[str, Any]:
        """Load traceability matrix."""
        data = self.state_manager.read_subsystem_data("requirements", self.MATRIX_FILE)
        if data is None:
            return {"version": "1.0", "matrix": {}, "dependencies": {}}
        return data

    def _save_matrix(self, matrix: dict[str, Any]) -> None:
        """Save traceability matrix."""
        self.state_manager.write_subsystem_data("requirements", self.MATRIX_FILE, matrix)

    def _load_dependencies(self) -> dict[str, Any]:
        """Load dependencies graph."""
        data = self.state_manager.read_subsystem_data("requirements", self.DEPENDENCIES_FILE)
        if data is None:
            return {"version": "1.0", "dependencies": {}, "reverse_deps": {}}
        return data

    def _save_dependencies(self, deps: dict[str, Any]) -> None:
        """Save dependencies graph."""
        self.state_manager.write_subsystem_data("requirements", self.DEPENDENCIES_FILE, deps)

    def _record_change(
        self,
        req_id: str,
        field: str,
        old_value: Any,
        new_value: Any,
        changed_by: str = "pm",
        reason: str = "",
    ) -> None:
        """Record requirement change to history."""
        change = {
            "change_id": _generate_id("CHG"),
            "req_id": req_id,
            "field": field,
            "old_value": old_value,
            "new_value": new_value,
            "changed_by": changed_by,
            "changed_at": _now_iso(),
            "reason": reason,
        }
        self.state_manager.append_to_history("requirements", change)

    def _generate_req_id(self, registry: dict[str, Any]) -> str:
        """Generate new requirement ID."""
        next_num = registry.get("next_id", 1)
        req_id = f"REQ-{next_num:04d}"
        registry["next_id"] = next_num + 1
        return req_id

    def register_requirement(
        self,
        title: str,
        description: str,
        source: str,
        source_section: str = "",
        priority: RequirementPriority = RequirementPriority.MEDIUM,
        req_type: RequirementType = RequirementType.FUNCTIONAL,
        dependencies: list[str] | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Requirement:
        """Register a new requirement.

        Args:
            title: Requirement title
            description: Requirement description
            source: Source document path
            source_section: Document section reference
            priority: Priority level
            req_type: Requirement type
            dependencies: List of dependent requirement IDs
            tags: List of tags
            metadata: Additional metadata

        Returns:
            Registered requirement
        """
        registry = self._load_registry()

        req_id = self._generate_req_id(registry)

        requirement = Requirement(
            id=req_id,
            title=title,
            description=description,
            status=RequirementStatus.DEFINED,
            priority=priority,
            req_type=req_type,
            source=source,
            source_section=source_section,
            dependencies=dependencies or [],
            tags=tags or [],
            metadata=metadata or {},
        )

        # Save to registry
        registry["requirements"][req_id] = asdict(requirement)
        self._save_registry(registry)

        # Initialize matrix entry
        matrix = self._load_matrix()
        matrix["matrix"][req_id] = {
            "tasks": [],
            "code_refs": [],
            "tests": [],
            "docs": [source],
        }
        self._save_matrix(matrix)

        # Record dependencies
        if dependencies:
            self._update_dependencies(req_id, dependencies)

        # Record change
        self._record_change(req_id, "created", None, title, reason="Requirement registered")

        # Update stats
        self.state_manager.increment_stat("total_requirements")

        return requirement

    def get_requirement(self, req_id: str) -> Requirement | None:
        """Get requirement by ID.

        Args:
            req_id: Requirement ID

        Returns:
            Requirement or None
        """
        registry = self._load_registry()
        data = registry["requirements"].get(req_id)
        if data is None:
            return None
        return Requirement(**data)

    def update_requirement(
        self,
        req_id: str,
        changed_by: str = "pm",
        reason: str = "",
        **updates: Any,
    ) -> Requirement | None:
        """Update requirement fields.

        Args:
            req_id: Requirement ID
            changed_by: Who made the change
            reason: Change reason
            **updates: Fields to update

        Returns:
            Updated requirement or None
        """
        registry = self._load_registry()
        data = registry["requirements"].get(req_id)
        if data is None:
            return None

        # Record changes for each field
        for field_name, new_value in updates.items():
            if field_name in data and data[field_name] != new_value:
                old_value = data[field_name]
                data[field_name] = new_value
                self._record_change(req_id, field_name, old_value, new_value, changed_by, reason)

        data["updated_at"] = _now_iso()
        self._save_registry(registry)

        return Requirement(**data)

    def update_status(
        self,
        req_id: str,
        new_status: RequirementStatus,
        changed_by: str = "pm",
        reason: str = "",
    ) -> Requirement | None:
        """Update requirement status.

        Args:
            req_id: Requirement ID
            new_status: New status
            changed_by: Who made the change
            reason: Change reason

        Returns:
            Updated requirement or None
        """
        registry = self._load_registry()
        data = registry["requirements"].get(req_id)
        if data is None:
            return None

        old_status = data["status"]
        if old_status == new_status.value:
            return Requirement(**data)

        data["status"] = new_status.value
        data["updated_at"] = _now_iso()

        # Set timestamps for specific statuses
        if new_status == RequirementStatus.IMPLEMENTED:
            data["implemented_at"] = _now_iso()
        elif new_status == RequirementStatus.VERIFIED:
            data["verified_at"] = _now_iso()
            data["verified_by"] = changed_by

        self._save_registry(registry)
        self._record_change(req_id, "status", old_status, new_status.value, changed_by, reason)

        # Update stats if completed
        if new_status == RequirementStatus.VERIFIED:
            self.state_manager.increment_stat("total_requirements_implemented")

        return Requirement(**data)

    def link_task(self, req_id: str, task_id: str) -> bool:
        """Link a task to requirement.

        Args:
            req_id: Requirement ID
            task_id: Task ID

        Returns:
            True if successful
        """
        registry = self._load_registry()
        if req_id not in registry["requirements"]:
            return False

        # Update requirement
        req_data = registry["requirements"][req_id]
        if task_id not in req_data["tasks"]:
            req_data["tasks"].append(task_id)
            req_data["updated_at"] = _now_iso()
            self._save_registry(registry)

        # Update matrix
        matrix = self._load_matrix()
        if req_id not in matrix["matrix"]:
            matrix["matrix"][req_id] = {"tasks": [], "code_refs": [], "tests": [], "docs": []}
        if task_id not in matrix["matrix"][req_id]["tasks"]:
            matrix["matrix"][req_id]["tasks"].append(task_id)
            self._save_matrix(matrix)

        return True

    def unlink_task(self, req_id: str, task_id: str) -> bool:
        """Unlink a task from requirement.

        Args:
            req_id: Requirement ID
            task_id: Task ID

        Returns:
            True if successful
        """
        registry = self._load_registry()
        if req_id not in registry["requirements"]:
            return False

        req_data = registry["requirements"][req_id]
        if task_id in req_data["tasks"]:
            req_data["tasks"].remove(task_id)
            req_data["updated_at"] = _now_iso()
            self._save_registry(registry)

        matrix = self._load_matrix()
        if req_id in matrix["matrix"] and task_id in matrix["matrix"][req_id]["tasks"]:
            matrix["matrix"][req_id]["tasks"].remove(task_id)
            self._save_matrix(matrix)

        return True

    def link_code(self, req_id: str, code_ref: str) -> bool:
        """Link code reference to requirement.

        Args:
            req_id: Requirement ID
            code_ref: Code reference (e.g., "src/auth.py:45-120")

        Returns:
            True if successful
        """
        registry = self._load_registry()
        if req_id not in registry["requirements"]:
            return False

        req_data = registry["requirements"][req_id]
        if code_ref not in req_data["code_refs"]:
            req_data["code_refs"].append(code_ref)
            req_data["updated_at"] = _now_iso()
            self._save_registry(registry)

        matrix = self._load_matrix()
        if req_id not in matrix["matrix"]:
            matrix["matrix"][req_id] = {"tasks": [], "code_refs": [], "tests": [], "docs": []}
        if code_ref not in matrix["matrix"][req_id]["code_refs"]:
            matrix["matrix"][req_id]["code_refs"].append(code_ref)
            self._save_matrix(matrix)

        return True

    def _update_dependencies(self, req_id: str, dependencies: list[str]) -> None:
        """Update requirement dependencies."""
        deps_data = self._load_dependencies()

        # Update forward dependencies
        deps_data["dependencies"][req_id] = dependencies

        # Update reverse dependencies
        for dep_id in dependencies:
            if dep_id not in deps_data["reverse_deps"]:
                deps_data["reverse_deps"][dep_id] = []
            if req_id not in deps_data["reverse_deps"][dep_id]:
                deps_data["reverse_deps"][dep_id].append(req_id)

        self._save_dependencies(deps_data)

        # Update matrix
        matrix = self._load_matrix()
        matrix["dependencies"][req_id] = dependencies
        self._save_matrix(matrix)

    def get_dependencies(self, req_id: str) -> list[str]:
        """Get requirement dependencies.

        Args:
            req_id: Requirement ID

        Returns:
            List of dependency requirement IDs
        """
        deps_data = self._load_dependencies()
        return deps_data["dependencies"].get(req_id, [])

    def get_reverse_dependencies(self, req_id: str) -> list[str]:
        """Get requirements that depend on this one.

        Args:
            req_id: Requirement ID

        Returns:
            List of dependent requirement IDs
        """
        deps_data = self._load_dependencies()
        return deps_data["reverse_deps"].get(req_id, [])

    def check_dependency_satisfaction(self, req_id: str) -> tuple[bool, list[str]]:
        """Check if all dependencies are satisfied.

        Args:
            req_id: Requirement ID

        Returns:
            (is_satisfied, list_of_unsatisfied_deps)
        """
        dependencies = self.get_dependencies(req_id)
        unsatisfied = []

        for dep_id in dependencies:
            dep = self.get_requirement(dep_id)
            if dep is None or dep.status not in [RequirementStatus.IMPLEMENTED, RequirementStatus.VERIFIED]:
                unsatisfied.append(dep_id)

        return len(unsatisfied) == 0, unsatisfied

    def list_requirements(
        self,
        status: RequirementStatus | None = None,
        priority: RequirementPriority | None = None,
        req_type: RequirementType | None = None,
        tag: str | None = None,
    ) -> list[Requirement]:
        """List requirements with optional filtering.

        Args:
            status: Filter by status
            priority: Filter by priority
            req_type: Filter by type
            tag: Filter by tag

        Returns:
            List of requirements
        """
        registry = self._load_registry()
        results = []

        for req_data in registry["requirements"].values():
            if status and req_data["status"] != status.value:
                continue
            if priority and req_data["priority"] != priority.value:
                continue
            if req_type and req_data["req_type"] != req_type.value:
                continue
            if tag and tag not in req_data.get("tags", []):
                continue
            results.append(Requirement(**req_data))

        return results

    def get_requirements_by_source(self, source: str) -> list[Requirement]:
        """Get requirements from a specific source document.

        Args:
            source: Source document path

        Returns:
            List of requirements
        """
        registry = self._load_registry()
        results = []

        for req_data in registry["requirements"].values():
            if req_data["source"] == source:
                results.append(Requirement(**req_data))

        return results

    def get_implementation_status(self, req_id: str) -> dict[str, Any]:
        """Get detailed implementation status for a requirement.

        Args:
            req_id: Requirement ID

        Returns:
            Status dictionary
        """
        req = self.get_requirement(req_id)
        if req is None:
            return {"error": "Requirement not found"}

        matrix = self._load_matrix()
        req_matrix = matrix["matrix"].get(req_id, {})

        deps_satisfied, unsatisfied_deps = self.check_dependency_satisfaction(req_id)

        return {
            "req_id": req_id,
            "title": req.title,
            "status": req.status.value,
            "tasks_total": len(req.tasks),
            "tasks_completed": len([t for t in req.tasks if self._is_task_completed(t)]),
            "code_refs_count": len(req.code_refs),
            "dependencies_satisfied": deps_satisfied,
            "unsatisfied_dependencies": unsatisfied_deps,
            "matrix": req_matrix,
        }

    def _is_task_completed(self, task_id: str) -> bool:
        """Check if a task is completed."""
        # Import here to avoid circular dependency
        from polaris.delivery.cli.pm.task_orchestrator import get_task_orchestrator

        try:
            orchestrator = get_task_orchestrator(self.workspace)
            task = orchestrator.get_task(task_id)
            if task:
                return task.status == TaskStatus.COMPLETED
        except (RuntimeError, ValueError):
            logger.debug("DEBUG: requirements_tracker.py:{616} {exc} (swallowed)")
        return False

    def get_coverage_report(self) -> dict[str, Any]:
        """Get requirements coverage report.

        Returns:
            Coverage statistics
        """
        registry = self._load_registry()
        total = len(registry["requirements"])

        if total == 0:
            return {
                "total": 0,
                "coverage": 0.0,
                "by_status": {},
                "by_priority": {},
            }

        by_status = {s.value: 0 for s in RequirementStatus}
        by_priority = {p.value: 0 for p in RequirementPriority}
        implemented = 0
        verified = 0
        with_tasks = 0
        with_code = 0

        for req_data in registry["requirements"].values():
            status = req_data["status"]
            priority = req_data["priority"]
            by_status[status] = by_status.get(status, 0) + 1
            by_priority[priority] = by_priority.get(priority, 0) + 1

            if status in [RequirementStatus.IMPLEMENTED.value, RequirementStatus.VERIFIED.value]:
                implemented += 1
            if status == RequirementStatus.VERIFIED.value:
                verified += 1
            if req_data.get("tasks"):
                with_tasks += 1
            if req_data.get("code_refs"):
                with_code += 1

        return {
            "total": total,
            "coverage": round(verified / total * 100, 2),
            "by_status": by_status,
            "by_priority": by_priority,
            "implemented": implemented,
            "verified": verified,
            "with_tasks": with_tasks,
            "with_code": with_code,
        }

    def get_traceability_matrix(self) -> dict[str, Any]:
        """Get full traceability matrix.

        Returns:
            Traceability matrix
        """
        return self._load_matrix()

    def get_change_history(
        self,
        req_id: str | None = None,
        date_str: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get requirement change history.

        Args:
            req_id: Filter by requirement ID
            date_str: Filter by date (YYYY-MM-DD)
            limit: Max records

        Returns:
            List of change records
        """
        records = self.state_manager.read_history("requirements", date_str, limit)

        if req_id:
            records = [r for r in records if r.get("req_id") == req_id]

        return records

    def delete_requirement(self, req_id: str, reason: str = "") -> bool:
        """Delete a requirement (soft delete by marking deprecated).

        Args:
            req_id: Requirement ID
            reason: Deletion reason

        Returns:
            True if successful
        """
        req = self.get_requirement(req_id)
        if req is None:
            return False

        self.update_status(req_id, RequirementStatus.DEPRECATED, "pm", reason)
        return True

    def parse_requirements_from_doc(
        self,
        doc_path: str,
        doc_content: str,
        auto_register: bool = False,
    ) -> list[dict[str, Any]]:
        """Parse requirements from document content.

        Extracts potential requirements based on patterns like:
        - REQ: ...
        - Requirement: ...
        - [REQ-XXX] ...

        Args:
            doc_path: Document path
            doc_content: Document content
            auto_register: Auto-register found requirements

        Returns:
            List of parsed requirements
        """
        patterns = [
            r"REQ:\s*(.+?)(?:\n|$)",
            r"Requirement:\s*(.+?)(?:\n|$)",
            r"需求:\s*(.+?)(?:\n|$)",
            r"【需求】\s*(.+?)(?:\n|$)",
        ]

        found = []
        lines = doc_content.split("\n")

        for i, line in enumerate(lines):
            for pattern in patterns:
                matches = re.finditer(pattern, line, re.IGNORECASE)
                for match in matches:
                    title = match.group(1).strip()
                    # Get context (surrounding lines)
                    context_start = max(0, i - 2)
                    context_end = min(len(lines), i + 3)
                    context = "\n".join(lines[context_start:context_end])

                    req_info = {
                        "title": title,
                        "description": context,
                        "source": doc_path,
                        "source_section": f"line-{i + 1}",
                        "line_number": i + 1,
                    }
                    found.append(req_info)

                    if auto_register:
                        self.register_requirement(
                            title=title,
                            description=context,
                            source=doc_path,
                            source_section=f"line-{i + 1}",
                        )

        return found

    def export_to_json(self, output_path: str | None = None) -> str:
        """Export all requirements to JSON.

        Args:
            output_path: Output file path

        Returns:
            Output file path
        """
        registry = self._load_registry()
        matrix = self._load_matrix()

        export_data = {
            "exported_at": _now_iso(),
            "requirements": registry["requirements"],
            "matrix": matrix["matrix"],
            "dependencies": matrix["dependencies"],
            "coverage": self.get_coverage_report(),
        }

        if output_path is None:
            from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

            output_path = os.path.join(
                self.workspace,
                get_workspace_metadata_dir_name(),
                "requirements_export.json",
            )

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)

        return output_path


# Global instance cache
_tracker_instances: dict[str, RequirementsTracker] = {}


def get_requirements_tracker(workspace: str) -> RequirementsTracker:
    """Get or create RequirementsTracker instance.

    Args:
        workspace: Workspace path

    Returns:
        RequirementsTracker instance
    """
    workspace_abs = os.path.abspath(workspace)
    if workspace_abs not in _tracker_instances:
        _tracker_instances[workspace_abs] = RequirementsTracker(workspace_abs)
    return _tracker_instances[workspace_abs]


def reset_requirements_tracker(workspace: str) -> None:
    """Reset tracker instance for workspace.

    Args:
        workspace: Workspace path
    """
    workspace_abs = os.path.abspath(workspace)
    _tracker_instances.pop(workspace_abs, None)
