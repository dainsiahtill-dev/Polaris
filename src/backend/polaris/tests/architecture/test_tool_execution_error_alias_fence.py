"""Architecture guard for tool-execution validation error-code exports."""

from __future__ import annotations

from polaris.kernelone import tool_execution
from polaris.kernelone.tool_execution import contracts

_RETIRED_SHORT_ERROR_ALIASES = {
    "ERROR_MIN_LENGTH",
    "ERROR_MAX_LENGTH",
    "ERROR_PATTERN",
    "ERROR_MINIMUM",
    "ERROR_MAXIMUM",
}


def test_tool_execution_does_not_export_short_validation_error_aliases() -> None:
    """Use descriptive validator error constants instead of short aliases."""
    for name in _RETIRED_SHORT_ERROR_ALIASES:
        assert not hasattr(contracts, name)
        assert not hasattr(tool_execution, name)
        assert name not in contracts.__all__
        assert name not in tool_execution.__all__
