"""Background reconciliation loop for parent/child task status convergence."""

from __future__ import annotations

import logging
import threading
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class _ReconcileService(Protocol):
    def reconcile_parent_statuses(self, workspace: str, *, limit: int = 5000) -> dict[str, object]: ...

    def sweep_escalation_timeouts(self, workspace: str) -> dict[str, Any]: ...

    def requeue_drifted_items(self, workspace: str) -> dict[str, Any]: ...


class TaskReconciliationLoop:
    """Periodic reconciliation loop.

    Runs ``TaskMarketService.reconcile_parent_statuses``,
    ``sweep_escalation_timeouts``, and ``requeue_drifted_items`` on a fixed
    interval to converge parent state, auto-escalate timed-out HITL reviews,
    and re-queue revision-drifted work items.
    """

    def __init__(
        self,
        *,
        service: _ReconcileService,
        workspace: str,
        interval_seconds: float = 30.0,
    ) -> None:
        self._service = service
        self._workspace = str(workspace or "").strip()
        if not self._workspace:
            raise ValueError("workspace must be a non-empty string")
        self._interval_seconds = max(1.0, float(interval_seconds))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> dict[str, object]:
        reconciliation = self._service.reconcile_parent_statuses(self._workspace)
        try:
            escalation = self._service.sweep_escalation_timeouts(self._workspace)
        except Exception as exc:  # noqa: BLE001
            logger.warning("escalation timeout sweep failed: %s", exc)
            escalation = {"escalated_count": 0}
        try:
            drift = self._service.requeue_drifted_items(self._workspace)
        except Exception as exc:  # noqa: BLE001
            logger.warning("drift requeue sweep failed: %s", exc)
            drift = {"requeued_count": 0}
        return {
            "reconciliation": reconciliation,
            "escalation_sweep": escalation,
            "drift_requeue": drift,
        }

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self, *, join_timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is None:
            return
        self._thread.join(timeout=max(0.1, float(join_timeout)))
        self._thread = None

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:
                logger.exception("task reconciliation loop failed: %s", exc)
            self._stop_event.wait(self._interval_seconds)


__all__ = ["TaskReconciliationLoop"]
