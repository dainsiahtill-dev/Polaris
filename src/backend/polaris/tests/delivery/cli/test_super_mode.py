"""Tests for polaris.delivery.cli.super_mode module."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from polaris.delivery.cli.super_mode import (
    SuperBlueprintItem,
    SuperClaimedTask,
    SuperModeRouter,
    SuperPipelineContext,
    SuperRouteDecision,
    SuperTaskItem,
    _clean_markdown_cell,
    _contains_any,
    _fallback_blueprint_items,
    _find_column_index,
    _has_code_artifact_hint,
    _has_explicit_file_target,
    _normalize_string_sequence,
    _parse_hours,
    _parse_task_data,
    _should_route_via_architect,
    _truncate_text,
    build_chief_engineer_handoff_message,
    build_director_handoff_message,
    build_director_task_handoff_message,
    build_pm_handoff_message,
    build_super_readonly_message,
    extract_blueprint_items_from_ce_output,
    extract_task_list_from_pm_output,
    write_architect_blueprint_to_disk,
)


class TestSuperModeRouterDecide:
    """Tests for SuperModeRouter.decide() method."""

    @pytest.fixture
    def router(self):
        return SuperModeRouter()

    def test_architecture_design_no_execution_intent(self, router):
        decision = router.decide("design the system architecture", fallback_role="director")
        assert decision.roles == ("architect",)
        assert decision.reason == "architecture_design"
        assert decision.use_architect is True
        assert decision.use_pm is False

    def test_architecture_with_execution_intent(self, router):
        decision = router.decide("design architecture then start implementing", fallback_role="director")
        assert decision.roles == ("architect", "pm", "chief_engineer", "director")
        assert decision.reason == "architect_code_delivery"
        assert decision.use_architect is True
        assert decision.use_pm is True
        assert decision.use_chief_engineer is True
        assert decision.use_director is True

    def test_code_delivery(self, router):
        decision = router.decide("fix the bug in main.py", fallback_role="director")
        assert "director" in decision.roles
        assert decision.reason == "code_delivery"

    def test_chief_engineer_keywords(self, router):
        decision = router.decide("root cause analysis of the failure", fallback_role="director")
        assert decision.roles == ("chief_engineer",)
        assert decision.reason == "technical_analysis"
        assert decision.use_chief_engineer is True

    def test_qa_keywords(self, router):
        decision = router.decide("verify the test results", fallback_role="director")
        assert decision.roles == ("qa",)
        assert decision.reason == "qa_validation"

    def test_pm_keywords(self, router):
        decision = router.decide("create a roadmap for the project", fallback_role="director")
        assert decision.roles == ("pm",)
        assert decision.reason == "planning"
        assert decision.use_pm is True

    def test_fallback(self, router):
        decision = router.decide("hello world", fallback_role="director")
        assert decision.roles == ("director",)
        assert decision.reason == "fallback"

    def test_empty_and_none_input(self, router):
        decision = router.decide("", fallback_role="director")
        assert decision.roles == ("director",)
        assert decision.reason == "fallback"
        decision = router.decide(None, fallback_role="pm")
        assert decision.roles == ("pm",)
        assert decision.reason == "fallback"


class TestUtilityFunctions:
    def test_contains_any_happy_path(self):
        assert _contains_any("hello world", ("world", "foo")) is True
        assert _contains_any("hello world", ("foo", "bar")) is False

    def test_has_code_artifact_hint(self):
        assert _has_code_artifact_hint("fix the code") is True
        assert _has_code_artifact_hint("check main.py") is True
        assert _has_code_artifact_hint("hello world") is False

    def test_has_explicit_file_target(self):
        assert _has_explicit_file_target("open main.py") is True
        assert _has_explicit_file_target("edit file.ts") is True
        assert _has_explicit_file_target("hello world") is False

    def test_should_route_via_architect(self):
        assert _should_route_via_architect("design context plane") is True
        assert _should_route_via_architect("use kernelone") is True
        assert _should_route_via_architect("fix bug") is False


class TestBuildMessages:
    def test_build_super_readonly_message(self):
        msg = build_super_readonly_message(role="architect", original_request="design the API")
        assert "[mode:analyze]" in msg
        assert "stage_role: architect" in msg
        assert "design the API" in msg
        assert "TASK_LIST" in msg

    def test_build_super_readonly_message_none_inputs(self):
        msg = build_super_readonly_message(role=None, original_request=None)
        assert "stage_role: unknown" in msg
        assert "original_user_request:" in msg

    def test_truncate_text(self):
        assert _truncate_text("short", limit=10) == "short"
        assert _truncate_text("short", limit=3) == "sho..."
        assert _truncate_text("", limit=10) == ""
        assert _truncate_text(None, limit=10) == ""

    def test_build_director_handoff_message_without_tasks(self):
        msg = build_director_handoff_message(original_request="fix bug", pm_output="plan")
        assert "[mode:materialize]" in msg
        assert "fix bug" in msg
        assert "synthetic_task" in msg

    def test_build_director_handoff_message_with_tasks(self):
        tasks = [SuperTaskItem(subject="T1", description="desc", target_files=("a.py",), estimated_hours=1.0)]
        msg = build_director_handoff_message(original_request="fix bug", pm_output="plan", extracted_tasks=tasks)
        assert "extracted_tasks:" in msg
        assert "T1" in msg
        assert "a.py" in msg

    def test_build_pm_handoff_message(self):
        msg = build_pm_handoff_message(
            original_request="design system",
            architect_output="blueprint content",
            blueprint_file_path="docs/blueprint.md",
        )
        assert "[SUPER_MODE_PM_HANDOFF]" in msg
        assert "design system" in msg
        assert "docs/blueprint.md" in msg

    def test_build_chief_engineer_handoff_message(self):
        claims = [
            SuperClaimedTask(
                task_id="t1",
                stage="ce",
                status="pending",
                trace_id="tr1",
                run_id="r1",
                lease_token="lt1",
                payload={"subject": "Task 1"},
            )
        ]
        msg = build_chief_engineer_handoff_message(
            original_request="fix bug", architect_output="arch out", pm_output="pm out", claimed_tasks=claims
        )
        assert "[SUPER_MODE_CE_HANDOFF]" in msg
        assert "Task 1" in msg

    def test_build_director_task_handoff_message(self):
        claims = [
            SuperClaimedTask(
                task_id="t1",
                stage="dir",
                status="pending",
                trace_id="tr1",
                run_id="r1",
                lease_token="lt1",
                payload={"subject": "Task 1", "target_files": ["a.py"]},
            )
        ]
        blueprints = [
            SuperBlueprintItem(
                task_id="t1",
                blueprint_id="bp1",
                summary="summary",
                scope_paths=("a.py",),
                guardrails=("g1",),
                no_touch_zones=("b.py",),
            )
        ]
        msg = build_director_task_handoff_message(
            original_request="fix bug",
            architect_output="arch",
            pm_output="pm",
            claimed_tasks=claims,
            blueprint_items=blueprints,
        )
        assert "[SUPER_MODE_DIRECTOR_TASK_HANDOFF]" in msg
        assert "bp1" in msg
        assert "g1" in msg
        assert "b.py" in msg


class TestTaskExtraction:
    def test_extract_task_list_from_fenced_json(self):
        pm_output = """
