"""Comprehensive tests for polaris.domain.entities.export_artifacts."""

from __future__ import annotations

import pytest
from polaris.domain.entities.export_artifacts import (
    ExecutionNotes,
    ExportFormat,
    ExportType,
    PatchSummary,
    PlanNotes,
    PMTaskDraft,
    QAAuditDraft,
    RoleSessionExport,
    create_execution_notes,
    create_patch_summary,
    create_plan_notes,
    create_pm_task_draft,
    create_qa_audit_draft,
    parse_export_type,
)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestExportType:
    def test_members(self):
        assert ExportType.PM_TASK_DRAFT.value == "pm_task_draft"
        assert ExportType.PLAN_NOTES.value == "plan_notes"
        assert ExportType.EXECUTION_NOTES.value == "execution_notes"
        assert ExportType.PATCH_SUMMARY.value == "patch_summary"
        assert ExportType.QA_AUDIT_DRAFT.value == "qa_audit_draft"
        assert ExportType.BLUEPRINT.value == "blueprint"
        assert ExportType.QA_MEMO.value == "qa_memo"

    def test_from_value(self):
        assert ExportType("plan_notes") == ExportType.PLAN_NOTES

    def test_from_invalid_value_raises(self):
        with pytest.raises(ValueError):
            ExportType("unknown")


class TestExportFormat:
    def test_members(self):
        assert ExportFormat.JSON.value == "json"
        assert ExportFormat.MARKDOWN.value == "markdown"


# ---------------------------------------------------------------------------
# Dataclass creation
# ---------------------------------------------------------------------------


class TestPMTaskDraft:
    def test_defaults(self):
        d = PMTaskDraft(title="Fix bug", description="Fix the login bug")
        assert d.title == "Fix bug"
        assert d.description == "Fix the login bug"
        assert d.priority == "medium"
        assert d.estimated_hours is None
        assert d.dependencies == []
        assert d.acceptance_criteria == []
        assert d.notes == ""

    def test_full_creation(self):
        d = PMTaskDraft(
            title="Feature",
            description="Add feature",
            priority="high",
            estimated_hours=4.5,
            dependencies=["dep1"],
            acceptance_criteria=["criteria1"],
            notes="some notes",
        )
        assert d.priority == "high"
        assert d.estimated_hours == 4.5
        assert d.dependencies == ["dep1"]
        assert d.acceptance_criteria == ["criteria1"]
        assert d.notes == "some notes"


class TestPlanNotes:
    def test_defaults(self):
        pn = PlanNotes(summary="Plan summary")
        assert pn.summary == "Plan summary"
        assert pn.goals == []
        assert pn.risks == []
        assert pn.timeline == ""
        assert pn.resources == []

    def test_full_creation(self):
        pn = PlanNotes(
            summary="Plan",
            goals=["goal1"],
            risks=["risk1"],
            timeline="2 weeks",
            resources=["dev1"],
        )
        assert pn.goals == ["goal1"]
        assert pn.risks == ["risk1"]
        assert pn.timeline == "2 weeks"
        assert pn.resources == ["dev1"]


class TestExecutionNotes:
    def test_defaults(self):
        en = ExecutionNotes()
        assert en.changes_made == []
        assert en.commands_executed == []
        assert en.files_modified == []
        assert en.tests_run == []
        assert en.issues_found == []

    def test_full_creation(self):
        en = ExecutionNotes(
            changes_made=["change1"],
            commands_executed=["cmd1"],
            files_modified=["file1.py"],
            tests_run=["test1"],
            issues_found=["issue1"],
        )
        assert en.changes_made == ["change1"]
        assert en.commands_executed == ["cmd1"]
        assert en.files_modified == ["file1.py"]
        assert en.tests_run == ["test1"]
        assert en.issues_found == ["issue1"]


class TestPatchSummary:
    def test_defaults(self):
        ps = PatchSummary(description="Summary")
        assert ps.description == "Summary"
        assert ps.files_changed == []
        assert ps.lines_added == 0
        assert ps.lines_deleted == 0
        assert ps.breaking_changes == []
        assert ps.verification_steps == []

    def test_full_creation(self):
        ps = PatchSummary(
            description="Summary",
            files_changed=["a.py"],
            lines_added=10,
            lines_deleted=5,
            breaking_changes=["api change"],
            verification_steps=["run tests"],
        )
        assert ps.files_changed == ["a.py"]
        assert ps.lines_added == 10
        assert ps.lines_deleted == 5
        assert ps.breaking_changes == ["api change"]
        assert ps.verification_steps == ["run tests"]


class TestQAAuditDraft:
    def test_defaults(self):
        qa = QAAuditDraft(target="module.py")
        assert qa.target == "module.py"
        assert qa.test_results == {}
        assert qa.issues_found == []
        assert qa.recommendations == []
        assert qa.severity_summary == {}

    def test_full_creation(self):
        qa = QAAuditDraft(
            target="module.py",
            test_results={"passed": 10},
            issues_found=[{"description": "bug"}],
            recommendations=["fix it"],
            severity_summary={"high": 1},
        )
        assert qa.test_results == {"passed": 10}
        assert qa.issues_found == [{"description": "bug"}]
        assert qa.recommendations == ["fix it"]
        assert qa.severity_summary == {"high": 1}


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


