"""Architecture guard for KernelOne planning validation result naming."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.roles.adapters.internal import pm_adapter
from polaris.kernelone import planning
from polaris.kernelone.planning import validator

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_FILES = (
    _BACKEND_ROOT / "polaris" / "kernelone" / "planning" / "validator.py",
    _BACKEND_ROOT / "polaris" / "kernelone" / "planning" / "__init__.py",
    _BACKEND_ROOT / "polaris" / "cells" / "roles" / "adapters" / "internal" / "pm_adapter.py",
)


def test_planning_validation_uses_explicit_plan_result_name() -> None:
    """The generic planning ValidationResult surface must not be restored."""
    assert hasattr(validator, "PlanValidationResult")
    assert hasattr(planning, "PlanValidationResult")
    assert hasattr(pm_adapter, "PlanValidationResult")

    assert not hasattr(validator, "ValidationResult")
    assert not hasattr(planning, "ValidationResult")
    assert not hasattr(pm_adapter, "ValidationResult")

    for path in _FILES:
        source = path.read_text(encoding="utf-8")
        assert "class ValidationResult" not in source
        assert '"ValidationResult"' not in source
