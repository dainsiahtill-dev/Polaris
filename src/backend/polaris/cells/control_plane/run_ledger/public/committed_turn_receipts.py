"""Read immutable role-owner receipts without creating competing ledger facts.

This observation is operational closure evidence. It never supplies acceptance
modalities, commits effects, or changes any historical owner fact.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from polaris.cells.control_plane.run_ledger.public.ledger import stable_hash
from polaris.cells.events.fact_stream.public import (
    FactPayloadReferenceV1,
    QueryFactEventsV1,
    QueryPayloadFactEventsV1,
    QuerySegmentedFactLedgerHeadV1,
    query_fact_events,
    query_payload_fact_events,
    query_payload_fact_ledger_presence,
    query_segmented_fact_ledger_head,
)
from polaris.kernelone.storage import resolve_workspace_runtime_identity
from polaris.kernelone.tool_execution.captured_tool_semantics import (
    classify_captured_tool_semantics,
    restore_captured_tool_spec_snapshot,
)
from polaris.kernelone.tool_execution.contracts import tool_batch_inventory_error
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

_STREAM = "roles.kernel.turn_outcomes"
_PAYLOAD_STREAM = "roles.kernel.turn_outcomes.v3"
_SOURCE = "roles.kernel.transaction"
_EVENT_TYPE = "turn_outcome_committed"
_MAX_FACTS = 4096


@dataclass(frozen=True, slots=True)
class CommittedTurnReceiptObservationV1:
    """Detached, immutable proof of one exact readonly dispatch outcome."""

    workspace: str
    run_id: str
    task_id: str
    turn_id: str
    batch_id: str
    status: Literal["observed", "unknown"]
    reason: str
    batch_receipt_json: str = "{}"
    batch_receipt_hash: str = ""
    batch_receipt_refs: tuple[tuple[str, str], ...] = ()
    native_call_ids: tuple[str, ...] = ()
    effect_class: str = ""
    fact_event_id: str = ""
    fact_event_seq: int = 0
    outcome_hash: str = ""
    evidence_refs: tuple[str, ...] = ()
    provider_correlation_qualified: bool = False
    provider_response_hash: str = ""

    @property
    def batch_receipt(self) -> dict[str, Any]:
        """Return a fresh receipt mapping so callers cannot alter the proof."""
        return json.loads(self.batch_receipt_json)


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if type(value) is dict else {}


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def _result_identity(row: dict[str, Any]) -> dict[str, Any]:
    """Ignore only documented empty typed fields and transport duration."""
    result = dict(row)
    for key in (
        "error",
        "effect_receipt",
        "effect_receipt_commit",
        "directed_effect_mutation_status",
        "directed_effect_claim_status",
    ):
        if result.get(key) is None:
            result.pop(key, None)
    duration = result.pop("execution_time_ms", 0)
    _require(type(duration) is int and duration >= 0, "invalid_result_duration")
    return result


def _receipt_serializations(receipt: dict[str, Any]) -> tuple[list[dict[str, Any]], ...]:
    """Known complete serializer versions only; never search arbitrary null subsets.

    Legacy ToolBatchRuntime receipts omit top-level empty effect_receipts and
    the three empty DEO fields added when the role owner parses BatchReceipt.
    Both aggregate and per-call readonly receipts have canonical derivations.
    A ref must still match the entire original serialized receipt exactly.
    """
    versions: list[dict[str, Any]] = [receipt]
    legacy = json.loads(json.dumps(receipt, ensure_ascii=False))
    _require(legacy.get("effect_receipts") == [], "readonly_effect_receipts_present")
    legacy.pop("effect_receipts")
    for row in legacy["results"]:
        for key in ("effect_receipt_commit", "directed_effect_mutation_status", "directed_effect_claim_status"):
            _require(row.get(key) is None, "readonly_directed_effect_present")
            row.pop(key, None)
    versions.append(legacy)
    layouts: list[list[dict[str, Any]]] = []
    for version in versions:
        layouts.append([version])
        singles = []
        for row, raw in zip(version["results"], version["raw_results"], strict=True):
            single = dict(version)
            success = int(row["status"] == "success")
            single.update(results=[row], raw_results=[raw], success_count=success, failure_count=1 - success)
            singles.append(single)
        layouts.append(singles)
    return tuple(layouts)


def _matching_receipt_layouts(
    receipt: dict[str, Any], observed_refs: tuple[tuple[str, str], ...]
) -> list[list[dict[str, Any]]]:
    """Prove complete known serializers with actual witnessed component IDs."""
    layouts = _receipt_serializations(receipt)
    matches = [
        layout for layout in layouts if tuple((item["batch_id"], stable_hash(item)) for item in layout) == observed_refs
    ]
    count = len(receipt["results"])
    if len(observed_refs) == count:
        singletons: dict[tuple[int, str], dict[str, Any]] = {}
        for layout in layouts:
            if len(layout) == count and all(len(item["results"]) == 1 for item in layout):
                for index, item in enumerate(layout):
                    singletons[(index, stable_hash(item))] = item
        selected: list[dict[str, Any]] = []
        indices: list[int] = []
        for batch_id, digest in observed_refs:
            candidates: dict[tuple[int, str], dict[str, Any]] = {}
            for (index, _), item in singletons.items():
                candidate = {**item, "batch_id": batch_id}
                if stable_hash(candidate) == digest:
                    candidates[(index, stable_hash(candidate))] = candidate
            if len(candidates) != 1:
                break
            (index, _), candidate = next(iter(candidates.items()))
            indices.append(index)
            selected.append(candidate)
        # merge_batch_receipts retains original receipt order and first ID.
        # Declaration order may differ, but the receipt/ref inventory may not.
        if indices == list(range(count)) and selected[0]["batch_id"] == receipt["batch_id"]:
            matches.append(selected)
    return list({stable_hash(layout): layout for layout in matches}.values())


def _validate_captured_read_classification(
    invocation: dict[str, Any], classification: dict[str, Any], *, required: bool
) -> None:
    """Use the owner's frozen snapshot; never recapture historical ToolSpecs."""
    if "snapshot" not in classification:
        _require(not required, "owner_parallel_snapshot_missing")
        return
    snapshot = restore_captured_tool_spec_snapshot(classification["snapshot"])
    _require(snapshot is not None and snapshot.registered is True, "owner_captured_snapshot_invalid")
    assert snapshot is not None
    _require(
        snapshot.raw_tool_name == invocation["raw_tool_name"] == classification.get("raw_tool_name")
        and snapshot.canonical_tool_name == invocation["tool_name"] == classification.get("canonical_tool_name"),
        "owner_captured_snapshot_binding_mismatch",
    )
    semantics = classify_captured_tool_semantics(snapshot)
    _require(
        semantics.effect_type == classification.get("effect_type") == invocation.get("effect_type") == "read"
        and semantics.execution_mode
        == classification.get("execution_mode")
        == invocation.get("execution_mode")
        == "readonly_parallel"
        and semantics.normalization_required is classification.get("normalization_required") is False
        and semantics.error_code is classification.get("error_code") is None,
        "owner_captured_semantics_mismatch",
    )


