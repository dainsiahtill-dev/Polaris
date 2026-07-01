"""Architecture fence for KernelOne tool-execution contract convergence."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
TOOL_EXECUTION_ROOT = BACKEND_ROOT / "polaris" / "kernelone" / "tool_execution"


def _production_python_sources(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if "__pycache__" not in path.parts and "tests" not in path.parts]


def test_contracts_do_not_expose_retired_compat_wrappers() -> None:
    source = (TOOL_EXECUTION_ROOT / "contracts.py").read_text(encoding="utf-8")
    retired_tokens = {
        "DeprecationWarning",
        "normalize_tool_args",
        "reset_tool_spec_registry_cache",
        "warnings.warn",
        "_TOOL_SPECS",
        "_deprecated",
        "_has_value",
    }

    offenders = sorted(token for token in retired_tokens if token in source)

    assert offenders == []


def test_tool_execution_production_uses_registry_and_canonical_normalizer() -> None:
    forbidden_fragments = {
        "from polaris.kernelone.tool_execution.contracts import _TOOL_SPECS",
        "from polaris.kernelone.tool_execution.contracts import normalize_tool_args",
        "normalize_tool_args(",
        "_TOOL_SPECS",
        "reset_tool_spec_registry_cache",
    }

    offenders: list[str] = []
    for path in _production_python_sources(TOOL_EXECUTION_ROOT):
        source = path.read_text(encoding="utf-8")
        for fragment in forbidden_fragments:
            if fragment in source:
                offenders.append(f"{path.relative_to(BACKEND_ROOT)}::{fragment}")

    assert offenders == []


def test_tool_execution_package_root_does_not_reexport_retired_normalizer() -> None:
    source = (TOOL_EXECUTION_ROOT / "__init__.py").read_text(encoding="utf-8")

    assert "normalize_tool_args" not in source
