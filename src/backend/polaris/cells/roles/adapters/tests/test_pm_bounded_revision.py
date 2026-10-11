"""Typed edits cannot replace hidden content or gain execution authority."""

from __future__ import annotations

import copy
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.pm import revision
from polaris.cells.roles.adapters.tests.test_pm_factory_controlled_identity import _contracts


def _base(tmp_path: Path, *, run_id: str = "child-a", quality: Any = None) -> Any:
    return revision.make_revision_base(
        _contracts(valid=True),
        workspace=str(tmp_path),
        run_id=run_id,
        task_id="pm-task",
        directive="Create model",
        quality=quality or {},
    )


def _patch(base: Any, **kwargs: Any) -> dict[str, Any]:
    return {
        "schema_version": "pm.task_revision.v1",
        "base_hash": base.base_hash,
        "updates": [],
        "add_tasks": [],
        "remove_task_ids": [],
        **kwargs,
    }


def test_unlocalized_html_blocker_requires_complete_reanswer(tmp_path: Path) -> None:
    quality = {
        "critical_issues": ["generated_output_input_invalid", "factory_typescript_contract_missing:index.html"],
        "contract_diagnostics": [
            {
                "code": "generated_output_input_invalid",
                "path": "$.tasks[0].generated_outputs",
                "task_id": "TASK-1-source",
                "message": "Inputs must bind self/ancestor authored targets",
            }
        ],
    }
    assert _base(tmp_path, quality=quality) is None


def test_generated_revision_displays_verifier_and_safe_derived_diagnostic_without_permission(tmp_path: Path) -> None:
    from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter

    diagnostic = {
        "code": "generated_output_input_invalid",
        "path": "$.tasks[0].generated_outputs",
        "task_id": "TASK-1-source",
        "message": "Inputs must bind self/ancestor authored targets",
        "provenance": {"original_task_id": "TASK-1", "derived_task_id": "TASK-1-source", "stage": "quality_autofix"},
    }
    quality = {"critical_issues": ["generated_output_input_invalid"], "contract_diagnostics": [diagnostic]}
    base = _base(tmp_path, quality=quality)
    assert base is not None
    row = base.view["tasks"][0]
    assert row["verification_commands"] == base.tasks[0]["verification_commands"]
    assert "verification_commands" not in base.view["edit_manifest"]["tasks"]["TASK-1"]["set"]
    prompt = PMAdapter(workspace=str(tmp_path))._build_pm_retry_message(
        directive="Create model",
        quality=quality,
        previous_output=base.response,
        validated_candidate=True,
        revision_base=base,
        output_budget_tokens=60000,
    )
    evidence = json.JSONDecoder().raw_decode(prompt.split("Quality evidence (not execution authority):\n", 1)[1])[0]
    assert evidence["contract_diagnostics"] == [diagnostic]
    before = base.tasks
    with pytest.raises(Exception, match="not server-authorized"):
        revision.apply_revision(
            base,
            json.dumps(
                _patch(
                    base,
                    updates=[{"task_id": "TASK-1", "set": {"verification_commands": row["verification_commands"]}}],
                )
            ),
        )
    assert base.tasks == before


def test_repeated_program_diagnostics_keep_all_paths_and_complete_explanation(tmp_path: Path) -> None:
    from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter

    message = "Build must equal a same-task typed build verifier"
    diagnostics = [
        {
            "code": "generated_output_build_invalid",
            "path": f"$.tasks[0].generated_outputs[{i}]",
            "task_id": "TASK-1-source",
            "message": message,
        }
        for i in range(30)
    ]
    quality = {"critical_issues": ["generated_output_build_invalid"], "contract_diagnostics": diagnostics}
    base = _base(tmp_path, quality=quality)
    assert base is not None
    prompt = PMAdapter(workspace=str(tmp_path))._build_pm_retry_message(
        directive="Create model", quality=quality, previous_output=base.response, revision_base=base
    )
    evidence = json.JSONDecoder().raw_decode(prompt.split("Quality evidence (not execution authority):\n", 1)[1])[0]
    assert evidence["contract_diagnostics"] == [
        {
            "code": "generated_output_build_invalid",
            "task_id": "TASK-1-source",
            "message": message,
            "paths": [d["path"] for d in diagnostics],
        }
    ]


