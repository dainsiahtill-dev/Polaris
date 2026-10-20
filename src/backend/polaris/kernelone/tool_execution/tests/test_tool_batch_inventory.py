"""Structural inventory binding; no registry, Role object or execution effects."""

from copy import deepcopy
from typing import Any

import pytest
from polaris.kernelone.tool_execution import contracts


def _check(wire: object, *, execution: bool = False) -> str | None:
    validator = getattr(contracts, "tool_batch_inventory_error", None)
    assert callable(validator), "shared structural inventory validation is missing"
    return validator(wire, require_execution_groups=execution)


def _call(call_id: str = "read-1", value: Any = True) -> dict[str, Any]:
    return {
        "call_id": call_id,
        "tool_name": "read_file",
        "raw_tool_name": "read_file",
        "arguments": {"file": "declared.txt", "nested": [value, {"x": "value"}]},
        "native_arguments": {"file_path": "declared.txt"},
        "effect_type": "read",
        "execution_mode": "readonly_parallel",
        "classification": {"snapshot": {"registered": True, "snapshot_hash": "a" * 64}},
    }


def _batch() -> dict[str, Any]:
    call = _call()
    return {"batch_id": "batch", "invocations": [call], "parallel_readonly": [deepcopy(call)]}


@pytest.mark.parametrize("field", ["arguments", "native_arguments", "classification", "raw_tool_name", "tool_name"])
def test_changed_member_payload_is_rejected(field: str) -> None:
    wire = _batch()
    wire["parallel_readonly"][0][field] = {"changed": "value"} if field.endswith("arguments") else "changed"
    assert _check(wire) == "tool_batch_inventory_member_mismatch"


@pytest.mark.parametrize("declared,partition", [(True, 1), (False, 0), (1, 1.0), ([1, 2], [2, 1])])
def test_typed_values_and_nested_order_are_not_python_equality(declared: Any, partition: Any) -> None:
    wire = _batch()
    wire["invocations"][0]["arguments"]["nested"] = declared
    wire["parallel_readonly"][0]["arguments"]["nested"] = partition
    assert _check(wire) == "tool_batch_inventory_member_mismatch"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "not a string key"}, ("not", "json"), object()])
def test_non_json_payload_is_rejected(value: object) -> None:
    wire = _batch()
    wire["invocations"][0]["arguments"]["invalid"] = value
    assert _check(wire) == "tool_batch_inventory_invalid_wire"


@pytest.mark.parametrize("where", ["invocations", "parallel_readonly", "cross_group"])
def test_duplicate_call_ids_are_rejected(where: str) -> None:
    wire = _batch()
    if where == "cross_group":
        wire["readonly_serial"] = [deepcopy(wire["invocations"][0])]
    else:
        wire[where].append(deepcopy(wire[where][0]))
    assert _check(wire) == "tool_batch_inventory_duplicate_call_id"


def test_missing_and_extra_partition_members_are_rejected() -> None:
    wire = _batch()
    wire["invocations"].append(_call("read-2"))
    assert _check(wire) == "tool_batch_inventory_member_missing"
    wire = _batch()
    wire["readonly_serial"] = [_call("read-2")]
    assert _check(wire) == "tool_batch_inventory_member_extra"


def test_group_only_capture_only_and_empty_batch_keep_distinct_meanings() -> None:
    assert _check({"batch_id": "group-only", "parallel_readonly": [_call()]}, execution=True) is None
    capture = {"batch_id": "capture", "invocations": [_call()]}
    assert _check(capture) is None
    assert _check(capture, execution=True) == "tool_batch_inventory_execution_groups_missing"
    assert _check({"batch_id": "empty"}, execution=True) is None


def test_phase_partition_order_and_mapping_key_order_can_differ() -> None:
    first, second = _call("first"), _call("second")
    assert (
        _check(
            {
                "invocations": [first, second],
                "parallel_readonly": [dict(reversed(list(second.items())))],
                "readonly_serial": [first],
            },
            execution=True,
        )
        is None
    )


@pytest.mark.parametrize(
    "wire", [None, [], {"invocations": None}, {"parallel_readonly": {}}, {"invocations": [{"call_id": ""}]}]
)
def test_invalid_inventory_shape_has_stable_error(wire: object) -> None:
    assert _check(wire) == "tool_batch_inventory_invalid_wire"
