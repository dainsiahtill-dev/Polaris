"""Architecture fence for the retired infrastructure token-service shim."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
RETIRED_TOKEN_SERVICE_MODULE = "polaris.infrastructure.llm.token_service"
CANONICAL_TOKEN_SERVICE_MODULE = "polaris.domain.services"


def _production_python_files() -> list[Path]:
    return [
        path
        for path in sorted(POLARIS_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
        and "tests" not in path.parts
        and "generated" not in path.parts
        and not path.name.startswith("test_")
    ]


def _retired_token_service_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == RETIRED_TOKEN_SERVICE_MODULE:
                    violations.append(alias.name)
            continue
        if isinstance(node, ast.ImportFrom) and node.module == RETIRED_TOKEN_SERVICE_MODULE:
            imported = ", ".join(sorted(alias.name for alias in node.names))
            violations.append(f"{node.module} import {imported}")
    return violations


def test_infrastructure_token_service_reexport_shim_is_removed() -> None:
    """TokenService is owned by the domain-services layer, not infrastructure."""
    retired_path = POLARIS_ROOT / "infrastructure/llm/token_service.py"
    assert not retired_path.exists(), "Retired infrastructure token_service.py shim was recreated."

    violations: list[str] = []
    for path in _production_python_files():
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        for imported in _retired_token_service_imports(path):
            violations.append(f"{rel}: {imported}")

    assert violations == [], (
        "Production code must import TokenService/get_token_service from "
        f"{CANONICAL_TOKEN_SERVICE_MODULE!r}; retired infrastructure imports remain:\n"
        + "\n".join(violations)
    )
