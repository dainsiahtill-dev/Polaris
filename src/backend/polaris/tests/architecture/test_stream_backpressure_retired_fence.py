"""Architecture fence for retired LLM engine stream backpressure buffer."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
RETIRED_MODULE = "polaris.kernelone.llm.engine.stream.backpressure"
RETIRED_EXPORT = "BackpressureBuffer"
CANONICAL_MODULE = "polaris.kernelone.stream.backpressure_buffer"
CANONICAL_EXPORT = "AsyncBackpressureBuffer"


def _imports_retired_backpressure(path: Path) -> list[str]:
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


def test_retired_stream_backpressure_module_is_removed() -> None:
    retired_path = POLARIS_ROOT / "kernelone" / "llm" / "engine" / "stream" / "backpressure.py"
    assert not retired_path.exists(), "Retired stream backpressure module was recreated."


def test_stream_package_root_does_not_export_retired_buffer() -> None:
    for relative_path in (
        "kernelone/llm/engine/stream/__init__.py",
        "kernelone/llm/engine/stream_executor.py",
    ):
        source = (POLARIS_ROOT / relative_path).read_text(encoding="utf-8")
        assert RETIRED_EXPORT not in source


def test_canonical_async_backpressure_buffer_exists() -> None:
    canonical_path = POLARIS_ROOT / "kernelone" / "stream" / "backpressure_buffer.py"
    source = canonical_path.read_text(encoding="utf-8")
    assert canonical_path.is_file()
    assert CANONICAL_EXPORT in source


def test_active_python_code_does_not_import_retired_backpressure() -> None:
    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    for path in POLARIS_ROOT.rglob("*.py"):
        if path.resolve() == this_file or "__pycache__" in path.parts:
            continue
        for imported in _imports_retired_backpressure(path):
            offenders.append(f"{path.relative_to(BACKEND_ROOT)} imports {imported}")

    assert not offenders, (
        f"Use {CANONICAL_MODULE!r}.{CANONICAL_EXPORT}; retired backpressure imports remain:\n"
        + "\n".join(offenders)
    )