def _observe_owner(
    workspace: str, lifecycle: dict[str, Any], fact: dict[str, Any]
) -> CommittedTurnReceiptObservationV1:
    run_id, task_id, turn_id = (lifecycle[key] for key in ("run_id", "task_id", "turn_id"))
    is_payload_fact = "storage_record" in fact
    record = _mapping(fact.get("storage_record")) if is_payload_fact else fact
    stream = record.get("logical_stream") if is_payload_fact else record.get("stream")
    payload = _mapping(fact.get("expanded_payload")) if is_payload_fact else _mapping(fact.get("payload"))
    metadata = _mapping(record.get("metadata"))
    outcome = _mapping(payload.get("outcome"))
    execution = _mapping(outcome.get("execution"))
    receipt = _mapping(execution.get("receipt"))
    batch_id = receipt.get("batch_id")
    identity = resolve_workspace_runtime_identity(workspace)
    if is_payload_fact:
        small = _mapping(record.get("payload"))
        _require(
            set(small) == {"schema_version", "payload_ref"}
            and small.get("schema_version") == "kernelone.payload_fact.v1",
            "owner_payload_reference_shape_mismatch",
        )
        ref = FactPayloadReferenceV1.from_record(small["payload_ref"])
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        _require(
            stable_hash(payload) == ref.sha256
            and len(encoded) == ref.byte_size
            and ref.payload_schema == "roles.kernel.turn_outcome_fact.v3"
            and ref.storage_identity_token == identity.token,
            "owner_expanded_payload_binding_mismatch",
        )
        _require(
            stable_hash({key: value for key, value in record.items() if key != "event_hash"})
            == record.get("event_hash"),
            "owner_small_record_hash_mismatch",
        )
    provenance = payload.get("provenance")
    expected_provenance = {
        "workspace": workspace,
        "run_id": run_id,
        "task_id": task_id,
        "turn_id": turn_id,
        "transition_id": payload.get("transition_id"),
    }
    _require(
        record.get("source") == _SOURCE
        and record.get("event_type") == _EVENT_TYPE
        and (
            (is_payload_fact and stream == _PAYLOAD_STREAM and record.get("schema") == "kernelone.segmented_event.v1")
            or (
                not is_payload_fact
                and stream == _STREAM
                and record.get("schema_version") == 1
                and record.get("event_version") == 1
            )
        ),
        "noncanonical_owner",
    )
    _require(
        payload.get("schema_version")
        == ("roles.kernel.turn_outcome_fact.v3" if is_payload_fact else "roles.kernel.turn_outcome_fact.v2")
        and outcome.get("schema_version") == "roles.kernel.turn_outcome.v1",
        "unsupported_owner_schema",
    )
    _require(
        all(type(value) is str and value.strip() == value and value for value in expected_provenance.values()),
        "owner_identity_missing",
    )
    _require(
        provenance == expected_provenance and (is_payload_fact or metadata.get("provenance") == expected_provenance),
        "owner_provenance_mismatch",
    )
    _require(
        payload.get("storage_identity") == identity.to_record()
        and (is_payload_fact or metadata.get("storage_identity") == identity.to_record()),
        "owner_storage_mismatch",
    )
    _require(
        all(payload.get(key) == expected_provenance[key] for key in ("run_id", "task_id", "turn_id"))
        and (
            is_payload_fact
            or (
                metadata.get("run_id") == run_id
                and metadata.get("task_id") == task_id
                and fact.get("aggregate_id") == task_id
                and fact.get("correlation_id") == turn_id
            )
        ),
        "owner_envelope_identity_mismatch",
    )
    expected_key = ":".join((str(stream), identity.token, run_id, task_id, turn_id, payload["transition_id"]))
    _require(
        (record.get("idempotency_key") if is_payload_fact else metadata.get("idempotency_key")) == expected_key,
        "owner_idempotency_mismatch",
    )
    _require(outcome.get("run_id") == run_id and outcome.get("turn_id") == turn_id, "owner_outcome_identity_mismatch")
    _require(stable_hash(outcome) == payload.get("outcome_hash"), "owner_outcome_hash_mismatch")
    _require(execution.get("side_effect_class") == "readonly", "owner_has_mutations")
    _require(
        type(batch_id) is str
        and bool(batch_id)
        and execution.get("batch_id") == batch_id
        and receipt.get("turn_id") == turn_id,
        "owner_batch_identity_mismatch",
    )
    batch_id = cast(str, batch_id)
    _require(
        set(receipt)
        == {
            "batch_id",
            "turn_id",
            "results",
            "raw_results",
            "effect_receipts",
            "success_count",
            "failure_count",
            "pending_async_count",
            "has_pending_async",
        },
        "owner_receipt_shape_mismatch",
    )
    results, raw_results = receipt["results"], receipt["raw_results"]
    dispatched = lifecycle.get("dispatched_tool_calls_count")
    _require(
        type(dispatched) is int
        and dispatched > 0
        and type(results) is list
        and type(raw_results) is list
        and len(results) == len(raw_results) == dispatched
        and type(lifecycle.get("tool_result_count")) is int
        and lifecycle.get("tool_result_count") == dispatched,
        "owner_result_count_mismatch",
    )
    _require(
        type(receipt["pending_async_count"]) is int
        and receipt["pending_async_count"] == 0
        and receipt["has_pending_async"] is False
        and receipt["effect_receipts"] == [],
        "owner_not_quiescent",
    )
    calls: list[str] = []
    for row, raw in zip(results, raw_results, strict=True):
        _require(type(row) is dict and type(raw) is dict, "owner_result_shape_mismatch")
        call = row.get("call_id")
        _require(type(call) is str and bool(call) and call not in calls, "owner_call_identity_collision")
        calls.append(call)
        _require(
            row.get("status") in {"success", "error", "failed", "cancelled", "timeout", "aborted"},
            "owner_result_nonterminal",
        )
        _require(
            all(
                row.get(key) is None
                for key in (
                    "effect_receipt",
                    "effect_receipt_commit",
                    "directed_effect_mutation_status",
                    "directed_effect_claim_status",
                )
            ),
            "owner_result_has_effect",
        )
        _require(
            stable_hash(_result_identity(row)) == stable_hash(_result_identity(raw)), "owner_result_mirror_mismatch"
        )
        _require(
            set(_result_identity(row)) <= {"call_id", "tool_name", "status", "result", "error"},
            "owner_result_unknown_field",
        )
    successes = sum(row["status"] == "success" for row in results)
    _require(
        type(receipt["success_count"]) is int
        and type(receipt["failure_count"]) is int
        and receipt["success_count"] == successes
        and receipt["failure_count"] == dispatched - successes,
        "owner_result_counters_mismatch",
    )
    decision = _mapping(outcome.get("decision"))
    decision_batch = _mapping(decision.get("tool_batch"))
    declared = decision_batch.get("invocations")
    logical_batch_id = decision_batch.get("batch_id")
    invocations = execution.get("invocations")
    _require(
        decision.get("turn_id") == turn_id
        and decision.get("kind") == "tool_batch"
        and type(logical_batch_id) is str
        and bool(logical_batch_id),
        "owner_decision_identity_mismatch",
    )
    inventory_error = tool_batch_inventory_error(decision.get("tool_batch"))
    _require(inventory_error is None, inventory_error or "owner_invocation_inventory_mismatch")
    _require(
        type(declared) is list
        and len(declared) == dispatched
        and all(type(item) is dict for item in declared)
        and type(invocations) is list
        and len(invocations) == dispatched,
        "owner_invocation_mismatch",
    )
    declared = cast(list[dict[str, Any]], declared)
    invocations = cast(list[dict[str, Any]], invocations)
    batches = execution.get("invocation_batches")
    _require(
        type(batches) is list
        and len(batches) == 1
        and type(batches[0]) is dict
        and batches[0].get("batch_id") == logical_batch_id
        and stable_hash(batches[0].get("invocations")) == stable_hash(declared),
        "owner_invocation_batch_mismatch",
    )
    declared_calls = [item.get("call_id") for item in declared]
    _require(
        all(type(call) is str and bool(call) for call in declared_calls) and len(set(declared_calls)) == dispatched,
        "owner_invocation_identity_collision",
    )
    declared_by_call = {item["call_id"]: item for item in declared}
    for invocation, call in zip(invocations, calls, strict=True):
        _require(
            type(invocation) is dict
            and invocation.get("call_id") == call
            and call in declared_by_call
            and stable_hash(invocation) == stable_hash(declared_by_call[call]),
            "owner_invocation_mismatch",
        )
    reordered = declared_calls != calls
    if reordered:
        _require(
            all(item.get("execution_mode") == "readonly_parallel" for item in declared)
            and not any(decision_batch.get(field) for field in ("readonly_serial", "serial_writes", "async_receipts")),
            "owner_invocation_order_not_parallel",
        )
    envelopes = _mapping(decision.get("metadata")).get("native_tool_call_envelopes")
    _require(
        type(envelopes) is list
        and len(envelopes) == dispatched
        and stable_hash(lifecycle.get("native_tool_call_envelope_refs")) == stable_hash(envelopes),
        "owner_native_envelope_mismatch",
    )
    envelopes = cast(list[dict[str, Any]], envelopes)
    owner_provider_hash = _mapping(decision.get("metadata")).get("provider_response_hash")
    lifecycle_provider_hash = lifecycle.get("provider_response_hash")
    provider_binding_missing = owner_provider_hash in (None, "") and lifecycle_provider_hash in (None, "")
    provider_binding_qualified = (
        type(owner_provider_hash) is str
        and owner_provider_hash == lifecycle_provider_hash
        and len(owner_provider_hash) == 64
        and all(character in "0123456789abcdef" for character in owner_provider_hash)
    )
    # A strict immutable readonly owner receipt proves resource quiescence even
    # when a historical producer omitted provider correlation. It supplies no
    # delivery acceptance. Present conflicting bindings remain unknown.
    _require(provider_binding_missing or provider_binding_qualified, "owner_provider_response_mismatch")
    results_by_call = {row["call_id"]: row for row in results}
    for index, (invocation, envelope) in enumerate(zip(declared, envelopes, strict=True)):
        result = results_by_call[invocation["call_id"]]
        _require(type(invocation) is dict and type(envelope) is dict, "owner_invocation_shape_mismatch")
        classification = _mapping(invocation.get("classification"))
        _require(
            invocation.get("call_id") == result["call_id"] == envelope.get("call_id")
            and invocation.get("tool_name") == result["tool_name"]
            and type(invocation.get("tool_name")) is str
            and bool(invocation["tool_name"])
            and type(invocation.get("raw_tool_name")) is str
            and bool(invocation["raw_tool_name"])
            and classification.get("registered") is True
            and classification.get("raw_tool_name") == invocation.get("raw_tool_name")
            and classification.get("effect_type") == invocation.get("effect_type") == "read"
            and classification.get("execution_mode") == invocation.get("execution_mode")
            and invocation.get("execution_mode") in {"readonly_parallel", "readonly_serial"}
            and classification.get("error_code") is None,
            "owner_invocation_classification_mismatch",
        )
        native_arguments = invocation.get("native_arguments")
        _require(
            type(native_arguments) is dict
            and stable_hash(native_arguments) == stable_hash(invocation.get("arguments"))
            and classification.get("normalization_required") is False
            and envelope.get("schema_version") == "native_tool_call_envelope.v1"
            and type(envelope.get("index")) is int
            and envelope.get("index") == index
            and envelope.get("tool_name") == invocation.get("raw_tool_name")
            and stable_hash(native_arguments) == envelope.get("arguments_hash"),
            "owner_native_arguments_mismatch",
        )
        _validate_captured_read_classification(invocation, classification, required=reordered)
        if "snapshot" not in classification:
            # Old owners lack frozen ToolSpec evidence. Retain their original
            # restrictive registry checks; never recapture complete snapshots.
            spec = ToolSpecRegistry.get(ToolSpecRegistry.get_canonical(invocation["tool_name"]))
            _require(
                spec is not None
                and spec.is_read_tool()
                and not spec.is_write_tool()
                and not spec.is_exec_tool()
                and classification.get("canonical_tool_name") == spec.canonical_name,
                "owner_result_not_readonly",
            )
    refs = lifecycle.get("batch_receipt_refs")
    _require(
        type(refs) is list
        and bool(refs)
        and all(
            type(ref) is dict
            and set(ref) == {"batch_id", "receipt_hash"}
            and type(ref["batch_id"]) is str
            and bool(ref["batch_id"])
            and type(ref["receipt_hash"]) is str
            and len(ref["receipt_hash"]) == 64
            and all(char in "0123456789abcdef" for char in ref["receipt_hash"])
            for ref in refs
        ),
        "owner_receipt_ref_shape_mismatch",
    )
    refs = cast(list[dict[str, Any]], refs)
    observed_refs = tuple((ref["batch_id"], ref["receipt_hash"]) for ref in refs)
    _require(len(set(observed_refs)) == len(observed_refs), "owner_receipt_ref_collision")
    matches = _matching_receipt_layouts(receipt, observed_refs)
    _require(bool(matches), "owner_receipt_ref_hash_mismatch")
    list_hash = lifecycle.get("batch_receipt_hash")
    if list_hash is not None and not (type(list_hash) is str and list_hash == ""):
        _require(
            type(list_hash) is str and len(list_hash) == 64 and all(char in "0123456789abcdef" for char in list_hash),
            "owner_batch_receipt_hash_malformed",
        )
        _require(
            any(stable_hash(layout) == list_hash for layout in matches),
            "owner_batch_receipt_hash_mismatch",
        )
    event_id = record.get("event_id")
    seq = record.get("global_seq") if is_payload_fact else record.get("seq")
    _require(type(event_id) is str and bool(event_id) and type(seq) is int and seq > 0, "owner_fact_identity_missing")
    event_id, seq = cast(str, event_id), cast(int, seq)
    return CommittedTurnReceiptObservationV1(
        workspace=workspace,
        run_id=run_id,
        task_id=task_id,
        turn_id=turn_id,
        batch_id=batch_id,
        status="observed",
        reason=(
            "immutable_readonly_owner_receipt_observed"
            if provider_binding_qualified
            else "immutable_readonly_owner_receipt_observed_provider_binding_unavailable"
        ),
        batch_receipt_json=json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        batch_receipt_hash=stable_hash(receipt),
        batch_receipt_refs=observed_refs,
        native_call_ids=tuple(calls),
        effect_class="readonly",
        fact_event_id=event_id,
        fact_event_seq=seq,
        outcome_hash=payload["outcome_hash"],
        evidence_refs=(
            str(stream) + ":" + event_id + ":" + str(seq),
            payload["outcome_hash"],
            stable_hash(receipt),
            *(ref[1] for ref in observed_refs),
        ),
        provider_correlation_qualified=provider_binding_qualified,
        provider_response_hash=cast(str, owner_provider_hash) if provider_binding_qualified else "",
    )


