"""Language rejection must expose replacements and related read-only scopes."""

import json
from copy import deepcopy
from pathlib import Path

from polaris.cells.orchestration.pm_planning.public.artifact_contracts import validate_pm_artifact_contracts
from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter


def _candidate() -> dict:
    return {
        "tasks": [
            {
                "id": "build",
                "target_files": ["src/model.ts", "package.json", "tsconfig.json"],
                "phase": "implementation",
                "assigned_to": "Director",
                "depends_on": [],
            },
            {
                "id": "verify",
                "target_files": ["src/other.rs"],
                "scope_paths": ["src/other.rs", "package.json", "tsconfig.json"],
                "phase": "validation",
                "assigned_to": "Director",
                "depends_on": ["build"],
                "metadata": {"pm_artifact_source_path": "$.tasks[1]"},
                "verification_commands": [{"modality": "test", "argv": ["npm", "test"], "cwd": "."}],
            },
        ]
    }


def test_language_diagnosis_has_exact_replacement_and_scope_evidence() -> None:
    candidate = _candidate()
    before = deepcopy(candidate)
    result = validate_pm_artifact_contracts(candidate["tasks"], primary_language="typescript")
    diagnostic = next(d for d in result.diagnostics if d["code"] == "pm_plan_language_mismatch")

    assert diagnostic["evidence"] == {
        "primary_language": "typescript",
        "wrong_authored_targets": ["src/other.rs"],
        "allowed_source_suffixes": [".cts", ".mts", ".ts", ".tsx"],
        "foreign_scope_file_owners": {"package.json": ["build"], "tsconfig.json": ["build"]},
    }
    assert set(diagnostic["repair_fields"]) >= {
        "target_files",
        "scope_paths",
        "context_files",
        "goal",
        "steps",
        "acceptance",
        "verification_commands",
    }
    assert candidate == before


def test_full_retry_explains_replacement_instead_of_retaining_bad_targets(tmp_path: Path) -> None:
    candidate = _candidate()
    diagnostics = list(validate_pm_artifact_contracts(candidate["tasks"], primary_language="typescript").diagnostics)
    prompt = PMAdapter(workspace=str(tmp_path))._build_pm_retry_message(
        directive="Build a TypeScript model",
        quality={"contract_diagnostics": diagnostics},
        previous_output=json.dumps(candidate),
        validated_candidate=True,
    )
    assert "替换或移除不合格路径" in prompt
    assert "不要保留错误 target_files 后再追加" in prompt
    assert "foreign_scope_file_owners" in prompt
    assert "context_files" in prompt
