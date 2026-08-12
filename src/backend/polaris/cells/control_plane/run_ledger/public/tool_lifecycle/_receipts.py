"""Lifecycle receipts, native tool-call envelopes, and receipt builders."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from polaris.cells.control_plane.run_ledger.public.failure_evidence import (
    FailureClassV1,
    FailureEvidenceV1,
    normalize_failure_class,
)
from polaris.cells.control_plane.run_ledger.public.tool_lifecycle._helpers import (
    _NATIVE_TOOL_FACT_EVIDENCE_KEYS,
    _NATIVE_TOOL_NAME_EVIDENCE_KEYS,
    _append_result_effect_receipts,
    _append_top_level_effect_receipts,
    _batch_receipt_refs,
    _clean_string,
    _dropped_tool_call_count,
    _dropped_tool_call_refs,
    _dropped_tool_calls_from_native_envelopes,
    _effect_receipt_refs,
    _first_tool_result_failure_reason,
    _int_value,
    _mapping,
    _mapping_refs,
    _native_tool_call_arguments,
    _native_tool_call_envelope_refs,
    _native_tool_call_id,
    _normalize_dispatch_status,
    _observed_tool_call_name,
    _raw_native_tool_call_name,
    _result_items,
    _stable_hash,
    _stable_json,
    _successful_write_results_without_effect_receipts,
    _text_fallback_lifecycle_fields,
)
from polaris.kernelone.tools.tool_kinds import is_write_tool_name


def normalize_native_tool_call_envelope_refs(value: Any) -> tuple[dict[str, Any], ...]:
    """Return deduplicated native tool-call envelope refs.

    Native tool-call envelopes are lifecycle evidence owned by Run Ledger. Other
    cells should consume this helper instead of carrying local envelope filtering
    or de-duplication rules.
    """

    return tuple(_native_tool_call_envelope_refs(value))


@dataclass(frozen=True, slots=True)
class NativeToolCallEnvelopeV1:
    """Stable provider-neutral reference for one native tool call.

    Boundary:
        Native tool-call envelopes are observational lifecycle evidence. They
        bind raw provider calls to stable hashes and identifiers without
        copying full arguments into metadata. Provider adapters may supply raw
        calls, but Run Ledger owns this reference shape.
    """

    envelope_id: str
    provider: str
    index: int
    tool_name: str
    call_id: str
    raw_call_hash: str
    arguments_hash: str
    source: str = "provider_native_tool_call"
    schema_version: str = "native_tool_call_envelope.v1"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "envelope_id": self.envelope_id,
            "provider": self.provider,
            "index": self.index,
            "tool_name": self.tool_name,
            "call_id": self.call_id,
            "raw_call_hash": self.raw_call_hash,
            "arguments_hash": self.arguments_hash,
            "source": self.source,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ToolCallLifecycleReceiptV1:
    """Canonical lifecycle receipt for provider tool calls.

    The receipt records the transaction path from provider-native tool calls
    through decoding, dispatch, tool results, effect receipts, and ledger
    commit. It is evidence only; it does not authorize execution.
    """

    run_id: str
    task_id: str
    turn_id: str
    role: str
    provider_response_hash: str
    native_tool_calls_count: int
    decoded_tool_calls_count: int
    dispatched_tool_calls_count: int
    tool_result_count: int
    effect_receipt_count: int
    dispatch_status: str
    failure_class: str
    ok: bool
    batch_receipt_hash: str = ""
    native_tool_call_envelope_refs: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    batch_receipt_refs: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    effect_receipt_refs: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    dropped_tool_calls: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    reason: str = ""
    compatibility_mode: str = "native_tools"
    text_fallback_requested: bool = False
    parser_attempted: bool = False
    native_tool_surface_absent_because_text_fallback: bool = False
    schema_version: str = "tool_call_lifecycle_receipt.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "turn_id": self.turn_id,
            "role": self.role,
            "provider_response_hash": self.provider_response_hash,
            "native_tool_calls_count": int(self.native_tool_calls_count),
            "decoded_tool_calls_count": int(self.decoded_tool_calls_count),
            "dispatched_tool_calls_count": int(self.dispatched_tool_calls_count),
            "tool_result_count": int(self.tool_result_count),
            "effect_receipt_count": int(self.effect_receipt_count),
            "dispatch_status": self.dispatch_status,
            "failure_class": self.failure_class,
            "ok": bool(self.ok),
            "batch_receipt_hash": self.batch_receipt_hash,
            "native_tool_call_envelope_refs": list(self.native_tool_call_envelope_refs),
            "batch_receipt_refs": list(self.batch_receipt_refs),
            "effect_receipt_refs": list(self.effect_receipt_refs),
            "dropped_tool_calls": list(self.dropped_tool_calls),
            "reason": self.reason,
            "compatibility_mode": self.compatibility_mode,
            "text_fallback_requested": self.text_fallback_requested,
            "parser_attempted": self.parser_attempted,
            "native_tool_surface_absent_because_text_fallback": (self.native_tool_surface_absent_because_text_fallback),
        }


def build_tool_call_lifecycle_receipt(
    *,
    run_id: str,
    task_id: str,
    turn_id: str,
    role: str,
    provider_response_hash: str = "",
    native_tool_calls_count: int = 0,
    decoded_tool_calls_count: int = 0,
    dispatched_tool_calls_count: int = 0,
    receipts: list[dict[str, Any]] | None = None,
    dropped_tool_calls: list[Any] | tuple[Any, ...] | None = None,
    native_tool_call_envelopes: list[Any] | tuple[Any, ...] | None = None,
    dispatch_status: str = "",
    failure_class: str = "",
    reason: str = "",
    compatibility_mode: str = "native_tools",
    text_fallback_requested: bool = False,
    parser_attempted: bool = False,
    native_tool_surface_absent_because_text_fallback: bool = False,
) -> ToolCallLifecycleReceiptV1:
    receipt_rows = [dict(item) for item in receipts or [] if isinstance(item, dict)]
    native_envelope_refs = _native_tool_call_envelope_refs(native_tool_call_envelopes)
    batch_refs = _batch_receipt_refs(receipt_rows)
    effect_refs = _effect_receipt_refs(receipt_rows)
    missing_write_effects = _successful_write_results_without_effect_receipts(receipt_rows)
    failed_effect_refs = [
        ref
        for ref in effect_refs
        if _clean_string(ref.get("receipt_outcome")).lower() == "failed"
        or _clean_string(ref.get("task_runtime_state")) in {"RECOVERY_PENDING", "DEAD_LETTER"}
    ]
    result_count = len(_result_items(receipt_rows))
    dispatched_count = _int_value(dispatched_tool_calls_count)
    if dispatched_count <= 0 and result_count > 0:
        dispatched_count = result_count
    status = _normalize_dispatch_status(dispatch_status)
    failure = normalize_failure_class(failure_class)
    dropped: list[dict[str, Any]] = _dropped_tool_call_refs(dropped_tool_calls)
    native_count = len(native_envelope_refs) if native_envelope_refs else _int_value(native_tool_calls_count)
    if native_count <= 0 and dropped:
        native_count = _dropped_tool_call_count(dropped)
    decoded_count = _int_value(decoded_tool_calls_count)
    if decoded_count <= 0 and native_count > 0 and dispatched_count <= 0:
        decoded_count = native_count

    if native_count > 0 and dispatched_count <= 0:
        status = "dropped"
        failure = failure or FailureClassV1.TOOL_DISPATCH_DROPPED.value
        if not dropped:
            dropped.extend(_dropped_tool_calls_from_native_envelopes(native_envelope_refs))
        if not dropped:
            dropped.append({"count": native_count, "reason": "native_tool_calls_without_dispatch"})
    elif decoded_count > 0 and not receipt_rows:
        status = status or "blocked"
        failure = failure or FailureClassV1.MISSING_BATCH_RECEIPT.value
    elif missing_write_effects:
        status = status or "blocked"
        failure = failure or FailureClassV1.MISSING_EFFECT_RECEIPT.value
        dropped.extend(missing_write_effects)
    elif failed_effect_refs:
        status = status or "blocked"
        failure = failure or FailureClassV1.TOOL_RESULT_FAILED.value
    elif result_count > 0:
        status = status or "dispatched"
    else:
        status = status or "blocked"
        failure = failure or FailureClassV1.MISSING_TOOL_RESULT.value

    failure_count = sum(_int_value(receipt.get("failure_count")) for receipt in receipt_rows)
    if failure_count > 0:
        failure = failure or FailureClassV1.TOOL_RESULT_FAILED.value

    ok = status == "dispatched" and failure_count == 0 and not failure
    # R156: when failure_count>0 but reason is empty, failure_evidence collapsed
    # to reason="dispatched" (dispatch_status). Prefer the first concrete tool
    # error/abort reason from batch results; do not rewrite dropped-dispatch
    # reasons that intentionally leave reason empty.
    resolved_reason = _clean_string(reason)
    if failure_count > 0 and not resolved_reason:
        resolved_reason = _first_tool_result_failure_reason(receipt_rows) or failure
    return ToolCallLifecycleReceiptV1(
        run_id=_clean_string(run_id),
        task_id=_clean_string(task_id),
        turn_id=_clean_string(turn_id),
        role=_clean_string(role),
        provider_response_hash=_clean_string(provider_response_hash),
        native_tool_calls_count=native_count,
        decoded_tool_calls_count=decoded_count,
        dispatched_tool_calls_count=dispatched_count,
        tool_result_count=result_count,
        effect_receipt_count=len(effect_refs),
        dispatch_status=status,
        failure_class=failure,
        ok=ok,
        batch_receipt_hash=_stable_hash(receipt_rows) if receipt_rows else "",
        native_tool_call_envelope_refs=tuple(native_envelope_refs),
        batch_receipt_refs=tuple(batch_refs),
        effect_receipt_refs=tuple(effect_refs),
        dropped_tool_calls=tuple(dropped),
        reason=resolved_reason,
        compatibility_mode=_clean_string(compatibility_mode) or "native_tools",
        text_fallback_requested=bool(text_fallback_requested),
        parser_attempted=bool(parser_attempted),
        native_tool_surface_absent_because_text_fallback=bool(native_tool_surface_absent_because_text_fallback),
    )


def build_tool_batch_lifecycle_receipt(
    *,
    run_id: str,
    task_id: str,
    turn_id: str,
    role: str,
    provider_response_hash: str = "",
    native_tool_calls_count: int = 0,
    decoded_tool_calls_count: int = 0,
    receipts: list[dict[str, Any]] | None = None,
    dropped_tool_calls: list[Any] | tuple[Any, ...] | None = None,
    native_tool_call_envelopes: list[Any] | tuple[Any, ...] | None = None,
    missing_receipt_reason: str = "decoded_tool_batch_produced_no_authoritative_batch_receipt",
    compatibility_mode: str = "native_tools",
    text_fallback_requested: bool = False,
    parser_attempted: bool = False,
    native_tool_surface_absent_because_text_fallback: bool = False,
) -> ToolCallLifecycleReceiptV1:
    """Build the lifecycle receipt for a decoded transaction tool batch.

    Boundary:
        Transaction callers provide facts: decoded count, native envelopes, and
        batch receipts. Run Ledger owns the dispatch classification so callers
        do not hand-write dropped/blocked status or failure-class projection.

    Complexity:
        O(r + e + d) over batch receipt rows, native envelopes, and supplied
        dropped-call refs.
    """

    receipt_rows = [dict(item) for item in receipts or [] if isinstance(item, dict)]
    result_count = len(_result_items(receipt_rows))
    decoded_count = _int_value(decoded_tool_calls_count)
    dropped_refs = _dropped_tool_call_refs(dropped_tool_calls)
    native_envelope_refs = _native_tool_call_envelope_refs(native_tool_call_envelopes)
    native_count = len(native_envelope_refs) if native_envelope_refs else _int_value(native_tool_calls_count)
    if decoded_count > 0 and result_count <= 0 and not dropped_refs and native_count <= 0:
        dropped_refs.append(
            {
                "count": decoded_count,
                "reason": "decoded_tool_batch_without_authoritative_receipt",
            }
        )
    fallback_failure_class = (
        FailureClassV1.REQUIRED_TOOL_TEXT_FALLBACK_NOT_DISPATCHED.value
        if text_fallback_requested and result_count <= 0
        else ""
    )

    return build_tool_call_lifecycle_receipt(
        run_id=run_id,
        task_id=task_id,
        turn_id=turn_id,
        role=role,
        provider_response_hash=provider_response_hash,
        native_tool_calls_count=native_tool_calls_count,
        decoded_tool_calls_count=decoded_count,
        receipts=receipt_rows,
        dropped_tool_calls=dropped_refs,
        native_tool_call_envelopes=native_envelope_refs,
        failure_class=fallback_failure_class,
        reason=missing_receipt_reason if decoded_count > 0 and result_count <= 0 else "",
        compatibility_mode=compatibility_mode,
        text_fallback_requested=text_fallback_requested,
        parser_attempted=parser_attempted,
        native_tool_surface_absent_because_text_fallback=native_tool_surface_absent_because_text_fallback,
    )


def build_tool_batch_lifecycle_receipt_from_sources(
    *,
    run_id: str,
    task_id: str,
    turn_id: str,
    role: str,
    provider_response_hash: str = "",
    metadata: Mapping[str, Any] | None = None,
    native_tool_calls: Sequence[Any] = (),
    decoded_tool_calls_count: int = 0,
    receipts: list[dict[str, Any]] | None = None,
    dropped_tool_calls: list[Any] | tuple[Any, ...] | None = None,
    missing_receipt_reason: str = "decoded_tool_batch_produced_no_authoritative_batch_receipt",
) -> ToolCallLifecycleReceiptV1:
    """Build a tool-batch lifecycle receipt from canonical native sources.

    Boundary:
        Run Ledger owns native tool-call count and envelope precedence for tool
        batch receipts. Transaction callers supply source metadata/raw calls and
        do not interpret native lifecycle aliases locally.

    Complexity:
        O(r + e + n) over lifecycle receipts, native envelopes, and raw calls.
    """

    native_facts = native_tool_call_facts_from_sources(metadata, native_tool_calls)
    fallback_fields = _text_fallback_lifecycle_fields(metadata)
    return build_tool_batch_lifecycle_receipt(
        run_id=run_id,
        task_id=task_id,
        turn_id=turn_id,
        role=role,
        provider_response_hash=provider_response_hash,
        native_tool_calls_count=native_tool_call_count_from_facts(native_facts),
        decoded_tool_calls_count=decoded_tool_calls_count,
        receipts=receipts,
        dropped_tool_calls=dropped_tool_calls,
        native_tool_call_envelopes=native_tool_call_envelope_refs_from_metadata(metadata),
        missing_receipt_reason=missing_receipt_reason,
        **fallback_fields,
    )


def batch_receipt_has_dispatch_evidence(batch_receipt: Any) -> bool:
    """Return whether a batch receipt contains dispatch evidence.

    Boundary:
        Run Ledger owns the dispatch-evidence key set for batch receipts.
        Runtime cells should consume this helper instead of locally
        checking ``"results"``, ``"raw_results"``, or ``"effect_receipts"``
        keys.

    Complexity:
        O(k) where ``k`` is the number of evidence keys checked.
    """

    if not isinstance(batch_receipt, Mapping):
        return False
    for key in ("results", "raw_results", "effect_receipts"):
        value = batch_receipt.get(key)
        if isinstance(value, list) and value:
            return True
    return False


def effect_receipts_from_batch_receipts(receipts: Sequence[Any]) -> list[dict[str, Any]]:
    """Extract effect receipts from normalized or raw batch receipts.

    Boundary:
        Run Ledger owns the batch-receipt dispatch/effect evidence key set.
        Runtime cells should consume this helper instead of locally checking
        ``"effect_receipts"``, ``"results"``, or ``"raw_results"``.

    Complexity:
        O(r + n), where ``r`` is the number of batch receipts and ``n`` is the
        total number of result rows/effect rows inspected.
    """

    effect_receipts: list[dict[str, Any]] = []
    for receipt in receipts:
        if not isinstance(receipt, Mapping):
            continue
        _append_top_level_effect_receipts(effect_receipts, receipt.get("effect_receipts"))
        for key in ("results", "raw_results"):
            _append_result_effect_receipts(effect_receipts, receipt.get(key))
    return effect_receipts


def build_missing_dispatch_lifecycle_receipt(
    *,
    required_write_tools: Sequence[Any],
    metadata_candidates: Sequence[Mapping[str, Any]] = (),
    tool_results: Sequence[Mapping[str, Any]] = (),
    batch_receipt: Mapping[str, Any] | None = None,
    reason: str = "required_write_tool_without_dispatch_evidence",
) -> dict[str, Any] | None:
    """Return a dropped-dispatch lifecycle receipt for missing write dispatch.

    Boundary:
        Completion owners provide required write-tool names and already-known
        metadata candidates. Run Ledger owns the evidence/no-evidence decision,
        native envelope extraction, dropped-call shape, and lifecycle receipt
        projection so stream and non-stream completion paths cannot drift.

        R133: provider-native write tool envelopes without batch/effect evidence
        must seal a dropped lifecycle even when the final-request
        ``required_tools`` list is empty. Claimed materialization turns can
        emit write tools under optional tool_choice; silent absence of
        lifecycle evidence previously projected as TOOL_LIFECYCLE_MISSING.

    Complexity:
        O(t + m + e + r) over required tools, metadata candidates, native
        envelopes, and batch receipt rows.
    """

    if tool_results or batch_receipt_has_dispatch_evidence(batch_receipt):
        return None

    native_envelopes: list[dict[str, Any]] = []
    fallback_fields: dict[str, Any] = {
        "compatibility_mode": "native_tools",
        "text_fallback_requested": False,
        "parser_attempted": False,
        "native_tool_surface_absent_because_text_fallback": False,
    }
    for candidate in metadata_candidates:
        candidate_fallback_fields = _text_fallback_lifecycle_fields(candidate)
        if candidate_fallback_fields["text_fallback_requested"]:
            fallback_fields = candidate_fallback_fields
        envelopes = native_tool_call_envelope_refs_from_metadata(candidate)
        if envelopes:
            native_envelopes = [dict(item) for item in envelopes]
            break

    tools: list[str] = []
    seen_tools: set[str] = set()
    for tool_name in required_write_tools:
        normalized = _clean_string(tool_name)
        if not normalized or not is_write_tool_name(normalized) or normalized in seen_tools:
            continue
        seen_tools.add(normalized)
        tools.append(normalized)

    derived_from_native_write_envelopes = False
    if not tools:
        for envelope in native_envelopes:
            if not isinstance(envelope, Mapping):
                continue
            normalized = _clean_string(envelope.get("tool_name"))
            if not normalized or not is_write_tool_name(normalized) or normalized in seen_tools:
                continue
            seen_tools.add(normalized)
            tools.append(normalized)
            derived_from_native_write_envelopes = True
    if not tools:
        return None

    resolved_reason = reason
    if derived_from_native_write_envelopes and reason == "required_write_tool_without_dispatch_evidence":
        resolved_reason = "native_tool_calls_without_dispatch"

    dropped_tool_calls = (
        [] if native_envelopes else [{"tool_name": tool_name, "reason": "tool_dispatch_dropped"} for tool_name in tools]
    )
    text_fallback_not_dispatched = bool(fallback_fields["text_fallback_requested"])
    return build_tool_call_lifecycle_receipt(
        run_id="",
        task_id="",
        turn_id="",
        role="",
        dispatched_tool_calls_count=0,
        dropped_tool_calls=dropped_tool_calls,
        native_tool_call_envelopes=native_envelopes,
        dispatch_status="dropped",
        failure_class=(
            FailureClassV1.REQUIRED_TOOL_TEXT_FALLBACK_NOT_DISPATCHED.value
            if text_fallback_not_dispatched
            else FailureClassV1.TOOL_DISPATCH_DROPPED.value
        ),
        reason=("required_tool_text_fallback_not_dispatched" if text_fallback_not_dispatched else resolved_reason),
        **fallback_fields,
    ).to_dict()


def build_claimed_materialization_without_tool_lifecycle_receipt(
    *,
    run_id: str,
    task_id: str,
    turn_id: str = "",
    role: str = "director",
    reason: str = "claimed_materialization_without_tool_lifecycle",
    failure_class: str = "",
) -> dict[str, Any]:
    """Seal blocked lifecycle for a claimed materialization that never produced tools.

    Boundary:
        Claimed Director materialization registers a tool-lifecycle requirement.
        When the attempt ends with zero tool batches (``closed_without_tools``,
        ``director_no_materialized_changes``, mid-turn fail-closed before dispatch),
        Run Ledger must still receive one authoritative receipt. Silent absence
        projects as ``TOOL_LIFECYCLE_MISSING`` even though the claim/fail evidence
        exists — that misattributes incomplete materialization as a missing seal.

        R137: seal a blocked receipt with ``INCOMPLETE_MATERIALIZATION`` /
        ``NO_MATERIALIZED_EFFECT`` so ``missing_required_task_keys`` clears while
        unresolved failure remains attributable.

    Complexity:
        O(1) receipt construction.
    """

    resolved_reason = _clean_string(reason) or "claimed_materialization_without_tool_lifecycle"
    resolved_failure = normalize_failure_class(failure_class or FailureClassV1.INCOMPLETE_MATERIALIZATION.value)
    return build_tool_call_lifecycle_receipt(
        run_id=_clean_string(run_id),
        task_id=_clean_string(task_id),
        turn_id=_clean_string(turn_id),
        role=_clean_string(role) or "director",
        native_tool_calls_count=0,
        decoded_tool_calls_count=0,
        dispatched_tool_calls_count=0,
        receipts=[],
        dispatch_status="blocked",
        failure_class=resolved_failure,
        reason=resolved_reason,
    ).to_dict()


def build_verified_existing_artifact_lifecycle_receipt(
    *,
    run_id: str,
    task_id: str,
    artifact_receipt_refs: Sequence[Any],
    turn_id: str = "",
    role: str = "director",
) -> ToolCallLifecycleReceiptV1:
    """Seal a no-dispatch retry that reuses exact project artifact receipts.

    This is deliberately distinct from a successful tool dispatch: all tool
    counters remain zero.  It only proves that the current Director attempt did
    not require another provider/tool turn because byte-current,
    project-authoritative artifact receipts already close the task scope.
    Empty, duplicate, or non-string receipt references fail closed.
    """

    normalized_refs = [_clean_string(item) for item in artifact_receipt_refs]
    if not normalized_refs or any(not item for item in normalized_refs):
        raise ValueError("verified-existing lifecycle requires non-empty artifact receipt refs")
    if len(set(normalized_refs)) != len(normalized_refs):
        raise ValueError("verified-existing lifecycle artifact receipt refs must be unique")
    return ToolCallLifecycleReceiptV1(
        run_id=_clean_string(run_id),
        task_id=_clean_string(task_id),
        turn_id=_clean_string(turn_id),
        role=_clean_string(role) or "director",
        provider_response_hash="",
        native_tool_calls_count=0,
        decoded_tool_calls_count=0,
        dispatched_tool_calls_count=0,
        tool_result_count=0,
        effect_receipt_count=len(normalized_refs),
        dispatch_status="verified_existing_artifacts",
        failure_class="",
        ok=True,
        effect_receipt_refs=tuple(
            {
                "receipt_ref": receipt_ref,
                "receipt_outcome": "success",
                "source": "runtime.execution_broker.project_artifact_receipt.v1",
            }
            for receipt_ref in normalized_refs
        ),
        reason="verified_existing_project_artifact_receipts",
        compatibility_mode="receipt_reuse",
    )


def build_tool_call_lifecycle_run_ledger_event(
    *,
    run_id: str,
    task_id: str,
    turn_id: str,
    role: str,
    lifecycle_receipt: Mapping[str, Any],
    stage: str = "director_tool_dispatch",
    project_id: str = "",
    capability_audit: Mapping[str, Any] | None = None,
    gate_policy: Mapping[str, Any] | None = None,
    job_token: Mapping[str, Any] | None = None,
    ok: bool | None = None,
) -> dict[str, Any]:
    """Return the canonical Run Ledger event for a lifecycle receipt.

    Boundary:
        Callers own append timing and workspace selection. Run Ledger owns the
        event shape, normalized receipt identity fields, and minimal job-token
        projection so completion and dropped-dispatch paths cannot drift.

    Complexity:
        O(e + d) through lifecycle receipt normalization where ``e`` is native
        envelope refs and ``d`` is dropped-call refs.
    """

    receipt_seed = {
        **dict(lifecycle_receipt),
        "run_id": run_id,
        "task_id": task_id,
        "turn_id": turn_id,
        "role": role,
    }
    if ok is not None:
        receipt_seed["ok"] = ok
    receipt = normalize_tool_call_lifecycle_receipt(receipt_seed)
    normalized_run_id = _clean_string(run_id or receipt.get("run_id") or turn_id)
    normalized_task_id = _clean_string(task_id or receipt.get("task_id"))
    if isinstance(job_token, Mapping):
        token_payload = dict(job_token)
        token_payload["run_id"] = _clean_string(token_payload.get("run_id")) or normalized_run_id
        token_payload["task_id"] = _clean_string(token_payload.get("task_id")) or normalized_task_id
        token_payload["project_id"] = (
            _clean_string(token_payload.get("project_id"))
            or _clean_string(project_id)
            or normalized_task_id
            or "unknown"
        )
        token_payload["stage"] = (
            _clean_string(token_payload.get("stage")) or _clean_string(stage) or "director_tool_dispatch"
        )
        if not isinstance(token_payload.get("capability_audit"), Mapping):
            token_payload["capability_audit"] = (
                dict(capability_audit) if isinstance(capability_audit, Mapping) else {"ok": True, "issues": []}
            )
        if not isinstance(token_payload.get("gate_policy"), Mapping):
            token_payload["gate_policy"] = dict(gate_policy) if isinstance(gate_policy, Mapping) else {}
    else:
        token_payload = {
            "run_id": normalized_run_id,
            "task_id": normalized_task_id,
            "project_id": _clean_string(project_id) or normalized_task_id or "unknown",
            "capability_audit": dict(capability_audit)
            if isinstance(capability_audit, Mapping)
            else {"ok": True, "issues": []},
            "gate_policy": dict(gate_policy) if isinstance(gate_policy, Mapping) else {},
        }
    return {
        "event_type": "tool_call_lifecycle",
        "stage": _clean_string(stage) or "director_tool_dispatch",
        "task_id": normalized_task_id,
        "run_id": normalized_run_id,
        "tool_call_lifecycle_receipt": receipt,
        "job_token": token_payload,
    }


def normalize_tool_call_lifecycle_receipt(value: Any) -> dict[str, Any]:
    """Return a safe tool lifecycle receipt mapping."""

    if isinstance(value, ToolCallLifecycleReceiptV1):
        return value.to_dict()
    payload = _mapping(value)
    if payload:
        payload.setdefault("schema_version", "tool_call_lifecycle_receipt.v1")
        fallback_fields = _text_fallback_lifecycle_fields(payload)
        for key, field_value in fallback_fields.items():
            payload.setdefault(key, field_value)
        native_refs = _native_tool_call_envelope_refs(payload.get("native_tool_call_envelope_refs"))
        if not native_refs:
            native_refs = _native_tool_call_envelope_refs(payload.get("native_tool_call_envelopes"))
        batch_refs = _mapping_refs(payload.get("batch_receipt_refs"))
        effect_refs = _mapping_refs(payload.get("effect_receipt_refs"))
        native_count = len(native_refs) if native_refs else _int_value(payload.get("native_tool_calls_count"))
        result_count = _int_value(payload.get("tool_result_count"))
        dispatched_count = _int_value(payload.get("dispatched_tool_calls_count"))
        if dispatched_count <= 0 and result_count > 0:
            dispatched_count = result_count
        payload["native_tool_call_envelope_refs"] = native_refs
        payload["batch_receipt_refs"] = batch_refs
        payload["effect_receipt_refs"] = effect_refs
        payload["dispatched_tool_calls_count"] = dispatched_count
        payload["tool_result_count"] = result_count
        payload["effect_receipt_count"] = (
            len(effect_refs) if effect_refs else _int_value(payload.get("effect_receipt_count"))
        )
        dropped_tool_calls = _dropped_tool_call_refs(payload.get("dropped_tool_calls"))
        if native_count > 0 and dispatched_count <= 0 and not dropped_tool_calls:
            dropped_tool_calls = _dropped_tool_calls_from_native_envelopes(native_refs)
        if native_count > 0 and dispatched_count <= 0 and not dropped_tool_calls:
            dropped_tool_calls = [{"count": native_count, "reason": "native_tool_calls_without_dispatch"}]
        if native_count <= 0 and dropped_tool_calls:
            native_count = _dropped_tool_call_count(dropped_tool_calls)
        decoded_count = _int_value(payload.get("decoded_tool_calls_count"))
        if decoded_count <= 0 and native_count > 0 and dispatched_count <= 0:
            decoded_count = native_count
        payload["dropped_tool_calls"] = dropped_tool_calls
        payload["native_tool_calls_count"] = native_count
        payload["decoded_tool_calls_count"] = decoded_count
        status = _normalize_dispatch_status(payload.get("dispatch_status"))
        if status:
            payload["dispatch_status"] = status
        if native_count > 0 and dispatched_count <= 0:
            payload["dispatch_status"] = "dropped"
            if not normalize_failure_class(payload.get("failure_class")):
                payload["failure_class"] = FailureClassV1.TOOL_DISPATCH_DROPPED.value
        elif result_count > 0 and not _clean_string(payload.get("dispatch_status")):
            payload["dispatch_status"] = "dispatched"
        if not _clean_string(payload.get("dispatch_status")):
            payload["dispatch_status"] = "unknown"
        if "failure_class" not in payload or payload.get("failure_class") is None:
            payload["failure_class"] = (
                "" if payload["dispatch_status"] == "dispatched" else FailureClassV1.TOOL_LIFECYCLE_UNKNOWN.value
            )
        else:
            payload["failure_class"] = normalize_failure_class(payload.get("failure_class"))
        payload.setdefault(
            "ok",
            payload["dispatch_status"] == "dispatched" and not normalize_failure_class(payload.get("failure_class")),
        )
        return payload
    return {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "ok": False,
        "dispatch_status": "unknown",
        "failure_class": FailureClassV1.TOOL_LIFECYCLE_MISSING.value,
    }


def tool_call_lifecycle_receipts_from_metadata(metadata: Mapping[str, Any] | None) -> tuple[dict[str, Any], ...]:
    """Return deduplicated lifecycle receipts from legacy/canonical metadata keys.

    Boundary:
        The ``tool_call_lifecycle_receipt`` canonical key and older
        ``tool_call_lifecycle`` / ``tool_call_lifecycle_receipts`` aliases are
        all Run Ledger evidence projections. Callers should consume this helper
        instead of reimplementing alias precedence or receipt deduplication.

    Complexity:
        O(r * s) time where ``r`` is receipt count and ``s`` is receipt size for
        stable de-duplication; O(r) additional memory.
    """

    if not isinstance(metadata, Mapping):
        return ()
    receipts: list[dict[str, Any]] = []
    seen_receipts: set[str] = set()

    def append_receipt(value: Mapping[str, Any]) -> None:
        receipt = normalize_tool_call_lifecycle_receipt(value)
        receipt_key = _stable_json(receipt)
        if receipt_key in seen_receipts:
            return
        seen_receipts.add(receipt_key)
        receipts.append(receipt)

    for key in ("tool_call_lifecycle_receipt", "tool_call_lifecycle"):
        receipt = metadata.get(key)
        if isinstance(receipt, Mapping):
            append_receipt(receipt)
    receipt_rows = metadata.get("tool_call_lifecycle_receipts")
    if isinstance(receipt_rows, (list, tuple)):
        for item in receipt_rows:
            if isinstance(item, Mapping):
                append_receipt(item)
    return tuple(receipts)


def native_tool_call_facts_from_lifecycle_receipt(value: Any) -> dict[str, Any]:
    """Derive native tool-call count/name facts from lifecycle receipt evidence.

    Lifecycle receipts are the public Run Ledger evidence shape for provider
    tool-call transactions. Downstream cells should use this helper instead of
    re-parsing envelope refs or dropped-call refs locally.

    Complexity:
        O(e + d) time where ``e`` is envelope refs and ``d`` is dropped-call
        refs; O(e + d) memory for the returned name list.
    """

    receipt = normalize_tool_call_lifecycle_receipt(value)
    names: list[str] = []
    seen: set[str] = set()
    for envelope in _native_tool_call_envelope_refs(receipt.get("native_tool_call_envelope_refs")):
        tool_name = _clean_string(envelope.get("tool_name"))
        if tool_name and tool_name not in seen:
            seen.add(tool_name)
            names.append(tool_name)
    for dropped in _dropped_tool_call_refs(receipt.get("dropped_tool_calls")):
        tool_name = _clean_string(dropped.get("tool_name"))
        if tool_name and tool_name not in seen:
            seen.add(tool_name)
            names.append(tool_name)
    return {
        "native_tool_calls_count": _int_value(receipt.get("native_tool_calls_count")),
        "native_tool_call_names": names,
    }


def native_tool_call_envelope_refs_from_metadata(metadata: Mapping[str, Any] | None) -> tuple[dict[str, Any], ...]:
    """Return native tool-call envelope refs from lifecycle-aware metadata.

    Boundary:
        This helper owns the compatibility order for top-level native envelope
        metadata and lifecycle receipt aliases. Role kernels and projections
        should not reimplement this key order.

    Complexity:
        O(r + e) time and memory where ``r`` is lifecycle receipt count and
        ``e`` is native envelope ref count.
    """

    if not isinstance(metadata, Mapping):
        return ()
    for key in ("native_tool_call_envelopes", "native_tool_call_envelope_refs"):
        valid_envelopes = normalize_native_tool_call_envelope_refs(metadata.get(key))
        if valid_envelopes:
            return valid_envelopes
    for receipt in tool_call_lifecycle_receipts_from_metadata(metadata):
        for key in ("native_tool_call_envelope_refs", "native_tool_call_envelopes"):
            valid_envelopes = normalize_native_tool_call_envelope_refs(receipt.get(key))
            if valid_envelopes:
                return valid_envelopes
    return ()


def native_tool_call_facts_from_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Derive native tool-call facts from lifecycle-aware metadata.

    Empty mapping means no structured native-tool evidence was present. A
    non-empty mapping with count ``0`` means lifecycle evidence was present and
    authoritative, so callers should not fall back to raw provider call lists.

    Complexity:
        O(r + e + n) time and memory where ``r`` is receipt count, ``e`` is
        envelope refs, and ``n`` is native tool-name count.
    """

    if not isinstance(metadata, Mapping):
        return {}
    envelopes = native_tool_call_envelope_refs_from_metadata(metadata)
    if envelopes:
        names = [name for envelope in envelopes if (name := _clean_string(envelope.get("tool_name")))]
        return {
            "native_tool_calls_count": len(envelopes),
            "native_tool_call_names": names,
        }
    receipts = tool_call_lifecycle_receipts_from_metadata(metadata)
    if not receipts:
        return {}
    for receipt in receipts:
        facts = native_tool_call_facts_from_lifecycle_receipt(receipt)
        raw_names = facts.get("native_tool_call_names")
        names = [
            name
            for item in (raw_names if isinstance(raw_names, (list, tuple)) else ())
            if (name := _clean_string(item))
        ]
        count = _int_value(facts.get("native_tool_calls_count"))
        if count > 0 or names:
            return {
                "native_tool_calls_count": count,
                "native_tool_call_names": names,
            }
    return {
        "native_tool_calls_count": 0,
        "native_tool_call_names": [],
    }


