"""Tests for QualityChecker._extract_json hardening (R13-B).

Validates that _extract_json accepts:
- Fenced ```json blocks (existing behavior preserved).
- Bare JSON objects (no fence).
- Prose followed by a balanced top-level JSON object.
And still rejects:
- Empty text.
- Invalid JSON.
- Non-dict JSON (arrays) for roles expecting objects.
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.quality_checker import (
    QualityChecker,
    QualityResult,
)


class TestExtractJsonFenced:
    """Fenced JSON blocks remain the preferred extraction path."""

    def test_fenced_json_still_passes(self) -> None:
        checker = QualityChecker()
        content = '```json\n{"tasks": [{"id": 1}]}\n```'
        data, errors = checker._extract_json(content)
        assert data is not None
        assert data["tasks"][0]["id"] == 1
        assert errors == []

    def test_fenced_json_without_language_hint(self) -> None:
        checker = QualityChecker()
        content = '```\n{"result": "ok"}\n```'
        data, errors = checker._extract_json(content)
        assert data is not None
        assert data["result"] == "ok"
        assert errors == []


class TestExtractJsonBare:
    """Bare JSON objects should be accepted when no fenced block is found."""

    def test_bare_json_object_passes(self) -> None:
        checker = QualityChecker()
        content = '{"construction_plan": {}, "scope_for_apply": ["a.py"], "risk_flags": []}'
        data, errors = checker._extract_json(content)
        assert data is not None
        assert "construction_plan" in data
        assert errors == []

    def test_bare_json_with_whitespace(self) -> None:
        checker = QualityChecker()
        content = '  \n  {"verdict": "PASS", "findings": []}  \n  '
        data, errors = checker._extract_json(content)
        assert data is not None
        assert data["verdict"] == "PASS"
        assert errors == []


class TestExtractJsonProsePlusJson:
    """Prose preceding a balanced JSON object should still be extractable."""

    def test_prose_plus_balanced_json(self) -> None:
        checker = QualityChecker()
        content = (
            "Here is my analysis.\n\n"
            '{"construction_plan": {"steps": [1]}, '
            '"scope_for_apply": ["src/a.py"], '
            '"risk_flags": ["low"]}\n\n'
            "Let me know if you need more."
        )
        data, errors = checker._extract_json(content)
        assert data is not None
        assert data["construction_plan"]["steps"] == [1]
        assert errors == []

    def test_prose_plus_nested_json(self) -> None:
        checker = QualityChecker()
        content = 'Analysis result:\n\n{"outer": {"inner": {"deep": true}}, "list": [1, 2, 3]}'
        data, errors = checker._extract_json(content)
        assert data is not None
        assert data["outer"]["inner"]["deep"] is True
        assert errors == []

    def test_multiple_json_objects_returns_first(self) -> None:
        checker = QualityChecker()
        content = '{"first": 1}\n\nSome text.\n\n{"second": 2}'
        data, errors = checker._extract_json(content)
        assert data is not None
        assert data["first"] == 1
        assert errors == []


class TestExtractJsonFailures:
    """Invalid or empty content must fail clearly with preserved errors."""

    def test_empty_text_fails(self) -> None:
        checker = QualityChecker()
        data, errors = checker._extract_json("")
        assert data is None
        assert errors == ["Empty text"]

    def test_whitespace_only_fails(self) -> None:
        checker = QualityChecker()
        data, errors = checker._extract_json("   \n\t  ")
        assert data is None
        assert errors == ["Empty text"]

    def test_invalid_json_fails_with_parse_error(self) -> None:
        checker = QualityChecker()
        data, errors = checker._extract_json("not json at all")
        assert data is None
        assert len(errors) > 0
        assert any("JSON解析错误" in e for e in errors)

    def test_truncated_json_fails(self) -> None:
        checker = QualityChecker()
        data, errors = checker._extract_json('{"key": "val')
        assert data is None
        assert len(errors) > 0

    def test_array_json_not_returned_as_dict(self) -> None:
        """Arrays should not be returned (PM/CE/QA expect dicts)."""
        checker = QualityChecker()
        data, _errors = checker._extract_json("[1, 2, 3]")
        assert data is None


class TestValidateOutputBareJsonIntegration:
    """End-to-end: validate_output accepts bare JSON for chief_engineer."""

    def test_chief_engineer_bare_json_passes_parsing(self) -> None:
        checker = QualityChecker()

        class MockProfile:
            role_id = "chief_engineer"

        content = (
            '{"construction_plan": {"phase": "impl"}, "scope_for_apply": ["src/main.py"], "risk_flags": ["medium"]}'
        )
        result = checker.validate_output(content, MockProfile())  # type: ignore[arg-type]
        assert isinstance(result, QualityResult)
        assert result.data is not None
        assert result.errors == []

    def test_chief_engineer_prose_plus_json_passes_parsing(self) -> None:
        checker = QualityChecker()

        class MockProfile:
            role_id = "chief_engineer"

        content = (
            "Based on my analysis, here is the plan.\n\n"
            '{"construction_plan": {"steps": []}, '
            '"scope_for_apply": ["a.py"], '
            '"risk_flags": []}'
        )
        result = checker.validate_output(content, MockProfile())  # type: ignore[arg-type]
        assert isinstance(result, QualityResult)
        assert result.data is not None
        assert result.errors == []

    def test_chief_engineer_missing_advisory_scope_still_passes(self) -> None:
        """PM scope authority makes CE ``scope_for_apply`` optional advice."""
        checker = QualityChecker()

        class MockProfile:
            role_id = "chief_engineer"

        content = (
            '{"construction_plan": {"task_plans": {}, "project_interface_contract": '
            '{"provider_declarations": [], "consumer_declarations": []}}, '
            '"project_completion_contract": {"obligations": {}}, "risk_flags": []}'
        )
        result = checker.validate_output(content, MockProfile())  # type: ignore[arg-type]
        assert result.success is True
        assert result.data is not None
        assert "scope_for_apply" not in result.data
        assert result.errors == []

    def test_pm_bare_json_passes_parsing(self) -> None:
        checker = QualityChecker()

        class MockProfile:
            role_id = "pm"

        content = '{"tasks": [{"id": "t1", "target_files": ["a.py"], "acceptance_criteria": ["works"]}]}'
        result = checker.validate_output(content, MockProfile())  # type: ignore[arg-type]
        assert isinstance(result, QualityResult)
        assert result.data is not None
        assert result.errors == []

    def test_qa_bare_json_passes_parsing(self) -> None:
        checker = QualityChecker()

        class MockProfile:
            role_id = "qa"

        content = '{"verdict": "PASS", "findings": []}'
        result = checker.validate_output(content, MockProfile())  # type: ignore[arg-type]
        assert isinstance(result, QualityResult)
        assert result.data is not None
        assert result.errors == []


class TestExtractFirstBalancedJsonObject:
    """Unit tests for the static _extract_all_balanced_json_objects helper."""

    def test_simple_object(self) -> None:
        results = QualityChecker._extract_all_balanced_json_objects('{"a": 1}')
        assert len(results) == 1
        assert results[0] == {"a": 1}

    def test_nested_object(self) -> None:
        results = QualityChecker._extract_all_balanced_json_objects('prefix {"outer": {"inner": [1, 2]}} suffix')
        assert len(results) >= 1
        assert results[0]["outer"]["inner"] == [1, 2]

    def test_string_with_braces(self) -> None:
        results = QualityChecker._extract_all_balanced_json_objects('{"key": "value with { } braces"}')
        assert len(results) == 1
        assert "braces" in results[0]["key"]

    def test_no_json_returns_empty(self) -> None:
        results = QualityChecker._extract_all_balanced_json_objects("no json here")
        assert results == []

    def test_array_not_returned(self) -> None:
        results = QualityChecker._extract_all_balanced_json_objects("[1, 2, 3]")
        assert results == []

    def test_multiple_objects_all_returned(self) -> None:
        results = QualityChecker._extract_all_balanced_json_objects('{"first": 1}\n\nSome text.\n\n{"second": 2}')
        assert len(results) == 2
        assert results[0]["first"] == 1
        assert results[1]["second"] == 2


class TestSchemaAwareJsonExtraction:
    """Role-aware JSON selection prefers candidates with blueprint keys."""

    def test_ce_selects_candidate_with_blueprint_keys(self) -> None:
        """When first JSON lacks CE keys but second has them, prefer second."""
        checker = QualityChecker()
        content = (
            '{"task_progress": {"done": 3}}\n\n'
            '{"construction_plan": {"steps": [1]}, '
            '"scope_for_apply": ["a.py"], '
            '"risk_flags": ["low"]}'
        )
        data, errors = checker._extract_json(content, role="chief_engineer")
        assert data is not None
        assert "construction_plan" in data
        assert "scope_for_apply" in data
        assert errors == []

    def test_ce_accepts_wrapped_llm_blueprint_object(self) -> None:
        """L1-05 regression: CE may wrap blueprint in ``llm_blueprint``."""
        checker = QualityChecker()
        content = (
            '{"llm_blueprint": {\n'
            '  "construction_plan": {"steps": [1]},\n'
            '  "scope_for_apply": ["src/main.rs"],\n'
            '  "risk_flags": ["low"]\n'
            "}}\n"
        )
        data, errors = checker._extract_json(content, role="chief_engineer")
        assert data is not None
        assert "construction_plan" in data
        assert data["scope_for_apply"] == ["src/main.rs"]
        assert data["risk_flags"] == ["low"]
        assert errors == []

    def test_ce_accepts_wrapped_blueprint_missing_optional_scope(self) -> None:
        """Absent CE advice stays absent; PM scope authority is not synthesized."""
        checker = QualityChecker()
        content = (
            '{"llm_blueprint": {\n'
            '  "construction_plan": {\n'
            '    "task_plans": {\n'
            '      "TASK-1": {"target_files": ["src/lib.rs"]}\n'
            "    },\n"
            '    "risk_flags": ["elevated"]\n'
            "  }\n"
            "}}\n"
        )
        data, errors = checker._extract_json(content, role="chief_engineer")
        assert data is not None
        assert "scope_for_apply" not in data
        assert data["risk_flags"] == ["elevated"]
        assert errors == []

    def test_ce_recovers_unquoted_angle_bracket_actions_array(self) -> None:
        """R68: CE emitted unquoted ``<Type>`` soup after ``actions`` colon.

        Pattern mirrored from deepseek-v4-pro portfolio output where the model
        dropped the opening quote/array bracket before angle-bracket type soup
        but still closed with a stray quote before additional array elements.
        """
        checker = QualityChecker()
        content = (
            "{\n"
            '  "construction_plan": {\n'
            '    "task_plans": {"TASK-1": {"title": "models"}},\n'
            '    "project_interface_contract": {\n'
            '      "provider_declarations": ["crate::models::Palette"],\n'
            '      "consumer_declarations": ["src/main.rs"]\n'
            "    },\n"
            '    "build_phases": [\n'
            "      {\n"
            '        "phase": "4. Domain types implementation",\n'
            '        "actions": <(Ingredient, String)> where String is quantity/unit), '
            'and method description.",\n'
            '              "Ensure all source files contain the keywords flavor, palette."\n'
            "            ],\n"
            '        "verification": "cargo build --lib succeeds"\n'
            "      }\n"
            "    ]\n"
            "  },\n"
            '  "scope_for_apply": ["src/lib.rs", "src/main.rs"],\n'
            '  "risk_flags": ["low"]\n'
            "}\n"
        )
        data, errors = checker._extract_json(content, role="chief_engineer")
        assert data is not None, errors
        assert "construction_plan" in data
        assert "scope_for_apply" in data
        assert "risk_flags" in data
        assert errors == []

    def test_ce_recovers_real_r68_angle_bracket_corruptions_shape(self) -> None:
        """Minimal shape from R68: angle soup + extra trailing fragment after root."""
        checker = QualityChecker()
        # Root object is complete; trailing garbage simulates stream residue.
        content = (
            '{"construction_plan":{"task_plans":{},"project_interface_contract":'
            '{"provider_declarations":[],"consumer_declarations":[]},'
            '"phases":[{"phase":"x","actions": <Foo, Bar>) helper note.",'
            '"more work."],'
            '"verification":"cargo check"}]},'
            '"scope_for_apply":["src/lib.rs"],"risk_flags":["low"]}'
            '["trailing","residue"]'
        )
        data, errors = checker._extract_json(content, role="chief_engineer")
        assert data is not None, errors
        assert set(data) >= {"construction_plan", "scope_for_apply", "risk_flags"}
        assert errors == []

    def test_valid_json_unchanged_by_angle_repair_path(self) -> None:
        """Strict-valid CE JSON must still win without needing salvage."""
        checker = QualityChecker()
        content = (
            '{"construction_plan": {"task_plans": {}, "project_interface_contract": '
            '{"provider_declarations": [], "consumer_declarations": []}}, '
            '"scope_for_apply": ["src/lib.rs"], "risk_flags": []}'
        )
        data, errors = checker._extract_json(content, role="chief_engineer")
        assert data is not None
        assert data["scope_for_apply"] == ["src/lib.rs"]
        assert errors == []

    def test_ce_rejects_session_patch_object(self) -> None:
        """<SESSION_PATCH> object must not be accepted as CE data."""
        checker = QualityChecker()
        content = (
            "Here is the analysis.\n\n"
            '{"task_progress": {"status": "running"}}\n\n'
            '<SESSION_PATCH>{"patch": "file.py"}</SESSION_PATCH>'
        )
        data, errors = checker._extract_json(content, role="chief_engineer")
        # Neither candidate has CE keys, so extraction fails with diagnostics.
        assert data is None
        assert len(errors) > 0
        assert any("blueprint keys" in e for e in errors)

    def test_ce_single_candidate_with_keys_returns_it(self) -> None:
        checker = QualityChecker()
        content = '{"construction_plan": {"steps": []}, "scope_for_apply": [], "risk_flags": []}'
        data, errors = checker._extract_json(content, role="chief_engineer")
        assert data is not None
        assert "construction_plan" in data
        assert errors == []

    def test_ce_reports_missing_keys_when_no_match(self) -> None:
        """When no candidate has any CE key, error names the missing keys."""
        checker = QualityChecker()
        content = '{"task_progress": {"done": 1}}'
        data, errors = checker._extract_json(content, role="chief_engineer")
        assert data is None
        assert len(errors) > 0
        assert "construction_plan" in errors[0]
        assert "risk_flags" in errors[0]

    def test_pm_prefers_tasks_object(self) -> None:
        checker = QualityChecker()
        content = '{"other": "data"}\n\n{"tasks": [{"id": "t1"}]}'
        data, errors = checker._extract_json(content, role="pm")
        assert data is not None
        assert "tasks" in data
        assert errors == []

    def test_qa_prefers_verdict_object(self) -> None:
        checker = QualityChecker()
        content = '{"something": "else"}\n\n{"verdict": "PASS", "findings": []}'
        data, errors = checker._extract_json(content, role="qa")
        assert data is not None
        assert data["verdict"] == "PASS"
        assert errors == []

    def test_unknown_role_returns_first_candidate(self) -> None:
        """Roles without a schema get first candidate (backward compat)."""
        checker = QualityChecker()
        content = '{"first": 1}\n\n{"second": 2}'
        data, errors = checker._extract_json(content, role="director")
        assert data is not None
        assert data["first"] == 1
        assert errors == []

    def test_no_role_returns_first_candidate(self) -> None:
        checker = QualityChecker()
        content = '{"first": 1}\n\n{"second": 2}'
        data, errors = checker._extract_json(content)
        assert data is not None
        assert data["first"] == 1
        assert errors == []


class TestR14DSessionPatchRejection:
    """R14-D regression: <SESSION_PATCH> must never be accepted as a
    valid chief_engineer blueprint review.

    The Kimi provider once emitted prose containing <SESSION_PATCH> blocks
    instead of the expected CE blueprint JSON.  These tests ensure the
    QualityChecker rejects such output.
    """

    def test_session_patch_only_content_fails_ce_extraction(self) -> None:
        """Pure <SESSION_PATCH> content without any valid CE JSON must fail."""
        checker = QualityChecker()
        content = '<SESSION_PATCH file="a.py">\n{"old": "pass", "new": "return 0"}\n</SESSION_PATCH>'
        data, _errors = checker._extract_json(content, role="chief_engineer")
        # <SESSION_PATCH> is not a balanced JSON object; extraction must fail.
        assert data is None

    def test_session_patch_with_valid_ce_json_later_is_accepted(self) -> None:
        """When <SESSION_PATCH> appears before a valid CE JSON object,
        the valid CE JSON should be found (role-aware selection)."""
        checker = QualityChecker()
        content = (
            '<SESSION_PATCH file="a.py">\n'
            '{"old": "x", "new": "y"}\n'
            "</SESSION_PATCH>\n\n"
            '{"construction_plan": {"steps": [1]}, '
            '"scope_for_apply": ["src/a.py"], '
            '"risk_flags": ["low"]}'
        )
        data, _errors = checker._extract_json(content, role="chief_engineer")
        assert data is not None
        assert "construction_plan" in data
        assert "scope_for_apply" in data
        assert "risk_flags" in data

    def test_session_patch_not_accepted_as_ce_via_validate_output(self) -> None:
        """End-to-end: validate_output for chief_engineer must reject
        content that is only <SESSION_PATCH> blocks."""
        checker = QualityChecker()

        class MockProfile:
            role_id = "chief_engineer"

        content = '<SESSION_PATCH file="src/a.py">\n{"old": "pass", "new": "return 0"}\n</SESSION_PATCH>'
        result = checker.validate_output(content, MockProfile())  # type: ignore[arg-type]
        assert result.success is False

    def test_session_patch_prose_with_patch_tag_rejected(self) -> None:
        """Prose wrapping a <SESSION_PATCH> tag must not be accepted."""
        checker = QualityChecker()
        content = (
            "I will now apply the session patch.\n\n"
            '<SESSION_PATCH file="src/main.py">\n'
            '{"old": "pass", "new": "return 0"}\n'
            "</SESSION_PATCH>\n\n"
            "Done."
        )
        data, _errors = checker._extract_json(content, role="chief_engineer")
        # The <SESSION_PATCH> content is not a valid CE blueprint.
        assert data is None or "construction_plan" not in (data or {})


class TestR14DCeSchemaPriorityOverUnrelatedJson:
    """R14-D regression: QualityChooser must prefer the CE schema JSON
    object over unrelated earlier JSON objects.

    When LLM output contains multiple JSON objects (e.g., debug info
    followed by the actual CE review), the role-aware extraction must
    select the one matching the CE schema blueprint keys.
    """

    def test_ce_schema_chosen_over_earlier_unrelated_json(self) -> None:
        """Unrelated JSON before valid CE blueprint: CE blueprint selected."""
        checker = QualityChecker()
        content = (
            '{"debug_info": "some internal state", "count": 42}\n\n'
            "Here is the review:\n\n"
            '{"construction_plan": {"phases": ["impl"]}, '
            '"scope_for_apply": ["src/main.py"], '
            '"risk_flags": ["medium"]}'
        )
        data, _errors = checker._extract_json(content, role="chief_engineer")
        assert data is not None
        assert "construction_plan" in data
        assert "scope_for_apply" in data

    def test_multiple_json_only_ce_schema_selected(self) -> None:
        """With multiple JSON objects, only the one matching CE schema
        blueprint keys should be selected for chief_engineer role."""
        checker = QualityChecker()
        content = (
            '{"status": "ok", "version": 1}\n\n'
            '{"task_count": 5, "completed": 3}\n\n'
            '{"construction_plan": {"impl": true}, '
            '"scope_for_apply": ["a.py"], '
            '"risk_flags": []}'
        )
        data, _errors = checker._extract_json(content, role="chief_engineer")
        assert data is not None
        assert "construction_plan" in data
        assert "scope_for_apply" in data
        assert "risk_flags" in data

    def test_ce_schema_with_fenced_block_among_distractors(self) -> None:
        """Fenced CE JSON block among distractor JSON objects is selected."""
        checker = QualityChecker()
        content = (
            '{"not_ce": True}\n\n'
            "```json\n"
            '{"construction_plan": {"steps": []}, '
            '"scope_for_apply": ["x.py"], '
            '"risk_flags": ["high"]}\n'
            "```"
        )
        data, _errors = checker._extract_json(content, role="chief_engineer")
        assert data is not None
        assert "construction_plan" in data
        assert "scope_for_apply" in data

    def test_no_ce_schema_returns_diagnostic_error(self) -> None:
        """When no JSON matches CE schema, return a diagnostic error
        listing missing blueprint keys (R14-B schema-aware behavior)."""
        checker = QualityChecker()
        content = '{"unrelated": 1}\n\n{"also_unrelated": 2}'
        data, errors = checker._extract_json(content, role="chief_engineer")
        assert data is None
        assert len(errors) > 0
        assert any("blueprint keys" in e for e in errors)

    def test_ce_schema_in_nested_fence_among_plain_json(self) -> None:
        """CE blueprint in a fenced block is found even when plain JSON
        objects appear first."""
        checker = QualityChecker()
        content = (
            '{"log": "debug output"}\n\n'
            '{"meta": "info"}\n\n'
            "```json\n"
            '{"construction_plan": {"impl": true}, '
            '"scope_for_apply": ["src/main.py"], '
            '"risk_flags": ["low"]}\n'
            "```"
        )
        data, _errors = checker._extract_json(content, role="chief_engineer")
        assert data is not None
        assert "construction_plan" in data
