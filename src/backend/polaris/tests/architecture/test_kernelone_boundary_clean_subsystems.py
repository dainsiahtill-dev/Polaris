from __future__ import annotations

from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parents[1]

HIGH_TRUST_SUBSYSTEMS = [
    BACKEND_ROOT / "polaris" / "kernelone" / "context",
    BACKEND_ROOT / "polaris" / "kernelone" / "storage",
    BACKEND_ROOT / "polaris" / "kernelone" / "fs",
    BACKEND_ROOT / "polaris" / "kernelone" / "memory",
    BACKEND_ROOT / "polaris" / "kernelone" / "runtime",
    BACKEND_ROOT / "polaris" / "kernelone" / "tools",
    BACKEND_ROOT / "polaris" / "cells" / "context" / "catalog",
]

HIGH_TRUST_FILES = [
    BACKEND_ROOT / "polaris" / "kernelone" / "process" / "ollama_utils.py",
]

# These patterns detect old-root imports (app., core., api., scripts.) that
# should not appear inside high-trust Polaris subsystems. This is an
# architecture invariant test: it scans polaris/kernelone/ and
# polaris/cells/context/catalog/ to ensure they only use canonical polaris.*
# imports. The strings below are intentionally literal search patterns, not
# import statements in this test file.
FORBIDDEN_PATTERNS = (
    "from io_utils import",
    "from storage_layout import",
    "from app.",
    "import app.",
    "from core.",
    "import core.",
    "from api.",
    "import api.",
    "from scripts.",
    "import scripts.",
)

FORBIDDEN_SYS_PATH_PATTERNS = (
    "sys.path.insert(",
    "sys.path.append(",
)


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def test_high_trust_subsystems_do_not_use_old_root_import_shortcuts() -> None:
    violations: list[str] = []

    for root in HIGH_TRUST_SUBSYSTEMS:
        for path in _iter_python_files(root):
            content = path.read_text(encoding="utf-8")
            for pattern in FORBIDDEN_PATTERNS:
                if pattern in content:
                    rel_path = path.relative_to(PROJECT_ROOT)
                    violations.append(f"{rel_path}: {pattern}")

    if violations:
        formatted = "\n".join(f"  - {item}" for item in violations)
        pytest.fail(f"Detected old-root or legacy shortcut imports inside high-trust Polaris subsystems:\n{formatted}")


def test_high_trust_subsystems_do_not_mutate_sys_path() -> None:
    violations: list[str] = []

    for root in HIGH_TRUST_SUBSYSTEMS:
        for path in _iter_python_files(root):
            content = path.read_text(encoding="utf-8")
            for pattern in FORBIDDEN_SYS_PATH_PATTERNS:
                if pattern in content:
                    rel_path = path.relative_to(PROJECT_ROOT)
                    violations.append(f"{rel_path}: {pattern}")

    for path in HIGH_TRUST_FILES:
        content = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_SYS_PATH_PATTERNS:
            if pattern in content:
                rel_path = path.relative_to(PROJECT_ROOT)
                violations.append(f"{rel_path}: {pattern}")

    if violations:
        formatted = "\n".join(f"  - {item}" for item in violations)
        pytest.fail(f"Detected sys.path mutation inside high-trust Polaris subsystems:\n{formatted}")
