"""Shared per-turn session scope for the transaction kernel.

UTF-8 编码验证: 本文所有文本使用 UTF-8。

Extracted verbatim (behavior-preserving) from the ~95%-duplicated session /
correlation / truthlog boilerplate at the head and tail of
``TurnTransactionController.execute`` and ``.execute_stream`` per the
REMAINING_06 decomposition blueprint (step 6).

``turn_session_scope`` is an async context manager that performs, in the exact
original order:

1. construct the per-turn :class:`TurnStateMachine` and :class:`TurnLedger`;
2. restore (or persist) the session-level :class:`PhaseManager` and
   :class:`ModificationContract` so phase/contract survive across turns
   (FIX-20250422 / FIX-20250422-v3);
3. resolve the effective correlation request/span ids and set the three
   correlation ``ContextVar`` tokens;
4. build the per-turn TruthLog recorder and publish it as the controller's
   ``_active_truthlog_recorder`` (so callback-path ``_emit_phase_event`` can
   record fire-and-forget events).

On exit it clears ``_active_truthlog_recorder``, flushes/shuts down the
recorder, and resets the three correlation ``ContextVar``s, each guarded by the
original ``contextlib.suppress(ValueError)`` so a contextvar reset in the wrong
context never masks the turn outcome.

The controller is passed in so the scope can read/write the controller's
session-level state and call the existing monkeypatchable controller helpers
exactly as the inline code did. Only one small frozen
:class:`TurnSessionScope` value is allocated per turn.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.transaction.correlation import (
    _TURN_PARENT_SPAN_ID_CONTEXT,
    _TURN_REQUEST_ID_CONTEXT,
    _TURN_SPAN_ID_CONTEXT,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.truthlog_recorder import TurnTruthLogRecorder
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnStateMachine

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.turn_transaction_controller import (
        TurnTransactionController,
    )

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnSessionScope:
    """Prepared per-turn execution scope shared by execute / execute_stream."""

    state_machine: TurnStateMachine
    ledger: TurnLedger
    effective_turn_request_id: str
    effective_turn_span_id: str
    truthlog_recorder: TurnTruthLogRecorder | None


@asynccontextmanager
async def turn_session_scope(
    controller: TurnTransactionController,
    *,
    turn_id: str,
    context: list[dict[str, Any]],
    turn_request_id: str | None,
    parent_span_id: str | None,
) -> AsyncIterator[TurnSessionScope]:
    """Open the shared per-turn session scope.

    Mirrors the original inline setup/teardown in ``execute`` /
    ``execute_stream`` byte-for-byte; ``turn_request_id`` is ``None`` for the
    non-stream path (a fresh id is generated) and ``parent_span_id`` is ``None``
    there as well.
    """
    state_machine = TurnStateMachine(turn_id=turn_id)
    ledger = TurnLedger(turn_id=turn_id)
    # FIX-20250422: Restore session-level PhaseManager so phase survives across turns
    if controller._session_phase_manager is not None:
        _restored_phase = controller._session_phase_manager.current_phase.value
        ledger.phase_manager = controller._session_phase_manager
        logger.debug(
            "[DEBUG][FIX-20250422] PhaseManager restored from session: phase=%s turn_id=%s",
            _restored_phase,
            turn_id,
        )
    else:
        controller._session_phase_manager = ledger.phase_manager
        logger.debug(
            "[DEBUG][FIX-20250422] PhaseManager created fresh: phase=%s turn_id=%s",
            ledger.phase_manager.current_phase.value,
            turn_id,
        )

    # FIX-20250422-v3: Restore session-level ModificationContract
    if controller._session_modification_contract is not None:
        ledger.modification_contract = controller._session_modification_contract
    else:
        controller._session_modification_contract = ledger.modification_contract

    effective_turn_request_id = turn_request_id or controller._generate_turn_request_id()
    effective_turn_span_id = controller._generate_span_id(prefix="turnspan")
    request_id_token = _TURN_REQUEST_ID_CONTEXT.set(effective_turn_request_id)
    span_id_token = _TURN_SPAN_ID_CONTEXT.set(effective_turn_span_id)
    parent_span_token = _TURN_PARENT_SPAN_ID_CONTEXT.set(parent_span_id)
    truthlog_recorder = controller._build_turn_truthlog_recorder(context)
    controller._active_truthlog_recorder = truthlog_recorder
    try:
        yield TurnSessionScope(
            state_machine=state_machine,
            ledger=ledger,
            effective_turn_request_id=effective_turn_request_id,
            effective_turn_span_id=effective_turn_span_id,
            truthlog_recorder=truthlog_recorder,
        )
    finally:
        controller._active_truthlog_recorder = None
        if truthlog_recorder is not None:
            await controller._shutdown_turn_truthlog_recorder(truthlog_recorder)
        with contextlib.suppress(ValueError):
            _TURN_REQUEST_ID_CONTEXT.reset(request_id_token)
        with contextlib.suppress(ValueError):
            _TURN_SPAN_ID_CONTEXT.reset(span_id_token)
        with contextlib.suppress(ValueError):
            _TURN_PARENT_SPAN_ID_CONTEXT.reset(parent_span_token)
