"""Shared transactional plumbing for the task-market service facade.

``ServiceBaseMixin`` carries the constructor, per-workspace locking, store
access, the atomic save helper, Cognitive Runtime receipt recording, metrics /
tracing instrumentation, and the outbox-record builders. Every behavioural
mixin (lifecycle, human-review, revision/saga, reconciliation) composes this
base; the bodies are moved verbatim from the original ``service.py`` so
behaviour is preserved exactly.

The module-level singleton + ``threading.Lock`` machinery deliberately lives in
``service.py`` (NOT here) so it stays single-instance.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING, Any

from polaris.cells.runtime.task_market.public.contracts import (
    TaskMarketError,
    TaskWorkItemResultV1,
)

from ._outbox import _stable_outbox_id
from .consumer_loop import ConsumerLoopManager
from .errors import TaskNotFoundError
from .fsm import get_fsm
from .metrics import get_task_market_metrics
from .models import (
    TaskWorkItemRecord,
    now_iso,
)
from .reconciler import TaskReconciliationLoop
from .store import get_store
from .tracing import get_task_market_tracer

logger = logging.getLogger(__name__)
_COGNITIVE_REQUIRED_KEYS = (
    "cognitive_runtime_required",
    "task_market_cognitive_runtime_required",
)
_CONTEXT_OS_EXPECTED_KEYS = (
    "context_os_expected",
    "task_market_context_os_expected",
)

__all__ = [
    "_COGNITIVE_REQUIRED_KEYS",
    "_CONTEXT_OS_EXPECTED_KEYS",
    "ServiceBaseMixin",
]


class ServiceBaseMixin:
    """Shared transactional plumbing shared by every task-market mixin."""

    if TYPE_CHECKING:
        # Surface provided by sibling mixins at runtime (resolved through the
        # composed ``TaskMarketService`` MRO). Declared type-only so each mixin
        # type-checks its cross-mixin calls without these declarations ever
        # shadowing the real implementations at runtime.
        def _maybe_start_reconciliation_loop(self, workspace: str) -> None: ...

        def _escalate_to_human_review_no_lock(
            self,
            *,
            workspace: str,
            store: Any,
            task_id: str,
            reason: str,
            requested_by: str,
        ) -> dict[str, Any]: ...

        def _compensate_task_no_lock(
            self,
            *,
            workspace: str,
            store: Any,
            items: dict[str, TaskWorkItemRecord],
            item: TaskWorkItemRecord,
            reason: str,
            initiator: str,
        ) -> dict[str, Any]: ...

        def _compensate_children_for_parent_failure(
            self,
            *,
            workspace: str,
            store: Any,
            items: dict[str, TaskWorkItemRecord],
            parent_task_id: str,
            reason: str,
        ) -> dict[str, Any]: ...

        @staticmethod
        def _strip_compensation_side_effects(summary: dict[str, Any]) -> dict[str, Any]: ...

        @staticmethod
        def _collect_compensation_transitions(summary: dict[str, Any]) -> list[dict[str, Any]]: ...

        @staticmethod
        def _collect_compensation_outbox(summary: dict[str, Any]) -> list[dict[str, Any]]: ...

        def reconcile_parent_statuses(self, workspace: str, *, limit: int = 5000) -> dict[str, Any]: ...

        def requeue_drifted_items(self, workspace: str) -> dict[str, Any]: ...

        def sweep_escalation_timeouts(self, workspace: str) -> dict[str, Any]: ...

        def relay_outbox_messages(self, workspace: str, *, limit: int = 200) -> dict[str, Any]: ...

    def __init__(self) -> None:
        self._workspace_locks: dict[str, threading.Lock] = {}
        self._workspace_locks_guard = threading.Lock()
        self._reconciliation_loops: dict[str, TaskReconciliationLoop] = {}
        self._reconciliation_loops_guard = threading.Lock()
        self._consumer_loop_managers: dict[str, ConsumerLoopManager] = {}
        self._consumer_loop_managers_guard = threading.Lock()
        self._auto_reconciliation_enabled = self._read_bool_env(
            "KERNELONE_TASK_MARKET_ENABLE_RECONCILIATION_LOOP",
            default=False,
        )
        self._auto_reconciliation_interval_seconds = self._read_float_env(
            "KERNELONE_TASK_MARKET_RECONCILIATION_INTERVAL_SECONDS",
            default=30.0,
            min_value=1.0,
        )
        self._fsm = get_fsm()
        self._metrics = get_task_market_metrics()
        self._tracer = get_task_market_tracer()

    def _workspace_lock(self, workspace: str) -> threading.Lock:
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise TaskMarketError("workspace is required", code="workspace_required")
        with self._workspace_locks_guard:
            lock = self._workspace_locks.get(workspace_token)
            if lock is None:
                lock = threading.Lock()
                self._workspace_locks[workspace_token] = lock
            return lock

    @staticmethod
    def _read_bool_env(name: str, *, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        normalized = str(raw).strip().lower()
        if not normalized:
            return default
        return normalized in {"1", "true", "yes", "on"}

    @staticmethod
    def _read_float_env(name: str, *, default: float, min_value: float) -> float:
        raw = os.getenv(name)
        if raw is None:
            return max(min_value, float(default))
        try:
            parsed = float(str(raw).strip())
        except (TypeError, ValueError):
            return max(min_value, float(default))
        return max(min_value, parsed)

    # ---- Store access -------------------------------------------------------

    def _get_store(self, workspace: str) -> Any:
        """Return the appropriate store backend (lazy)."""
        return get_store(workspace)

    @staticmethod
    def _atomic_save_changed_items(
        *,
        store: Any,
        items: dict[str, TaskWorkItemRecord],
        transitions: list[dict[str, Any]],
        outbox_records: list[dict[str, Any]],
        expected_versions: dict[str, int],
        dead_letter_records: list[dict[str, Any]] | None = None,
        human_review_records: list[dict[str, Any]] | None = None,
    ) -> None:
        """Persist only rows whose read-version baseline is known."""
        expected = {str(task_id): int(version) for task_id, version in expected_versions.items()}
        changed_items = {task_id: items[task_id] for task_id in expected if task_id in items}
        store.save_items_and_outbox_atomic(
            items=changed_items,
            transitions=transitions,
            outbox_records=outbox_records,
            expected_versions=expected,
            dead_letter_records=dead_letter_records or [],
            human_review_records=human_review_records or [],
        )

    @staticmethod
    def _coerce_optional_bool(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            token = value.strip().lower()
            if token in {"1", "true", "yes", "y", "on", "required"}:
                return True
            if token in {"0", "false", "no", "n", "off", "optional", "disabled"}:
                return False
        return None

    @classmethod
    def _payload_flag(cls, *payloads: dict[str, Any], keys: tuple[str, ...]) -> bool:
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            for key in keys:
                if key not in payload:
                    continue
                coerced = cls._coerce_optional_bool(payload.get(key))
                if coerced is not None:
                    return coerced
            metadata = payload.get("metadata")
            if isinstance(metadata, dict):
                for key in keys:
                    if key not in metadata:
                        continue
                    coerced = cls._coerce_optional_bool(metadata.get(key))
                    if coerced is not None:
                        return coerced
        return False

    @staticmethod
    def _task_market_session_id(item: TaskWorkItemRecord) -> str:
        for payload in (item.payload, item.metadata):
            if not isinstance(payload, dict):
                continue
            for key in ("task_market_session_id", "role_session_id", "session_id"):
                token = str(payload.get(key) or "").strip()
                if token:
                    return token
        basis = str(item.run_id or item.task_id or "").strip()
        return f"task-market-{basis}"

    def _record_cognitive_runtime_lifecycle_receipt(
        self,
        *,
        item: TaskWorkItemRecord,
        event_type: str,
        from_status: str,
        to_status: str,
        worker_id: str = "",
        lease_token: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a Cognitive Runtime receipt for a task-market lifecycle event."""

        event_metadata = dict(metadata or {})
        required = self._payload_flag(
            item.payload,
            item.metadata,
            event_metadata,
            keys=_COGNITIVE_REQUIRED_KEYS,
        )
        context_os_expected = self._payload_flag(
            item.payload,
            item.metadata,
            event_metadata,
            keys=_CONTEXT_OS_EXPECTED_KEYS,
        )
        evidence: dict[str, Any] = {
            "source": "runtime.task_market",
            "event_type": event_type,
            "required": required,
            "receipt_recorded": False,
            "context_os_expected": context_os_expected,
        }
        try:
            from polaris.cells.factory.cognitive_runtime.public import (
                RecordRuntimeReceiptCommandV1,
                get_cognitive_runtime_public_service,
            )

            service = get_cognitive_runtime_public_service()
            try:
                result = service.record_runtime_receipt(
                    RecordRuntimeReceiptCommandV1(
                        workspace=item.workspace,
                        receipt_type="task_market_lifecycle",
                        payload={
                            "event_type": event_type,
                            "task_id": item.task_id,
                            "trace_id": item.trace_id,
                            "run_id": item.run_id,
                            "stage": item.stage,
                            "from_status": from_status,
                            "to_status": to_status,
                            "worker_id": worker_id,
                            "lease_token_present": bool(str(lease_token or "").strip()),
                            "plan_id": item.plan_id,
                            "plan_revision_id": item.plan_revision_id,
                            "root_task_id": item.root_task_id or item.task_id,
                            "parent_task_id": item.parent_task_id,
                            "context_os_expected": context_os_expected,
                            "metadata": event_metadata,
                        },
                        session_id=self._task_market_session_id(item),
                        run_id=item.run_id or None,
                        trace_refs=(item.trace_id,) if item.trace_id else (),
                        turn_envelope={
                            "source": "runtime.task_market",
                            "event_type": event_type,
                            "task_id": item.task_id,
                            "from_status": from_status,
                            "to_status": to_status,
                        },
                    )
                )
            finally:
                service.close()
        except (ImportError, RuntimeError, ValueError) as exc:
            evidence["error_message"] = str(exc)
            if required:
                raise TaskMarketError(
                    f"Cognitive Runtime receipt failed for task-market event {event_type}: {exc}",
                    code="cognitive_runtime_receipt_failed",
                    details={"task_id": item.task_id, "event_type": event_type},
                ) from exc
            return evidence

        if not bool(getattr(result, "ok", False)):
            error_message = str(getattr(result, "error_message", "") or "").strip()
            error_code = str(getattr(result, "error_code", "") or "").strip()
            evidence["error_code"] = error_code
            evidence["error_message"] = error_message
            if required:
                raise TaskMarketError(
                    error_message or error_code or "cognitive_runtime_receipt_failed",
                    code="cognitive_runtime_receipt_failed",
                    details={"task_id": item.task_id, "event_type": event_type},
                )
            return evidence

        receipt = getattr(result, "receipt", None)
        receipt_id = str(getattr(receipt, "receipt_id", "") or "").strip()
        evidence["receipt_recorded"] = bool(receipt_id)
        if receipt_id:
            evidence["receipt_id"] = receipt_id
        if required and not receipt_id:
            raise TaskMarketError(
                "Cognitive Runtime receipt did not return a receipt id",
                code="cognitive_runtime_receipt_missing_id",
                details={"task_id": item.task_id, "event_type": event_type},
            )
        return evidence

    @staticmethod
    def _attach_lifecycle_evidence(
        *,
        item: TaskWorkItemRecord,
        transition: dict[str, Any],
        outbox_record: dict[str, Any],
        evidence: dict[str, Any],
    ) -> None:
        item.metadata = dict(item.metadata)
        item.metadata["last_cognitive_runtime_lifecycle"] = dict(evidence)
        receipt_id = str(evidence.get("receipt_id") or "").strip()
        if receipt_id:
            existing = item.metadata.get("cognitive_runtime_receipt_ids")
            receipt_ids = [str(row) for row in existing] if isinstance(existing, list) else []
            if receipt_id not in receipt_ids:
                receipt_ids.append(receipt_id)
            item.metadata["cognitive_runtime_receipt_ids"] = receipt_ids

        transition_metadata = transition.get("metadata")
        if not isinstance(transition_metadata, dict):
            transition_metadata = {}
            transition["metadata"] = transition_metadata
        transition_metadata["cognitive_runtime"] = dict(evidence)

        payload = outbox_record.get("payload")
        if not isinstance(payload, dict):
            payload = {}
            outbox_record["payload"] = payload
        payload["cognitive_runtime"] = dict(evidence)

    # ---- Instrumentation / shared helpers ----------------------------------

    def _observe(
        self,
        operation: str,
        duration_ms: float,
        *,
        stage: str = "",
        ok: bool = True,
        task_id: str = "",
        trace_id: str = "",
    ) -> None:
        """Record operation metrics, structured logging, and OTel span."""
        self._metrics.record_operation(operation, duration_ms, stage=stage, ok=ok)
        logger.info(
            "task_market %s: task_id=%s stage=%s trace_id=%s ok=%s duration_ms=%.1f",
            operation,
            task_id,
            stage,
            trace_id,
            ok,
            duration_ms,
        )
        # OTel span — records operation as a span event on the current span
        # (if a parent span exists) or as a standalone span.
        if self._tracer.enabled:
            with self._tracer.start_span(
                f"task_market.{operation}",
                {
                    "task_id": task_id,
                    "stage": stage,
                    "trace_id": trace_id,
                    "ok": str(ok),
                    "duration_ms": duration_ms,
                },
            ):
                pass  # Span is opened and immediately closed — records the event.

    def _require_item(self, items: dict[str, TaskWorkItemRecord], task_id: str) -> TaskWorkItemRecord:
        item = items.get(str(task_id or "").strip())
        if item is None:
            raise TaskNotFoundError(
                f"Task not found: {task_id}",
                task_id=task_id,
            )
        return item

    def _result_from_item(
        self,
        item: TaskWorkItemRecord,
        *,
        ok: bool = True,
        lease_token: str = "",
        reason: str = "",
    ) -> TaskWorkItemResultV1:
        return TaskWorkItemResultV1(
            ok=ok,
            task_id=item.task_id,
            stage=item.stage,
            status=item.status,
            version=item.version,
            trace_id=item.trace_id,
            run_id=item.run_id,
            lease_token=lease_token or item.lease_token,
            reason=reason,
            payload=item.payload,
        )

    def _maybe_emit_webhook(
        self,
        *,
        workspace: str,
        run_id: str,
        task_id: str,
        action: str,
        callback_url: str,
        current_role: str,
        review: dict[str, Any],
    ) -> None:
        """Emit a webhook outbox record if callback_url is provided."""
        url = str(callback_url or "").strip()
        if not url:
            return
        outbox = self._build_outbox_record(
            workspace=workspace,
            event_type="task_market.human_review_callback",
            run_id=run_id,
            task_id=task_id,
            payload={
                "callback_url": url,
                "task_id": task_id,
                "action": action,
                "current_role": current_role,
                "review": review,
            },
        )
        try:
            store = self._get_store(workspace)
            store.append_outbox_message(outbox)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning(
                "task_market webhook outbox append failed: task_id=%s action=%s error=%s",
                task_id,
                action,
                exc,
            )

    def _build_outbox_record(
        self,
        *,
        workspace: str,
        event_type: str,
        run_id: str,
        task_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Build an outbox record for later atomic write or direct append."""
        return {
            "outbox_id": _stable_outbox_id(
                workspace=workspace,
                stream="task_market.events",
                event_type=event_type,
                run_id=run_id,
                task_id=task_id,
                payload=payload,
            ),
            "workspace": workspace,
            "stream": "task_market.events",
            "event_type": event_type,
            "source": "runtime.task_market",
            "run_id": run_id,
            "task_id": task_id,
            "payload": dict(payload),
            "status": "pending",
            "attempts": 0,
            "last_error": "",
            "created_at": now_iso(),
            "failed_at": "",
            "delivered_at": "",
        }

    def _emit_fact(
        self,
        *,
        workspace: str,
        event_type: str,
        run_id: str,
        task_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Write an outbox record for async fact_stream delivery via relay.

        This method does NOT call append_fact_event inline - that would violate
        the outbox_atomic fitness rule. The outbox record is written to the store,
        and a relay process handles delivery to fact_stream.
        """
        outbox_id = _stable_outbox_id(
            workspace=workspace,
            stream="task_market.events",
            event_type=event_type,
            run_id=run_id,
            task_id=task_id,
            payload=payload,
        )
        outbox_record: dict[str, Any] = {
            "outbox_id": outbox_id,
            "workspace": workspace,
            "stream": "task_market.events",
            "event_type": event_type,
            "source": "runtime.task_market",
            "run_id": run_id,
            "task_id": task_id,
            "payload": dict(payload),
            "status": "pending",
            "attempts": 0,
            "last_error": "",
            "created_at": now_iso(),
            "failed_at": "",
            "delivered_at": "",
        }
        try:
            store = self._get_store(workspace)
            store.append_outbox_message(outbox_record)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning(
                "task_market outbox append failed: event_type=%s task_id=%s outbox_id=%s error=%s",
                event_type,
                task_id,
                outbox_id,
                exc,
            )
            return
