"""Document planning repairs retain implementation and QA verification authority."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.orchestration.pm_planning.public.artifact_contracts import (
    validate_pm_verification_commands,
)
from polaris.cells.roles.adapters.internal.pm.revision import apply_revision, make_revision_base
from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter


def _candidate() -> dict[str, Any]:
    return {
        "tasks": [
            {
                "id": "TASK-DOC",
                "title": "Plan module interfaces",
                "goal": "Write BLUEPRINT.md with TypeScript CLI module interfaces and verification commands",
                "scope_paths": ["BLUEPRINT.md"],
                "target_files": ["BLUEPRINT.md"],
                "steps": ["Read requirements.md", "Describe module interfaces and CLI verification in BLUEPRINT.md"],
                "acceptance": ["BLUEPRINT.md records module paths, CLI entrypoint and test commands"],
                "verification_commands": [],
                "phase": "planning",
                "assigned_to": "ChiefEngineer",
                "execution_backend": "doc_only",
                "depends_on": [],
            },
            {
                "id": "TASK-IMPLEMENT",
                "title": "Implement TypeScript CLI and tests",
                "goal": "Implement CLI record validation and tests for valid and invalid records",
                "scope_paths": ["src", "tests", "package.json", "tsconfig.json", "README.md"],
                "target_files": [
                    "src/model.ts",
                    "src/main.ts",
                    "tests/model.test.ts",
                    "package.json",
                    "tsconfig.json",
                    "README.md",
                ],
                "steps": [
                    "Implement record validation and CLI",
                    "Configure build and test scripts",
                    "Run npm install, npm run build and npm test",
                ],
                "acceptance": [
                    "Valid records retain fields",
                    "Invalid records produce validation errors",
                    "npm test and npm run build pass; node dist/main.js exercises the CLI",
                ],
                "verification_commands": [
                    {"modality": "environment_prep", "argv": ["npm", "install"], "cwd": "."},
                    {"modality": "build", "argv": ["npm", "run", "build"], "cwd": "."},
                    {"modality": "test", "argv": ["npm", "test"], "cwd": "."},
                    {"modality": "entrypoint", "argv": ["node", "dist/main.js"], "cwd": "."},
                ],
                "generated_outputs": [
                    {
                        "path": "dist/main.js",
                        "inputs": [{"task_id": "TASK-IMPLEMENT", "path": "src/main.ts"}],
                        "build": {"argv": ["npm", "run", "build"], "cwd": "."},
                    }
                ],
                "phase": "implementation",
                "assigned_to": "Director",
                "execution_backend": "code_edit",
                "depends_on": ["TASK-DOC"],
            },
            {
                "id": "TASK-QA",
                "title": "Verify CLI behavior",
                "goal": "Write QA_REPORT.md documenting actual CLI test, build and entrypoint results",
                "scope_paths": ["QA_REPORT.md"],
                "target_files": ["QA_REPORT.md"],
                "context_files": ["src/main.ts", "tests/model.test.ts", "package.json", "tsconfig.json"],
                "steps": [
                    "Run npm test and npm run build",
                    "Run node dist/main.js",
                    "Write command exit codes in QA_REPORT.md",
                ],
                "acceptance": [
                    "QA_REPORT.md records successful npm test, npm run build and node dist/main.js exit codes"
                ],
                "verification_commands": [
                    {"modality": "test", "argv": ["npm", "test"], "cwd": "."},
                    {"modality": "build", "argv": ["npm", "run", "build"], "cwd": "."},
                    {"modality": "entrypoint", "argv": ["node", "dist/main.js"], "cwd": "."},
                ],
                "phase": "validation",
                "assigned_to": "QA",
                "execution_backend": "doc_only",
                "depends_on": ["TASK-IMPLEMENT"],
            },
        ]
    }


def _patch(base_hash: str, updates: list[dict[str, Any]]) -> str:
    return json.dumps(
        {
            "schema_version": "pm.task_revision.v1",
            "base_hash": base_hash,
            "updates": updates,
            "add_tasks": [],
            "remove_task_ids": [],
        }
    )


@pytest.mark.parametrize("argv", [["node", "--version"], ["npm", "--version"], ["node", "--help"]])
def test_document_revision_guidance_yields_legal_local_repair(tmp_path: Path, argv: list[str]) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    original = _candidate()
    _, valid_quality = adapter._evaluate_contract_quality(original["tasks"], directive="Create a TypeScript CLI")
    assert valid_quality["ok"] is True
    assert valid_quality["score"] >= 80
    original["tasks"][0]["verification_commands"] = [{"modality": "environment_prep", "argv": argv, "cwd": "."}]
    before = copy.deepcopy(original)
    _, quality = adapter._evaluate_contract_quality(original["tasks"], directive="Create a TypeScript CLI")
    assert quality["ok"] is False
    assert any(row["code"] == "verification_command_proof_missing" for row in quality["contract_diagnostics"])
    base = make_revision_base(
        json.dumps(original),
        workspace=str(tmp_path),
        run_id="pm-document-repair",
        task_id="pm-task",
        directive="Create a TypeScript CLI",
        quality=quality,
    )
    assert base is not None
    prompt = adapter._build_pm_retry_message(
        directive="Create a TypeScript CLI",
        quality=quality,
        previous_output=base.response,
        validated_candidate=True,
        revision_base=base,
    )
    # Consume the advertised operation, then use the real merge and quality gates.
    # Missing guidance fails before any model fixture can invent an operation.
    operation = re.search(r"set\.verification_commands\s*=\s*(\[\])", prompt)
    assert operation is not None, (
        "PM feedback must explain the legal empty verifier-array operation for document planning"
    )
    replacement = json.loads(operation.group(1))
    merged = apply_revision(
        base, _patch(base.base_hash, [{"task_id": "TASK-DOC", "set": {"verification_commands": replacement}}])
    )
    assert merged["tasks"][0]["verification_commands"] == []
    assert merged["tasks"][1:] == before["tasks"][1:]
    assert base.tasks == before["tasks"]
    assert original == before
    for task in merged["tasks"]:
        rows, diagnostics = validate_pm_verification_commands(task["verification_commands"])
        assert not diagnostics
        assert rows == task["verification_commands"]
    normalized, repaired_quality = adapter._evaluate_contract_quality(
        merged["tasks"], directive="Create a TypeScript CLI"
    )
    assert repaired_quality["ok"] is True
    assert repaired_quality["score"] >= 80
    assert not repaired_quality["critical_issues"]
    by_id = {task["id"]: task for task in normalized}
    assert by_id["TASK-IMPLEMENT"]["verification_commands"] == before["tasks"][1]["verification_commands"]
    assert by_id["TASK-QA"]["verification_commands"] == before["tasks"][2]["verification_commands"]


def test_document_empty_verifiers_cannot_clear_project_delivery_gates(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    original = _candidate()
    base = make_revision_base(
        json.dumps(original),
        workspace=str(tmp_path),
        run_id="pm-project-gates",
        task_id="pm-task",
        directive="Create a TypeScript CLI",
        quality={},
    )
    assert base is not None
    merged = apply_revision(
        base,
        _patch(
            base.base_hash,
            [{"task_id": task["id"], "set": {"verification_commands": []}} for task in original["tasks"]],
        ),
    )
    _, quality = adapter._evaluate_contract_quality(merged["tasks"], directive="Create a TypeScript CLI")
    assert quality["ok"] is False
    assert "verification_commands_missing" in quality["critical_issues"]
    assert base.tasks == original["tasks"]


def test_document_probe_repair_preserves_same_task_installation(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    original = _candidate()
    installation = {"modality": "environment_prep", "argv": ["npm", "install"], "cwd": "."}
    original["tasks"][0]["verification_commands"] = [
        {"modality": "environment_prep", "argv": ["node", "--version"], "cwd": "."},
        installation,
    ]
    before = copy.deepcopy(original)
    _, quality = adapter._evaluate_contract_quality(original["tasks"], directive="Create a TypeScript CLI")
    assert quality["ok"] is False
    base = make_revision_base(
        json.dumps(original),
        workspace=str(tmp_path),
        run_id="pm-probe-installation",
        task_id="pm-task",
        directive="Create a TypeScript CLI",
        quality=quality,
    )
    assert base is not None
    merged = apply_revision(
        base, _patch(base.base_hash, [{"task_id": "TASK-DOC", "set": {"verification_commands": [installation]}}])
    )
    normalized, quality = adapter._evaluate_contract_quality(merged["tasks"], directive="Create a TypeScript CLI")
    assert quality["ok"] is True
    assert quality["score"] >= 80
    assert merged["tasks"][0]["verification_commands"] == [installation]
    assert next(task for task in normalized if task["id"] == "TASK-DOC")["verification_commands"] == [installation]
    assert merged["tasks"][1:] == before["tasks"][1:]
    assert base.tasks == before["tasks"]
