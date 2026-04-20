"""Export Artifacts - 导出产物定义

定义 Workbench 会话的标准导出格式：
- PM 合同草案 (pm_task_draft)
- 计划笔记 (plan_notes)
- 执行笔记 (execution_notes)
- 补丁摘要 (patch_summary)
- QA 审查草案 (qa_audit_draft)

这些产物可以被 Workflow 节点导入和使用。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ExportType(str, Enum):
    """导出类型枚举"""

    PM_TASK_DRAFT = "pm_task_draft"
    PLAN_NOTES = "plan_notes"
    EXECUTION_NOTES = "execution_notes"
    PATCH_SUMMARY = "patch_summary"
    QA_AUDIT_DRAFT = "qa_audit_draft"
    BLUEPRINT = "blueprint"
    QA_MEMO = "qa_memo"


class ExportFormat(str, Enum):
    """导出格式枚举"""

    JSON = "json"
    MARKDOWN = "markdown"


@dataclass
class PMTaskDraft:
    """PM 任务合同草案"""

    title: str
    description: str
    priority: str = "medium"
    estimated_hours: float | None = None
    dependencies: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class PlanNotes:
    """计划笔记"""

    summary: str
    goals: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    timeline: str = ""
    resources: list[str] = field(default_factory=list)


@dataclass
class ExecutionNotes:
    """执行笔记"""

    changes_made: list[str] = field(default_factory=list)
    commands_executed: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    tests_run: list[str] = field(default_factory=list)
    issues_found: list[str] = field(default_factory=list)


@dataclass
class PatchSummary:
    """补丁摘要"""

    description: str
    files_changed: list[str] = field(default_factory=list)
    lines_added: int = 0
    lines_deleted: int = 0
    breaking_changes: list[str] = field(default_factory=list)
    verification_steps: list[str] = field(default_factory=list)


@dataclass
class QAAuditDraft:
    """QA 审查草案"""

    target: str
    test_results: dict[str, Any] = field(default_factory=dict)
    issues_found: list[dict[str, Any]] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    severity_summary: dict[str, int] = field(default_factory=dict)


@dataclass
class RoleSessionExport:
    """完整的会话导出"""

    session_id: str
    role: str
    host_kind: str
    workspace: str
    created_at: str
    exported_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # 产物内容
    pm_task_draft: PMTaskDraft | None = None
    plan_notes: PlanNotes | None = None
    execution_notes: ExecutionNotes | None = None
    patch_summary: PatchSummary | None = None
    qa_audit_draft: QAAuditDraft | None = None

    # 原始消息
    messages: list[dict[str, Any]] = field(default_factory=list)

    # 元数据
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        result = {}
        for key, value in asdict(self).items():
            if value is None:
                continue
            if hasattr(value, "to_dict"):
                result[key] = value.to_dict()
            else:
                result[key] = value
        return result

    def to_markdown(self) -> str:
        """转换为 Markdown 格式"""
        md = "# Session Export\n\n"
        md += f"- Session ID: {self.session_id}\n"
        md += f"- Role: {self.role}\n"
        md += f"- Host: {self.host_kind}\n"
        md += f"- Workspace: {self.workspace}\n"
        md += f"- Created: {self.created_at}\n"
        md += f"- Exported: {self.exported_at}\n\n"

        if self.pm_task_draft:
            md += "## PM Task Draft\n\n"
            md += f"- Title: {self.pm_task_draft.title}\n"
            md += f"- Description: {self.pm_task_draft.description}\n"
            md += f"- Priority: {self.pm_task_draft.priority}\n\n"

        if self.plan_notes:
            md += "## Plan Notes\n\n"
            md += f"{self.plan_notes.summary}\n\n"

        if self.execution_notes:
            md += "## Execution Notes\n\n"
            md += "### Changes Made\n"
            for change in self.execution_notes.changes_made:
                md += f"- {change}\n"
            md += "\n"

        if self.patch_summary:
            md += "## Patch Summary\n\n"
            md += f"{self.patch_summary.description}\n\n"
            md += "### Files Changed\n"
            for f in self.patch_summary.files_changed:
                md += f"- {f}\n"
            md += f"\n- Lines added: {self.patch_summary.lines_added}\n"
            md += f"- Lines deleted: {self.patch_summary.lines_deleted}\n\n"

        if self.qa_audit_draft:
            md += "## QA Audit Draft\n\n"
            md += f"- Target: {self.qa_audit_draft.target}\n\n"
            md += "### Issues Found\n"
            for issue in self.qa_audit_draft.issues_found:
                md += f"- {issue.get('description', 'N/A')}\n"
            md += "\n"

        if self.messages:
            md += "## Transcript\n\n"
            for msg in self.messages:
                md += f"### {msg.get('role', 'unknown')}\n\n"
                md += f"{msg.get('content', '')}\n\n"

        return md


# ==================== Factory Functions ====================


def create_pm_task_draft(
    title: str,
    description: str,
    priority: str = "medium",
    estimated_hours: float | None = None,
    dependencies: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
    notes: str = "",
) -> PMTaskDraft:
    """创建 PM 任务草案"""
    return PMTaskDraft(
        title=title,
        description=description,
        priority=priority,
        estimated_hours=estimated_hours,
        dependencies=dependencies or [],
        acceptance_criteria=acceptance_criteria or [],
        notes=notes,
    )


def create_plan_notes(
    summary: str,
    goals: list[str] | None = None,
    risks: list[str] | None = None,
    timeline: str = "",
    resources: list[str] | None = None,
) -> PlanNotes:
    """创建计划笔记"""
    return PlanNotes(
        summary=summary,
        goals=goals or [],
        risks=risks or [],
        timeline=timeline,
        resources=resources or [],
    )


def create_execution_notes(
    changes_made: list[str] | None = None,
    commands_executed: list[str] | None = None,
    files_modified: list[str] | None = None,
    tests_run: list[str] | None = None,
    issues_found: list[str] | None = None,
) -> ExecutionNotes:
    """创建执行笔记"""
    return ExecutionNotes(
        changes_made=changes_made or [],
        commands_executed=commands_executed or [],
        files_modified=files_modified or [],
        tests_run=tests_run or [],
        issues_found=issues_found or [],
    )


def create_patch_summary(
    description: str,
    files_changed: list[str] | None = None,
    lines_added: int = 0,
    lines_deleted: int = 0,
    breaking_changes: list[str] | None = None,
    verification_steps: list[str] | None = None,
) -> PatchSummary:
    """创建补丁摘要"""
    return PatchSummary(
        description=description,
        files_changed=files_changed or [],
        lines_added=lines_added,
        lines_deleted=lines_deleted,
        breaking_changes=breaking_changes or [],
        verification_steps=verification_steps or [],
    )


def create_qa_audit_draft(
    target: str,
    test_results: dict[str, Any] | None = None,
    issues_found: list[dict[str, Any]] | None = None,
    recommendations: list[str] | None = None,
    severity_summary: dict[str, int] | None = None,
) -> QAAuditDraft:
    """创建 QA 审查草案"""
    return QAAuditDraft(
        target=target,
        test_results=test_results or {},
        issues_found=issues_found or [],
        recommendations=recommendations or [],
        severity_summary=severity_summary or {},
    )


def parse_export_type(export_type: str) -> ExportType:
    """解析导出类型"""
    try:
        return ExportType(export_type)
    except ValueError:
        # 默认返回 None，让调用者处理
        return ExportType.PM_TASK_DRAFT