class TestCreatePMTaskDraft:
    def test_with_none_lists(self):
        d = create_pm_task_draft(title="T", description="D", dependencies=None, acceptance_criteria=None)
        assert d.dependencies == []
        assert d.acceptance_criteria == []

    def test_with_values(self):
        d = create_pm_task_draft(
            title="T",
            description="D",
            priority="high",
            estimated_hours=2.0,
            dependencies=["dep1"],
            acceptance_criteria=["ac1"],
            notes="notes",
        )
        assert d.title == "T"
        assert d.priority == "high"
        assert d.estimated_hours == 2.0
        assert d.dependencies == ["dep1"]
        assert d.acceptance_criteria == ["ac1"]
        assert d.notes == "notes"


class TestCreatePlanNotes:
    def test_with_none_lists(self):
        pn = create_plan_notes(summary="S", goals=None, risks=None, resources=None)
        assert pn.goals == []
        assert pn.risks == []
        assert pn.resources == []

    def test_with_values(self):
        pn = create_plan_notes(
            summary="S",
            goals=["g1"],
            risks=["r1"],
            timeline="1 week",
            resources=["res1"],
        )
        assert pn.summary == "S"
        assert pn.goals == ["g1"]
        assert pn.timeline == "1 week"


class TestCreateExecutionNotes:
    def test_with_none_lists(self):
        en = create_execution_notes(
            changes_made=None,
            commands_executed=None,
            files_modified=None,
            tests_run=None,
            issues_found=None,
        )
        assert en.changes_made == []
        assert en.commands_executed == []
        assert en.files_modified == []
        assert en.tests_run == []
        assert en.issues_found == []

    def test_with_values(self):
        en = create_execution_notes(
            changes_made=["c1"],
            commands_executed=["cmd1"],
            files_modified=["f1.py"],
            tests_run=["t1"],
            issues_found=["i1"],
        )
        assert en.changes_made == ["c1"]


class TestCreatePatchSummary:
    def test_with_none_lists(self):
        ps = create_patch_summary(description="D", files_changed=None, breaking_changes=None, verification_steps=None)
        assert ps.files_changed == []
        assert ps.breaking_changes == []
        assert ps.verification_steps == []

    def test_with_values(self):
        ps = create_patch_summary(
            description="D",
            files_changed=["a.py"],
            lines_added=5,
            lines_deleted=2,
            breaking_changes=["bc1"],
            verification_steps=["vs1"],
        )
        assert ps.description == "D"
        assert ps.lines_added == 5
        assert ps.lines_deleted == 2


class TestCreateQAAuditDraft:
    def test_with_none_values(self):
        qa = create_qa_audit_draft(
            target="T", test_results=None, issues_found=None, recommendations=None, severity_summary=None
        )
        assert qa.test_results == {}
        assert qa.issues_found == []
        assert qa.recommendations == []
        assert qa.severity_summary == {}

    def test_with_values(self):
        qa = create_qa_audit_draft(
            target="T",
            test_results={"pass": True},
            issues_found=[{"id": 1}],
            recommendations=["fix"],
            severity_summary={"high": 2},
        )
        assert qa.target == "T"
        assert qa.test_results == {"pass": True}
        assert qa.severity_summary == {"high": 2}


# ---------------------------------------------------------------------------
# RoleSessionExport
# ---------------------------------------------------------------------------


class TestRoleSessionExport:
    def test_creation_minimal(self):
        rse = RoleSessionExport(
            session_id="s1",
            role="director",
            host_kind="local",
            workspace="/ws",
            created_at="2024-01-01T00:00:00Z",
        )
        assert rse.session_id == "s1"
        assert rse.role == "director"
        assert rse.host_kind == "local"
        assert rse.workspace == "/ws"
        assert rse.created_at == "2024-01-01T00:00:00Z"
        assert rse.pm_task_draft is None
        assert rse.plan_notes is None
        assert rse.execution_notes is None
        assert rse.patch_summary is None
        assert rse.qa_audit_draft is None
        assert rse.messages == []
        assert rse.metadata == {}
        assert isinstance(rse.exported_at, str)

    def test_creation_full(self):
        rse = RoleSessionExport(
            session_id="s1",
            role="pm",
            host_kind="remote",
            workspace="/ws",
            created_at="2024-01-01T00:00:00Z",
            pm_task_draft=PMTaskDraft(title="T", description="D"),
            plan_notes=PlanNotes(summary="S"),
            execution_notes=ExecutionNotes(changes_made=["c1"]),
            patch_summary=PatchSummary(description="PS"),
            qa_audit_draft=QAAuditDraft(target="M"),
            messages=[{"role": "user", "content": "hi"}],
            metadata={"key": "value"},
        )
        assert rse.pm_task_draft is not None
        assert rse.pm_task_draft.title == "T"
        assert rse.plan_notes is not None
        assert rse.execution_notes is not None
        assert rse.patch_summary is not None
        assert rse.qa_audit_draft is not None
        assert rse.messages == [{"role": "user", "content": "hi"}]
        assert rse.metadata == {"key": "value"}


