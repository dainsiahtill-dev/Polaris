"""Normal Node test helpers and read scopes must not reject TypeScript delivery."""

from copy import deepcopy

import pytest
from polaris.cells.orchestration.pm_planning.public.artifact_contracts import (
    validate_pm_artifact_contracts,
    validate_pm_verification_commands,
)
from polaris.cells.roles.adapters.internal.pm.normalization import _pm_typescript_factory_contract_missing
from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter


def _tasks() -> list[dict]:
    return [
        {
            "id": "build",
            "target_files": ["src/model.ts", "package.json", "tsconfig.json", "README.md", "index.html"],
            "scope_paths": ["src/model.ts", "package.json", "tsconfig.json", "README.md", "index.html"],
            "goal": "Implement valid and invalid record handling",
            "title": "Implement model",
            "steps": ["Implement record fields", "Reject invalid records", "Build the package"],
            "acceptance": ["Valid records retain fields", "Invalid records are rejected", "Build succeeds"],
            "phase": "implementation",
            "assigned_to": "Director",
            "depends_on": [],
            "verification_commands": [
                {"modality": "environment_prep", "argv": ["npm", "install"], "cwd": "."},
                {"modality": "build", "argv": ["npm", "run", "build"], "cwd": "."},
            ],
        },
        {
            "id": "verify",
            "target_files": ["tests/check.mjs"],
            "scope_paths": ["tests/check.mjs", "package.json", "tsconfig.json"],
            "goal": "Verify normal, boundary and invalid record behavior",
            "title": "Verify records",
            "steps": ["Import built record module", "Assert normal and invalid results", "Run Node assertions"],
            "acceptance": ["Assertions verify field values", "Invalid inputs raise errors", "Tests succeed"],
            "phase": "validation",
            "assigned_to": "Director",
            "depends_on": ["build"],
            "verification_commands": [{"modality": "test", "argv": ["node", "--test", "tests/check.mjs"], "cwd": "."}],
        },
    ]


def test_typescript_business_code_can_use_authored_javascript_test_helper() -> None:
    tasks = _tasks()
    before = deepcopy(tasks)
    result = validate_pm_artifact_contracts(tasks, primary_language="typescript")
    assert result.ok, result.diagnostics
    assert "tests/check.mjs" in result.authored_targets
    assert tasks == before


def test_typescript_project_does_not_require_a_particular_test_filename_suffix() -> None:
    missing = _pm_typescript_factory_contract_missing(
        _tasks(), "Implement in TypeScript. package.json and tsconfig.json are required."
    )
    assert "*.test.ts" not in missing
    assert missing == []


def test_foreign_configuration_scope_is_read_context_not_a_missing_write(tmp_path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    tasks = _tasks()
    _, quality = adapter._evaluate_contract_quality(
        tasks, directive="Build a TypeScript simulation with package.json and tsconfig.json"
    )
    assert not any("scope_paths missing from target_files" in issue for issue in quality["critical_issues"])


@pytest.mark.parametrize("html", ["public/index.html", "src/entry/browser/index.html", "public/garden.html"])
def test_declared_html_entrypoint_does_not_require_a_colocated_script(tmp_path, html: str) -> None:
    import json

    tasks = _tasks()
    tasks[0]["target_files"].remove("index.html")
    tasks[0]["scope_paths"].remove("index.html")
    tasks[0]["target_files"].append(html)
    tasks[0]["scope_paths"].append(html)
    tasks[0]["verification_commands"].append({"modality": "entrypoint", "argv": ["npm", "run", "start"], "cwd": "."})
    original = deepcopy(tasks)
    adapter = PMAdapter(workspace=str(tmp_path))
    directive = "Implement in TypeScript. package.json and tsconfig.json are required."

    assert _pm_typescript_factory_contract_missing(tasks, directive) == []

    contracts = adapter._extract_task_contracts(json.dumps({"tasks": tasks}), directive=directive)
    normalized, quality = adapter._evaluate_contract_quality(contracts, directive=directive)

    assert quality["ok"] is True, quality["critical_issues"]
    assert any(html in task["target_files"] for task in normalized)
    assert not any(path == "index.html" for task in normalized for path in task["target_files"])
    assert tasks == original


@pytest.mark.parametrize("argv", [["tsc", "--noEmit"], ["npx", "tsc", "--noEmit", "-p", "tsconfig.json"]])
def test_typecheck_labelled_as_test_is_normalized_without_fabricating_test_evidence(argv) -> None:
    commands = [
        {"modality": "test", "argv": argv, "cwd": "."},
        {"modality": "test", "argv": ["npm", "test"], "cwd": "."},
    ]
    before = deepcopy(commands)

    normalized, diagnostics = validate_pm_verification_commands(commands)

    assert diagnostics == []
    assert normalized == [
        {"modality": "lint", "argv": argv, "cwd": "."},
        {"modality": "test", "argv": ["npm", "test"], "cwd": "."},
    ]
    assert commands == before
