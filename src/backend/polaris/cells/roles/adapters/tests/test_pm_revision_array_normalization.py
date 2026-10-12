"""Authorized array revisions compose set then append without widening manifests."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.orchestration.pm_planning.public.artifact_contracts import validate_pm_verification_commands
from polaris.cells.roles.adapters.internal.pm import revision
from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter
from polaris.cells.roles.adapters.tests.test_pm_revision_document_verifiers import _candidate


def _base(tmp_path: Path) -> revision.PMRevisionBase:
    base = revision.make_revision_base(
        json.dumps(_candidate()),
        workspace=str(tmp_path),
        run_id="pm-array-normalization",
        task_id="pm-task",
        directive="Create a TypeScript CLI",
        quality={"critical_issues": ["TASK-DOC: acceptance requires executable command or file evidence"]},
    )
    assert base is not None
    return base


def _patch(base: revision.PMRevisionBase, update: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "pm.task_revision.v1",
        "base_hash": base.base_hash,
        "updates": [update],
        "add_tasks": [],
        "remove_task_ids": [],
    }


def _array_update() -> dict[str, Any]:
    return {
        "task_id": "TASK-DOC",
        "set": {"acceptance": ["BLUEPRINT.md declares module interfaces and npm build/test commands"]},
        "append": {"acceptance": ["BLUEPRINT.md lists the CLI entrypoint and malformed-record test cases"]},
    }


def test_authorized_array_set_then_append_preserves_unedited_tasks_and_verifiers(tmp_path: Path) -> None:
    base = _base(tmp_path)
    before = base.tasks
    patch = _patch(base, _array_update())
    original_patch = copy.deepcopy(patch)
    raw_response = json.dumps(patch)
    merged = revision.apply_revision(base, raw_response)
    assert merged["tasks"][0]["acceptance"] == [
        "BLUEPRINT.md declares module interfaces and npm build/test commands",
        "BLUEPRINT.md lists the CLI entrypoint and malformed-record test cases",
    ]
    assert before[0]["acceptance"] == ["BLUEPRINT.md records module paths, CLI entrypoint and test commands"]
    assert merged["tasks"][1:] == before[1:]
    for actual, original in zip(merged["tasks"], before, strict=True):
        assert actual["verification_commands"] == original["verification_commands"]
        rows, diagnostics = validate_pm_verification_commands(actual["verification_commands"])
        assert rows == original["verification_commands"]
        assert not diagnostics
    assert base.tasks == before
    assert patch == original_patch
    normalized, quality = PMAdapter(workspace=str(tmp_path))._evaluate_contract_quality(
        merged["tasks"], directive="Create a TypeScript CLI"
    )
    assert quality["ok"] is True
    assert quality["score"] >= 80
    derivation = revision.revision_derivation(
        base, raw_response, raw_merged_candidate=merged, normalized_plan=normalized, quality=quality
    )
    assert derivation["original_response"] == base.response
    assert derivation["original_response_hash"] == revision.content_hash(base.response)
    assert derivation["revision_response"] == raw_response
    assert derivation["revision_response_hash"] == revision.content_hash(raw_response)
    assert derivation["revision"] == original_patch
    assert derivation["raw_merged_candidate"] == merged
    assert derivation["merged_candidate_is_original_provider_response"] is False


@pytest.mark.parametrize(
    "invalid",
    [
        "append_denied",
        "set_denied",
        "unknown_field",
        "stale_base",
        "duplicate_task",
        "scalar",
        "bad_set_array",
        "bad_append_array",
        "unsafe_scope",
        "dependency_cycle",
        "nonproof_verifier",
    ],
)
def test_array_composition_never_normalizes_unauthorized_or_invalid_edits(tmp_path: Path, invalid: str) -> None:
    base = _base(tmp_path)
    before = base.tasks
    update = _array_update()
    patch = _patch(base, update)
    if invalid == "append_denied":
        update["set"]["target_files"] = ["BLUEPRINT.md"]
        update["append"]["target_files"] = ["src/unowned.ts"]
    elif invalid == "set_denied":
        update["task_id"] = "TASK-QA"
    elif invalid == "unknown_field":
        update["set"]["unknown"] = ["x"]
        update["append"]["unknown"] = ["y"]
    elif invalid == "stale_base":
        patch["base_hash"] = "0" * 64
    elif invalid == "duplicate_task":
        patch["updates"].append(copy.deepcopy(update))
    elif invalid == "scalar":
        update["set"]["title"] = "Replacement title"
        update["append"]["title"] = "Appended title"
    elif invalid == "bad_set_array":
        update["set"]["acceptance"] = []
    elif invalid == "bad_append_array":
        update["append"]["acceptance"] = [None]
    elif invalid == "unsafe_scope":
        update["set"]["scope_paths"] = ["../outside"]
    elif invalid == "dependency_cycle":
        update["set"]["depends_on"] = ["TASK-IMPLEMENT"]
    elif invalid == "nonproof_verifier":
        update["set"]["verification_commands"] = [{"modality": "test", "argv": ["node", "--version"], "cwd": "."}]
    with pytest.raises(ValueError):
        revision.apply_revision(base, json.dumps(patch))
    assert base.tasks == before


def test_overlapping_scalar_is_rejected_even_if_manifest_has_both_operations(tmp_path: Path) -> None:
    base = _base(tmp_path)
    view = base.view
    view["edit_manifest"]["tasks"]["TASK-DOC"]["append"]["title"] = "nonempty_string"
    malformed_manifest_base = replace(base, view_json=revision.canonical_json(view))
    update = {"task_id": "TASK-DOC", "set": {"title": "New title"}, "append": {"title": "Additional title"}}
    with pytest.raises(ValueError):
        revision.apply_revision(malformed_manifest_base, json.dumps(_patch(malformed_manifest_base, update)))
    assert malformed_manifest_base.tasks == base.tasks


def test_array_composition_still_requires_original_quality_gate(tmp_path: Path) -> None:
    base = _base(tmp_path)
    patch = _patch(base, _array_update())
    patch["updates"].extend(
        {"task_id": task_id, "set": {"verification_commands": []}} for task_id in ("TASK-IMPLEMENT", "TASK-QA")
    )
    merged = revision.apply_revision(base, json.dumps(patch))
    _, quality = PMAdapter(workspace=str(tmp_path))._evaluate_contract_quality(
        merged["tasks"], directive="Create a TypeScript CLI"
    )
    assert quality["ok"] is False
    assert "verification_commands_missing" in quality["critical_issues"]
