"""Pure policy for CELL_KERNELONE_04 path resolution governance."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RULE_ID = "CELL_KERNELONE_04"

CANONICAL_PATH_MODULES: tuple[str, ...] = (
    "polaris.kernelone.storage",
    "polaris.kernelone.storage.io_paths",
    "polaris.kernelone.storage.paths",
)
PUBLIC_ARTIFACT_SERVICE_MODULE = "polaris.cells.runtime.artifact_store.public.service"
ALLOWED_DELEGATE_MODULES: tuple[str, ...] = (
    *CANONICAL_PATH_MODULES,
    PUBLIC_ARTIFACT_SERVICE_MODULE,
)
CANONICAL_PATH_FILES: tuple[Path, ...] = (
    Path("polaris/kernelone/storage/paths.py"),
    Path("polaris/kernelone/storage/io_paths.py"),
)
PATH_RESOLUTION_NAMES: frozenset[str] = frozenset(
    {
        "_resolve_artifact_path",
        "_resolve_signal_path",
        "resolve_artifact_path",
        "resolve_signal_path",
        "_resolve_preferred_logical_prefix",
        "resolve_preferred_logical_prefix",
        "_resolve_runtime_path",
        "resolve_runtime_path",
    }
)
EXCLUDED_SCAN_PARTS: frozenset[str] = frozenset(
    {
        "__pycache__",
        "fixtures",
        "test",
        "tests",
    }
)


@dataclass(frozen=True)
class PathResolutionDefinition:
    """A path resolution function definition found below ``polaris/cells``."""

    file: str
    line: int
    function: str
    delegated: bool
    target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation for CLI and tests."""
        payload: dict[str, Any] = {
            "file": self.file,
            "line": self.line,
            "function": self.function,
            "delegated": self.delegated,
        }
        if self.target:
            payload["target"] = self.target
        return payload


@dataclass(frozen=True)
class CellKernelone04PolicyResult:
    """Evaluation result for KernelOne path resolution governance."""

    rule_id: str
    passed: bool
    evidence: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


class CellKernelone04Policy:
    """Evaluate whether Cells delegate path resolution to KernelOne.

    The rule preserves a single authoritative storage path implementation while
    allowing narrow compatibility wrappers. A Cell wrapper is accepted only when
    AST evidence shows that it calls KernelOne storage helpers or the runtime
    artifact store public service. Independent local implementations fail.

    Complexity:
        O(f + n) time for Python files and AST nodes under the scanned root.
        O(d) space for detected definitions, where ``d`` is the number of path
        resolution functions found.
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def evaluate(self) -> CellKernelone04PolicyResult:
        """Evaluate the policy for the configured workspace."""
        evidence: list[str] = []
        violations: list[str] = []
        warnings: list[str] = []

        if not self.verify_kernelone_has_paths():
            violations.append("Canonical path resolution not found in polaris/kernelone/storage/")
            return CellKernelone04PolicyResult(
                rule_id=RULE_ID,
                passed=False,
                violations=tuple(violations),
                details={"definitions": []},
            )
        evidence.append("Canonical path resolution verified in polaris/kernelone/storage/")

        cells_dir = self.workspace / "polaris" / "cells"
        definitions = self.find_path_definitions(cells_dir)
        non_delegating = [definition for definition in definitions if not definition.delegated]
        delegating = [definition for definition in definitions if definition.delegated]

        if delegating:
            evidence.append(f"Found {len(delegating)} Cell path resolver wrapper(s) delegating to canonical APIs")
        if non_delegating:
            for definition in non_delegating:
                violations.append(
                    f"Local path resolution at {definition.file}:{definition.line}: def {definition.function}(...)"
                )
        else:
            evidence.append("No independent local path resolution definitions found in polaris/cells/")

        return CellKernelone04PolicyResult(
            rule_id=RULE_ID,
            passed=not violations,
            evidence=tuple(evidence),
            violations=tuple(violations),
            warnings=tuple(warnings),
            details={
                "definitions": [definition.to_dict() for definition in definitions],
                "delegating_wrappers": [definition.to_dict() for definition in delegating],
                "non_delegating_definitions": [definition.to_dict() for definition in non_delegating],
            },
        )

    def verify_kernelone_has_paths(self) -> bool:
        """Return whether KernelOne exposes canonical path resolution helpers."""
        for relative_path in CANONICAL_PATH_FILES:
            source_path = self.workspace / relative_path
            if not source_path.exists():
                continue
            try:
                tree = ast.parse(source_path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name in PATH_RESOLUTION_NAMES:
                    return True
        return False

    def find_path_definitions(self, scan_root: Path) -> list[PathResolutionDefinition]:
        """Find path resolution definitions below ``scan_root``."""
        if not scan_root.exists():
            return []

        definitions: list[PathResolutionDefinition] = []
        for source_path in sorted(scan_root.rglob("*.py")):
            if self._is_excluded_source(source_path):
                continue
            try:
                source = source_path.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (OSError, SyntaxError) as exc:
                definitions.append(
                    PathResolutionDefinition(
                        file=self._relative(source_path),
                        line=1,
                        function="<parse_error>",
                        delegated=False,
                        target=f"unparseable source: {exc}",
                    )
                )
                continue

            import_aliases = self._collect_import_aliases(tree)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name not in PATH_RESOLUTION_NAMES:
                    continue
                target = self._delegation_target(node, import_aliases)
                definitions.append(
                    PathResolutionDefinition(
                        file=self._relative(source_path),
                        line=node.lineno,
                        function=node.name,
                        delegated=target is not None,
                        target=target,
                    )
                )
        return definitions

    def _is_excluded_source(self, source_path: Path) -> bool:
        if source_path.name.startswith("test_") or source_path.name.endswith("_test.py"):
            return True
        return bool(set(source_path.parts).intersection(EXCLUDED_SCAN_PARTS))

    def _collect_import_aliases(self, tree: ast.AST) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    bound_name = alias.asname or alias.name
                    aliases[bound_name] = f"{node.module}.{alias.name}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    bound_name = alias.asname or alias.name.split(".", 1)[0]
                    aliases[bound_name] = alias.name
        return aliases

    def _delegation_target(
        self,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        import_aliases: dict[str, str],
    ) -> str | None:
        function_imports = self._collect_import_aliases(function)
        aliases = {**import_aliases, **function_imports}
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            target = self._call_target(node.func, aliases)
            if target and self._is_allowed_delegate(target):
                return target
        return None

    def _call_target(self, func: ast.expr, import_aliases: dict[str, str]) -> str | None:
        if isinstance(func, ast.Name):
            return import_aliases.get(func.id)
        if isinstance(func, ast.Attribute):
            base = self._call_target(func.value, import_aliases)
            if base:
                return f"{base}.{func.attr}"
        return None

    def _is_allowed_delegate(self, target: str) -> bool:
        return any(target == module or target.startswith(f"{module}.") for module in ALLOWED_DELEGATE_MODULES)

    def _relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.workspace)).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")


def evaluate_cell_kernelone_04(workspace: Path) -> CellKernelone04PolicyResult:
    """Evaluate CELL_KERNELONE_04 for ``workspace``."""
    return CellKernelone04Policy(workspace).evaluate()
