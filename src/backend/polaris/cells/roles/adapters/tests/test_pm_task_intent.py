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
async def test_actual_pm_provider_accepts_semantic_one_task_plan_without_recipe_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.adapters.tests.test_pm_factory_controlled_identity import _local_runtime
    from polaris.infrastructure.log_pipeline.jetstream_publisher import JetStreamPublisher

    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "kernel-home"))
    monkeypatch.setattr(JetStreamPublisher, "publish", lambda *_args, **_kwargs: True)
    intent = _intent()
    _runtime, _freezes, wires = _local_runtime(
        tmp_path, monkeypatch, [{"role": "assistant", "content": json.dumps(intent)}]
    )
    adapter = PMAdapter(workspace=str(tmp_path))
    result = await adapter._run_pm_stage(
        "pm-intent-task",
        "Create a Python record validator",
        {},
        {"run_id": "pm-intent-run", "metadata": {"llm_max_tokens": 4096}},
    )
    assert result["success"] is True, result
    assert len(wires) == 1
    artifact = json.loads((tmp_path / ".polaris/runtime/tasks/plan.json").read_text(encoding="utf-8"))
    assert len(artifact["tasks"]) == 1
    task = artifact["tasks"][0]
    assert task["target_files"] == task["verification_commands"] == []
    assert task["required_requirements"] == intent["required_requirements"]
    assert task["optional_requirements"] == intent["optional_requirements"]
    assert len(adapter.task_runtime.list_observable_task_rows()) == 1
    wire_text = "\n".join(str(message.get("content", "")) for message in wires[0]["messages"])
    assert "pm.task_intent.v1" in wire_text
    assert "至少 3 个任务" not in wire_text and "minimum 3" not in wire_text


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
async def test_actual_provider_revises_semantic_draft_under_the_original_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.adapters.tests.test_pm_factory_controlled_identity import _local_runtime
    from polaris.infrastructure.log_pipeline.jetstream_publisher import JetStreamPublisher

    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "kernel-home"))
    monkeypatch.setattr(JetStreamPublisher, "publish", lambda *_args, **_kwargs: True)
    intent = _intent()
    intent["tasks"][0]["acceptance"] = []

    def correction(wire: dict[str, Any]) -> dict[str, Any]:
        messages = "\n".join(str(message.get("content", "")) for message in wire["messages"])
        view, _ = json.JSONDecoder().raw_decode(messages.split("Revision base/view/manifest (server-owned):\n", 1)[1])
        assert view["product_contract_readonly"]["required_requirements"] == intent["required_requirements"]
        assert set(view["edit_manifest"]["tasks"]["TASK-1"]["set"]).isdisjoint(
            {"target_files", "verification_commands", "phase"}
        )
        return {
            "role": "assistant",
            "content": json.dumps(
                {
                    "schema_version": "pm.task_revision.v1",
                    "base_hash": view["base_hash"],
                    "updates": [
                        {"task_id": "TASK-1", "set": {"acceptance": ["A missing identifier produces a clear error"]}}
                    ],
                    "add_tasks": [],
                    "remove_task_ids": [],
                }
            ),
        }

    _runtime, _freezes, wires = _local_runtime(
        tmp_path, monkeypatch, [{"role": "assistant", "content": json.dumps(intent)}, correction]
    )
    adapter = PMAdapter(workspace=str(tmp_path))
    result = await adapter._run_pm_stage(
        "pm-intent-revision-task",
        "Create a Python record validator",
        {},
        {"run_id": "pm-intent-revision-run", "metadata": {"llm_max_tokens": 4096}},
    )
    assert result["success"] is True, result
    assert len(wires) == 2
    artifact = json.loads((tmp_path / ".polaris/runtime/tasks/plan.json").read_text(encoding="utf-8"))
    assert artifact["tasks"][0]["target_files"] == artifact["tasks"][0]["verification_commands"] == []
    assert artifact["tasks"][0]["required_requirements"] == intent["required_requirements"]
    assert artifact["tasks"][0]["optional_requirements"] == intent["optional_requirements"]


@pytest.mark.asyncio
async def test_unsafe_explicit_scope_is_terminal_before_pm_retry_or_task_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.adapters.tests.test_pm_factory_controlled_identity import _local_runtime
    from polaris.infrastructure.log_pipeline.jetstream_publisher import JetStreamPublisher

    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "kernel-home"))
    monkeypatch.setattr(JetStreamPublisher, "publish", lambda *_args, **_kwargs: True)
    unsafe = _intent()
    unsafe["tasks"][0]["target_files"] = ["../outside.py"]
    _, _, wires = _local_runtime(
        tmp_path,
        monkeypatch,
        [{"role": "assistant", "content": json.dumps(unsafe)}, {"role": "assistant", "content": json.dumps(_intent())}],
    )
    adapter = PMAdapter(workspace=str(tmp_path))
    result = await adapter._run_pm_stage(
        "pm-unsafe-task",
        "Create a Python validator",
        {},
        {"run_id": "pm-unsafe-run", "metadata": {"llm_max_tokens": 4096}},
    )
    assert result["success"] is False, "unsafe explicit scope cannot be erased by ordinary draft repair"
    assert len(wires) == 1
    assert result["tasks_created"] == 0
    assert adapter.task_runtime.list_observable_task_rows() == []


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
    base = make_revision_base(raw, workspace=str(tmp_path), run_id="dependency-revision", task_id="pm-deps",
                              directive="Create a Python validator", quality=quality)
    assert base is not None
    patch = {"schema_version": "pm.task_revision.v1", "base_hash": base.base_hash,
             "updates": [{"task_id": "TASK-1", "set": {"depends_on": []}}], "add_tasks": [], "remove_task_ids": []}
    revised = apply_revision(base, json.dumps(patch))
    assert revised["required_requirements"] == intent["required_requirements"]
    tasks = adapter._extract_task_contracts(json.dumps(revised), directive="Create a Python validator")
    _, quality = adapter._evaluate_contract_quality(tasks, directive="Create a Python validator")
    assert quality["ok"] is True, quality
