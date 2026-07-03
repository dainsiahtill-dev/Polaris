"""Architecture fence for retired ``llm.types.estimate_usage`` helper."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
RETIRED_NAME = "estimate_usage"
CANONICAL_USAGE = "Usage.estimate"


def _imports_retired_estimate_usage(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "polaris.kernelone.llm.types":
            for alias in node.names:
                if alias.name == RETIRED_NAME:
                    imports.append(alias.name)
    return imports


def test_llm_types_does_not_define_estimate_usage() -> None:
    types_path = POLARIS_ROOT / "kernelone" / "llm" / "types.py"
    source = types_path.read_text(encoding="utf-8")
    assert "def estimate_usage" not in source


def test_active_python_code_does_not_import_estimate_usage() -> None:
    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    for path in POLARIS_ROOT.rglob("*.py"):
        if path.resolve() == this_file or "__pycache__" in path.parts:
            continue
        for imported in _imports_retired_estimate_usage(path):
            offenders.append(f"{path.relative_to(BACKEND_ROOT)} imports {imported}")

    assert not offenders, f"Use {CANONICAL_USAGE}; retired estimate_usage imports remain:\n" + "\n".join(offenders)
