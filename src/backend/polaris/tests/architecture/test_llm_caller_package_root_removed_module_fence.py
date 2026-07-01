from __future__ import annotations

import importlib
import warnings
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
LLM_CALLER_ROOT = BACKEND_ROOT / "polaris" / "cells" / "roles" / "kernel" / "internal" / "llm_caller" / "__init__.py"


def test_llm_caller_package_root_import_does_not_emit_deprecation_warning() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        module = importlib.import_module("polaris.cells.roles.kernel.internal.llm_caller")
        module = importlib.reload(module)

    assert not [warning for warning in caught if issubclass(warning.category, DeprecationWarning)]
    assert hasattr(module, "LLMInvoker")


def test_removed_llm_caller_modules_fail_closed_without_warning() -> None:
    module = importlib.import_module("polaris.cells.roles.kernel.internal.llm_caller")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        removed_module_name = "call_sync"
        try:
            getattr(module, removed_module_name)
        except ModuleNotFoundError as exc:
            message = str(exc)
        else:  # pragma: no cover - defensive assertion
            raise AssertionError("call_sync compatibility module unexpectedly resolved")

    assert "Functionality merged into LLMInvoker.call()" in message
    assert not [warning for warning in caught if issubclass(warning.category, DeprecationWarning)]


def test_llm_caller_package_root_does_not_reintroduce_warning_marker() -> None:
    source = LLM_CALLER_ROOT.read_text(encoding="utf-8")

    assert "DeprecationWarning" not in source
    assert "warnings.warn" not in source
    assert "_warn_removed_module" not in source
