from dataclasses import FrozenInstanceError, replace

import pytest
from polaris.kernelone.tool_execution.contracts import (
    CapturedToolSpecSnapshotV1,
    FrozenMapEntryV1,
    FrozenMapV1,
    FrozenNullV1,
    FrozenScalarV1,
    FrozenSequenceV1,
)


def _node(value):
    if value is None:
        return FrozenNullV1()
    if isinstance(value, dict):
        return FrozenMapV1(tuple(FrozenMapEntryV1(k, _node(v)) for k, v in sorted(value.items())))
    if isinstance(value, list):
        return FrozenSequenceV1(tuple(_node(v) for v in value))
    kind = {str: "string", bool: "boolean", int: "integer", float: "float"}[type(value)]
    return FrozenScalarV1(kind, value)


def _snapshot(fields):
    return CapturedToolSpecSnapshotV1(
        raw_tool_name="semantic_alias",
        canonical_tool_name="semantic_tool",
        registered=True,
        canonical_effective_spec=_node(fields),
        canonical_name_view=_node(["semantic_tool"]),
        alias_binding_view=_node({"semantic_alias": "semantic_tool"}),
    )


@pytest.mark.parametrize(
    ("fields", "effect", "mode", "normalization"),
    [
        ({"category": "read"}, "read", "readonly_parallel", False),
        ({"categories": [" READ ", "read"], "effect_type": "Read"}, "read", "readonly_parallel", False),
        ({"categories": ["async"], "effect_type": " ASYNC "}, "async", "async_receipt", True),
        ({"category": "write", "categories": ["exec", "delete", "mutate"]}, "write", "write_serial", True),
        ({"effect_type": "execute"}, "write", "write_serial", True),
        ({"category": None, "categories": ["mutation"]}, "write", "write_serial", True),
    ],
)
def test_captured_semantic_categories_determine_execution(fields, effect, mode, normalization):
    from polaris.kernelone.tool_execution.captured_tool_semantics import classify_captured_tool_semantics

    snapshot = _snapshot(fields)
    before = snapshot.snapshot_hash
    result = classify_captured_tool_semantics(snapshot)
    assert (result.effect_type, result.execution_mode, result.normalization_required, result.error_code) == (
        effect,
        mode,
        normalization,
        None,
    )
    assert snapshot.snapshot_hash == before


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"category": "future"},
        {"category": ""},
        {"categories": []},
        {"category": "read", "categories": ["async"]},
        {"categories": ["read", "write"]},
        {"effect_type": "read", "category": "delete"},
        {"category": False},
        {"categories": "read"},
        {"categories": ["read", 0]},
        {"effect_type": 0},
        {"categories": [None]},
    ],
)
def test_ambiguous_unknown_or_malformed_semantics_stay_fail_closed(fields):
    from polaris.kernelone.tool_execution.captured_tool_semantics import classify_captured_tool_semantics

    result = classify_captured_tool_semantics(_snapshot(fields))
    assert (result.effect_type, result.execution_mode, result.normalization_required) == ("write", "write_serial", True)
    assert result.error_code == "captured_tool_semantics_invalid"


def test_semantic_result_is_immutable_and_only_reads_captured_fields():
    from polaris.kernelone.tool_execution.captured_tool_semantics import classify_captured_tool_semantics

    captured = _snapshot({"category": "read"})
    first = classify_captured_tool_semantics(captured)
    changed = replace(captured, canonical_effective_spec=_node({"category": "write"}))
    assert classify_captured_tool_semantics(changed).effect_type == "write"
    assert classify_captured_tool_semantics(captured) == first
    with pytest.raises(FrozenInstanceError):
        first.effect_type = "write"


