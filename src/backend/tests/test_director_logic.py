"""Tests for Director Logic module.

Tests cover:
- JSON payload parsing
- Acceptance decision parsing
- Defect ticket extraction and validation
- PM payload compaction
- File validation
- Write gate checks
"""

import os
import sys

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from polaris.cells.director.execution.internal.director_logic_rules import (
    compact_pm_payload,
    extract_defect_ticket,
    extract_required_evidence,
    parse_acceptance,
    parse_json_payload,
    validate_defect_ticket,
    validate_files_to_edit,
    write_gate_check,
)


class TestParseJsonPayload:
    """Test JSON payload parsing."""

    def test_parse_valid_json(self):
        """Should parse valid JSON."""
        text = '{"key": "value", "number": 42}'
        result = parse_json_payload(text)
        assert result == {"key": "value", "number": 42}

    def test_parse_json_with_markdown_code_block(self):
        """Should parse JSON inside markdown code blocks."""
        text = '''```json
{"key": "value"}
```'''
        result = parse_json_payload(text)
        assert result == {"key": "value"}

    def test_parse_json_with_generic_code_block(self):
        """Should parse JSON inside generic markdown blocks."""
        text = '''```
{"key": "value"}
```'''
        result = parse_json_payload(text)
        assert result == {"key": "value"}

    def test_parse_json_embedded_in_text(self):
        """Should extract JSON embedded in text."""
        text = 'Some text before {"key": "value"} some text after'
        result = parse_json_payload(text)
        assert result == {"key": "value"}

    def test_parse_empty_string(self):
        """Should handle empty string."""
        result = parse_json_payload("")
        assert result is None

    def test_parse_invalid_json(self):
        """Should return None for invalid JSON."""
        result = parse_json_payload("not json at all")
        assert result is None

    def test_parse_none(self):
        """Should handle None input."""
        result = parse_json_payload(None)  # type: ignore
        assert result is None


class TestParseAcceptance:
    """Test acceptance decision parsing."""

    def test_parse_json_acceptance_true(self):
        """Should parse JSON with acceptance=true."""
        text = '{"acceptance": true}'
        result = parse_acceptance(text)
        assert result is True

    def test_parse_json_acceptance_false(self):
        """Should parse JSON with acceptance=false."""
        text = '{"acceptance": false}'
        result = parse_acceptance(text)
        assert result is False

    def test_parse_json_acceptance_pass_string(self):
        """Should parse JSON with acceptance='PASS'."""
        text = '{"acceptance": "PASS"}'
        result = parse_acceptance(text)
        assert result is True

    def test_parse_json_acceptance_fail_string(self):
        """Should parse JSON with acceptance='FAIL'."""
        text = '{"acceptance": "FAIL"}'
        result = parse_acceptance(text)
        assert result is False

    def test_parse_marker_line_acceptance(self):
        """Should parse ACCEPTANCE_DECISION: marker."""
        text = "Some text\nACCEPTANCE_DECISION: PASS\nMore text"
        result = parse_acceptance(text)
        assert result is True

    def test_parse_short_marker_line_acceptance(self):
        """Should parse ACCEPTANCE: marker."""
        text = "Some text\nACCEPTANCE: FAIL\nMore text"
        result = parse_acceptance(text)
        assert result is False

    def test_parse_fuzzy_pass(self):
        """Should fuzzy match 'pass' in text."""
        text = "The acceptance decision is to pass this"
        result = parse_acceptance(text)
        assert result is True

    def test_parse_fuzzy_fail(self):
        """Should fuzzy match 'fail' in text."""
        text = "The acceptance decision is to fail this"
        result = parse_acceptance(text)
        assert result is False

    def test_parse_ambiguous(self):
        """Should return None for ambiguous text."""
        text = "This text contains both pass and fail"
        result = parse_acceptance(text)
        assert result is None

    def test_parse_empty(self):
        """Should return None for empty text."""
        result = parse_acceptance("")
        assert result is None

    def test_parse_none(self):
        """Should handle None input."""
        result = parse_acceptance(None)  # type: ignore
        assert result is None


