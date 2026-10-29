"""Immutable, workspace-bound payload bytes for small durable event facts.

Content is pinned audit data, independent of ContextOS caches and their TTLs.
Explicit preparation enrolls one content path; reads never create lock state.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from polaris.kernelone.fs import LockedRegularFileError, LockedRegularFileSetV1
from polaris.kernelone.fs.locked_regular_file import default_platform_lock_root
from polaris.kernelone.storage import resolve_workspace_runtime_identity

_REFERENCE_SCHEMA = "kernelone.payload_reference.v1"
_SHA = re.compile(r"[0-9a-f]{64}")
_FIELDS = {"schema_version", "sha256", "byte_size", "payload_schema", "storage_identity_token"}


class PayloadStoreError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def _text(value: str, name: str) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise ValueError(f"{name} must be an exact non-empty string")
    return value


def _canonical_payload(payload: Mapping[str, Any], schema: str) -> bytes:
    if type(payload) is not dict or payload.get("schema_version") != schema:
        raise ValueError("payload schema does not match its declared schema")

    def validate(value: Any) -> None:
        if isinstance(value, dict):
            if any(type(key) is not str for key in value):
                raise ValueError("payload keys must be exact strings")
            for item in value.values():
                validate(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                validate(item)

    validate(payload)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


@dataclass(frozen=True, slots=True)
class PayloadReferenceV1:
    sha256: str
    byte_size: int
    payload_schema: str
    storage_identity_token: str

    def __post_init__(self) -> None:
        if type(self.sha256) is not str or _SHA.fullmatch(self.sha256) is None:
            raise ValueError("sha256 must be full lowercase SHA-256")
        if type(self.byte_size) is not int or self.byte_size < 1:
            raise ValueError("byte_size must be a positive exact int")
        _text(self.payload_schema, "payload_schema")
        _text(self.storage_identity_token, "storage_identity_token")

    @property
    def logical_path(self) -> str:
        return f"runtime/events/.payloads/{self.sha256[:2]}/{self.sha256}.json"

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": _REFERENCE_SCHEMA,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "payload_schema": self.payload_schema,
            "storage_identity_token": self.storage_identity_token,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> PayloadReferenceV1:
        if type(record) is not dict or set(record) != _FIELDS or record.get("schema_version") != _REFERENCE_SCHEMA:
            raise ValueError("payload reference shape is invalid")
        return cls(
            sha256=record["sha256"],
            byte_size=record["byte_size"],
            payload_schema=record["payload_schema"],
            storage_identity_token=record["storage_identity_token"],
        )


class DurablePayloadStore:
    """Persist one canonical payload before its authoritative referencing fact."""

    def __init__(self, workspace: str, *, read_only: bool = False) -> None:
        self.identity = resolve_workspace_runtime_identity(_text(workspace, "workspace"), read_only=read_only)
        self._read_only = read_only

    def _acquire(self, ref: PayloadReferenceV1, *, read_only: bool):
        if type(ref) is not PayloadReferenceV1 or ref.storage_identity_token != self.identity.token:
            raise PayloadStoreError(
                "payload reference belongs to a different storage identity", code="payload_storage_identity_mismatch"
            )
        return LockedRegularFileSetV1.acquire(
            runtime_root=self.identity.runtime_root,
            storage_identity_token=self.identity.token,
            logical_paths=(ref.logical_path,),
            platform_lock_root=default_platform_lock_root(),
            read_only=read_only,
            timeout_seconds=15.0,
        )

    def _decode(self, ref: PayloadReferenceV1, data: bytes) -> dict[str, Any]:
        if len(data) != ref.byte_size or hashlib.sha256(data).hexdigest() != ref.sha256:
            raise PayloadStoreError("payload bytes do not match the reference", code="payload_integrity_mismatch")
        try:
            payload = json.loads(data.decode("utf-8"))
            if _canonical_payload(payload, ref.payload_schema) != data:
                raise ValueError("payload canonical bytes drift")
        except (UnicodeError, ValueError, TypeError) as exc:
            raise PayloadStoreError("payload schema or encoding is invalid", code="payload_schema_mismatch") from exc
        return cast("dict[str, Any]", (payload))

    def prepare(
        self, payload: Mapping[str, Any], *, payload_schema: str, maintenance_reason: str
    ) -> PayloadReferenceV1:
        if self._read_only:
            raise PayloadStoreError("read-only payload store cannot prepare", code="read_only_store")
        _text(maintenance_reason, "maintenance_reason")
        data = _canonical_payload(payload, _text(payload_schema, "payload_schema"))
        ref = PayloadReferenceV1(hashlib.sha256(data).hexdigest(), len(data), payload_schema, self.identity.token)
        try:
            # Preparation is an explicit maintenance command, never an implicit
            # enrollment performed by a query or ordinary fact append.
            LockedRegularFileSetV1.enroll_stream_lock_keys(
                runtime_root=self.identity.runtime_root,
                storage_identity_token=self.identity.token,
                logical_paths=(ref.logical_path,),
                platform_lock_root=default_platform_lock_root(),
            )
            with self._acquire(ref, read_only=False) as files:
                lease = files.lease(ref.logical_path)
                if lease.open_existing(writable=True):
                    self._decode(ref, lease.read_bytes())
                    lease.confirm_durability()
                else:
                    lease.append_bytes(data, fsync_file=True, fsync_parent_on_create=True)
                self._decode(ref, lease.read_bytes())
        except LockedRegularFileError as exc:
            raise PayloadStoreError(str(exc), code=exc.code) from exc
        return ref

    def confirm(self, ref: PayloadReferenceV1) -> dict[str, Any]:
        """Confirm exact existing bytes are durable without enrolling or rewriting."""
        if self._read_only:
            raise PayloadStoreError("read-only payload store cannot confirm", code="read_only_store")
        try:
            with self._acquire(ref, read_only=False) as files:
                lease = files.lease(ref.logical_path)
                if not lease.open_existing(writable=True):
                    raise PayloadStoreError("referenced payload is missing", code="payload_missing")
                payload = self._decode(ref, lease.read_bytes())
                lease.confirm_durability()
                self._decode(ref, lease.read_bytes())
                return payload
        except LockedRegularFileError as exc:
            raise PayloadStoreError(str(exc), code=exc.code) from exc

    def read(self, ref: PayloadReferenceV1) -> dict[str, Any]:
        try:
            with self._acquire(ref, read_only=True) as files:
                lease = files.lease(ref.logical_path)
                if not lease.open_existing():
                    raise PayloadStoreError("referenced payload is missing", code="payload_missing")
                return self._decode(ref, lease.read_bytes())
        except LockedRegularFileError as exc:
            raise PayloadStoreError(str(exc), code=exc.code) from exc
