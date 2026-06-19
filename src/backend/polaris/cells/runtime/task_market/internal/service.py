"""Service facade for ``runtime.task_market`` — composes responsibility mixins.

This module remains the canonical entry point. ``TaskMarketService`` is built by
composing focused mixins extracted into sibling ``_service_*`` modules; the
public surface (every previously importable name, the module-level singleton,
its ``threading.Lock`` and accessors) is preserved here verbatim so the import
path resolves identically:

- ``ServiceBaseMixin`` (``_service_base``) — shared transactional plumbing
- ``LifecycleMixin`` (``_service_lifecycle``) — lease / stage transactions
- ``HumanReviewMixin`` (``_service_human_review``) — HITL / Tri-Council
- ``RevisionSagaMixin`` (``_service_revision_saga``) — plan-revision /
  change-order / saga compensation
- ``ReconciliationMixin`` (``_service_reconciliation``) — background
  convergence and loop lifecycle
- ``claim_readiness`` — pure, stateless claim-readiness free functions

``relay_outbox_messages`` is defined here (not in a mixin) so the relay resolves
``append_fact_event`` from this module's namespace — the test suite patches
``...internal.service.append_fact_event``.

The store backend (JSON or SQLite) is selected via
``KERNELONE_TASK_MARKET_STORE`` or the ``get_store()`` factory.
"""

from __future__ import annotations

import hashlib as hashlib
import json as json
import logging
import os as os
import threading
import time
from collections import Counter as Counter
from datetime import (
    datetime as datetime,
    timezone as timezone,
)
from typing import Any

from polaris.cells.events.fact_stream.public.contracts import AppendFactEventCommandV1
from polaris.cells.events.fact_stream.public.service import append_fact_event

# Public-contract re-exports (stable surface; consumed by callers/tests).
from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1 as AcknowledgeTaskStageCommandV1,
    ChangeOrderResultV1 as ChangeOrderResultV1,
    ClaimTaskWorkItemCommandV1 as ClaimTaskWorkItemCommandV1,
    FailTaskStageCommandV1 as FailTaskStageCommandV1,
    HumanReviewResultV1 as HumanReviewResultV1,
    MoveTaskToDeadLetterCommandV1 as MoveTaskToDeadLetterCommandV1,
    PlanRevisionResultV1 as PlanRevisionResultV1,
    PublishTaskWorkItemCommandV1 as PublishTaskWorkItemCommandV1,
    QueryChangeOrdersV1 as QueryChangeOrdersV1,
    QueryPendingHumanReviewsV1 as QueryPendingHumanReviewsV1,
    QueryPlanRevisionsV1 as QueryPlanRevisionsV1,
    QueryTaskMarketStatusV1 as QueryTaskMarketStatusV1,
    RegisterPlanRevisionCommandV1 as RegisterPlanRevisionCommandV1,
    RenewTaskLeaseCommandV1 as RenewTaskLeaseCommandV1,
    RequestHumanReviewCommandV1 as RequestHumanReviewCommandV1,
    RequeueTaskCommandV1 as RequeueTaskCommandV1,
    ResolveHumanReviewCommandV1 as ResolveHumanReviewCommandV1,
    SubmitChangeOrderCommandV1 as SubmitChangeOrderCommandV1,
    TaskLeaseRenewResultV1 as TaskLeaseRenewResultV1,
    TaskMarketError,
    TaskMarketStatusResultV1 as TaskMarketStatusResultV1,
    TaskWorkItemResultV1 as TaskWorkItemResultV1,
)

