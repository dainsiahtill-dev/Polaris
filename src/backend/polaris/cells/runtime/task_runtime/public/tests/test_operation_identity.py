"""Inventory identity retains its original digest and strict validation."""

from __future__ import annotations

import importlib
from dataclasses import replace
from typing import Any

import pytest


def _subject() -> Any:
    owner = importlib.import_module("polaris.cells.runtime.task_runtime.public.operation_identity")
    return owner.DirectedEffectOperationSubjectV1(
        workspace="/workspace/旧",
        turn_id="turn",
        batch_id="batch",
        tool_call_id="call",
        inventory_ordinal=0,
        normalized_tool_name="write_file",
        normalized_arguments=(("content", "汉🙂"), ("file", "src/a.txt")),
        effect_type="write",
        execution_mode="write_serial",
        prospective_operation_hash="a" * 64,
    )


def test_subject_digest_ignores_supplied_digest_and_preserves_original_identity() -> None:
    owner = importlib.import_module("polaris.cells.runtime.task_runtime.public.operation_identity")
    subject = _subject()
    expected = "1f1af97c73e0b3894e0979a08cf2172b79be7fabcaabffef619c7e6e50f8d6fc"
    assert owner.hash_directed_effect_operation_subject(subject) == expected
    assert (
        owner.hash_directed_effect_operation_subject(replace(subject, prospective_operation_hash="b" * 64)) == expected
    )
    assert owner.hash_directed_effect_operation_subject(replace(subject, tool_call_id="other")) != expected


def test_director_facades_preserve_class_function_identity_and_serialization_hash() -> None:
    owner = importlib.import_module("polaris.cells.runtime.task_runtime.public.operation_identity")
    payload = importlib.import_module("polaris.kernelone.effect.immutable_payload")
    director = importlib.import_module("polaris.cells.director.runtime.public.directed_effect_contracts")
    policy = importlib.import_module("polaris.cells.director.runtime.public.directed_effect_policy_contracts")
    assert policy.DirectorEffectPolicyOperationSubjectV1 is owner.DirectedEffectOperationSubjectV1
    assert policy.hash_director_effect_policy_operation_subject is owner.hash_directed_effect_operation_subject
    for name in (
        "DirectedEffectImmutableMapV1",
        "DirectedEffectImmutableSequenceV1",
        "DirectedEffectImmutableValueV1",
        "DirectedEffectImmutableItemsV1",
        "hash_directed_effect_arguments",
        "require_directed_effect_immutable_items",
    ):
        assert getattr(director, name) is getattr(payload, name)
    assert director.hash_directed_effect_arguments(policy._operation_subject_items(_subject())) == (
        "c007cb23021e5592bdd6cf3e37694ff424ef634328efb03c27070b49f31bef88"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"inventory_ordinal": True},
        {"inventory_ordinal": -1},
        {"inventory_ordinal": 0.5},
        {"prospective_operation_hash": "A" * 64},
        {"workspace": " "},
        {"normalized_arguments": (("z", 1), ("a", 2))},
        {"effect_type": "read"},
        {"execution_mode": "read_parallel"},
        {"effect_type": "async", "execution_mode": "write_serial"},
    ],
)
def test_subject_rejects_invalid_identity_and_inventory_semantics(change: dict[str, Any]) -> None:
    with pytest.raises((TypeError, ValueError)):
        replace(_subject(), **change)


def test_subject_hash_rejects_lookalike_object() -> None:
    owner = importlib.import_module("polaris.cells.runtime.task_runtime.public.operation_identity")
    with pytest.raises(TypeError, match="exactly"):
        owner.hash_directed_effect_operation_subject(object())
