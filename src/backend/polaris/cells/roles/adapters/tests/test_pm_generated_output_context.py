"""PM must receive the artifact facts needed to repair its own declaration."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from polaris.cells.orchestration.pm_planning.public.artifact_contracts import validate_pm_artifact_contracts
from polaris.cells.roles.adapters.internal.pm import revision
from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter
from polaris.cells.roles.adapters.tests.test_pm_factory_controlled_identity import _contracts


def _candidate() -> dict:
    draft = json.loads(_contracts(valid=True))
    task = draft["tasks"][0]
    task["target_files"] = ["src/input.py"]
    task["scope_paths"] = ["src/input.py"]
    task["verification_commands"] = [{"modality": "build", "argv": ["python3", "scripts/build.py"], "cwd": "."}]
    task["generated_outputs"] = [
        {
            "path": "src/input.py",
            "inputs": [{"task_id": task["id"], "path": "src/input.py"}],
            "build": {"argv": ["python3", "scripts/build.py"], "cwd": "."},
        }
    ]
    return draft


def test_generated_output_collision_exposes_exact_authored_owner_without_mutation() -> None:
    draft = _candidate()
    before = copy.deepcopy(draft)
    result = validate_pm_artifact_contracts(draft["tasks"], primary_language="python")
    diagnostic = next(d for d in result.diagnostics if d["code"] == "generated_output_ownership_invalid")
    assert diagnostic["path"].endswith(".generated_outputs[0]")
    assert diagnostic["evidence"] == {
        "generated_output_path": "src/input.py",
        "authored_owner_task_ids": [draft["tasks"][0]["id"]],
        "generated_owner_task_ids": [draft["tasks"][0]["id"]],
    }
    assert draft == before


def test_generated_build_mismatch_exposes_exact_declared_build_authority() -> None:
    draft = _candidate()
    task = draft["tasks"][0]
    task["generated_outputs"][0]["path"] = "dist/output.txt"
    task["generated_outputs"][0]["build"]["argv"] = ["python3", "scripts/other.py"]
    result = validate_pm_artifact_contracts(draft["tasks"], primary_language="python")
    diagnostic = next(d for d in result.diagnostics if d["code"] == "generated_output_build_invalid")
    assert diagnostic["evidence"]["declared_output_build"] == {"argv": ["python3", "scripts/other.py"], "cwd": "."}
    assert diagnostic["evidence"]["task_build_verifiers"] == task["verification_commands"]


@pytest.mark.parametrize("mode", ["initial", "complete_retry", "bounded_retry"])
def test_every_pm_request_explains_build_outputs_and_empty_optional_declaration(tmp_path: Path, mode: str) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    candidate = _candidate()
    diagnostics = list(validate_pm_artifact_contracts(candidate["tasks"], primary_language="python").diagnostics)
    quality = {"critical_issues": ["generated_output_ownership_invalid"], "contract_diagnostics": diagnostics}
    if mode == "initial":
        prompt = adapter._build_pm_message([], "Create model")
    else:
        base = (
            revision.make_revision_base(
                json.dumps(candidate),
                workspace=str(tmp_path),
                run_id="run",
                task_id="pm",
                directive="Create model",
                quality=quality,
            )
            if mode == "bounded_retry"
            else None
        )
        if mode == "bounded_retry":
            assert base is not None
        prompt = adapter._build_pm_retry_message(
            directive="Create model",
            quality=quality,
            previous_output=json.dumps(candidate),
            validated_candidate=True,
            revision_base=base,
        )
    assert "构建命令产生的派生文件" in prompt
    assert "所有任务的 target_files" in prompt
    assert "generated_outputs=[]" in prompt
    assert "src/input.py" in prompt if mode != "initial" else True


def test_bounded_retry_keeps_program_generated_collision_evidence(tmp_path: Path) -> None:
    candidate = _candidate()
    diagnostics = list(validate_pm_artifact_contracts(candidate["tasks"], primary_language="python").diagnostics)
    quality = {"critical_issues": ["generated_output_ownership_invalid"], "contract_diagnostics": diagnostics}
    base = revision.make_revision_base(
        json.dumps(candidate),
        workspace=str(tmp_path),
        run_id="run",
        task_id="pm",
        directive="Create model",
        quality=quality,
    )
    assert base is not None
    prompt = PMAdapter(workspace=str(tmp_path))._build_pm_retry_message(
        directive="Create model", quality=quality, previous_output=base.response, revision_base=base
    )
    visible = json.JSONDecoder().raw_decode(prompt.split("Quality evidence (not execution authority):\n", 1)[1])[0]
    assert visible["contract_diagnostics"][0]["evidence"] == diagnostics[0]["evidence"]
    assert base.view["edit_manifest"]["tasks"][candidate["tasks"][0]["id"]]["set"] == {
        "generated_outputs": "generated_outputs_array"
    }


def test_bounded_revision_explains_exclusive_field_operations(tmp_path: Path) -> None:
    candidate = _candidate()
    diagnostics = list(validate_pm_artifact_contracts(candidate["tasks"], primary_language="python").diagnostics)
    quality = {"critical_issues": ["generated_output_ownership_invalid"], "contract_diagnostics": diagnostics}
    base = revision.make_revision_base(
        json.dumps(candidate),
        workspace=str(tmp_path),
        run_id="run",
        task_id="pm",
        directive="Create model",
        quality=quality,
    )
    assert base is not None
    prompt = PMAdapter(workspace=str(tmp_path))._build_pm_retry_message(
        directive="Create model", quality=quality, previous_output=base.response, revision_base=base
    )
    assert "同一字段只能选择 set 或 append" in prompt
    assert "phase" in prompt
    assert "不在 edit_manifest" in prompt
