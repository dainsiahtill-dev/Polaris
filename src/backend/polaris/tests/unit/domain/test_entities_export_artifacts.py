"""Tests for polaris.domain.entities.export_artifacts."""

from __future__ import annotations

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


class TestExportType:
    def test_values(self) -> None:
        assert ExportType.PM_TASK_DRAFT.value == "pm_task_draft"
        assert ExportType.PLAN_NOTES.value == "plan_notes"
        assert ExportType.EXECUTION_NOTES.value == "execution_notes"
        assert ExportType.PATCH_SUMMARY.value == "patch_summary"
        assert ExportType.QA_AUDIT_DRAFT.value == "qa_audit_draft"
        assert ExportType.BLUEPRINT.value == "blueprint"
        assert ExportType.QA_MEMO.value == "qa_memo"


class TestExportFormat:
    def test_values(self) -> None:
        assert ExportFormat.JSON.value == "json"
        assert ExportFormat.MARKDOWN.value == "markdown"


class TestPMTaskDraft:
    def test_defaults(self) -> None:
        draft = PMTaskDraft(title="T", description="D")
        assert draft.priority == "medium"
        assert draft.dependencies == []
        assert draft.acceptance_criteria == []


class TestPlanNotes:
    def test_defaults(self) -> None:
        notes = PlanNotes(summary="S")
        assert notes.goals == []
        assert notes.risks == []


class TestExecutionNotes:
    def test_defaults(self) -> None:
        notes = ExecutionNotes()
        assert notes.changes_made == []
        assert notes.tests_run == []


class TestPatchSummary:
    def test_defaults(self) -> None:
        summary = PatchSummary(description="D")
        assert summary.lines_added == 0
        assert summary.lines_deleted == 0


class TestQAAuditDraft:
    def test_defaults(self) -> None:
        draft = QAAuditDraft(target="T")
        assert draft.issues_found == []
        assert draft.recommendations == []


class TestRoleSessionExport:
    def test_to_dict_omits_none(self) -> None:
        export = RoleSessionExport(
            session_id="s1",
            role="director",
            host_kind="workflow",
            workspace=".",
            created_at="2024-01-01T00:00:00",
        )
        d = export.to_dict()
        assert d["session_id"] == "s1"
        assert "pm_task_draft" not in d

    def test_to_dict_includes_set_fields(self) -> None:
        export = RoleSessionExport(
            session_id="s1",
            role="director",
            host_kind="workflow",
            workspace=".",
            created_at="2024-01-01T00:00:00",
            pm_task_draft=PMTaskDraft(title="T", description="D"),
        )
        d = export.to_dict()
        assert "pm_task_draft" in d

    def test_to_markdown(self) -> None:
        export = RoleSessionExport(
            session_id="s1",
            role="director",
            host_kind="workflow",
            workspace=".",
            created_at="2024-01-01T00:00:00",
            pm_task_draft=PMTaskDraft(title="T", description="D"),
        )
        md = export.to_markdown()
        assert "Session Export" in md
        assert "s1" in md
        assert "T" in md

    def test_to_markdown_with_execution_notes(self) -> None:
        export = RoleSessionExport(
            session_id="s1",
            role="director",
            host_kind="workflow",
            workspace=".",
            created_at="2024-01-01T00:00:00",
            execution_notes=ExecutionNotes(changes_made=["fixed bug"]),
        )
        md = export.to_markdown()
        assert "fixed bug" in md

    def test_to_markdown_with_patch_summary(self) -> None:
        export = RoleSessionExport(
            session_id="s1",
            role="director",
            host_kind="workflow",
            workspace=".",
            created_at="2024-01-01T00:00:00",
            patch_summary=PatchSummary(description="patch", files_changed=["a.py"], lines_added=5),
        )
        md = export.to_markdown()
        assert "patch" in md
        assert "a.py" in md

    def test_to_markdown_with_qa_audit(self) -> None:
        export = RoleSessionExport(
            session_id="s1",
            role="director",
            host_kind="workflow",
            workspace=".",
            created_at="2024-01-01T00:00:00",
            qa_audit_draft=QAAuditDraft(target="module", issues_found=[{"description": "bug"}]),
        )
        md = export.to_markdown()
        assert "module" in md
        assert "bug" in md


class TestFactoryFunctions:
    def test_create_pm_task_draft(self) -> None:
        draft = create_pm_task_draft("Title", "Desc", priority="high")
        assert draft.title == "Title"
        assert draft.priority == "high"

    def test_create_pm_task_draft_defaults(self) -> None:
        draft = create_pm_task_draft("T", "D")
        assert draft.dependencies == []
        assert draft.acceptance_criteria == []

    def test_create_plan_notes(self) -> None:
        notes = create_plan_notes("Summary", goals=["g1"])
        assert notes.summary == "Summary"
        assert notes.goals == ["g1"]

    def test_create_execution_notes(self) -> None:
        notes = create_execution_notes(changes_made=["c1"])
        assert notes.changes_made == ["c1"]

    def test_create_patch_summary(self) -> None:
        summary = create_patch_summary("desc", files_changed=["a.py"], lines_added=5)
        assert summary.description == "desc"
        assert summary.lines_added == 5

    def test_create_qa_audit_draft(self) -> None:
        draft = create_qa_audit_draft("target", test_results={"pass": True})
        assert draft.target == "target"
        assert draft.test_results == {"pass": True}


class TestParseExportType:
    def test_valid(self) -> None:
        assert parse_export_type("plan_notes") == ExportType.PLAN_NOTES

    def test_invalid_defaults_to_pm_task_draft(self) -> None:
        assert parse_export_type("invalid") == ExportType.PM_TASK_DRAFT
