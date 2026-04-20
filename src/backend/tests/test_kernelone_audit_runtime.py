from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.audit import (
    KernelAuditEvent,
    KernelAuditEventType,
    KernelAuditRole,
    KernelAuditRuntime,
    KernelAuditWriteError,
)
from polaris.kernelone.audit.contracts import (
    GENESIS_HASH,
    KernelChainVerificationResult,
)


class _InMemoryAuditStore:
    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = runtime_root
        self.events: list[KernelAuditEvent] = []

    def append(self, event: KernelAuditEvent) -> KernelAuditEvent:
        stored = replace(
            event,
            source=dict(event.source),
            task=dict(event.task),
            resource=dict(event.resource),
            action=dict(event.action),
            data=dict(event.data),
            context=dict(event.context),
        )
        self.events.append(stored)
        return stored

    def query(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        event_type: KernelAuditEventType | None = None,
        role: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[KernelAuditEvent]:
        del start_time, end_time, event_type, role, task_id
        rows = list(reversed(self.events))
        return rows[offset : offset + limit]

    def export_json(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {}

    def export_csv(self, **kwargs: Any) -> str:
        del kwargs
        return ""

    def verify_chain(self) -> KernelChainVerificationResult:
        return KernelChainVerificationResult(
            is_valid=True,
            first_hash=GENESIS_HASH,
            last_hash=GENESIS_HASH,
            total_events=len(self.events),
            gap_count=0,
            verified_at=datetime.now(timezone.utc),
        )

    def get_stats(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {"total_events": len(self.events)}

    def cleanup_old_logs(self, *, dry_run: bool = False) -> dict[str, Any]:
        return {"dry_run": dry_run, "deleted": 0}


@pytest.fixture(autouse=True)
def _reset_audit_singletons() -> None:
    KernelAuditRuntime.shutdown_all()
    yield
    KernelAuditRuntime.shutdown_all()


def _hash_event(event: KernelAuditEvent) -> str:
    payload = json.dumps(
        event.to_dict(),
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_audit_runtime_links_prev_hash_to_previous_event(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    store = _InMemoryAuditStore(runtime_root)
    runtime = KernelAuditRuntime(runtime_root, store)

    runtime.emit_event(
        event_type=KernelAuditEventType.TASK_START,
        role=KernelAuditRole.SYSTEM,
        workspace=str(tmp_path),
        run_id="run-1",
    )
    runtime.emit_event(
        event_type=KernelAuditEventType.TASK_COMPLETE,
        role=KernelAuditRole.SYSTEM,
        workspace=str(tmp_path),
        run_id="run-1",
    )

    assert len(store.events) == 2
    assert store.events[0].prev_hash == GENESIS_HASH
    assert store.events[1].prev_hash == _hash_event(store.events[0])


def test_audit_runtime_raises_when_store_append_fails(tmp_path: Path) -> None:
    class _FailingStore(_InMemoryAuditStore):
        def append(self, event: KernelAuditEvent) -> KernelAuditEvent:
            del event
            raise OSError("disk full")

    runtime_root = tmp_path / "runtime"
    runtime = KernelAuditRuntime(runtime_root, _FailingStore(runtime_root))

    with pytest.raises(KernelAuditWriteError, match="Mandatory audit write failed"):
        runtime.emit_event(
            event_type=KernelAuditEventType.LLM_CALL,
            role=KernelAuditRole.SYSTEM,
            workspace=str(tmp_path),
            run_id="run-1",
        )
