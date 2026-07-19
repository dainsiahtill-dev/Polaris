"""A5.1 classifier and snapshot-bound decoding parity tests."""

from __future__ import annotations

import json
from dataclasses import fields, replace

import pytest
from polaris.cells.roles.kernel.internal.turn_decision_decoder import (
    DecodeConfig,
    RawLLMResponse,
    TurnDecisionDecoder,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    TurnId,
    classify_tool_invocation,
)
from polaris.kernelone.tool_execution.contracts import (
    CapturedToolSpecSnapshotV1,
    FrozenMapEntryV1,
    FrozenMapV1,
    FrozenScalarV1,
    FrozenSequenceV1,
)
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry


def _native_tool(name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "id": "call_1",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _with_semantic_fields(
    snapshot: CapturedToolSpecSnapshotV1,
    *,
    category: str | None,
    categories: tuple[str, ...] | None,
    effect_type: str | None,
) -> CapturedToolSpecSnapshotV1:
    entries = {entry.key: entry.value for entry in snapshot.canonical_effective_spec.entries}
    if category is None:
        entries.pop("category", None)
    else:
        entries["category"] = FrozenScalarV1("string", category)
    if categories is None:
        entries.pop("categories", None)
    else:
        entries["categories"] = FrozenSequenceV1(tuple(FrozenScalarV1("string", value) for value in categories))
    if effect_type is None:
        entries.pop("effect_type", None)
    else:
        entries["effect_type"] = FrozenScalarV1("string", effect_type)
    return replace(
        snapshot,
        canonical_effective_spec=FrozenMapV1(
            tuple(FrozenMapEntryV1(key, value) for key, value in sorted(entries.items()))
        ),
    )


def _capture(snapshot: CapturedToolSpecSnapshotV1):
    def capture_effective_spec(_: str) -> CapturedToolSpecSnapshotV1:
        return snapshot

    return capture_effective_spec


def _forged_snapshot(
    snapshot: CapturedToolSpecSnapshotV1,
    **changes: object,
) -> CapturedToolSpecSnapshotV1:
    """Build an adversarial snapshot whose derived hashes need not agree with its fields."""
    forged = object.__new__(CapturedToolSpecSnapshotV1)
    for field in fields(CapturedToolSpecSnapshotV1):
        object.__setattr__(forged, field.name, changes.get(field.name, getattr(snapshot, field.name)))
    return forged


def test_classifier_retains_alias_provenance_and_read_semantics() -> None:
    classification = classify_tool_invocation("cat")

    assert classification.raw_tool_name == "cat"
    assert classification.canonical_tool_name == "read_file"
    assert classification.effect_type is ToolEffectType.READ
    assert classification.execution_mode is ToolExecutionMode.READONLY_PARALLEL
    assert classification.snapshot is not None


@pytest.mark.parametrize(
    ("category", "categories", "effect_type"),
    (
        ("read", None, "write"),
        (None, ("read", "write"), None),
        ("read", None, "delete"),
        ("read", ("read", "unsupported"), None),
    ),
)
def test_classifier_fails_closed_for_conflicting_captured_effective_spec(
    monkeypatch: pytest.MonkeyPatch,
    category: str | None,
    categories: tuple[str, ...] | None,
    effect_type: str | None,
) -> None:
    captured = classify_tool_invocation("write_file").snapshot
    assert isinstance(captured, CapturedToolSpecSnapshotV1)
    monkeypatch.setattr(
        ToolSpecRegistry,
        "capture_effective_spec",
        _capture(
            _with_semantic_fields(
                captured,
                category=category,
                categories=categories,
                effect_type=effect_type,
            )
        ),
    )

    classification = classify_tool_invocation("write_file")

    assert classification.effect_type is ToolEffectType.WRITE
    assert classification.execution_mode is ToolExecutionMode.WRITE_SERIAL
    assert classification.normalization_required is True
    assert classification.error_code == "deo_tool_normalization_failed"


def test_classifier_accepts_consistent_read_and_write_effective_specs() -> None:
    read = classify_tool_invocation("read_file")
    write = classify_tool_invocation("write_file")

    assert (read.effect_type, read.error_code, read.normalization_required) == (ToolEffectType.READ, None, False)
    assert (write.effect_type, write.error_code, write.normalization_required) == (ToolEffectType.WRITE, None, True)


def test_tool_invocation_rejects_forged_caller_classification() -> None:
    with pytest.raises(ValueError, match="deo_tool_classification_mismatch"):
        ToolInvocation(
            call_id=ToolCallId("call_write"),
            tool_name="write_file",
            arguments={"path": "main.py", "content": "x"},
            effect_type=ToolEffectType.READ,
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )


def test_tool_invocation_ignores_direct_classification_injection() -> None:
    injected_read = classify_tool_invocation("read_file")

    invocation = ToolInvocation(
        call_id=ToolCallId("call_injected"),
        tool_name="write_file",
        raw_tool_name="write_file",
        arguments={"path": "main.py", "content": "x"},
        classification=injected_read,
    )

    assert invocation.classification is not injected_read
    assert invocation.effect_type is ToolEffectType.WRITE
    assert invocation.execution_mode is ToolExecutionMode.WRITE_SERIAL


def test_captured_factory_rejects_direct_forged_write_as_read_classification() -> None:
    classification = classify_tool_invocation("write_file")
    forged = replace(
        classification,
        effect_type=ToolEffectType.READ,
        execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        normalization_required=False,
    )

    with pytest.raises(ValueError, match="deo_tool_classification_mismatch"):
        ToolInvocation._from_captured_classification(
            call_id=ToolCallId("call_forged"),
            raw_tool_name="write_file",
            arguments={"path": "main.py", "content": "x"},
            classification=forged,
        )


def test_captured_factory_rejects_swapped_read_snapshot_for_write_raw_name() -> None:
    read = classify_tool_invocation("read_file")
    assert isinstance(read.snapshot, CapturedToolSpecSnapshotV1)
    forged = replace(read, raw_tool_name="write_file")

    with pytest.raises(ValueError, match="deo_tool_classification_mismatch"):
        ToolInvocation._from_captured_classification(
            call_id=ToolCallId("call_swapped_snapshot"),
            raw_tool_name="write_file",
            arguments={"path": "main.py", "content": "x"},
            classification=forged,
        )


def test_captured_factory_accepts_alias_bound_to_its_captured_snapshot() -> None:
    alias = classify_tool_invocation("cat")

    invocation = ToolInvocation._from_captured_classification(
        call_id=ToolCallId("call_alias"),
        raw_tool_name="cat",
        arguments={"path": "main.py"},
        classification=alias,
    )

    assert invocation.raw_tool_name == "cat"
    assert invocation.tool_name == "read_file"
    assert invocation.effect_type is ToolEffectType.READ
    assert invocation.execution_mode is ToolExecutionMode.READONLY_PARALLEL


def test_captured_factory_rejects_canonical_name_drift() -> None:
    classification = classify_tool_invocation("write_file")
    forged = replace(classification, canonical_tool_name="execute_command")

    with pytest.raises(ValueError, match="deo_tool_classification_mismatch"):
        ToolInvocation._from_captured_classification(
            call_id=ToolCallId("call_canonical_drift"),
            raw_tool_name="write_file",
            arguments={"path": "main.py", "content": "x"},
            classification=forged,
        )


def test_captured_factory_rejects_snapshot_raw_hash_mismatch() -> None:
    read = classify_tool_invocation("read_file")
    assert isinstance(read.snapshot, CapturedToolSpecSnapshotV1)
    forged_snapshot = _forged_snapshot(read.snapshot, raw_tool_name="write_file")
    forged = replace(read, raw_tool_name="write_file", snapshot=forged_snapshot)

    with pytest.raises(ValueError, match="deo_tool_classification_mismatch"):
        ToolInvocation._from_captured_classification(
            call_id=ToolCallId("call_snapshot_hash_drift"),
            raw_tool_name="write_file",
            arguments={"path": "main.py", "content": "x"},
            classification=forged,
        )


def test_captured_factory_rejects_rehashed_cross_tool_snapshot() -> None:
    read = classify_tool_invocation("read_file")
    assert isinstance(read.snapshot, CapturedToolSpecSnapshotV1)
    cross_tool_snapshot = replace(read.snapshot, raw_tool_name="write_file")
    forged = replace(read, raw_tool_name="write_file", snapshot=cross_tool_snapshot)

    with pytest.raises(ValueError, match="deo_tool_classification_mismatch"):
        ToolInvocation._from_captured_classification(
            call_id=ToolCallId("call_rehashed_cross_tool_snapshot"),
            raw_tool_name="write_file",
            arguments={"path": "main.py", "content": "x"},
            classification=forged,
        )


def test_unregistered_async_named_tool_is_conservative_write_serial() -> None:
    classification = classify_tool_invocation("deploy")

    assert classification.registered is False
    assert classification.effect_type is ToolEffectType.WRITE
    assert classification.execution_mode is ToolExecutionMode.WRITE_SERIAL


@pytest.mark.parametrize("synthetic_name", ["__prepare_shadow__", "__validate_shadow__", "__private_shadow__"])
def test_synthetic_names_are_not_tool_invocations(synthetic_name: str) -> None:
    with pytest.raises(ValueError, match="synthetic_tool_invocation_forbidden"):
        classify_tool_invocation(synthetic_name)

    with pytest.raises(ValueError, match="synthetic_tool_invocation_forbidden"):
        ToolInvocation(
            call_id=ToolCallId("call_synthetic"),
            tool_name=synthetic_name,
            arguments={},
        )


def test_synthetic_shadow_key_is_deeply_immutable_and_hash_stable() -> None:
    from polaris.cells.roles.kernel.internal.speculation.contracts import SyntheticShadowToolKeyV1

    key = SyntheticShadowToolKeyV1.build(
        source_tool_call_id="call_1",
        canonical_tool_name="__prepare_shadow__",
        shadow_phase="write_phase",
    )
    original_hash = key.shadow_key_hash
    original_object_hash = hash(key)

    assert key.shadow_key_hash == original_hash
    assert hash(key) == original_object_hash
    assert key.executable is False
    assert {field.name for field in fields(key)} == {
        "source_tool_call_id",
        "canonical_tool_name",
        "shadow_phase",
        "shadow_key_hash",
        "executable",
    }
    assert not hasattr(key, "arguments")


def test_synthetic_shadow_key_equality_is_call_and_phase_sensitive() -> None:
    from polaris.cells.roles.kernel.internal.speculation.contracts import SyntheticShadowToolKeyV1

    first = SyntheticShadowToolKeyV1.build(
        source_tool_call_id="call_1",
        canonical_tool_name="__prepare_shadow__",
        shadow_phase="write_phase",
    )
    same = SyntheticShadowToolKeyV1.build(
        source_tool_call_id="call_1",
        canonical_tool_name="__prepare_shadow__",
        shadow_phase="write_phase",
    )
    different_call = SyntheticShadowToolKeyV1.build(
        source_tool_call_id="call_2",
        canonical_tool_name="__prepare_shadow__",
        shadow_phase="write_phase",
    )
    different_phase = SyntheticShadowToolKeyV1.build(
        source_tool_call_id="call_1",
        canonical_tool_name="__prepare_shadow__",
        shadow_phase="candidate",
    )

    assert first == same
    assert hash(first) == hash(same)
    assert first.shadow_key_hash == same.shadow_key_hash
    assert first != different_call
    assert first.shadow_key_hash != different_call.shadow_key_hash
    assert first != different_phase
    assert first.shadow_key_hash != different_phase.shadow_key_hash


def test_synthetic_shadow_key_prevents_cross_call_collisions() -> None:
    from polaris.cells.roles.kernel.internal.speculation.contracts import SyntheticShadowToolKeyV1

    first = SyntheticShadowToolKeyV1.build(
        source_tool_call_id="call_first",
        canonical_tool_name="__prepare_shadow__",
        shadow_phase="write_phase",
    )
    concurrent_equivalent = SyntheticShadowToolKeyV1.build(
        source_tool_call_id="call_second",
        canonical_tool_name="__prepare_shadow__",
        shadow_phase="write_phase",
    )

    assert first.source_tool_call_id != concurrent_equivalent.source_tool_call_id
    assert first != concurrent_equivalent
    assert hash(first) != hash(concurrent_equivalent)
    assert first.shadow_key_hash != concurrent_equivalent.shadow_key_hash


def test_synthetic_shadow_key_exact_shape_binds_source_call_without_arguments() -> None:
    import inspect

    from polaris.cells.roles.kernel.internal.speculation.contracts import SyntheticShadowToolKeyV1

    assert "arguments" not in inspect.signature(SyntheticShadowToolKeyV1.build).parameters
    first = SyntheticShadowToolKeyV1.build(
        source_tool_call_id="call_first",
        canonical_tool_name="__prepare_shadow__",
        shadow_phase="write_phase",
    )
    second = SyntheticShadowToolKeyV1.build(
        source_tool_call_id="call_second",
        canonical_tool_name="__prepare_shadow__",
        shadow_phase="write_phase",
    )

    assert first != second
    assert first.shadow_key_hash != second.shadow_key_hash
    assert "_shadow_arguments" not in inspect.getsource(SyntheticShadowToolKeyV1)


def test_transaction_layer_has_no_second_tool_alias_authority() -> None:
    from pathlib import Path

    backend_root = Path(__file__).resolve().parents[5]
    transaction_root = backend_root / "polaris/cells/roles/kernel/internal/transaction"
    for relative in ("constants.py", "__init__.py", "task_contract_builder.py", "contract_guards.py"):
        source = (transaction_root / relative).read_text(encoding="utf-8")
        assert "TOOL_ALIASES" not in source, relative


def test_decoder_read_alias_preserves_raw_arguments_without_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.kernel.internal import turn_decision_decoder

    def _unexpected_normalization(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("registered READ must not normalize arguments")

    monkeypatch.setattr(
        turn_decision_decoder,
        "normalize_tool_arguments_from_snapshot",
        _unexpected_normalization,
    )
    response = RawLLMResponse(
        content="",
        thinking=None,
        native_tool_calls=[_native_tool("cat", {"path": "main.py"})],
        model="test",
        usage={},
    )

    decision = TurnDecisionDecoder(config=DecodeConfig(domain="code")).decode(response, TurnId("turn_read"))

    invocation = decision.tool_batch.invocations[0]  # type: ignore[union-attr]
    assert invocation.raw_tool_name == "cat"
    assert invocation.tool_name == "read_file"
    assert invocation.arguments == {"path": "main.py"}


def test_decoder_mutation_normalizes_once_from_captured_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.kernel.internal import turn_decision_decoder

    calls = 0

    def _normalize_once(snapshot: CapturedToolSpecSnapshotV1, arguments: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        assert snapshot.canonical_tool_name == "write_file"
        assert arguments == {"path": "main.py", "content": "x"}
        return {"file": "main.py", "content": "x"}

    monkeypatch.setattr(
        turn_decision_decoder,
        "normalize_tool_arguments_from_snapshot",
        _normalize_once,
    )
    response = RawLLMResponse(
        content="",
        thinking=None,
        native_tool_calls=[_native_tool("write_file", {"path": "main.py", "content": "x"})],
        model="test",
        usage={},
    )

    decision = TurnDecisionDecoder(config=DecodeConfig(domain="code")).decode(response, TurnId("turn_write"))

    invocation = decision.tool_batch.invocations[0]  # type: ignore[union-attr]
    assert calls == 1
    assert invocation.arguments == {"file": "main.py", "content": "x"}