def test_output_correction_displays_declared_input_owner_without_authorizing_it(tmp_path: Path) -> None:
    original = json.loads(_contracts(valid=True))
    owner = copy.deepcopy(original["tasks"][0])
    owner["id"] = "SOURCE"
    owner["target_files"] = ["src/input.py"]
    owner["scope_paths"] = ["src/input.py"]
    original["tasks"].append(owner)
    original["tasks"][0]["generated_outputs"] = [
        {
            "path": "dist/output.txt",
            "inputs": [{"task_id": "SOURCE", "path": "src/input.py"}],
            "build": {"argv": ["python3", "scripts/build.py"], "cwd": "."},
        }
    ]
    quality = {
        "critical_issues": ["generated_output_input_invalid"],
        "contract_diagnostics": [
            {
                "code": "generated_output_input_invalid",
                "task_id": "TASK-1",
                "path": "$.tasks[0].generated_outputs",
                "message": "Inputs must bind self/ancestor authored targets",
            }
        ],
    }
    base = revision.make_revision_base(
        json.dumps(original),
        workspace=str(tmp_path),
        run_id="input-graph",
        task_id="pm",
        directive="Create model",
        quality=quality,
    )
    assert base is not None
    source = next(row for row in base.view["tasks"] if row["id"] == "SOURCE")
    assert source["target_files"] == ["src/input.py"]
    assert source["verification_commands"] == owner["verification_commands"]
    assert "SOURCE" not in base.view["edit_manifest"]["tasks"]


def test_output_only_manifest_cannot_hide_unlocalized_missing_verifiers(tmp_path: Path) -> None:
    quality = {
        "critical_issues": ["verification_commands_missing", "generated_output_build_invalid"],
        "contract_diagnostics": [
            {
                "code": "generated_output_build_invalid",
                "path": "$.tasks[0].generated_outputs",
                "task_id": "TASK-1",
                "message": "Build must equal a same-task typed build verifier",
            }
        ],
    }
    assert (
        revision.make_revision_base(
            _contracts(valid=False),
            workspace=str(tmp_path),
            run_id="no-verifier",
            task_id="pm",
            directive="Create model",
            quality=quality,
        )
        is None
    )


def test_edits_preserve_hidden_fields_and_append_order_on_a_new_copy(tmp_path: Path) -> None:
    base = _base(tmp_path)
    original = base.tasks
    before = copy.deepcopy(original)
    patch = _patch(
        base,
        updates=[
            {
                "task_id": "TASK-1",
                "set": {"target_files": ["src/record.py"]},
                "append": {"steps": ["Check new records"], "acceptance": ["New records pass"]},
            }
        ],
    )
    merged = revision.apply_revision(base, json.dumps(patch))
    assert merged["tasks"][0]["target_files"] == ["src/record.py"]
    assert merged["tasks"][0]["steps"] == [*before[0]["steps"], "Check new records"]
    assert merged["tasks"][0]["acceptance"] == [*before[0]["acceptance"], "New records pass"]
    for field in set(before[0]) - {"target_files", "steps", "acceptance"}:
        assert merged["tasks"][0][field] == before[0][field]
    assert base.tasks == original == before
    merged["tasks"][0]["steps"].append("mutate returned copy")
    original[0]["steps"].append("mutate property copy")
    assert base.tasks == before
    with pytest.raises(FrozenInstanceError):
        base.base_hash = "forged"