def native_tool_call_facts_from_raw_calls(native_tool_calls: Sequence[Any]) -> dict[str, Any]:
    """Derive native tool-call facts from provider-native raw call payloads.

    Boundary:
        Run Ledger owns count/name alias handling for provider-native call
        facts. Role kernels may still extract response-shaped raw call rows,
        but should not maintain a second alias table for count/name facts.

    Complexity:
        O(n) time where ``n`` is raw call count; O(n) additional memory for the
        returned name list.
    """

    names: list[str] = []
    count = 0
    for item in native_tool_calls:
        if not isinstance(item, Mapping):
            continue
        count += 1
        name = _raw_native_tool_call_name(item)
        if name:
            names.append(name)
    return {
        "native_tool_calls_count": count,
        "native_tool_call_names": names,
    }


def build_native_tool_call_envelopes(
    native_tool_calls: Sequence[Any],
    *,
    provider: str,
) -> tuple[NativeToolCallEnvelopeV1, ...]:
    """Build stable envelope refs for provider-native tool calls.

    Boundary:
        Run Ledger owns the native tool-call envelope shape and hashing rules.
        Callers supply raw provider call mappings and a provider label; this
        helper does not authorize, dispatch, or infer tool calls from prose.

    Complexity:
        O(n * k) time over native calls and argument/hash serialization size;
        O(n) memory for the returned envelope refs.
    """

    provider_label = _clean_string(provider).lower() or "auto"
    envelopes: list[NativeToolCallEnvelopeV1] = []
    for index, item in enumerate(native_tool_calls):
        if not isinstance(item, Mapping):
            continue
        call = dict(item)
        raw_call_hash = _stable_hash(call)
        arguments_hash = _stable_hash(_native_tool_call_arguments(call))
        call_id = _native_tool_call_id(call, index=index, raw_call_hash=raw_call_hash)
        tool_name = _raw_native_tool_call_name(call)
        envelope_id = f"native_tool_call:{provider_label}:{index}:{call_id}:{raw_call_hash[:16]}"
        envelopes.append(
            NativeToolCallEnvelopeV1(
                envelope_id=envelope_id,
                provider=provider_label,
                index=index,
                tool_name=tool_name,
                call_id=call_id,
                raw_call_hash=raw_call_hash,
                arguments_hash=arguments_hash,
                metadata={"has_tool_name": bool(tool_name)},
            )
        )
    return tuple(envelopes)


