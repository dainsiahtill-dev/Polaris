"""Light PM contracts reach the actual provider, parser and public admission gate."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.orchestration.pm_planning.public import contracts as contract_api
from polaris.cells.orchestration.pm_planning.public.service import (
    autofix_pm_contract_for_quality,
    evaluate_pm_task_quality,
)
from polaris.cells.roles.adapters.public import PMAdapter


def _intent() -> dict[str, Any]:
    return {
        "schema_version": "pm.task_intent.v1",
        "product_goal": "Implement a record validator with clear invalid-input feedback",
        "required_requirements": ["Valid records retain their original fields", "Reject missing record identifiers"],
        "optional_requirements": ["Offer colored terminal output if appropriate"],
        "acceptance": ["A valid record keeps its identifier", "A missing identifier produces a clear error"],
        "explicit_constraints": ["Use Python", "Preserve the user's input records"],
        "tasks": [
            {
                "id": "TASK-1",
                "title": "Implement record validation",
                "goal": "Implement valid and invalid record handling together",
                "acceptance": ["A missing identifier produces a clear error"],
                "depends_on": [],
            }
        ],
    }


def _project(payload: dict[str, Any]) -> list[dict[str, Any]]:
    normalize = getattr(contract_api, "normalize_pm_task_intent", None)
    assert callable(normalize), "public semantic PM contract normalizer is missing"
    tasks: list[dict[str, Any]] = normalize(payload)
    return tasks


def test_public_single_task_contract_delegates_technical_choices_without_manufacturing_work() -> None:
    intent = _intent()
    before = deepcopy(intent)
    tasks = _project(intent)
    assert intent == before
    assert len(tasks) == 1
    task = tasks[0]
    assert task["goal"] == intent["tasks"][0]["goal"]
    assert task["required_requirements"] == intent["required_requirements"]
    assert task["optional_requirements"] == intent["optional_requirements"]
    assert task["explicit_constraints"] == intent["explicit_constraints"]
    assert task["product_acceptance"] == intent["acceptance"]
    assert task["target_files"] == task["scope_paths"] == task["verification_commands"] == []
    assert task.get("phase", "") == "" and task.get("steps", []) == []
    assert task["metadata"]["topology_authority"] == "chief_engineer"
    assert task["metadata"]["verification_authority"] == "chief_engineer"
    payload = {"tasks": tasks}
    before = deepcopy(payload)
    autofix_pm_contract_for_quality(payload, workspace_full="/example/workspace")
    assert payload == before
    quality = evaluate_pm_task_quality(payload, workspace_full="/example/workspace")
    assert quality["ok"] is True, quality
    assert quality["score"] == 100


@pytest.mark.parametrize(
    "defect",
    [
        "empty_goal",
        "empty_required",
        "empty_acceptance",
        "unknown_dependency",
        "cycle",
        "unsafe_path",
        "unsafe_command",
    ],
)
def test_public_semantic_contract_keeps_hard_input_boundaries(defect: str) -> None:
    intent = _intent()
    if defect == "empty_goal":
        intent["product_goal"] = ""
    elif defect == "empty_required":
        intent["required_requirements"] = []
    elif defect == "empty_acceptance":
        intent["acceptance"] = []
    elif defect == "unknown_dependency":
        intent["tasks"][0]["depends_on"] = ["missing"]
    elif defect == "cycle":
        intent["tasks"][0]["depends_on"] = ["TASK-1"]
    elif defect == "unsafe_path":
        intent["tasks"][0]["target_files"] = ["../outside.py"]
    else:
        intent["tasks"][0]["verification_commands"] = [
            {"modality": "test", "argv": ["bash", "-c", "anything"], "cwd": "."}
        ]
    normalize = getattr(contract_api, "normalize_pm_task_intent", None)
    assert callable(normalize), "public semantic PM contract normalizer is missing"
    with pytest.raises(ValueError):
        normalize(intent)


def test_explicit_paths_and_exact_command_values_survive_public_projection() -> None:
    intent = _intent()
    row = intent["tasks"][0]
    row["target_files"] = ["records.py"]
    row["scope_paths"] = ["records.py"]
    row["verification_commands"] = [{"modality": "test", "argv": ["python3", "-m", "unittest", "discover"], "cwd": "."}]
    task = _project(intent)[0]
    assert task["target_files"] == row["target_files"]
    assert task["verification_commands"] == row["verification_commands"]
    assert task["metadata"]["verification_authority"] == "chief_engineer"
    assert "verification_commands" in task["metadata"]["explicit_technical_fields"]


def test_legacy_metadata_alone_cannot_forge_semantic_admission(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    raw = {
        "tasks": [
            {
                "id": "TASK-1",
                "title": "Implement records",
                "goal": "Implement record validation",
                "acceptance": ["clear error"],
                "metadata": {
                    "planning_contract_version": "pm.task_intent.v1",
                    "topology_authority": "chief_engineer",
                    "verification_authority": "chief_engineer",
                },
            }
        ]
    }
    diagnostics: list[dict[str, Any]] = []
    tasks = adapter._extract_task_contracts(
        json.dumps(raw), directive="Create a Python record validator", diagnostics=diagnostics
    )
    assert not tasks, "legacy metadata must not activate semantic admission"
    assert diagnostics


@pytest.mark.asyncio
async def test_semantic_one_task_plan_needs_no_recipe_retry(
    tmp_path: Path,
) -> None:
    """A semantic one-task plan is admitted without a recipe retry.

    The scenario this test drove reached the retired legacy role session
    entry to observe the provider turn and the plan artifact it produced.
    That entry is closed now: a new engineering run has exactly one owner,
    the engineering run service. The semantic admission stays a live
    server-side contract, so it is asserted directly here.
    """
    from polaris.cells.roles.kernel.tests.legacy_engine_retirement import (
        assert_legacy_session_entry_is_closed,
    )

    await assert_legacy_session_entry_is_closed(
        role="pm",
        session_id="retired-pm-intent-admission",
        workspace=str(tmp_path),
    )
    intent = _intent()
    adapter = PMAdapter(workspace=str(tmp_path))
    tasks = adapter._extract_task_contracts(
        json.dumps(intent), directive="Create a Python record validator"
    )
    normalized, quality = adapter._evaluate_contract_quality(
        tasks, directive="Create a Python record validator"
    )
    assert quality["ok"] is True, quality
    assert quality["score"] == 100
    assert len(normalized) == 1
    # The semantic contract delegates the technical recipe, so the admitted
    # plan carries no invented paths and no verifier commands.
    assert normalized[0]["target_files"] == normalized[0]["verification_commands"] == []
    assert normalized[0]["required_requirements"] == intent["required_requirements"]
    assert normalized[0]["optional_requirements"] == intent["optional_requirements"]
    # The request states the semantic contract and no legacy task-count rule.
    message = adapter._build_pm_message([], "Create a Python record validator")
    assert "pm.task_intent.v1" in message
    assert "至少 3 个任务" not in message and "minimum 3" not in message


def test_semantic_acceptance_revision_preserves_original_product_contract(tmp_path: Path) -> None:
    from polaris.cells.roles.adapters.internal.pm.revision import apply_revision, make_revision_base

    intent = _intent()
    intent["tasks"][0]["acceptance"] = []
    adapter = PMAdapter(workspace=str(tmp_path))
    raw = json.dumps(intent)
    tasks = adapter._extract_task_contracts(raw, directive="Create a Python validator")
    normalized, quality = adapter._evaluate_contract_quality(tasks, directive="Create a Python validator")
    assert quality["ok"] is False
    base = make_revision_base(
        raw,
        workspace=str(tmp_path),
        run_id="intent-revision",
        task_id="pm-intent",
        directive="Create a Python validator",
        quality=quality,
    )
    assert base is not None, "ordinary semantic draft omission must retain its original revision base"
    patch = {
        "schema_version": "pm.task_revision.v1",
        "base_hash": base.base_hash,
        "updates": [{"task_id": "TASK-1", "set": {"acceptance": ["A missing identifier produces a clear error"]}}],
        "add_tasks": [],
        "remove_task_ids": [],
    }
    revised = apply_revision(base, json.dumps(patch))
    assert revised["schema_version"] == "pm.task_intent.v1"
    for field in (
        "product_goal",
        "required_requirements",
        "optional_requirements",
        "acceptance",
        "explicit_constraints",
    ):
        assert revised[field] == intent[field]
    tasks = adapter._extract_task_contracts(json.dumps(revised), directive="Create a Python validator")
    normalized, quality = adapter._evaluate_contract_quality(tasks, directive="Create a Python validator")
    assert quality["ok"] is True, quality
    assert len(normalized) == 1
    assert normalized[0]["target_files"] == normalized[0]["verification_commands"] == []


@pytest.mark.asyncio
async def test_semantic_draft_revision_keeps_the_original_manifest(
    tmp_path: Path,
) -> None:
    """A semantic draft revision stays under the original manifest.

    The scenario this test drove reached the retired legacy role session
    entry to observe the revision turn and its wire. That entry is closed
    now: a new engineering run has exactly one owner, the engineering run
    service. The manifest that answers the revision stays a live server-side
    contract, so it is asserted directly here.
    """
    from polaris.cells.roles.adapters.internal.pm.revision import make_revision_base
    from polaris.cells.roles.kernel.tests.legacy_engine_retirement import (
        assert_legacy_session_entry_is_closed,
    )

    await assert_legacy_session_entry_is_closed(
        role="pm",
        session_id="retired-pm-intent-revision",
        workspace=str(tmp_path),
    )
    intent = _intent()
    intent["tasks"][0]["acceptance"] = []
    adapter = PMAdapter(workspace=str(tmp_path))
    raw = json.dumps(intent)
    tasks = adapter._extract_task_contracts(raw, directive="Create a Python record validator")
    _normalized, quality = adapter._evaluate_contract_quality(
        tasks, directive="Create a Python record validator"
    )
    assert quality["ok"] is False
    base = make_revision_base(
        raw,
        workspace=str(tmp_path),
        run_id="pm-intent-revision-run",
        task_id="pm-intent-revision-task",
        directive="Create a Python record validator",
        quality=quality,
    )
    assert base is not None
    # The product contract is read-only for the revision, and the technical
    # fields stay outside the edit authority.
    assert base.view["product_contract_readonly"]["required_requirements"] == intent["required_requirements"]
    assert set(base.view["edit_manifest"]["tasks"]["TASK-1"]["set"]).isdisjoint(
        {"target_files", "verification_commands", "phase"}
    )
    assert "acceptance" in base.view["edit_manifest"]["tasks"]["TASK-1"]["set"]


@pytest.mark.asyncio
async def test_unsafe_explicit_scope_is_terminal_before_any_retry(
    tmp_path: Path,
) -> None:
    """An unsafe explicit scope is terminal for the planning stage.

    The scenario this test drove reached the retired legacy role session
    entry to observe that the unsafe draft was not repaired by a second
    provider turn. That entry is closed now: a new engineering run has
    exactly one owner, the engineering run service. The terminal boundary
    itself stays a live server-side fact, so it is asserted directly here.
    """
    from polaris.cells.roles.kernel.tests.legacy_engine_retirement import (
        assert_legacy_session_entry_is_closed,
    )

    await assert_legacy_session_entry_is_closed(
        role="pm",
        session_id="retired-pm-unsafe-scope",
        workspace=str(tmp_path),
    )
    unsafe = _intent()
    unsafe["tasks"][0]["target_files"] = ["../outside.py"]
    adapter = PMAdapter(workspace=str(tmp_path))
    diagnostics: list[dict[str, Any]] = []
    tasks = adapter._extract_task_contracts(
        json.dumps(unsafe), directive="Create a Python validator", diagnostics=diagnostics
    )
    # An unsafe explicit scope produces no task and a hard boundary fact, so
    # the stage cannot admit or repair it.
    assert tasks == []
    assert diagnostics
    assert diagnostics[0]["hard_boundary"] is True
    assert diagnostics[0]["code"] == "pm_task_intent_invalid"


@pytest.mark.parametrize("defect", ["unknown_dependency", "cycle"])
def test_semantic_dependency_correction_uses_the_original_base(tmp_path: Path, defect: str) -> None:
    from polaris.cells.roles.adapters.internal.pm.revision import apply_revision, make_revision_base

    intent = _intent()
    intent["tasks"][0]["depends_on"] = ["missing" if defect == "unknown_dependency" else "TASK-1"]
    raw = json.dumps(intent)
    adapter = PMAdapter(workspace=str(tmp_path))
    tasks = adapter._extract_task_contracts(raw, directive="Create a Python validator")
    assert len(tasks) == 1, "correctable dependency draft must remain retained, without execution authority"
    _, quality = adapter._evaluate_contract_quality(tasks, directive="Create a Python validator")
    assert quality["ok"] is False
    base = make_revision_base(
        raw,
        workspace=str(tmp_path),
        run_id="dependency-revision",
        task_id="pm-deps",
        directive="Create a Python validator",
        quality=quality,
    )
    assert base is not None
    patch = {
        "schema_version": "pm.task_revision.v1",
        "base_hash": base.base_hash,
        "updates": [{"task_id": "TASK-1", "set": {"depends_on": []}}],
        "add_tasks": [],
        "remove_task_ids": [],
    }
    revised = apply_revision(base, json.dumps(patch))
    assert revised["required_requirements"] == intent["required_requirements"]
    tasks = adapter._extract_task_contracts(json.dumps(revised), directive="Create a Python validator")
    _, quality = adapter._evaluate_contract_quality(tasks, directive="Create a Python validator")
    assert quality["ok"] is True, quality
