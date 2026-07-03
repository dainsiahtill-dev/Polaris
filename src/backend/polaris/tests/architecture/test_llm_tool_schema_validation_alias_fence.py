"""Architecture guard for LLM tool-schema validation result naming."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.llm.tools import schema_validator

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_VALIDATOR = _BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "tools" / "schema_validator.py"


def test_llm_tool_schema_validator_uses_explicit_result_name() -> None:
    """Tool-schema validation must not export generic validation aliases."""
    source = _SCHEMA_VALIDATOR.read_text(encoding="utf-8")

    assert hasattr(schema_validator, "ToolSchemaValidationResult")
    assert not hasattr(schema_validator, "SchemaValidationResult")
    assert not hasattr(schema_validator, "ValidationResult")
    assert "SchemaValidationResult = ToolSchemaValidationResult" not in source
    assert "ValidationResult =" not in source
    assert '"SchemaValidationResult"' not in source
    assert '"ValidationResult"' not in source