def build_native_tool_call_envelope_payloads(
    native_tool_calls: Sequence[Any],
    *,
    provider: str,
) -> list[dict[str, Any]]:
    """Return JSON-ready native tool-call envelope refs."""

    return [envelope.to_dict() for envelope in build_native_tool_call_envelopes(native_tool_calls, provider=provider)]


def native_tool_call_facts_from_sources(
    metadata: Mapping[str, Any] | None,
    native_tool_calls: Sequence[Any],
) -> dict[str, Any]:
    """Derive canonical native tool-call facts from structured sources.

    Boundary:
        Run Ledger owns the precedence and projection shape for native
        tool-call count/name facts. Callers may provide lifecycle-aware
        metadata and provider-native raw calls, but should not assemble the
        ``native_tool_calls_count`` / ``native_tool_call_names`` mapping
        themselves.

    Complexity:
        O(r + e + n) where ``r`` is lifecycle receipt count, ``e`` is envelope
        refs, and ``n`` is raw provider call count.
    """

    if isinstance(metadata, Mapping):
        facts = native_tool_call_facts_from_metadata(metadata)
        if facts:
            raw_names = facts.get("native_tool_call_names")
            return {
                "native_tool_calls_count": _int_value(facts.get("native_tool_calls_count")),
                "native_tool_call_names": [
                    name
                    for item in (raw_names if isinstance(raw_names, (list, tuple)) else ())
                    if (name := _clean_string(item))
                ],
            }
    raw_facts = native_tool_call_facts_from_raw_calls(native_tool_calls)
    raw_names = raw_facts.get("native_tool_call_names")
    raw_count = _int_value(raw_facts.get("native_tool_calls_count"))
    if raw_count > 0 or raw_names:
        return {
            "native_tool_calls_count": raw_count,
            "native_tool_call_names": [
                name
                for item in (raw_names if isinstance(raw_names, (list, tuple)) else ())
                if (name := _clean_string(item))
            ],
        }
    if isinstance(metadata, Mapping):
        raw_legacy_names = metadata.get("native_tool_call_names")
        legacy_names = [
            name
            for item in (raw_legacy_names if isinstance(raw_legacy_names, (list, tuple)) else ())
            if (name := _clean_string(item))
        ]
        legacy_count = _int_value(metadata.get("native_tool_calls_count"))
        if legacy_count > 0 or legacy_names:
            return {
                "native_tool_calls_count": legacy_count or len(legacy_names),
                "native_tool_call_names": legacy_names,
            }
    return {
        "native_tool_calls_count": 0,
        "native_tool_call_names": [],
    }


