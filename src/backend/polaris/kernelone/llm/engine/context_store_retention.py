"""ContextOS pin authority and bounded, fail-closed retention.

Snapshots and pins retain their existing identity, token and physical lock key.
Original producer bytes, inventory, reads, immutable creates, counters and exact
CAS removals use KernelOne FS. Complete enumeration means EOF only; the shared
authority excludes cooperating writers, not arbitrary native namespace changes.
TTL, count and byte caps run in order. Unavailable observations preserve real
removal effects and cannot publish success totals or throttle state.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
import threading
import time
import weakref
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeAlias

from polaris.kernelone.events.final_request_evidence import (
    ContextSnapshotAuditPinV1,
    ContextSnapshotReferenceOwnerV1,
    ContextSnapshotReferencePinV2,
    canonical_final_request_hash,
)
from polaris.kernelone.fs import (
    DirectoryEntryV1,
    FileMetadataV1,
    GuardedMetadataError,
    GuardedRegularFileSnapshotError,
    GuardedRegularFileSnapshotV1,
    LockedRegularFileError,
    LockedRegularFileSetV1,
    guarded_compare_and_create_regular_file,
    guarded_compare_and_remove_regular_file,
    guarded_compare_and_replace_regular_file,
    guarded_confirm_regular_file_durability,
    read_guarded_regular_file_snapshot,
)
from polaris.kernelone.fs.locked_regular_file import default_platform_lock_root
from polaris.kernelone.llm.engine.internal.context_hash import CONTEXT_HASH_PATTERN, validate_context_hash
from polaris.kernelone.storage import resolve_workspace_runtime_identity

logger = logging.getLogger(__name__)
SnapshotPin: TypeAlias = ContextSnapshotAuditPinV1 | ContextSnapshotReferencePinV2

SWEEP_STATE_FILENAME = ".sweep_state.json"
_PROVIDER_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_AUDIT_LOCK_LOGICAL_PATH = "runtime/contexts/context_snapshot_audit.control"
_AUDIT_PROCESS_LOCKS_GUARD = threading.Lock()
_AUDIT_PROCESS_LOCKS: weakref.WeakValueDictionary[str, Any] = weakref.WeakValueDictionary()


def _audit_process_lock(storage_identity_token: str) -> threading.RLock:
    """Serialize same-runtime audit writers before taking the physical lock.

    Each provider call constructs a repository instance.  Without a shared
    process mutex, concurrent calls from the same backend race for the same
    2-second ``flock`` and can fail before reaching the Provider.  The weak
    registry keeps one re-entrant mutex while repositories use it and releases
    idle workspace entries automatically.
    """

    token = str(storage_identity_token or "").strip()
    with _AUDIT_PROCESS_LOCKS_GUARD:
        lock = _AUDIT_PROCESS_LOCKS.get(token)
        if lock is None:
            lock = threading.RLock()
            _AUDIT_PROCESS_LOCKS[token] = lock
        return lock


class ContextSnapshotAuditPinError(RuntimeError):
    """Pinned ContextOS state is missing, corrupt, or bound elsewhere."""


# Finite operational traversal allowance, not a snapshot/model admission quota.
# Repeated enumeration work counts too; EOF never implies atomic membership.
_DIRECTORY_INITIAL_BATCH = 256
_INVENTORY_ENTRY_BUDGET = 1_000_000


class ContextStoreInventoryError(OSError):
    """An observation is unavailable; numeric subset totals are not authoritative."""

    def __init__(self, message: str, *, code: str = "context_inventory_unavailable") -> None:
        super().__init__(message)
        self.code = code


@dataclass
class _InventoryBudget:
    remaining: int = field(default_factory=lambda: _INVENTORY_ENTRY_BUDGET)


class ContextSnapshotAuditPinRepository:
    """Workspace-bound immutable snapshot/pin store and deletion preflight."""

    def __init__(self, *, workspace: str, runtime_root: str | None = None, read_only: bool = False) -> None:
        self.read_only = read_only
        identity = resolve_workspace_runtime_identity(workspace, read_only=True)
        selected_runtime_root = os.path.abspath(runtime_root or identity.runtime_root)
        if not read_only and selected_runtime_root != identity.runtime_root:
            raise ContextSnapshotAuditPinError("snapshot writer runtime override is not canonical")
        if not read_only:
            current = resolve_workspace_runtime_identity(workspace)
            if current != identity:
                raise ContextSnapshotAuditPinError("context snapshot storage identity changed")
        self.workspace_abs = identity.workspace_abs
        self.workspace_key = identity.workspace_key
        self.runtime_root = selected_runtime_root
        self.storage_identity_token = self._storage_token(
            workspace_abs=self.workspace_abs,
            workspace_key=self.workspace_key,
            runtime_root=self.runtime_root,
        )
        self.contexts_root = os.path.join(self.runtime_root, "contexts")
        from polaris.kernelone.fs import KernelFileSystem, get_default_adapter

        self._fs = KernelFileSystem(self.runtime_root, get_default_adapter(), read_only_paths=True)
        self._lock_logical_path = _AUDIT_LOCK_LOGICAL_PATH
        self._platform_lock_root = default_platform_lock_root()
        self._process_lock = _audit_process_lock(self.storage_identity_token)

    def _require_writable(self) -> None:
        if self.read_only:
            raise ContextSnapshotAuditPinError("readonly context snapshot repository cannot mutate")

    def _metadata(self, path: str, *, optional: bool = False) -> FileMetadataV1 | None:
        try:
            return self._fs.workspace_lstat(path)
        except GuardedMetadataError as exc:
            if optional and exc.code == "guarded_metadata_missing" and exc.details.get("errno") == 2:
                return None
            raise ContextStoreInventoryError(str(exc), code=exc.code) from exc

    @staticmethod
    def _same_observation(left: FileMetadataV1, right: FileMetadataV1 | GuardedRegularFileSnapshotV1) -> bool:
        return all(
            getattr(left, key) == getattr(right, key)
            for key in (
                "root_device",
                "root_inode",
                "device",
                "inode",
                "size",
                "mtime_ns",
                "ctime_ns",
            )
        )

    def _list_directory(
        self, path: str, budget: _InventoryBudget, expected: FileMetadataV1 | None = None
    ) -> tuple[DirectoryEntryV1, ...]:
        bound = min(_DIRECTORY_INITIAL_BATCH, budget.remaining)
        while bound > 0:
            try:
                listing = self._fs.workspace_list_directory(path, max_entries=bound)
            except GuardedMetadataError as exc:
                raise ContextStoreInventoryError(str(exc), code=exc.code) from exc
            budget.remaining -= max(1, len(listing.entries))
            if expected is not None and not self._same_observation(expected, listing.directory):
                raise ContextStoreInventoryError("directory identity changed between observations")
            if listing.complete:
                return tuple(sorted(listing.entries, key=lambda entry: entry.name))
            bound = min(bound * 2, budget.remaining)
        raise ContextStoreInventoryError(
            "directory enumeration exhausted operational entry budget", code="context_inventory_budget_exhausted"
        )

    def _read_snapshot(self, path: str, expected: FileMetadataV1 | None = None) -> GuardedRegularFileSnapshotV1:
        metadata = expected or self._metadata(path)
        if metadata is None or not stat.S_ISREG(metadata.mode) or metadata.nlink != 1:
            raise ContextStoreInventoryError("context file must be a regular file with one link")
        try:
            snapshot = read_guarded_regular_file_snapshot(
                self.runtime_root,
                metadata.relative_path,
                max_bytes=max(1, metadata.size),
            )
        except GuardedRegularFileSnapshotError as exc:
            raise ContextStoreInventoryError(str(exc), code=exc.code) from exc
        if not self._same_observation(metadata, snapshot):
            raise ContextStoreInventoryError("context file changed between metadata and byte read")
        return snapshot

    def persist_snapshot_bytes(
        self, content: bytes, *, reference_owner: ContextSnapshotReferenceOwnerV1 | None = None
    ) -> str:
        """Publish original producer bytes under the existing pin authority."""
        self._require_writable()
        if type(content) is not bytes:
            raise TypeError("snapshot content must be immutable bytes")
        ref = hashlib.sha256(content).hexdigest()[:24]
        with self._exclusive_lock():
            self._assert_current_identity()
            if reference_owner is not None:
                self._reference_pin(ref, content, reference_owner)
            self._write_immutable(self.snapshot_path(ref), content, kind="snapshot")
            if reference_owner is not None:
                self._pin_reference_locked(ref, content, reference_owner)
        return ref

    def _reference_pin(
        self, ref: str, content: bytes, owner: ContextSnapshotReferenceOwnerV1
    ) -> ContextSnapshotReferencePinV2:
        return ContextSnapshotReferencePinV2.create(
            owner=owner,
            workspace_abs=self.workspace_abs,
            runtime_root=self.runtime_root,
            snapshot_logical_path=f"runtime/contexts/{ref[:2]}/{ref}",
            snapshot_absolute_path=self.snapshot_path(ref),
            snapshot_source="host_reference",
            context_snapshot_ref=ref,
            storage_identity_token=self.storage_identity_token,
            snapshot_content_hash=hashlib.sha256(content).hexdigest(),
        )

    def reference_pin_path(self, ref: str, owner: ContextSnapshotReferenceOwnerV1) -> str:
        return self.pin_path(ref, "reference-" + canonical_final_request_hash(owner.to_record()))

    def _pin_reference_locked(
        self, ref: str, content: bytes, owner: ContextSnapshotReferenceOwnerV1
    ) -> ContextSnapshotReferencePinV2:
        pin = self._reference_pin(ref, content, owner)
        path = self.reference_pin_path(ref, owner)
        if self._metadata(os.path.dirname(path), optional=True) is not None:
            self._query_snapshot_pins_locked(ref)
        encoded = json.dumps(pin.to_record(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self._write_immutable(path, encoded, kind="pin")
        if pin not in self._query_snapshot_pins_locked(ref):
            raise ContextSnapshotAuditPinError("durable reference pin reread mismatch")
        return pin

    def reconcile_references(
        self,
        references: Iterable[tuple[str, ContextSnapshotReferenceOwnerV1]],
        *,
        validate_snapshot: Callable[[bytes, ContextSnapshotReferenceOwnerV1], None],
    ) -> dict[str, Any]:
        """Protect present host refs under one GC lock; never restore missing bytes.

        Caller must hold its original owner quiescence lock before enumerating.
        A failure aborts activation; pins already committed remain conservative.
        """
        self._require_writable()
        present = 0
        missing: list[str] = []
        committed: set[tuple[str, ContextSnapshotReferenceOwnerV1]] = set()
        with self._exclusive_lock():
            self._assert_current_identity()
            for ref, owner in references:
                path = self.snapshot_path(ref)
                committed.add((ref, owner))
                if self._metadata(path, optional=True) is None:
                    missing.append(ref)
                    continue
                content = self._read_bytes(path)
                if hashlib.sha256(content).hexdigest()[:24] != ref:
                    raise ContextSnapshotAuditPinError("reference snapshot ref/content mismatch")
                validate_snapshot(content, owner)
                self._fsync_and_verify(path, content)
                self._pin_reference_locked(ref, content, owner)
                present += 1
            orphan_pins = sum(
                isinstance(pin, ContextSnapshotReferencePinV2)
                and (pin.context_snapshot_ref, pin.owner) not in committed
                for pin in self._all_pins_locked()
            )
        return {
            "status": "reconciled",
            "present_references": present,
            "unavailable_references": missing,
            "orphan_pins_retained": orphan_pins,
            "replayed_operations": 0,
        }

    def query_reference_pins(self, context_snapshot_ref: str) -> tuple[ContextSnapshotReferencePinV2, ...]:
        with self._exclusive_lock(observe=True):
            self._assert_current_identity()
            return tuple(
                pin
                for pin in self._query_snapshot_pins_locked(context_snapshot_ref)
                if isinstance(pin, ContextSnapshotReferencePinV2)
            )

    @property
    def lock_logical_path(self) -> str:
        """Registered KernelOne lock key shared by producers and retention."""

        return self._lock_logical_path

    @staticmethod
    def canonical_snapshot(snapshot: Mapping[str, Any]) -> tuple[str, str]:
        try:
            content = json.dumps(dict(snapshot), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ContextSnapshotAuditPinError("context snapshot must be JSON safe") from exc
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return content, content_hash[:24]

    def snapshot_path(self, context_snapshot_ref: str) -> str:
        ref = self._validated_ref(context_snapshot_ref)
        return os.path.join(self.contexts_root, ref[:2], ref)

    def pin_path(self, context_snapshot_ref: str, provider_request_id: str) -> str:
        ref = self._validated_ref(context_snapshot_ref)
        provider_id = str(provider_request_id or "").strip()
        if not _PROVIDER_REQUEST_ID_RE.fullmatch(provider_id):
            raise ContextSnapshotAuditPinError("provider_request_id is not path safe")
        return os.path.join(self.contexts_root, "pins", ref[:2], ref, f"{provider_id}.json")

    def persist_snapshot_and_pin(
        self,
        *,
        snapshot: Mapping[str, Any],
        factory_run_id: str,
        role: str,
        verification_scope: str,
        request_freeze_id: str,
        provider_request_id: str,
        composite_request_hash: str,
        snapshot_source: str,
    ) -> ContextSnapshotAuditPinV1:
        self._require_writable()
        content, context_ref = self.canonical_snapshot(snapshot)
        content_bytes = content.encode("utf-8")
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        snapshot_path = self.snapshot_path(context_ref)
        snapshot_logical_path = f"runtime/contexts/{context_ref[:2]}/{context_ref}"
        pin = ContextSnapshotAuditPinV1.create(
            workspace_abs=self.workspace_abs,
            runtime_root=self.runtime_root,
            snapshot_logical_path=snapshot_logical_path,
            snapshot_absolute_path=snapshot_path,
            snapshot_source=snapshot_source,
            factory_run_id=factory_run_id,
            role=role,
            verification_scope=verification_scope,
            request_freeze_id=request_freeze_id,
            provider_request_id=provider_request_id,
            context_snapshot_ref=context_ref,
            storage_identity_token=self.storage_identity_token,
            snapshot_content_hash=content_hash,
            composite_request_hash=composite_request_hash,
        )
        pin_content = json.dumps(pin.to_record(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        pin_bytes = pin_content.encode("utf-8")
        pin_path = self.pin_path(context_ref, provider_request_id)

        with self._exclusive_lock():
            self._assert_current_identity()
            pin_directory = os.path.dirname(pin_path)
            if self._metadata(pin_directory, optional=True) is not None:
                self._query_snapshot_pins_locked(context_ref)
            self._write_immutable(snapshot_path, content_bytes, kind="snapshot")
            self._write_immutable(pin_path, pin_bytes, kind="pin")
            pins = self._query_snapshot_pins_locked(context_ref)
            for persisted in pins:
                if (
                    isinstance(persisted, ContextSnapshotAuditPinV1)
                    and persisted.provider_request_id == provider_request_id
                ):
                    if persisted != pin:
                        raise ContextSnapshotAuditPinError("persisted pin binding mismatch")
                    return persisted
            raise ContextSnapshotAuditPinError("durable pin reread did not return the provider request")

    def query_snapshot_pins(self, context_snapshot_ref: str) -> tuple[ContextSnapshotAuditPinV1, ...]:
        ref = self._validated_ref(context_snapshot_ref)
        with self._exclusive_lock(observe=True):
            self._assert_current_identity()
            return tuple(
                pin for pin in self._query_snapshot_pins_locked(ref) if isinstance(pin, ContextSnapshotAuditPinV1)
            )

    def query_factory_run_pins(self, factory_run_id: str) -> tuple[ContextSnapshotAuditPinV1, ...]:
        """Return validated snapshot pins bound to one exact Factory run.

        Role calls use child run ids, so their journal events are not always
        present in a parent Factory trail.  Audit pins carry the immutable
        parent ``factory_run_id`` and are therefore the durable correlation
        authority for final provider requests.
        """

        requested_run_id = str(factory_run_id or "").strip()
        if not requested_run_id:
            raise ContextSnapshotAuditPinError("factory_run_id must be non-empty")
        with self._exclusive_lock(observe=True):
            self._assert_current_identity()
            return tuple(
                pin
                for pin in self._all_pins_locked()
                if isinstance(pin, ContextSnapshotAuditPinV1) and pin.factory_run_id == requested_run_id
            )

    def _all_pins_locked(self, budget: _InventoryBudget | None = None) -> tuple[SnapshotPin, ...]:
        budget = budget or _InventoryBudget()
        pins_root = os.path.join(self.contexts_root, "pins")
        metadata = self._metadata(pins_root, optional=True)
        if metadata is None:
            return ()
        matched: list[SnapshotPin] = []
        for shard in self._list_directory(pins_root, budget, metadata):
            if not stat.S_ISDIR(shard.metadata.mode) or not re.fullmatch(r"[0-9a-f]{2}", shard.name):
                raise ContextSnapshotAuditPinError("context snapshot pins root contains an invalid shard")
            for entry in self._list_directory(os.path.join(pins_root, shard.name), budget, shard.metadata):
                ref = self._validated_ref(entry.name)
                if not stat.S_ISDIR(entry.metadata.mode) or ref[:2] != shard.name:
                    raise ContextSnapshotAuditPinError("context snapshot pin shard/ref mismatch")
                matched.extend(self._query_snapshot_pins_locked(ref, budget=budget, expected=entry.metadata))
        return tuple(matched)

    def remove_snapshot_if_unpinned(self, snapshot_path: str) -> bool:
        self._require_writable()
        candidate = os.path.abspath(snapshot_path)
        with self._exclusive_lock():
            self._assert_current_identity()
            if not self._is_snapshot_path(candidate):
                raise ContextSnapshotAuditPinError("snapshot deletion path is outside the ContextOS store")
            if self._metadata(candidate, optional=True) is None:
                return False
            return self._remove_snapshot_locked(self._read_snapshot(candidate), _InventoryBudget())

    def _remove_snapshot_locked(self, expected: GuardedRegularFileSnapshotV1, budget: _InventoryBudget) -> bool:
        self._require_writable()
        candidate = os.path.join(self.runtime_root, expected.relative_path)
        if not self._is_snapshot_path(candidate):
            raise ContextSnapshotAuditPinError("snapshot deletion path is outside the ContextOS store")
        ref = os.path.basename(candidate)
        if CONTEXT_HASH_PATTERN.fullmatch(ref) and self._query_snapshot_pins_locked(ref, budget=budget):
            return False
        try:
            guarded_compare_and_remove_regular_file(self.runtime_root, expected, max_bytes=max(1, expected.size))
        except GuardedRegularFileSnapshotError as exc:
            raise ContextStoreInventoryError(str(exc), code=exc.code) from exc
        return True

    def _query_snapshot_pins_locked(
        self,
        context_snapshot_ref: str,
        *,
        budget: _InventoryBudget | None = None,
        expected: FileMetadataV1 | None = None,
    ) -> tuple[SnapshotPin, ...]:
        ref = self._validated_ref(context_snapshot_ref)
        pin_directory = os.path.dirname(self.pin_path(ref, "probe"))
        metadata = expected or self._metadata(pin_directory, optional=True)
        if metadata is None:
            return ()
        entries = self._list_directory(pin_directory, budget or _InventoryBudget(), metadata)
        if not entries:
            raise ContextSnapshotAuditPinError("context snapshot pin directory is present but empty")
        pins: list[SnapshotPin] = []
        for entry in entries:
            if not stat.S_ISREG(entry.metadata.mode) or entry.metadata.nlink != 1 or not entry.name.endswith(".json"):
                raise ContextSnapshotAuditPinError("context snapshot pin directory contains an invalid entry")
            path = os.path.join(pin_directory, entry.name)
            try:
                raw = json.loads(self._read_snapshot(path, entry.metadata).content.decode("utf-8"))
                pin = (
                    ContextSnapshotReferencePinV2.from_record(raw)
                    if isinstance(raw, dict) and raw.get("schema_version") == "llm.context_snapshot_reference_pin.v2"
                    else ContextSnapshotAuditPinV1.from_record(raw)
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ContextSnapshotAuditPinError("context snapshot pin is corrupt or unreadable") from exc
            self._validate_pin_binding(pin, path)
            pins.append(pin)
        snapshot_bytes = self._read_snapshot_bytes_for_pins(ref, pins)
        actual_hash = hashlib.sha256(snapshot_bytes).hexdigest()
        if any(pin.snapshot_content_hash != actual_hash for pin in pins):
            raise ContextSnapshotAuditPinError("pinned context snapshot content hash mismatch")
        return tuple(pins)

    def _validate_pin_binding(self, pin: SnapshotPin, pin_path: str) -> None:
        expected_snapshot_path = self.snapshot_path(pin.context_snapshot_ref)
        expected_logical_path = f"runtime/contexts/{pin.context_snapshot_ref[:2]}/{pin.context_snapshot_ref}"
        expected_pin_path = (
            self.reference_pin_path(pin.context_snapshot_ref, pin.owner)
            if isinstance(pin, ContextSnapshotReferencePinV2)
            else self.pin_path(pin.context_snapshot_ref, pin.provider_request_id)
        )
        if os.path.realpath(pin_path) != os.path.realpath(expected_pin_path):
            raise ContextSnapshotAuditPinError("context snapshot pin filename/provider binding mismatch")
        if (
            os.path.realpath(pin.workspace_abs) != self.workspace_abs
            or os.path.realpath(pin.runtime_root) != self.runtime_root
            or pin.storage_identity_token != self.storage_identity_token
        ):
            raise ContextSnapshotAuditPinError("context snapshot pin workspace/storage identity mismatch")
        if pin.snapshot_logical_path != expected_logical_path or os.path.realpath(
            pin.snapshot_absolute_path
        ) != os.path.realpath(expected_snapshot_path):
            raise ContextSnapshotAuditPinError("context snapshot pin path/source binding mismatch")

    def _read_snapshot_bytes_for_pins(
        self,
        context_snapshot_ref: str,
        pins: list[SnapshotPin],
    ) -> bytes:
        if not pins:
            raise ContextSnapshotAuditPinError("context snapshot pins are required")
        snapshot_path = self.snapshot_path(context_snapshot_ref)
        try:
            content = self._read_bytes(snapshot_path)
        except OSError as exc:
            raise ContextSnapshotAuditPinError("pinned context snapshot is missing or unreadable") from exc
        if hashlib.sha256(content).hexdigest()[:24] != context_snapshot_ref:
            raise ContextSnapshotAuditPinError("pinned context snapshot ref/content mismatch")
        return content

    def _write_immutable(self, path: str, expected: bytes, *, kind: str) -> None:
        self._require_writable()
        try:
            metadata = self._metadata(path, optional=True)
            if metadata is None:
                relative = str(Path(path).relative_to(self.runtime_root))
                guarded_compare_and_create_regular_file(
                    self.runtime_root,
                    relative,
                    expected,
                    max_bytes=max(1, len(expected)),
                )
            elif self._read_snapshot(path, metadata).content != expected:
                raise ContextSnapshotAuditPinError(f"pinned {kind} is immutable and content differs")
        except (OSError, GuardedRegularFileSnapshotError) as exc:
            raise ContextSnapshotAuditPinError(f"{kind} persistence unavailable: {exc}") from exc
        # Preserve the established durability failure type for existing callers.
        self._fsync_and_verify(path, expected)

    def _fsync_and_verify(self, path: str, expected: bytes) -> None:
        self._require_writable()
        snapshot = self._read_snapshot(path)
        if snapshot.content != expected:
            raise OSError("context snapshot or audit pin durable reread mismatch")
        try:
            # Readability after an interrupted create does not confirm durability.
            # Sync the exact immutable object on every publication, including retry.
            guarded_confirm_regular_file_durability(
                self.runtime_root,
                snapshot,
                max_bytes=max(1, snapshot.size),
            )
        except GuardedRegularFileSnapshotError as exc:
            cause = exc.__cause__
            if isinstance(cause, OSError):
                raise OSError(
                    cause.errno, f"ContextOS durability UNKNOWN at {exc.details.get('stage')}: {cause}"
                ) from exc
            raise ContextStoreInventoryError(
                f"ContextOS durability UNKNOWN: {exc}",
                code=exc.code,
            ) from exc

    def _read_bytes(self, path: str) -> bytes:
        return self._read_snapshot(path).content

    @contextmanager
    def _exclusive_lock(self, *, observe: bool = False) -> Iterator[None]:
        """Use the same authority; observations never provision or enroll keys."""
        read_only = self.read_only or observe
        with self._process_lock:
            try:
                if not read_only:
                    LockedRegularFileSetV1.provision_authority(
                        platform_lock_root=self._platform_lock_root,
                        storage_identity_token=self.storage_identity_token,
                        runtime_root=self.runtime_root,
                    )
                    LockedRegularFileSetV1.enroll_stream_lock_keys(
                        platform_lock_root=self._platform_lock_root,
                        storage_identity_token=self.storage_identity_token,
                        runtime_root=self.runtime_root,
                        logical_paths=(self._lock_logical_path,),
                    )
                with LockedRegularFileSetV1.acquire(
                    runtime_root=self.runtime_root,
                    storage_identity_token=self.storage_identity_token,
                    logical_paths=(self._lock_logical_path,),
                    platform_lock_root=self._platform_lock_root,
                    read_only=read_only,
                ):
                    yield
            except LockedRegularFileError as exc:
                raise ContextSnapshotAuditPinError(f"context snapshot audit lock unavailable: {exc.code}") from exc

    def _assert_current_identity(self) -> None:
        current = resolve_workspace_runtime_identity(self.workspace_abs, read_only=True)
        expected_token = self._storage_token(
            workspace_abs=os.path.realpath(current.workspace_abs),
            workspace_key=current.workspace_key,
            runtime_root=self.runtime_root,
        )
        if (
            os.path.realpath(current.workspace_abs) != self.workspace_abs
            or current.workspace_key != self.workspace_key
            or expected_token != self.storage_identity_token
            or (not self.read_only and current.runtime_root != self.runtime_root)
        ):
            raise ContextSnapshotAuditPinError("context snapshot storage identity changed")

    def _is_snapshot_path(self, path: str) -> bool:
        try:
            relative = Path(path).relative_to(Path(self.contexts_root))
        except ValueError:
            return False
        return len(relative.parts) == 2 and relative.parts[0] != "pins"

    @staticmethod
    def _validated_ref(value: str) -> str:
        try:
            return validate_context_hash(str(value or ""))
        except ValueError as exc:
            raise ContextSnapshotAuditPinError("context_snapshot_ref must be exactly 24 lowercase hex") from exc

    @staticmethod
    def _storage_token(*, workspace_abs: str, workspace_key: str, runtime_root: str) -> str:
        token_source = "\x00".join((workspace_abs, workspace_key, runtime_root)).encode("utf-8")
        return hashlib.sha256(token_source).hexdigest()[:24]


@dataclass
class ContextStoreRetentionConfig:
    """Configuration for ``ContextStoreRetention``.

    Attributes:
        ttl_seconds: Wall-clock TTL. Files older than this are dropped first.
        max_total_bytes: Hard cap on the total bytes in ``runtime/contexts/``.
            After TTL, the oldest files are dropped until the tree is under
            this cap.
        max_files: Hard cap on the number of files in ``runtime/contexts/``.
            After TTL, the oldest files are dropped until the tree is under
            this cap.
        sweep_min_interval_seconds: Minimum wall-clock interval between full
            sweeps. ``sweep_if_needed()`` will skip the full sweep when the
            last sweep was within this window AND the gate thresholds are
            all clean.
        enabled: Master switch — when ``False``, ``on_read_gate()`` and
            ``sweep_if_needed()`` are no-ops.
    """

    ttl_seconds: int = 604800  # 7 days
    max_total_bytes: int = 524288000  # 500MB
    max_files: int = 20000
    sweep_min_interval_seconds: int = 300  # 5 minutes
    enabled: bool = True


@dataclass
class SweepReport:
    """Result of a single sweep pass.

    Attributes:
        scanned_files: Number of candidate files visited during the sweep.
        removed_files: Number of files actually removed.
        removed_bytes: Cumulative size of removed files.
        kept_files: Number of candidate files left in the store after sweep.
        total_bytes_after: Total bytes under ``contexts_root`` after sweep.
        elapsed_ms: Wall-clock duration of the sweep in milliseconds.
        triggers: Tags describing why the sweep was triggered.
            Always includes ``"manual"`` for admin-driven sweeps, plus
            ``"ttl"``, ``"max_files"``, ``"max_total_bytes"`` when the
            corresponding cap fired.
    """

    scanned_files: int
    removed_files: int
    removed_bytes: int
    kept_files: int
    total_bytes_after: int
    elapsed_ms: int
    triggers: list[str] = field(default_factory=list)
    inventory_complete: bool = True
    error_code: str | None = None
    error_message: str | None = None
    protected_bytes: int | None = None
    unreclaimable_bytes: int | None = None
    budget_satisfied: bool | None = None


class ContextStoreRetention:
    """Observe and prune ContextOS using its existing cooperative pin authority.

    EOF inventories exclude cooperating writers while the authority is held.
    External namespace changes remain possible; exact guarded CAS protects each
    deletion. Incomplete observations never publish totals or throttle state.
    """

    def __init__(
        self,
        workspace: str | None = None,
        config: ContextStoreRetentionConfig | None = None,
        runtime_base: str | None = None,
        *,
        read_only: bool = False,
    ) -> None:
        from polaris.kernelone.storage import StorageLayout

        self._workspace = workspace or "."
        self._config = config or ContextStoreRetentionConfig()
        self._read_only = read_only
        runtime_root = (
            str(StorageLayout(workspace=self._workspace, runtime_base=runtime_base).runtime_root)
            if runtime_base
            else None
        )
        self._pin_repository = ContextSnapshotAuditPinRepository(
            workspace=self._workspace,
            runtime_root=runtime_root,
            read_only=read_only,
        )
        self._contexts_root = self._pin_repository.contexts_root
        self._runtime_root = self._pin_repository.runtime_root
        self._sweep_state_path = os.path.join(self._contexts_root, SWEEP_STATE_FILENAME)

    @property
    def config(self) -> ContextStoreRetentionConfig:
        return self._config

    @property
    def contexts_root(self) -> str:
        return self._contexts_root

    @property
    def runtime_root(self) -> str:
        return self._runtime_root

    @property
    def sweep_state_path(self) -> str:
        return self._sweep_state_path

    @property
    def pin_repository(self) -> ContextSnapshotAuditPinRepository:
        return self._pin_repository

    def _inventory(self, budget: _InventoryBudget | None = None) -> list[FileMetadataV1]:
        repository = self._pin_repository
        budget = budget or _InventoryBudget()
        result: list[FileMetadataV1] = []
        for shard in repository._list_directory(self._contexts_root, budget):
            if shard.name in {"pins", SWEEP_STATE_FILENAME}:
                required_type = stat.S_ISDIR if shard.name == "pins" else stat.S_ISREG
                if not required_type(shard.metadata.mode) or (
                    shard.name == SWEEP_STATE_FILENAME and shard.metadata.nlink != 1
                ):
                    raise ContextStoreInventoryError("unsafe ContextOS metadata entry")
                continue
            if not stat.S_ISDIR(shard.metadata.mode):
                raise ContextStoreInventoryError("ContextOS shard is not a directory")
            for entry in repository._list_directory(
                os.path.join(self._contexts_root, shard.name), budget, shard.metadata
            ):
                if not stat.S_ISREG(entry.metadata.mode) or entry.metadata.nlink != 1:
                    raise ContextStoreInventoryError("ContextOS shard contains an unsafe file")
                if entry.name != SWEEP_STATE_FILENAME:
                    result.append(entry.metadata)
        return result

    def _iter_candidate_files(self) -> Iterator[tuple[str, float, int]]:
        for metadata in self._inventory():
            yield (os.path.join(self._runtime_root, metadata.relative_path), metadata.mtime_ns / 1e9, metadata.size)

    @staticmethod
    def _state_from_inventory(files: list[FileMetadataV1]) -> dict[str, Any]:
        return {
            "file_count": len(files),
            "total_bytes": sum(item.size for item in files),
            "oldest_mtime": min((item.mtime_ns / 1e9 for item in files), default=None),
        }

    def _gate_state(self) -> dict[str, Any]:
        return self._state_from_inventory(self._inventory())

    def _read_sweep_state(self) -> dict[str, Any]:
        fallback: dict[str, Any] = {"last_sweep_at": 0.0, "last_gate_state": {}}
        metadata = self._pin_repository._metadata(self._sweep_state_path, optional=True)
        if metadata is None:
            return fallback
        try:
            raw = json.loads(
                self._pin_repository._read_snapshot(self._sweep_state_path, metadata).content.decode("utf-8")
            )
            if not isinstance(raw, dict):
                return fallback
            last_sweep_at = float(raw.get("last_sweep_at", 0.0))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return fallback
        gate = raw.get("last_gate_state", {})
        return {"last_sweep_at": last_sweep_at, "last_gate_state": gate if isinstance(gate, dict) else {}}

    def _write_sweep_state(self, last_sweep_at: float, last_gate_state: dict[str, Any]) -> None:
        repository = self._pin_repository
        repository._require_writable()
        payload = json.dumps(
            {"last_sweep_at": last_sweep_at, "last_gate_state": last_gate_state},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        metadata = repository._metadata(self._sweep_state_path, optional=True)
        try:
            if metadata is None:
                guarded_compare_and_create_regular_file(
                    self._runtime_root, "contexts/" + SWEEP_STATE_FILENAME, payload, max_bytes=max(1, len(payload))
                )
            else:
                previous = repository._read_snapshot(self._sweep_state_path, metadata)
                guarded_compare_and_replace_regular_file(
                    self._runtime_root, previous, payload, max_bytes=max(1, len(payload), previous.size)
                )
        except GuardedRegularFileSnapshotError as exc:
            raise ContextStoreInventoryError(str(exc), code=exc.code) from exc

    def get_stats(self) -> dict[str, Any]:
        with self._pin_repository._exclusive_lock(observe=True):
            self._pin_repository._assert_current_identity()
            files = self._inventory()
            counter = self._read_sweep_state()
            state = self._state_from_inventory(files)
            protected_refs = {pin.context_snapshot_ref for pin in self._pin_repository._all_pins_locked()}
            protected_bytes = sum(item.size for item in files if os.path.basename(item.relative_path) in protected_refs)
            return {
                "workspace": str(self._workspace),
                "contexts_root": self._contexts_root,
                **state,
                "protected_bytes": protected_bytes,
                "unreclaimable_bytes": protected_bytes,
                "budget_satisfied": state["total_bytes"] <= self._config.max_total_bytes
                and state["file_count"] <= self._config.max_files,
                "newest_mtime": max((item.mtime_ns / 1e9 for item in files), default=None),
                "config": {
                    "ttl_seconds": self._config.ttl_seconds,
                    "max_total_bytes": self._config.max_total_bytes,
                    "max_files": self._config.max_files,
                    "sweep_min_interval_seconds": self._config.sweep_min_interval_seconds,
                    "enabled": self._config.enabled,
                },
                "last_sweep_at": counter["last_sweep_at"],
            }

    @staticmethod
    def _failure_report(
        exc: Exception, *, start: float, triggers: list[str], scanned: int = 0, removed: int = 0, removed_bytes: int = 0
    ) -> SweepReport:
        return SweepReport(
            scanned_files=scanned,
            removed_files=removed,
            removed_bytes=removed_bytes,
            kept_files=0,
            total_bytes_after=0,
            elapsed_ms=int((time.monotonic() - start) * 1000),
            triggers=triggers,
            inventory_complete=False,
            error_code=getattr(exc, "code", "context_inventory_unavailable"),
            error_message=str(exc),
        )

    def sweep(self, triggers: list[str] | None = None) -> SweepReport:
        start = time.monotonic()
        tags = list(triggers) if triggers else ["manual"]
        scanned = removed = removed_bytes = 0
        repository = self._pin_repository
        try:
            repository._require_writable()
            with repository._exclusive_lock():
                repository._assert_current_identity()
                budget = _InventoryBudget()
                files = self._inventory(budget)
                scanned = len(files)
                # Complete pin validation is a precondition, never an empty-on-error shortcut.
                all_pins = repository._all_pins_locked(budget)
                protected_refs = {pin.context_snapshot_ref for pin in all_pins}
                candidates = sorted(files, key=lambda item: item.mtime_ns)
                kept_count = len(candidates)
                kept_bytes = sum(item.size for item in candidates)
                now = time.time()
                for phase in ("ttl", "max_files", "max_total_bytes"):
                    survivors: list[FileMetadataV1] = []
                    for metadata in candidates:
                        selected = (
                            metadata.mtime_ns / 1e9 < now - self._config.ttl_seconds
                            if phase == "ttl"
                            else kept_count > self._config.max_files
                            if phase == "max_files"
                            else kept_bytes > self._config.max_total_bytes
                        )
                        if selected:
                            if phase not in tags:
                                tags.append(phase)
                            expected = repository._read_snapshot(
                                os.path.join(self._runtime_root, metadata.relative_path), metadata
                            )
                            if repository._remove_snapshot_locked(expected, budget):
                                removed += 1
                                removed_bytes += expected.size
                                kept_count -= 1
                                kept_bytes -= expected.size
                                continue
                        survivors.append(metadata)
                    candidates = survivors
                final = self._inventory(budget)
                state = self._state_from_inventory(final)
                protected_bytes = sum(
                    item.size for item in final if os.path.basename(item.relative_path) in protected_refs
                )
                budget_satisfied = (
                    state["total_bytes"] <= self._config.max_total_bytes
                    and state["file_count"] <= self._config.max_files
                )
                # Failure here also stays explicit; a successful delete is never replayed.
                self._write_sweep_state(now, state)
                return SweepReport(
                    scanned_files=scanned,
                    removed_files=removed,
                    removed_bytes=removed_bytes,
                    kept_files=state["file_count"],
                    total_bytes_after=state["total_bytes"],
                    elapsed_ms=int((time.monotonic() - start) * 1000),
                    triggers=tags,
                    protected_bytes=protected_bytes,
                    unreclaimable_bytes=protected_bytes,
                    budget_satisfied=budget_satisfied,
                )
        except (OSError, ContextSnapshotAuditPinError, ValueError) as exc:
            return self._failure_report(
                exc, start=start, triggers=tags, scanned=scanned, removed=removed, removed_bytes=removed_bytes
            )

    def sweep_if_needed(self) -> SweepReport | None:
        if not self._config.enabled:
            return None
        start = time.monotonic()
        try:
            self._pin_repository._require_writable()
            with self._pin_repository._exclusive_lock():
                self._pin_repository._assert_current_identity()
                state = self._gate_state()
                counter = self._read_sweep_state()
            now = time.time()
            triggers: list[str] = []
            if state["file_count"] > self._config.max_files:
                triggers.append("max_files")
            if state["total_bytes"] > self._config.max_total_bytes:
                triggers.append("max_total_bytes")
            if state["oldest_mtime"] is not None and state["oldest_mtime"] < now - self._config.ttl_seconds:
                triggers.append("ttl")
            if not triggers and now - counter["last_sweep_at"] < self._config.sweep_min_interval_seconds:
                return None
            return self.sweep(triggers=triggers)
        except (OSError, ContextSnapshotAuditPinError, ValueError) as exc:
            return self._failure_report(exc, start=start, triggers=["gate"])

    def on_read_gate(self) -> dict[str, Any]:
        try:
            report = self.sweep_if_needed()
            if report is not None and not report.inventory_complete:
                return {
                    "inventory_complete": False,
                    "error_code": report.error_code,
                    "error_message": report.error_message,
                }
            with self._pin_repository._exclusive_lock():
                self._pin_repository._assert_current_identity()
                return self._gate_state()
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            return {
                "inventory_complete": False,
                "error_code": getattr(exc, "code", "context_inventory_unavailable"),
                "error_message": str(exc),
            }


# ---------------------------------------------------------------------------
# Module-level lazy singleton cache
# ---------------------------------------------------------------------------
_RETENTION_CACHE: dict[str, ContextStoreRetention] = {}


def get_retention(workspace: str | None = None) -> ContextStoreRetention:
    """Return a per-workspace cached :class:`ContextStoreRetention`.

    The retention is layout-scoped to ``storage.get_path('runtime',
    'contexts')``, so caching by workspace is correct. Test code that
    needs a fresh instance (with a different config) should construct
    ``ContextStoreRetention(...)`` directly.
    """
    key = str(workspace or ".")
    cached = _RETENTION_CACHE.get(key)
    if cached is not None:
        return cached
    instance = ContextStoreRetention(workspace=key)
    _RETENTION_CACHE[key] = instance
    return instance


def clear_retention_cache() -> None:
    """Reset the per-workspace cache (used in tests)."""
    _RETENTION_CACHE.clear()


__all__ = [
    "ContextStoreRetention",
    "ContextStoreRetentionConfig",
    "SweepReport",
    "clear_retention_cache",
    "get_retention",
]
