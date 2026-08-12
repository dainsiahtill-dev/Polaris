# mypy: disable-error-code="attr-defined"
from __future__ import annotations

import logging
import time
from typing import Any

from polaris.cells.runtime.task_market.public.contracts import (
    TASK_REQUEUE_RECEIPTS_METADATA_KEY,
    MoveTaskToDeadLetterCommandV1,
    QueryTaskRequeueReceiptV1,
    RequeueTaskCommandV1,
    TaskRequeueReceiptV1,
    TaskWorkItemResultV1,
)

from ..dlq import DLQManager
from ..lease_manager import LeaseManager
from ..models import (
    now_iso,
)
from ._constants import (
    _REQUEUE_CONTEXT_PAYLOAD_KEYS,
)

logger = logging.getLogger(__name__.rsplit(".", 1)[0])


class RequeueDlqMixin:
    """Requeue, requeue receipts, and dead-letter moves."""

    # ---- Requeue -----------------------------------------------------------

    def requeue_task(self, command: RequeueTaskCommandV1) -> TaskWorkItemResultV1:
        t0 = time.monotonic()
        with self._workspace_lock(command.workspace):
            store = self._get_store(command.workspace)
            items = store.load_items()
            item = self._require_item(items, command.task_id)

            if command.idempotency_key:
                raw_receipts = dict(item.metadata).get(TASK_REQUEUE_RECEIPTS_METADATA_KEY)
                receipt_records = dict(raw_receipts) if isinstance(raw_receipts, dict) else {}
                existing_record = receipt_records.get(command.idempotency_key)
                if isinstance(existing_record, dict):
                    try:
                        existing_receipt = self._task_requeue_receipt_from_record(
                            workspace=command.workspace,
                            task_id=item.task_id,
                            record=existing_record,
                        )
                    except (TypeError, ValueError):
                        return self._result_from_item(item, ok=False, reason="idempotency_receipt_invalid")
                    if (
                        existing_receipt.idempotency_fingerprint != command.idempotency_fingerprint
                        or existing_receipt.effect_hash != command.effect_hash
                    ):
                        return self._result_from_item(item, ok=False, reason="idempotency_conflict")
                    return self._result_from_item(item, reason="already_requeued")

            if item.status in {"rejected", "dead_letter"}:
                return self._result_from_item(item, ok=False, reason="terminal_status")
            resolved_reopen_source = ""
            if item.status == "resolved":
                reopen_allowed, reopen_reason, resolved_reopen_source = self._resolved_reopen_allowed(item, command)
                if not reopen_allowed:
                    return self._result_from_item(item, ok=False, reason=reopen_reason)
            if item.status in {"completed", "cancelled"}:
                return self._result_from_item(item, ok=False, reason="unsupported_status")
            if item.status == "waiting_human":
                return self._result_from_item(item, ok=False, reason="waiting_human")

            lm = LeaseManager(store)
            if str(item.lease_token or "").strip() and not lm.is_lease_expired(item):
                return self._result_from_item(item, ok=False, reason="active_lease")

            previous_status = item.status
            previous_version = int(item.version)
            item.stage = command.target_stage
            item.status = command.target_stage
            lm.clear_lease(item)
            requeue_metadata = dict(command.metadata)
            item.metadata = dict(item.metadata)
            item.metadata["requeue_reason"] = command.reason
            item.metadata["requeue_metadata"] = requeue_metadata
            requeued_at = now_iso()
            item.metadata["requeued_at"] = requeued_at
            if previous_status == "resolved":
                item.metadata["reopen_count"] = self._safe_reopen_count(item.metadata) + 1
                if resolved_reopen_source:
                    item.metadata["last_reopen_source"] = resolved_reopen_source
            last_failure = requeue_metadata.get("last_failure")
            if isinstance(last_failure, dict):
                item.payload = {
                    **dict(item.payload),
                    "last_failure": dict(last_failure),
                }
            item.version += 1
            item.updated_at = now_iso()

            if command.idempotency_key:
                receipt = TaskRequeueReceiptV1(
                    workspace=command.workspace,
                    task_id=item.task_id,
                    idempotency_key=command.idempotency_key,
                    idempotency_fingerprint=command.idempotency_fingerprint,
                    effect_hash=command.effect_hash,
                    target_stage=command.target_stage,
                    reason=command.reason,
                    transition_version=item.version,
                    accepted_at=requeued_at,
                )
                raw_receipts = item.metadata.get(TASK_REQUEUE_RECEIPTS_METADATA_KEY)
                receipt_records = dict(raw_receipts) if isinstance(raw_receipts, dict) else {}
                receipt_records[command.idempotency_key] = {
                    "idempotency_key": receipt.idempotency_key,
                    "idempotency_fingerprint": receipt.idempotency_fingerprint,
                    "effect_hash": receipt.effect_hash,
                    "target_stage": receipt.target_stage,
                    "reason": receipt.reason,
                    "transition_version": receipt.transition_version,
                    "accepted_at": receipt.accepted_at,
                    "status": receipt.status,
                    "receipt_hash": receipt.receipt_hash,
                }
                item.metadata[TASK_REQUEUE_RECEIPTS_METADATA_KEY] = receipt_records

            items[item.task_id] = item

            transition = {
                "task_id": item.task_id,
                "from_status": previous_status,
                "to_status": item.status,
                "event_type": "requeued",
                "worker_id": "",
                "lease_token": "",
                "version": item.version,
                "metadata": {
                    "trace_id": item.trace_id,
                    "target_stage": command.target_stage,
                    "reason": command.reason,
                },
            }

            outbox = self._build_outbox_record(
                workspace=command.workspace,
                event_type="task_market.work_item_requeued",
                run_id=item.run_id,
                task_id=item.task_id,
                payload={
                    "trace_id": item.trace_id,
                    "target_stage": command.target_stage,
                    "reason": command.reason,
                },
            )
            lifecycle_evidence = self._record_cognitive_runtime_lifecycle_receipt(
                item=item,
                event_type="requeued",
                from_status=previous_status,
                to_status=item.status,
                metadata={
                    "target_stage": command.target_stage,
                    "reason": command.reason,
                    "requeue_metadata": dict(command.metadata),
                },
            )
            self._attach_lifecycle_evidence(
                item=item,
                transition=transition,
                outbox_record=outbox,
                evidence=lifecycle_evidence,
            )
            items[item.task_id] = item

            self._atomic_save_changed_items(
                store=store,
                items=items,
                transitions=[transition],
                outbox_records=[outbox],
                expected_versions={item.task_id: previous_version},
            )

            self._observe("requeue", (time.monotonic() - t0) * 1000.0, stage=command.target_stage, task_id=item.task_id)
            return self._result_from_item(item, reason="requeued")

    def query_task_requeue_receipt(
        self,
        query: QueryTaskRequeueReceiptV1,
    ) -> TaskRequeueReceiptV1 | None:
        """Return one durable idempotency receipt without changing task state."""

        if type(query) is not QueryTaskRequeueReceiptV1:
            raise TypeError("query must be an exact QueryTaskRequeueReceiptV1")
        with self._workspace_lock(query.workspace):
            store = self._get_store(query.workspace)
            item = store.load_items().get(query.task_id)
            if item is None:
                return None
            raw_receipts = dict(item.metadata).get(TASK_REQUEUE_RECEIPTS_METADATA_KEY)
            records = dict(raw_receipts) if isinstance(raw_receipts, dict) else {}
            record = records.get(query.idempotency_key)
            if not isinstance(record, dict):
                return None
            return self._task_requeue_receipt_from_record(
                workspace=query.workspace,
                task_id=query.task_id,
                record=record,
            )

    @staticmethod
    def _task_requeue_receipt_from_record(
        *,
        workspace: str,
        task_id: str,
        record: dict[str, Any],
    ) -> TaskRequeueReceiptV1:
        transition_version = record.get("transition_version")
        if type(transition_version) is not int:
            raise TypeError("stored task requeue transition_version must be an exact integer")
        receipt = TaskRequeueReceiptV1(
            workspace=workspace,
            task_id=task_id,
            idempotency_key=str(record.get("idempotency_key") or ""),
            idempotency_fingerprint=str(record.get("idempotency_fingerprint") or ""),
            effect_hash=str(record.get("effect_hash") or ""),
            target_stage=str(record.get("target_stage") or ""),
            reason=str(record.get("reason") or ""),
            transition_version=transition_version,
            accepted_at=str(record.get("accepted_at") or ""),
            status=str(record.get("status") or ""),
        )
        if str(record.get("receipt_hash") or "") != receipt.receipt_hash:
            raise ValueError("stored task requeue receipt hash mismatch")
        return receipt

    # ---- Dead Letter --------------------------------------------------------

    def move_task_to_dead_letter(self, command: MoveTaskToDeadLetterCommandV1) -> TaskWorkItemResultV1:
        t0 = time.monotonic()
        with self._workspace_lock(command.workspace):
            store = self._get_store(command.workspace)
            items = store.load_items()
            item = self._require_item(items, command.task_id)

            previous_status = item.status
            previous_version = int(item.version)
            dlq = DLQManager(store)
            dead_letter_record = dlq.move_to_dead_letter(
                item=item,
                reason=command.reason,
                error_code=str(command.error_code or "").strip(),
                metadata=dict(command.metadata),
                persist=False,
            )
            items[item.task_id] = item

            transition = {
                "task_id": item.task_id,
                "from_status": previous_status,
                "to_status": "dead_letter",
                "event_type": "dead_lettered",
                "worker_id": "",
                "lease_token": "",
                "version": item.version,
                "metadata": {
                    "trace_id": item.trace_id,
                    "reason": command.reason,
                    "error_code": command.error_code or "",
                },
            }

            outbox = self._build_outbox_record(
                workspace=command.workspace,
                event_type="task_market.work_item_dead_lettered",
                run_id=item.run_id,
                task_id=item.task_id,
                payload={
                    "trace_id": item.trace_id,
                    "reason": command.reason,
                    "error_code": command.error_code or "",
                },
            )
            lifecycle_evidence = self._record_cognitive_runtime_lifecycle_receipt(
                item=item,
                event_type="dead_lettered",
                from_status=previous_status,
                to_status="dead_letter",
                metadata={
                    "reason": command.reason,
                    "error_code": command.error_code or "",
                    "dead_letter_metadata": dict(command.metadata),
                },
            )
            self._attach_lifecycle_evidence(
                item=item,
                transition=transition,
                outbox_record=outbox,
                evidence=lifecycle_evidence,
            )
            items[item.task_id] = item

            self._atomic_save_changed_items(
                store=store,
                items=items,
                transitions=[transition],
                outbox_records=[outbox],
                expected_versions={item.task_id: previous_version},
                dead_letter_records=[dead_letter_record],
            )

            self._observe("dead_letter", (time.monotonic() - t0) * 1000.0, task_id=item.task_id)
            return self._result_from_item(item, reason="dead_lettered")

    @staticmethod
    def _requeue_context_payload(metadata: Any) -> dict[str, Any]:
        if not isinstance(metadata, dict):
            return {}
        payload: dict[str, Any] = {}
        for key in _REQUEUE_CONTEXT_PAYLOAD_KEYS:
            value = metadata.get(key)
            if value is None:
                continue
            payload[key] = value
        if not payload:
            return {}
        return {
            **payload,
            "requeue_context": {
                "schema_version": "task_market.requeue_context.v1",
                "keys": sorted(payload),
            },
        }
