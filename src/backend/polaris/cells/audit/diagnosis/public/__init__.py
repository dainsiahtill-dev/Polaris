"""Public boundary for `audit.diagnosis` cell."""

from polaris.cells.audit.diagnosis.public.contracts import (
    AuditDiagnosisCompletedEventV1,
    AuditDiagnosisError,
    AuditDiagnosisResultV1,
    IAuditDiagnosisService,
    QueryAuditDiagnosisTrailV1,
    RunAuditDiagnosisCommandV1,
)
from polaris.cells.audit.diagnosis.public.service import (
    AuditDiagnosisEngine,
    AuditUseCaseFacade,
    ErrorChain,
    ErrorChainLink,
    ErrorChainSearcher,
    resolve_runtime_root,
    run_audit_command,
    to_legacy_result,
    write_ws_connection_event,
    write_ws_connection_event_sync,
)

__all__ = [
    "AuditDiagnosisCompletedEventV1",
    "AuditDiagnosisEngine",
    "AuditDiagnosisError",
    "AuditDiagnosisResultV1",
    "AuditUseCaseFacade",
    "ErrorChain",
    "ErrorChainLink",
    "ErrorChainSearcher",
    "IAuditDiagnosisService",
    "QueryAuditDiagnosisTrailV1",
    "RunAuditDiagnosisCommandV1",
    "resolve_runtime_root",
    "run_audit_command",
    "to_legacy_result",
    "write_ws_connection_event",
    "write_ws_connection_event_sync",
]