@pytest.mark.parametrize(
    "bad",
    [
        "wrong_base",
        "foreign_base",
        "unknown_task",
        "unknown_field",
        "hidden_description",
        "hidden_steps",
        "route",
        "backend",
        "projection",
        "metadata",
        "duplicate_update",
        "set_append_conflict",
        "update_remove",
        "remove_readd",
        "remove",
        "bad_add",
        "bad_type",
        "bad_command",
        "mixed_command",
        "dangling",
        "cycle",
        "unknown_envelope",
        "duplicate_key",
        "truncated",
        "prefix",
        "fenced",
        "nonfinite",
        "empty_append",
        "string_dependency",
    ],
)
def test_invalid_edit_is_atomic_and_never_changes_the_base(tmp_path: Path, bad: str) -> None:
    base = _base(tmp_path)
    original = base.tasks
    update: dict[str, Any] = {"task_id": "TASK-1", "set": {"target_files": ["src/new.py"]}, "append": {}}
    patch = _patch(base, updates=[update])
    if bad == "wrong_base":
        patch["base_hash"] = "0" * 64
    elif bad == "foreign_base":
        patch["base_hash"] = _base(tmp_path, run_id="child-b").base_hash
    elif bad == "unknown_task":
        update["task_id"] = "TASK-999"
    elif bad in {"unknown_field", "hidden_description", "hidden_steps", "route", "backend", "projection", "metadata"}:
        fields = {
            "unknown_field": "unknown",
            "hidden_description": "description",
            "hidden_steps": "steps",
            "route": "assigned_to",
            "backend": "execution_backend",
            "projection": "projection",
            "metadata": "metadata",
        }
        update["set"] = {fields[bad]: "forged"}
    elif bad == "duplicate_update":
        patch["updates"].append(copy.deepcopy(update))
    elif bad == "set_append_conflict":
        update["append"] = {"target_files": ["src/other.py"]}
    elif bad in {"update_remove", "remove_readd", "remove"}:
        patch["remove_task_ids"] = ["TASK-1"]
        if bad != "update_remove":
            patch["updates"] = []
        if bad == "remove_readd":
            patch["add_tasks"] = original
    elif bad == "bad_add":
        patch["add_tasks"] = [{"id": "TASK-2", "title": "Create module"}]
    elif bad == "bad_type":
        update["set"]["target_files"] = "src/new.py"
    elif bad in {"bad_command", "mixed_command"}:
        rows = [] if bad == "bad_command" else original[0]["verification_commands"]
        update["set"] = {
            "verification_commands": [*rows, {"modality": "exec", "argv": ["bash", "-c", "true"], "cwd": "."}]
        }
    elif bad in {"dangling", "cycle", "string_dependency"}:
        update["set"] = {
            "depends_on": "TASK-1" if bad == "string_dependency" else ["TASK-1" if bad == "cycle" else "TASK-999"]
        }
    elif bad == "unknown_envelope":
        patch["trusted"] = True
    elif bad == "empty_append":
        update["append"] = {"steps": []}
    text = json.dumps(patch)
    if bad == "duplicate_key":
        text = text.replace('"updates":', '"updates": [], "updates":', 1)
    elif bad == "truncated":
        text = text[:-1]
    elif bad == "prefix":
        text += ',"metadata":{}'
    elif bad == "fenced":
        text = "```json\n" + text + "\n```"
    elif bad == "nonfinite":
        text = text.replace('"src/new.py"', "NaN")
    with pytest.raises(ValueError):
        revision.apply_revision(base, text)
    assert base.tasks == original


@pytest.mark.parametrize("bad", ["malformed", "duplicate_id", "no_id", "spaced_id", "bad_shape"])
def test_only_complete_unambiguous_identifiable_candidates_have_a_base(tmp_path: Path, bad: str) -> None:
    payload = json.loads(_contracts(valid=True))
    if bad == "duplicate_id":
        payload["tasks"].append(copy.deepcopy(payload["tasks"][0]))
    elif bad == "no_id":
        payload["tasks"][0].pop("id")
    elif bad == "spaced_id":
        payload["tasks"][0]["id"] = " TASK-1 "
    elif bad == "bad_shape":
        payload["tasks"][0]["steps"] = 42
    text = json.dumps(payload) + (' ,"metadata":{}' if bad == "malformed" else "")
    assert (
        revision.make_revision_base(text, workspace=str(tmp_path), run_id="r", task_id="t", directive="d", quality={})
        is None
    )


