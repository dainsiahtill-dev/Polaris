"""Pure policy for direct role-call governance checks."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

RULE_ID = "no_direct_role_call"
ROLE_NAMES = frozenset({"pm", "chief_engineer", "director", "qa"})
ROLE_SERVICE_NAMES = {
    "pm": frozenset({"PmService", "PmAgent", "PmAdapter"}),
    "chief_engineer": frozenset({"ChiefEngineerService", "ChiefEngineerAgent", "ChiefEngineerAdapter"}),
    "director": frozenset({"DirectorService", "DirectorAgent", "DirectorAdapter"}),
    "qa": frozenset({"QaService", "QaAgent", "QaAdapter", "QAService", "QAAgent", "QAAdapter"}),
}
ROLE_RUNTIME_CALLS = frozenset({"execute_role", "invoke_role", "run_role", "call_role"})


@dataclass(frozen=True)
class RoleCallHierarchyPolicyResult:
    """Evaluation result for no-direct-role-call governance."""

    rule_id: str
    passed: bool
    evidence: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _relative_path(workspace: Path, path: Path) -> str:
    """Return a stable repository-relative path."""
    try:
        return str(path.relative_to(workspace)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _is_test_path(path: Path) -> bool:
    """Return true when a path belongs to tests or fixtures."""
    return any(part in {"test", "tests"} for part in path.parts) or path.name.startswith("test_")


def _source_role(workspace: Path, path: Path) -> str | None:
    """Infer the owning role for a source path."""
    rel_parts = Path(_relative_path(workspace, path)).parts
    for index, part in enumerate(rel_parts):
        if part == "cells" and index + 1 < len(rel_parts):
            direct_cell = rel_parts[index + 1]
            if direct_cell in ROLE_NAMES:
                return direct_cell
            if direct_cell == "roles" and index + 2 < len(rel_parts):
                role_cell = rel_parts[index + 2]
                if role_cell in ROLE_NAMES:
                    return role_cell
    return None


def _module_role(module: str) -> str | None:
    """Infer the target role from a Python module path."""
    parts = module.split(".")
    if len(parts) < 3 or parts[0:2] != ["polaris", "cells"]:
        return None

    direct_cell = parts[2]
    if direct_cell in ROLE_NAMES:
        return direct_cell
    if direct_cell == "roles" and len(parts) > 3 and parts[3] in ROLE_NAMES:
        return parts[3]
    return None


def _is_role_runtime_path(workspace: Path, path: Path) -> bool:
    """Return true for the canonical roles.runtime implementation itself."""
    rel_path = _relative_path(workspace, path)
    return rel_path.startswith("polaris/cells/roles/runtime/")


def _iter_mainline_files(workspace: Path) -> tuple[Path, ...]:
    """Return mainline role orchestration files to inspect."""
    cells_dir = workspace / "polaris" / "cells"
    roots = (
        cells_dir / "director" / "execution",
        cells_dir / "chief_engineer" / "blueprint",
        cells_dir / "pm" / "workflow",
        cells_dir / "qa" / "audit_verdict",
        cells_dir / "roles" / "runtime",
    )
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        files.extend(path for path in root.rglob("*.py") if not _is_test_path(path))
    return tuple(files)


def _read_python_source(path: Path) -> str | None:
    """Read a Python file, returning None when it cannot be inspected."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _parse_python_source(path: Path, content: str) -> ast.Module | None:
    """Parse Python source, returning None for syntax-invalid files."""
    try:
        return ast.parse(content, filename=str(path))
    except SyntaxError:
        return None


def _call_name(node: ast.AST) -> str | None:
    """Return a simple call name from an AST call function expression."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _collect_violations(workspace: Path, path: Path, tree: ast.Module) -> tuple[str, ...]:
    """Collect direct peer role import/call violations for one file."""
    rel_path = _relative_path(workspace, path)
    source_role = _source_role(workspace, path)
    if source_role is None:
        return ()

    imported_peer_symbols: set[str] = set()
    violations: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue

        module = node.module or ""
        target_role = _module_role(module)
        if target_role is None or target_role == source_role:
            continue

        for alias in node.names:
            imported_name = alias.asname or alias.name
            if alias.name in ROLE_SERVICE_NAMES.get(target_role, frozenset()):
                imported_peer_symbols.add(imported_name)
                violations.append(f"Direct peer role import at {rel_path}:{node.lineno}: {module}.{alias.name}")

    if _is_role_runtime_path(workspace, path):
        return tuple(violations)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        name = _call_name(node.func)
        if name is None:
            continue
        if name in imported_peer_symbols:
            violations.append(f"Direct peer role call at {rel_path}:{node.lineno}: {name}")
        elif name in ROLE_RUNTIME_CALLS:
            violations.append(f"Suspicious role runtime call at {rel_path}:{node.lineno}: {name}")

    return tuple(violations)


def evaluate_role_call_hierarchy(workspace: Path) -> RoleCallHierarchyPolicyResult:
    """Evaluate whether role collaboration avoids direct peer role calls.

    The policy is intentionally AST-based and scoped to mainline orchestration
    roots. It skips tests, allows a role cell to import its own public/internal
    implementation, and allows ``roles.runtime`` to define the runtime API it
    owns. Cross-role imports/calls and direct runtime role calls outside the
    runtime owner remain violations.

    Complexity:
        O(f + n) time for scanned files and AST nodes, and O(v + w) space for
        emitted messages. File I/O dominates runtime.
    """
    violations: list[str] = []
    warnings: list[str] = []

    for py_file in _iter_mainline_files(workspace):
        content = _read_python_source(py_file)
        if content is None:
            warnings.append(f"Could not read {_relative_path(workspace, py_file)}")
            continue
        if "polaris.cells" not in content and not any(name in content for name in ROLE_RUNTIME_CALLS):
            continue

        tree = _parse_python_source(py_file, content)
        if tree is None:
            warnings.append(f"Could not parse {_relative_path(workspace, py_file)}")
            continue

        violations.extend(_collect_violations(workspace, py_file, tree))

    evidence: list[str] = []
    if not violations:
        evidence.append("No direct peer role calls found in mainline orchestration")

    return RoleCallHierarchyPolicyResult(
        rule_id=RULE_ID,
        passed=not violations,
        evidence=tuple(evidence),
        violations=tuple(violations),
        warnings=tuple(warnings),
    )
