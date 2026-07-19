"""Stable service exports for events.fact_stream."""

from __future__ import annotations

from typing import Any

# Debug-tracing controls are part of this cell's observable surface: delivery
# and bootstrap layers must use these public re-exports instead of importing
# from the internal module directly.
from polaris.cells.events.fact_stream.internal.debug_trace import (
    configure_debug_tracing,
    emit_debug_event,
    install_global_debug_hooks,
    is_debug_tracing_enabled,
    log_stream_token,
    sanitize_headers,
    set_debug_tracing_enabled,
)
from polaris.kernelone.events.sourcing import (
    EventSourcingError,
    JsonlEventStore,
    SegmentedEventStoreError,
    SegmentedJsonlEventStore,
    SegmentedLedgerHeadV1,
    append_if_guarded_snapshot as _append_if_guarded_snapshot,
    read_guarded_fact_snapshot as _read_guarded_fact_snapshot,
)
from polaris.kernelone.fs import LockedRegularFileError, LockedRegularFileSetV1, LockMaintenanceProofV1
from polaris.kernelone.fs.locked_regular_file import default_platform_lock_root

from .contracts import (
    AppendFactEventCommandV1,
    AppendIfGuardedSnapshotCommandV1,
    AppendSegmentedFactEventCommandV1,
    EnrollFactStreamStreamsCommandV1,
    EnsureSegmentedFactLedgerCommandV1,
    FactEventAppendedV1,
    FactStreamError,
    FactStreamHeadV1,
    FactStreamLockIdentityV1,
    FactStreamLockKeyEvidenceV1,
    FactStreamMaintenanceProofV1,
    FactStreamMaintenanceReceiptV1,
    FactStreamProvenanceV1,
    FactStreamQueryResultV1,
    GuardedFactAppendedV1,
    GuardedFactSnapshotV1,
    ProvisionFactStreamLockAuthorityCommandV1,
    QueryFactEventsV1,
    QueryFactStreamHeadV1,
    QuerySegmentedFactEventsV1,
    QuerySegmentedFactLedgerHeadV1,
    ReadGuardedFactSnapshotCommandV1,
    SegmentedFactEventAppendedV1,
    SegmentedFactLedgerHeadV1,
    SegmentedFactLedgerReadyV1,
    SegmentedFactQueryResultV1,
)

_SEGMENTED_AUTHORITY_PREFIXES = (
    "roles.kernel.provider_attempts.factory.",
    "roles.kernel.provider_attempts.session.",
    "factory.role_evidence_authority.",
)


def provision_fact_stream_lock_authority(
    command: ProvisionFactStreamLockAuthorityCommandV1,
) -> FactStreamMaintenanceReceiptV1:
    """Provision one immutable FactStream lock authority for maintenance.

    ``maintenance_reason`` is audit-facing command intent. It never changes
    the established binding and is deliberately not persisted in authority
    state, which remains limited to physical identity and format fields.
    """

    _reject_ordinary_segmented_streams(command.streams)
    store, root = _maintenance_store(command.workspace, command.platform_lock_root)
    identity = store.storage_identity
    try:
        kernel_proof = LockedRegularFileSetV1.provision_authority(
            platform_lock_root=root,
            storage_identity_token=identity.token,
            runtime_root=identity.runtime_root,
        )
    except LockedRegularFileError as exc:
        raise _maintenance_failure("provision_fact_stream_lock_authority", exc) from exc
    except (ValueError, EventSourcingError) as exc:
        raise _maintenance_failure("provision_fact_stream_lock_authority", exc) from exc

    proofs: tuple[FactStreamMaintenanceProofV1, ...] = (_project_maintenance_proof(kernel_proof),)
    if command.streams:
        # Compatibility for the earlier combined public command. New callers use
        # the dedicated enrollment command or the bootstrap application service.
        enrollment = enroll_fact_stream_streams(
            EnrollFactStreamStreamsCommandV1(
                workspace=command.workspace,
                streams=command.streams,
                maintenance_reason=command.maintenance_reason,
                platform_lock_root=command.platform_lock_root,
            )
        )
        proofs += enrollment.proofs
    return FactStreamMaintenanceReceiptV1(
        workspace=identity.workspace_abs,
        storage_identity_token=identity.token,
        maintenance_reason=command.maintenance_reason,
        operation="provision_authority",
        streams=command.streams,
        proofs=proofs,
    )