Some text before.

`json
{
  "tasks": [
    {
      "subject": "Task One",
      "description": "Do something",
      "target_files": ["a.py", "b.py"],
      "estimated_hours": 2.5
    }
  ]
}
`
Some text after.
"""
        tasks = extract_task_list_from_pm_output(pm_output)
        assert len(tasks) == 1
        assert tasks[0].subject == "Task One"
        assert tasks[0].description == "Do something"
        assert tasks[0].target_files == ("a.py", "b.py")
        assert tasks[0].estimated_hours == 2.5

    def test_extract_task_list_from_markdown_table(self):
        pm_output = """
| Title | Description | target_files | estimated_hours |
|-------|-------------|--------------|-----------------|
| **Task 1** | desc | .py | 1.5h |
| Task 2 | desc2 | b.py, c.py | 2 |
"""
        tasks = extract_task_list_from_pm_output(pm_output)
        assert len(tasks) == 2
        assert tasks[0].subject == "Task 1"
        assert tasks[0].estimated_hours == 1.5
        assert tasks[1].subject == "Task 2"
        assert tasks[1].target_files == ("b.py", "c.py")

    def test_extract_task_list_empty_and_invalid(self):
        assert extract_task_list_from_pm_output("") == []
        assert extract_task_list_from_pm_output(None) == []
        assert extract_task_list_from_pm_output("no tasks here") == []

    def test_parse_hours(self):
        assert _parse_hours("1.5h") == 1.5
        assert _parse_hours("2å°æ¶") == 2.0
        assert _parse_hours("0.5") == 0.5
        assert _parse_hours("") == 0.0
        assert _parse_hours("abc") == 0.0
        assert _parse_hours("about 3 hours") == 3.0

    def test_clean_markdown_cell(self):
        assert _clean_markdown_cell("**bold**") == "bold"
        assert _clean_markdown_cell("code") == "code"
        assert _clean_markdown_cell("~~strike~~") == "strike"
        assert _clean_markdown_cell("  trim  ") == "trim"

    def test_find_column_index(self):
        headers = ["Title", "Description", "target_files", "hours"]
        assert _find_column_index(headers, ("Title", "subject")) == 0
        assert _find_column_index(headers, ("file", "target_files")) == 2
        assert _find_column_index(headers, ("missing",)) is None

    def test_parse_task_data_skips_invalid(self):
        data = {
            "tasks": [
                {"subject": "Valid", "description": "desc", "target_files": ["a.py"], "estimated_hours": 1},
                {"no_subject": "Invalid"},
                "not a dict",
            ]
        }
        tasks = _parse_task_data(data)
        assert len(tasks) == 1
        assert tasks[0].subject == "Valid"


class TestBlueprintExtraction:
    def test_extract_blueprint_items_from_fenced_json(self):
        ce_output = """
