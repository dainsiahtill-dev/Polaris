"""Public service exports for `audit.evidence` cell."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from polaris.cells.audit.evidence.bundle_service import EvidenceBundleService, create_evidence_bundle_service
from polaris.cells.audit.evidence.internal.role_session_audit_service import (
    RoleSessionAuditService,
)
from polaris.cells.audit.evidence.internal.task_audit_llm_binding import (
    AuditLLMBindingConfig,
    bind_audit_llm_to_task_service,
    build_audit_llm_binding_config,
    get_audit_role_descriptor,
)
from polaris.cells.audit.evidence.public.contracts import (
    AppendEvidenceEventCommandV1,
    EvidenceAppendedEventV1,
    EvidenceAuditError,
)
from polaris.cells.audit.evidence.task_service import (
    EvidenceService,
    build_error_evidence,
    build_file_evidence,
    detect_language,
)
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter

_EVIDENCE_KIND_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _evidence_event_id(record: dict[str, Any]) -> str:
    basis = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _evidence_logical_path(kind: str) -> str:
    if not _EVIDENCE_KIND_PATTERN.fullmatch(kind):
        raise EvidenceAuditError(f"invalid evidence kind: {kind!r}")
    return f"runtime/evidence/{kind}.jsonl"


def append_evidence_event(
    command: AppendEvidenceEventCommandV1,
    *,
    kernel_fs: Any | None = None,
) -> EvidenceAppendedEventV1:
    """Append one typed evidence event through the audit.evidence public boundary."""
    if not isinstance(command, AppendEvidenceEventCommandV1):
        raise TypeError("command must be AppendEvidenceEventCommandV1")

    record: dict[str, Any] = {
        "schema_version": "audit.evidence.event.v1",
        "kind": command.kind,
        "workspace": command.workspace,
        "payload": dict(command.payload),
        "metadata": dict(command.metadata),
        "timestamp": _utc_now(),
    }
    record["event_id"] = _evidence_event_id(record)
    logical_path = _evidence_logical_path(command.kind)
    fs = kernel_fs or KernelFileSystem(command.workspace, get_default_adapter())
    receipt = fs.append_jsonl(logical_path, record)
    return EvidenceAppendedEventV1(kind=command.kind, receipt_path=str(receipt.logical_path))


__all__ = [
    "AppendEvidenceEventCommandV1",
    "AuditLLMBindingConfig",
    "EvidenceAppendedEventV1",
    "EvidenceAuditError",
    "EvidenceBundleService",
    "EvidenceService",
    "RoleSessionAuditService",
    "append_evidence_event",
    "bind_audit_llm_to_task_service",
    "build_audit_llm_binding_config",
    "build_error_evidence",
    "build_file_evidence",
    "create_evidence_bundle_service",
    "detect_language",
    "get_audit_role_descriptor",
]