def enroll_fact_stream_streams(
    command: EnrollFactStreamStreamsCommandV1,
) -> FactStreamMaintenanceReceiptV1:
    """Enroll stream lock keys without provisioning or repairing authority."""

    _reject_ordinary_segmented_streams(command.streams)
    store, root = _maintenance_store(command.workspace, command.platform_lock_root)
    identity = store.storage_identity
    logical_paths = tuple(store.stream_logical_path(stream) for stream in command.streams)
    try:
        proof = LockedRegularFileSetV1.enroll_stream_lock_keys(
            platform_lock_root=root,
            storage_identity_token=identity.token,
            runtime_root=identity.runtime_root,
            logical_paths=logical_paths,
        )
    except LockedRegularFileError as exc:
        raise _maintenance_failure("enroll_fact_stream_streams", exc) from exc
    except (ValueError, EventSourcingError) as exc:
        raise _maintenance_failure("enroll_fact_stream_streams", exc) from exc
    return FactStreamMaintenanceReceiptV1(
        workspace=identity.workspace_abs,
        storage_identity_token=identity.token,
        maintenance_reason=command.maintenance_reason,
        operation="enroll_streams",
        streams=command.streams,
        proofs=(_project_maintenance_proof(proof),),
    )


def _project_maintenance_proof(proof: LockMaintenanceProofV1) -> FactStreamMaintenanceProofV1:
    """Project immutable KernelOne maintenance evidence into the Cell contract."""

    return FactStreamMaintenanceProofV1(
        operation=proof.operation,
        verdict=proof.verdict,
        storage_identity_token=proof.storage_identity,
        runtime_root=proof.runtime_root,
        format_revision=proof.format_revision,
        root_identity=FactStreamLockIdentityV1(
            device=proof.root_identity.device,
            inode=proof.root_identity.inode,
        ),
        anchor_identity=FactStreamLockIdentityV1(
            device=proof.anchor_identity.device,
            inode=proof.anchor_identity.inode,
        ),
        realm_identity=FactStreamLockIdentityV1(
            device=proof.realm_identity.device,
            inode=proof.realm_identity.inode,
        ),
        lock_keys=tuple(
            FactStreamLockKeyEvidenceV1(
                logical_path=item.logical_path,
                lock_key=item.lock_key,
                verdict=item.verdict,
                identity=FactStreamLockIdentityV1(
                    device=item.identity.device,
                    inode=item.identity.inode,
                ),
            )
            for item in proof.lock_keys
        ),
        final_validation=proof.final_validation,
    )


def _maintenance_store(
    workspace: str,
    platform_lock_root: str | None,
) -> tuple[JsonlEventStore, str]:
    """Resolve immutable authority inputs for one explicit maintenance call."""

    store = JsonlEventStore(workspace)
    return store, platform_lock_root or default_platform_lock_root()


def _maintenance_failure(operation: str, exc: Exception) -> FactStreamError:
    """Preserve exact KernelOne maintenance evidence at the public boundary."""

    if isinstance(exc, LockedRegularFileError):
        return FactStreamError(str(exc), code=exc.code, details=dict(exc.details))
    return FactStreamError(
        f"{operation} failed: {exc}",
        code=_fact_stream_failure_code("lock_authority_provision", exc),
        details=_failure_details(exc),
    )


def read_guarded_fact_snapshot(command: ReadGuardedFactSnapshotCommandV1) -> GuardedFactSnapshotV1:
    """Prepare a strict immutable two-stream witness through FactStream."""

    try:
        return _read_guarded_fact_snapshot(command)
    except (ValueError, EventSourcingError) as exc:
        raise FactStreamError(
            f"read_guarded_fact_snapshot failed: {exc}",
            code=_guarded_failure_code(exc),
            details=_guarded_failure_details(exc),
        ) from exc


def append_if_guarded_snapshot(command: AppendIfGuardedSnapshotCommandV1) -> GuardedFactAppendedV1:
    """Commit one fsync target fact only when its proof still matches."""

    try:
        return _append_if_guarded_snapshot(command)
    except (ValueError, EventSourcingError) as exc:
        raise FactStreamError(
            f"append_if_guarded_snapshot failed: {exc}",
            code=_guarded_failure_code(exc),
            details=_guarded_failure_details(exc),
        ) from exc