from ._outbox import _stable_outbox_id as _stable_outbox_id
from ._service_base import (
    _COGNITIVE_REQUIRED_KEYS as _COGNITIVE_REQUIRED_KEYS,
    _CONTEXT_OS_EXPECTED_KEYS as _CONTEXT_OS_EXPECTED_KEYS,
    ServiceBaseMixin,
)
from ._service_human_review import HumanReviewMixin
from ._service_lifecycle import (
    _DEPENDENCY_TERMINAL_FAILURE_STATUSES as _DEPENDENCY_TERMINAL_FAILURE_STATUSES,
    _IN_PROGRESS_STATUSES as _IN_PROGRESS_STATUSES,
    _NON_CONSUMING_REQUEUE_ERROR_CODES as _NON_CONSUMING_REQUEUE_ERROR_CODES,
    LifecycleMixin,
)
from ._service_reconciliation import (
    _DESIGN_STATUS_SET as _DESIGN_STATUS_SET,
    _EXECUTION_STATUS_SET as _EXECUTION_STATUS_SET,
    _QA_STATUS_SET as _QA_STATUS_SET,
    ReconciliationMixin,
)
from ._service_revision_saga import RevisionSagaMixin
from .consumer_loop import ConsumerLoopManager as ConsumerLoopManager
from .dlq import DLQManager as DLQManager
from .errors import (
    StaleLeaseTokenError as StaleLeaseTokenError,
    TaskMarketError as InternalTaskMarketError,  # noqa: F401 — re-export
    TaskNotClaimableError as TaskNotClaimableError,
    TaskNotFoundError as TaskNotFoundError,
)
from .fsm import PRIORITY_WEIGHT as PRIORITY_WEIGHT, get_fsm as get_fsm
from .human_review import (
    RESOLUTION_TO_STAGE as RESOLUTION_TO_STAGE,
    HumanReviewManager as HumanReviewManager,
    get_next_escalation_role as get_next_escalation_role,
)
from .lease_manager import LeaseManager as LeaseManager
from .metrics import get_task_market_metrics as get_task_market_metrics
from .models import (
    TERMINAL_STATUSES as TERMINAL_STATUSES,
    TaskWorkItemRecord as TaskWorkItemRecord,
    now_epoch as now_epoch,
    now_iso,
)
from .reconciler import TaskReconciliationLoop as TaskReconciliationLoop
from .saga import CompensationAction as CompensationAction, SagaCompensator as SagaCompensator
from .store import get_store as get_store
from .tracing import get_task_market_tracer as get_task_market_tracer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class TaskMarketService(
    LifecycleMixin,
    RevisionSagaMixin,
    HumanReviewMixin,
    ReconciliationMixin,
    ServiceBaseMixin,
):
    """Task market service with lease-aware stage transitions.

    This implementation composes focused responsibility mixins and delegates
    to internal modules:
    - ``LeaseManager`` — lease lifecycle (grant / renew / validate)
    - ``DLQManager`` — dead-letter queue management
    - ``HumanReviewManager`` — WAITING_HUMAN / HITL management
    - ``TaskStageFSM`` — state transition validation

    The store backend (JSON or SQLite) is selected via
    ``KERNELONE_TASK_MARKET_STORE`` or the ``get_store()`` factory.
    """

    # ---- Outbox Relay -------------------------------------------------------

    def relay_outbox_messages(self, workspace: str, *, limit: int = 200) -> dict[str, Any]:
        t0 = time.monotonic()
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise TaskMarketError("workspace is required", code="workspace_required")
        max_limit = max(1, int(limit))
        with self._workspace_lock(workspace_token):
            store = self._get_store(workspace_token)
            rows = store.load_outbox_messages(
                workspace_token,
                statuses=("pending", "failed"),
                limit=max_limit,
            )

            sent = 0
            failed = 0
            sent_outbox_ids: list[str] = []
            failed_outbox_ids: list[str] = []
            for row in rows:
                outbox_id = str(row.get("outbox_id") or "").strip()
                if not outbox_id:
                    continue
                payload_raw = row.get("payload")
                payload = dict(payload_raw) if isinstance(payload_raw, dict) else {}
                try:
                    append_fact_event(
                        AppendFactEventCommandV1(
                            workspace=workspace_token,
                            stream=str(row.get("stream") or "task_market.events").strip() or "task_market.events",
                            event_type=str(row.get("event_type") or "").strip(),
                            source=str(row.get("source") or "runtime.task_market").strip() or "runtime.task_market",
                            run_id=str(row.get("run_id") or "").strip(),
                            task_id=str(row.get("task_id") or "").strip(),
                            payload=payload,
                            idempotency_key=outbox_id,
                        )
                    )
                    store.mark_outbox_message_sent(
                        workspace_token,
                        outbox_id,
                        delivered_at=now_iso(),
                    )
                    sent += 1
                    sent_outbox_ids.append(outbox_id)
                except (OSError, RuntimeError, ValueError) as exc:
                    store.mark_outbox_message_failed(
                        workspace_token,
                        outbox_id,
                        error_message=str(exc),
                        failed_at=now_iso(),
                    )
                    failed += 1
                    failed_outbox_ids.append(outbox_id)

            self._metrics.record_outbox_relay(sent=sent, failed=failed)
            self._observe("outbox_relay", (time.monotonic() - t0) * 1000.0)
            return {
                "workspace": workspace_token,
                "scanned": len(rows),
                "sent": sent,
                "failed": failed,
                "sent_outbox_ids": tuple(sent_outbox_ids),
                "failed_outbox_ids": tuple(failed_outbox_ids),
            }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_service_lock = threading.Lock()
_service_singleton: TaskMarketService | None = None


def get_task_market_service() -> TaskMarketService:
    global _service_singleton
    if _service_singleton is not None:
        return _service_singleton
    with _service_lock:
        if _service_singleton is None:
            _service_singleton = TaskMarketService()
        return _service_singleton


def reset_task_market_service() -> None:
    global _service_singleton
    with _service_lock:
        singleton = _service_singleton
        _service_singleton = None
    if singleton is not None:
        singleton.stop_all_consumer_loops()
        singleton.stop_all_reconciliation_loops()


__all__ = [
    "TaskMarketService",
    "get_task_market_service",
    "reset_task_market_service",
]