@pytest.mark.parametrize(
    "defect",
    [
        "valid",
        "tool_spec_hash",
        "canonical_name_view_hash",
        "alias_binding_hash",
        "snapshot_hash",
        "alias",
        "canonical",
    ],
)
def test_snapshot_consistency_preserves_hashes_and_captured_name_binding(defect):
    from polaris.kernelone.tool_execution.captured_tool_semantics import captured_tool_spec_snapshot_is_consistent

    snapshot = _snapshot({"category": "read"})
    if defect in {"tool_spec_hash", "canonical_name_view_hash", "alias_binding_hash", "snapshot_hash"}:
        object.__setattr__(snapshot, defect, "0" * 64)
    elif defect == "alias":
        snapshot = replace(snapshot, alias_binding_view=_node({"semantic_alias": "foreign"}))
    elif defect == "canonical":
        snapshot = replace(snapshot, canonical_name_view=_node(["foreign"]))
    assert captured_tool_spec_snapshot_is_consistent(snapshot) is (defect == "valid")


def test_base_import_has_no_cell_dependency_in_cold_process():
    import subprocess
    import sys

    script = "import sys; import polaris.kernelone.tool_execution.captured_tool_semantics; assert not any(n.startswith('polaris.cells') for n in sys.modules)"
    result = subprocess.run(
        ["rtk", "proxy", sys.executable, "-c", script], capture_output=True, text=True, encoding="utf-8", check=False
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "defect",
    [
        "valid",
        "tool_spec_hash",
        "canonical_name_view_hash",
        "alias_binding_hash",
        "snapshot_hash",
        "missing_hash",
        "extra_field",
        "registered_integer",
        "registered_string",
        "scalar_bool_as_integer",
        "node_extra",
        "node_wrong_kind",
        "map_duplicate",
        "map_unsorted",
        "alias",
        "canonical",
    ],
)
def test_restore_owner_wire_compares_provided_hashes_and_exact_typed_record(defect):
    import json
    from dataclasses import asdict

    from polaris.kernelone.tool_execution.captured_tool_semantics import restore_captured_tool_spec_snapshot

    snapshot = _snapshot({"category": "read", "flag": False, "number": 0})
    wire = json.loads(json.dumps(asdict(snapshot)))
    if defect in {"tool_spec_hash", "canonical_name_view_hash", "alias_binding_hash", "snapshot_hash"}:
        wire[defect] = "0" * 64
    elif defect == "missing_hash":
        del wire["snapshot_hash"]
    elif defect == "extra_field":
        wire["extra"] = True
    elif defect == "registered_integer":
        wire["registered"] = 1
    elif defect == "registered_string":
        wire["registered"] = "true"
    elif defect == "scalar_bool_as_integer":
        wire["canonical_effective_spec"]["entries"][1]["value"]["value"] = 0
    elif defect == "node_extra":
        wire["canonical_effective_spec"]["entries"][0]["value"]["extra"] = 0
    elif defect == "node_wrong_kind":
        wire["canonical_effective_spec"]["kind"] = "unknown"
    elif defect == "map_duplicate":
        wire["canonical_effective_spec"]["entries"].append(wire["canonical_effective_spec"]["entries"][0])
    elif defect == "map_unsorted":
        wire["canonical_effective_spec"]["entries"].reverse()
    elif defect == "alias":
        wire = json.loads(
            json.dumps(asdict(replace(snapshot, alias_binding_view=_node({"semantic_alias": "foreign"}))))
        )
    elif defect == "canonical":
        wire = json.loads(json.dumps(asdict(replace(snapshot, canonical_name_view=_node(["foreign"])))))
    restored = restore_captured_tool_spec_snapshot(wire)
    assert restored == snapshot if defect == "valid" else restored is None


@pytest.mark.parametrize("value", [None, [], {}, False, 0, "snapshot"])
def test_restore_missing_or_nonrecord_owner_wire_has_no_snapshot(value):
    from polaris.kernelone.tool_execution.captured_tool_semantics import restore_captured_tool_spec_snapshot

    assert restore_captured_tool_spec_snapshot(value) is None
