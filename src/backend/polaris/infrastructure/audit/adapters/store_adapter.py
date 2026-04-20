"""Adapter from AuditStore to KernelAuditStorePort."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.infrastructure.audit.stores.audit_store import (
    AuditEvent,
    AuditEventType,
    AuditStore,
)
from polaris.kernelone.audit.contracts import (
    KernelAuditEvent,
    KernelAuditEventType,
    KernelAuditStorePort,
    KernelChainVerificationResult,
)

if TYPE_CHECKING:
    from datetime import datetime


class AuditStoreAdapter(KernelAuditStorePort):
    """Expose AuditStore through the KernelOne audit port."""

    def __init__(self, runtime_root: Path, *, retention_days: int = 90) -> None:
        self._runtime_root = Path(runtime_root).resolve()
        self._store = AuditStore(runtime_root=self._runtime_root, retention_days=retention_days)

    @property
    def runtime_root(self) -> Path:
        return self._runtime_root

    @property
    def raw_store(self) -> AuditStore:
        """Expose underlying store instance."""
        return self._store

    def append(self, event: KernelAuditEvent) -> KernelAuditEvent:
        legacy = AuditEvent(
            event_id=event.event_id,
            timestamp=event.timestamp,
            event_type=AuditEventType(event.event_type.value),
            version=event.version,
            source=dict(event.source),
            task=dict(event.task),
            resource=dict(event.resource),
            action=dict(event.action),
            data=dict(event.data),
            context=dict(event.context),
            prev_hash=event.prev_hash,
            signature=event.signature,
        )
        persisted = self._store.append(legacy)
        return KernelAuditEvent(
            event_id=persisted.event_id,
            timestamp=persisted.timestamp,
            event_type=KernelAuditEventType(persisted.event_type.value),
            version=persisted.version,
            source=dict(persisted.source),
            task=dict(persisted.task),
            resource=dict(persisted.resource),
            action=dict(persisted.action),
            data=dict(persisted.data),
            context=dict(persisted.context),
            prev_hash=persisted.prev_hash,
            signature=persisted.signature,
        )

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
        legacy_event_type: AuditEventType | None = None
        if event_type is not None:
            legacy_event_type = AuditEventType(event_type.value)
        events = self._store.query(
            start_time=start_time,
            end_time=end_time,
            event_type=legacy_event_type,
            role=role,
            task_id=task_id,
            limit=limit,
            offset=offset,
        )
        return [
            KernelAuditEvent(
                event_id=item.event_id,
                timestamp=item.timestamp,
                event_type=KernelAuditEventType(item.event_type.value),
                version=item.version,
                source=dict(item.source),
                task=dict(item.task),
                resource=dict(item.resource),
                action=dict(item.action),
                data=dict(item.data),
                context=dict(item.context),
                prev_hash=item.prev_hash,
                signature=item.signature,
            )
            for item in events
        ]

    def export_json(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        event_types: list[KernelAuditEventType] | None = None,
        include_data: bool = True,
    ) -> dict[str, Any]:
        legacy_types: list[AuditEventType] | None = None
        if event_types is not None:
            legacy_types = [AuditEventType(item.value) for item in event_types]
        return self._store.export_json(
            start_time=start_time,
            end_time=end_time,
            event_types=legacy_types,
            include_data=include_data,
        )

    def export_csv(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> str:
        return self._store.export_csv(start_time=start_time, end_time=end_time)

    def verify_chain(self) -> KernelChainVerificationResult:
        result = self._store.verify_chain()
        return KernelChainVerificationResult(
            is_valid=bool(result.is_valid),
            first_hash=str(result.first_hash),
            last_hash=str(result.last_hash),
            total_events=int(result.total_events),
            gap_count=int(result.gap_count),
            verified_at=result.verified_at,
            invalid_events=list(result.invalid_events),
        )

    def get_stats(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, Any]:
        return self._store.get_stats(start_time=start_time, end_time=end_time)

    def cleanup_old_logs(self, *, dry_run: bool = False) -> dict[str, Any]:
        return self._store.cleanup_old_logs(dry_run=dry_run)
