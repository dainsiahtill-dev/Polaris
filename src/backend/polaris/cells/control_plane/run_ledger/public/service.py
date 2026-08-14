"""Platform run-ledger projection service.

This public boundary intentionally avoids temporary internal harness naming.
Stress harnesses may still write migration ledger files while writer
migration is in progress; formal consumers should call this service or its
HTTP facade.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.cells.control_plane.run_ledger.public.contracts import (
    AppendRunLedgerEventCommandV1,
    AppendToolCallLifecycleEventCommandV1,
    ReadRunLedgerProjectionBarrierQueryV1,
    ReadRunLedgerProjectionQueryV1,
    ReadRunProvenanceBundleQueryV1,
    RunLedgerAppendResultV1,
    RunLedgerProjectionBarrierResultV1,
    RunLedgerProjectionResultV1,
    RunProvenanceBundleResultV1,
)
from polaris.cells.control_plane.run_ledger.public.ledger import RunLedger
from polaris.cells.control_plane.run_ledger.public.projection import (
    build_run_ledger_projection,
    summarize_run_ledger_projection,
)
from polaris.cells.control_plane.run_ledger.public.provenance import build_run_provenance_bundle
from polaris.cells.control_plane.run_ledger.public.settlement_barrier import (
    FactorySettlementBarrierQueryV1,
    FactorySettlementBarrierResultV1,
    project_factory_settlement_barrier,
)
from polaris.cells.control_plane.run_ledger.public.tool_lifecycle import (
    ToolLifecycleRequirementV1,
    build_tool_call_lifecycle_run_ledger_event,
    build_tool_lifecycle_requirement_run_ledger_event,
    empty_tool_lifecycle_summary,
    merge_tool_lifecycle_summaries,
)
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    FactEventAppendedV1,
    QueryFactEventsV1,
    QueryFactStreamHeadV1,
    append_fact_event,
    query_fact_events,
    query_fact_stream_head,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    TASK_RUNTIME_EXECUTION_STREAM_V1,
)
from polaris.infrastructure.log_pipeline.jetstream_publisher import get_log_jetstream_publisher
from polaris.kernelone.storage import resolve_storage_roots

logger = logging.getLogger(__name__)
_JETSTREAM_PUBLISH_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_EXECUTION_CONTROL_PLANE_STREAM = "execution.control_plane"
_RUN_LEDGER_FACT_PAYLOAD_KEYS = frozenset({"schema_version", "run_id", "event"})
_RUN_LEDGER_EVENT_IDENTITY_KEYS = frozenset({"event_id", "content_id", "append_id"})
_RUN_LEDGER_FACT_PROOF_PAGE_SIZE = 1000
# Keep the recovery proof no broader than both authoritative stores: FactStream
# strict reads and RunLedger projections each cap canonical records at 4096.
_RUN_LEDGER_FACT_PROOF_MAX_RECORDS = 4096
_RUN_LEDGER_FACT_PROOF_MAX_PAGES = (
    _RUN_LEDGER_FACT_PROOF_MAX_RECORDS + _RUN_LEDGER_FACT_PROOF_PAGE_SIZE - 1
) // _RUN_LEDGER_FACT_PROOF_PAGE_SIZE
_TASK_RUNTIME_SCOPE_FACT_CACHE_MAX = 4
_TASK_RUNTIME_SCOPE_FACT_CACHE: OrderedDict[
    tuple[str, int, str, str, str], tuple[dict[str, Any], ...]
] = OrderedDict()
_TASK_RUNTIME_SCOPE_FACT_CACHE_LOCK = threading.RLock()


@dataclass(frozen=True, slots=True)
class _RunLedgerFactAuthority:
    canonical_event: dict[str, Any]
    projection_row: dict[str, Any]
    projection_bytes: bytes
    receipt: FactEventAppendedV1


def _is_run_ledger_fact_candidate(fact: dict[str, Any]) -> bool:
    """Identify every suspicious Run Ledger authority for strict validation.

    The union deliberately includes both canonical outer markers and structural
    identity markers.  A damaged Fact must not evade completeness proof merely
    because its source, schema, and idempotency marker were corrupted together.
    Obviously unrelated control-plane Facts remain outside this recovery-only
    boundary unless they have the exact Run Ledger envelope or event identity.
    """

    payload = fact.get("payload")
    fact_payload = payload if isinstance(payload, dict) else {}
    metadata = fact.get("metadata")
    fact_metadata = metadata if isinstance(metadata, dict) else {}
    raw_idempotency_key = fact_metadata.get("idempotency_key")
    raw_event = fact_payload.get("event")
    has_event_identity = type(raw_event) is dict and _RUN_LEDGER_EVENT_IDENTITY_KEYS.issubset(raw_event)
    return bool(
        fact.get("source") == "control_plane.run_ledger"
        or fact_payload.get("schema_version") == "execution.control_plane.fact.v1"
        or (isinstance(raw_idempotency_key, str) and raw_idempotency_key.startswith("run-ledger:"))
        or set(fact_payload) == _RUN_LEDGER_FACT_PAYLOAD_KEYS
        or has_event_identity
    )


def _build_run_ledger_fact_authority(
    *,
    fact: dict[str, Any],
    fact_event_id: str,
    fact_seq: int,
    workspace: Path,
    run_id: str,
    ledger: RunLedger,
) -> _RunLedgerFactAuthority | None:
    """Strictly reconstruct one candidate Fact authority, or ignore unrelated evidence."""

    payload = fact.get("payload")
    fact_payload = payload if isinstance(payload, dict) else {}
    metadata = fact.get("metadata")
    fact_metadata = metadata if isinstance(metadata, dict) else {}
    if not _is_run_ledger_fact_candidate(fact):
        return None
    raw_event = fact_payload.get("event")
    if type(raw_event) is not dict:
        raise ValueError("run_ledger_projection_corrupt:partial_tail_fact_noncanonical")
    try:
        canonical_fact_event = ledger.prepare_idempotent_event(raw_event)
    except ValueError as exc:
        raise ValueError("run_ledger_projection_corrupt:partial_tail_fact_noncanonical") from exc
    expected_event_type = str(canonical_fact_event.get("event_type") or "control_plane_event").strip()
    expected_task_id = str(canonical_fact_event.get("task_id") or "").strip()
    expected_correlation_id = str(
        canonical_fact_event.get("turn_id") or canonical_fact_event.get("event_id") or ""
    ).strip()
    expected_idempotency_key = ledger.fact_idempotency_key(canonical_fact_event)
    expected_aggregate_id = expected_task_id or run_id
    if (
        fact.get("source") != "control_plane.run_ledger"
        or fact.get("stream") != _EXECUTION_CONTROL_PLANE_STREAM
        or fact.get("event_type") != expected_event_type
        or fact.get("event_version") != 1
        or fact.get("aggregate_id") != expected_aggregate_id
        or fact.get("correlation_id") != expected_correlation_id
        or fact.get("causation_id") is not None
        or set(fact_payload) != _RUN_LEDGER_FACT_PAYLOAD_KEYS
        or fact_payload.get("schema_version") != "execution.control_plane.fact.v1"
        or fact_payload.get("run_id") != run_id
        or raw_event != canonical_fact_event
        or fact_metadata.get("idempotency_key") != expected_idempotency_key
        or fact_metadata.get("run_id") != run_id
        or str(fact_metadata.get("task_id") or "") != expected_task_id
        or str(fact_metadata.get("correlation_id") or "") != expected_correlation_id
    ):
        raise ValueError("run_ledger_projection_corrupt:partial_tail_fact_noncanonical")
    fact_occurred_at = fact.get("occurred_at")
    if type(fact_occurred_at) is not str or not fact_occurred_at:
        raise ValueError("run_ledger_projection_corrupt:partial_tail_fact_noncanonical")
    receipt = FactEventAppendedV1(
        event_id=fact_event_id,
        workspace=str(workspace),
        stream=_EXECUTION_CONTROL_PLANE_STREAM,
        storage_path="runtime/events/execution.control_plane.jsonl",
        appended_at=fact_occurred_at,
        appended_seq=fact_seq,
    )
    try:
        projection_row, projection_bytes = ledger._build_canonical_projection_row(
            canonical_fact_event,
            recorded_at=receipt.appended_at,
        )
    except ValueError as exc:
        raise ValueError("run_ledger_projection_corrupt:partial_tail_fact_noncanonical") from exc
    return _RunLedgerFactAuthority(
        canonical_event=canonical_fact_event,
        projection_row=projection_row,
        projection_bytes=projection_bytes,
        receipt=receipt,
    )


def _next_run_ledger_fact_page_offset(
    *,
    current_offset: int,
    raw_next_offset: Any,
    visited_offsets: set[int],
) -> int | None:
    if type(raw_next_offset) is not int or raw_next_offset < 0:
        raise ValueError("run_ledger_projection_corrupt:partial_tail_fact_page_offset_noncanonical")
    if raw_next_offset == 0:
        return None
    if raw_next_offset == current_offset:
        raise ValueError("run_ledger_projection_corrupt:partial_tail_fact_page_offset_stalled")
    if raw_next_offset in visited_offsets:
        raise ValueError("run_ledger_projection_corrupt:partial_tail_fact_page_offset_cycle")
    if raw_next_offset < current_offset:
        raise ValueError("run_ledger_projection_corrupt:partial_tail_fact_page_offset_regressed")
    return raw_next_offset


def _load_run_ledger_fact_authorities(
    *,
    workspace: Path,
    run_id: str,
    ledger: RunLedger,
) -> tuple[_RunLedgerFactAuthority, ...]:
    """Page all same-run Facts once, proving bounded identity and authority order."""

    authorities: list[_RunLedgerFactAuthority] = []
    seen_fact_event_ids: set[str] = set()
    seen_fact_seqs: set[int] = set()
    visited_offsets: set[int] = set()
    previous_fact_seq = 0
    record_count = 0
    page_count = 0
    offset = 0
    while True:
        if page_count >= _RUN_LEDGER_FACT_PROOF_MAX_PAGES:
            raise ValueError("run_ledger_projection_corrupt:partial_tail_fact_page_limit_exceeded")
        visited_offsets.add(offset)
        page_count += 1
        page = query_fact_events(
            QueryFactEventsV1(
                workspace=str(workspace),
                stream=_EXECUTION_CONTROL_PLANE_STREAM,
                limit=_RUN_LEDGER_FACT_PROOF_PAGE_SIZE,
                offset=offset,
                run_id=run_id,
                strict_integrity=True,
            )
        )
        page_events = page.events
        record_count += len(page_events)
        if record_count > _RUN_LEDGER_FACT_PROOF_MAX_RECORDS:
            raise ValueError("run_ledger_projection_corrupt:partial_tail_fact_record_limit_exceeded")
        for fact in page_events:
            fact_event_id = fact.get("event_id")
            if type(fact_event_id) is not str or not fact_event_id:
                raise ValueError("run_ledger_projection_corrupt:partial_tail_fact_event_id_noncanonical")
            if fact_event_id in seen_fact_event_ids:
                raise ValueError("run_ledger_projection_corrupt:partial_tail_fact_event_id_duplicate")
            fact_seq = fact.get("seq")
            if type(fact_seq) is not int or fact_seq < 1:
                raise ValueError("run_ledger_projection_corrupt:partial_tail_fact_seq_noncanonical")
            if fact_seq in seen_fact_seqs:
                raise ValueError("run_ledger_projection_corrupt:partial_tail_fact_seq_duplicate")
            if previous_fact_seq and fact_seq <= previous_fact_seq:
                raise ValueError("run_ledger_projection_corrupt:partial_tail_fact_seq_out_of_order")
            seen_fact_event_ids.add(fact_event_id)
            seen_fact_seqs.add(fact_seq)
            previous_fact_seq = fact_seq
            authority = _build_run_ledger_fact_authority(
                fact=fact,
                fact_event_id=fact_event_id,
                fact_seq=fact_seq,
                workspace=workspace,
                run_id=run_id,
                ledger=ledger,
            )
            if authority is not None:
                authorities.append(authority)
        next_offset = _next_run_ledger_fact_page_offset(
            current_offset=offset,
            raw_next_offset=page.next_offset,
            visited_offsets=visited_offsets,
        )
        if next_offset is None:
            return tuple(authorities)
        offset = next_offset


def _reconcile_run_ledger_partial_tail_authority(
    *,
    authorities: tuple[_RunLedgerFactAuthority, ...],
    canonical_event: dict[str, Any],
    projected_rows: tuple[dict[str, Any], ...],
    partial_tail: bytes,
) -> tuple[str, FactEventAppendedV1] | None:
    """Require the projection to be the exact authority prefix plus one partial row."""

    if not authorities:
        if projected_rows:
            raise ValueError("run_ledger_projection_corrupt:partial_tail_projection_fact_mismatch")
        return None
    authority_append_ids: set[str] = set()
    for authority in authorities:
        append_id = str(authority.canonical_event.get("append_id") or "")
        if append_id in authority_append_ids:
            raise ValueError("run_ledger_projection_corrupt:partial_tail_fact_ambiguous")
        authority_append_ids.add(append_id)
    if len(projected_rows) != len(authorities) - 1:
        raise ValueError("run_ledger_projection_corrupt:partial_tail_unprojected_fact_ambiguity")
    for authority, projected_row in zip(authorities[:-1], projected_rows, strict=True):
        if authority.projection_row != projected_row:
            raise ValueError("run_ledger_projection_corrupt:partial_tail_projection_fact_order_mismatch")
    authority = authorities[-1]
    if authority.canonical_event != canonical_event:
        raise ValueError("run_ledger_projection_corrupt:partial_tail_current_fact_mismatch")
    projection_bytes = authority.projection_bytes
    if len(partial_tail) >= len(projection_bytes) or not projection_bytes.startswith(partial_tail):
        raise ValueError("run_ledger_projection_corrupt:partial_tail_mismatch")
    return authority.receipt.appended_at, authority.receipt


def _count_value(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _jetstream_publish_enabled() -> bool:
    raw = str(os.environ.get("KERNELONE_JETSTREAM_PUBLISH") or "").strip().lower()
    return bool(raw) and raw not in _JETSTREAM_PUBLISH_FALSE_VALUES


def _safe_token(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value or "").strip())
    return cleaned.strip("-") or "unknown"


def _unique_run_ids(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Return non-empty run identifiers once, preserving caller order."""

    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        run_id = str(value or "").strip()
        if run_id and run_id not in seen:
            unique.append(run_id)
            seen.add(run_id)
    return tuple(unique)


