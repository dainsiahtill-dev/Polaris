"""Characterization tests for transaction.turn_session_scope (REMAINING_06 step 6).

UTF-8 编码验证: 本文所有文本使用 UTF-8。

Pins the shared per-turn session/correlation/truthlog scope extracted verbatim
from the head and tail of ``TurnTransactionController.execute`` /
``.execute_stream``:

* session-level PhaseManager / ModificationContract are created fresh on the
  first turn and restored (same identity) on subsequent turns;
* the three correlation ``ContextVar``s are set inside the scope and reset on
  exit (also on exception);
* the active truthlog recorder is published for the scope body and cleared on
  exit;
* ``turn_request_id`` is passed through when provided and generated when ``None``;
  ``parent_span_id`` is published to the parent-span ContextVar inside the scope.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.transaction.correlation import (
    _TURN_PARENT_SPAN_ID_CONTEXT,
    _TURN_REQUEST_ID_CONTEXT,
    _TURN_SPAN_ID_CONTEXT,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
from polaris.cells.roles.kernel.internal.transaction.turn_session_scope import (
    TurnSessionScope,
    turn_session_scope,
)
from polaris.cells.roles.kernel.internal.turn_transaction_controller import (
    TurnTransactionController,
)


def _controller() -> TurnTransactionController:
    return TurnTransactionController(
        llm_provider=AsyncMock(),
        tool_runtime=AsyncMock(),
        config=TransactionConfig(),
    )


@pytest.mark.asyncio
async def test_scope_creates_then_restores_session_state() -> None:
    controller = _controller()
    assert controller._session_phase_manager is None
    assert controller._session_modification_contract is None

    async with turn_session_scope(
        controller, turn_id="t1", context=[], turn_request_id=None, parent_span_id=None
    ) as scope:
        assert isinstance(scope, TurnSessionScope)
        first_pm = controller._session_phase_manager
        first_mc = controller._session_modification_contract
        # Created fresh and bound onto the per-turn ledger.
        assert first_pm is not None
        assert first_mc is not None
        assert scope.ledger.phase_manager is first_pm
        assert scope.ledger.modification_contract is first_mc

    # Second turn: same session-level identities are restored, not recreated.
    async with turn_session_scope(
        controller, turn_id="t2", context=[], turn_request_id=None, parent_span_id=None
    ) as scope2:
        assert controller._session_phase_manager is first_pm
        assert controller._session_modification_contract is first_mc
        assert scope2.ledger.phase_manager is first_pm
        assert scope2.ledger.modification_contract is first_mc


@pytest.mark.asyncio
async def test_scope_sets_and_resets_correlation_contextvars() -> None:
    controller = _controller()
    # Outside the scope, the contextvars are at their defaults.
    assert _TURN_REQUEST_ID_CONTEXT.get() is None
    assert _TURN_SPAN_ID_CONTEXT.get() is None
    assert _TURN_PARENT_SPAN_ID_CONTEXT.get() is None

    async with turn_session_scope(
        controller,
        turn_id="t",
        context=[],
        turn_request_id="req-123",
        parent_span_id="parent-9",
    ) as scope:
        assert scope.effective_turn_request_id == "req-123"
        assert _TURN_REQUEST_ID_CONTEXT.get() == "req-123"
        assert _TURN_SPAN_ID_CONTEXT.get() == scope.effective_turn_span_id
        assert _TURN_PARENT_SPAN_ID_CONTEXT.get() == "parent-9"
        assert controller._active_truthlog_recorder is scope.truthlog_recorder

    # Reset on exit.
    assert _TURN_REQUEST_ID_CONTEXT.get() is None
    assert _TURN_SPAN_ID_CONTEXT.get() is None
    assert _TURN_PARENT_SPAN_ID_CONTEXT.get() is None
    assert controller._active_truthlog_recorder is None


@pytest.mark.asyncio
async def test_scope_generates_request_id_when_none() -> None:
    controller = _controller()
    async with turn_session_scope(
        controller, turn_id="t", context=[], turn_request_id=None, parent_span_id=None
    ) as scope:
        assert scope.effective_turn_request_id
        assert _TURN_REQUEST_ID_CONTEXT.get() == scope.effective_turn_request_id


@pytest.mark.asyncio
async def test_scope_resets_contextvars_on_exception() -> None:
    controller = _controller()

    class _BoomError(RuntimeError):
        pass

    with pytest.raises(_BoomError):
        async with turn_session_scope(controller, turn_id="t", context=[], turn_request_id=None, parent_span_id=None):
            assert _TURN_REQUEST_ID_CONTEXT.get() is not None
            raise _BoomError()

    assert _TURN_REQUEST_ID_CONTEXT.get() is None
    assert _TURN_SPAN_ID_CONTEXT.get() is None
    assert _TURN_PARENT_SPAN_ID_CONTEXT.get() is None
    assert controller._active_truthlog_recorder is None
