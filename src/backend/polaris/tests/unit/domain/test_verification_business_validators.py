"""Tests for polaris.domain.verification.business_validators."""

from __future__ import annotations

from polaris.domain.verification.business_validators import (
    validate_director_evidence,
    validate_director_safe_scope,
    validate_docs_template,
    validate_no_hallucinated_paths,
    validate_pm_plan_json,
    validate_qa_json,
    validate_qa_passfail,
)


class TestValidatePmPlanJson:
    def test_valid(self) -> None:
        ok, msg = validate_pm_plan_json('{"goal": "G", "backlog": [], "timeline": "T"}')
        assert ok is True
        assert msg == "Valid"

    def test_missing_keys(self) -> None:
        ok, _msg = validate_pm_plan_json('{"goal": "G"}')
        assert ok is False
        assert "Missing keys" in _msg

    def test_invalid_json(self) -> None:
        ok, _msg = validate_pm_plan_json("not json")
        assert ok is False
        assert "Invalid JSON" in _msg

    def test_not_dict(self) -> None:
        ok, _msg = validate_pm_plan_json("[1, 2, 3]")
        assert ok is False
        assert "Root must be an object" in _msg

    def test_extracts_from_markdown(self) -> None:
        text = '```json\n{"goal": "G", "backlog": [], "timeline": "T"}\n```'
        ok, _msg = validate_pm_plan_json(text)
        assert ok is True


class TestValidateDirectorSafeScope:
    def test_safe(self) -> None:
        ok, msg = validate_director_safe_scope('{"scope": ["src"]}')
        assert ok is True
        assert msg == "Safe"

    def test_restricted_update_docs(self) -> None:
        ok, _msg = validate_director_safe_scope("update docs/readme")
        assert ok is False
        assert "docs/" in _msg

    def test_restricted_with_never_context(self) -> None:
        ok, _msg = validate_director_safe_scope("never update docs/readme")
        assert ok is True

    def test_plain_text_safe(self) -> None:
        ok, _msg = validate_director_safe_scope("modify src/main.py")
        assert ok is True


class TestValidateDirectorEvidence:
    def test_no_evidence(self) -> None:
        ok, _msg = validate_director_evidence("{}")
        assert ok is True

    def test_with_evidence(self) -> None:
        ok, _msg = validate_director_evidence('{"evidence": ["a.py"]}')
        assert ok is True

    def test_invalid_json(self) -> None:
        ok, _msg = validate_director_evidence("not json")
        assert ok is False


class TestValidateNoHallucinatedPaths:
    def test_no_paths(self) -> None:
        ok, _msg = validate_no_hallucinated_paths("hello world", ["/known"])
        assert ok is True

    def test_known_path(self) -> None:
        ok, _msg = validate_no_hallucinated_paths("/known/file", ["/known/file"])
        assert ok is True

    def test_hallucinated_path(self) -> None:
        ok, _msg = validate_no_hallucinated_paths("/unknown/file", ["/known"])
        assert ok is False
        assert "Hallucinated" in _msg

    def test_no_known_paths(self) -> None:
        ok, _msg = validate_no_hallucinated_paths("/any/path")
        assert ok is True
        assert "No known paths" in _msg


class TestValidateQaJson:
    def test_valid_questions(self) -> None:
        ok, _msg = validate_qa_json('{"questions": ["q1"]}')
        assert ok is True

    def test_valid_items(self) -> None:
        ok, _msg = validate_qa_json('{"items": ["i1"]}')
        assert ok is True

    def test_missing_keys(self) -> None:
        ok, _msg = validate_qa_json('{"other": "value"}')
        assert ok is False
        assert "Missing" in _msg

    def test_invalid_json(self) -> None:
        ok, _msg = validate_qa_json("not json")
        assert ok is False


class TestValidateQaPassfail:
    def test_passed_true(self) -> None:
        ok, msg = validate_qa_passfail({"passed": True})
        assert ok is True
        assert msg == "Pass"

    def test_pass_false(self) -> None:
        ok, _msg = validate_qa_passfail({"pass": False})
        assert ok is False
        assert _msg == "Fail"

    def test_success_true(self) -> None:
        ok, _msg = validate_qa_passfail({"success": True})
        assert ok is True

    def test_no_indicator(self) -> None:
        ok, _msg = validate_qa_passfail({})
        assert ok is False
        assert "No pass/fail" in _msg

    def test_bool_false_value(self) -> None:
        ok, _msg = validate_qa_passfail({"passed": False})
        assert ok is False


class TestValidateDocsTemplate:
    def test_valid(self) -> None:
        ok, _msg = validate_docs_template('{"goal": "G", "in_scope": [], "out_of_scope": [], "constraints": []}')
        assert ok is True

    def test_missing_fields(self) -> None:
        ok, _msg = validate_docs_template('{"goal": "G"}')
        assert ok is False
        assert "Missing fields" in _msg

    def test_invalid_json(self) -> None:
        ok, _msg = validate_docs_template("not json")
        assert ok is False
