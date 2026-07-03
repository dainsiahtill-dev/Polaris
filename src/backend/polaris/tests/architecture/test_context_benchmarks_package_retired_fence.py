"""Architecture fence for the retired ContextOS benchmark package."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
RETIRED_PACKAGE = "polaris.kernelone.context.benchmarks"
CANONICAL_PACKAGE = "polaris.kernelone.benchmark"


def _imports_retired_context_benchmarks(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == RETIRED_PACKAGE or alias.name.startswith(f"{RETIRED_PACKAGE}."):
                    imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == RETIRED_PACKAGE or module.startswith(f"{RETIRED_PACKAGE}."):
                imports.append(module)
    return imports


def test_context_benchmarks_package_is_retired() -> None:
    retired_path = POLARIS_ROOT / "kernelone" / "context" / "benchmarks"
    assert not retired_path.exists(), "Retired ContextOS benchmark package was recreated."


def test_canonical_benchmark_package_remains_available() -> None:
    canonical_path = POLARIS_ROOT / "kernelone" / "benchmark"
    assert canonical_path.is_dir(), f"{CANONICAL_PACKAGE} must remain the benchmark owner."


def test_active_python_code_does_not_import_retired_context_benchmarks() -> None:
    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    for path in POLARIS_ROOT.rglob("*.py"):
        if path.resolve() == this_file or "__pycache__" in path.parts:
            continue
        for imported in _imports_retired_context_benchmarks(path):
            offenders.append(f"{path.relative_to(BACKEND_ROOT)} imports {imported}")

    assert not offenders, (
        f"Use {CANONICAL_PACKAGE!r} for benchmark models, runners, and validators; "
        "retired ContextOS benchmark imports remain:\n" + "\n".join(offenders)
    )
