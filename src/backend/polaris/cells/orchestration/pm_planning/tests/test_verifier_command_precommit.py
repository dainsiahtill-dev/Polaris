"""PM diagnoses command-transport defects before immutable plan acceptance."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.control_plane.verifier_policy.public import (
    UpdateVerifierPolicyCommandV1,
    update_verifier_policy,
)
from polaris.cells.orchestration.pm_planning.public.service import evaluate_pm_task_quality
from polaris.cells.orchestration.pm_planning.tests.test_verification_operand_readiness import _command, _task
from polaris.cells.roles.adapters.internal.pm.revision import apply_revision, make_revision_base
from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter


@pytest.mark.parametrize(
    "argv",
    [
        ["node", "x" * 2049],
        ["node", "é" * 1025],
        ["node", "value\nwith-control"],
        ["node", 7],
        ["node"] + ["arg"] * 128,
    ],
)
def test_invalid_raw_argv_has_existing_precommit_feedback(argv: Any) -> None:
    tasks = [_task("TASK-1", ["scripts/check.mjs"], [], [_command(argv, modality="entrypoint")])]
    before = deepcopy(tasks)
    quality = evaluate_pm_task_quality({"tasks": tasks})
    assert quality["ok"] is False
    diagnostic = next(d for d in quality["contract_diagnostics"] if d["code"] == "verification_command_argv_invalid")
    assert diagnostic["path"] == "$.tasks[0].verification_commands[0].argv"
    assert diagnostic["task_id"] == "TASK-1"
    assert diagnostic["repair_fields"] == ["verification_commands"]
    assert tasks == before


def test_129_byte_supported_script_keeps_exact_command_and_task_identity() -> None:
    path = "scripts/" + "x" * 117 + ".mjs"
    assert len(path.encode("utf-8")) == 129
    tasks = [_task("TASK-1", [path], [], [_command(["node", path], modality="entrypoint")])]
    before = deepcopy(tasks)
    quality = evaluate_pm_task_quality({"tasks": tasks})
    assert quality["ok"] is True, quality
    assert not any(d["code"] == "verification_command_argv_invalid" for d in quality.get("contract_diagnostics", []))
    assert tasks == before


@pytest.mark.parametrize("flag", ["-e", "--eval"])
def test_inline_command_profile_is_diagnosed_before_plan_acceptance(tmp_path: Path, flag: str) -> None:
    script = "process.exit(" + "0" * 115 + ")"
    assert len(script.encode("utf-8")) == 129
    tasks = [_task("TASK-3", ["scripts/check.mjs"], [], [_command(["node", flag, script], modality="entrypoint")])]
    before = deepcopy(tasks)
    quality = evaluate_pm_task_quality({"tasks": tasks, "workspace": str(tmp_path)})
    assert quality["ok"] is False
    diagnostic = next(
        d for d in quality["contract_diagnostics"] if d["code"] == "verification_command_profile_unsupported"
    )
    assert diagnostic["path"] == "$.tasks[0].verification_commands[0]"
    assert diagnostic["task_id"] == "TASK-3"
    assert diagnostic["policy_error_code"] == "untrusted_verifier_command"
    assert diagnostic["repair_fields"] == ["verification_commands"]
    assert tasks == before


@pytest.mark.parametrize(
    ("invalid_argv", "diagnostic_code"),
    [
        (["node", "x" * 2049], "verification_command_argv_invalid"),
        (["node", "-e", "process.exit(0)"], "verification_command_profile_unsupported"),
    ],
)
def test_command_diagnosis_enters_same_base_revision_without_rewriting_other_authority(
    tmp_path: Path, invalid_argv: list[str], diagnostic_code: str
) -> None:
    tasks = [
        _task("TASK-1", ["scripts/check.mjs"], [], [_command(invalid_argv, modality="entrypoint")]),
        _task("TASK-2", ["src/module.ts"], ["TASK-1"], [_command(["npm", "test"])]),
    ]
    raw = json.dumps({"tasks": tasks}, ensure_ascii=False)
    adapter_factory: Callable[..., PMAdapter] = PMAdapter
    adapter = adapter_factory(workspace=str(tmp_path))
    _, quality = adapter._evaluate_contract_quality(
        tasks, directive="Implement declared modules and preserve their verification."
    )
    assert quality["ok"] is False
    assert any(d["code"] == diagnostic_code for d in quality["contract_diagnostics"])
    base = make_revision_base(
        raw,
        workspace=str(tmp_path),
        run_id="pm-transport",
        task_id="pm-plan",
        directive="Implement declared modules and preserve their verification.",
        quality=quality,
    )
    assert base is not None
    assert base.identity["original_response_hash"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert base.view["edit_manifest"]["tasks"]["TASK-1"]["set"]["verification_commands"] == "verifier_array"
    corrected = [_command(["node", "scripts/check.mjs"], modality="entrypoint")]
    patch = json.dumps(
        {
            "schema_version": "pm.task_revision.v1",
            "base_hash": base.base_hash,
            "updates": [{"task_id": "TASK-1", "set": {"verification_commands": corrected}}],
            "add_tasks": [],
            "remove_task_ids": [],
        }
    )
    proposal = apply_revision(base, patch)
    assert proposal["tasks"][1] == tasks[1]
    assert proposal["tasks"][0]["id"] == "TASK-1"
    assert proposal["tasks"][0]["goal"] == tasks[0]["goal"]
    assert proposal["tasks"][0]["scope_paths"] == tasks[0]["scope_paths"]
    assert base.response == raw
    assert not any(
        d["code"] == diagnostic_code for d in evaluate_pm_task_quality(proposal).get("contract_diagnostics", [])
    )


def test_real_pm_adapter_uses_bound_registered_custom_profile_before_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED", "1")
    script = "checks/assertions.sh"
    tasks = [_task("TASK-1", ["src/module.py"], [], [_command(["bash", script])])]
    before = deepcopy(tasks)
    adapter_factory: Callable[..., PMAdapter] = PMAdapter
    adapter = adapter_factory(workspace=str(tmp_path))
    _, rejected = adapter._evaluate_contract_quality(tasks)
    assert any(
        diagnostic["code"] == "verification_command_profile_unsupported"
        for diagnostic in rejected["contract_diagnostics"]
    )
    update_verifier_policy(
        UpdateVerifierPolicyCommandV1(
            workspace=str(tmp_path),
            custom_script_enabled=True,
            custom_scripts=(
                {
                    "id": "declared-check",
                    "path": script,
                    "modality": "test",
                    "enabled": True,
                    "content_sha256": hashlib.sha256(b"exit 0\n").hexdigest(),
                },
            ),
        )
    )
    _, accepted = adapter._evaluate_contract_quality(tasks)
    assert not any(
        diagnostic["code"].startswith("verification_command_profile_")
        for diagnostic in accepted.get("contract_diagnostics", [])
    ), accepted
    assert tasks == before
    assert not (tmp_path / script).exists()


def test_unbound_custom_candidate_is_advisory_without_inventing_permission() -> None:
    tasks = [_task("TASK-1", ["src/module.py"], [], [_command(["bash", "checks/assertions.sh"])])]
    quality = evaluate_pm_task_quality({"tasks": tasks})
    assert quality["ok"] is True, quality
    diagnostic = next(
        diagnostic
        for diagnostic in quality["contract_diagnostics"]
        if diagnostic["code"] == "verification_command_profile_uncertain"
    )
    assert diagnostic["severity"] == "warning"
    assert diagnostic["policy_error_code"] == "verifier_workspace_binding_missing"


@pytest.mark.parametrize(
    ("argv", "expected_error"),
    [(["npm", "ci"], ""), (["node", "--version"], "non_proving_verifier_command")],
)
def test_environment_preparation_uses_same_profile_owner(argv: list[str], expected_error: str) -> None:
    tasks = [_task("TASK-1", ["package.json"], [], [_command(argv, modality="environment_prep")])]
    quality = evaluate_pm_task_quality({"tasks": tasks})
    diagnostics = quality.get("contract_diagnostics", [])
    if expected_error:
        assert quality["ok"] is False
        assert any(d.get("policy_error_code") == expected_error for d in diagnostics)
    else:
        assert quality["ok"] is True, quality
        assert not diagnostics