def _empty_projection(
    *,
    workspace: Path,
    status: str = "pending",
    include_migration_ledgers: bool = False,
    query_scope: dict[str, str] | None = None,
    consumed_run_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": "run_ledger_projection",
        "available": False,
        "ok": False,
        "status": status,
        "audit_path": str(workspace / "runtime" / "control_plane" / "ledger"),
        "migration_ledgers_included": bool(include_migration_ledgers),
        "query_scope": dict(query_scope or {}),
        "consumed_run_ids": list(_unique_run_ids(consumed_run_ids)),
        "total": 0,
        "projected": 0,
        "missing": 0,
        "failed": 0,
        "missing_required_modalities": [],
        "failed_required_modalities": [],
        "failed_control_plane_events": [],
        "failed_evidence_details": {
            "required_modalities": [],
            "control_plane_events": [],
            "failed_gate_count": 0,
        },
        "projects": [],
        "detail": "run ledger projection is not available yet",
        "evidence_policy": {
            "ok": False,
            "enabled_modalities": [],
            "required_modalities": [],
            "missing_required_modalities": [],
            "failed_required_modalities": [],
        },
        "evidence_modalities": {},
        "task_boundary": {"ok": True, "verdict_count": 0, "latest": {}, "failed": []},
        "tool_lifecycle": empty_tool_lifecycle_summary(),
    }


