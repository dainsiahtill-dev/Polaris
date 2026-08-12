"""In-repo regression for typescript_syntax package modularization.

Drives the shipped package face (not a reimplementation): multi-module layout,
path SSoT wrappers, and real plan builders through public imports.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from polaris.cells.director.runtime.internal.repair_kernel import typescript_syntax as ts_syntax
from polaris.cells.director.runtime.internal.repair_kernel.path_files import (
    normalize_base_files_strict,
    normalize_repair_path_strict,
)
from polaris.cells.director.runtime.internal.repair_kernel.typescript_syntax import (
    TYPESCRIPT_JSON_AS_SOURCE_SOURCE_TOOL,
    TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL,
    build_typescript_object_literal_comma_plan,
    build_typescript_runtime_plan_for_source_tool,
)
from polaris.cells.director.runtime.internal.repair_kernel.typescript_syntax.common import (
    _dedupe_preserve_order,
    _line_start_offsets,
    _normalize_repair_path,
    _normalized_base_files,
    _strip_javascript_callable_type_match,
)

_PACKAGE_DIR = Path(ts_syntax.__file__).resolve().parent
_REQUIRED_DOMAIN_MODULES = (
    "constants.py",
    "dispatch.py",
    "object_literals.py",
    "nullability.py",
    "imports_exports.py",
    "modules.py",
    "members.py",
    "config_scaffold.py",
    "html_dom.py",
    "text_repairs.py",
)
# type_shapes is itself a >2000-line module, so it is split into its own
# sub-package (type_shapes/) rather than a single type_shapes.py file.
_REQUIRED_SUBPACKAGE_DIRS = ("type_shapes",)
_REQUIRED_COMMON_MODULES = (
    "arg_shape_ops.py",
    "import_text_ops.py",
    "member_text_ops.py",
    "misc_ops.py",
    "null_dom_ops.py",
    "parse_ops.py",
    "path_ops.py",
    "plan_ops.py",
)


def test_typescript_syntax_is_package_not_monolith_module() -> None:
    """Shipped layout must be a multi-file package, not a single .py body."""

    assert _PACKAGE_DIR.is_dir()
    assert (_PACKAGE_DIR / "__init__.py").is_file()
    # No sibling monolith module remaining next to the package.
    assert not (_PACKAGE_DIR.parent / "typescript_syntax.py").exists()

    domain_files = sorted(p.name for p in _PACKAGE_DIR.glob("*.py") if p.name != "__init__.py")
    assert len(domain_files) >= 5
    for name in _REQUIRED_DOMAIN_MODULES:
        assert name in domain_files, f"missing domain module {name}"
    common_dir = _PACKAGE_DIR / "common"
    assert common_dir.is_dir()
    assert (common_dir / "__init__.py").is_file()
    common_files = sorted(p.name for p in common_dir.glob("*.py") if p.name != "__init__.py")
    for name in _REQUIRED_COMMON_MODULES:
        assert name in common_files, f"missing common domain module {name}"

    # Sub-packages (type_shapes was its own >2000-line module -> own package).
    for sub in _REQUIRED_SUBPACKAGE_DIRS:
        sub_dir = _PACKAGE_DIR / sub
        assert sub_dir.is_dir(), f"missing sub-package {sub}/"
        assert (sub_dir / "__init__.py").is_file(), f"missing {sub}/__init__.py"

    # Facade stays thin; rule bodies live in domain modules.
    init_lines = sum(1 for _ in (_PACKAGE_DIR / "__init__.py").open(encoding="utf-8"))
    assert init_lines < 300
    common_lines = sum(sum(1 for _ in path.open(encoding="utf-8")) for path in common_dir.glob("*.py"))
    dispatch_lines = sum(1 for _ in (_PACKAGE_DIR / "dispatch.py").open(encoding="utf-8"))
    assert common_lines > 100
    assert dispatch_lines > 20
    assert dispatch_lines < common_lines


def test_package_path_helpers_are_thin_wrappers_over_path_files_ssot() -> None:
    """TS package must not re-copy path traversal logic; only delegate to path_files."""

    wrapper_src = inspect.getsource(_normalize_repair_path)
    assert "normalize_repair_path_strict" in wrapper_src
    assert "while " not in wrapper_src
    assert 'startswith("../")' not in wrapper_src

    base_src = inspect.getsource(_normalized_base_files)
    assert "normalize_base_files_strict" in base_src

    # Exactly one _normalize_repair_path def in the package, and it is a wrapper.
    defs: list[tuple[str, str]] = []
    for path in _PACKAGE_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "_normalize_repair_path":
                defs.append((path.name, ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""))
    assert len(defs) == 1
    assert defs[0][0] == "path_ops.py"
    assert "normalize_repair_path_strict" in defs[0][1]

    # No package file reimplements the strip/reject loop (SSoT lives in path_files).
    for path in _PACKAGE_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "while normalized.startswith" not in text, path.name

    # Behavioral equivalence with shared SSoT on real inputs.
    samples = ("foo.ts", "./bar.ts", "../escape.ts", "/abs.ts", "a/../b.ts", "")
    for raw in samples:
        assert _normalize_repair_path(raw) == normalize_repair_path_strict(raw)

    base = {"./src/a.ts": "export {}", "../bad.ts": "x", "src/b.ts": "y"}
    assert _normalized_base_files(base) == normalize_base_files_strict(base)


def test_common_dependency_primitives_live_below_their_consumers() -> None:
    """Regression: package split must not leave reverse imports or runtime NameError."""

    assert _line_start_offsets(["a\n", "bc"]) == [0, 2, 4]
    assert _line_start_offsets.__module__.endswith("common.plan_ops")
    assert _dedupe_preserve_order([" a ", "", "a", "b"]) == ["a", "b"]
    assert _dedupe_preserve_order.__module__.endswith("common.path_ops")
    assert _strip_javascript_callable_type_match.__module__.endswith("common.misc_ops")


def test_public_facade_exports_source_tools_and_builders() -> None:
    """Stable import face used by registry/runtime_dispatch must keep working."""

    assert TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL == ("deterministic_typescript_return_object_semicolon_repair")
    assert TYPESCRIPT_JSON_AS_SOURCE_SOURCE_TOOL == ("deterministic_typescript_json_as_source_repair")
    assert callable(build_typescript_runtime_plan_for_source_tool)
    assert callable(build_typescript_object_literal_comma_plan)
    # Implementation module is domain dispatch, re-exported from package face.
    assert build_typescript_runtime_plan_for_source_tool.__module__.endswith("typescript_syntax.dispatch")


def test_runtime_plan_dispatch_unsupported_and_json_as_source_real_path() -> None:
    """Drive shipped dispatcher: unknown tool → None; json-as-source → real plan."""

    unsupported = build_typescript_runtime_plan_for_source_tool(
        source_tool="not_a_registered_repair_source_tool",
        base_files={},
        diagnostics=(),
    )
    assert unsupported is None

    package_json_body = (
        '{"name":"split-pkg-fixture","version":"1.0.0","private":true,'
        '"scripts":{"test":"node --test tests/*.test.ts"},"type":"module"}\n'
    )
    real_package = (
        "{\n"
        '  "name": "split-pkg-fixture",\n'
        '  "version": "0.1.0",\n'
        '  "private": true,\n'
        '  "type": "module",\n'
        '  "scripts": {\n'
        '    "test": "node --test tests/*.test.ts"\n'
        "  }\n"
        "}\n"
    )
    plan = build_typescript_runtime_plan_for_source_tool(
        source_tool=TYPESCRIPT_JSON_AS_SOURCE_SOURCE_TOOL,
        base_files={
            "src/verify.ts": package_json_body,
            "package.json": real_package,
        },
        diagnostics=(),
        mode="shadow",
    )
    assert plan is not None
    assert plan.source_tool == TYPESCRIPT_JSON_AS_SOURCE_SOURCE_TOOL
    assert plan.rule_id == "typescript.json_as_source"
    assert len(plan.operations) >= 1
    paths = {op.path for op in plan.operations}
    assert "src/verify.ts" in paths


def test_object_literal_comma_builder_resolves_from_package_face() -> None:
    """Public builder import remains bound after package split."""

    # Empty diagnostics → no plan (real fail-closed path).
    plan = build_typescript_object_literal_comma_plan(
        base_files={"src/a.ts": "export const x = { a: 1 b: 2 };\n"},
        diagnostics=(),
    )
    assert plan is None
    assert build_typescript_object_literal_comma_plan.__module__.endswith("typescript_syntax.object_literals")


@pytest.mark.parametrize(
    "attr",
    [
        "TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL",
        "TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL",
        "build_typescript_nullable_canvas_context_plan",
        "build_typescript_duplicate_object_property_plan",
    ],
)
def test_facade_attribute_access_for_consumer_symbols(attr: str) -> None:
    """runtime_dispatch/registry style attribute access on package module."""

    assert hasattr(ts_syntax, attr)
    value = getattr(ts_syntax, attr)
    assert value is not None