def _read_legacy_owner_scope(workspace: str, run_id: str, task_id: str) -> list[dict[str, Any]]:
    """One complete strict read, retaining the existing per-scope record budget."""
    facts: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = query_fact_events(
            QueryFactEventsV1(
                workspace=workspace,
                stream=_STREAM,
                run_id=run_id,
                task_id=task_id,
                event_type=_EVENT_TYPE,
                strict_integrity=True,
                limit=1000,
                offset=offset,
            )
        )
        facts.extend(page.events)
        _require(len(facts) <= _MAX_FACTS, "owner_history_limit_exceeded")
        if page.next_offset == 0:
            return facts
        _require(page.next_offset > offset, "owner_history_cursor_invalid")
        offset = page.next_offset


def _read_requested_owner_facts(
    workspace: str,
    keys: set[tuple[str, str, str]],
) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    """Share only this request's strict snapshot, then verify its currentness.

    V2 scopes retain exact bytes/digests. V3 pages bind one captured head;
    selected pages are re-read so mutable or missing blobs cannot borrow an
    earlier observation. No proof is accepted from a caller or another query.
    """
    candidates: dict[tuple[str, str, str], list[dict[str, Any]]] = {key: [] for key in keys}
    legacy: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for run_id, task_id in sorted({key[:2] for key in keys}):
        facts = _read_legacy_owner_scope(workspace, run_id, task_id)
        legacy[(run_id, task_id)] = facts
        for fact in facts:
            payload_turn = _mapping(fact.get("payload")).get("turn_id")
            correlation = fact.get("correlation_id")
            for turn_id in {payload_turn, correlation}:
                key = (run_id, task_id, turn_id)
                if key in candidates:
                    candidates[cast(tuple[str, str, str], key)].append(fact)
    head_query = QuerySegmentedFactLedgerHeadV1(workspace=workspace, logical_stream=_PAYLOAD_STREAM)
    presence = query_payload_fact_ledger_presence(head_query)
    captured_head = None
    selected_pages: list[tuple[str | None, str]] = []
    if presence:
        continuation = None
        seen_cursors: set[str] = set()
        while True:
            page = query_payload_fact_events(
                QueryPayloadFactEventsV1(
                    workspace=workspace,
                    logical_stream=_PAYLOAD_STREAM,
                    limit=8,
                    continuation=continuation,
                )
            )
            if captured_head is None:
                captured_head = page.captured_head
            _require(page.captured_head == captured_head, "owner_payload_head_changed")
            selected = False
            for fact in page.events:
                if fact.get("event_type") != _EVENT_TYPE:
                    continue
                body = _mapping(fact.get("expanded_payload"))
                payload_key = tuple(body.get(name) for name in ("run_id", "task_id", "turn_id"))
                if payload_key in candidates:
                    candidates[cast(tuple[str, str, str], payload_key)].append(fact)
                    selected = True
            if selected:
                selected_pages.append((continuation, stable_hash(page.events)))
            if page.continuation is None:
                break
            _require(page.continuation not in seen_cursors, "owner_history_cursor_invalid")
            seen_cursors.add(page.continuation)
            continuation = page.continuation
        for continuation, page_hash in selected_pages:
            page = query_payload_fact_events(
                QueryPayloadFactEventsV1(
                    workspace=workspace,
                    logical_stream=_PAYLOAD_STREAM,
                    limit=8,
                    continuation=continuation,
                )
            )
            _require(page.captured_head == captured_head, "owner_payload_head_changed")
            _require(stable_hash(page.events) == page_hash, "owner_payload_snapshot_changed")
        _require(query_segmented_fact_ledger_head(head_query) == captured_head, "owner_payload_head_changed")
    _require(query_payload_fact_ledger_presence(head_query) == presence, "owner_payload_namespace_changed")
    for (run_id, task_id), facts in legacy.items():
        _require(_read_legacy_owner_scope(workspace, run_id, task_id) == facts, "owner_legacy_snapshot_changed")
    return candidates