def _ledger_dirs(workspace: Path, *, include_migration_ledgers: bool = False) -> list[Path]:
    runtime_root = workspace / "runtime"
    dirs = [runtime_root / "control_plane" / "ledger"]
    if include_migration_ledgers:
        dirs.append(runtime_root / "factory" / "ledger")
    return dirs


def _ledger_paths(
    workspace: Path,
    *,
    run_id: str,
    max_runs: int,
    include_migration_ledgers: bool = False,
) -> list[Path]:
    paths: list[Path] = []
    if run_id:
        safe_run_id = _safe_token(run_id)
        for ledger_dir in _ledger_dirs(workspace, include_migration_ledgers=include_migration_ledgers):
            path = ledger_dir / f"{safe_run_id}.ndjson"
            if path.is_file():
                paths.append(path)
        return paths

    for ledger_dir in _ledger_dirs(workspace, include_migration_ledgers=include_migration_ledgers):
        if not ledger_dir.is_dir():
            continue
        paths.extend(path for path in ledger_dir.glob("*.ndjson") if path.is_file())
    unique_paths = sorted(set(paths), key=lambda item: (item.stat().st_mtime, str(item)), reverse=True)
    return unique_paths[:max_runs]


def _ledger_paths_for_run_ids(
    workspace: Path,
    *,
    run_ids: tuple[str, ...],
    include_migration_ledgers: bool = False,
) -> list[Path]:
    """Read compatibility paths only for the canonical scope's selected runs."""

    paths: list[Path] = []
    seen: set[Path] = set()
    for run_id in _unique_run_ids(run_ids):
        for path in _ledger_paths(
            workspace,
            run_id=run_id,
            max_runs=1,
            include_migration_ledgers=include_migration_ledgers,
        ):
            if path not in seen:
                paths.append(path)
                seen.add(path)
    return paths


def _read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle.read().splitlines():
            if not line.strip():
                continue
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                parsed.setdefault("_ledger_path", str(path))
                events.append(parsed)
    return events


def _event_run_id(event: dict[str, Any]) -> str:
    """Extract the authoritative run identifier carried by an event payload."""

    token = event.get("job_token")
    token_map = token if isinstance(token, dict) else {}
    return str(event.get("run_id") or token_map.get("run_id") or "").strip()


def _query_scope(query: ReadRunLedgerProjectionQueryV1) -> dict[str, str]:
    """Project the caller-selected boundary into auditable response metadata."""

    return {
        "run_id": query.run_id,
        "factory_run_id": query.factory_run_id,
        "project_id": query.project_id,
    }


def _has_factory_project_scope(query: ReadRunLedgerProjectionQueryV1) -> bool:
    return bool(query.factory_run_id or query.project_id)


def _task_runtime_fact_matches_scope(
    payload: dict[str, Any],
    *,
    factory_run_id: str,
    project_id: str,
) -> bool:
    """Match only explicit TaskRuntime factory/project facts, never inferred IDs."""

    if factory_run_id and str(payload.get("factory_run_id") or "").strip() != factory_run_id:
        return False
    return not project_id or str(payload.get("factory_bench_project_id") or "").strip() == project_id


def _structured_string_list(value: Any) -> list[str]:
    """Normalize a structured string sequence without parsing prose."""

    if not isinstance(value, (list, tuple, set)):
        return []
    return list(dict.fromkeys(token for item in value if (token := str(item or "").strip())))


