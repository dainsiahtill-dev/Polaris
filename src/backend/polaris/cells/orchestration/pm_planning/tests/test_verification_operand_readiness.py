"""PM verification cannot require an artifact produced by an unavailable task."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.orchestration.pm_planning.public.service import evaluate_pm_task_quality
from polaris.cells.roles.adapters.internal.pm.revision import apply_revision, make_revision_base
from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter


def _task(identity: str, targets: list[str], deps: list[str], commands: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": identity,
        "title": f"Implement {identity} deliverable",
        "goal": f"Implement the {identity} module and retain real behavioral verification results.",
        "target_files": targets,
        "scope_paths": targets,
        "depends_on": deps,
        "phase": "implementation",
        "assigned_to": "Director",
        "steps": ["Implement declared files", "Run committed verification"],
        "execution_checklist": ["Materialize owned files", "Retain verification evidence"],
        "acceptance": [f"{targets[0]} is implemented; npm test passes with at least 2 behavior assertions."],
        "verification_commands": commands,
    }


def _command(argv: list[str], *, cwd: str = ".", modality: str = "test") -> dict[str, Any]:
    return {"modality": modality, "argv": argv, "cwd": cwd}


def _r24_shape() -> list[dict[str, Any]]:
    doc = _task("TASK-CE-1", ["docs/blueprint.md"], [], [_command(["npm", "install"], modality="environment_prep")])
    doc.update(assigned_to="ChiefEngineer", phase="planning")
    return [
        doc,
        _task(
            "TASK-DIR-2",
            ["src/domain.ts"],
            ["TASK-CE-1"],
            [
                _command(["npm", "install"], modality="environment_prep"),
                _command(["npx", "tsc", "-p", "tsconfig.json"], modality="build"),
                _command(["node", "--test", "tests/domain.test.mjs"]),
            ],
        ),
        _task(
            "TASK-DIR-3",
            ["package.json", "tsconfig.json", "index.html"],
            ["TASK-DIR-2"],
            [_command(["npm", "test"]), _command(["node", "scripts/verify.mjs"], modality="entrypoint")],
        ),
        _task(
            "TASK-DIR-4",
            ["tests/domain.test.mjs", "scripts/verify.mjs"],
            ["TASK-DIR-3"],
            [_command(["npm", "test"]), _command(["npm", "run", "verify"])],
        ),
        _task("TASK-QA-5", ["reports/qa.md"], ["TASK-DIR-4"], [_command(["npm", "test"])]),
    ]


def test_r24_future_operands_are_localized_before_pm_acceptance() -> None:
    tasks = _r24_shape()
    before = deepcopy(tasks)
    quality = evaluate_pm_task_quality({"tasks": tasks})
    assert quality["ok"] is False
    diagnostics = [d for d in quality["contract_diagnostics"] if d["code"] == "verification_operand_not_ready"]
    assert {(d["task_id"], d["operand_path"], tuple(d["producer_task_ids"])) for d in diagnostics} == {
        ("TASK-DIR-2", "tests/domain.test.mjs", ("TASK-DIR-4",)),
        ("TASK-DIR-2", "tsconfig.json", ("TASK-DIR-3",)),
        ("TASK-DIR-3", "scripts/verify.mjs", ("TASK-DIR-4",)),
    }
    domain = next(d for d in diagnostics if d["operand_path"] == "tests/domain.test.mjs")
    assert domain["path"] == "$.tasks[1].verification_commands[2]"
    assert domain["argv"] == ["node", "--test", "tests/domain.test.mjs"]
    assert domain["cwd"] == "."
    assert domain["repair_fields"] == ["verification_commands", "depends_on"]
    assert "verification_operand_not_ready" in quality["critical_issues"]
    assert tasks == before


@pytest.mark.parametrize(
    "argv,cwd,target,profile_error",
    [
        (["node", "--test", "tests/input.mjs"], ".", "tests/input.mjs", ""),
        (["python3", "scripts/check.py"], ".", "scripts/check.py", ""),
        (["tsc", "-p", "config/tsconfig.json"], ".", "config/tsconfig.json", "untrusted_verifier_command"),
        (["npx", "tsc", "--project=config/tsconfig.json"], ".", "config/tsconfig.json", "verifier_modality_mismatch"),
        (
            ["npx", "--no-install", "tsc", "--project", "tsconfig.json"],
            "app",
            "app/tsconfig.json",
            "untrusted_verifier_command",
        ),
        (["node", "--test", "tests/input.mjs"], "app", "app/tests/input.mjs", ""),
    ],
)
def test_own_or_transitively_available_operand_is_ready(
    argv: list[str], cwd: str, target: str, profile_error: str
) -> None:
    tasks = [
        _task("PRODUCER", [target], [], []),
        _task("MIDDLE", ["src/middle.py"], ["PRODUCER"], []),
        _task("CONSUMER", ["reports/result.md"], ["MIDDLE"], [_command(argv, cwd=cwd)]),
    ]
    own = [_task("OWN", [target], [], [_command(argv, cwd=cwd)])]
    for available in (tasks, own):
        quality = evaluate_pm_task_quality({"tasks": available})
        diagnostics = quality.get("contract_diagnostics", [])
        assert not any(d["code"].startswith("verification_operand_") for d in diagnostics)
        assert quality["ok"] is (not profile_error)
        if profile_error:
            assert any(d.get("policy_error_code") == profile_error for d in diagnostics)
    premature = [
        _task("EARLY", ["src/early.py"], [], [_command(argv, cwd=cwd)]),
        _task("LATER", [target], ["EARLY"], []),
    ]
    premature_quality = evaluate_pm_task_quality({"tasks": premature})
    assert premature_quality["ok"] is False
    diagnostic = next(
        d for d in premature_quality["contract_diagnostics"] if d["code"] == "verification_operand_not_ready"
    )
    assert diagnostic["operand_path"] == target
    assert diagnostic["producer_task_ids"] == ["LATER"]


@pytest.mark.parametrize(
    "argv,profile_error",
    [
        (["npm", "test"], ""),
        (["npm", "run", "verify"], "untrusted_verifier_command"),
        (["pip", "install", "scripts/input.py"], "untrusted_verifier_command"),
        (["python", "-c", "print('scripts/input.py')"], "untrusted_verifier_command"),
        (["python", "-m", "scripts.input"], "verifier_modality_mismatch"),
        (["node", "--eval", "require('scripts/input.py')"], "untrusted_verifier_command"),
        (["tsc", "--outDir", "scripts/input.py"], "untrusted_verifier_command"),
        (["node", "--test", "tests/*.mjs"], ""),
        (["node", "../scripts/input.py"], "untrusted_verifier_command"),
    ],
)
def test_unknown_package_dynamic_or_output_operands_do_not_invent_dependencies(
    argv: list[str], profile_error: str
) -> None:
    tasks = [_task("EARLY", ["src/main.py"], [], [_command(argv)]), _task("LATER", ["scripts/input.py"], ["EARLY"], [])]
    quality = evaluate_pm_task_quality({"tasks": tasks})
    diagnostics = quality.get("contract_diagnostics", [])
    assert not any(d["code"].startswith("verification_operand_") for d in diagnostics)
    assert quality["ok"] is (not profile_error)
    if profile_error:
        assert any(d.get("policy_error_code") == profile_error for d in diagnostics)


def test_multiple_declared_producers_are_advisory_without_inventing_an_owner() -> None:
    tasks = [
        _task("EARLY", ["src/main.py"], [], [_command(["python", "scripts/check.py"])]),
        _task("LATER-A", ["scripts/check.py"], ["EARLY"], []),
        _task("LATER-B", ["scripts/check.py"], ["EARLY"], []),
    ]
    quality = evaluate_pm_task_quality({"tasks": tasks})
    assert quality["ok"] is True
    assert quality["score"] == 100
    diagnostic = next(
        d for d in quality["contract_diagnostics"] if d["code"] == "verification_operand_producer_ambiguous"
    )
    assert diagnostic["severity"] == "warning"
    assert diagnostic["producer_task_ids"] == ["LATER-A", "LATER-B"]


def test_typed_same_base_correction_retains_real_verifiers_and_other_tasks(tmp_path: Path) -> None:
    tasks = [
        _task(
            "EARLY",
            ["src/main.py"],
            [],
            [_command(["npm", "install"], modality="environment_prep"), _command(["python", "scripts/check.py"])],
        ),
        _task(
            "LATER",
            ["scripts/check.py"],
            ["EARLY"],
            [_command(["python", "scripts/check.py"]), _command(["python", "scripts/check.py"], modality="entrypoint")],
        ),
    ]
    raw = json.dumps({"tasks": tasks}, ensure_ascii=False)
    adapter = PMAdapter(workspace=str(tmp_path))
    _, quality = adapter._evaluate_contract_quality(tasks, directive="Implement modules and verify results")
    assert quality["ok"] is False
    assert any(d["code"] == "verification_operand_not_ready" for d in quality["contract_diagnostics"])
    base = make_revision_base(
        raw,
        workspace=str(tmp_path),
        run_id="pm-temporal",
        task_id="pm-task",
        directive="Implement modules and verify results",
        quality=quality,
    )
    assert base is not None
    assert base.identity["original_response_hash"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert base.view["edit_manifest"]["tasks"]["EARLY"]["set"]["verification_commands"] == "verifier_array"
    patch = json.dumps(
        {
            "schema_version": "pm.task_revision.v1",
            "base_hash": base.base_hash,
            "updates": [{"task_id": "EARLY", "set": {"verification_commands": tasks[0]["verification_commands"][:1]}}],
            "add_tasks": [],
            "remove_task_ids": [],
        }
    )
    merged = apply_revision(base, patch)
    assert merged["tasks"][1] == tasks[1]
    assert merged["tasks"][0]["goal"] == tasks[0]["goal"]
    assert merged["tasks"][0]["scope_paths"] == tasks[0]["scope_paths"]
    assert base.response == raw and base.tasks == tasks
    _, repaired_quality = adapter._evaluate_contract_quality(
        merged["tasks"], directive="Implement modules and verify results"
    )
    assert not any(
        d["code"].startswith("verification_operand_") for d in repaired_quality.get("contract_diagnostics", [])
    )
    # Correcting availability cannot approve an unregistered test script.
    assert repaired_quality["ok"] is False
    assert any(
        d.get("policy_error_code") == "verifier_modality_mismatch" for d in repaired_quality["contract_diagnostics"]
    )


def test_temporal_diagnostics_survive_other_existing_verifier_findings(tmp_path: Path) -> None:
    tasks = [
        _task("EARLY", ["src/main.py"], [], [_command(["python", "scripts/check.py"])]),
        _task(
            "LATER",
            ["scripts/check.py"],
            ["EARLY"],
            [_command(["python", "scripts/check.py"]), _command(["python", "scripts/check.py"], modality="entrypoint")],
        ),
    ]
    _, quality = PMAdapter(workspace=str(tmp_path))._evaluate_contract_quality(
        tasks, directive="Implement modules and verify results"
    )
    assert quality["ok"] is False
    assert "verification_environment_prep_missing" in quality["critical_issues"]
    assert any(d["code"] == "verification_operand_not_ready" for d in quality["contract_diagnostics"])
