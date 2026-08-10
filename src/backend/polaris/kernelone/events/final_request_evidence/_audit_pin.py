"""Context snapshot audit-pin contract for final-request evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from polaris.kernelone.events.final_request_evidence._constants import (
    _CONTEXT_SNAPSHOT_AUDIT_PIN_FIELDS,
    _EXACT_HASH_64_RE,
)
from polaris.kernelone.events.final_request_evidence._evidence import (
    canonical_final_request_hash,
)
from polaris.kernelone.events.final_request_evidence._helpers import (
    _validate_exact_context_snapshot_hash,
)


@dataclass(frozen=True, slots=True)
class ContextSnapshotAuditPinV1:
    schema_version: str
    workspace_abs: str
    runtime_root: str
    snapshot_logical_path: str
    snapshot_absolute_path: str
    snapshot_source: str
    factory_run_id: str
    role: str
    verification_scope: str
    request_freeze_id: str
    provider_request_id: str
    context_snapshot_ref: str
    storage_identity_token: str
    snapshot_content_hash: str
    composite_request_hash: str
    retention: str
    pin_hash: str

    @classmethod
    def create(
        cls,
        *,
        workspace_abs: str,
        runtime_root: str,
        snapshot_logical_path: str,
        snapshot_absolute_path: str,
        snapshot_source: str,
        factory_run_id: str,
        role: str,
        verification_scope: str,
        request_freeze_id: str,
        provider_request_id: str,
        context_snapshot_ref: str,
        storage_identity_token: str,
        snapshot_content_hash: str,
        composite_request_hash: str,
    ) -> ContextSnapshotAuditPinV1:
        try:
            ref = _validate_exact_context_snapshot_hash(str(context_snapshot_ref or ""))
        except ValueError as exc:
            raise ValueError("context_snapshot_ref must be exactly 24 lowercase hex") from exc
        payload = {
            "schema_version": "llm.context_snapshot_audit_pin.v1",
            "workspace_abs": str(workspace_abs or "").strip(),
            "runtime_root": str(runtime_root or "").strip(),
            "snapshot_logical_path": str(snapshot_logical_path or "").strip(),
            "snapshot_absolute_path": str(snapshot_absolute_path or "").strip(),
            "snapshot_source": str(snapshot_source or "").strip(),
            "factory_run_id": str(factory_run_id or "").strip(),
            "role": str(role or "").strip(),
            "verification_scope": str(verification_scope or "").strip(),
            "request_freeze_id": str(request_freeze_id or "").strip(),
            "provider_request_id": str(provider_request_id or "").strip(),
            "context_snapshot_ref": ref,
            "storage_identity_token": str(storage_identity_token or "").strip(),
            "snapshot_content_hash": str(snapshot_content_hash or "").strip(),
            "composite_request_hash": str(composite_request_hash or "").strip(),
            "retention": "pinned_audit_no_delete",
        }
        if any(not value for key_name, value in payload.items() if key_name not in {"schema_version", "retention"}):
            raise ValueError("context snapshot audit pin bindings must be non-empty")
        for field_name in ("snapshot_content_hash", "composite_request_hash"):
            if not _EXACT_HASH_64_RE.fullmatch(str(payload[field_name])):
                raise ValueError(f"{field_name} must be exactly 64 lowercase hex")
        try:
            _validate_exact_context_snapshot_hash(str(payload["storage_identity_token"]))
        except ValueError as exc:
            raise ValueError("storage_identity_token must be exactly 24 lowercase hex") from exc
        return cls(**payload, pin_hash=canonical_final_request_hash(payload))

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> ContextSnapshotAuditPinV1:
        if not isinstance(record, Mapping):
            raise ValueError("context snapshot audit pin must be a mapping")
        if frozenset(record) != _CONTEXT_SNAPSHOT_AUDIT_PIN_FIELDS:
            raise ValueError("context snapshot audit pin fields mismatch")
        if any(not isinstance(value, str) for value in record.values()):
            raise ValueError("context snapshot audit pin fields must be strings")
        created = cls.create(
            workspace_abs=str(record.get("workspace_abs") or ""),
            runtime_root=str(record.get("runtime_root") or ""),
            snapshot_logical_path=str(record.get("snapshot_logical_path") or ""),
            snapshot_absolute_path=str(record.get("snapshot_absolute_path") or ""),
            snapshot_source=str(record.get("snapshot_source") or ""),
            factory_run_id=str(record.get("factory_run_id") or ""),
            role=str(record.get("role") or ""),
            verification_scope=str(record.get("verification_scope") or ""),
            request_freeze_id=str(record.get("request_freeze_id") or ""),
            provider_request_id=str(record.get("provider_request_id") or ""),
            context_snapshot_ref=str(record.get("context_snapshot_ref") or ""),
            storage_identity_token=str(record.get("storage_identity_token") or ""),
            snapshot_content_hash=str(record.get("snapshot_content_hash") or ""),
            composite_request_hash=str(record.get("composite_request_hash") or ""),
        )
        if record.get("schema_version") != created.schema_version:
            raise ValueError("context snapshot audit pin schema mismatch")
        if record.get("retention") != created.retention:
            raise ValueError("context snapshot audit pin retention mismatch")
        if record.get("pin_hash") != created.pin_hash:
            raise ValueError("context snapshot audit pin hash mismatch")
        return created

    def to_record(self) -> dict[str, Any]:
        return asdict(self)
