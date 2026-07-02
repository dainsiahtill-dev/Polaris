"""Public service exports for `audit.diagnosis` cell."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from polaris.cells.audit.diagnosis.internal.connection_audit_service import (
    write_ws_connection_event,
    write_ws_connection_event_sync,
)
from polaris.cells.audit.diagnosis.internal.diagnosis_engine import AuditDiagnosisEngine
from polaris.cells.audit.diagnosis.internal.toolkit import (
    build_failure_hops,
    build_triage_bundle,
    run_audit_command,
    to_script_projection,
)
from polaris.cells.audit.diagnosis.internal.toolkit.error_chain import (
    ChainBuilder,
    ErrorChain,
    ErrorChainLink,
    ErrorChainSearcher,
    ErrorMatcher,
    EventLoader,
    _parse_event_datetime,
)
from polaris.cells.audit.diagnosis.internal.toolkit.service import (
    _discover_journal_run_dirs,
    load_journal_events,
    resolve_runtime_root,
)
from polaris.cells.audit.diagnosis.internal.usecases import AuditUseCaseFacade
from polaris.cells.audit.diagnosis.public.contracts import (
    AuditDiagnosisResultV1,
    QueryAuditDiagnosisTrailV1,
)
from polaris.kernelone.audit.registry import has_audit_store_factory

# Public alias for discover_journal_run_dirs
discover_journal_run_dirs = _discover_journal_run_dirs


def _event_run_id(payload: dict[str, Any]) -> str:
    for section in ("source", "task", "context", "data", "action"):
        value = payload.get(section)
        if isinstance(value, dict):
            run_id = str(value.get("run_id") or "").strip()
            if run_id:
                return run_id
    return str(payload.get("run_id") or "").strip()


def _has_audit_artifacts(runtime_root: Path) -> bool:
    audit_root = runtime_root / "audit"
    if not audit_root.exists():
        return False
    try:
        return any(audit_root.rglob("*"))
    except OSError:
        return True


def _empty_diagnosis_trail(
    query: QueryAuditDiagnosisTrailV1,
    *,
    runtime_root: Path,
    reason: str,
) -> AuditDiagnosisResultV1:
    return AuditDiagnosisResultV1(
        ok=True,
        status="empty",
        workspace=query.workspace,
        payload={
            "runtime_root": str(runtime_root),
            "run_id": query.run_id or "",
            "task_id": query.task_id or "",
            "limit": query.limit,
            "total": 0,
            "events": [],
            "empty_reason": reason,
        },
    )


def query_audit_diagnosis_trail(query: QueryAuditDiagnosisTrailV1) -> AuditDiagnosisResultV1:
    """Read audit diagnosis trail evidence through the public Cell boundary."""

    runtime_root = resolve_runtime_root(workspace=query.workspace)
    if runtime_root is None:
        return AuditDiagnosisResultV1(
            ok=False,
            status="unavailable",
            workspace=query.workspace,
            payload={"events": [], "total": 0},
            error_code="runtime_root_unavailable",
            error_message="Unable to resolve runtime root for audit diagnosis trail.",
        )

    try:
        if not runtime_root.exists():
            return _empty_diagnosis_trail(query, runtime_root=runtime_root, reason="runtime_root_missing")
        has_artifacts = _has_audit_artifacts(runtime_root)
        if not has_audit_store_factory() and not has_artifacts:
            return _empty_diagnosis_trail(
                query,
                runtime_root=runtime_root,
                reason="audit_store_factory_unregistered_without_artifacts",
            )
        facade = AuditUseCaseFacade(runtime_root=runtime_root)
        events = facade.query_logs(
            task_id=query.task_id,
            limit=query.limit,
        )
        payload_events = [event.to_dict() for event in events]
        if query.run_id:
            payload_events = [event for event in payload_events if _event_run_id(event) == query.run_id]
        return AuditDiagnosisResultV1(
            ok=True,
            status="available" if payload_events else "empty",
            workspace=query.workspace,
            payload={
                "runtime_root": str(runtime_root),
                "run_id": query.run_id or "",
                "task_id": query.task_id or "",
                "limit": query.limit,
                "total": len(payload_events),
                "events": payload_events,
            },
        )
    except (RuntimeError, ValueError, OSError) as exc:
        return AuditDiagnosisResultV1(
            ok=False,
            status="unavailable",
            workspace=query.workspace,
            payload={"events": [], "total": 0, "runtime_root": str(runtime_root)},
            error_code="audit_diagnosis_query_failed",
            error_message=str(exc),
        )


__all__ = [
    "AuditDiagnosisEngine",
    "AuditUseCaseFacade",
    "ChainBuilder",
    "ErrorChain",
    "ErrorChainLink",
    "ErrorChainSearcher",
    "ErrorMatcher",
    "EventLoader",
    "_parse_event_datetime",
    "build_failure_hops",
    "build_triage_bundle",
    "discover_journal_run_dirs",
    "load_journal_events",
    "query_audit_diagnosis_trail",
    "resolve_runtime_root",
    "run_audit_command",
    "to_script_projection",
    "write_ws_connection_event",
    "write_ws_connection_event_sync",
]
