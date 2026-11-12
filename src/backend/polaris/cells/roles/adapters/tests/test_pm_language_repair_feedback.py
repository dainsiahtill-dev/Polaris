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
    adapter = PMAdapter(workspace=str(tmp_path))
    quality = {"contract_diagnostics": diagnostics}
    prompt = adapter._build_pm_retry_message(
        directive="Build a TypeScript model",
        quality=quality,
        previous_output=json.dumps(candidate),
        validated_candidate=True,
    )
    # The complete-replacement retry carries the exact diagnosis facts, so the
    # PM can repair its own declaration instead of keeping the bad targets.
    assert "foreign_scope_file_owners" in prompt
    assert "wrong_authored_targets" in prompt
    assert "allowed_source_suffixes" in prompt
    projected = json.loads(
        prompt.split(
            "Correct the complete semantic draft using this quality evidence; "
            "preserve the original user requirements and constraints:\n",
            1,
        )[1].split("\n", 1)[0]
    )
    # The optional technical topology belongs to the Chief Engineer, so the
    # repair authority the product PM receives stays on the product fields.
    assert projected["contract_diagnostics"][0]["repair_fields"] == ["goal", "description", "steps", "acceptance"]