def append_fact_event(command: AppendFactEventCommandV1) -> FactEventAppendedV1:
    """Append an immutable fact event to the canonical runtime stream.

    When ``command.expected_seq`` is provided (opt-in CAS), the underlying
    store allocates that exact sequence number and fail-closes if the
    stream's next free number doesn't match. Idempotent hits return the
    existing event unchanged; if the caller supplied ``expected_seq`` but
    the existing event's seq doesn't match the request, we fail-closed
    rather than silently returning a mismatched event.
    """
    _reject_ordinary_segmented_stream(command.stream)
    idempotency_key = str(command.idempotency_key or "").strip()
    store: JsonlEventStore | None = None
    try:
        store = JsonlEventStore(command.workspace)
        effective_run_id, effective_task_id = _resolve_provenance_envelope(
            command=command,
            workspace_abs=store.storage_identity.workspace_abs,
        )
        metadata: dict[str, Any] = _compact_metadata(
            {
                "run_id": effective_run_id,
                "task_id": effective_task_id,
                "correlation_id": command.correlation_id,
                "idempotency_key": idempotency_key,
            }
        )
        _add_provenance_metadata(
            metadata=metadata,
            provenance=command.provenance,
            storage_identity=store.storage_identity.to_record(),
        )
        event = store.append(
            stream=command.stream,
            event_type=command.event_type,
            source=command.source,
            payload=command.payload,
            event_version=1,
            aggregate_id=effective_task_id or effective_run_id,
            correlation_id=command.correlation_id,
            metadata=metadata,
            expected_seq=command.expected_seq,
            idempotency_key=idempotency_key,
            durability=command.durability,
            strict_integrity=command.strict_integrity,
        )
    except (ValueError, EventSourcingError) as exc:
        # Translate generic store failures to FactStreamError so callers
        # see a single error type. Use a dedicated code when the underlying
        # message clearly identifies a CAS drift so consumers can branch.
        # Note: the idempotent drift raise inside this try raises
        # FactStreamError (a RuntimeError subclass) which is NOT in this
        # except tuple, so it propagates unchanged.
        code = _fact_stream_failure_code("append", exc)
        details: dict[str, Any] = {
            "workspace": command.workspace,
            "stream": command.stream,
            "event_type": command.event_type,
            "expected_seq": command.expected_seq,
            "durability": command.durability,
            "strict_integrity": command.strict_integrity,
        }
        details.update(_failure_details(exc))
        if code == "expected_seq_drift" and idempotency_key and store is not None:
            existing = _find_existing_idempotent_event(
                store=store,
                stream=command.stream,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                details["existing_seq"] = int(existing.seq)
                details["event_id"] = existing.event_id
        raise FactStreamError(
            f"append_fact_event failed: {exc}",
            code=code,
            details=details,
        ) from exc

    return FactEventAppendedV1(
        event_id=event.event_id,
        workspace=command.workspace,
        stream=command.stream,
        storage_path=store.stream_logical_path(command.stream),
        appended_at=event.occurred_at,
        appended_seq=int(event.seq),
    )


def _find_existing_idempotent_event(
    *,
    store: JsonlEventStore,
    stream: str,
    idempotency_key: str,
) -> Any | None:
    offset = 0
    while True:
        result = store.query(stream=stream, limit=1000, offset=offset)
        for event in result.events:
            if str(event.metadata.get("idempotency_key") or "").strip() == idempotency_key:
                return event
        if result.next_offset == 0:
            return None
        offset = result.next_offset


def query_fact_events(query: QueryFactEventsV1) -> FactStreamQueryResultV1:
    """Query canonical fact events with pagination and optional filters."""
    _reject_ordinary_segmented_stream(query.stream)
    try:
        store = JsonlEventStore(query.workspace)
        result = store.query(
            stream=query.stream,
            limit=query.limit,
            offset=query.offset,
            event_type=query.event_type,
            run_id=query.run_id,
            task_id=query.task_id,
            strict_integrity=query.strict_integrity,
        )
    except (ValueError, EventSourcingError) as exc:
        raise FactStreamError(
            f"query_fact_events failed: {exc}",
            code=_fact_stream_failure_code("query", exc),
            details={
                "workspace": query.workspace,
                "stream": query.stream,
                "offset": query.offset,
                "limit": query.limit,
                "strict_integrity": query.strict_integrity,
                **_failure_details(exc),
            },
        ) from exc

    event_payloads = tuple(
        item.to_record(include_integrity_digest=True) if query.strict_integrity else _event_to_dict(item.to_record())
        for item in result.events
    )
    return FactStreamQueryResultV1(
        workspace=query.workspace,
        stream=query.stream,
        events=event_payloads,
        total=result.total,
        next_offset=result.next_offset,
    )


def query_fact_stream_head(query: QueryFactStreamHeadV1) -> FactStreamHeadV1:
    """Return the durable stream cursor through the FactStream boundary."""

    _reject_ordinary_segmented_stream(query.stream)

    try:
        store = JsonlEventStore(query.workspace)
        current_seq = store.current_seq(query.stream, strict_integrity=query.strict_integrity)
    except (ValueError, EventSourcingError) as exc:
        raise FactStreamError(
            f"query_fact_stream_head failed: {exc}",
            code=_fact_stream_failure_code("head_query", exc),
            details={
                "workspace": query.workspace,
                "stream": query.stream,
                "strict_integrity": query.strict_integrity,
                **_failure_details(exc),
            },
        ) from exc
    return FactStreamHeadV1(
        workspace=query.workspace,
        stream=query.stream,
        storage_path=store.stream_logical_path(query.stream),
        current_seq=current_seq,
        next_expected_seq=current_seq + 1,
    )


def ensure_segmented_fact_ledger(
    command: EnsureSegmentedFactLedgerCommandV1,
) -> SegmentedFactLedgerReadyV1:
    """Enroll one dynamic logical lock under existing workspace authority."""

    _require_segmented_authority_stream(command.logical_stream)
    store = SegmentedJsonlEventStore(command.workspace, logical_stream=command.logical_stream)
    identity = store.storage_identity
    try:
        LockedRegularFileSetV1.enroll_stream_lock_keys(
            platform_lock_root=default_platform_lock_root(),
            storage_identity_token=identity.token,
            runtime_root=identity.runtime_root,
            logical_paths=(store.control_logical_path,),
        )
        head = store.ensure()
    except (LockedRegularFileError, SegmentedEventStoreError, ValueError) as exc:
        raise _segmented_failure("ensure_segmented_fact_ledger", command.logical_stream, exc) from exc
    projected = _project_segmented_head(identity.workspace_abs, head)
    return SegmentedFactLedgerReadyV1(
        workspace=identity.workspace_abs,
        logical_stream=command.logical_stream,
        storage_prefix=head.storage_prefix,
        storage_identity_token=identity.token,
        retention=command.retention,
        head=projected,
    )


def append_segmented_fact_event(
    command: AppendSegmentedFactEventCommandV1,
) -> SegmentedFactEventAppendedV1:
    """Append one strict fsync fact through the segmented authority API."""

    _require_segmented_authority_stream(command.logical_stream)
    store = SegmentedJsonlEventStore(command.workspace, logical_stream=command.logical_stream)
    try:
        event = store.append(
            event_type=command.event_type,
            source=command.source,
            payload=command.payload,
            idempotency_key=command.idempotency_key,
            expected_global_seq=command.expected_global_seq,
            require_idempotency_replay=command.require_idempotency_replay,
            durability=command.durability,
        )
    except (LockedRegularFileError, SegmentedEventStoreError, ValueError) as exc:
        raise _segmented_failure("append_segmented_fact_event", command.logical_stream, exc) from exc
    return SegmentedFactEventAppendedV1(
        workspace=store.storage_identity.workspace_abs,
        logical_stream=command.logical_stream,
        event_id=event.event_id,
        global_seq=event.global_seq,
        segment_index=event.segment_index,
        local_seq=event.local_seq,
        event_hash=event.event_hash,
        appended_at=event.occurred_at,
    )


def query_segmented_fact_ledger_head(
    query: QuerySegmentedFactLedgerHeadV1,
) -> SegmentedFactLedgerHeadV1:
    _require_segmented_authority_stream(query.logical_stream)
    store = SegmentedJsonlEventStore(query.workspace, logical_stream=query.logical_stream)
    try:
        head = store.head(strict_integrity=query.strict_integrity)
    except (LockedRegularFileError, SegmentedEventStoreError, ValueError) as exc:
        raise _segmented_failure("query_segmented_fact_ledger_head", query.logical_stream, exc) from exc
    return _project_segmented_head(store.storage_identity.workspace_abs, head)


def query_segmented_fact_events(
    query: QuerySegmentedFactEventsV1,
) -> SegmentedFactQueryResultV1:
    _require_segmented_authority_stream(query.logical_stream)
    store = SegmentedJsonlEventStore(query.workspace, logical_stream=query.logical_stream)
    try:
        result = store.query(
            limit=query.limit,
            continuation=query.continuation,
            strict_integrity=query.strict_integrity,
        )
    except (LockedRegularFileError, SegmentedEventStoreError, ValueError) as exc:
        raise _segmented_failure("query_segmented_fact_events", query.logical_stream, exc) from exc
    events = tuple(
        {
            "event_id": event.event_id,
            "logical_stream": event.logical_stream,
            "global_seq": event.global_seq,
            "segment_index": event.segment_index,
            "local_seq": event.local_seq,
            "event_type": event.event_type,
            "source": event.source,
            "payload": dict(event.payload),
            "idempotency_key": event.idempotency_key,
            "occurred_at": event.occurred_at,
            "previous_event_hash": event.previous_event_hash,
            "event_hash": event.event_hash,
        }
        for event in result.events
    )
    return SegmentedFactQueryResultV1(
        workspace=store.storage_identity.workspace_abs,
        logical_stream=query.logical_stream,
        events=events,
        captured_head=_project_segmented_head(store.storage_identity.workspace_abs, result.captured_head),
        continuation=result.continuation,
    )


def _project_segmented_head(workspace: str, head: SegmentedLedgerHeadV1) -> SegmentedFactLedgerHeadV1:
    return SegmentedFactLedgerHeadV1(
        workspace=workspace,
        logical_stream=head.logical_stream,
        storage_prefix=head.storage_prefix,
        total_count=head.total_count,
        segment_count=head.segment_count,
        global_seq=head.global_seq,
        next_expected_global_seq=head.global_seq + 1,
        tail_segment_index=head.tail_segment_index,
        tail_local_seq=head.tail_local_seq,
        head_hash=head.head_hash,
        storage_bytes=head.storage_bytes,
    )


def _is_segmented_authority_stream(stream: str) -> bool:
    token = str(stream or "").strip()
    return any(token.startswith(prefix) and len(token) > len(prefix) for prefix in _SEGMENTED_AUTHORITY_PREFIXES)


def _require_segmented_authority_stream(stream: str) -> None:
    if not _is_segmented_authority_stream(stream):
        raise FactStreamError(
            "logical stream is not an approved segmented authority namespace",
            code="segmented_stream_namespace_required",
            details={"stream": stream},
        )


def _reject_ordinary_segmented_stream(stream: str) -> None:
    token = str(stream or "").strip()
    if any(token.startswith(prefix) for prefix in _SEGMENTED_AUTHORITY_PREFIXES) or ".segmented" in token:
        raise FactStreamError(
            "segmented authority streams require the typed segmented API",
            code="segmented_stream_api_required",
            details={"stream": token},
        )


def _reject_ordinary_segmented_streams(streams: tuple[str, ...]) -> None:
    for stream in streams:
        _reject_ordinary_segmented_stream(stream)


def _segmented_failure(operation: str, stream: str, exc: Exception) -> FactStreamError:
    code = str(getattr(exc, "code", "") or "segmented_fact_ledger_failed")
    details = dict(getattr(exc, "details", {}) or {})
    details.setdefault("stream", stream)
    details.setdefault("operation", operation)
    return FactStreamError(f"{operation} failed: {exc}", code=code, details=details)


def _compact_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in payload.items():
        token = str(value or "").strip()
        if token:
            compact[str(key)] = token
    return compact


def _fact_stream_failure_code(operation: str, exc: Exception) -> str:
    """Map KernelOne strict evidence to stable FactStream failure codes."""

    if isinstance(exc, EventSourcingError):
        if exc.code in _STRICT_FAILURE_CODES:
            return "strict_stream_corruption"
        return exc.code
    return f"{operation}_failed"


_STRICT_FAILURE_CODES = frozenset(
    {
        "torn_tail",
        "sequence_violation",
        "stream_corruption",
        "strict_record_corruption",
        "unknown_schema_version",
        "unknown_event_version",
    }
)


def _guarded_failure_code(exc: Exception) -> str:
    """Project guarded strict failures without operation-specific generic codes."""

    raw_code = str(getattr(exc, "code", "") or "").strip()
    if raw_code in _STRICT_FAILURE_CODES:
        return "strict_stream_corruption"
    return raw_code or "fact_stream_error"


def _guarded_failure_details(exc: Exception) -> dict[str, Any]:
    """Preserve strict parser evidence beneath the stable public category."""

    details = _failure_details(exc)
    raw_code = str(getattr(exc, "code", "") or "").strip()
    if raw_code in _STRICT_FAILURE_CODES:
        details["strict_failure_code"] = raw_code
    return details


def _failure_details(exc: Exception) -> dict[str, Any]:
    """Detach KernelOne typed evidence for a FactStream public failure."""

    details = getattr(exc, "details", None)
    detached = dict(details) if isinstance(details, dict) else {}
    raw_code = str(getattr(exc, "code", "") or "").strip()
    if raw_code in _STRICT_FAILURE_CODES:
        detached.setdefault("strict_failure_code", raw_code)
    return detached


def _add_provenance_metadata(
    *,
    metadata: dict[str, Any],
    provenance: FactStreamProvenanceV1 | None,
    storage_identity: dict[str, str],
) -> None:
    """Attach already-validated transition provenance."""

    if provenance is None:
        return
    metadata["provenance"] = provenance.to_record()
    metadata["storage_identity"] = dict(storage_identity)


def _resolve_provenance_envelope(
    *,
    command: AppendFactEventCommandV1,
    workspace_abs: str,
) -> tuple[str | None, str | None]:
    """Resolve one run/task envelope and reject contradictory provenance."""

    provenance = command.provenance
    if provenance is None:
        return command.run_id, command.task_id

    mismatches: list[str] = []
    if provenance.workspace != workspace_abs:
        mismatches.append("workspace")
    if command.run_id is not None and command.run_id != provenance.run_id:
        mismatches.append("run_id")
    if command.task_id is not None and command.task_id != provenance.task_id:
        mismatches.append("task_id")
    if mismatches:
        raise FactStreamError(
            "fact provenance workspace/run/task does not match the append command envelope "
            f"fields={','.join(mismatches)}",
            code="provenance_mismatch",
            details={
                "fields": tuple(mismatches),
                "command_workspace": command.workspace,
                "resolved_workspace": workspace_abs,
                "provenance_workspace": provenance.workspace,
                "command_run_id": command.run_id,
                "provenance_run_id": provenance.run_id,
                "command_task_id": command.task_id,
                "provenance_task_id": provenance.task_id,
            },
        )

    # Compatibility boundary: omitted optional command fields inherit the
    # required typed provenance values; explicit fields must match exactly.
    return command.run_id or provenance.run_id, command.task_id or provenance.task_id


def _event_to_dict(record: dict[str, Any]) -> dict[str, Any]:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    event = dict(record)
    if "run_id" not in event:
        run_id = (
            str(metadata.get("run_id") or payload.get("run_id") or "").strip()
            if isinstance(metadata, dict) and isinstance(payload, dict)
            else ""
        )
        if run_id:
            event["run_id"] = run_id
    if "task_id" not in event:
        task_id = (
            str(metadata.get("task_id") or payload.get("task_id") or "").strip()
            if isinstance(metadata, dict) and isinstance(payload, dict)
            else ""
        )
        if task_id:
            event["task_id"] = task_id
    return event


__all__ = [
    "AppendFactEventCommandV1",
    "AppendIfGuardedSnapshotCommandV1",
    "FactEventAppendedV1",
    "FactStreamError",
    "FactStreamHeadV1",
    "FactStreamProvenanceV1",
    "FactStreamQueryResultV1",
    "GuardedFactAppendedV1",
    "GuardedFactSnapshotV1",
    "QueryFactEventsV1",
    "QueryFactStreamHeadV1",
    "ReadGuardedFactSnapshotCommandV1",
    "append_fact_event",
    "append_if_guarded_snapshot",
    "configure_debug_tracing",
    "emit_debug_event",
    "install_global_debug_hooks",
    "is_debug_tracing_enabled",
    "log_stream_token",
    "query_fact_events",
    "query_fact_stream_head",
    "read_guarded_fact_snapshot",
    "sanitize_headers",
    "set_debug_tracing_enabled",
]
