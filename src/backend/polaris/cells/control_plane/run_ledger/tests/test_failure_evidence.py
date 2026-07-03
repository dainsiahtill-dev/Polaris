from __future__ import annotations

from polaris.cells.control_plane.run_ledger.public import (
    FailureClassV1,
    FailureEvidenceV1,
    is_failure_class,
    normalize_failure_class,
)


def test_normalize_failure_class_canonicalizes_known_values() -> None:
    assert normalize_failure_class("tool_dispatch_dropped") == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert normalize_failure_class(" TOOL-DISPATCH-DROPPED ") == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert normalize_failure_class(FailureClassV1.MISSING_EFFECT_RECEIPT) == FailureClassV1.MISSING_EFFECT_RECEIPT.value


def test_normalize_failure_class_preserves_unknown_values() -> None:
    assert normalize_failure_class("new_platform_failure") == "new_platform_failure"
    assert normalize_failure_class(None, default=FailureClassV1.TOOL_LIFECYCLE_UNKNOWN) == (
        FailureClassV1.TOOL_LIFECYCLE_UNKNOWN.value
    )


def test_is_failure_class_uses_canonical_comparison() -> None:
    assert is_failure_class("tool dispatch dropped", FailureClassV1.TOOL_DISPATCH_DROPPED)
    assert not is_failure_class("missing_tool_result", FailureClassV1.TOOL_DISPATCH_DROPPED)


def test_failure_evidence_to_dict_normalizes_failure_class_and_refs() -> None:
    evidence = FailureEvidenceV1(
        failure_class="tool_dispatch_dropped",
        responsible_layer="execution_control_plane",
        reason="native calls had no dispatch receipt",
        evidence_refs=("receipt-1", "", "receipt-2"),
        metadata={"turn_id": "turn-1"},
    ).to_dict()

    assert evidence == {
        "schema_version": "failure_evidence.v1",
        "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        "responsible_layer": "execution_control_plane",
        "reason": "native calls had no dispatch receipt",
        "evidence_refs": ["receipt-1", "receipt-2"],
        "metadata": {"turn_id": "turn-1"},
    }
