"""Private helpers, constants, and fence types for FactoryRunService.

Private implementation module of the factory_run_service package.
"""

from __future__ import annotations

import contextlib
import logging
import threading  # re-exported for lossless surface compatibility
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING  # Protocol re-exported for lossless surface

from polaris.cells.roles.kernel.public.provider_attempt_lifecycle_replay import (
    AppendFactoryProviderAttemptRecoveryTerminalV1,
)

from ..factory_physical_attempt_replay import (
    FactoryPhysicalAttemptReplayError,
)
from ..factory_role_evidence_authority import (
    FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY,
)

if TYPE_CHECKING:
    from collections.abc import Callable


class _FactoryStageCancellationCutError(RuntimeError):
    """Internal cut proving outer cancellation won before marker append."""


class _FactoryStageCommitArbitration:
    """One shared linearization point for cancellation and marker durability."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled = False

    @contextlib.contextmanager
    def commit_permit(self) -> Iterator[None]:
        """Hold the permit across marker fsync and strict post-append reread."""

        with self._lock:
            if self._cancelled:
                raise _FactoryStageCancellationCutError(
                    "outer cancellation cut won before authoritative marker durability"
                )
            yield

    def mark_cancelled(self) -> None:
        """Linearize cancellation without ever blocking the asyncio event loop."""

        with self._lock:
            self._cancelled = True


logger = logging.getLogger("polaris.cells.factory.pipeline.internal.factory_run_service")

_WORKSPACE_LEASE_METADATA_KEY = "factory_workspace_run_lease"

_STAGE_IN_FLIGHT_METADATA_KEY = "factory_stage_in_flight"

_FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY = FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY

_CHILD_SESSIONS_SETTLED_METADATA_KEY = "factory_child_sessions_settled"

_CHILD_SESSION_SETTLEMENT_EVIDENCE_METADATA_KEY = "factory_child_session_settlement_evidence"

_AUTOMATIC_ROUTER_MUTATION_GUARD_MATRIX: dict[str, tuple[str, ...]] = {
    "summary_projection": ("store.save_run",),
    "chief_engineer_local_rework": (
        "store.save_run",
        "_append_event",
        "reconcile_stage_execution_for_reentry",
    ),
    "chief_engineer_local_rework_reentry": ("reconcile_stage_execution_for_reentry",),
    "director_local_rework": ("store.save_run", "_append_event", "reconcile_stage_execution_for_reentry"),
    "director_local_rework_reentry": ("reconcile_stage_execution_for_reentry",),
    "quality_rework": ("store.save_run", "_append_event", "reconcile_stage_execution_for_reentry"),
    "quality_rework_reentry": ("reconcile_stage_execution_for_reentry",),
    "stage_sequence": ("execute_stage",),
    "run_configuration": ("store.save_run",),
    "delivery_loop_projection": ("store.save_run", "_append_event"),
    "success_terminalization": ("_persist_run_summary", "complete_run"),
    "failure_terminalization": (
        "reconcile_stage_execution_for_reentry",
        "store.save_run",
        "_persist_run_summary",
        "_append_event",
        "complete_run",
    ),
    "factory_failure_terminalization": ("reconcile_stage_execution_for_reentry",),
}

_FACTORY_FANOUT_MAX_PAYLOAD_BYTES = 200_000


class _FactoryProviderAttemptRecoveryFence:
    """Private non-serializable capability held under Factory admission lock."""

    verification_scope = "factory"

    def __init__(self, *, factory_run_id: str, revalidate: Callable[[], None]) -> None:
        self.factory_run_id = str(factory_run_id or "").strip()
        if not self.factory_run_id:
            raise ValueError("factory_run_id_missing")
        self._revalidate = revalidate

    @contextlib.contextmanager
    def hold_recovery_terminal(
        self,
        command: AppendFactoryProviderAttemptRecoveryTerminalV1,
    ) -> Iterator[Callable[[], None]]:
        if command.attempt.factory_run_id != self.factory_run_id:
            raise FactoryPhysicalAttemptReplayError("factory_provider_attempt_recovery_fence_scope_mismatch")
        self._revalidate()
        yield self._revalidate
        self._revalidate()
