"""Architecture fence for retired KernelOne LLM tools contracts shim."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
RETIRED_TOOLS_CONTRACTS_PATH = POLARIS_ROOT / "kernelone/llm/tools/contracts.py"
RETIRED_TOOLS_CONTRACTS_IMPORT = "polaris.kernelone.llm.tools.contracts"
RETIRED_TOOLS_PACKAGE_IMPORT = "polaris.kernelone.llm.tools"


def _production_python_files() -> list[Path]:
    return [
        path
        for path in sorted(POLARIS_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
        and "tests" not in path.parts
        and "generated" not in path.parts
        and not path.name.startswith("test_")
    ]


def _retired_tools_contract_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == RETIRED_TOOLS_CONTRACTS_IMPORT:
                    violations.append(alias.name)
            continue
        if isinstance(node, ast.ImportFrom):
            if node.module == RETIRED_TOOLS_CONTRACTS_IMPORT:
                imported = ", ".join(sorted(alias.name for alias in node.names))
                violations.append(f"{node.module} import {imported}")
            elif node.module == RETIRED_TOOLS_PACKAGE_IMPORT:
                imported_contracts = [alias.name for alias in node.names if alias.name == "contracts"]
                if imported_contracts:
                    violations.append(f"{node.module} import {', '.join(imported_contracts)}")
    return violations


def test_retired_kernelone_llm_tools_contracts_shim_is_removed() -> None:
    """Tool contracts are owned by ``polaris.kernelone.llm.contracts.tool``."""
    assert not RETIRED_TOOLS_CONTRACTS_PATH.exists(), (
        "Retired kernelone.llm.tools.contracts shim was recreated; import tool "
        "contracts from polaris.kernelone.llm.contracts or "
        "polaris.kernelone.llm.contracts.tool instead."
    )


def test_production_code_does_not_import_retired_tools_contracts_shim() -> None:
    """Production code must not route tool-contract imports through the retired shim."""
    violations: list[str] = []
    for path in _production_python_files():
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        for imported in _retired_tools_contract_imports(path):
            violations.append(f"{rel}: {imported}")

    assert violations == [], (
        "Production code must import tool contracts from canonical KernelOne LLM "
        "contracts modules, not the retired tools.contracts shim:\n" + "\n".join(violations)
    )
