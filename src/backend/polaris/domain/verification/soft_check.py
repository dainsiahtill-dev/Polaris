"""Soft Check - Progressive validation against hallucination.

Detects:
- Missing target files (AI claims to have created them)
- Unresolved imports (AI generates code with broken dependencies)

Migrated from: scripts/director/iteration/verification.py
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SoftCheckResult:
    """Result of soft check validation.

    This is the "soft" verification that runs before expensive checks.
    It catches structural issues without compiling/running code.
    """

    missing_targets: list[str]
    unresolved_imports: list[str]
    files_created: list[str]

    @property
    def verify_ready(self) -> bool:
        """Check if soft validation passes (no blocking issues)."""
        return not self.missing_targets and not self.unresolved_imports

    @property
    def has_issues(self) -> bool:
        """Check if there are any issues."""
        return bool(self.missing_targets or self.unresolved_imports)

    def get_summary(self) -> str:
        """Get human-readable summary."""
        parts = []
        if self.missing_targets:
            parts.append(f"{len(self.missing_targets)} missing targets")
        if self.unresolved_imports:
            parts.append(f"{len(self.unresolved_imports)} unresolved imports")
        if not parts:
            return "All checks passed"
        return ", ".join(parts)


class SoftCheck:
    """Progressive validation to detect AI hallucinations early."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def check(
        self,
        target_files: list[str],
        changed_files: list[str] | None = None,
    ) -> SoftCheckResult:
        """Run soft check validation.

        Args:
            target_files: Expected target files
            changed_files: Files that were actually modified

        Returns:
            SoftCheckResult with issues found
        """
        # Check missing targets
        missing = check_missing_targets(target_files, self.workspace)

        # Check unresolved imports in changed files
        unresolved = []
        if changed_files:
            for file_path in changed_files:
                file_unresolved = detect_unresolved_imports(file_path, self.workspace)
                unresolved.extend(file_unresolved)

        # Determine created files
        created = []
        if changed_files:
            for f in changed_files:
                full_path = os.path.join(self.workspace, f)
                if os.path.exists(full_path):
                    created.append(f)

        return SoftCheckResult(
            missing_targets=missing,
            unresolved_imports=unresolved,
            files_created=created,
        )


def check_missing_targets(target_files: list[str], workspace: str) -> list[str]:
    """Check which target files are missing.

    This is a key anti-hallucination check:
    - AI may claim to have created a file
    - This function verifies the file actually exists
    """
    missing: list[str] = []

    for rel in normalize_paths(target_files):
        if not rel:
            continue
        full = os.path.join(workspace, rel)
        if not os.path.exists(full):
            missing.append(rel)

    return missing


def detect_unresolved_imports(file_path: str, workspace: str) -> list[str]:
    """Detect unresolved imports in a file.

    This prevents AI from generating code with "ghost imports"
    that point to non-existent files.

    Supports:
    - JavaScript/TypeScript: import X from "./path"
    - Python: from .module import X
    """
    unresolved: list[str] = []
    full_path = os.path.join(workspace, file_path)

    if not os.path.exists(full_path):
        return []

    try:
        with open(full_path, encoding="utf-8") as f:
            content = f.read()
    except (RuntimeError, ValueError) as e:
        logger.warning("Failed to check imports in %s: %s", full_path, e)
        return []

    # Check based on file extension
    if file_path.endswith((".js", ".ts", ".tsx", ".jsx")):
        unresolved = _check_js_imports(content, file_path, workspace)
    elif file_path.endswith(".py"):
        unresolved = _check_python_imports(content, file_path, workspace)

    return unresolved


def _check_js_imports(content: str, source_file: str, workspace: str) -> list[str]:
    """Check for unresolved JS/TS imports."""
    unresolved = []
    source_dir = os.path.dirname(os.path.join(workspace, source_file))

    # Pattern: import X from "./path" or import("./path")
    patterns = [
        r'import\s+.*?\s+from\s+["\']([\./][^"\']+)["\']',
        r'import\s*\(\s*["\']([\./][^"\']+)["\']\s*\)',
        r'require\s*\(\s*["\']([\./][^"\']+)["\']\s*\)',
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, content):
            import_path = match.group(1)
            if not import_path.startswith("."):
                continue  # Skip node_modules

            # Resolve relative to source file
            resolved = os.path.normpath(os.path.join(source_dir, import_path))

            # Check with various extensions
            extensions = ["", ".js", ".ts", ".tsx", ".jsx", ".json", "/index.js", "/index.ts"]
            exists = any(os.path.isfile(resolved + ext) for ext in extensions)

            if not exists:
                unresolved.append(f"{source_file}:{import_path}")

    return unresolved


def _check_python_imports(content: str, source_file: str, workspace: str) -> list[str]:
    """Check for unresolved Python relative imports."""
    unresolved = []
    source_dir = os.path.dirname(os.path.join(workspace, source_file))

    # Pattern: from .module import X or from ..module import X
    pattern = r"^\s*from\s+(\.{1,2}[a-zA-Z0-9_\.]*)\s+import"

    for match in re.finditer(pattern, content, re.MULTILINE):
        import_ref = match.group(1)

        # Convert relative import to path
        # from .utils -> ./utils.py or ./utils/__init__.py
        # from ..config -> ../runtime_config.py
        dots = import_ref[: len(import_ref) - len(import_ref.lstrip("."))]
        module_part = import_ref.lstrip(".")

        # Calculate base directory based on dot count
        base_dir = source_dir
        for _ in range(len(dots)):
            base_dir = os.path.dirname(base_dir)

        if module_part:
            # from .utils -> check utils.py, utils/__init__.py
            module_path = os.path.join(base_dir, module_part.replace(".", os.sep))
            checks = [
                module_path + ".py",
                os.path.join(module_path, "__init__.py"),
            ]
        else:
            # from . -> check __init__.py in parent
            checks = [os.path.join(base_dir, "__init__.py")]

        exists = any(os.path.isfile(p) for p in checks)
        if not exists:
            unresolved.append(f"{source_file}:{import_ref}")

    return unresolved


def normalize_paths(paths: list[str]) -> list[str]:
    """Normalize and deduplicate paths."""
    seen: set[str] = set()
    result = []
    for p in paths:
        normalized = p.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


# Standard library modules (Python) - not comprehensive
_STANDARD_LIBS = {
    "os",
    "sys",
    "json",
    "re",
    "time",
    "datetime",
    "pathlib",
    "typing",
    "collections",
    "itertools",
    "functools",
    "math",
    "random",
    "hashlib",
    "logging",
    "argparse",
    "unittest",
    "pytest",
    "tempfile",
    "shutil",
    "subprocess",
    "asyncio",
    "dataclasses",
    "enum",
    "abc",
    "inspect",
    "textwrap",
    "string",
    "csv",
    "xml",
    "html",
    "http",
    "urllib",
}
