"""Tests for business_validators module."""

from __future__ import annotations

import json

from polaris.domain.verification.business_validators import (
    _extract_json_object,
    validate_director_evidence,
    validate_director_safe_scope,
    validate_docs_template,
    validate_no_hallucinated_paths,
    validate_pm_plan_json,
    validate_qa_json,
    validate_qa_passfail,
)


# =============================================================================
# _extract_json_object
# =============================================================================
def test_extract_json_object_empty_and_none():
    assert _extract_json_object("") is None
    assert _extract_json_object("   ") is None
    assert _extract_json_object(None) is None


def test_extract_json_object_plain_json():
    assert _extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_json_object_markdown_block():
    text = '```json\n{"key": "value"}\n```'
    assert _extract_json_object(text) == {"key": "value"}


def test_extract_json_object_markdown_no_lang():
    text = '```\n{"key": "value"}\n```'
    assert _extract_json_object(text) == {"key": "value"}


def test_extract_json_object_nested_braces():
    text = '{"outer": {"inner": true}}'
    assert _extract_json_object(text) == {"outer": {"inner": True}}


def test_extract_json_object_invalid_json():
    assert _extract_json_object("{not json}") is None
    assert _extract_json_object("just text") is None


def test_extract_json_object_list_root():
    # List root should be skipped; returns None since only dict is accepted
    assert _extract_json_object("[1, 2, 3]") is None


# =============================================================================
# validate_pm_plan_json
# =============================================================================
def test_validate_pm_plan_json_valid():
    text = json.dumps({"goal": "x", "backlog": [], "timeline": "1w"})
    ok, msg = validate_pm_plan_json(text)
    assert ok is True
    assert msg == "Valid"


def test_validate_pm_plan_json_missing_keys():
    text = json.dumps({"goal": "x"})
    ok, msg = validate_pm_plan_json(text)
    assert ok is False
    assert "Missing keys" in msg
    assert "backlog" in msg and "timeline" in msg


def test_validate_pm_plan_json_invalid_json():
    ok, msg = validate_pm_plan_json("not json")
    assert ok is False
    assert "Invalid JSON" in msg


def test_validate_pm_plan_json_non_dict_root():
    ok, msg = validate_pm_plan_json("[1, 2, 3]")
    assert ok is False
    assert "Root must be an object" in msg


def test_validate_pm_plan_json_from_markdown():
    text = "```json\n" + json.dumps({"goal": "x", "backlog": [], "timeline": "1w"}) + "\n```"
    ok, _msg = validate_pm_plan_json(text)
    assert ok is True


# =============================================================================
# validate_director_safe_scope
# =============================================================================
def test_validate_director_safe_scope_safe_text():
    ok, msg = validate_director_safe_scope("Modify src/app.py only")
    assert ok is True
    assert msg == "Safe"


def test_validate_director_safe_scope_restricted_update_docs():
    ok, msg = validate_director_safe_scope("Plan: update docs/api.md")
    assert ok is False
    assert "restricted operation on docs/" in msg.lower()


def test_validate_director_safe_scope_never_context():
    ok, msg = validate_director_safe_scope("We never update docs/ in this task")
    assert ok is True
    assert msg == "Safe"


def test_validate_director_safe_scope_not_context():
    ok, msg = validate_director_safe_scope("We do not modify scripts/ here")
    assert ok is True
    assert msg == "Safe"


def test_validate_director_safe_scope_json_scope():
    payload = json.dumps({"scope": ["update docs/readme.md"]})
    ok, _msg = validate_director_safe_scope(payload)
    assert ok is False


def test_validate_director_safe_scope_json_safe():
    payload = json.dumps({"scope": ["src/main.py"]})
    ok, _msg = validate_director_safe_scope(payload)
    assert ok is True


def test_validate_director_safe_scope_plain_text_restricted_scripts():
    ok, msg = validate_director_safe_scope("Write to scripts/deploy.sh")
    assert ok is False
    assert "scripts/" in msg.lower()


# =============================================================================
# validate_director_evidence
# =============================================================================
def test_validate_director_evidence_valid():
    text = json.dumps({"evidence": ["file1.py"]})
    ok, msg = validate_director_evidence(text)
    assert ok is True
    assert msg == "Evidence present"


def test_validate_director_evidence_empty():
    text = json.dumps({"evidence": []})
    ok, msg = validate_director_evidence(text)
    assert ok is True
    assert msg == "No evidence required"


def test_validate_director_evidence_no_key():
    text = json.dumps({"other": "value"})
    ok, msg = validate_director_evidence(text)
    assert ok is True
    assert msg == "No evidence required"


def test_validate_director_evidence_invalid_json():
    ok, msg = validate_director_evidence("not json")
    assert ok is False
    assert msg == "Invalid JSON"


