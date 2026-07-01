"""Architecture fence for the retired KernelOne LLM error category shim."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
KERNELONE_ROOT = BACKEND_ROOT / "polaris" / "kernelone"
RETIRED_MODULE = KERNELONE_ROOT / "llm" / "error_categories.py"


def test_llm_error_categories_module_is_retired() -> None:
    """LLM error categories must use polaris.kernelone.errors directly."""
    assert not RETIRED_MODULE.exists()


def test_production_code_does_not_import_retired_llm_error_categories() -> None:
    """No production path may import the retired LLM-local error category module."""
    forbidden_imports = (
        "polaris.kernelone.llm.error_categories",
        "..error_categories",
        ".error_categories",
    )
    offenders: list[str] = []

    for path in (KERNELONE_ROOT / "llm").rglob("*.py"):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if any(token in source for token in forbidden_imports):
            offenders.append(path.relative_to(BACKEND_ROOT).as_posix())

    assert offenders == []


def test_kernelone_errors_remains_canonical_error_owner() -> None:
    """The canonical error owner must expose ErrorCategory and classify_error."""
    source = (KERNELONE_ROOT / "errors.py").read_text(encoding="utf-8")

    assert "class ErrorCategory" in source
    assert "def classify_error" in source