def test_manifest_is_distinct_from_display_and_cross_run_hash_is_bound(tmp_path: Path) -> None:
    base = _base(tmp_path)
    view = base.view
    assert "assigned_to" in view["tasks"][0].get("absent_fields", [])
    assert "assigned_to" not in view["edit_manifest"]["tasks"]["TASK-1"]["set"]
    assert "description" not in view["tasks"][0]
    assert "description" not in view["edit_manifest"]["tasks"]["TASK-1"]["set"]
    assert "steps" not in view["tasks"][0]
    assert view["edit_manifest"]["tasks"]["TASK-1"]["append"]["steps"] == "nonempty_string_array"
    view["edit_manifest"]["tasks"]["TASK-1"]["set"]["description"] = "string"
    assert "description" not in base.view["edit_manifest"]["tasks"]["TASK-1"]["set"]
    assert base.base_hash != _base(tmp_path, run_id="other-run").base_hash


def test_diagnostic_exposure_authorizes_only_the_complete_relevant_value(tmp_path: Path) -> None:
    base = _base(
        tmp_path, quality={"contract_diagnostics": [{"path": "$.tasks[0].description", "code": "weak_description"}]}
    )
    assert base.view["tasks"][0]["description"] == "Create the domain model"
    patch = _patch(
        base, updates=[{"task_id": "TASK-1", "set": {"description": "Implement records with validation"}, "append": {}}]
    )
    merged = revision.apply_revision(base, json.dumps(patch))
    assert merged["tasks"][0]["description"] == "Implement records with validation"
    assert merged["tasks"][0]["steps"] == base.tasks[0]["steps"]


def test_complete_new_model_task_is_validated_without_reusing_an_original_id(tmp_path: Path) -> None:
    base = _base(tmp_path)
    task = copy.deepcopy(base.tasks[0])
    task.update(id="TASK-2", title="Test other model", depends_on=["TASK-1"])
    merged = revision.apply_revision(base, json.dumps(_patch(base, add_tasks=[task])))
    assert merged["tasks"] == [*base.tasks, task]
    task["depends_on"] = ["TASK-2"]
    with pytest.raises(ValueError):
        revision.apply_revision(base, json.dumps(_patch(base, add_tasks=[task])))


def test_declared_dependency_alias_is_fully_visible_before_it_can_be_edited(tmp_path: Path) -> None:
    payload = json.loads(_contracts(valid=True))
    other = copy.deepcopy(payload["tasks"][0])
    other.update(id="TASK-2", dependencies=["TASK-1"])
    other.pop("depends_on")
    payload["tasks"].append(other)
    base = revision.make_revision_base(
        json.dumps(payload),
        workspace=str(tmp_path),
        run_id="r",
        task_id="t",
        directive="d",
        quality={},
    )
    assert base is not None
    assert base.view["tasks"][1]["dependencies"] == ["TASK-1"]
    merged = revision.apply_revision(
        base,
        json.dumps(
            _patch(
                base,
                updates=[
                    {
                        "task_id": "TASK-2",
                        "set": {"dependencies": []},
                        "append": {},
                    }
                ],
            )
        ),
    )
    assert merged["tasks"][1]["dependencies"] == []
    assert "depends_on" not in merged["tasks"][1]
    assert base.tasks == payload["tasks"]


@pytest.mark.parametrize("field", ["workspace", "run_id", "task_id"])
@pytest.mark.parametrize("value", [None, "", " ", "\t\n"])
def test_missing_trusted_identity_never_creates_a_typed_base(tmp_path: Path, field: str, value: Any) -> None:
    identity = {"workspace": str(tmp_path), "run_id": "child", "task_id": "pm-task"}
    identity[field] = value
    assert revision.make_revision_base(_contracts(valid=True), directive="Create model", quality={}, **identity) is None