class TestExtractDefectTicket:
    """Test defect ticket extraction."""

    def test_extract_from_defect_ticket_field(self):
        """Should extract from defect_ticket field."""
        payload = {
            "defect_ticket": {
                "defect_id": "DEFECT-001",
                "severity": "high",
                "repro_steps": ["Step 1", "Step 2"],
            }
        }
        result = extract_defect_ticket(payload)
        assert result["defect_id"] == "DEFECT-001"
        assert result["severity"] == "high"
        assert result["repro_steps"] == ["Step 1", "Step 2"]

    def test_extract_from_root_payload(self):
        """Should extract from root when defect_ticket is missing."""
        payload = {
            "defect_id": "DEFECT-002",
            "severity": "low",
            "repro_steps": "Step 1",
        }
        result = extract_defect_ticket(payload)
        assert result["defect_id"] == "DEFECT-002"
        assert result["severity"] == "low"

    def test_extract_generates_defect_id(self):
        """Should generate defect_id if missing."""
        payload = {
            "summary": "Test summary",
            "findings": ["Finding 1", "Finding 2"],
        }
        result = extract_defect_ticket(payload)
        assert "defect_id" in result
        assert result["defect_id"].startswith("DEFECT-")

    def test_extract_filters_empty_values(self):
        """Should filter out empty values."""
        payload = {
            "defect_ticket": {
                "defect_id": "DEFECT-003",
                "severity": "",
                "repro_steps": [],
                "expected": None,
            }
        }
        result = extract_defect_ticket(payload)
        assert result["defect_id"] == "DEFECT-003"
        assert "severity" not in result
        assert "repro_steps" not in result
        assert "expected" not in result

    def test_extract_none_payload(self):
        """Should handle None payload."""
        result = extract_defect_ticket(None)
        assert result == {}

    def test_extract_non_dict_payload(self):
        """Should handle non-dict payload."""
        result = extract_defect_ticket("not a dict")  # type: ignore
        assert result == {}


class TestValidateDefectTicket:
    """Test defect ticket validation."""

    def test_validate_all_fields_present(self):
        """Should validate when all required fields present."""
        payload = {
            "defect_id": "DEFECT-001",
            "severity": "high",
            "repro_steps": ["Step 1"],
            "expected": "Expected behavior",
            "actual": "Actual behavior",
            "artifact_path": "/path/to/artifact",
            "suspected_scope": "src/module",
        }
        is_valid, ticket, missing = validate_defect_ticket(payload)
        assert is_valid is True
        assert len(missing) == 0

    def test_validate_missing_fields(self):
        """Should report missing fields."""
        payload = {
            "defect_id": "DEFECT-001",
        }
        is_valid, ticket, missing = validate_defect_ticket(payload)
        assert is_valid is False
        assert len(missing) > 0
        assert "severity" in missing

    def test_validate_custom_required_fields(self):
        """Should use custom required fields list."""
        payload = {
            "defect_id": "DEFECT-001",
            "severity": "high",
        }
        is_valid, ticket, missing = validate_defect_ticket(
            payload, required_fields=["defect_id", "severity"]
        )
        assert is_valid is True
        assert len(missing) == 0

    def test_validate_empty_list_field(self):
        """Should flag empty list fields as missing."""
        payload = {
            "defect_id": "DEFECT-001",
            "severity": "high",
            "repro_steps": [],  # Empty list
        }
        is_valid, ticket, missing = validate_defect_ticket(
            payload, required_fields=["defect_id", "severity", "repro_steps"]
        )
        assert is_valid is False
        assert "repro_steps" in missing


class TestCompactPmPayload:
    """Test PM payload compaction."""

    def test_compact_empty_payload(self):
        """Should handle empty payload."""
        result = compact_pm_payload(None, 1000)
        assert result == {}

    def test_compact_non_dict_payload(self):
        """Should handle non-dict payload."""
        result = compact_pm_payload("not a dict", 1000)  # type: ignore
        assert result == {}

    def test_compact_preserves_overall_goal(self):
        """Should preserve overall_goal field."""
        payload = {
            "overall_goal": "Test goal",
            "tasks": [],
        }
        result = compact_pm_payload(payload, 1000)
        assert result["overall_goal"] == "Test goal"

    def test_compact_limits_tasks(self):
        """Should limit number of tasks."""
        payload = {
            "overall_goal": "Test",
            "tasks": [
                {"id": f"task-{i}", "title": f"Task {i}"}
                for i in range(10)
            ],
        }
        result = compact_pm_payload(payload, 500)
        assert len(result["tasks"]) < 10

    def test_compact_ultra_small_max_chars(self):
        """Should create ultra-compact payload when max_chars very small."""
        payload = {
            "overall_goal": "Test goal",
            "focus": "Test focus",
            "tasks": [{"id": "task-1", "title": "Task 1"}],
        }
        result = compact_pm_payload(payload, 50)
        # Should fall back to summary only
        assert "summary" in result

    def test_compact_no_max_chars(self):
        """Should not limit when max_chars is 0."""
        payload = {
            "overall_goal": "Test",
            "tasks": [{"id": "task-1", "title": "Task 1"}],
        }
        result = compact_pm_payload(payload, 0)
        assert result["overall_goal"] == "Test"
        assert len(result["tasks"]) == 1


