"""Public service exports for `audit.evidence` cell."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from threading import Lock
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
    MANAGED_PROCESS_RECEIPT_LOGICAL_PATH_V1,
    AppendEvidenceEventCommandV1,
    EvidenceAppendedEventV1,
    EvidenceAuditError,
    EvidenceQueryResultV1,
    EvidenceVerificationResultV1,
    ManagedProcessReceiptPersistResultV1,
    ManagedProcessReceiptRecordV1,
    PersistManagedProcessReceiptCommandV1,
    QueryEvidenceEventsV1,
    ReadManagedProcessReceiptQueryV1,
    VerifyEvidenceChainV1,
)
from polaris.cells.audit.evidence.task_service import (
    EvidenceService,
    build_error_evidence,
    build_file_evidence,
    detect_language,
)
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter

_EVIDENCE_KIND_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_MANAGED_PROCESS_RECEIPT_SCHEMA_V1 = "audit.evidence.managed_process_receipt.v1"
_MANAGED_PROCESS_RECEIPT_LOCK = Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _evidence_event_id(record: dict[str, Any]) -> str:
    basis = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _evidence_logical_path(kind: str) -> str:
    if not _EVIDENCE_KIND_PATTERN.fullmatch(kind):
        raise EvidenceAuditError(f"invalid evidence kind: {kind!r}")
    return f"runtime/evidence/{kind}.jsonl"


def _evidence_logical_paths(fs: Any) -> list[str]:
    if not hasattr(fs, "resolve_path"):
        return []
    evidence_dir = fs.resolve_path("runtime/evidence")
    if not evidence_dir.exists():
        return []
    return [f"runtime/evidence/{path.name}" for path in sorted(evidence_dir.glob("*.jsonl")) if path.is_file()]


def _read_evidence_records(fs: Any, logical_path: str) -> list[dict[str, Any]]:
    try:
        content = fs.read_text(logical_path, encoding="utf-8")
    except FileNotFoundError:
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(str(content or "").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise EvidenceAuditError(f"invalid evidence JSONL at {logical_path}:{line_number}") from exc
        if not isinstance(payload, dict):
            raise EvidenceAuditError(f"invalid evidence record at {logical_path}:{line_number}")
        records.append(payload)
    return records


def _event_sort_key(event: dict[str, Any]) -> tuple[str, str]:
    return (str(event.get("timestamp") or ""), str(event.get("event_id") or ""))


def _all_evidence_records(fs: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for logical_path in _evidence_logical_paths(fs):
        records.extend(_read_evidence_records(fs, logical_path))
    return sorted(records, key=_event_sort_key)


def _plain_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json_value(item) for item in value]
    return value


def _canonical_receipt_json(receipt: Mapping[str, Any]) -> str:
    return json.dumps(
        _plain_json_value(receipt),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _managed_process_receipt_hash(receipt: Mapping[str, Any]) -> str:
    canonical = _canonical_receipt_json(receipt)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _managed_process_receipt_ref(receipt_hash: str) -> str:
    return f"{MANAGED_PROCESS_RECEIPT_LOGICAL_PATH_V1}#{receipt_hash}"


def _read_managed_process_receipt_records(fs: Any) -> tuple[ManagedProcessReceiptRecordV1, ...]:
    try:
        content = fs.read_text(MANAGED_PROCESS_RECEIPT_LOGICAL_PATH_V1, encoding="utf-8")
    except FileNotFoundError:
        return ()
    records: list[ManagedProcessReceiptRecordV1] = []
    seen: set[tuple[str, str]] = set()
    for line_number, line in enumerate(str(content or "").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
            if not isinstance(payload, dict):
                raise TypeError("record must be an object")
            if payload.get("schema_version") != _MANAGED_PROCESS_RECEIPT_SCHEMA_V1:
                raise ValueError("unexpected schema_version")
            record = ManagedProcessReceiptRecordV1(
                workspace=payload["workspace"],
                receipt_ref=payload["receipt_ref"],
                receipt_hash=payload["receipt_hash"],
                receipt=payload["receipt"],
            )
            if _managed_process_receipt_hash(record.receipt) != record.receipt_hash:
                raise ValueError("receipt_hash does not bind receipt")
            identity = (record.workspace, record.receipt_hash)
            if identity in seen:
                raise ValueError("duplicate receipt identity")
            seen.add(identity)
            records.append(record)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise EvidenceAuditError(
                f"invalid managed-process receipt at {MANAGED_PROCESS_RECEIPT_LOGICAL_PATH_V1}:{line_number}"
            ) from exc
    return tuple(records)


def persist_managed_process_receipt(
    command: PersistManagedProcessReceiptCommandV1,
    *,
    kernel_fs: Any | None = None,
) -> ManagedProcessReceiptPersistResultV1:
    """Persist one canonical full receipt idempotently through audit.evidence."""

    if type(command) is not PersistManagedProcessReceiptCommandV1:
        raise TypeError("command must be PersistManagedProcessReceiptCommandV1")
    receipt_hash = _managed_process_receipt_hash(command.receipt)
    if command.claimed_receipt_hash is not None and command.claimed_receipt_hash != receipt_hash:
        raise EvidenceAuditError("claimed receipt hash does not match the canonical receipt body")
    receipt_ref = _managed_process_receipt_ref(receipt_hash)
    fs = kernel_fs or KernelFileSystem(command.workspace, get_default_adapter())
    with _MANAGED_PROCESS_RECEIPT_LOCK:
        records = _read_managed_process_receipt_records(fs)
        for record in records:
            if record.workspace != command.workspace or record.receipt_hash != receipt_hash:
                continue
            if _canonical_receipt_json(record.receipt) != _canonical_receipt_json(command.receipt):
                raise EvidenceAuditError("managed-process receipt hash collision")
            return ManagedProcessReceiptPersistResultV1(
                workspace=command.workspace,
                receipt_ref=record.receipt_ref,
                receipt_hash=record.receipt_hash,
                already_present=True,
            )
        fs.append_jsonl(
            MANAGED_PROCESS_RECEIPT_LOGICAL_PATH_V1,
            {
                "schema_version": _MANAGED_PROCESS_RECEIPT_SCHEMA_V1,
                "workspace": command.workspace,
                "receipt_ref": receipt_ref,
                "receipt_hash": receipt_hash,
                "receipt": _plain_json_value(command.receipt),
            },
        )
    return ManagedProcessReceiptPersistResultV1(
        workspace=command.workspace,
        receipt_ref=receipt_ref,
        receipt_hash=receipt_hash,
        already_present=False,
    )


def read_managed_process_receipt(
    query: ReadManagedProcessReceiptQueryV1,
    *,
    kernel_fs: Any | None = None,
) -> ManagedProcessReceiptRecordV1 | None:
    """Read immutable receipt data by hash within one workspace boundary."""

    if type(query) is not ReadManagedProcessReceiptQueryV1:
        raise TypeError("query must be ReadManagedProcessReceiptQueryV1")
    fs = kernel_fs or KernelFileSystem(query.workspace, get_default_adapter())
    with _MANAGED_PROCESS_RECEIPT_LOCK:
        records = _read_managed_process_receipt_records(fs)
    for record in records:
        if record.workspace == query.workspace and record.receipt_hash == query.receipt_hash:
            return record
    return None


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


def query_evidence_events(
    query: QueryEvidenceEventsV1,
    *,
    workspace: str = ".",
    kernel_fs: Any | None = None,
) -> EvidenceQueryResultV1:
    """Read typed evidence events through the audit.evidence public boundary."""

    if not isinstance(query, QueryEvidenceEventsV1):
        raise TypeError("query must be QueryEvidenceEventsV1")
    fs = kernel_fs or KernelFileSystem(workspace, get_default_adapter())
    records = _all_evidence_records(fs)
    try:
        limit = max(0, int(query.limit))
    except (TypeError, ValueError):
        limit = 50
    selected = records[-limit:] if limit else []
    return EvidenceQueryResultV1(events=tuple(dict(record) for record in selected), total=len(records))


def verify_evidence_chain(
    query: VerifyEvidenceChainV1,
    *,
    workspace: str = ".",
    kernel_fs: Any | None = None,
) -> EvidenceVerificationResultV1:
    """Verify evidence event IDs without mutating source evidence."""

    if not isinstance(query, VerifyEvidenceChainV1):
        raise TypeError("query must be VerifyEvidenceChainV1")
    fs = kernel_fs or KernelFileSystem(workspace, get_default_adapter())
    checked_events = 0
    try:
        records = _all_evidence_records(fs)
    except EvidenceAuditError:
        return EvidenceVerificationResultV1(ok=False, checked_events=0)
    for record in records:
        timestamp = str(record.get("timestamp") or "")
        if query.start_at and timestamp < query.start_at:
            continue
        expected_record = dict(record)
        actual_event_id = str(expected_record.pop("event_id", "") or "")
        checked_events += 1
        if not actual_event_id or _evidence_event_id(expected_record) != actual_event_id:
            return EvidenceVerificationResultV1(ok=False, checked_events=checked_events)
    return EvidenceVerificationResultV1(ok=True, checked_events=checked_events)


__all__ = [
    "AppendEvidenceEventCommandV1",
    "AuditLLMBindingConfig",
    "EvidenceAppendedEventV1",
    "EvidenceAuditError",
    "EvidenceBundleService",
    "EvidenceQueryResultV1",
    "EvidenceService",
    "EvidenceVerificationResultV1",
    "ManagedProcessReceiptPersistResultV1",
    "ManagedProcessReceiptRecordV1",
    "PersistManagedProcessReceiptCommandV1",
    "ReadManagedProcessReceiptQueryV1",
    "RoleSessionAuditService",
    "append_evidence_event",
    "bind_audit_llm_to_task_service",
    "build_audit_llm_binding_config",
    "build_error_evidence",
    "build_file_evidence",
    "create_evidence_bundle_service",
    "detect_language",
    "get_audit_role_descriptor",
    "persist_managed_process_receipt",
    "query_evidence_events",
    "read_managed_process_receipt",
    "verify_evidence_chain",
]
