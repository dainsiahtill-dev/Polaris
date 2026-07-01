"""KernelGuard invariant assertions bound to ledger telemetry.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Free-function implementations behind the ``_guard_assert_*`` controller
entrypoints. Each binds a :class:`KernelGuard`
invariant assertion to the per-turn ledger's ``record_kernel_guard_assert``
telemetry. The controller keeps thin static methods delegating here so that the
``guard_assert_single_tool_batch`` / ``guard_assert_no_finalization_tool_calls``
callables injected into the sub-handlers keep their exact signatures.

Bodies moved verbatim from ``turn_transaction_controller.py``.
"""

from __future__ import annotations

from typing import Any

from polaris.cells.roles.kernel.internal.kernel_guard import (
    KernelGuard,
    KernelGuardError,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger


def guard_assert_single_decision(
    *,
    turn_id: str,
    decision_count: int,
    tool_batch_count: int | None,
    ledger: TurnLedger,
) -> None:
    try:
        KernelGuard.assert_single_decision(turn_id, decision_count, tool_batch_count)
        ledger.record_kernel_guard_assert(True)
    except KernelGuardError:
        ledger.record_kernel_guard_assert(False)
        raise


def guard_assert_single_tool_batch(*, turn_id: str, tool_batch_count: int, ledger: TurnLedger) -> None:
    try:
        KernelGuard.assert_single_tool_batch(turn_id, tool_batch_count)
        ledger.record_kernel_guard_assert(True)
    except KernelGuardError:
        ledger.record_kernel_guard_assert(False)
        raise


def guard_assert_no_hidden_continuation(
    *,
    turn_id: str,
    state_trajectory: list[str] | tuple[str, ...],
    ledger: TurnLedger,
) -> None:
    try:
        KernelGuard.assert_no_hidden_continuation(turn_id, state_trajectory)
        ledger.record_kernel_guard_assert(True)
    except KernelGuardError:
        ledger.record_kernel_guard_assert(False)
        raise


def guard_assert_no_finalization_tool_calls(*, turn_id: str, tool_calls: list[Any] | None, ledger: TurnLedger) -> None:
    # Soft guard: no longer raises KernelGuardError, but records anomaly flags
    # and metrics. We still record assert pass for telemetry consistency.
    KernelGuard.assert_no_finalization_tool_calls(turn_id, tool_calls, ledger=ledger)
    ledger.record_kernel_guard_assert(True)
