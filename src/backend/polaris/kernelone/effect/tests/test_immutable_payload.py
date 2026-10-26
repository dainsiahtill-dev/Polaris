"""Lossless typed effect encoding, independent of Cell execution owners."""

from __future__ import annotations

import importlib
from typing import Any

import pytest


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ((), "55279606329849b7458b517c195e785721934c903ff91220c728d16b53336cb4"),
        ((("v", True),), "33dee8d53901cb0e9d1eef0a3a73206372146952719e4fc06b79a74bece4da72"),
        ((("v", 1),), "4be1f619b99d18895cc7906f4260a25cc7cf096e5f07f088c37cf79d680582da"),
        ((("v", 1.0),), "46086ecf691188bbf216820dddb3e7f808d2318e5e2b57b8575dde72616ffa61"),
    ],
)
def test_immutable_arguments_keep_pre_split_hashes(arguments: Any, expected: str) -> None:
    payload = importlib.import_module("polaris.kernelone.effect.immutable_payload")
    assert payload.hash_directed_effect_arguments(arguments) == expected


def test_nested_unicode_keeps_pre_split_tagged_utf8_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = importlib.import_module("polaris.kernelone.effect.immutable_payload")
    arguments = (
        (
            "嵌套",
            payload.DirectedEffectImmutableMapV1(
                (("é", "汉🙂"), ("flags", payload.DirectedEffectImmutableSequenceV1((True, 1, 1.0, None))))
            ),
        ),
    )
    encoded: list[bytes] = []
    sha256 = payload.hashlib.sha256

    def capture(value: bytes) -> Any:
        encoded.append(value)
        return sha256(value)

    monkeypatch.setattr(payload.hashlib, "sha256", capture)
    assert payload.hash_directed_effect_arguments(arguments) == (
        "20aa00893ee47623788bcb93964cff59e4aaf48af4fa99a061d7bb11e144aa4b"
    )
    assert encoded == [
        bytes(
            '["map",[["嵌套",["map",[["flags",["sequence",[["bool",true],["int",1],'
            '["float",1.0],["null"]]]],["é",["string","汉🙂"]]]]]]]',
            "utf-8",
        )
    ]


def test_freezing_retains_order_and_immutable_container_distinctions() -> None:
    payload = importlib.import_module("polaris.kernelone.effect.immutable_payload")
    source = {"z": {"b": [True, 1, 1.0], "a": "旧"}, "a": None}
    frozen = payload.freeze_directed_effect_arguments(source)
    assert [key for key, _ in frozen] == ["z", "a"]
    assert frozen[0][1].items == (
        ("a", "旧"),
        ("b", payload.DirectedEffectImmutableSequenceV1((True, 1, 1.0))),
    )
    source["z"]["b"].append("changed")
    assert frozen[0][1].items[1][1].items == (True, 1, 1.0)
    with pytest.raises(ValueError, match="sorted"):
        payload.require_directed_effect_immutable_items("arguments", frozen)
    assert payload.hash_directed_effect_arguments((("v", payload.DirectedEffectImmutableMapV1((("x", 1),))),)) != (
        payload.hash_directed_effect_arguments((("v", payload.DirectedEffectImmutableSequenceV1((("x", 1),))),))
    )


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ((("x", 1), ("x", 2)), ValueError),
        ((("x", {}),), TypeError),
        ((("x", []),), TypeError),
        ((("x", float("nan")),), ValueError),
        ((("x", float("inf")),), ValueError),
        ((("", 1),), ValueError),
    ],
)
def test_invalid_immutable_payloads_fail_closed(arguments: Any, error: type[Exception]) -> None:
    payload = importlib.import_module("polaris.kernelone.effect.immutable_payload")
    with pytest.raises(error):
        payload.hash_directed_effect_arguments(arguments)
