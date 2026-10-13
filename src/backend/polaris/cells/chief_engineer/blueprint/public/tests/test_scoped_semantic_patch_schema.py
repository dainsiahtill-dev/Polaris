"""Diagnosis-scoped CE patches omit unrelated operation containers without new authority."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from polaris.cells.chief_engineer.blueprint.public import (
    ChiefEngineerSemanticRepairDiagnosisV1,
    ChiefEngineerSemanticRepairPatchV1,
    build_chief_engineer_semantic_repair_patch_schema,
    compose_chief_engineer_semantic_repair,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import ChiefEngineerSemanticRepairOperationV1
from polaris.cells.chief_engineer.blueprint.public.tests.test_semantic_repair import _candidate, _tasks

_ANCHORS = {"base_candidate_hash": "a" * 64, "diagnosis_hash": "b" * 64}
_BEHAVIOR = {
    "invariant_id": "INV-STABLE",
    "statement": "A valid input preserves bounded output.",
    "owner_task_id": "TASK-1",
    "consumer_task_ids": ["TASK-2"],
    "covered_obligation_ids": ["artifact-main"],
    "verification_examples": [{"given": "valid input", "when": "compute", "then": "bounded output"}],
}


def _behavior_payload() -> dict[str, Any]:
    return {**_ANCHORS, "behavior_invariant_upserts": [deepcopy(_BEHAVIOR)]}


def _legacy_payload() -> dict[str, Any]:
    return {
        **_behavior_payload(),
        "artifact_upserts": [],
        "entrypoint_upserts": [],
        "task_behavior_ref_replacements": {},
    }


@pytest.mark.parametrize(
    "operation,fields",
    [
        ("artifact_upsert", {"artifact_upserts"}),
        ("entrypoint_upsert", {"entrypoint_upserts", "entrypoint_remove_obligation_ids"}),
        ("behavior_invariant_upsert", {"behavior_invariant_upserts"}),
        ("task_behavior_ref_replace", {"task_behavior_ref_replacements"}),
    ],
)
def test_scoped_wire_advertises_only_anchors_and_authorized_groups(
    operation: ChiefEngineerSemanticRepairOperationV1,
    fields: set[str],
) -> None:
    schema = build_chief_engineer_semantic_repair_patch_schema(allowed_operations=(operation,))
    assert set(schema["properties"]) == set(_ANCHORS) | fields
    assert set(schema["required"]) == set(_ANCHORS)
    assert schema["additionalProperties"] is False


def test_scoped_behavior_patch_omits_unchanged_containers() -> None:
    operations: tuple[ChiefEngineerSemanticRepairOperationV1, ...] = (
        "behavior_invariant_upsert",
        "task_behavior_ref_replace",
    )
    payload = _behavior_payload()
    original = deepcopy(payload)
    schema = build_chief_engineer_semantic_repair_patch_schema(allowed_operations=operations)
    Draft202012Validator(schema).validate(payload)
    patch = ChiefEngineerSemanticRepairPatchV1.from_provider_dict(payload, allowed_operations=operations)

    assert patch.operations == ("behavior_invariant_upsert",)
    assert patch.artifact_upserts == ()
    assert patch.entrypoint_upserts == ()
    assert patch.entrypoint_remove_obligation_ids == ()
    assert patch.task_behavior_ref_replacements == {}
    assert patch.behavior_invariant_upserts[0].invariant_id == "INV-STABLE"
    assert patch.base_candidate_hash == _ANCHORS["base_candidate_hash"]
    assert patch.diagnosis_hash == _ANCHORS["diagnosis_hash"]
    assert payload == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("behavior_invariant_upserts", ""),
        ("behavior_invariant_upserts", ["wrong item"]),
        ("task_behavior_ref_replacements", ""),
        ("task_behavior_ref_replacements", {"TASK-1": "wrong refs"}),
    ],
)
def test_present_enabled_groups_remain_strict(field: str, value: Any) -> None:
    payload = _legacy_payload()
    payload[field] = value
    operations: tuple[ChiefEngineerSemanticRepairOperationV1, ...] = (
        "behavior_invariant_upsert",
        "task_behavior_ref_replace",
    )
    with pytest.raises((TypeError, ValueError)):
        ChiefEngineerSemanticRepairPatchV1.from_provider_dict(payload, allowed_operations=operations)
    scoped = {key: val for key, val in payload.items() if key not in {"artifact_upserts", "entrypoint_upserts"}}
    with pytest.raises(ValidationError):
        Draft202012Validator(build_chief_engineer_semantic_repair_patch_schema(allowed_operations=operations)).validate(
            scoped
        )


@pytest.mark.parametrize("defect", ["unknown_key", "bad_base", "missing_diagnosis", "no_operation"])
def test_optional_containers_do_not_weaken_anchors_or_operation_requirement(defect: str) -> None:
    payload = _behavior_payload()
    if defect == "unknown_key":
        payload["freeform"] = "forbidden"
    elif defect == "bad_base":
        payload["base_candidate_hash"] = "not-a-hash"
    elif defect == "missing_diagnosis":
        del payload["diagnosis_hash"]
    else:
        del payload["behavior_invariant_upserts"]
    with pytest.raises((TypeError, ValueError)):
        ChiefEngineerSemanticRepairPatchV1.from_provider_dict(
            payload, allowed_operations=("behavior_invariant_upsert",)
        )


def test_known_disabled_groups_are_only_dropped_by_dto_compatibility() -> None:
    payload = _legacy_payload()
    payload["artifact_upserts"] = {"untrusted": "unused"}
    payload["entrypoint_upserts"] = [{"command": ""}]
    payload["entrypoint_remove_obligation_ids"] = {"task_behavior_ref_replacements": ""}
    original = deepcopy(payload)
    patch = ChiefEngineerSemanticRepairPatchV1.from_provider_dict(
        payload, allowed_operations=("behavior_invariant_upsert",)
    )

    assert patch.operations == ("behavior_invariant_upsert",)
    assert patch.artifact_upserts == ()
    assert patch.entrypoint_upserts == ()
    assert patch.entrypoint_remove_obligation_ids == ()
    assert payload == original
    # DTO legacy compatibility does not advertise or authorize those keys in a new scoped wire.
    with pytest.raises(ValidationError):
        Draft202012Validator(
            build_chief_engineer_semantic_repair_patch_schema(allowed_operations=("behavior_invariant_upsert",))
        ).validate(payload)


def test_no_scope_keeps_legacy_full_required_wire_and_parser() -> None:
    schema = build_chief_engineer_semantic_repair_patch_schema()
    assert set(schema["required"]) == {
        "base_candidate_hash",
        "diagnosis_hash",
        "artifact_upserts",
        "entrypoint_upserts",
        "behavior_invariant_upserts",
        "task_behavior_ref_replacements",
    }
    assert "entrypoint_remove_obligation_ids" in schema["properties"]
    payload = _legacy_payload()
    Draft202012Validator(schema).validate(payload)
    assert ChiefEngineerSemanticRepairPatchV1.from_provider_dict(payload).operations == ("behavior_invariant_upsert",)
    with pytest.raises(ValueError, match="provider patch fields"):
        ChiefEngineerSemanticRepairPatchV1.from_provider_dict(_behavior_payload())


def test_sparse_scoped_patch_composition_preserves_exact_authority(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("delivery_depth.prod_files",),
        allowed_operations=("artifact_upsert",),
    )
    payload = {
        "base_candidate_hash": candidate.candidate_hash,
        "diagnosis_hash": diagnosis.diagnosis_hash,
        "artifact_upserts": [
            {
                "obligation_id": "artifact-readme",
                "path": "README.md",
                "semantic_role": "docs",
                "applicability": "required",
                "owner_task_id": "TASK-2",
            }
        ],
    }
    original = deepcopy(candidate.candidate)
    Draft202012Validator(
        build_chief_engineer_semantic_repair_patch_schema(allowed_operations=diagnosis.allowed_operations)
    ).validate(payload)
    patch = ChiefEngineerSemanticRepairPatchV1.from_provider_dict(
        payload, allowed_operations=diagnosis.allowed_operations
    )
    repaired, receipt = compose_chief_engineer_semantic_repair(candidate, diagnosis, patch, tasks=_tasks())

    assert candidate.candidate == original
    assert repaired.task_ids == candidate.task_ids
    assert repaired.pm_contract_hash == candidate.pm_contract_hash
    assert repaired.candidate["risk_flags"] == ["preserve-me"]
    assert receipt.before_candidate_hash == candidate.candidate_hash
    assert receipt.diagnosis_hash == diagnosis.diagnosis_hash
    assert set(receipt.unchanged_section_hashes) >= {"verification", "task_behavior_refs", "project_interface_contract"}
    wrong = {**payload, "base_candidate_hash": "c" * 64}
    bad_patch = ChiefEngineerSemanticRepairPatchV1.from_provider_dict(
        wrong, allowed_operations=diagnosis.allowed_operations
    )
    with pytest.raises(ValueError, match="base_candidate_hash"):
        compose_chief_engineer_semantic_repair(candidate, diagnosis, bad_patch, tasks=_tasks())