def test_validate_director_evidence_non_dict():
    ok, msg = validate_director_evidence("[1, 2]")
    assert ok is False
    assert "Root must be an object" in msg


# =============================================================================
# validate_no_hallucinated_paths
# =============================================================================
def test_validate_no_hallucinated_paths_no_known_paths():
    ok, msg = validate_no_hallucinated_paths("/some/path", None)
    assert ok is True
    assert "No known paths" in msg


def test_validate_no_hallucinated_paths_empty_known_paths():
    ok, msg = validate_no_hallucinated_paths("/some/path", [])
    assert ok is True
    assert "No known paths" in msg


def test_validate_no_hallucinated_paths_valid_path():
    ok, msg = validate_no_hallucinated_paths("See /src/main.py", ["/src/main.py", "/src/util.py"])
    assert ok is True
    assert "No hallucinated paths" in msg


def test_validate_no_hallucinated_paths_hallucinated():
    ok, msg = validate_no_hallucinated_paths("Check /fake/path.py", ["/real/path.py"])
    assert ok is False
    assert "Hallucinated paths" in msg
    assert "/fake/path.py" in msg


def test_validate_no_hallucinated_paths_mixed():
    text = 'Use "/real/a.py" and "/fake/b.py"'
    ok, msg = validate_no_hallucinated_paths(text, ["/real/a.py"])
    assert ok is False
    assert "/fake/b.py" in msg


def test_validate_no_hallucinated_paths_windows_style():
    ok, _msg = validate_no_hallucinated_paths("C:\\Users\\test\\file.py", ["C:\\Users\\test\\file.py"])
    assert ok is True


def test_validate_no_hallucinated_paths_case_insensitive():
    ok, _msg = validate_no_hallucinated_paths("/SRC/Main.PY", ["/src/main.py"])
    assert ok is True


# =============================================================================
# validate_qa_json
# =============================================================================
def test_validate_qa_json_with_questions():
    text = json.dumps({"questions": [{"q": "a?"}]})
    ok, msg = validate_qa_json(text)
    assert ok is True
    assert msg == "Valid"


def test_validate_qa_json_with_items():
    text = json.dumps({"items": [{"id": 1}]})
    ok, _msg = validate_qa_json(text)
    assert ok is True


def test_validate_qa_json_missing_both():
    text = json.dumps({"other": "value"})
    ok, msg = validate_qa_json(text)
    assert ok is False
    assert "questions" in msg and "items" in msg


def test_validate_qa_json_invalid_json():
    ok, msg = validate_qa_json("bad json")
    assert ok is False
    assert "Invalid JSON" in msg


def test_validate_qa_json_non_dict():
    ok, msg = validate_qa_json("[]")
    assert ok is False
    assert "Root must be an object" in msg


# =============================================================================
# validate_qa_passfail
# =============================================================================
def test_validate_qa_passfail_passed_true():
    ok, msg = validate_qa_passfail({"passed": True})
    assert ok is True
    assert msg == "Pass"


def test_validate_qa_passfail_passed_false():
    ok, msg = validate_qa_passfail({"passed": False})
    assert ok is False
    assert msg == "Fail"


def test_validate_qa_passfail_pass_key():
    ok, _msg = validate_qa_passfail({"pass": True})
    assert ok is True


def test_validate_qa_passfail_success_key():
    ok, _msg = validate_qa_passfail({"success": True})
    assert ok is True


def test_validate_qa_passfail_none_found():
    ok, msg = validate_qa_passfail({"score": 85})
    assert ok is False
    assert "No pass/fail indicator" in msg


def test_validate_qa_passfail_explicit_false_not_none():
    # Explicit False should be handled, not treated as missing
    ok, msg = validate_qa_passfail({"passed": False, "score": 85})
    assert ok is False
    assert msg == "Fail"


# =============================================================================
# validate_docs_template
# =============================================================================
def test_validate_docs_template_valid():
    text = json.dumps(
        {
            "goal": "x",
            "in_scope": ["a"],
            "out_of_scope": ["b"],
            "constraints": ["c"],
        }
    )
    ok, msg = validate_docs_template(text)
    assert ok is True
    assert msg == "Valid"


def test_validate_docs_template_missing_fields():
    text = json.dumps({"goal": "x"})
    ok, msg = validate_docs_template(text)
    assert ok is False
    assert "Missing fields" in msg
    assert "in_scope" in msg
    assert "out_of_scope" in msg
    assert "constraints" in msg


def test_validate_docs_template_invalid_json():
    ok, msg = validate_docs_template("not json")
    assert ok is False
    assert "Invalid JSON" in msg


def test_validate_docs_template_non_dict():
    ok, msg = validate_docs_template("[1]")
    assert ok is False
    assert "Root must be an object" in msg