def native_tool_call_count_from_metadata(metadata: Mapping[str, Any] | None, *, fallback: int = 0) -> int:
    """Derive native tool-call count from lifecycle-aware metadata.

    Boundary:
        Run Ledger owns the precedence between envelope-derived facts,
        lifecycle receipt facts, legacy numeric metadata, and caller fallback.

    Complexity:
        O(r + e + n) time and memory through
        :func:`native_tool_call_facts_from_metadata`.
    """

    if isinstance(metadata, Mapping):
        facts = native_tool_call_facts_from_metadata(metadata)
        if facts:
            return _int_value(facts.get("native_tool_calls_count"))
        metadata_count = _int_value(metadata.get("native_tool_calls_count"))
        if metadata_count > 0:
            return metadata_count
    return _int_value(fallback)


def native_tool_call_count_from_facts(facts: Mapping[str, Any] | None, *, fallback: int = 0) -> int:
    """Derive native tool-call count from a Run Ledger native-fact mapping.

    Boundary:
        ``native_tool_call_facts_from_sources`` and related helpers emit the
        native-fact shape. Consumers should call this reader instead of
        interpreting ``native_tool_calls_count`` locally, so count coercion and
        fallback semantics stay owned by Run Ledger.

    Complexity:
        O(1) time and memory.
    """

    if isinstance(facts, Mapping):
        count = _int_value(facts.get("native_tool_calls_count"))
        if count > 0:
            return count
    return _int_value(fallback)


