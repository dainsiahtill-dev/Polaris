"""Audit store recovery tests for persisted KernelAuditEvent facts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from polaris.infrastructure.audit.stores.audit_store import AuditStore
from polaris.kernelone.audit.contracts import KernelAuditEvent, KernelAuditEventType


def _event_hash(event: KernelAuditEvent) -> str:
    content = json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _audit_event(event_id: str, timestamp: str) -> KernelAuditEvent:
    return KernelAuditEvent(
        event_id=event_id,
        timestamp=datetime.fromisoformat(timestamp),
        event_type=KernelAuditEventType.TASK_START,
        source={"role": "test"},
        task={"task_id": event_id},
        resource={},
        action={"result": "success"},
        data={},
        context={},
    )


def test_audit_store_load_last_hash_ignores_malformed_tail_event(tmp_path: Path) -> None:
    first_store = AuditStore(tmp_path, secret_key="test-secret")
    first_event = first_store.append(_audit_event("event-1", "2026-07-01T00:00:00+00:00"))
    expected_prev_hash = _event_hash(first_event)

    corrupt_tail = first_event.to_dict()
    corrupt_tail.pop("event_id")
    log_file = first_store.get_log_file_path()
    with open(log_file, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(corrupt_tail, ensure_ascii=False) + "\n")

    reloaded_store = AuditStore(tmp_path, secret_key="test-secret")
    second_event = reloaded_store.append(_audit_event("event-2", "2026-07-01T00:00:01+00:00"))

    assert second_event.prev_hash == expected_prev_hash
    assert [event.event_id for event in reloaded_store.query(limit=10)] == ["event-2", "event-1"]