class TestValidateFilesToEdit:
    """Test file validation."""

    def test_validate_empty_files_list(self):
        """Should pass for empty files list."""
        is_valid, missing, unreadable = validate_files_to_edit([], "/tmp")
        assert is_valid is True
        assert missing == []
        assert unreadable == []

    def test_validate_missing_files(self, tmp_path):
        """Should report missing files."""
        files = ["nonexistent.py"]
        is_valid, missing, unreadable = validate_files_to_edit(
            files, str(tmp_path)
        )
        assert is_valid is True  # Missing files don't make it invalid
        assert len(missing) == 1
        assert "nonexistent.py" in missing

    def test_validate_directory_as_file(self, tmp_path):
        """Should skip directories."""
        (tmp_path / "test_dir").mkdir()
        files = ["test_dir"]
        is_valid, missing, unreadable = validate_files_to_edit(
            files, str(tmp_path)
        )
        assert is_valid is True
        assert unreadable == []

    def test_validate_readable_file(self, tmp_path):
        """Should pass for readable files."""
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")
        files = ["test.py"]
        is_valid, missing, unreadable = validate_files_to_edit(
            files, str(tmp_path)
        )
        assert is_valid is True
        assert missing == []
        assert unreadable == []


class TestWriteGateCheck:
    """Test write gate checking."""

    def test_gate_no_changes_not_required(self):
        """Should pass when no changes and change not required."""
        is_valid, message = write_gate_check(
            changed_files=[],
            act_files=["file.py"],
            require_change=False,
        )
        assert is_valid is True

    def test_gate_no_changes_required(self):
        """Should fail when no changes but change required."""
        is_valid, message = write_gate_check(
            changed_files=[],
            act_files=["file.py"],
            require_change=True,
        )
        assert is_valid is False
        assert "No files changed" in message

    def test_gate_changes_within_scope(self):
        """Should pass when changes within act scope."""
        is_valid, message = write_gate_check(
            changed_files=["file.py"],
            act_files=["file.py"],
        )
        assert is_valid is True
        assert message == ""

    def test_gate_changes_exceed_scope(self):
        """Should warn when changes exceed act scope."""
        is_valid, message = write_gate_check(
            changed_files=["file1.py", "file2.py"],
            act_files=["file1.py"],
        )
        # Non-strict mode allows scope expansion
        assert is_valid is True
        assert "scope" in message.lower()

    def test_gate_with_pm_scope(self):
        """Should check PM target scope."""
        is_valid, message = write_gate_check(
            changed_files=["src/file.py"],
            act_files=["src/file.py"],
            pm_target_files=["src/"],
        )
        assert is_valid is True

    def test_gate_pm_scope_mismatch(self):
        """Should fail when no overlap with PM scope in non-strict mode."""
        is_valid, message = write_gate_check(
            changed_files=["other/file.py"],
            act_files=["other/file.py"],
            pm_target_files=["src/"],
        )
        # Non-strict mode: no overlap with PM scope causes failure
        assert is_valid is False
        assert "scope" in message.lower()

    def test_gate_companion_files_allowed(self):
        """Should allow companion files (tests, docs, etc.)."""
        is_valid, message = write_gate_check(
            changed_files=["src/file.py", "tests/test_file.py"],
            act_files=["src/file.py"],
        )
        assert is_valid is True


class TestExtractRequiredEvidence:
    """Test required evidence extraction."""

    def test_extract_from_root(self):
        """Should extract from root level."""
        payload = {
            "required_evidence": {
                "test_results": True,
                "documentation": True,
            }
        }
        result = extract_required_evidence(payload)
        assert result["test_results"] is True
        assert result["documentation"] is True

    def test_extract_from_task(self):
        """Should extract from first task with evidence."""
        payload = {
            "tasks": [
                {
                    "id": "task-1",
                    "required_evidence": {
                        "coverage_report": True,
                    }
                }
            ]
        }
        result = extract_required_evidence(payload)
        assert result["coverage_report"] is True

    def test_extract_none_payload(self):
        """Should handle None payload."""
        result = extract_required_evidence(None)
        assert result == {}

    def test_extract_no_evidence(self):
        """Should return empty dict when no evidence."""
        payload = {
            "tasks": [{"id": "task-1"}]
        }
        result = extract_required_evidence(payload)
        assert result == {}
