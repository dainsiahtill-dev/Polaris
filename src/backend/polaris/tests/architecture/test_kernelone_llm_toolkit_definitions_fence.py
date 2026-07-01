"""Architecture fence for the retired LLM toolkit definitions bridge."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
KERNELONE_ROOT = POLARIS_ROOT / "kernelone"
RETIRED_MODULE = KERNELONE_ROOT / "llm" / "toolkit" / "definitions.py"


def test_llm_toolkit_definitions_module_is_retired() -> None:
    """Tool schemas must be emitted by ToolSpecRegistry, not an LLM toolkit shim."""
    assert not RETIRED_MODULE.exists()


def test_production_code_does_not_import_retired_toolkit_definitions() -> None:
    """No production path may import the retired definitions bridge."""
    forbidden_imports = (
        "polaris.kernelone.llm.toolkit.definitions",
        "from .definitions import",
        "import .definitions",
    )
    offenders: list[str] = []

    for path in POLARIS_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if any(token in source for token in forbidden_imports):
            offenders.append(path.relative_to(BACKEND_ROOT).as_posix())

    assert offenders == []


def test_tool_spec_registry_owns_provider_schema_projection() -> None:
    """The canonical registry must expose provider schemas and alias expansion."""
    source = (KERNELONE_ROOT / "tool_execution" / "tool_spec_registry.py").read_text(encoding="utf-8")

    assert "def get_llm_schema" in source
    assert "def generate_llm_schemas" in source
    assert "include_arg_aliases" in source
