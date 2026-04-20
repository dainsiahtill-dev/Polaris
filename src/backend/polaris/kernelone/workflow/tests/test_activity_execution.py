"""T4: ActivityExecution.attempt field regression tests.

Verifies that:
1. 'attempt' is a declared dataclass field (not a dynamic attribute).
2. 'attempt' defaults to 0 when ActivityExecution is created without it.
3. 'attempt' is writable (ActivityExecution is not frozen).
4. The field survives dataclasses.fields() introspection correctly.
"""

from __future__ import annotations

import dataclasses


def test_activity_execution_attempt_field_declared() -> None:
    """'attempt' must be a declared dataclass field, not a dynamic/instance attribute."""
    from polaris.kernelone.workflow.activity_runner import ActivityExecution

    field_names = {f.name for f in dataclasses.fields(ActivityExecution)}
    assert "attempt" in field_names, (
        "ActivityExecution must have 'attempt' as a declared dataclass field. "
        "A missing field causes AttributeError during retry loops."
    )


def test_activity_execution_attempt_default_is_zero() -> None:
    """ActivityExecution created without 'attempt' must have attempt == 0."""
    from polaris.kernelone.workflow.activity_runner import ActivityExecution

    exec_ = ActivityExecution(
        activity_id="a1",
        activity_name="test_act",
        workflow_id="wf1",
        input={},
    )
    assert exec_.attempt == 0, f"attempt should default to 0, got {exec_.attempt!r}"


def test_activity_execution_attempt_is_writable() -> None:
    """ActivityExecution must NOT be frozen — attempt must be assignable."""
    from polaris.kernelone.workflow.activity_runner import ActivityExecution

    exec_ = ActivityExecution(
        activity_id="a2",
        activity_name="retry_act",
        workflow_id="wf2",
        input={},
    )
    exec_.attempt = 3
    assert exec_.attempt == 3, f"attempt must be writable, got {exec_.attempt!r} after setting to 3"


def test_activity_execution_attempt_field_type_annotation() -> None:
    """The 'attempt' field annotation must be int (not Any or missing)."""
    import typing

    from polaris.kernelone.workflow.activity_runner import ActivityExecution

    attempt_field = next(
        (f for f in dataclasses.fields(ActivityExecution) if f.name == "attempt"),
        None,
    )
    assert attempt_field is not None
    # Resolve the annotation — works even with from __future__ import annotations
    hints = typing.get_type_hints(ActivityExecution)
    assert hints.get("attempt") is int, f"'attempt' field annotation must be int, got {hints.get('attempt')!r}"


def test_activity_execution_all_required_fields_present() -> None:
    """Smoke test: all originally-required fields plus 'attempt' must co-exist."""
    from polaris.kernelone.workflow.activity_runner import ActivityExecution

    required = {"activity_id", "activity_name", "workflow_id", "input", "attempt"}
    actual = {f.name for f in dataclasses.fields(ActivityExecution)}
    missing = required - actual
    assert not missing, f"ActivityExecution is missing required fields: {missing}"
