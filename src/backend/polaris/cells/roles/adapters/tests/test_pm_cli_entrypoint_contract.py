"""TypeScript package requirements do not choose the product entrypoint."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.pm.normalization import _pm_typescript_factory_contract_missing
from polaris.cells.roles.adapters.public import PMAdapter


def _task(identity: str, targets: list[str], deps: list[str], commands: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": identity,
        "title": f"Implement {identity} modules",
        "goal": f"Implement the {identity} module with normal, boundary and invalid behavior checks.",
        "scope_paths": targets,
        "target_files": targets,
        "depends_on": deps,
        "assigned_to": "Director",
        "phase": "implementation",
        "steps": ["Implement declared source", "Run committed checks"],
        "execution_checklist": ["Materialize declared files", "Run committed checks"],
        "acceptance": [f"verify {targets[0]} exists; npm test passes at least 2 behavior assertions."],
        "verification_commands": commands,
    }


def _contracts() -> list[dict[str, Any]]:
    planning = _task("PLAN", ["docs/design.md"], [], [])
    planning.update(assigned_to="ChiefEngineer", phase="planning")
    commands = [
        {"modality": "environment_prep", "argv": ["npm", "ci"], "cwd": "."},
        {"modality": "build", "argv": ["npm", "run", "build"], "cwd": "."},
        {"modality": "test", "argv": ["npm", "test"], "cwd": "."},
        {"modality": "entrypoint", "argv": ["npm", "start"], "cwd": "."},
    ]
    implementation = _task(
        "IMPLEMENT",
        ["package.json", "tsconfig.json", "README.md", "src/module.ts", "src/cli.ts", "tests/module.test.ts"],
        ["PLAN"],
        commands,
    )
    return [planning, implementation]


def test_real_pm_adapter_accepts_complete_typescript_cli_without_inferred_html(tmp_path: Path) -> None:
    tasks = _contracts()
    before = deepcopy(tasks)
    directive = "Implement in TypeScript a CLI package. package.json and tsconfig.json are required."
    constructor: Callable[..., PMAdapter] = PMAdapter
    adapter = constructor(workspace=str(tmp_path))
    normalized, quality = adapter._evaluate_contract_quality(tasks, directive=directive)
    assert _pm_typescript_factory_contract_missing(tasks, directive) == []
    assert quality["ok"] is True, quality
    assert not any(path.endswith(".html") for task in normalized for path in task["target_files"])
    assert tasks == before
    assert normalized[1]["verification_commands"] == before[1]["verification_commands"]


@pytest.mark.parametrize(
    ("required", "expected"),
    [
        ("package.json", "package.json"),
        ("tsconfig.json", "tsconfig.json"),
        ("README.md", "README.md"),
        ("src/module.ts", "*.ts"),
        ("tests/module.test.ts", "executable_test_target"),
    ],
)
def test_existing_package_source_test_obligations_remain(required: str, expected: str) -> None:
    tasks = _contracts()
    if required == "src/module.ts":
        tasks[1]["target_files"] = [p for p in tasks[1]["target_files"] if not p.endswith(".ts")]
    else:
        tasks[1]["target_files"].remove(required)
    missing = _pm_typescript_factory_contract_missing(
        tasks, "Implement in TypeScript a CLI package. package.json and tsconfig.json are required."
    )
    assert expected in missing