def _task_runtime_execution_stream_head(workspace: Path) -> int | None:
    """Return the durable TaskRuntime head used to fence projection reuse."""

    try:
        projection = query_fact_stream_head(
            QueryFactStreamHeadV1(
                workspace=str(workspace),
                stream=TASK_RUNTIME_EXECUTION_STREAM_V1,
            )
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    head = getattr(projection, "current_seq", None)
    if isinstance(head, bool) or not isinstance(head, int) or head < 0:
        return None
    return head


def _cached_task_runtime_scope_facts(
    *,
    workspace: Path,
    stream_head: int | None,
    run_id: str,
    factory_run_id: str,
    project_id: str,
) -> list[dict[str, Any]] | None:
    if stream_head is None:
        return None
    key = (str(workspace), stream_head, run_id, factory_run_id, project_id)
    with _TASK_RUNTIME_SCOPE_FACT_CACHE_LOCK:
        rows = _TASK_RUNTIME_SCOPE_FACT_CACHE.get(key)
        if rows is None:
            return None
        _TASK_RUNTIME_SCOPE_FACT_CACHE.move_to_end(key)
        return [dict(row) for row in rows]


def _cache_task_runtime_scope_facts(
    *,
    workspace: Path,
    stream_head: int | None,
    run_id: str,
    factory_run_id: str,
    project_id: str,
    facts: list[dict[str, Any]],
) -> None:
    if stream_head is None:
        return
    workspace_token = str(workspace)
    key = (workspace_token, stream_head, run_id, factory_run_id, project_id)
    with _TASK_RUNTIME_SCOPE_FACT_CACHE_LOCK:
        stale_keys = [
            candidate
            for candidate in _TASK_RUNTIME_SCOPE_FACT_CACHE
            if candidate[0] == workspace_token and candidate[2:] == key[2:] and candidate != key
        ]
        for stale_key in stale_keys:
            _TASK_RUNTIME_SCOPE_FACT_CACHE.pop(stale_key, None)
        _TASK_RUNTIME_SCOPE_FACT_CACHE[key] = tuple(dict(row) for row in facts)
        _TASK_RUNTIME_SCOPE_FACT_CACHE.move_to_end(key)
        while len(_TASK_RUNTIME_SCOPE_FACT_CACHE) > _TASK_RUNTIME_SCOPE_FACT_CACHE_MAX:
            _TASK_RUNTIME_SCOPE_FACT_CACHE.popitem(last=False)


def _read_task_runtime_execution_facts(
    *,
    workspace: Path,
    run_id: str = "",
    factory_run_id: str = "",
    project_id: str = "",
) -> list[dict[str, Any]]:
    """Read exact TaskRuntime facts for one run or factory/project scope.

    TaskRuntime remains the execution-state authority. This adapter preserves
    source fact identity while exposing only structured payload fields to the
    Run Ledger projection.

    Complexity:
        O(n) time and memory over selected TaskRuntime facts.
    """

    selected_run_id = str(run_id or "").strip()
    selected_factory_run_id = str(factory_run_id or "").strip()
    selected_project_id = str(project_id or "").strip()
    factory_scope = bool(selected_factory_run_id or selected_project_id)
    stream_head = _task_runtime_execution_stream_head(workspace)
    cached = _cached_task_runtime_scope_facts(
        workspace=workspace,
        stream_head=stream_head,
        run_id=selected_run_id,
        factory_run_id=selected_factory_run_id,
        project_id=selected_project_id,
    )
    if cached is not None:
        return cached
    facts: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = query_fact_events(
            QueryFactEventsV1(
                workspace=str(workspace),
                stream=TASK_RUNTIME_EXECUTION_STREAM_V1,
                limit=1000,
                offset=offset,
                run_id=selected_run_id if selected_run_id and not factory_scope else None,
            )
        )
        for fact in page.events:
            payload_raw = fact.get("payload")
            payload = dict(payload_raw) if isinstance(payload_raw, dict) else {}
            if factory_scope and not _task_runtime_fact_matches_scope(
                payload,
                factory_run_id=selected_factory_run_id,
                project_id=selected_project_id,
            ):
                continue
            if selected_run_id and not factory_scope and _event_run_id(payload) != selected_run_id:
                continue
            payload["fact_event_id"] = str(fact.get("event_id") or "")
            payload["fact_event_seq"] = int(fact.get("seq") or 0)
            payload["fact_stream"] = TASK_RUNTIME_EXECUTION_STREAM_V1
            payload.setdefault("project_id", str(payload.get("factory_bench_project_id") or "").strip())
            facts.append(payload)
        if page.next_offset == 0:
            final_head = _task_runtime_execution_stream_head(workspace)
            if final_head == stream_head:
                _cache_task_runtime_scope_facts(
                    workspace=workspace,
                    stream_head=stream_head,
                    run_id=selected_run_id,
                    factory_run_id=selected_factory_run_id,
                    project_id=selected_project_id,
                    facts=facts,
                )
            return facts
        offset = page.next_offset


def _task_runtime_tool_lifecycle_requirement_event(
    fact: dict[str, Any],
) -> dict[str, Any] | None:
    """Project a Director materialization claim into a lifecycle requirement.

    Only canonical TaskRuntime claim facts can activate this requirement. A
    JobToken is capability evidence, not proof that Director execution began.
    """

    if str(fact.get("fact_stream") or "").strip() != TASK_RUNTIME_EXECUTION_STREAM_V1:
        return None
    if str(fact.get("event_type") or "").strip().lower() not in {"claimed", "claim_renewed"}:
        return None
    task_row_raw = fact.get("task_row_snapshot")
    task_row = dict(task_row_raw) if isinstance(task_row_raw, dict) else {}
    metadata_raw = task_row.get("metadata")
    metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
    runtime_execution_raw = metadata.get("runtime_execution")
    runtime_execution = dict(runtime_execution_raw) if isinstance(runtime_execution_raw, dict) else {}
    if str(runtime_execution.get("role_id") or "").strip().lower() != "director":
        return None
    task_contract_raw = metadata.get("task_contract")
    task_contract = dict(task_contract_raw) if isinstance(task_contract_raw, dict) else {}
    target_files = _structured_string_list(metadata.get("target_files"))
    if not target_files:
        target_files = _structured_string_list(task_contract.get("target_files"))
    if not target_files and metadata.get("materialization_required") is not True:
        return None
    task_id = str(
        metadata.get("external_task_id")
        or metadata.get("pm_task_id")
        or fact.get("task_id")
        or task_row.get("id")
        or ""
    ).strip()
    run_id = _event_run_id(fact)
    fact_event_id = str(fact.get("fact_event_id") or "").strip()
    fact_event_seq = int(fact.get("fact_event_seq") or 0)
    evidence_ref = ":".join(
        part
        for part in (
            TASK_RUNTIME_EXECUTION_STREAM_V1,
            fact_event_id,
            str(fact_event_seq) if fact_event_seq else "",
        )
        if part
    )
    requirement = ToolLifecycleRequirementV1(
        task_id=task_id,
        run_id=run_id,
        source=TASK_RUNTIME_EXECUTION_STREAM_V1,
        reason="director_materialization_claimed",
        evidence_refs=(evidence_ref,) if evidence_ref else (),
    )
    return build_tool_lifecycle_requirement_run_ledger_event(
        requirement,
        project_id=str(fact.get("project_id") or "").strip(),
    )


def _tool_lifecycle_requirement_events_from_task_runtime(
    facts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return canonical requirement events from TaskRuntime execution facts."""

    return [event for fact in facts if (event := _task_runtime_tool_lifecycle_requirement_event(fact)) is not None]


def _discover_factory_child_run_ids(
    *,
    workspace: Path,
    factory_run_id: str,
    project_id: str,
) -> tuple[str, ...]:
    """Resolve child runs from paginated canonical TaskRuntime execution facts.

    The fact-stream API has no factory/project predicate, so this makes one
    paginated scan of ``task_runtime.execution`` and performs exact matching
    on the durable payload fields. It intentionally returns no fallback IDs:
    a scope miss must not widen to unrelated workspace runs.
    """

    facts = _read_task_runtime_execution_facts(
        workspace=workspace,
        factory_run_id=factory_run_id,
        project_id=project_id,
    )
    return _unique_run_ids([_event_run_id(fact) for fact in facts])


def _read_execution_control_plane_facts(
    *,
    workspace: Path,
    run_id: str = "",
    run_ids: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Read paginated canonical control-plane facts for a selected run set.

    ``run_ids=None`` preserves the legacy single-run/all-workspace query
    semantics. An explicit empty tuple is a closed scope and returns no facts.
    """

    events: list[dict[str, Any]] = []
    selected_run_ids = _unique_run_ids(run_ids or ()) if run_ids is not None else (str(run_id or "").strip(),)
    if run_ids is not None and not selected_run_ids:
        return events
    for selected_run_id in selected_run_ids:
        offset = 0
        while True:
            page = query_fact_events(
                QueryFactEventsV1(
                    workspace=str(workspace),
                    stream=_EXECUTION_CONTROL_PLANE_STREAM,
                    limit=1000,
                    offset=offset,
                    run_id=selected_run_id or None,
                )
            )
            for fact in page.events:
                payload = fact.get("payload")
                fact_payload = payload if isinstance(payload, dict) else {}
                event = fact_payload.get("event")
                if not isinstance(event, dict):
                    continue
                projected = dict(event)
                recorded_at = str(projected.get("recorded_at") or fact.get("occurred_at") or "").strip()
                if recorded_at:
                    projected["recorded_at"] = recorded_at
                projected["fact_event_id"] = str(fact.get("event_id") or "")
                projected["fact_event_seq"] = int(fact.get("seq") or 0)
                projected["fact_stream"] = _EXECUTION_CONTROL_PLANE_STREAM
                events.append(projected)
            if page.next_offset == 0:
                break
            offset = page.next_offset
    return events


def _event_matches_barrier(event: dict[str, Any], *, append_id: str, event_hash: str) -> bool:
    if append_id and str(event.get("append_id") or "").strip() == append_id:
        return True
    if event_hash and str(event.get("content_id") or "").strip() == event_hash:
        return True
    return bool(event_hash and str(event.get("event_id") or "").strip() == event_hash)


def _event_project_id(event: dict[str, Any]) -> str:
    token = event.get("job_token")
    token_map = token if isinstance(token, dict) else {}
    return str(
        token_map.get("project_id")
        or event.get("project_id")
        or token_map.get("run_id")
        or event.get("run_id")
        or "workspace"
    ).strip()


def _group_events_for_query_scope(
    events: list[dict[str, Any]],
    query: ReadRunLedgerProjectionQueryV1,
) -> dict[str, list[dict[str, Any]]]:
    """Group events by the caller's authoritative projection boundary.

    A scoped query represents one run or one factory run tree. Event-level
    ``project_id`` values are provenance produced by different subsystems and
    may legitimately be a workspace, task id, or catalog project id; they must
    not split a single requested scope into independently judged projects.
    Unscoped workspace queries retain the legacy event grouping for discovery.

    Complexity:
        O(n) time and memory over ``events``.
    """

    if query.run_id or query.factory_run_id:
        event_project_ids = _unique_run_ids([_event_project_id(event) for event in events])
        scope_id = (
            query.project_id
            or (event_project_ids[0] if len(event_project_ids) == 1 else "")
            or query.factory_run_id
            or query.run_id
        )
        return {scope_id: list(events)}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(_event_project_id(event), []).append(event)
    return grouped


def _merge_evidence_policy(projects: list[dict[str, Any]]) -> dict[str, Any]:
    enabled: list[str] = []
    required: list[str] = []
    missing: list[str] = []
    failed: list[str] = []
    for project in projects:
        policy = project.get("evidence_policy")
        if not isinstance(policy, dict):
            continue
        for target, key in (
            (enabled, "enabled_modalities"),
            (required, "required_modalities"),
            (missing, "missing_required_modalities"),
            (failed, "failed_required_modalities"),
        ):
            raw_items = policy.get(key)
            if isinstance(raw_items, list):
                target.extend(str(item) for item in raw_items if str(item))
    enabled = list(dict.fromkeys(enabled))
    required = list(dict.fromkeys(required))
    missing = list(dict.fromkeys(missing))
    failed = list(dict.fromkeys(failed))
    return {
        "ok": not missing and not failed,
        "enabled_modalities": enabled,
        "required_modalities": required,
        "missing_required_modalities": missing,
        "failed_required_modalities": failed,
    }


def _merge_evidence_modalities(projects: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, dict[str, Any]] = {}
    for project in projects:
        modalities = project.get("evidence_modalities")
        if not isinstance(modalities, dict):
            continue
        for name, raw_summary in modalities.items():
            if not isinstance(raw_summary, dict):
                continue
            summary = merged.setdefault(
                str(name),
                {"total": 0, "present": 0, "ok": 0, "failed": 0, "latest_detail": ""},
            )
            for key in ("total", "present", "ok", "failed"):
                summary[key] = int(summary.get(key) or 0) + int(raw_summary.get(key) or 0)
            detail = str(raw_summary.get("latest_detail") or "").strip()
            if detail:
                summary["latest_detail"] = detail
    return dict(sorted(merged.items()))


def _merge_task_boundary(projects: list[dict[str, Any]]) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    latest_by_task: dict[str, dict[str, Any]] = {}
    failed: list[dict[str, Any]] = []
    verdict_count = 0
    historical_failed_count = 0
    suppressed_non_mutating_deferred_count = 0
    for project in projects:
        boundary = project.get("task_boundary")
        if not isinstance(boundary, dict):
            continue
        verdict_count += _count_value(boundary.get("verdict_count"))
        historical_failed_count += _count_value(boundary.get("historical_failed_count"))
        suppressed_non_mutating_deferred_count += _count_value(
            boundary.get("suppressed_non_mutating_deferred_count")
        )
        boundary_latest = boundary.get("latest")
        if isinstance(boundary_latest, dict) and boundary_latest:
            latest = dict(boundary_latest)
        boundary_latest_by_task = boundary.get("latest_by_task")
        if isinstance(boundary_latest_by_task, dict):
            for task_key, raw_verdict in boundary_latest_by_task.items():
                normalized_task_key = str(task_key or "").strip()
                if normalized_task_key and isinstance(raw_verdict, dict):
                    latest_by_task[normalized_task_key] = dict(raw_verdict)
        boundary_failed = boundary.get("failed")
        if isinstance(boundary_failed, list):
            failed.extend(dict(item) for item in boundary_failed if isinstance(item, dict))
    return {
        "ok": not failed,
        "verdict_count": verdict_count,
        "historical_failed_count": historical_failed_count,
        "suppressed_non_mutating_deferred_count": suppressed_non_mutating_deferred_count,
        "latest": latest,
        "latest_by_task": latest_by_task,
        "failed": failed,
    }


def _project_from_events(project_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    projection = build_run_ledger_projection(events)
    summary = summarize_run_ledger_projection(projection)
    capability = projection.get("capability")
    capability_map = capability if isinstance(capability, dict) else {}
    evidence_policy = projection.get("evidence_policy")
    evidence_policy_map = evidence_policy if isinstance(evidence_policy, dict) else {}
    evidence_modalities = projection.get("evidence_modalities")
    evidence_modalities_map = evidence_modalities if isinstance(evidence_modalities, dict) else {}
    missing_required = evidence_policy_map.get("missing_required_modalities")
    missing_required_list = [str(item) for item in missing_required] if isinstance(missing_required, list) else []
    failed_required = evidence_policy_map.get("failed_required_modalities")
    failed_required_list = [str(item) for item in failed_required] if isinstance(failed_required, list) else []
    failed_control_plane_events = summary.get("failed_control_plane_events")
    failed_control_plane_event_list = (
        [str(item) for item in failed_control_plane_events] if isinstance(failed_control_plane_events, list) else []
    )
    task_boundary = projection.get("task_boundary")
    task_boundary_map = task_boundary if isinstance(task_boundary, dict) else {}
    tool_lifecycle = projection.get("tool_lifecycle")
    tool_lifecycle_map = tool_lifecycle if isinstance(tool_lifecycle, dict) else {}
    return {
        "project_id": project_id,
        "ok": bool(summary.get("ok")),
        "integrity_ok": bool(projection.get("integrity_ok")),
        "outcome_ok": bool(projection.get("outcome_ok")),
        "gate_count": int(projection.get("gate_count") or 0),
        "failed_gate_count": int(summary.get("failed_gate_count") or 0),
        "latest_token_id": str(capability_map.get("latest_token_id") or ""),
        "detail": str(summary.get("detail") or ""),
        "missing": list(summary.get("missing") or []),
        "missing_required_modalities": missing_required_list,
        "failed_required_modalities": failed_required_list,
        "failed_control_plane_events": failed_control_plane_event_list,
        "failed_evidence_details": {
            "required_modalities": failed_required_list,
            "control_plane_events": failed_control_plane_event_list,
            "failed_gate_count": int(summary.get("failed_gate_count") or 0),
        },
        "evidence_policy": evidence_policy_map,
        "evidence_modalities": evidence_modalities_map,
        "task_boundary": task_boundary_map,
        "tool_lifecycle": tool_lifecycle_map,
    }


def read_run_ledger_projection(query: ReadRunLedgerProjectionQueryV1) -> RunLedgerProjectionResultV1:
    """Read and project platform run ledger events for one workspace.

    A factory/project scope first resolves TaskRuntime child runs, then joins
    an explicit parent run if supplied. Canonical control-plane facts remain
    authoritative; compatibility NDJSON is consulted only when that selected
    canonical fact set is empty. Scope resolution is O(T + C) fact reads for
    T TaskRuntime rows and C selected control-plane rows, plus O(R) run IDs.
    """

    workspace = Path(query.workspace).expanduser().resolve()
    query_scope = _query_scope(query)
    factory_project_scoped = _has_factory_project_scope(query)
    if factory_project_scoped:
        task_runtime_facts = _read_task_runtime_execution_facts(
            workspace=workspace,
            factory_run_id=query.factory_run_id,
            project_id=query.project_id,
        )
        child_run_ids = _unique_run_ids([_event_run_id(fact) for fact in task_runtime_facts])
        consumed_run_ids = _unique_run_ids((query.run_id, *child_run_ids))
        paths = _ledger_paths_for_run_ids(
            workspace,
            run_ids=consumed_run_ids,
            include_migration_ledgers=query.include_migration_ledgers,
        )
        events = _read_execution_control_plane_facts(
            workspace=workspace,
            run_ids=consumed_run_ids,
        )
    else:
        task_runtime_facts = _read_task_runtime_execution_facts(
            workspace=workspace,
            run_id=query.run_id,
        )
        consumed_run_ids = _unique_run_ids((query.run_id,))
        paths = _ledger_paths(
            workspace,
            run_id=query.run_id,
            max_runs=query.max_runs,
            include_migration_ledgers=query.include_migration_ledgers,
        )
        events = _read_execution_control_plane_facts(
            workspace=workspace,
            run_id=query.run_id,
        )
    lifecycle_requirement_events = _tool_lifecycle_requirement_events_from_task_runtime(task_runtime_facts)
    if not paths and not events and not lifecycle_requirement_events:
        return RunLedgerProjectionResultV1(
            projection=_empty_projection(
                workspace=workspace,
                include_migration_ledgers=query.include_migration_ledgers,
                query_scope=query_scope,
                consumed_run_ids=consumed_run_ids,
            )
        )

    if not events:
        for path in paths:
            events.extend(_read_events(path))
    events.extend(lifecycle_requirement_events)
    if not events:
        return RunLedgerProjectionResultV1(
            projection=_empty_projection(
                workspace=workspace,
                status="empty",
                include_migration_ledgers=query.include_migration_ledgers,
                query_scope=query_scope,
                consumed_run_ids=consumed_run_ids,
            )
        )

    if not consumed_run_ids and not factory_project_scoped:
        consumed_run_ids = _unique_run_ids([_event_run_id(event) for event in events])

    grouped_events = _group_events_for_query_scope(events, query)

    projects = [
        _project_from_events(project_id, project_events)
        for project_id, project_events in sorted(grouped_events.items())
    ]
    run_projection = build_run_ledger_projection(events)
    failed = sum(1 for project in projects if not bool(project.get("ok")))
    projected = len(projects)
    ok = projected > 0 and failed == 0
    task_boundary = _merge_task_boundary(projects)
    tool_lifecycle = merge_tool_lifecycle_summaries(projects)
    evidence_policy = _merge_evidence_policy(projects)
    missing_required = list(evidence_policy.get("missing_required_modalities") or [])
    failed_required = list(evidence_policy.get("failed_required_modalities") or [])
    failed_control_plane_events = list(
        dict.fromkeys(
            str(item)
            for project in projects
            for item in (project.get("failed_control_plane_events") or [])
            if str(item)
        )
    )
    return RunLedgerProjectionResultV1(
        projection={
            "schema_version": 1,
            "source": "run_ledger_projection",
            "available": True,
            "ok": ok,
            "status": "ready" if ok else "failed",
            "audit_path": str(workspace / "runtime" / "control_plane" / "ledger"),
            "migration_ledgers_included": query.include_migration_ledgers,
            "query_scope": query_scope,
            "consumed_run_ids": list(consumed_run_ids),
            "total": projected,
            "projected": projected,
            "missing": 0,
            "failed": failed,
            "missing_required_modalities": missing_required,
            "failed_required_modalities": failed_required,
            "failed_control_plane_events": failed_control_plane_events,
            "failed_evidence_details": {
                "required_modalities": failed_required,
                "control_plane_events": failed_control_plane_events,
                "failed_project_count": failed,
            },
            "projects": projects,
            "run_projection": run_projection,
            "detail": f"run ledger projection {projected} project(s), {failed} failed",
            "evidence_policy": evidence_policy,
            "evidence_modalities": _merge_evidence_modalities(projects),
            "task_boundary": task_boundary,
            "tool_lifecycle": tool_lifecycle,
        }
    )


def query_factory_settlement_barrier(
    workspace: str | Path,
    factory_run_id: str,
) -> FactorySettlementBarrierResultV1:
    """Return the current settlement barrier for one exact Factory run.

    The query reads only canonical ``task_runtime.execution`` and
    ``execution.control_plane`` FactStream projections.  It never consults
    migration ledgers, waits for future events, or writes derived state.

    Complexity:
        O(T + C) FactStream reads and O(T + C) projection work for ``T``
        TaskRuntime facts and ``C`` control-plane facts in the selected run.
    """

    query = FactorySettlementBarrierQueryV1(
        workspace=str(workspace or ""),
        factory_run_id=factory_run_id,
    )
    resolved_workspace = Path(query.workspace).expanduser().resolve()
    task_runtime_facts = _read_task_runtime_execution_facts(
        workspace=resolved_workspace,
        factory_run_id=query.factory_run_id,
    )
    consumed_run_ids = _unique_run_ids([_event_run_id(fact) for fact in task_runtime_facts])
    ledger_facts = _read_execution_control_plane_facts(
        workspace=resolved_workspace,
        run_ids=consumed_run_ids,
    )
    lifecycle_requirements = _tool_lifecycle_requirement_events_from_task_runtime(task_runtime_facts)
    run_projection = build_run_ledger_projection([*ledger_facts, *lifecycle_requirements])
    return project_factory_settlement_barrier(
        workspace=str(resolved_workspace),
        factory_run_id=query.factory_run_id,
        run_projection=run_projection,
        task_runtime_facts=task_runtime_facts,
        ledger_facts=ledger_facts,
        consumed_run_ids=consumed_run_ids,
    )


def read_run_ledger_projection_barrier(
    query: ReadRunLedgerProjectionBarrierQueryV1,
) -> RunLedgerProjectionBarrierResultV1:
    """Read a projection after the requested ledger barrier is visible.

    The barrier is a consistency guard for QA. It prevents a verdict from being
    based on a projection that has not yet consumed the Director effect or
    verifier event referenced by the current task.
    """

    workspace = Path(query.workspace).expanduser().resolve()
    append_id = str(query.min_append_id or "").strip()
    event_hash = str(query.min_event_hash or "").strip()
    deadline = time.monotonic() + (query.timeout_ms / 1000.0)
    events: list[dict[str, Any]] = []
    paths: list[Path] = []
    barrier_satisfied = not append_id and not event_hash
    while True:
        paths = _ledger_paths(
            workspace,
            run_id=query.run_id,
            max_runs=1,
            include_migration_ledgers=query.include_migration_ledgers,
        )
        events = _read_execution_control_plane_facts(
            workspace=workspace,
            run_id=query.run_id,
        )
        if not events:
            for path in paths:
                events.extend(_read_events(path))
        if not barrier_satisfied:
            barrier_satisfied = any(
                _event_matches_barrier(event, append_id=append_id, event_hash=event_hash) for event in events
            )
        if barrier_satisfied or query.timeout_ms <= 0 or time.monotonic() >= deadline:
            break
        time.sleep(0.05)

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(workspace),
            run_id=query.run_id,
            max_runs=1,
            include_migration_ledgers=query.include_migration_ledgers,
        )
    ).projection
    consumed_append_ids = [
        str(event.get("append_id") or "").strip() for event in events if str(event.get("append_id") or "").strip()
    ]
    consumed_event_hashes = [
        str(event.get("content_id") or event.get("event_id") or "").strip()
        for event in events
        if str(event.get("content_id") or event.get("event_id") or "").strip()
    ]
    return RunLedgerProjectionBarrierResultV1(
        projection=projection,
        barrier={
            "schema_version": "run_ledger.projection_barrier.v1",
            "workspace": str(workspace),
            "run_id": query.run_id,
            "barrier_satisfied": bool(barrier_satisfied),
            "min_append_id": append_id,
            "min_event_hash": event_hash,
            "consumed_until_append_id": consumed_append_ids[-1] if consumed_append_ids else "",
            "consumed_append_ids": consumed_append_ids,
            "consumed_event_hashes": consumed_event_hashes,
            "ledger_paths": [str(path) for path in paths],
            "event_count": len(events),
        },
    )


def _publish_run_ledger_projection_update(
    *,
    workspace: Path,
    run_id: str,
    event: dict[str, Any],
) -> bool:
    """Publish the latest control-plane projection after a durable ledger append."""

    if not _jetstream_publish_enabled():
        return False
    workspace_token = str(workspace or "").strip()
    if not workspace_token:
        return False
    try:
        roots = resolve_storage_roots(workspace_token)
        workspace_key = str(getattr(roots, "workspace_key", "") or "").strip()
        if not workspace_key:
            return False
        projection = read_run_ledger_projection(
            ReadRunLedgerProjectionQueryV1(
                workspace=workspace_token,
                run_id=str(run_id or "").strip(),
                max_runs=1,
            )
        ).projection
        now = datetime.now(timezone.utc)
        event_id = str(event.get("event_id") or event.get("append_id") or int(now.timestamp() * 1000)).strip()
        transport_event = _project_run_ledger_event_for_transport(event)
        envelope = {
            "schema_version": "runtime.v2",
            "event_id": f"control-plane-ledger-{event_id}",
            "workspace_key": workspace_key,
            "run_id": str(run_id or event.get("run_id") or ""),
            "channel": "status.control_plane",
            "kind": "control_plane_ledger_projection_update",
            "ts": str(event.get("timestamp") or event.get("ts") or now.isoformat()),
            "cursor": 0,
            "trace_id": str(event.get("trace_id") or ""),
            "payload": {
                "projection": projection,
                "ledger_event": transport_event,
                "run_id": str(run_id or event.get("run_id") or ""),
            },
            "meta": {"source": "control_plane.run_ledger"},
        }
        return get_log_jetstream_publisher().publish(
            subject=f"hp.runtime.{workspace_key}.status.control_plane",
            payload=envelope,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.debug("Run Ledger projection JetStream publish failed: %s", exc)
        return False


def _project_run_ledger_event_for_transport(event: dict[str, Any]) -> dict[str, Any]:
    """Build a bounded notification; durable Ledger remains full authority."""

    gate = event.get("gate")
    gate = gate if isinstance(gate, dict) else {}
    job_token = event.get("job_token")
    job_token = job_token if isinstance(job_token, dict) else {}
    physical = event.get("physical_evidence")
    physical = physical if isinstance(physical, dict) else {}
    modalities = physical.get("modalities")
    modalities = modalities if isinstance(modalities, dict) else {}
    return {
        "schema_version": event.get("schema_version"),
        "event_id": str(event.get("event_id") or ""),
        "append_id": str(event.get("append_id") or ""),
        "content_id": str(event.get("content_id") or ""),
        "event_type": str(event.get("event_type") or ""),
        "stage": str(event.get("stage") or ""),
        "recorded_at": str(event.get("recorded_at") or event.get("timestamp") or ""),
        "gate": {
            "name": str(gate.get("name") or ""),
            "ok": bool(gate.get("ok")),
            "summary": str(gate.get("summary") or "")[:1024],
            "failing_requirements": list(gate.get("failing_requirements") or [])[:32],
        },
        "job_token": {
            key: job_token.get(key)
            for key in ("token_id", "run_id", "project_id", "stage")
            if job_token.get(key) not in (None, "")
        },
        "physical_evidence_summary": {
            "command_count": int(physical.get("command_count") or 0),
            "sampled_command_count": int(physical.get("sampled_command_count") or 0),
            "commands_truncated": bool(physical.get("commands_truncated")),
            "modalities": modalities,
            "repair_evidence_ref": str(
                (physical.get("repair_result") or {}).get("full_evidence_ref")
                if isinstance(physical.get("repair_result"), dict)
                else ""
            ),
            "repair_evidence_sha256": str(
                (physical.get("repair_result") or {}).get("full_evidence_sha256")
                if isinstance(physical.get("repair_result"), dict)
                else ""
            ),
        },
        "durable_event_available": True,
    }


def append_run_ledger_event(command: AppendRunLedgerEventCommandV1) -> RunLedgerAppendResultV1:
    """Commit one Fact-backed ledger row through the projection-owned transaction.

    Managed-process lifecycle events may only enter via
    ``project_managed_process_lifecycle`` (GR3B-B3), which sets a private
    call-stack ContextVar.  There is **no** public authorize keyword; any
    direct append of ``managed_process_lifecycle`` fails closed.
    """

    from polaris.cells.control_plane.run_ledger.public.contracts import ControlPlaneRunLedgerV1Error
    from polaris.cells.control_plane.run_ledger.public.managed_process_lifecycle import (
        MANAGED_PROCESS_LIFECYCLE_EVENT_TYPE,
        managed_process_append_is_authorized,
    )

    workspace = Path(command.workspace).expanduser().resolve()
    ledger = RunLedger(workspace, run_id=command.run_id)
    prepared_event = ledger.prepare_idempotent_event(dict(command.event))
    event_type = str(prepared_event.get("event_type") or "control_plane_event").strip()
    if event_type == MANAGED_PROCESS_LIFECYCLE_EVENT_TYPE and not managed_process_append_is_authorized():
        raise ControlPlaneRunLedgerV1Error(
            "managed_process_lifecycle_requires_typed_projection:"
            "use project_managed_process_lifecycle; generic append is forbidden"
        )

    def prove_partial_tail_fact(
        canonical_event: dict[str, Any],
        projected_rows: tuple[dict[str, Any], ...],
        partial_tail: bytes,
    ) -> tuple[str, Any] | None:
        """Prove complete Fact ownership before repairing one partial tail."""
        authorities = _load_run_ledger_fact_authorities(
            workspace=workspace,
            run_id=command.run_id,
            ledger=ledger,
        )
        return _reconcile_run_ledger_partial_tail_authority(
            authorities=authorities,
            canonical_event=canonical_event,
            projected_rows=projected_rows,
            partial_tail=partial_tail,
        )

    def append_canonical_fact(canonical_event: dict[str, Any]) -> tuple[str, Any]:
        fact_receipt = append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(workspace),
                stream=_EXECUTION_CONTROL_PLANE_STREAM,
                event_type=event_type,
                payload={
                    "schema_version": "execution.control_plane.fact.v1",
                    "run_id": command.run_id,
                    "event": canonical_event,
                },
                source="control_plane.run_ledger",
                run_id=command.run_id,
                task_id=str(canonical_event.get("task_id") or "").strip() or None,
                correlation_id=str(canonical_event.get("turn_id") or canonical_event.get("event_id") or "").strip()
                or None,
                idempotency_key=ledger.fact_idempotency_key(canonical_event),
                durability="fsync",
                strict_integrity=True,
            )
        )
        return fact_receipt.appended_at, fact_receipt

    persisted, fact_receipt = ledger.append_event_with_fact_transaction(
        prepared_event,
        append_fact=append_canonical_fact,
        prove_partial_tail_fact=prove_partial_tail_fact,
    )
    persisted["fact_receipt"] = {
        "event_id": fact_receipt.event_id,
        "stream": fact_receipt.stream,
        "storage_path": fact_receipt.storage_path,
        "appended_at": fact_receipt.appended_at,
        "appended_seq": fact_receipt.appended_seq,
    }
    event = persisted.get("event")
    if isinstance(event, dict) and _jetstream_publish_enabled():
        published = _publish_run_ledger_projection_update(
            workspace=workspace,
            run_id=command.run_id,
            event=event,
        )
        if not published:
            raise RuntimeError("Run Ledger projection publish failed after durable append")
    return RunLedgerAppendResultV1(receipt=persisted)


