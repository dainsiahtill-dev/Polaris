"""Characterization tests for the TS-8 JsonValue typing pass.

These tests pin the concrete behavior of the JSON-coercion helpers BEFORE they
are re-annotated from ``Any`` to the shared ``JsonValue`` type alias, so that
the typing pass can be proven behavior-preserving.

Scope (category (b) "real type-erasure" only):
- ``JsonValue`` / ``JsonObject`` barrel export + structural round-trip.
- ``_sanitize_json`` output shape (canonical JsonValue producer).
- ``pm_contract_store`` JSON helpers round-trip with nested JsonValue payloads.

It does NOT assert on the divergent Protocol-return cluster (IRoleSessionService,
IRoleKernelService.classify_error) which are intentionally left as ``Any``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from polaris.cells.llm.evaluation.internal.tool_calling_matrix._contracts import (
    _sanitize_json,
)
from polaris.cells.runtime.state_owner.internal.pm_contract_store import (
    read_json_safe,
    safe_payload_digest,
    write_json_atomic,
)


def test_jsonvalue_alias_is_exported_from_contracts_barrels() -> None:
    """JsonValue / JsonObject must be re-exported additively through both barrels."""
    from polaris.kernelone.contracts import JsonObject, JsonValue
    from polaris.kernelone.contracts.technical import (
        JsonObject as JsonObjectTechnical,
        JsonValue as JsonValueTechnical,
    )
    from polaris.kernelone.contracts.technical.json_types import (
        JsonObject as JsonObjectModule,
        JsonValue as JsonValueModule,
    )

    # All re-export paths resolve to the same underlying alias object.
    assert JsonValue is JsonValueTechnical is JsonValueModule
    assert JsonObject is JsonObjectTechnical is JsonObjectModule


def test_jsonvalue_covers_the_full_json_value_space() -> None:
    """A representative JSON value round-trips through json.dumps/loads unchanged."""
    value = {
        "none": None,
        "bool": True,
        "int": 7,
        "float": 1.5,
        "str": "hello",
        "list": [1, "two", None, {"nested": [True, False]}],
        "obj": {"k": {"deep": [1, 2, 3]}},
    }
    assert json.loads(json.dumps(value, ensure_ascii=False)) == value


def test_sanitize_json_returns_only_json_primitives_and_containers() -> None:
    """_sanitize_json maps any input onto the canonical JsonValue space."""

    class _Opaque:
        def __str__(self) -> str:
            return "opaque-instance"

    raw = {
        "primitive_none": None,
        "primitive_bool": False,
        "primitive_int": 3,
        "primitive_float": 2.5,
        "primitive_str": "ok",
        "mapping": {"a": 1, 2: "coerced-key"},
        "sequence": (1, 2, 3),
        "set_value": {9},
        "opaque": _Opaque(),
    }

    result = _sanitize_json(raw)

    # Top-level mapping with string-coerced keys.
    assert isinstance(result, dict)
    assert set(result.keys()) == {
        "primitive_none",
        "primitive_bool",
        "primitive_int",
        "primitive_float",
        "primitive_str",
        "mapping",
        "sequence",
        "set_value",
        "opaque",
    }
    assert result["primitive_none"] is None
    assert result["primitive_bool"] is False
    assert result["primitive_int"] == 3
    assert result["primitive_float"] == 2.5
    assert result["primitive_str"] == "ok"
    # Mapping keys are coerced to str.
    assert result["mapping"] == {"a": 1, "2": "coerced-key"}
    # Tuples and sets become lists.
    assert result["sequence"] == [1, 2, 3]
    assert isinstance(result["set_value"], list) and result["set_value"] == [9]
    # Opaque objects fall back to str().
    assert result["opaque"] == "opaque-instance"

    # The sanitized output must itself be JSON-serializable (the whole point).
    json.dumps(result, ensure_ascii=False)


def test_sanitize_json_scalar_passthrough() -> None:
    assert _sanitize_json(None) is None
    assert _sanitize_json(True) is True
    assert _sanitize_json(42) == 42
    assert _sanitize_json(3.14) == 3.14
    assert _sanitize_json("text") == "text"


def test_pm_contract_store_roundtrip_with_nested_jsonvalue_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """write_json_atomic / read_json_safe round-trip a nested JsonValue object."""
    monkeypatch.chdir(tmp_path)
    payload: dict[str, object] = {
        "version": 1,
        "enabled": True,
        "ratio": 0.5,
        "note": None,
        "tasks": [
            {"id": "t-1", "tags": ["a", "b"], "meta": {"depth": [1, [2, [3]]]}},
        ],
    }
    logical_path = "runtime/contracts/ts8_roundtrip.json"

    write_json_atomic(logical_path, payload)
    assert read_json_safe(logical_path) == payload


def test_safe_payload_digest_is_stable_for_equivalent_json_values() -> None:
    """safe_payload_digest produces a stable 16-char hex digest for JsonValue input."""
    payload_a = {"b": 2, "a": 1, "nested": [1, 2, 3]}
    payload_b = {"a": 1, "b": 2, "nested": [1, 2, 3]}

    digest_a = safe_payload_digest(payload_a)
    digest_b = safe_payload_digest(payload_b)

    assert digest_a == digest_b
    assert len(digest_a) == 16
    assert all(c in "0123456789abcdef" for c in digest_a)


def test_safe_payload_digest_returns_invalid_sentinel_on_value_error() -> None:
    """Circular references (ValueError) yield the sentinel 'invalid' digest.

    Characterization note: ``safe_payload_digest`` only catches
    ``(RuntimeError, ValueError)``. ``json.dumps`` raises ``ValueError`` for
    circular references, which is mapped to the ``"invalid"`` sentinel.
    Truly unserializable objects raise ``TypeError`` which is NOT caught and
    propagates — this is the existing behavior and is intentionally preserved
    by the typing pass.
    """
    circular: dict[str, object] = {}
    circular["self"] = circular

    assert safe_payload_digest(circular) == "invalid"
