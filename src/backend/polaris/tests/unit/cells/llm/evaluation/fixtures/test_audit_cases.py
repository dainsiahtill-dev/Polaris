"""Tests for polaris.cells.llm.evaluation.fixtures.agentic_benchmark.audit_cases.

Covers the pure audit functions that validate benchmark case JSON structure.
All tests use in-memory dicts; no filesystem I/O is performed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from polaris.cells.llm.evaluation.fixtures.agentic_benchmark.audit_cases import (
    CaseIssue,
    audit_case_id_naming,
    audit_empty_required_tools,
    audit_forbidden_output_logic,
    audit_json_structure,
    audit_output_substrings,
    audit_role_consistency,
    audit_score_threshold,
    audit_strict_refusal_cases,
    audit_tool_call_bounds,
    audit_tool_references,
    audit_validators,
)


class TestCaseIssue:
    """Tests for the CaseIssue dataclass-like helper."""

    def test_repr_without_field(self) -> None:
        issue = CaseIssue("l1_test", "ERROR", "STRUCTURE", "Missing field")
        repr_str = repr(issue)
        assert "l1_test" in repr_str
        assert "ERROR" in repr_str
        assert "Missing field" in repr_str

    def test_repr_with_field(self) -> None:
        issue = CaseIssue("l1_test", "WARNING", "LOGIC", "Bad value", field="judge")
        repr_str = repr(issue)
        assert "[judge]" in repr_str
        assert "Bad value" in repr_str

    def test_attributes_set_correctly(self) -> None:
        issue = CaseIssue("c1", "ERROR", "TYPE", "msg", "field1")
        assert issue.case_id == "c1"
        assert issue.severity == "ERROR"
        assert issue.category == "TYPE"
        assert issue.message == "msg"
        assert issue.field == "field1"


class TestAuditJsonStructure:
    """Tests for audit_json_structure."""

    def _make_case(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        base = {
            "case_id": "l1_test_case",
            "role": "director",
            "title": "Test",
            "prompt": "Do something",
            "judge": {"score_threshold": 0.75},
        }
        if overrides:
            base.update(overrides)
        return base

    def test_valid_case_no_issues(self) -> None:
        case = self._make_case()
        issues = audit_json_structure(Path("test.json"), case)
        assert len(issues) == 0

    @pytest.mark.parametrize("missing_field", ["case_id", "role", "title", "prompt", "judge"])
    def test_missing_required_field(self, missing_field: str) -> None:
        case = self._make_case()
        del case[missing_field]
        issues = audit_json_structure(Path("test.json"), case)
        assert len(issues) >= 1
        assert any(missing_field in i.message for i in issues)

    def test_judge_not_dict(self) -> None:
        case = self._make_case({"judge": "not_a_dict"})
        issues = audit_json_structure(Path("test.json"), case)
        assert any("judge must be a dict" in i.message for i in issues)

    def test_judge_missing_score_threshold(self) -> None:
        case = self._make_case({"judge": {}})
        issues = audit_json_structure(Path("test.json"), case)
        assert any("score_threshold" in i.message for i in issues)


class TestAuditToolReferences:
    """Tests for audit_tool_references."""

    def _make_case(self, judge_overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        base = {
            "case_id": "l1_test",
            "judge": {
                "score_threshold": 0.75,
                "required_tools": [],
                "forbidden_tools": [],
                "required_tool_arguments": [],
                "forbidden_tool_arguments": [],
            },
        }
        if judge_overrides:
            base["judge"].update(judge_overrides)
        return base

    def test_valid_tools_no_issues(self) -> None:
        case = self._make_case(judge_overrides={"required_tools": ["repo_tree", "read_file"]})
        issues = audit_tool_references(Path("test.json"), case)
        assert len(issues) == 0

    def test_unknown_required_tool(self) -> None:
        case = self._make_case(judge_overrides={"required_tools": ["unknown_tool"]})
        issues = audit_tool_references(Path("test.json"), case)
        assert len(issues) == 1
        assert issues[0].category == "INVALID_TOOL"
        assert "unknown_tool" in issues[0].message

    def test_unknown_forbidden_tool(self) -> None:
        case = self._make_case(judge_overrides={"forbidden_tools": ["fake_tool"]})
        issues = audit_tool_references(Path("test.json"), case)
        assert len(issues) == 1
        assert "fake_tool" in issues[0].message

    def test_tool_in_both_required_and_forbidden(self) -> None:
        case = self._make_case(judge_overrides={"required_tools": ["repo_tree"], "forbidden_tools": ["repo_tree"]})
        issues = audit_tool_references(Path("test.json"), case)
        assert any(i.category == "CONFLICT" for i in issues)

    def test_unknown_tool_in_required_arguments(self) -> None:
        case = self._make_case(
            judge_overrides={"required_tool_arguments": [{"tools": ["bad_tool"]}]},
        )
        issues = audit_tool_references(Path("test.json"), case)
        assert any("bad_tool" in i.message for i in issues)

    def test_unknown_tool_in_forbidden_arguments(self) -> None:
        case = self._make_case(
            judge_overrides={"forbidden_tool_arguments": [{"tools": ["bad_tool"]}]},
        )
        issues = audit_tool_references(Path("test.json"), case)
        assert any("bad_tool" in i.message for i in issues)


class TestAuditToolCallBounds:
    """Tests for audit_tool_call_bounds."""

    def _make_case(self, judge_overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        base = {
            "case_id": "l1_test",
            "judge": {
                "score_threshold": 0.75,
                "min_tool_calls": 0,
            },
        }
        if judge_overrides:
            base["judge"].update(judge_overrides)
        return base

    def test_valid_bounds_no_issues(self) -> None:
        case = self._make_case(
            judge_overrides={"min_tool_calls": 1, "max_tool_calls": 5, "required_tools": ["repo_tree"]}
        )
        issues = audit_tool_call_bounds(Path("test.json"), case)
        assert len(issues) == 0

    def test_min_greater_than_max(self) -> None:
        case = self._make_case(judge_overrides={"min_tool_calls": 5, "max_tool_calls": 1})
        issues = audit_tool_call_bounds(Path("test.json"), case)
        assert any("min_tool_calls" in i.message and "max_tool_calls" in i.message for i in issues)

    def test_min_tool_calls_not_int(self) -> None:
        case = self._make_case(judge_overrides={"min_tool_calls": "five"})
        issues = audit_tool_call_bounds(Path("test.json"), case)
        assert any(i.category == "TYPE" for i in issues)

    def test_max_tool_calls_not_int(self) -> None:
        case = self._make_case(judge_overrides={"max_tool_calls": "five"})
        issues = audit_tool_call_bounds(Path("test.json"), case)
        assert any(i.category == "TYPE" for i in issues)

    def test_min_positive_without_required_tools_warning(self) -> None:
        case = self._make_case(
            judge_overrides={"min_tool_calls": 2, "max_tool_calls": 5, "required_tools": []},
        )
        issues = audit_tool_call_bounds(Path("test.json"), case)
        assert any(i.severity == "WARNING" for i in issues)


class TestAuditOutputSubstrings:
    """Tests for audit_output_substrings."""

    def _make_case(self, required: list[str] | None = None, forbidden: list[str] | None = None) -> dict[str, Any]:
        return {
            "case_id": "l1_test",
            "judge": {
                "score_threshold": 0.75,
                "required_output_substrings": required or [],
                "forbidden_output_substrings": forbidden or [],
            },
        }

    def test_no_conflict_no_issues(self) -> None:
        case = self._make_case(required=["hello"], forbidden=["world"])
        issues = audit_output_substrings(Path("test.json"), case)
        assert len(issues) == 0

    def test_same_substring_in_both_lists(self) -> None:
        case = self._make_case(required=["overlap"], forbidden=["overlap"])
        issues = audit_output_substrings(Path("test.json"), case)
        assert len(issues) == 1
        assert issues[0].category == "CONFLICT"


class TestAuditRoleConsistency:
    """Tests for audit_role_consistency."""

    def _make_case(self, role: str = "director") -> dict[str, Any]:
        return {"case_id": "l1_test", "role": role}

    @pytest.mark.parametrize(
        "valid_role",
        [
            "director",
            "pm",
            "architect",
            "chief_engineer",
            "qa",
            "scout",
            "all",
            "default",
            "benchmark",
            "agentic",
        ],
    )
    def test_valid_roles_no_issues(self, valid_role: str) -> None:
        case = self._make_case(role=valid_role)
        issues = audit_role_consistency(Path("test.json"), case)
        assert len(issues) == 0

    def test_invalid_role_warns(self) -> None:
        case = self._make_case(role="invalid_role")
        issues = audit_role_consistency(Path("test.json"), case)
        assert len(issues) == 1
        assert issues[0].category == "ROLE"

    def test_role_case_insensitive(self) -> None:
        case = self._make_case(role="DIRECTOR")
        issues = audit_role_consistency(Path("test.json"), case)
        assert len(issues) == 0


class TestAuditScoreThreshold:
    """Tests for audit_score_threshold."""

    def _make_case(self, threshold: Any = 0.75) -> dict[str, Any]:
        return {"case_id": "l1_test", "judge": {"score_threshold": threshold}}

    def test_valid_threshold_no_issues(self) -> None:
        case = self._make_case(0.5)
        issues = audit_score_threshold(Path("test.json"), case)
        assert len(issues) == 0

    def test_threshold_at_boundaries(self) -> None:
        assert len(audit_score_threshold(Path("test.json"), self._make_case(0.0))) == 0
        assert len(audit_score_threshold(Path("test.json"), self._make_case(1.0))) == 0

    def test_threshold_below_zero(self) -> None:
        case = self._make_case(-0.1)
        issues = audit_score_threshold(Path("test.json"), case)
        assert len(issues) == 1
        assert issues[0].category == "RANGE"

    def test_threshold_above_one(self) -> None:
        case = self._make_case(1.5)
        issues = audit_score_threshold(Path("test.json"), case)
        assert len(issues) == 1
        assert issues[0].category == "RANGE"

    def test_non_numeric_threshold(self) -> None:
        case = self._make_case("high")
        issues = audit_score_threshold(Path("test.json"), case)
        assert len(issues) == 1
        assert issues[0].category == "TYPE"


class TestAuditValidators:
    """Tests for audit_validators."""

    def _make_case(self, validators: list[str] | None = None) -> dict[str, Any]:
        return {
            "case_id": "l1_test",
            "judge": {"score_threshold": 0.75, "validators": validators or []},
        }

    def test_known_validator_no_issue(self) -> None:
        case = self._make_case(["safety_check", "no_prompt_leakage"])
        issues = audit_validators(Path("test.json"), case)
        assert len(issues) == 0

    def test_unknown_validator_warns(self) -> None:
        case = self._make_case(["unknown_validator"])
        issues = audit_validators(Path("test.json"), case)
        assert len(issues) == 1
        assert issues[0].category == "UNKNOWN_VALIDATOR"

    def test_validator_prefix_allowed(self) -> None:
        case = self._make_case(["validator:custom"])
        issues = audit_validators(Path("test.json"), case)
        assert len(issues) == 0

    def test_mixed_valid_and_invalid(self) -> None:
        case = self._make_case(["safety_check", "bogus"])
        issues = audit_validators(Path("test.json"), case)
        assert len(issues) == 1
        assert "bogus" in issues[0].message


class TestAuditCaseIdNaming:
    """Tests for audit_case_id_naming."""

    def test_valid_case_id_no_issue(self) -> None:
        case = {"case_id": "l1_test_case"}
        issues = audit_case_id_naming(Path("test.json"), case)
        assert len(issues) == 0

    @pytest.mark.parametrize(
        "bad_id",
        [
            "test_case",
            "L1_test",
            "l1-test",
            "l1_",
            "",
            "l12_test",
        ],
    )
    def test_invalid_case_id_warns(self, bad_id: str) -> None:
        case = {"case_id": bad_id}
        issues = audit_case_id_naming(Path("test.json"), case)
        assert len(issues) == 1
        assert issues[0].category == "NAMING"


class TestAuditForbiddenOutputLogic:
    """Tests for audit_forbidden_output_logic."""

    def _make_case(self, prompt: str = "", forbidden: list[str] | None = None) -> dict[str, Any]:
        return {
            "case_id": "l1_test",
            "prompt": prompt,
            "judge": {
                "score_threshold": 0.75,
                "forbidden_output_substrings": forbidden or [],
            },
        }

    def test_no_conflict_no_issue(self) -> None:
        case = self._make_case(prompt="Hello", forbidden=["world"])
        issues = audit_forbidden_output_logic(Path("test.json"), case)
        assert len(issues) == 0

    def test_todo_in_prompt_and_forbidden_warns(self) -> None:
        case = self._make_case(prompt="Check the TODO list", forbidden=["TODO"])
        issues = audit_forbidden_output_logic(Path("test.json"), case)
        assert len(issues) == 1
        assert "TODO" in issues[0].message


class TestAuditEmptyRequiredTools:
    """Tests for audit_empty_required_tools."""

    def _make_case(
        self, min_calls: int = 0, required: list[str] | None = None, forbidden: list[str] | None = None
    ) -> dict[str, Any]:
        return {
            "case_id": "l1_test",
            "judge": {
                "score_threshold": 0.75,
                "min_tool_calls": min_calls,
                "required_tools": required or [],
                "forbidden_tools": forbidden or [],
            },
        }

    def test_no_min_calls_no_issue(self) -> None:
        case = self._make_case(min_calls=0)
        issues = audit_empty_required_tools(Path("test.json"), case)
        assert len(issues) == 0

    def test_min_calls_without_tools_errors(self) -> None:
        case = self._make_case(min_calls=2)
        issues = audit_empty_required_tools(Path("test.json"), case)
        assert len(issues) == 1
        assert issues[0].severity == "ERROR"

    def test_min_calls_with_required_tools_ok(self) -> None:
        case = self._make_case(min_calls=2, required=["repo_tree"])
        issues = audit_empty_required_tools(Path("test.json"), case)
        assert len(issues) == 0


class TestAuditStrictRefusalCases:
    """Tests for audit_strict_refusal_cases."""

    def _make_case(
        self, min_calls: int = 0, max_calls: int | None = None, forbidden: list[str] | None = None, prompt: str = ""
    ) -> dict[str, Any]:
        judge: dict[str, Any] = {
            "score_threshold": 0.75,
            "min_tool_calls": min_calls,
            "forbidden_tools": forbidden or [],
        }
        if max_calls is not None:
            judge["max_tool_calls"] = max_calls
        return {"case_id": "l1_test", "prompt": prompt, "judge": judge}

    def test_zero_calls_with_dangerous_forbidden_ok(self) -> None:
        case = self._make_case(min_calls=0, max_calls=0, forbidden=["execute_command"])
        issues = audit_strict_refusal_cases(Path("test.json"), case)
        assert len(issues) == 0

    def test_zero_calls_without_dangerous_tools_warns(self) -> None:
        case = self._make_case(min_calls=0, max_calls=0, forbidden=["repo_tree"])
        issues = audit_strict_refusal_cases(Path("test.json"), case)
        assert len(issues) == 1
        assert issues[0].severity == "WARNING"

    def test_non_zero_calls_no_issue(self) -> None:
        case = self._make_case(min_calls=1, max_calls=5)
        issues = audit_strict_refusal_cases(Path("test.json"), case)
        assert len(issues) == 0
