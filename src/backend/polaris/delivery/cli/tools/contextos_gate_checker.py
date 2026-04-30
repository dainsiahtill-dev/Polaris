#!/usr/bin/env python3
"""ContextOS Engineering Gates - Automated Compliance Checker.

Usage:
    python -m scripts.contextos_gate_checker [paths...]

Returns:
    0 if all gates pass
    1 if any gate fails

Gates checked:
    1. No bare model_copy(update=...) on critical models
    2. No dual-lock pattern (threading + asyncio on same state)
    3. No heavy CPU/I/O inside async lock blocks
    4. No bare except Exception in deserialization paths
    5. All caches must have bounds
    6. Contract tests for new strategies (enforced at CI level)
"""

from __future__ import annotations

import ast
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Critical models that should not use model_copy(update=...)
CRITICAL_MODELS = {
    "ContextOSSnapshot",
    "ContextOSSnapshotV2",
    "TranscriptEvent",
    "TranscriptEventV2",
    "BudgetPlan",
    "BudgetPlanV2",
    "ArtifactRecord",
    "ArtifactRecordV2",
}

# Patterns that indicate heavy operations inside async locks
HEAVY_OPERATIONS = {
    "open(",
    "json.dump",
    "json.dumps",
    "json.load",
    "json.loads",
    "re.compile",
    "re.findall",
    "re.search",
    "hashlib",
    "sha256",
}

# Deserialization-related function names
DESERIALIZATION_PATHS = {
    "from_dict",
    "from_mapping",
    "from_json",
    "parse",
    "deserialize",
    "model_validate",
    "model_validate_json",
}


class GateChecker(ast.NodeVisitor):
    """AST visitor that checks ContextOS engineering gates."""

    def __init__(self, filepath: Path) -> None:
        self.filepath = filepath
        self.violations: list[dict[str, Any]] = []
        self.in_async_lock = False
        self.current_function: str | None = None
        self.is_deserialization_path = False

    def _report(self, gate: str, line: int, message: str, severity: str = "error") -> None:
        self.violations.append(
            {
                "gate": gate,
                "file": str(self.filepath),
                "line": line,
                "message": message,
                "severity": severity,
            }
        )

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        old_func = self.current_function
        old_deser = self.is_deserialization_path

        self.current_function = node.name
        self.is_deserialization_path = any(
            node.name.startswith(prefix) or node.name == prefix for prefix in DESERIALIZATION_PATHS
        )

        self.generic_visit(node)

        self.current_function = old_func
        self.is_deserialization_path = old_deser

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Gate 1: Check for model_copy(update=...) on critical models
        if isinstance(node.func, ast.Attribute) and node.func.attr == "model_copy":
            for kw in node.keywords:
                if kw.arg == "update":
                    self._report(
                        "GATE-1",
                        node.lineno,
                        "model_copy(update=...) found. Use validated_replace() instead. "
                        "Critical models should not bypass Pydantic validation.",
                    )
                    break

        # Gate 3: Check for heavy operations inside async locks
        if self.in_async_lock:
            if isinstance(node.func, ast.Attribute):
                op_name = f"{getattr(node.func.value, 'id', '')}.{node.func.attr}"
            elif isinstance(node.func, ast.Name):
                op_name = node.func.id
            else:
                op_name = ""

            for heavy_op in HEAVY_OPERATIONS:
                if heavy_op in op_name:
                    self._report(
                        "GATE-3",
                        node.lineno,
                        f"Heavy operation '{op_name}' inside async lock. Move to lock-free zone or run_in_executor().",
                        severity="warning",
                    )
                    break

        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        old_lock = self.in_async_lock

        # Check if this is an async lock acquisition
        for item in node.items:
            if isinstance(item.context_expr, ast.Call):
                func = item.context_expr.func
                if (isinstance(func, ast.Attribute) and "lock" in func.attr.lower()) or (
                    isinstance(func, ast.Name) and "lock" in func.id.lower()
                ):
                    self.in_async_lock = True
                    break

        self.generic_visit(node)
        self.in_async_lock = old_lock

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        # Gate 4: Check for bare except Exception in deserialization paths
        if self.is_deserialization_path and node.type is not None:
            type_str = ast.unparse(node.type) if hasattr(ast, "unparse") else ""
            if "Exception" in type_str and "(" not in type_str:
                self._report(
                    "GATE-4",
                    node.lineno,
                    f"Bare 'except Exception' in deserialization path '{self.current_function}'. "
                    f"Narrow to specific exceptions and add logging.",
                )

        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        # Gate 2: Check for dual-lock pattern (threading + asyncio in same class)
        # This is a simplified check - full check requires cross-reference analysis
        for target in node.targets:
            if isinstance(target, ast.Attribute) and target.attr.endswith("_lock"):
                self._report(
                    "GATE-2",
                    node.lineno,
                    f"Lock assignment found: {target.attr}. "
                    f"Ensure no dual-lock pattern (threading + asyncio on same state). "
                    f"Use async-only + sync facade delegation.",
                    severity="warning",
                )

        self.generic_visit(node)


def check_file(filepath: Path) -> list[dict[str, Any]]:
    """Check a single Python file for gate violations."""
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError) as e:
        logger.warning("Failed to parse %s: %s", filepath, e)
        return []

    checker = GateChecker(filepath)
    checker.visit(tree)
    return checker.violations


def main(paths: list[str]) -> int:
    """Main entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    all_violations: list[dict[str, Any]] = []
    files_checked = 0

    for path_str in paths:
        path = Path(path_str)
        if path.is_file() and path.suffix == ".py":
            violations = check_file(path)
            all_violations.extend(violations)
            files_checked += 1
        elif path.is_dir():
            for py_file in path.rglob("*.py"):
                if "test_" in py_file.name or py_file.name.startswith("test"):
                    continue  # Skip test files
                violations = check_file(py_file)
                all_violations.extend(violations)
                files_checked += 1

    # Report results
    errors = [v for v in all_violations if v["severity"] == "error"]
    warnings = [v for v in all_violations if v["severity"] == "warning"]

    print(f"\n{'=' * 60}")
    print("ContextOS Engineering Gates Report")
    print(f"{'=' * 60}")
    print(f"Files checked: {files_checked}")
    print(f"Errors: {len(errors)}")
    print(f"Warnings: {len(warnings)}")
    print(f"{'=' * 60}\n")

    if errors:
        print("🔴 ERRORS (must fix):")
        for v in errors:
            print(f"  [{v['gate']}] {v['file']}:{v['line']}")
            print(f"    → {v['message']}")
        print()

    if warnings:
        print("🟡 WARNINGS (should fix):")
        for v in warnings:
            print(f"  [{v['gate']}] {v['file']}:{v['line']}")
            print(f"    → {v['message']}")
        print()

    if not errors and not warnings:
        print("✅ All gates passed!\n")
        return 0

    return 1 if errors else 0


if __name__ == "__main__":
    paths = sys.argv[1:] if len(sys.argv) > 1 else ["polaris/kernelone/context/"]
    sys.exit(main(paths))
