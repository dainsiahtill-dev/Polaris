"""Tool-result projection must preserve immutable map/sequence kinds exactly."""

from __future__ import annotations

import pytest
from polaris.cells.director.runtime.public import DirectedEffectImmutableMapV1, DirectedEffectImmutableSequenceV1
from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolBatchRuntime


@pytest.mark.parametrize(
    "value, expected",
    [
        (DirectedEffectImmutableMapV1(items=()), {}),
        (DirectedEffectImmutableSequenceV1(items=()), []),
        (
            DirectedEffectImmutableMapV1(
                items=(
                    ("forbidden_paths", DirectedEffectImmutableSequenceV1(items=())),
                    ("metadata", DirectedEffectImmutableMapV1(items=())),
                    ("reasons", DirectedEffectImmutableSequenceV1(items=())),
                )
            ),
            {"forbidden_paths": [], "metadata": {}, "reasons": []},
        ),
        (
            DirectedEffectImmutableSequenceV1(
                items=(DirectedEffectImmutableMapV1(items=()), DirectedEffectImmutableSequenceV1(items=()))
            ),
            [{}, []],
        ),
    ],
)
def test_result_projection_keeps_declared_container_kind(value: object, expected: object) -> None:
    """Empty sequence must not be inferred as a map by all(empty)==True."""
    assert ToolBatchRuntime._thaw_directed_effect_value(value) == expected