def tool_dispatch_dropped_guard_applies(
    *,
    native_tool_call_facts: Mapping[str, Any] | None,
    tool_definitions_present: bool,
    decoded_tool_batch_present: bool,
) -> bool:
    """Return whether a provider tool-call response was dropped before dispatch.

    Boundary:
        Run Ledger owns native tool-call count coercion for dropped-dispatch
        guards. Role runtimes pass canonical facts and tool-surface booleans;
        they must not derive the count before deciding whether to fail closed.

    Complexity:
        O(1) time and memory.
    """

    if not tool_definitions_present or decoded_tool_batch_present:
        return False
    return native_tool_call_count_from_facts(native_tool_call_facts) > 0


def native_tool_call_names_from_facts(
    facts: Mapping[str, Any] | None,
    *,
    fallback: Sequence[Any] = (),
) -> list[str]:
    """Derive native tool-call names from a Run Ledger native-fact mapping.

    Boundary:
        ``native_tool_call_facts_from_sources`` and related helpers emit the
        native-fact shape. Consumers should call this reader instead of
        interpreting ``native_tool_call_names`` locally, so name coercion and
        fallback semantics stay owned by Run Ledger.

    Complexity:
        O(n) time and memory where ``n`` is the number of candidate names.
    """

    raw_names: Sequence[Any]
    if isinstance(facts, Mapping):
        value = facts.get("native_tool_call_names")
        raw_names = value if isinstance(value, (list, tuple)) else ()
        if raw_names:
            return [name for item in raw_names if (name := _clean_string(item))]
    return [name for item in fallback if (name := _clean_string(item))]


