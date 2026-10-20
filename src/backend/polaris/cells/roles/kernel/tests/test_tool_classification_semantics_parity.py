from dataclasses import replace

import pytest
from polaris.cells.roles.kernel.public import turn_contracts as contracts
from polaris.kernelone.tool_execution.contracts import (
    CapturedToolSpecSnapshotV1,
    FrozenMapEntryV1,
    FrozenMapV1,
    FrozenNullV1,
    FrozenScalarV1,
    FrozenSequenceV1,
)
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry, ToolSpecRegistryConsistencyError

# Semantic boundaries, not registry fixtures. The external exact-original-module
# experiment reuses this matrix to compare old/new public producer wire bytes.
SEMANTIC_CASES = [
    ({"category": "read"}, "read", "readonly_parallel", False, None),
    ({"categories": ["read"]}, "read", "readonly_parallel", False, None),
    ({"effect_type": " READ "}, "read", "readonly_parallel", False, None),
    (
        {"category": "Read", "categories": ["read", " READ "], "effect_type": "read"},
        "read",
        "readonly_parallel",
        False,
        None,
    ),
    ({"category": "async"}, "async", "async_receipt", True, None),
    ({"categories": ["ASYNC"], "effect_type": "async"}, "async", "async_receipt", True, None),
    ({"category": "write"}, "write", "write_serial", True, None),
    ({"categories": ["exec", "execute", "delete", "mutate", "mutation"]}, "write", "write_serial", True, None),
    ({"category": "delete", "effect_type": "write"}, "write", "write_serial", True, None),
    ({"category": None, "categories": ["read"], "effect_type": None}, "read", "readonly_parallel", False, None),
    ({"category": "read", "categories": ["async"]}, "write", "write_serial", True, "deo_tool_normalization_failed"),
    ({"category": "read", "effect_type": "exec"}, "write", "write_serial", True, "deo_tool_normalization_failed"),
    ({"categories": ["read", "write"]}, "write", "write_serial", True, "deo_tool_normalization_failed"),
    ({"category": "future"}, "write", "write_serial", True, "deo_tool_normalization_failed"),
    ({"category": ""}, "write", "write_serial", True, "deo_tool_normalization_failed"),
    ({"category": "read", "categories": ["network"]}, "write", "write_serial", True, "deo_tool_normalization_failed"),
    ({}, "write", "write_serial", True, "deo_tool_normalization_failed"),
    (
        {"category": None, "categories": None, "effect_type": None},
        "write",
        "write_serial",
        True,
        "deo_tool_normalization_failed",
    ),
    ({"categories": []}, "write", "write_serial", True, "deo_tool_normalization_failed"),
    ({"category": False}, "write", "write_serial", True, "deo_tool_normalization_failed"),
    ({"category": 0}, "write", "write_serial", True, "deo_tool_normalization_failed"),
    ({"category": ["read"]}, "write", "write_serial", True, "deo_tool_normalization_failed"),
    ({"categories": "read"}, "write", "write_serial", True, "deo_tool_normalization_failed"),
    ({"categories": ["read", 0]}, "write", "write_serial", True, "deo_tool_normalization_failed"),
    ({"categories": [None]}, "write", "write_serial", True, "deo_tool_normalization_failed"),
    ({"effect_type": False}, "write", "write_serial", True, "deo_tool_normalization_failed"),
    ({"effect_type": {"read": True}}, "write", "write_serial", True, "deo_tool_normalization_failed"),
]


def _node(value):
    if value is None:
        return FrozenNullV1()
    if isinstance(value, dict):
        return FrozenMapV1(tuple(FrozenMapEntryV1(k, _node(v)) for k, v in sorted(value.items())))
    if isinstance(value, list):
        return FrozenSequenceV1(tuple(_node(v) for v in value))
    return FrozenScalarV1({str: "string", bool: "boolean", int: "integer", float: "float"}[type(value)], value)


def _snapshot(fields, *, registered=True):
    return CapturedToolSpecSnapshotV1(
        raw_tool_name="semantic_alias",
        canonical_tool_name="semantic_tool",
        registered=registered,
        canonical_effective_spec=_node(fields),
        canonical_name_view=_node(["semantic_tool"]),
        alias_binding_view=_node({"semantic_alias": "semantic_tool"}),
    )


def _capture(monkeypatch, snapshot):
    monkeypatch.setattr(ToolSpecRegistry, "capture_effective_spec", classmethod(lambda cls, name: snapshot))


@pytest.mark.parametrize(("fields", "effect", "mode", "normalization", "error"), SEMANTIC_CASES)
def test_public_role_classifier_retains_captured_semantic_contract(
    monkeypatch, fields, effect, mode, normalization, error
):
    snapshot = _snapshot(fields)
    _capture(monkeypatch, snapshot)
    result = contracts.classify_tool_invocation("semantic_alias")
    assert result.raw_tool_name == "semantic_alias" and result.canonical_tool_name == "semantic_tool"
    assert result.effect_type is contracts.ToolEffectType(effect)
    assert result.execution_mode is contracts.ToolExecutionMode(mode)
    assert (result.normalization_required, result.error_code) == (normalization, error)
    assert result.snapshot is snapshot
    assert contracts.tool_classification_matches_snapshot(result) is (error is None)


