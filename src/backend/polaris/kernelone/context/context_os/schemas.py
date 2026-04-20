"""Schema validation for Attention Runtime evaluation suite and report.

This module provides validation functions for:
- Suite JSON/YAML validation
- Report JSON validation against schema
- Threshold change validation

Usage:
    python -m polaris.kernelone.context.context_os.schemas.validate_suite path/to/suite.json
    python -m polaris.kernelone.context.context_os.schemas.validate_report path/to/report.json
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# =============================================================================
# Schema Definitions
# =============================================================================

SUITE_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["version", "suite_id", "cases"],
    "properties": {
        "version": {"type": "integer", "minimum": 1},
        "suite_id": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "cases": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["case_id", "conversation"],
                "properties": {
                    "case_id": {"type": "string", "minLength": 1},
                    "conversation": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["role", "content"],
                            "properties": {
                                "role": {"type": "string", "enum": ["user", "assistant", "tool", "system"]},
                                "content": {"type": "string"},
                            },
                        },
                    },
                    "expected_latest_intent": {"type": "string"},
                    "expected_pending_followup_status": {
                        "type": "string",
                        "enum": ["", "pending", "confirmed", "denied", "paused", "redirected"],
                    },
                    "expected_attention_roots_count": {"type": "integer", "minimum": 0},
                    "expect_seal_blocked": {"type": "boolean"},
                },
            },
        },
    },
}

REPORT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": [
        "version",
        "suite_id",
        "generated_at",
        "total_cases",
        "passed_cases",
        "failed_cases",
        "pass_rate",
        "attention_summary",
    ],
    "properties": {
        "version": {"type": "integer", "minimum": 1},
        "suite_id": {"type": "string", "minLength": 1},
        "generated_at": {"type": "string"},  # ISO datetime
        "total_cases": {"type": "integer", "minimum": 0},
        "passed_cases": {"type": "integer", "minimum": 0},
        "failed_cases": {"type": "integer", "minimum": 0},
        "pass_rate": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "attention_summary": {
            "type": "object",
            "required": [
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
            ],
            "properties": {
                "total_cases": {"type": "integer", "minimum": 0},
                "pass_rate": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "intent_carryover_accuracy": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "latest_turn_retention_rate": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "focus_regression_rate": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "false_clear_rate": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "pending_followup_resolution_rate": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "seal_while_pending_rate": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "continuity_focus_alignment_rate": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "context_redundancy_rate": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
        },
        "case_results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string"},
                    "passed": {"type": "boolean"},
                    "failures": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "failures": {"type": "array", "items": {"type": "string"}},
    },
}


# =============================================================================
# Validation Result
# =============================================================================


@dataclass(frozen=True, slots=True)
class SchemaValidationResult:
    """Result of schema validation.

    Note: This is distinct from other ValidationResult types:
    - ToolArgValidationResult: Tool argument validation
    - ProviderConfigValidationResult: Provider configuration validation
    - FileOpValidationResult: File operation validation
    - LaunchValidationResult: Bootstrap launch validation
    """

    is_valid: bool
    file_path: str
    errors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def print_report(self) -> None:
        """Print validation report to stdout."""
        status = "PASS" if self.is_valid else "FAIL"
        print(f"[{status}] {self.file_path}")

        if self.errors:
            print(f"  Errors ({len(self.errors)}):")
            for error in self.errors:
                print(f"    - {error}")

        if self.warnings:
            print(f"  Warnings ({len(self.warnings)}):")
            for warning in self.warnings:
                print(f"    - {warning}")


# Backward compatibility alias (deprecated)
ValidationResult = SchemaValidationResult


# =============================================================================
# Validation Functions
# =============================================================================


def validate_suite_file(path: str | Path) -> ValidationResult:
    """Validate a suite JSON/YAML file against schema.

    Args:
        path: Path to the suite file

    Returns:
        ValidationResult with errors and warnings
    """
    errors: list[str] = []
    warnings: list[str] = []
    path = Path(path)

    if not path.exists():
        return ValidationResult(
            is_valid=False,
            file_path=str(path),
            errors=(f"File not found: {path}",),
        )

    # Load file
    try:
        if path.suffix.lower() in (".yaml", ".yml"):
            import yaml

            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        else:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
    except json.JSONDecodeError as e:
        return ValidationResult(
            is_valid=False,
            file_path=str(path),
            errors=(f"Invalid JSON: {e}",),
        )
    except yaml.YAMLError as e:
        return ValidationResult(
            is_valid=False,
            file_path=str(path),
            errors=(f"Invalid YAML: {e}",),
        )
    except (RuntimeError, ValueError) as e:
        return ValidationResult(
            is_valid=False,
            file_path=str(path),
            errors=(f"Failed to load file: {e}",),
        )

    if not isinstance(data, dict):
        return ValidationResult(
            is_valid=False,
            file_path=str(path),
            errors=("Root element must be an object",),
        )

    # Validate required fields
    for field_name in ["version", "suite_id", "cases"]:
        if field_name not in data:
            errors.append(f"Missing required field: {field_name}")

    # Validate version
    if "version" in data:
        if not isinstance(data["version"], int):
            errors.append("version must be an integer")
        elif data["version"] < 1:
            errors.append("version must be >= 1")

    # Validate suite_id
    if "suite_id" in data and (not isinstance(data["suite_id"], str) or not data["suite_id"].strip()):
        errors.append("suite_id must be a non-empty string")

    # Validate cases
    if "cases" in data:
        if not isinstance(data["cases"], list):
            errors.append("cases must be an array")
        else:
            for i, case in enumerate(data["cases"]):
                if not isinstance(case, dict):
                    errors.append(f"cases[{i}] must be an object")
                    continue

                # Required case fields
                if "case_id" not in case:
                    errors.append(f"cases[{i}]: missing case_id")
                if "conversation" not in case:
                    errors.append(f"cases[{i}]: missing conversation")
                elif not isinstance(case["conversation"], list):
                    errors.append(f"cases[{i}]: conversation must be an array")
                elif len(case["conversation"]) == 0:
                    warnings.append(f"cases[{i}]: conversation is empty")

                # Validate conversation items
                for j, msg in enumerate(case.get("conversation", [])):
                    if not isinstance(msg, dict):
                        errors.append(f"cases[{i}].conversation[{j}]: must be an object")
                        continue
                    if "role" not in msg:
                        errors.append(f"cases[{i}].conversation[{j}]: missing role")
                    elif msg["role"] not in ("user", "assistant", "tool", "system"):
                        warnings.append(f"cases[{i}].conversation[{j}]: role '{msg['role']}' is non-standard")
                    if "content" not in msg:
                        errors.append(f"cases[{i}].conversation[{j}]: missing content")

    # Warning for missing description
    if "description" not in data:
        warnings.append("Missing optional field: description")

    return ValidationResult(
        is_valid=len(errors) == 0,
        file_path=str(path),
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def validate_report_file(path: str | Path) -> ValidationResult:
    """Validate a report JSON file against schema.

    Args:
        path: Path to the report file

    Returns:
        ValidationResult with errors and warnings
    """
    errors: list[str] = []
    warnings: list[str] = []
    path = Path(path)

    if not path.exists():
        return ValidationResult(
            is_valid=False,
            file_path=str(path),
            errors=(f"File not found: {path}",),
        )

    # Load file
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return ValidationResult(
            is_valid=False,
            file_path=str(path),
            errors=(f"Invalid JSON: {e}",),
        )
    except (RuntimeError, ValueError) as e:
        return ValidationResult(
            is_valid=False,
            file_path=str(path),
            errors=(f"Failed to load file: {e}",),
        )

    if not isinstance(data, dict):
        return ValidationResult(
            is_valid=False,
            file_path=str(path),
            errors=("Root element must be an object",),
        )

    # Required top-level fields
    required_fields = [
        "version",
        "suite_id",
        "generated_at",
        "total_cases",
        "passed_cases",
        "failed_cases",
        "pass_rate",
        "attention_summary",
    ]
    for field_name in required_fields:
        if field_name not in data:
            errors.append(f"Missing required field: {field_name}")

    # Validate core_summary (optional - gate may inject)
    core_summary = data.get("core_summary")
    if core_summary is not None:
        if not isinstance(core_summary, dict):
            errors.append("core_summary must be an object")
        else:
            required_core_fields = [
                "total_cases",
                "exact_fact_recovery",
                "decision_preservation",
                "open_loop_continuity",
                "artifact_restore_precision",
                "temporal_update_correctness",
                "abstention",
                "compaction_regret",
            ]
            for field_name in required_core_fields:
                if field_name not in core_summary:
                    errors.append(f"core_summary: missing field {field_name}")
    else:
        warnings.append("core_summary not present (gate may inject)")

    # Validate cognitive_runtime_summary (optional - gate may inject)
    cognitive_summary = data.get("cognitive_runtime_summary")
    if cognitive_summary is not None:
        if not isinstance(cognitive_summary, dict):
            errors.append("cognitive_runtime_summary must be an object")
        else:
            required_cognitive_fields = [
                "total_cases",
                "receipt_coverage",
                "handoff_roundtrip_success_rate",
                "state_restore_accuracy",
                "transaction_envelope_coverage",
                "receipt_write_failure_rate",
                "sqlite_write_p95_ms",
            ]
            for field_name in required_cognitive_fields:
                if field_name not in cognitive_summary:
                    errors.append(f"cognitive_runtime_summary: missing field {field_name}")
    else:
        warnings.append("cognitive_runtime_summary not present (gate may inject)")

    # Validate attention_summary structure
    summary = data.get("attention_summary")
    if summary:
        if not isinstance(summary, dict):
            errors.append("attention_summary must be an object")
        else:
            required_summary_fields = [
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
            for field_name in required_summary_fields:
                if field_name not in summary:
                    errors.append(f"attention_summary: missing field {field_name}")

            # Validate metric ranges
            for metric_name, min_val, max_val in [
                ("pass_rate", 0.0, 1.0),
                ("intent_carryover_accuracy", 0.0, 1.0),
                ("latest_turn_retention_rate", 0.0, 1.0),
                ("continuity_focus_alignment_rate", 0.0, 1.0),
                ("pending_followup_resolution_rate", 0.0, 1.0),
                ("context_redundancy_rate", 0.0, 1.0),
            ]:
                value = summary.get(metric_name)
                if value is not None:
                    try:
                        v = float(value)
                        if not (min_val <= v <= max_val):
                            errors.append(f"{metric_name} must be between {min_val} and {max_val}, got {v}")
                    except (TypeError, ValueError):
                        errors.append(f"{metric_name} must be numeric, got {value}")

            for metric_name in [
                "focus_regression_rate",
                "false_clear_rate",
                "seal_while_pending_rate",
            ]:
                value = summary.get(metric_name)
                if value is not None:
                    try:
                        v = float(value)
                        if not (0.0 <= v <= 1.0):
                            errors.append(f"{metric_name} must be between 0 and 1, got {v}")
                    except (TypeError, ValueError):
                        errors.append(f"{metric_name} must be numeric, got {value}")

    # Cross-field validation
    total = data.get("total_cases", 0)
    passed = data.get("passed_cases", 0)
    failed = data.get("failed_cases", 0)
    if passed + failed != total:
        errors.append(f"passed_cases ({passed}) + failed_cases ({failed}) != total_cases ({total})")

    return ValidationResult(
        is_valid=len(errors) == 0,
        file_path=str(path),
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


# =============================================================================
# CLI Entry Point
# =============================================================================


def main() -> int:
    """CLI entry point for schema validation."""
    if len(sys.argv) < 3:
        print("Usage:")
        print("  python -m polaris.kernelone.context.context_os.schemas validate-suite <path>")
        print("  python -m polaris.kernelone.context.context_os.schemas validate-report <path>")
        return 1

    command = sys.argv[1]
    file_path = sys.argv[2]

    if command == "validate-suite":
        result = validate_suite_file(file_path)
    elif command == "validate-report":
        result = validate_report_file(file_path)
    else:
        print(f"Unknown command: {command}")
        return 1

    result.print_report()
    return 0 if result.is_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
