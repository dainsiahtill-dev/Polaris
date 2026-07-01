"""Architecture fence for retired llm.evaluation deterministic judge shim."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
RETIRED_DETERMINISTIC_JUDGE = POLARIS_ROOT / "cells/llm/evaluation/internal/deterministic_judge.py"
RETIRED_IMPORT = "polaris.cells.llm.evaluation.internal.deterministic_judge"


def _production_python_files() -> list[Path]:
    return [
        path
        for path in sorted(POLARIS_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
        and "tests" not in path.parts
        and "generated" not in path.parts
        and not path.name.startswith("test_")
    ]


def test_retired_deterministic_judge_module_is_removed() -> None:
    """The old re-export shim must not return beside the canonical judge package."""
    assert not RETIRED_DETERMINISTIC_JUDGE.exists(), (
        "Retired deterministic_judge.py shim was recreated; use "
        "polaris.cells.llm.evaluation.internal.judge.* modules instead."
    )


def test_production_code_imports_canonical_judge_package() -> None:
    """Production code must not import through the retired deterministic judge shim."""
    violations: list[str] = []
    for path in _production_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module == RETIRED_IMPORT:
                violations.append(path.relative_to(BACKEND_ROOT).as_posix())

    assert violations == [], (
        "Production code must import llm.evaluation judge symbols from the "
        "canonical internal.judge package, not the retired deterministic_judge shim: "
        + ", ".join(violations)
    )