def observed_tool_call_names_from_sources(
    tool_calls: Sequence[Any],
    metadata: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Derive observed tool-call names from runtime calls and lifecycle metadata.

    Boundary:
        Runtime cells may pass their raw observed ``tool_calls`` rows and
        lifecycle-aware metadata here. Run Ledger owns the alias order and the
        envelope fallback, so role/runtime projections do not maintain another
        tool-name extraction table.

    Complexity:
        O(c + r + e) time and memory where ``c`` is observed call count, ``r``
        is lifecycle receipt count, and ``e`` is envelope ref count.
    """

    names: list[str] = []
    for item in tool_calls or ():
        if not isinstance(item, Mapping):
            continue
        name = _observed_tool_call_name(item)
        if name:
            names.append(name)
    if names:
        return tuple(names)
    facts = native_tool_call_facts_from_metadata(metadata)
    return tuple(native_tool_call_names_from_facts(facts))


def project_native_tool_call_facts_from_evidence_to_metadata(
    metadata: dict[str, Any],
    evidence: Mapping[str, Any] | None,
) -> None:
    """Project lifecycle-derived native tool-call facts from evidence.

    Boundary:
        Run Ledger owns the evidence keys that are authoritative for native
        tool-call projections. Role kernels should call this helper instead of
        maintaining a second trigger-key table.

    Complexity:
        O(r + e + n) time and memory through
        :func:`native_tool_call_facts_from_metadata`.
    """

    if not isinstance(evidence, Mapping):
        return
    if not any(key in evidence for key in _NATIVE_TOOL_FACT_EVIDENCE_KEYS):
        return
    facts = native_tool_call_facts_from_metadata(evidence)
    if not facts:
        return
    project_native_tool_call_facts_to_metadata(
        metadata,
        facts,
        project_names=any(key in evidence for key in _NATIVE_TOOL_NAME_EVIDENCE_KEYS),
    )


def task_boundary_tool_dispatch_from_lifecycle_receipt(lifecycle_receipt: Mapping[str, Any]) -> dict[str, Any] | None:
    """Project TaskBoundary tool-dispatch evidence from one lifecycle receipt.

    Boundary:
        This is a read-only projection from the public
        ``tool_call_lifecycle_receipt.v1`` evidence shape. Role kernels should
        use this helper instead of locally reinterpreting lifecycle count,
        status, and native-tool fields before calling the TaskBoundary gate.

    Complexity:
        O(e + d) time and memory through
        :func:`native_tool_call_facts_from_lifecycle_receipt`.
    """

    lifecycle = normalize_tool_call_lifecycle_receipt(lifecycle_receipt)
    dispatch_status = _clean_string(lifecycle.get("dispatch_status"))
    failure_class = normalize_failure_class(lifecycle.get("failure_class"))
    task_boundary_failures = {
        FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        FailureClassV1.REQUIRED_TOOL_TEXT_FALLBACK_NOT_DISPATCHED.value,
        FailureClassV1.NO_MATERIALIZED_EFFECT.value,
    }
    if (
        dispatch_status not in {"dropped", "blocked", "decode_failed", "failed"}
        and failure_class not in task_boundary_failures
    ):
        return None
    native_facts = native_tool_call_facts_from_lifecycle_receipt(lifecycle)
    return {
        "status": dispatch_status or "failed",
        "dropped": dispatch_status == "dropped",
        "native_tool_calls_count": _int_value(native_facts.get("native_tool_calls_count")),
        "native_tool_call_names": list(native_facts.get("native_tool_call_names") or []),
        "decoded_tool_calls_count": _int_value(lifecycle.get("decoded_tool_calls_count")),
        "dispatched_tool_calls_count": _int_value(lifecycle.get("dispatched_tool_calls_count")),
        "provider_response_hash": _clean_string(lifecycle.get("provider_response_hash")),
        "failure_class": failure_class,
        "reason": _clean_string(lifecycle.get("reason")),
        "compatibility_mode": _clean_string(lifecycle.get("compatibility_mode")),
        "text_fallback_requested": bool(lifecycle.get("text_fallback_requested")),
        "parser_attempted": bool(lifecycle.get("parser_attempted")),
    }


def task_boundary_tool_dispatch_from_lifecycle_metadata(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    """Project TaskBoundary tool-dispatch evidence from lifecycle metadata.

    Boundary:
        Metadata may contain one or more lifecycle receipt compatibility keys.
        This helper only selects the dropped receipt; the receipt-to-dispatch
        projection itself is owned by
        :func:`task_boundary_tool_dispatch_from_lifecycle_receipt`.

    Complexity:
        O(n * (e + d)) over lifecycle receipts and native/dropped refs.
    """

    for receipt in tool_call_lifecycle_receipts_from_metadata(metadata):
        dispatch = task_boundary_tool_dispatch_from_lifecycle_receipt(receipt)
        if dispatch:
            return dispatch
    return None


def project_native_tool_call_facts_to_metadata(
    metadata: dict[str, Any],
    facts: Mapping[str, Any],
    *,
    project_names: bool = True,
    project_decision_caller_count: bool = False,
) -> None:
    """Write canonical native tool-call facts to a metadata projection.

    Boundary:
        This helper owns only the legacy metadata projection shape. Callers own
        where facts are derived from, and may suppress name projection when the
        source evidence did not contain names.

    Complexity:
        O(n) time and memory for normalizing the optional tool-name list.
    """

    native_count = _int_value(facts.get("native_tool_calls_count"))
    metadata["native_tool_calls_count"] = native_count
    if project_decision_caller_count:
        metadata["decision_caller_native_tool_calls_count"] = native_count
    if not project_names:
        return
    names = facts.get("native_tool_call_names")
    metadata["native_tool_call_names"] = [
        name for item in (names if isinstance(names, (list, tuple)) else []) if (name := _clean_string(item))
    ]


def project_native_tool_call_envelopes_to_metadata(
    metadata: dict[str, Any],
    envelopes: Sequence[Any],
) -> None:
    """Project native tool-call envelope evidence and derived facts.

    Boundary:
        Run Ledger owns the metadata projection for native tool-call envelopes,
        their count, and their names. Provider adapters may still construct raw
        envelope rows, but should not maintain a second count/name projection.

    Complexity:
        O(e) time and memory where ``e`` is envelope count.
    """

    valid_envelopes = normalize_native_tool_call_envelope_refs(envelopes)
    metadata["native_tool_call_envelopes"] = [dict(item) for item in valid_envelopes]
    project_native_tool_call_facts_to_metadata(
        metadata,
        {
            "native_tool_calls_count": len(valid_envelopes),
            "native_tool_call_names": [
                name for envelope in valid_envelopes if (name := _clean_string(envelope.get("tool_name")))
            ],
        },
    )


def failure_evidence_from_lifecycle_receipt(value: Any) -> dict[str, Any]:
    """Project lifecycle failure evidence into the Run Ledger taxonomy.

    Success receipts return an empty mapping. Callers should use this helper
    instead of reinterpreting ``failure_class`` or ``dispatch_status`` locally.

    Complexity:
        O(b + d + e) time and memory where ``b`` is batch receipt refs, ``d`` is
        dropped-call refs, and ``e`` is native envelope refs.
    """

    receipt = normalize_tool_call_lifecycle_receipt(value)
    failure_class = normalize_failure_class(receipt.get("failure_class"))
    dispatch_status = _clean_string(receipt.get("dispatch_status"))
    if not failure_class:
        if bool(receipt.get("ok")) and dispatch_status == "dispatched":
            return {}
        failure_class = FailureClassV1.TOOL_LIFECYCLE_UNKNOWN.value

    evidence_refs: list[str] = []
    provider_response_hash = _clean_string(receipt.get("provider_response_hash"))
    if provider_response_hash:
        evidence_refs.append(f"provider_response:{provider_response_hash}")
    for batch_ref in _mapping_refs(receipt.get("batch_receipt_refs")):
        receipt_hash = _clean_string(batch_ref.get("receipt_hash"))
        if receipt_hash:
            evidence_refs.append(f"batch_receipt:{receipt_hash}")
    for envelope in _native_tool_call_envelope_refs(receipt.get("native_tool_call_envelope_refs")):
        envelope_id = _clean_string(envelope.get("envelope_id"))
        if envelope_id:
            evidence_refs.append(f"native_tool_call:{envelope_id}")
    for dropped in _dropped_tool_call_refs(receipt.get("dropped_tool_calls")):
        evidence_refs.append(f"dropped_tool_call:{_stable_hash(dropped)}")

    reason = _clean_string(receipt.get("reason")) or dispatch_status or failure_class
    return FailureEvidenceV1(
        failure_class=failure_class,
        responsible_layer="execution_control_plane",
        reason=reason,
        evidence_refs=tuple(evidence_refs),
        metadata={
            "source": "tool_call_lifecycle_receipt.v1",
            "dispatch_status": dispatch_status,
            "native_tool_calls_count": _int_value(receipt.get("native_tool_calls_count")),
            "decoded_tool_calls_count": _int_value(receipt.get("decoded_tool_calls_count")),
            "dispatched_tool_calls_count": _int_value(receipt.get("dispatched_tool_calls_count")),
            "tool_result_count": _int_value(receipt.get("tool_result_count")),
            "effect_receipt_count": _int_value(receipt.get("effect_receipt_count")),
            "dropped_tool_calls": _dropped_tool_call_refs(receipt.get("dropped_tool_calls")),
        },
    ).to_dict()
