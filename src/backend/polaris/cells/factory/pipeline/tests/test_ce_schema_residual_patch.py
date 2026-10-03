"""Pure draft repair tests: exact diagnostics, immutable siblings, no authority."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal.ce_schema_residual_patch import plan_ce_schema_residual_patch

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "stable": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {name: {"type": "string"} for name in ("given", "when", "then")},
                "required": ["given", "when", "then"],
            },
        },
    },
    "required": ["stable", "steps"],
}
BASE: dict[str, Any] = {"stable": "KEEP", "steps": [{"given": "A", "then": "B"}], "extra": {"note": "draft"}}


def _patch() -> dict[str, Any]:
    # Hash uses the independently specified canonical encoding, not the planner.
    base_hash = hashlib.sha256(
        json.dumps(BASE, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "base_candidate_hash": base_hash,
        "edits": [
            {"operation": "remove", "path": ["extra"]},
            {"operation": "add", "path": ["steps", 0, "when"], "value": "C"},
        ],
    }


def test_mixed_error_patch_handles_existing_array_element_without_replacing_siblings() -> None:
    plan = plan_ce_schema_residual_patch(candidate=BASE, schema=SCHEMA)
    assert plan is not None
    assert plan.provider_context()["required_edits"] == [
        {"operation": "remove", "path": ["extra"]},
        {"operation": "add", "path": ["steps", 0, "when"]},
    ]
    assert plan.compose(_patch()) == {"stable": "KEEP", "steps": [{"given": "A", "when": "C", "then": "B"}]}
    assert BASE == {"stable": "KEEP", "steps": [{"given": "A", "then": "B"}], "extra": {"note": "draft"}}


@pytest.mark.parametrize("attack", ["hash", "path", "overwrite", "reorder", "missing", "extra", "value"])
def test_forged_or_incomplete_patch_never_composes(attack: str) -> None:
    plan = plan_ce_schema_residual_patch(candidate=BASE, schema=SCHEMA)
    assert plan is not None
    patch = _patch()
    if attack == "hash":
        patch["base_candidate_hash"] = "0" * 64
    elif attack == "path":
        patch["edits"][1]["path"] = ["steps", 1, "when"]
    elif attack == "overwrite":
        patch["edits"][1]["path"] = ["stable"]
    elif attack == "reorder":
        patch["edits"].reverse()
    elif attack == "missing":
        patch["edits"].pop()
    elif attack == "extra":
        patch["edits"].append({"operation": "remove", "path": ["stable"]})
    else:
        patch["edits"][1]["value"] = 5
    with pytest.raises(ValueError, match="ce_schema_patch_payload_rejected"):
        plan.compose(patch)


def test_caller_mutation_does_not_change_frozen_draft_or_edit_schema() -> None:
    base = deepcopy(BASE)
    schema = deepcopy(SCHEMA)
    plan = plan_ce_schema_residual_patch(candidate=base, schema=schema)
    assert plan is not None
    base["stable"] = "changed"
    schema["properties"]["stable"]["type"] = "number"
    exposed = plan.provider_context()
    exposed["candidate"]["stable"] = "injected"
    exposed["required_edits"][1]["path"] = ["stable"]
    returned_schema = plan.patch_schema
    returned_schema["properties"]["edits"]["prefixItems"] = []
    assert plan.compose(_patch())["stable"] == "KEEP"
    assert plan.provider_context()["execution_authority"] is False


def test_type_errors_remain_strict_reconstruction() -> None:
    invalid = deepcopy(BASE)
    invalid["stable"] = 5
    assert plan_ce_schema_residual_patch(candidate=invalid, schema=SCHEMA) is None


def test_ambiguous_pattern_properties_remain_strict_reconstruction() -> None:
    schema = deepcopy(SCHEMA)
    schema["patternProperties"] = {"^known_": {"type": "string"}}
    assert plan_ce_schema_residual_patch(candidate=BASE, schema=schema) is None


def test_large_missing_member_set_does_not_create_unbounded_patch() -> None:
    names = [f"field_{index}" for index in range(17)]
    schema = {"type": "object", "properties": {name: {"type": "string"} for name in names}, "required": names}
    assert plan_ce_schema_residual_patch(candidate={}, schema=schema) is None


def test_referenced_value_schema_is_not_rebased_into_a_different_schema_root() -> None:
    schema = {
        "type": "object",
        "$defs": {"value": {"type": "string"}},
        "properties": {"missing": {"$ref": "#/$defs/value"}},
        "required": ["missing"],
    }
    assert plan_ce_schema_residual_patch(candidate={}, schema=schema) is None


def test_invalid_schema_is_a_stable_contract_error_not_attribute_error() -> None:
    with pytest.raises(ValueError, match="ce_schema_patch_schema_invalid"):
        plan_ce_schema_residual_patch(candidate={}, schema={"type": "object", "properties": []})


def test_composition_revalidates_constraints_beyond_individual_added_values() -> None:
    base = {"stable": "KEEP", "extra": "draft"}
    schema = {
        "type": "object",
        "properties": {"stable": {"type": "string"}},
        "required": ["stable"],
        "additionalProperties": False,
        "minProperties": 2,
    }
    plan = plan_ce_schema_residual_patch(candidate=base, schema=schema)
    assert plan is not None
    base_hash = hashlib.sha256(
        json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    with pytest.raises(ValueError, match="ce_schema_patch_full_schema_rejected"):
        plan.compose({"base_candidate_hash": base_hash, "edits": [{"operation": "remove", "path": ["extra"]}]})
