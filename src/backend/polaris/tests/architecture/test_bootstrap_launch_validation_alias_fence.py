"""Architecture fence for retired bootstrap validation aliases."""

from __future__ import annotations

from pathlib import Path

import polaris.bootstrap.launch_validation as launch_validation

BACKEND_ROOT = Path(__file__).resolve().parents[3]
LAUNCH_VALIDATION = BACKEND_ROOT / "polaris" / "bootstrap" / "launch_validation.py"


def test_bootstrap_validation_uses_specific_result_type() -> None:
    """Bootstrap validation must expose the canonical launch result type."""
    assert hasattr(launch_validation, "LaunchValidationResult")
    assert not hasattr(launch_validation, "ValidationResult")
    assert launch_validation.validate_environment().__class__ is launch_validation.LaunchValidationResult


def test_bootstrap_validation_source_has_no_generic_alias() -> None:
    """Source-level fence blocks reintroducing generic ValidationResult alias."""
    source = LAUNCH_VALIDATION.read_text(encoding="utf-8")
    assert "ValidationResult = LaunchValidationResult" not in source
    assert "-> ValidationResult" not in source
