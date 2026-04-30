#!/usr/bin/env python3
"""CI gate script that detects cross-cell internal/ imports.

Reads ``docs/graph/catalog/cells.yaml`` to build a mapping of file paths to
owning cells, then scans every Python file under ``polaris/`` for imports that
target ``polaris.cells.*/internal/`` or ``polaris.kernelone.*/internal/``.

A violation occurs when:

* the source file and target module belong to **different** cells, **or**
* the source file is **not owned** by any cell but imports an internal module,
  **or**
* the target internal module is **not owned** by any cell (orphan internal).

Usage::

    python docs/governance/ci/scripts/check_cell_internal_fence.py \
        --workspace . --mode audit-only

    python docs/governance/ci/scripts/check_cell_internal_fence.py \
        --workspace . --mode fail-on-new --baseline baseline.json

"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MODE_AUDIT_ONLY = "audit-only"
_MODE_FAIL_ON_NEW = "fail-on-new"
_SUPPORTED_MODES = (_MODE_AUDIT_ONLY, _MODE_FAIL_ON_NEW)

_CELLS_YAML_RELATIVE = "docs/graph/catalog/cells.yaml"
_SCAN_ROOT = "polaris"


@dataclass(frozen=True)
class Violation:
    """One cross-cell internal import violation."""

    source_file: str
    source_cell: str | None
    line: int
    import_stmt: str
    target_module: str
    target_cell: str | None
    reason: str

    def fingerprint(self) -> str:
        key = (
            f"{self.source_file}|{self.source_cell or 'unowned'}|"
            f"{self.line}|{self.import_stmt}|{self.target_module}|"
            f"{self.target_cell or 'unowned'}|{self.reason}"
        )
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "source_cell": self.source_cell,
            "line": self.line,
            "import_stmt": self.import_stmt,
            "target_module": self.target_module,
            "target_cell": self.target_cell,
            "reason": self.reason,
            "suggested_fix": self.suggested_fix(),
            "fingerprint": self.fingerprint(),
        }

    def suggested_fix(self) -> str:
        if self.reason == "unowned_source":
            return (
                f"File is not owned by any cell. Either add its path to a cell's "
                f"'owned_paths' in {_CELLS_YAML_RELATIVE}, or refactor to use a "
                f"public contract instead of {self.target_module}."
            )
        if self.reason == "orphan_target":
            return (
                f"Target module is not owned by any cell. Add its path to a cell's "
                f"'owned_paths' in {_CELLS_YAML_RELATIVE}, or migrate the import to "
                f"a public contract module."
            )
        return (
            f"Refactor to use the public contract surface of '{self.target_cell}' "
            f"instead of importing {self.target_module}."
        )


@dataclass
class FenceReport:
    """Structured report for the cell internal fence check."""

    mode: str
    exit_code: int = 0
    violation_count: int = 0
    new_violation_count: int = 0
    violating_files: list[str] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    unowned_source_files: list[str] = field(default_factory=list)
    orphan_target_modules: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "exit_code": self.exit_code,
            "violation_count": self.violation_count,
            "new_violation_count": self.new_violation_count,
            "violating_files": sorted(set(self.violating_files)),
            "violations": [v.to_dict() for v in self.violations],
            "unowned_source_files": sorted(set(self.unowned_source_files)),
            "orphan_target_modules": sorted(set(self.orphan_target_modules)),
            "errors": self.errors,
        }


class CellInternalFenceChecker:
    """Scan Python files for cross-cell internal/ import violations."""

    def __init__(
        self,
        workspace: Path,
        *,
        mode: str = _MODE_AUDIT_ONLY,
        baseline_path: Path | None = None,
    ) -> None:
        if mode not in _SUPPORTED_MODES:
            raise ValueError(f"Unsupported mode: {mode}")
        self._workspace = workspace.resolve()
        self._mode = mode
        self._baseline_path = baseline_path
        self._path_to_cell: dict[str, str] = {}
        self._cells_yaml_path = self._workspace / _CELLS_YAML_RELATIVE
        self._scan_root = self._workspace / _SCAN_ROOT

    def run(self) -> FenceReport:
        report = FenceReport(mode=self._mode)

        if not self._cells_yaml_path.exists():
            report.errors.append(f"Cells catalog not found: {self._cells_yaml_path}")
            report.exit_code = 1 if self._mode != _MODE_AUDIT_ONLY else 0
            return report

        self._load_cells(report)

        if not self._scan_root.exists():
            report.errors.append(f"Scan root not found: {self._scan_root}")
            report.exit_code = 1 if self._mode != _MODE_AUDIT_ONLY else 0
            return report

        self._scan_files(report)
        self._deduplicate(report)
        self._classify(report)
        report.new_violation_count = self._count_new_violations(report)
        report.exit_code = self._resolve_exit_code(report)
        return report

    # --------------------------------------------------------------------- #
    # Cell catalog loading
    # --------------------------------------------------------------------- #
    def _load_cells(self, report: FenceReport) -> None:
        try:
            import yaml
        except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover
            report.errors.append(f"PyYAML is required but unavailable: {exc}")
            return

        try:
            raw = yaml.safe_load(self._cells_yaml_path.read_text(encoding="utf-8"))
        except (RuntimeError, ValueError, OSError) as exc:
            report.errors.append(f"Failed to parse cells.yaml: {exc}")
            return

        if not isinstance(raw, dict):
            report.errors.append("cells.yaml root is not a mapping")
            return

        cells = raw.get("cells", [])
        if not isinstance(cells, list):
            report.errors.append("cells.yaml 'cells' key is not a list")
            return

        for cell in cells:
            if not isinstance(cell, dict):
                continue
            cell_id = cell.get("id")
            if not cell_id:
                continue
            for owned in cell.get("owned_paths", []):
                owned_norm = str(owned).replace("\\", "/").rstrip("/")
                if owned_norm:
                    self._path_to_cell[owned_norm] = str(cell_id)

    # --------------------------------------------------------------------- #
    # File scanning
    # --------------------------------------------------------------------- #
    def _scan_files(self, report: FenceReport) -> None:
        for py_file in self._scan_root.rglob("*.py"):
            if not py_file.is_file():
                continue
            self._scan_one_file(py_file, report)

    def _scan_one_file(self, py_file: Path, report: FenceReport) -> None:
        rel_path = self._rel_workspace(py_file)
        src_cell = self._resolve_cell(rel_path)

        try:
            content = py_file.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            report.errors.append(f"UTF-8 decode error in {rel_path}: {exc}")
            return
        except OSError as exc:
            report.errors.append(f"Failed to read {rel_path}: {exc}")
            return

        try:
            tree = ast.parse(content, filename=str(py_file))
        except SyntaxError as exc:
            report.errors.append(f"Syntax error in {rel_path} line {exc.lineno}: {exc.msg}")
            return
        except (RuntimeError, ValueError) as exc:
            report.errors.append(f"AST parse error in {rel_path}: {exc}")
            return

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._evaluate_import(
                        rel_path,
                        src_cell,
                        alias.name or "",
                        None,
                        node.lineno,
                        report,
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [alias.name for alias in node.names]
                self._evaluate_import(
                    rel_path,
                    src_cell,
                    module,
                    names,
                    node.lineno,
                    report,
                )

    def _evaluate_import(
        self,
        source_file: str,
        source_cell: str | None,
        module: str,
        names: list[str] | None,
        line: int,
        report: FenceReport,
    ) -> None:
        if not module:
            return

        is_internal = (
            module.startswith("polaris.cells.") and ".internal" in module
        ) or (
            module.startswith("polaris.kernelone.") and ".internal" in module
        )
        if not is_internal:
            return

        target_cell = self._resolve_target_cell_from_import(module)

        # Build a human-readable import statement string
        import_stmt = f"from {module} import {', '.join(names)}" if names else f"import {module}"

        # Determine if this is a violation
        if target_cell is None:
            # Target internal module is not owned by any cell
            report.violations.append(
                Violation(
                    source_file=source_file,
                    source_cell=source_cell,
                    line=line,
                    import_stmt=import_stmt,
                    target_module=module,
                    target_cell=None,
                    reason="orphan_target",
                )
            )
            report.violating_files.append(source_file)
            return

        if source_cell is None:
            # Unowned file importing an owned internal module
            report.violations.append(
                Violation(
                    source_file=source_file,
                    source_cell=None,
                    line=line,
                    import_stmt=import_stmt,
                    target_module=module,
                    target_cell=target_cell,
                    reason="unowned_source",
                )
            )
            report.violating_files.append(source_file)
            return

        if source_cell != target_cell:
            report.violations.append(
                Violation(
                    source_file=source_file,
                    source_cell=source_cell,
                    line=line,
                    import_stmt=import_stmt,
                    target_module=module,
                    target_cell=target_cell,
                    reason="cross_cell",
                )
            )
            report.violating_files.append(source_file)
            return

        # Same cell -> allowed, no violation

    # --------------------------------------------------------------------- #
    # Cell resolution helpers
    # --------------------------------------------------------------------- #
    def _resolve_cell(self, rel_path: str) -> str | None:
        """Map a relative file path to its owning cell id."""
        path_norm = rel_path.replace("\\", "/")
        best: str | None = None
        best_len = -1
        for owned, cell_id in self._path_to_cell.items():
            match_len = self._match_owned(path_norm, owned)
            if match_len is not None and match_len > best_len:
                best_len = match_len
                best = cell_id
        return best

    @staticmethod
    def _match_owned(path: str, owned: str) -> int | None:
        """Return match specificity length if *path* is covered by *owned*."""
        if owned.endswith("/**"):
            prefix = owned[:-3]
            if path.startswith(prefix):
                return len(prefix)
            return None
        if owned.endswith("/*"):
            prefix = owned[:-2]
            if path.startswith(prefix + "/"):
                return len(prefix)
            return None
        if "*" in owned:
            if fnmatch.fnmatch(path, owned):
                return len(owned)
            return None
        if path == owned or path.startswith(owned + "/"):
            return len(owned)
        return None

    def _resolve_target_cell_from_import(self, import_name: str) -> str | None:
        """Map a dotted module name to its owning cell id."""
        parts = import_name.split(".")
        if len(parts) < 2:
            return None

        # Try file path variants
        candidates = [
            "/".join(parts) + ".py",
            "/".join(parts) + "/__init__.py",
            "/".join(parts),
        ]
        for candidate in candidates:
            cell = self._resolve_cell(candidate)
            if cell:
                return cell
        return None

    # --------------------------------------------------------------------- #
    # Post-processing
    # --------------------------------------------------------------------- #
    def _deduplicate(self, report: FenceReport) -> None:
        seen: set[str] = set()
        unique: list[Violation] = []
        for v in report.violations:
            fp = v.fingerprint()
            if fp not in seen:
                seen.add(fp)
                unique.append(v)
        report.violations = unique
        report.violation_count = len(unique)

    def _classify(self, report: FenceReport) -> None:
        for v in report.violations:
            if v.reason == "unowned_source":
                report.unowned_source_files.append(v.source_file)
            if v.reason == "orphan_target":
                report.orphan_target_modules.append(v.target_module)

    def _count_new_violations(self, report: FenceReport) -> int:
        if self._mode != _MODE_FAIL_ON_NEW:
            return 0
        if self._baseline_path is None or not self._baseline_path.exists():
            return report.violation_count
        try:
            baseline = json.loads(self._baseline_path.read_text(encoding="utf-8"))
        except (RuntimeError, ValueError, OSError) as exc:
            logger.debug("Failed to parse baseline: %s", exc)
            return report.violation_count

        previous = baseline.get("issue_fingerprints") or baseline.get("violation_fingerprints")
        if not isinstance(previous, list):
            return report.violation_count

        baseline_set = {str(item).strip() for item in previous if str(item).strip()}
        current_set = {v.fingerprint() for v in report.violations}
        return len(current_set - baseline_set)

    def _resolve_exit_code(self, report: FenceReport) -> int:
        if self._mode == _MODE_AUDIT_ONLY:
            return 0
        if self._mode == _MODE_FAIL_ON_NEW:
            return 1 if report.new_violation_count > 0 else 0
        return 1 if report.violation_count > 0 else 0

    # --------------------------------------------------------------------- #
    # Utilities
    # --------------------------------------------------------------------- #
    def _rel_workspace(self, path: Path) -> str:
        return str(path.relative_to(self._workspace)).replace("\\", "/")

    def write_baseline(self, path: Path, report: FenceReport) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "workspace": str(self._workspace),
            "violation_fingerprints": sorted({v.fingerprint() for v in report.violations}),
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect cross-cell internal/ import violations.",
    )
    parser.add_argument(
        "--workspace",
        required=True,
        help="Repository workspace root (e.g. .)",
    )
    parser.add_argument(
        "--mode",
        default=_MODE_AUDIT_ONLY,
        choices=_SUPPORTED_MODES,
        help="audit-only (non-blocking) or fail-on-new (exit non-zero if new violations)",
    )
    parser.add_argument(
        "--baseline",
        help="JSON baseline file; only report NEW violations in fail-on-new mode",
    )
    parser.add_argument(
        "--output",
        help="Write JSON report to this path",
    )
    parser.add_argument(
        "--write-baseline",
        help="Write current violation fingerprints to this baseline JSON path",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    workspace = Path(str(args.workspace)).resolve()
    baseline_path = Path(str(args.baseline)).resolve() if args.baseline else None

    checker = CellInternalFenceChecker(
        workspace=workspace,
        mode=str(args.mode),
        baseline_path=baseline_path,
    )
    report = checker.run()

    payload = report.to_dict()
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    print(serialized)

    if args.output:
        output_path = Path(str(args.output)).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(serialized, encoding="utf-8")

    if args.write_baseline:
        checker.write_baseline(Path(str(args.write_baseline)).resolve(), report)

    return int(report.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
