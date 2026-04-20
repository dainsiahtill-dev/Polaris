"""Tests for Attention Runtime schema validation module."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from polaris.kernelone.context.context_os.schemas import (
    REPORT_SCHEMA,
    SUITE_SCHEMA,
    ValidationResult,
    validate_report_file,
    validate_suite_file,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_valid_result(self) -> None:
        """Test valid ValidationResult."""
        result = ValidationResult(
            is_valid=True,
            file_path="/path/to/file.json",
            errors=(),
            warnings=("warning1",),
        )
        assert result.is_valid is True
        assert result.file_path == "/path/to/file.json"
        assert result.errors == ()
        assert result.warnings == ("warning1",)

    def test_invalid_result(self) -> None:
        """Test invalid ValidationResult."""
        result = ValidationResult(
            is_valid=False,
            file_path="/path/to/file.json",
            errors=("error1", "error2"),
            warnings=(),
        )
        assert result.is_valid is False
        assert len(result.errors) == 2
        assert result.warnings == ()


class TestValidateSuiteFile:
    """Tests for suite file validation."""

    def test_valid_suite_json(self, tmp_path: Path) -> None:
        """Test validation of valid JSON suite file."""
        suite_data = {
            "version": 1,
            "suite_id": "test_suite_001",
            "description": "Test suite",
            "cases": [
                {
                    "case_id": "case_1",
                    "conversation": [
                        {"role": "user", "content": "Hello"},
                        {"role": "assistant", "content": "Hi there!"},
                    ],
                },
            ],
        }
        suite_file = tmp_path / "suite.json"
        suite_file.write_text(json.dumps(suite_data), encoding="utf-8")

        result = validate_suite_file(suite_file)

        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_valid_suite_yaml(self, tmp_path: Path) -> None:
        """Test validation of valid YAML suite file."""
        import yaml

        suite_data = {
            "version": 1,
            "suite_id": "test_suite_001",
            "cases": [
                {
                    "case_id": "case_1",
                    "conversation": [
                        {"role": "user", "content": "Hello"},
                    ],
                },
            ],
        }
        suite_file = tmp_path / "suite.yaml"
        suite_file.write_text(yaml.dump(suite_data), encoding="utf-8")

        result = validate_suite_file(suite_file)

        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_missing_file(self) -> None:
        """Test validation of non-existent file."""
        result = validate_suite_file("/nonexistent/path/suite.json")

        assert result.is_valid is False
        assert len(result.errors) == 1
        assert "not found" in result.errors[0].lower()

    def test_invalid_json(self, tmp_path: Path) -> None:
        """Test validation of invalid JSON file."""
        suite_file = tmp_path / "suite.json"
        suite_file.write_text("{ invalid json }", encoding="utf-8")

        result = validate_suite_file(suite_file)

        assert result.is_valid is False
        assert any("Invalid JSON" in e for e in result.errors)

    def test_missing_required_fields(self, tmp_path: Path) -> None:
        """Test validation with missing required fields."""
        suite_data = {
            "version": 1,
            # Missing suite_id and cases
        }
        suite_file = tmp_path / "suite.json"
        suite_file.write_text(json.dumps(suite_data), encoding="utf-8")

        result = validate_suite_file(suite_file)

        assert result.is_valid is False
        assert any("suite_id" in e for e in result.errors)
        assert any("cases" in e for e in result.errors)

    def test_invalid_version(self, tmp_path: Path) -> None:
        """Test validation with invalid version."""
        suite_data = {
            "version": 0,  # Must be >= 1
            "suite_id": "test",
            "cases": [],
        }
        suite_file = tmp_path / "suite.json"
        suite_file.write_text(json.dumps(suite_data), encoding="utf-8")

        result = validate_suite_file(suite_file)

        assert result.is_valid is False
        assert any("version must be >= 1" in e for e in result.errors)

    def test_empty_suite_id(self, tmp_path: Path) -> None:
        """Test validation with empty suite_id."""
        suite_data = {
            "version": 1,
            "suite_id": "",  # Must be non-empty
            "cases": [],
        }
        suite_file = tmp_path / "suite.json"
        suite_file.write_text(json.dumps(suite_data), encoding="utf-8")

        result = validate_suite_file(suite_file)

        assert result.is_valid is False
        assert any("suite_id must be a non-empty" in e for e in result.errors)

    def test_missing_case_id(self, tmp_path: Path) -> None:
        """Test validation with missing case_id."""
        suite_data = {
            "version": 1,
            "suite_id": "test",
            "cases": [
                {
                    # Missing case_id
                    "conversation": [{"role": "user", "content": "Hello"}],
                },
            ],
        }
        suite_file = tmp_path / "suite.json"
        suite_file.write_text(json.dumps(suite_data), encoding="utf-8")

        result = validate_suite_file(suite_file)

        assert result.is_valid is False
        assert any("case_id" in e for e in result.errors)

    def test_missing_conversation(self, tmp_path: Path) -> None:
        """Test validation with missing conversation."""
        suite_data = {
            "version": 1,
            "suite_id": "test",
            "cases": [
                {
                    "case_id": "case_1",
                    # Missing conversation
                },
            ],
        }
        suite_file = tmp_path / "suite.json"
        suite_file.write_text(json.dumps(suite_data), encoding="utf-8")

        result = validate_suite_file(suite_file)

        assert result.is_valid is False
        assert any("conversation" in e for e in result.errors)

    def test_empty_conversation_warning(self, tmp_path: Path) -> None:
        """Test validation with empty conversation (warning)."""
        suite_data = {
            "version": 1,
            "suite_id": "test",
            "cases": [
                {
                    "case_id": "case_1",
                    "conversation": [],  # Empty
                },
            ],
        }
        suite_file = tmp_path / "suite.json"
        suite_file.write_text(json.dumps(suite_data), encoding="utf-8")

        result = validate_suite_file(suite_file)

        assert result.is_valid is True  # Still valid, just warning
        assert any("empty" in w.lower() for w in result.warnings)

    def test_missing_description_warning(self, tmp_path: Path) -> None:
        """Test validation with missing description (warning)."""
        suite_data = {
            "version": 1,
            "suite_id": "test",
            "cases": [],
        }
        suite_file = tmp_path / "suite.json"
        suite_file.write_text(json.dumps(suite_data), encoding="utf-8")

        result = validate_suite_file(suite_file)

        assert result.is_valid is True
        assert any("description" in w.lower() for w in result.warnings)

    def test_missing_role_in_message(self, tmp_path: Path) -> None:
        """Test validation with missing role in conversation message."""
        suite_data = {
            "version": 1,
            "suite_id": "test",
            "cases": [
                {
                    "case_id": "case_1",
                    "conversation": [
                        {"content": "Hello"},  # Missing role
                    ],
                },
            ],
        }
        suite_file = tmp_path / "suite.json"
        suite_file.write_text(json.dumps(suite_data), encoding="utf-8")

        result = validate_suite_file(suite_file)

        assert result.is_valid is False
        assert any("missing role" in e for e in result.errors)

    def test_non_standard_role_warning(self, tmp_path: Path) -> None:
        """Test validation with non-standard role (warning)."""
        suite_data = {
            "version": 1,
            "suite_id": "test",
            "cases": [
                {
                    "case_id": "case_1",
                    "conversation": [
                        {"role": "custom_role", "content": "Hello"},  # Non-standard
                    ],
                },
            ],
        }
        suite_file = tmp_path / "suite.json"
        suite_file.write_text(json.dumps(suite_data), encoding="utf-8")

        result = validate_suite_file(suite_file)

        assert result.is_valid is True  # Still valid
        assert any("custom_role" in w for w in result.warnings)


class TestValidateReportFile:
    """Tests for report file validation."""

    def test_valid_report(self, tmp_path: Path) -> None:
        """Test validation of valid report file."""
        report_data = {
            "version": 1,
            "suite_id": "test_suite_001",
            "generated_at": "2026-03-27T00:00:00+00:00",
            "total_cases": 10,
            "passed_cases": 9,
            "failed_cases": 1,
            "pass_rate": 0.9,
            "attention_summary": {
                "total_cases": 10,
                "pass_rate": 0.9,
                "intent_carryover_accuracy": 0.95,
                "latest_turn_retention_rate": 1.0,
                "focus_regression_rate": 0.1,
                "false_clear_rate": 0.0,
                "pending_followup_resolution_rate": 1.0,
                "seal_while_pending_rate": 0.0,
                "continuity_focus_alignment_rate": 0.8,
                "context_redundancy_rate": 0.05,
            },
            "case_results": [],
            "failures": [],
        }
        report_file = tmp_path / "report.json"
        report_file.write_text(json.dumps(report_data), encoding="utf-8")

        result = validate_report_file(report_file)

        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_missing_file(self) -> None:
        """Test validation of non-existent file."""
        result = validate_report_file("/nonexistent/path/report.json")

        assert result.is_valid is False
        assert len(result.errors) == 1
        assert "not found" in result.errors[0].lower()

    def test_invalid_json(self, tmp_path: Path) -> None:
        """Test validation of invalid JSON file."""
        report_file = tmp_path / "report.json"
        report_file.write_text("{ invalid json }", encoding="utf-8")

        result = validate_report_file(report_file)

        assert result.is_valid is False
        assert any("Invalid JSON" in e for e in result.errors)

    def test_missing_required_fields(self, tmp_path: Path) -> None:
        """Test validation with missing required fields."""
        report_data = {
            "version": 1,
            "suite_id": "test",
            # Missing generated_at, total_cases, etc.
        }
        report_file = tmp_path / "report.json"
        report_file.write_text(json.dumps(report_data), encoding="utf-8")

        result = validate_report_file(report_file)

        assert result.is_valid is False
        assert any("generated_at" in e for e in result.errors)
        assert any("total_cases" in e for e in result.errors)

    def test_cross_field_mismatch(self, tmp_path: Path) -> None:
        """Test cross-field validation: passed + failed != total."""
        report_data = {
            "version": 1,
            "suite_id": "test",
            "generated_at": "2026-03-27T00:00:00+00:00",
            "total_cases": 10,
            "passed_cases": 5,
            "failed_cases": 3,  # 5 + 3 != 10
            "pass_rate": 0.5,
            "attention_summary": {
                "total_cases": 10,
                "pass_rate": 0.5,
                "intent_carryover_accuracy": 0.5,
                "latest_turn_retention_rate": 0.5,
                "focus_regression_rate": 0.5,
                "false_clear_rate": 0.5,
                "pending_followup_resolution_rate": 0.5,
                "seal_while_pending_rate": 0.5,
                "continuity_focus_alignment_rate": 0.5,
                "context_redundancy_rate": 0.10,
            },
            "case_results": [],
            "failures": [],
        }
        report_file = tmp_path / "report.json"
        report_file.write_text(json.dumps(report_data), encoding="utf-8")

        result = validate_report_file(report_file)

        assert result.is_valid is False
        assert any("passed_cases" in e and "failed_cases" in e for e in result.errors)

    def test_invalid_attention_summary(self, tmp_path: Path) -> None:
        """Test validation with missing attention_summary fields."""
        report_data = {
            "version": 1,
            "suite_id": "test",
            "generated_at": "2026-03-27T00:00:00+00:00",
            "total_cases": 10,
            "passed_cases": 10,
            "failed_cases": 0,
            "pass_rate": 1.0,
            "attention_summary": {
                "total_cases": 10,
                "pass_rate": 1.0,
                # Missing other required fields
            },
            "case_results": [],
            "failures": [],
        }
        report_file = tmp_path / "report.json"
        report_file.write_text(json.dumps(report_data), encoding="utf-8")

        result = validate_report_file(report_file)

        assert result.is_valid is False
        assert any("intent_carryover_accuracy" in e for e in result.errors)

    def test_out_of_range_pass_rate(self, tmp_path: Path) -> None:
        """Test validation with out-of-range pass_rate."""
        report_data = {
            "version": 1,
            "suite_id": "test",
            "generated_at": "2026-03-27T00:00:00+00:00",
            "total_cases": 10,
            "passed_cases": 15,  # More than total
            "failed_cases": 0,
            "pass_rate": 1.5,  # > 1.0
            "attention_summary": {
                "total_cases": 10,
                "pass_rate": 1.5,  # > 1.0
                "intent_carryover_accuracy": 0.95,
                "latest_turn_retention_rate": 1.0,
                "focus_regression_rate": 0.1,
                "false_clear_rate": 0.0,
                "pending_followup_resolution_rate": 1.0,
                "seal_while_pending_rate": 0.0,
                "continuity_focus_alignment_rate": 0.8,
                "context_redundancy_rate": 0.05,
            },
            "case_results": [],
            "failures": [],
        }
        report_file = tmp_path / "report.json"
        report_file.write_text(json.dumps(report_data), encoding="utf-8")

        result = validate_report_file(report_file)

        assert result.is_valid is False
        assert any("0 and 1" in e or "between 0" in e for e in result.errors)

    def test_negative_regression_rate(self, tmp_path: Path) -> None:
        """Test validation with negative regression rate."""
        report_data = {
            "version": 1,
            "suite_id": "test",
            "generated_at": "2026-03-27T00:00:00+00:00",
            "total_cases": 10,
            "passed_cases": 10,
            "failed_cases": 0,
            "pass_rate": 1.0,
            "attention_summary": {
                "total_cases": 10,
                "pass_rate": 1.0,
                "intent_carryover_accuracy": 0.95,
                "latest_turn_retention_rate": 1.0,
                "focus_regression_rate": -0.1,  # Negative
                "false_clear_rate": 0.0,
                "pending_followup_resolution_rate": 1.0,
                "seal_while_pending_rate": 0.0,
                "continuity_focus_alignment_rate": 0.8,
                "context_redundancy_rate": 0.05,
            },
            "case_results": [],
            "failures": [],
        }
        report_file = tmp_path / "report.json"
        report_file.write_text(json.dumps(report_data), encoding="utf-8")

        result = validate_report_file(report_file)

        assert result.is_valid is False
        assert any("focus_regression_rate" in e for e in result.errors)

    def test_non_numeric_metric(self, tmp_path: Path) -> None:
        """Test validation with non-numeric metric value."""
        report_data = {
            "version": 1,
            "suite_id": "test",
            "generated_at": "2026-03-27T00:00:00+00:00",
            "total_cases": 10,
            "passed_cases": 10,
            "failed_cases": 0,
            "pass_rate": 1.0,
            "attention_summary": {
                "total_cases": 10,
                "pass_rate": 1.0,
                "intent_carryover_accuracy": "high",  # Should be numeric
                "latest_turn_retention_rate": 1.0,
                "focus_regression_rate": 0.1,
                "false_clear_rate": 0.0,
                "pending_followup_resolution_rate": 1.0,
                "seal_while_pending_rate": 0.0,
                "continuity_focus_alignment_rate": 0.8,
                "context_redundancy_rate": 0.05,
            },
            "case_results": [],
            "failures": [],
        }
        report_file = tmp_path / "report.json"
        report_file.write_text(json.dumps(report_data), encoding="utf-8")

        result = validate_report_file(report_file)

        assert result.is_valid is False
        assert any("numeric" in e for e in result.errors)


class TestSchemaDefinitions:
    """Tests for schema definitions."""

    def test_suite_schema_has_required_fields(self) -> None:
        """Test that SUITE_SCHEMA has required fields."""
        assert "required" in SUITE_SCHEMA
        assert "version" in SUITE_SCHEMA["required"]
        assert "suite_id" in SUITE_SCHEMA["required"]
        assert "cases" in SUITE_SCHEMA["required"]

    def test_report_schema_has_required_fields(self) -> None:
        """Test that REPORT_SCHEMA has required fields."""
        assert "required" in REPORT_SCHEMA
        required = REPORT_SCHEMA["required"]
        assert "version" in required
        assert "suite_id" in required
        assert "generated_at" in required
        assert "total_cases" in required
        assert "passed_cases" in required
        assert "failed_cases" in required
        assert "pass_rate" in required
        assert "attention_summary" in required

    def test_attention_summary_has_all_metrics(self) -> None:
        """Test that attention_summary in REPORT_SCHEMA has all required metrics."""
        props = REPORT_SCHEMA.get("properties")
        attention_summary: dict[str, Any] | None = None
        if isinstance(props, dict):
            attention_summary_raw = props.get("attention_summary")
            if isinstance(attention_summary_raw, dict):
                attention_summary = attention_summary_raw
        summary_props: list[str] = []
        if attention_summary is not None:
            required_raw = attention_summary.get("required")
            if isinstance(required_raw, list):
                summary_props = [str(item) for item in required_raw]
        expected_metrics = [
            "total_cases",
            "pass_rate",
            "intent_carryover_accuracy",
            "latest_turn_retention_rate",
            "focus_regression_rate",
            "false_clear_rate",
            "pending_followup_resolution_rate",
            "seal_while_pending_rate",
            "continuity_focus_alignment_rate",
            "context_redundancy_rate",
        ]
        for metric in expected_metrics:
            assert metric in summary_props, f"Missing metric: {metric}"