def append_tool_call_lifecycle_event(command: AppendToolCallLifecycleEventCommandV1) -> RunLedgerAppendResultV1:
    """Append a canonical tool-call lifecycle event through Run Ledger public APIs.

    Boundary:
        This is the single public append path for tool lifecycle receipts.
        Callers may build different receipt payloads, but they do not own the
        Run Ledger event envelope, job-token projection, or append command
        construction.  Managed-process lifecycle facts must not be smuggled
        through this path (GR3B-B3).

    Complexity:
        O(e + d) through lifecycle normalization, matching
        ``build_tool_call_lifecycle_run_ledger_event`` where ``e`` is native
        envelope refs and ``d`` is dropped-call refs.
    """

    from polaris.cells.control_plane.run_ledger.public.contracts import ControlPlaneRunLedgerV1Error
    from polaris.cells.control_plane.run_ledger.public.managed_process_lifecycle import (
        looks_like_managed_process_tool_lifecycle_substitute,
    )

    if looks_like_managed_process_tool_lifecycle_substitute(command.lifecycle_receipt):
        raise ControlPlaneRunLedgerV1Error(
            "managed_process_lifecycle_forbids_tool_lifecycle_substitute"
        )
    if str(command.stage or "").strip().lower() in {
        "managed_process",
        "managed_process_lifecycle",
        "process_receipt",
    }:
        raise ControlPlaneRunLedgerV1Error(
            "managed_process_lifecycle_forbids_tool_lifecycle_substitute:stage"
        )

    event = build_tool_call_lifecycle_run_ledger_event(
        run_id=command.run_id,
        task_id=command.task_id,
        turn_id=command.turn_id,
        role=command.role,
        lifecycle_receipt=command.lifecycle_receipt,
        stage=command.stage,
        project_id=command.project_id,
        capability_audit=command.capability_audit,
        gate_policy=command.gate_policy,
        job_token=command.job_token,
        ok=command.ok,
    )
    return append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=command.workspace,
            run_id=command.run_id,
            event=event,
        )
    )


