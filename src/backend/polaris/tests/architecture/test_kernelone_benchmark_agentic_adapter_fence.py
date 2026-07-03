"""Architecture fence for the retired KernelOne agentic benchmark adapter."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
RETIRED_MODULE = "polaris.kernelone.benchmark.adapters.agentic_adapter"
TEST_BENCHMARK_MODULE = "polaris.tests.benchmark.adapters.agentic_adapter"


def _imports_retired_agentic_adapter(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == RETIRED_MODULE:
                    imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == RETIRED_MODULE:
                imports.append(module)
    return imports


def test_kernelone_agentic_benchmark_adapter_is_retired() -> None:
    retired_path = POLARIS_ROOT / "kernelone" / "benchmark" / "adapters" / "agentic_adapter.py"
    assert not retired_path.exists(), "KernelOne agentic benchmark adapter was recreated."


def test_agentic_adapter_lives_in_test_benchmark_package() -> None:
    test_owned_path = POLARIS_ROOT / "tests" / "benchmark" / "adapters" / "agentic_adapter.py"
    assert test_owned_path.is_file(), f"{TEST_BENCHMARK_MODULE} must own agentic benchmark execution."


def test_kernelone_benchmark_package_root_does_not_export_agentic_adapter() -> None:
    package_root = POLARIS_ROOT / "kernelone" / "benchmark" / "adapters" / "__init__.py"
    source = package_root.read_text(encoding="utf-8")
    assert "AgenticBenchmarkAdapter" not in source
    assert "agentic_adapter" not in source


def test_active_python_code_does_not_import_retired_agentic_adapter() -> None:
    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    for path in POLARIS_ROOT.rglob("*.py"):
        if path.resolve() == this_file or "__pycache__" in path.parts:
            continue
        for imported in _imports_retired_agentic_adapter(path):
            offenders.append(f"{path.relative_to(BACKEND_ROOT)} imports {imported}")

    assert not offenders, (
        f"Agentic benchmark execution is test-owned at {TEST_BENCHMARK_MODULE!r}; "
        "retired KernelOne imports remain:\n" + "\n".join(offenders)
    )