def query_committed_turn_receipts(
    workspace: str | Path, *, lifecycle_receipts: Sequence[Mapping[str, Any]]
) -> tuple[CommittedTurnReceiptObservationV1, ...]:
    """Read exact readonly owner facts; missing/ambiguous evidence stays unknown.

    One request reads each V2 scope and the V3 ledger once, with a closing
    currentness check. Every receipt still undergoes independent owner binding.
    Scope is supplied only by lifecycle identity, never by success/tool flags.
    """
    normalized_workspace = str(Path(workspace).expanduser().resolve())
    lifecycles = [dict(raw) for raw in lifecycle_receipts]
    keys = {
        cast(tuple[str, str, str], tuple(lifecycle.get(key) for key in ("run_id", "task_id", "turn_id")))
        for lifecycle in lifecycles
        if all(
            type(lifecycle.get(key)) is str and lifecycle[key] and lifecycle[key].strip() == lifecycle[key]
            for key in ("run_id", "task_id", "turn_id")
        )
    }
    candidates: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    snapshot_error = ""
    try:
        if keys:
            candidates = _read_requested_owner_facts(normalized_workspace, keys)
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
        snapshot_error = str(exc)
    observations = []
    for lifecycle in lifecycles:
        scope = tuple(lifecycle.get(key) for key in ("run_id", "task_id", "turn_id"))
        try:
            _require(
                all(type(value) is str and value and value.strip() == value for value in scope),
                "lifecycle_identity_missing",
            )
            _require(not snapshot_error, snapshot_error)
            matching = candidates[cast(tuple[str, str, str], scope)]
            _require(len(matching) == 1, "owner_turn_missing_or_ambiguous")
            observations.append(_observe_owner(normalized_workspace, lifecycle, matching[0]))
        except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
            observations.append(
                CommittedTurnReceiptObservationV1(
                    workspace=normalized_workspace,
                    run_id=str(scope[0] or ""),
                    task_id=str(scope[1] or ""),
                    turn_id=str(scope[2] or ""),
                    batch_id="",
                    status="unknown",
                    reason=str(exc),
                )
            )
    return tuple(observations)