def test_unregistered_captured_read_is_still_conservative_without_normalization_error(monkeypatch):
    snapshot = _snapshot({"category": "read"}, registered=False)
    _capture(monkeypatch, snapshot)
    result = contracts.classify_tool_invocation("semantic_alias")
    assert not result.registered
    assert (result.effect_type, result.execution_mode, result.normalization_required, result.error_code) == (
        contracts.ToolEffectType.WRITE,
        contracts.ToolExecutionMode.WRITE_SERIAL,
        False,
        None,
    )


@pytest.mark.parametrize(
    "fields", [{"category": "read"}, {"category": "async"}, {"category": "write"}, {"categories": ["read", "write"]}]
)
def test_invocation_and_outcome_json_roundtrip_keep_role_class_identity(monkeypatch, fields):
    _capture(monkeypatch, _snapshot(fields))
    invocation = contracts.ToolInvocation(
        call_id=contracts.ToolCallId("call-1"), tool_name="semantic_alias", arguments={"path": "source.txt"}
    )
    restored = contracts.ToolInvocation.model_validate_json(invocation.model_dump_json())
    assert restored.model_dump_json() == invocation.model_dump_json()
    assert type(restored.classification) is contracts.ToolClassificationV1
    assert type(restored.effect_type) is contracts.ToolEffectType
    assert type(restored.execution_mode) is contracts.ToolExecutionMode
    outcome = contracts.TurnOutcome(
        turn_id=contracts.TurnId("turn-1"),
        run_id="run-1",
        decision=contracts.TurnDecision(
            turn_id=contracts.TurnId("turn-1"),
            kind=contracts.TurnDecisionKind.TOOL_BATCH,
            visible_message="semantic probe",
            tool_batch=contracts.ToolBatch(batch_id=contracts.BatchId("batch-1"), invocations=[invocation]),
            finalize_mode=contracts.FinalizeMode.NONE,
            domain="code",
        ),
        outcome_status=contracts.OutcomeStatus.FAILED,
        resolution_code=contracts.ResolutionCode.FAIL_CLOSED,
    )
    assert (
        contracts.TurnOutcome.model_validate_json(outcome.model_dump_json()).model_dump_json()
        == outcome.model_dump_json()
    )


@pytest.mark.parametrize("field", ["tool_spec_hash", "canonical_name_view_hash", "alias_binding_hash", "snapshot_hash"])
def test_public_snapshot_guard_rejects_original_hash_tampering(monkeypatch, field):
    snapshot = _snapshot({"category": "read"})
    _capture(monkeypatch, snapshot)
    classification = contracts.classify_tool_invocation("semantic_alias")
    object.__setattr__(snapshot, field, "0" * 64)
    assert not contracts.tool_classification_matches_snapshot(classification)


@pytest.mark.parametrize("defect", ["alias", "canonical", "raw", "classification"])
def test_captured_factory_preserves_full_binding_and_classification_guards(monkeypatch, defect):
    snapshot = _snapshot({"category": "read"})
    if defect == "alias":
        snapshot = replace(snapshot, alias_binding_view=_node({"semantic_alias": "foreign"}))
    elif defect == "canonical":
        snapshot = replace(snapshot, canonical_name_view=_node(["foreign"]))
    _capture(monkeypatch, snapshot)
    result = contracts.classify_tool_invocation("semantic_alias")
    if defect == "raw":
        result = replace(result, raw_tool_name="foreign")
    elif defect == "classification":
        result = replace(result, normalization_required=True)
    assert not contracts.tool_classification_matches_snapshot(result)


def test_captured_guard_never_recaptures_live_registry(monkeypatch):
    classification = contracts.classify_tool_invocation("cat")
    monkeypatch.setattr(
        ToolSpecRegistry,
        "capture_effective_spec",
        classmethod(lambda cls, name: (_ for _ in ()).throw(AssertionError("live recapture forbidden"))),
    )
    assert contracts.tool_classification_matches_snapshot(classification)
    invocation = contracts.ToolInvocation._from_captured_classification(
        call_id=contracts.ToolCallId("call-1"), raw_tool_name="cat", arguments={}, classification=classification
    )
    assert invocation.classification is classification


def test_registry_consistency_failure_keeps_exact_role_error(monkeypatch):
    monkeypatch.setattr(
        ToolSpecRegistry,
        "capture_effective_spec",
        classmethod(lambda cls, name: (_ for _ in ()).throw(ToolSpecRegistryConsistencyError("inconsistent"))),
    )
    result = contracts.classify_tool_invocation("cat")
    assert (result.registered, result.snapshot, result.normalization_required, result.error_code) == (
        False,
        None,
        True,
        "deo_tool_normalization_failed",
    )


@pytest.mark.parametrize("name", ["__internal", " __synthetic", "__async_callback"])
def test_public_synthetic_names_remain_forbidden(name):
    with pytest.raises(ValueError, match="synthetic_tool_invocation_forbidden"):
        contracts.classify_tool_invocation(name)
