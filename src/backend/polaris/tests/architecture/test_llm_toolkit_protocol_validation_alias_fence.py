"""Architecture guard for file-operation protocol validation result naming."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.llm import toolkit
from polaris.kernelone.llm.toolkit import protocol
from polaris.kernelone.llm.toolkit.protocol import models

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_FILES = (
    _BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "toolkit" / "__init__.py",
    _BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "toolkit" / "protocol" / "models.py",
    _BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "toolkit" / "protocol" / "validator.py",
    _BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "toolkit" / "protocol" / "__init__.py",
)


def test_protocol_uses_explicit_file_operation_validation_result_name() -> None:
    """The generic ValidationResult compatibility alias must not be restored."""
    assert hasattr(models, "FileOpValidationResult")
    assert hasattr(protocol, "FileOpValidationResult")
    assert hasattr(toolkit, "FileOpValidationResult")
    assert not hasattr(models, "ValidationResult")
    assert not hasattr(protocol, "ValidationResult")
    assert not hasattr(toolkit, "ValidationResult")

    for path in _FILES:
        source = path.read_text(encoding="utf-8")
        assert "ValidationResult = FileOpValidationResult" not in source
        assert '"ValidationResult"' not in source