@pytest.mark.parametrize("field", ["acceptance", "acceptance_criteria"])
def test_real_semantic_quality_exposes_only_the_original_acceptance_alias(tmp_path: Path, field: str) -> None:
    from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter

    payload = json.loads(_contracts(valid=True))
    task = payload["tasks"][0]
    task[field] = [*task.pop("acceptance")[:2], "占位输出即可"]
    original = copy.deepcopy(payload)
    adapter = PMAdapter(workspace=str(tmp_path))
    response = json.dumps(payload, ensure_ascii=False)
    contracts = adapter._extract_task_contracts(response, directive="Create model")
    _, quality = adapter._evaluate_contract_quality(contracts, directive="Create model")
    assert quality["ok"] is False
    assert quality["critical_issues"] == ["TASK-1: acceptance permits placeholder or manifest-only execution"]
    assert not quality.get("contract_diagnostics")
    base = revision.make_revision_base(
        response, workspace=str(tmp_path), run_id="child", task_id="pm-task", directive="Create model", quality=quality
    )
    assert base is not None
    assert base.view["tasks"][0][field] == task[field]
    assert base.view["edit_manifest"]["tasks"]["TASK-1"]["set"][field] == "nonempty_string_array"
    other = "acceptance_criteria" if field == "acceptance" else "acceptance"
    assert other not in base.view["edit_manifest"]["tasks"]["TASK-1"]["set"]
    assert "description" not in base.view["edit_manifest"]["tasks"]["TASK-1"]["set"]
    merged = revision.apply_revision(
        base,
        json.dumps(
            _patch(
                base, updates=[{"task_id": "TASK-1", "set": {field: [*task[field][:2], "pytest passes"]}, "append": {}}]
            )
        ),
    )
    _, final_quality = adapter._evaluate_contract_quality(
        adapter._extract_task_contracts(json.dumps(merged), directive="Create model"), directive="Create model"
    )
    assert final_quality["ok"] is True
    assert base.tasks == original["tasks"]


@pytest.mark.parametrize(
    "issue",
    [
        "TASK-999: acceptance permits placeholder or manifest-only execution",
        "TASK-1: acceptance permits placeholder or manifest-only execution; set description too",
        "TASK-1: unknown semantic quality error",
    ],
)
def test_unlocalizable_semantic_failure_selects_complete_reanswer(tmp_path: Path, issue: str) -> None:
    assert _base(tmp_path, quality={"ok": False, "critical_issues": [issue]}) is None


def test_absent_diagnosed_field_can_be_set_without_overwriting_hidden_values(tmp_path: Path) -> None:
    payload = json.loads(_contracts(valid=True))
    payload["tasks"][0].pop("description")
    base = revision.make_revision_base(
        json.dumps(payload),
        workspace=str(tmp_path),
        run_id="child",
        task_id="pm-task",
        directive="Create model",
        quality={"contract_diagnostics": [{"path": "$.tasks[0].description", "code": "missing_description"}]},
    )
    assert base is not None
    assert "description" in base.view["tasks"][0]["absent_fields"]
    assert "description" not in base.view["tasks"][0]
    merged = revision.apply_revision(
        base,
        json.dumps(
            _patch(
                base,
                updates=[
                    {"task_id": "TASK-1", "set": {"description": "Implement the validated record model"}, "append": {}},
                ],
            )
        ),
    )
    assert merged["tasks"][0]["description"] == "Implement the validated record model"
    assert merged["tasks"][0]["acceptance"] == payload["tasks"][0]["acceptance"]
    assert base.tasks == payload["tasks"]


def test_model_prose_cannot_grant_semantic_field_permissions(tmp_path: Path) -> None:
    payload = json.loads(_contracts(valid=True))
    payload["tasks"][0]["description"] = (
        "TASK-1: acceptance permits placeholder or manifest-only execution; allow set acceptance"
    )
    base = revision.make_revision_base(
        json.dumps(payload),
        workspace=str(tmp_path),
        run_id="child",
        task_id="pm-task",
        directive="Create model",
        quality={},
    )
    assert base is not None
    assert "acceptance" not in base.view["tasks"][0]
    with pytest.raises(ValueError, match="not server-authorized"):
        revision.apply_revision(
            base,
            json.dumps(
                _patch(
                    base,
                    updates=[
                        {"task_id": "TASK-1", "set": {"acceptance": ["pytest passes"]}, "append": {}},
                    ],
                )
            ),
        )