class TestRoleSessionExportToDict:
    def test_skips_none_values(self):
        rse = RoleSessionExport(
            session_id="s1",
            role="director",
            host_kind="local",
            workspace="/ws",
            created_at="2024-01-01T00:00:00Z",
        )
        d = rse.to_dict()
        assert "pm_task_draft" not in d
        assert "plan_notes" not in d
        assert "execution_notes" not in d
        assert "patch_summary" not in d
        assert "qa_audit_draft" not in d
        assert d["session_id"] == "s1"
        assert d["role"] == "director"
        assert d["messages"] == []

    def test_includes_present_values(self):
        rse = RoleSessionExport(
            session_id="s1",
            role="pm",
            host_kind="local",
            workspace="/ws",
            created_at="2024-01-01T00:00:00Z",
            pm_task_draft=PMTaskDraft(title="T", description="D"),
            metadata={"k": "v"},
        )
        d = rse.to_dict()
        assert d["pm_task_draft"]["title"] == "T"
        assert d["metadata"] == {"k": "v"}


class TestRoleSessionExportToMarkdown:
    def test_minimal(self):
        rse = RoleSessionExport(
            session_id="s1",
            role="director",
            host_kind="local",
            workspace="/ws",
            created_at="2024-01-01T00:00:00Z",
        )
        md = rse.to_markdown()
        assert "# Session Export" in md
        assert "s1" in md
        assert "director" in md
        assert "local" in md
        assert "/ws" in md

    def test_with_pm_task_draft(self):
        rse = RoleSessionExport(
            session_id="s1",
            role="pm",
            host_kind="local",
            workspace="/ws",
            created_at="2024-01-01T00:00:00Z",
            pm_task_draft=PMTaskDraft(title="Title", description="Desc"),
        )
        md = rse.to_markdown()
        assert "## PM Task Draft" in md
        assert "Title" in md
        assert "Desc" in md

    def test_with_plan_notes(self):
        rse = RoleSessionExport(
            session_id="s1",
            role="pm",
            host_kind="local",
            workspace="/ws",
            created_at="2024-01-01T00:00:00Z",
            plan_notes=PlanNotes(summary="Plan summary"),
        )
        md = rse.to_markdown()
        assert "## Plan Notes" in md
        assert "Plan summary" in md

    def test_with_execution_notes(self):
        rse = RoleSessionExport(
            session_id="s1",
            role="director",
            host_kind="local",
            workspace="/ws",
            created_at="2024-01-01T00:00:00Z",
            execution_notes=ExecutionNotes(changes_made=["change1", "change2"]),
        )
        md = rse.to_markdown()
        assert "## Execution Notes" in md
        assert "change1" in md
        assert "change2" in md

    def test_with_patch_summary(self):
        rse = RoleSessionExport(
            session_id="s1",
            role="director",
            host_kind="local",
            workspace="/ws",
            created_at="2024-01-01T00:00:00Z",
            patch_summary=PatchSummary(
                description="Patch desc", files_changed=["a.py"], lines_added=5, lines_deleted=2
            ),
        )
        md = rse.to_markdown()
        assert "## Patch Summary" in md
        assert "Patch desc" in md
        assert "a.py" in md
        assert "Lines added: 5" in md or "Lines deleted: 2" in md

    def test_with_qa_audit_draft(self):
        rse = RoleSessionExport(
            session_id="s1",
            role="qa",
            host_kind="local",
            workspace="/ws",
            created_at="2024-01-01T00:00:00Z",
            qa_audit_draft=QAAuditDraft(target="module.py", issues_found=[{"description": "bug found"}]),
        )
        md = rse.to_markdown()
        assert "## QA Audit Draft" in md
        assert "module.py" in md
        assert "bug found" in md

    def test_with_messages(self):
        rse = RoleSessionExport(
            session_id="s1",
            role="director",
            host_kind="local",
            workspace="/ws",
            created_at="2024-01-01T00:00:00Z",
            messages=[{"role": "user", "content": "hello"}],
        )
        md = rse.to_markdown()
        assert "## Transcript" in md
        assert "user" in md
        assert "hello" in md


# ---------------------------------------------------------------------------
# parse_export_type
# ---------------------------------------------------------------------------


class TestParseExportType:
    def test_valid_types(self):
        assert parse_export_type("pm_task_draft") == ExportType.PM_TASK_DRAFT
        assert parse_export_type("plan_notes") == ExportType.PLAN_NOTES
        assert parse_export_type("execution_notes") == ExportType.EXECUTION_NOTES
        assert parse_export_type("patch_summary") == ExportType.PATCH_SUMMARY
        assert parse_export_type("qa_audit_draft") == ExportType.QA_AUDIT_DRAFT
        assert parse_export_type("blueprint") == ExportType.BLUEPRINT
        assert parse_export_type("qa_memo") == ExportType.QA_MEMO

    def test_invalid_fallback(self):
        assert parse_export_type("unknown_type") == ExportType.PM_TASK_DRAFT
        assert parse_export_type("") == ExportType.PM_TASK_DRAFT
