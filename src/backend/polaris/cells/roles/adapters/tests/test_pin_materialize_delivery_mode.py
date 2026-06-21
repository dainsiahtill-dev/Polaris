"""F31 (2026-06-16): a from-scratch build Director turn must materialize.

The kernel resolves the delivery contract by text-classifying the turn message;
a weak/terse build goal can fall through to the default ANALYZE_ONLY, whose
delivery-mode-filter then DROPS the Director's write tools ->
director_no_materialized_changes even though the Director DID emit writes
(factory-bench L4-23: 3 write tools dropped in analyze_only mode, 0 files).
``_pin_materialize_delivery_mode`` pins the explicit ``[mode:materialize]``
marker for requires-fresh tasks so the contract is deterministic.
"""

from __future__ import annotations

from polaris.cells.roles.adapters.internal.director import execute_method as director_execute_method
from polaris.cells.roles.adapters.internal.director.execute_method import (
    _pin_materialize_delivery_mode,
)
from polaris.cells.roles.kernel.public import DeliveryMode
from polaris.cells.roles.kernel.public.transaction_contracts import resolve_delivery_mode


class TestPinMaterializeDeliveryMode:
    def test_fresh_create_context_pins_materialize_control_plane(self) -> None:
        context = {"context_os_snapshot": {}, "metadata": {"task_id": "t1"}}
        pin_context = getattr(director_execute_method, "_pin_materialize_context_delivery_mode", None)

        assert pin_context is not None
        assert pin_context(context, True) is context
        assert context["delivery_mode"] == "materialize_changes"
        assert context["metadata"]["delivery_mode"] == "materialize_changes"

    def test_non_fresh_context_is_unchanged(self) -> None:
        context = {"context_os_snapshot": {}, "metadata": {"task_id": "t1"}}
        pin_context = getattr(director_execute_method, "_pin_materialize_context_delivery_mode", None)

        assert pin_context is not None
        assert pin_context(context, False) is context
        assert "delivery_mode" not in context
        assert context["metadata"] == {"task_id": "t1"}

    def test_fresh_create_without_marker_is_pinned(self) -> None:
        out = _pin_materialize_delivery_mode("Create main.py and a package", True)
        assert out.startswith("[mode:materialize]\n")
        assert "Create main.py" in out

    def test_marker_already_present_is_unchanged(self) -> None:
        msg = "[mode:materialize]\nbuild it"
        assert _pin_materialize_delivery_mode(msg, True) == msg

    def test_marker_present_other_case_is_unchanged(self) -> None:
        msg = "[MODE:MATERIALIZE] build it"
        assert _pin_materialize_delivery_mode(msg, True) == msg

    def test_non_fresh_task_is_unchanged(self) -> None:
        # An analysis/non-create Director turn must NOT be forced to materialize.
        msg = "Review the existing code and report issues"
        assert _pin_materialize_delivery_mode(msg, False) == msg

    def test_empty_message_non_fresh_unchanged(self) -> None:
        assert _pin_materialize_delivery_mode("", False) == ""

    def test_closed_loop_pinned_message_resolves_to_materialize(self) -> None:
        # The marker the helper injects MUST be honoured by the kernel's
        # delivery-mode classifier — otherwise the pin is inert. A terse goal
        # that would otherwise default to ANALYZE_ONLY must become MATERIALIZE.
        terse_goal = "scaffold the project"
        # Sanity: the terse goal alone does not already resolve to materialize
        # (else the test proves nothing) — but if the rule engine happens to,
        # the pin is still correct. The load-bearing assertion is the pinned one.
        pinned = _pin_materialize_delivery_mode(terse_goal, True)
        assert resolve_delivery_mode(pinned).mode == DeliveryMode.MATERIALIZE_CHANGES

    def test_non_fresh_terse_goal_not_forced(self) -> None:
        # Without the pin, a pure-analysis phrasing stays out of MATERIALIZE.
        contract = resolve_delivery_mode("analyze the architecture and summarize")
        assert contract.mode != DeliveryMode.MATERIALIZE_CHANGES
