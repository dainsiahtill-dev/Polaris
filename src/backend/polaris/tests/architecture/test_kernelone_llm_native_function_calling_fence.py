"""Architecture fence for retired KernelOne native-function-calling facade."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
TOOLKIT_INIT_PATH = POLARIS_ROOT / "kernelone/llm/toolkit/__init__.py"
RETIRED_NATIVE_FUNCTION_CALLING_PATH = POLARIS_ROOT / "kernelone/llm/toolkit/native_function_calling.py"
RETIRED_NATIVE_FUNCTION_CALLING_IMPORT = "polaris.kernelone.llm.toolkit.native_function_calling"
RETIRED_TOOLKIT_EXPORTS = {
    "ConversationalToolExecutor",
    "NativeFunctionCallingHandler",
    "ToolEnabledAIRequest",
    "ToolEnabledAIResponse",
    "ToolEnabledProviderMixin",
    "ToolResult",
    "create_tool_request",
    "execute_with_native_function_calling",
}


def _production_python_files() -> list[Path]:
    return [
        path
        for path in sorted(POLARIS_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
        and "tests" not in path.parts
        and "generated" not in path.parts
        and not path.name.startswith("test_")
    ]


def _retired_native_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == RETIRED_NATIVE_FUNCTION_CALLING_IMPORT:
                    violations.append(alias.name)
            continue
        if isinstance(node, ast.ImportFrom) and node.module == RETIRED_NATIVE_FUNCTION_CALLING_IMPORT:
            imported = ", ".join(sorted(alias.name for alias in node.names))
            violations.append(f"{node.module} import {imported}")
    return violations


def test_retired_native_function_calling_module_is_removed() -> None:
    """Native tool parsing/execution is owned by parsers + KernelToolCallingRuntime."""
    assert not RETIRED_NATIVE_FUNCTION_CALLING_PATH.exists(), (
        "Retired toolkit.native_function_calling facade was recreated; use "
        "polaris.kernelone.llm.toolkit.parsers plus "
        "polaris.kernelone.llm.toolkit.executor.runtime instead."
    )


def test_production_code_does_not_import_retired_native_function_calling() -> None:
    """Production code must not import through the retired facade module."""
    violations: list[str] = []
    for path in _production_python_files():
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        for imported in _retired_native_imports(path):
            violations.append(f"{rel}: {imported}")

    assert violations == [], (
        "Production code must use canonical parser/runtime modules instead of "
        "toolkit.native_function_calling:\n" + "\n".join(violations)
    )


def test_toolkit_public_exports_do_not_republish_retired_native_facade_symbols() -> None:
    """The toolkit package root must not keep old facade symbols alive."""
    tree = ast.parse(TOOLKIT_INIT_PATH.read_text(encoding="utf-8"))
    exported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if not (isinstance(target, ast.Name) and target.id == "__all__"):
                    continue
                if isinstance(node.value, ast.List):
                    exported.update(
                        item.value
                        for item in node.value.elts
                        if isinstance(item, ast.Constant) and isinstance(item.value, str)
                    )

    assert exported.isdisjoint(RETIRED_TOOLKIT_EXPORTS), (
        "Retired native-function-calling facade symbols must not be re-exported "
        f"from toolkit.__all__: {sorted(exported & RETIRED_TOOLKIT_EXPORTS)}"
    )