def project_managed_process_lifecycle(command: Any) -> RunLedgerAppendResultV1:
    """Public re-export of GR3B-B3 typed managed-process lifecycle projection."""

    from polaris.cells.control_plane.run_ledger.public.managed_process_lifecycle import (
        project_managed_process_lifecycle as _project,
    )

    return _project(command)


def read_run_provenance_bundle(query: ReadRunProvenanceBundleQueryV1) -> RunProvenanceBundleResultV1:
    """Read one run's provenance bundle through the public ledger boundary."""

    workspace = Path(query.workspace).expanduser().resolve()
    paths = _ledger_paths(
        workspace,
        run_id=query.run_id,
        max_runs=1,
        include_migration_ledgers=query.include_migration_ledgers,
    )
    events: list[dict[str, Any]] = []
    for path in paths:
        events.extend(_read_events(path))
    projection = (
        build_run_ledger_projection(events)
        if events
        else _empty_projection(
            workspace=workspace,
            status="pending",
            include_migration_ledgers=query.include_migration_ledgers,
        )
    )
    return RunProvenanceBundleResultV1(
        bundle=build_run_provenance_bundle(
            workspace=str(workspace),
            run_id=query.run_id,
            events=events,
            projection=projection,
        )
    )


__all__ = [
    "append_run_ledger_event",
    "read_run_ledger_projection",
    "read_run_ledger_projection_barrier",
    "read_run_provenance_bundle",
]
