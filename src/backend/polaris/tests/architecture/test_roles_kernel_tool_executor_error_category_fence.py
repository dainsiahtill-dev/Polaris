from __future__ import annotations

import importlib
import warnings
from pathlib import Path

from polaris.kernelone.errors import ErrorCategory

BACKEND_ROOT = Path(__file__).resolve().parents[3]
TOOL_EXECUTOR_MODULE = (
    BACKEND_ROOT / "polaris" / "cells" / "roles" / "kernel" / "internal" / "services" / "tool_executor.py"
)


def test_tool_executor_does_not_export_error_category_compat_alias() -> None:
    module = importlib.import_module("polaris.cells.roles.kernel.internal.services.tool_executor")
    assert "ErrorCategory" not in vars(module)


def test_tool_executor_uses_canonical_error_category_without_warning() -> None:
    module = importlib.import_module("polaris.cells.roles.kernel.internal.services.tool_executor")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = module.ToolResult(success=False, tool="read_file", error_category=ErrorCategory.NOT_FOUND)
        error = module.ToolError("not found", error_category=ErrorCategory.NOT_FOUND)

    assert result.error_category is ErrorCategory.NOT_FOUND
    assert error.error_category is ErrorCategory.NOT_FOUND
    assert not [warning for warning in caught if issubclass(warning.category, DeprecationWarning)]


def test_tool_executor_source_does_not_reintroduce_deprecation_marker() -> None:
    source = TOOL_EXECUTOR_MODULE.read_text(encoding="utf-8")

    assert "DeprecationWarning" not in source
    assert "warnings.warn" not in source
    assert "def __getattr__" not in source
    assert "TypeAlias" not in source
