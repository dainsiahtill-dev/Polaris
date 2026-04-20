"""Application-layer audit use case facade."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.kernelone.audit import (
    KernelAuditEvent,
    KernelAuditEventType,
    KernelAuditRuntime,
)

if TYPE_CHECKING:
    from datetime import datetime


class AuditUseCaseFacade:
    """Facade over KernelAuditRuntime for delivery adapters."""

    def __init__(self, runtime_root: Path) -> None:
        self._runtime = KernelAuditRuntime.get_instance(Path(runtime_root).resolve())

    def query_logs(
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
        return self._runtime.query_events(
            start_time=start_time,
            end_time=end_time,
            event_type=event_type,
            role=role,
            task_id=task_id,
            limit=limit,
            offset=offset,
        )

    def export_json(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        event_types: list[KernelAuditEventType | str] | None = None,
        include_data: bool = True,
    ) -> dict[str, Any]:
        return self._runtime.export_json(
            start_time=start_time,
            end_time=end_time,
            event_types=event_types,
            include_data=include_data,
        )

    def export_csv(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> str:
        return self._runtime.export_csv(start_time=start_time, end_time=end_time)

    def verify_chain(self) -> dict[str, Any]:
        result = self._runtime.verify_chain()
        return {
            "chain_valid": result.is_valid,
            "first_event_hash": result.first_hash,
            "last_event_hash": result.last_hash,
            "total_events": result.total_events,
            "gap_count": result.gap_count,
            "verified_at": result.verified_at.isoformat(),
            "invalid_events": result.invalid_events,
        }

    def get_stats(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, Any]:
        return self._runtime.get_stats(start_time=start_time, end_time=end_time)

    def cleanup_old_logs(self, *, dry_run: bool = False) -> dict[str, Any]:
        return self._runtime.cleanup_old_logs(dry_run=dry_run)

    def get_corruption_log(self, *, workspace: str, limit: int = 100) -> list[dict[str, Any]]:
        return self._runtime.get_corruption_log(workspace=workspace, limit=limit)
