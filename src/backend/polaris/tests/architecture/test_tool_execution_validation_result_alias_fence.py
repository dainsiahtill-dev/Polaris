"""Architecture guard for tool-argument validation result naming."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone import tool_execution
from polaris.kernelone.tool_execution import validators

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_FILES = (
    _BACKEND_ROOT / "polaris" / "kernelone" / "tool_execution" / "validators.py",
    _BACKEND_ROOT / "polaris" / "kernelone" / "tool_execution" / "contracts.py",
    _BACKEND_ROOT / "polaris" / "kernelone" / "tool_execution" / "__init__.py",
)


def test_tool_execution_uses_explicit_tool_arg_validation_result_name() -> None:
    """The generic ValidationResult alias must not be restored."""
    assert hasattr(validators, "ToolArgValidationResult")
    assert hasattr(tool_execution, "ToolArgValidationResult")
    assert not hasattr(validators, "ValidationResult")
    assert not hasattr(tool_execution, "ValidationResult")

    for path in _FILES:
        source = path.read_text(encoding="utf-8")
        assert "ValidationResult = ToolArgValidationResult" not in source
        assert '"ValidationResult"' not in source