`json
{
  "blueprints": [
    {
      "task_id": "t1",
      "blueprint_id": "bp1",
      "summary": "summary",
      "scope_paths": ["a.py"],
      "guardrails": ["g1"],
      "no_touch_zones": ["b.py"]
    }
  ]
}
`
"""
        items = extract_blueprint_items_from_ce_output(ce_output)
        assert len(items) == 1
        assert items[0].task_id == "t1"
        assert items[0].blueprint_id == "bp1"
        assert items[0].scope_paths == ("a.py",)

    def test_extract_blueprint_items_empty(self):
        assert extract_blueprint_items_from_ce_output("") == []
        assert extract_blueprint_items_from_ce_output(None) == []

    def test_fallback_blueprint_items(self):
        claims = [
            SuperClaimedTask(
                task_id="t1",
                stage="ce",
                status="pending",
                trace_id="tr1",
                run_id="r1",
                lease_token="lt1",
                payload={"scope_paths": ["a.py"]},
            )
        ]
        items = _fallback_blueprint_items("some text", claims)
        assert len(items) == 1
        assert items[0].task_id == "t1"
        assert items[0].scope_paths == ("a.py",)

    def test_normalize_string_sequence(self):
        assert _normalize_string_sequence(None) == ()
        assert _normalize_string_sequence("a") == ("a",)
        assert _normalize_string_sequence(["a", "b", "a"]) == ("a", "b")
        assert _normalize_string_sequence(123) == ()


class TestWriteArchitectBlueprint:
    def test_write_blueprint_happy_path(self, tmp_path):
        workspace = str(tmp_path)
        result = write_architect_blueprint_to_disk(
            workspace=workspace,
            original_request="design the API gateway",
            architect_output="# Blueprint\n\nUse FastAPI.",
        )
        assert result != ""
        assert result.startswith("docs/blueprints/")
        assert result.endswith(".md")
        full_path = tmp_path / result
        assert full_path.exists()
        content = full_path.read_text(encoding="utf-8")
        assert "Blueprint" in content
        assert "FastAPI" in content

    def test_write_blueprint_empty_output(self, tmp_path):
        result = write_architect_blueprint_to_disk(
            workspace=str(tmp_path), original_request="design", architect_output=""
        )
        assert result == ""

    def test_write_blueprint_none_output(self, tmp_path):
        result = write_architect_blueprint_to_disk(
            workspace=str(tmp_path), original_request="design", architect_output=None
        )
        assert result == ""

    def test_write_blueprint_handles_special_chars_in_request(self, tmp_path):
        workspace = str(tmp_path)
        result = write_architect_blueprint_to_disk(
            workspace=workspace, original_request="design API gateway!!!", architect_output="content"
        )
        assert result != ""
        assert ".md" in result

    def test_write_blueprint_exception_handled(self, tmp_path):
        with patch("os.makedirs", side_effect=OSError("disk full")):
            result = write_architect_blueprint_to_disk(
                workspace=str(tmp_path), original_request="design", architect_output="content"
            )
        assert result == ""


class TestSuperPipelineContext:
    def test_default_values(self):
        ctx = SuperPipelineContext(original_request="test")
        assert ctx.original_request == "test"
        assert ctx.architect_output == ""
        assert ctx.pm_output == ""
        assert ctx.blueprint_file_path == ""
        assert ctx.extracted_tasks == ()
        assert ctx.published_task_ids == ()
        assert ctx.ce_claims == ()
        assert ctx.director_claims == ()
        assert ctx.blueprint_items == ()

    def test_source_chain_default(self):
        ctx = SuperPipelineContext(original_request="test")
        assert ctx.source_chain is not None


class TestSuperRouteDecision:
    def test_default_bool_flags(self):
        decision = SuperRouteDecision(roles=("architect",), reason="test", fallback_role="director")
        assert decision.use_architect is False
        assert decision.use_pm is False
        assert decision.use_chief_engineer is False
        assert decision.use_director is False

    def test_explicit_flags(self):
        decision = SuperRouteDecision(
            roles=("architect", "pm"), reason="test", fallback_role="director", use_architect=True, use_pm=True
        )
        assert decision.use_architect is True
        assert decision.use_pm is True
