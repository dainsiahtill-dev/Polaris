"""Dispatch-dropped anomaly projections and metadata writers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from polaris.cells.control_plane.run_ledger.public.failure_evidence import (
    FailureClassV1,
    append_failure_evidence_to_metadata,
)
from polaris.cells.control_plane.run_ledger.public.tool_lifecycle._helpers import (
    _COMPLETION_AUDIT_EVIDENCE_KEYS,
    _COMPLETION_DISPATCH_EVIDENCE_KEYS,
    _clean_string,
    _dropped_tool_call_refs,
    _int_value,
    _native_tool_call_envelope_refs,
)
from polaris.cells.control_plane.run_ledger.public.tool_lifecycle._receipts import (
    build_tool_call_lifecycle_receipt,
    failure_evidence_from_lifecycle_receipt,
    native_tool_call_count_from_facts,
    native_tool_call_envelope_refs_from_metadata,
    native_tool_call_facts_from_sources,
    normalize_native_tool_call_envelope_refs,
    normalize_tool_call_lifecycle_receipt,
    project_native_tool_call_facts_from_evidence_to_metadata,
    tool_call_lifecycle_receipts_from_metadata,
)


def project_completion_dispatch_evidence_to_metadata(
    metadata: dict[str, Any],
    *evidence_sources: Mapping[str, Any] | None,
) -> None:
    """Project stream/non-stream completion dispatch evidence into metadata.

    Boundary:
        This owns the completion-side metadata keys that are evidence for
        lifecycle/envelope projection. Callers provide candidate source
        mappings; this helper only copies canonical evidence fields that are
        not already present and derives ``native_tool_call_envelope_refs`` via
        the same lifecycle-aware metadata helper used by Run Ledger projections.

    Complexity:
        O(s * k + e) time, where ``s`` is source count, ``k`` is the fixed
        evidence-key set, and ``e`` is native envelope count; O(e) memory.
    """

    for evidence_source in evidence_sources:
        if not isinstance(evidence_source, Mapping):
            continue
        for evidence_key in _COMPLETION_DISPATCH_EVIDENCE_KEYS:
            if evidence_key not in evidence_source or evidence_key in metadata:
                continue
            evidence_value = evidence_source[evidence_key]
            metadata[evidence_key] = dict(evidence_value) if isinstance(evidence_value, Mapping) else evidence_value
    native_tool_call_envelopes = native_tool_call_envelope_refs_from_metadata(metadata)
    if native_tool_call_envelopes:
        metadata.setdefault(
            "native_tool_call_envelope_refs",
            [dict(item) for item in native_tool_call_envelopes],
        )


def project_completion_audit_evidence_to_metadata(
    metadata: dict[str, Any],
    *evidence_sources: Mapping[str, Any] | None,
    overwrite_native_facts: bool = False,
) -> None:
    """Project completion audit evidence and lifecycle-derived facts.

    Boundary:
        Completion audit evidence is produced by stream and non-stream role
        execution paths, but Run Ledger owns the lifecycle/native/failure fact
        projection. Callers should pass candidate evidence mappings here rather
        than copying native-tool or failure-evidence keys locally.

    Complexity:
        O(s * k + n) time where ``s`` is source count, ``k`` is fixed evidence
        keys, and ``n`` is lifecycle evidence size; O(n) additional memory.
    """

    project_completion_dispatch_evidence_to_metadata(metadata, *evidence_sources)
    for evidence_source in evidence_sources:
        if not isinstance(evidence_source, Mapping):
            continue
        for evidence_key in _COMPLETION_AUDIT_EVIDENCE_KEYS:
            if evidence_key not in evidence_source:
                continue
            if evidence_key in metadata and not (
                overwrite_native_facts and evidence_key in {"native_tool_calls_count", "native_tool_call_names"}
            ):
                continue
            evidence_value = evidence_source[evidence_key]
            metadata[evidence_key] = dict(evidence_value) if isinstance(evidence_value, Mapping) else evidence_value
    project_tool_lifecycle_metadata(metadata)


def build_tool_dispatch_dropped_anomaly_projection(
    *,
    run_id: str,
    task_id: str,
    turn_id: str,
    role: str,
    provider_response_hash: str,
    native_tool_calls_count: int,
    native_tool_call_envelopes: list[Any] | tuple[Any, ...],
    streaming: bool = False,
    reason: str = "provider_emitted_tool_calls_but_no_decoded_tool_batch",
) -> dict[str, Any]:
    """Return the canonical dropped-dispatch anomaly projection.

    Boundary:
        Callers provide already-normalized response facts. This helper owns the
        lifecycle receipt, dropped-call count projection, and failure-evidence
        shape so stream and non-stream paths cannot drift.

    Complexity:
        O(e + d) over envelope and dropped-call receipt refs.
    """

    lifecycle = build_tool_call_lifecycle_receipt(
        run_id=run_id,
        task_id=task_id,
        turn_id=turn_id,
        role=role,
        provider_response_hash=provider_response_hash,
        native_tool_calls_count=native_tool_calls_count,
        dispatched_tool_calls_count=0,
        native_tool_call_envelopes=native_tool_call_envelopes,
        dispatch_status="dropped",
        failure_class=FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        reason=reason,
    ).to_dict()
    return build_tool_dispatch_dropped_anomaly_from_lifecycle_receipt(
        lifecycle,
        streaming=streaming,
    )


def build_tool_dispatch_dropped_anomaly_from_sources(
    *,
    run_id: str,
    task_id: str,
    turn_id: str,
    role: str,
    provider_response_hash: str,
    metadata: Mapping[str, Any] | None,
    native_tool_calls: Sequence[Any],
    native_tool_call_envelopes: Sequence[Any] = (),
    streaming: bool = False,
    reason: str = "provider_emitted_tool_calls_but_no_decoded_tool_batch",
) -> dict[str, Any]:
    """Return the dropped-dispatch anomaly from canonical native sources.

    Boundary:
        Run Ledger owns native tool-call fact precedence. Role runtimes pass
        structured metadata/raw calls/envelope refs and append the returned
        anomaly; they must not derive the native call count themselves.

    Complexity:
        O(r + e + n) through lifecycle receipts, envelope refs, and raw calls.
    """

    native_facts = native_tool_call_facts_from_sources(metadata, native_tool_calls)
    envelope_refs = normalize_native_tool_call_envelope_refs(native_tool_call_envelopes)
    if not envelope_refs:
        envelope_refs = native_tool_call_envelope_refs_from_metadata(metadata)
    return build_tool_dispatch_dropped_anomaly_projection(
        run_id=run_id,
        task_id=task_id,
        turn_id=turn_id,
        role=role,
        provider_response_hash=provider_response_hash,
        native_tool_calls_count=native_tool_call_count_from_facts(native_facts),
        native_tool_call_envelopes=envelope_refs,
        streaming=streaming,
        reason=reason,
    )


def build_tool_dispatch_dropped_anomaly_from_lifecycle_receipt(
    lifecycle_receipt: Mapping[str, Any],
    *,
    streaming: bool = False,
) -> dict[str, Any]:
    """Return the canonical anomaly flag for a dropped-dispatch lifecycle.

    Boundary:
        Lifecycle receipt construction and normalization remain in Run Ledger.
        Callers append the returned anomaly only; they do not copy lifecycle
        counters, dropped calls, envelopes, or failure evidence into ad-hoc
        dictionaries.

    Complexity:
        O(b + d + e) through lifecycle normalization and failure evidence
        projection.
    """

    lifecycle = normalize_tool_call_lifecycle_receipt(lifecycle_receipt)
    anomaly = {
        "type": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        "turn_id": lifecycle["turn_id"],
        "native_tool_calls_count": lifecycle["native_tool_calls_count"],
        "decoded_tool_calls_count": lifecycle["decoded_tool_calls_count"],
        "dispatched_tool_calls_count": lifecycle["dispatched_tool_calls_count"],
        "native_tool_call_envelopes": lifecycle["native_tool_call_envelope_refs"],
        "provider_response_hash": lifecycle["provider_response_hash"],
        "reason": lifecycle["reason"],
        "dropped_tool_calls": lifecycle["dropped_tool_calls"],
        "tool_call_lifecycle_receipt": lifecycle,
    }
    failure_evidence = failure_evidence_from_lifecycle_receipt(lifecycle)
    if failure_evidence:
        anomaly["failure_evidence"] = [failure_evidence]
    if streaming:
        anomaly["streaming"] = True
    return anomaly


def tool_dispatch_dropped_error_message(anomaly: Mapping[str, Any] | None) -> str:
    """Return the canonical runtime error text for dropped tool dispatch.

    Boundary:
        Run Ledger owns the dropped-dispatch lifecycle counters and their
        human-readable runtime projection. Role runtimes may raise the returned
        message, but must not restate native-call counts with local f-strings.

    Complexity:
        O(1); only top-level anomaly and nested lifecycle counters are read.
    """

    count = 0
    if isinstance(anomaly, Mapping):
        count = _int_value(anomaly.get("native_tool_calls_count"))
        lifecycle = anomaly.get("tool_call_lifecycle_receipt")
        if count <= 0 and isinstance(lifecycle, Mapping):
            count = _int_value(lifecycle.get("native_tool_calls_count"))
    return f"tool_dispatch_dropped: provider emitted {count} tool call(s), but no executable tool batch was decoded"


def build_tool_dispatch_dropped_lifecycle_from_anomaly_flags(
    *,
    anomaly_flags: Any,
    run_id: str,
    task_id: str,
    turn_id: str,
    role: str,
    reason: str,
) -> dict[str, Any]:
    """Return a canonical dropped-dispatch lifecycle from anomaly flags.

    Boundary:
        Older kernel paths may carry dropped-dispatch evidence as anomaly flag
        dictionaries. This helper is the Run Ledger public adapter for that
        compatibility shape, so callers do not reimplement lifecycle seed,
        envelope, count, or dropped-call extraction.

    Complexity:
        O(f + e + d) for anomaly flags, envelope refs, and dropped-call refs.
    """

    native_count = 1
    decoded_count = 0
    provider_response_hash = ""
    native_tool_call_envelopes: list[dict[str, Any]] = []
    dropped_tool_calls: list[dict[str, Any]] = []
    flags = anomaly_flags if isinstance(anomaly_flags, (list, tuple)) else ()
    for flag in flags:
        if not isinstance(flag, Mapping):
            continue
        if _clean_string(flag.get("type")) != FailureClassV1.TOOL_DISPATCH_DROPPED.value:
            continue
        lifecycle_raw = flag.get("tool_call_lifecycle_receipt") or flag.get("tool_call_lifecycle")
        if isinstance(lifecycle_raw, Mapping):
            lifecycle_seed = normalize_tool_call_lifecycle_receipt(lifecycle_raw)
            native_count = 0
            decoded_count = 0
            native_tool_call_envelopes = list(
                _native_tool_call_envelope_refs(lifecycle_seed.get("native_tool_call_envelope_refs"))
            )
            dropped_tool_calls = _dropped_tool_call_refs(lifecycle_seed.get("dropped_tool_calls"))
            provider_response_hash = _clean_string(lifecycle_seed.get("provider_response_hash"))
        else:
            native_tool_call_envelopes = list(
                _native_tool_call_envelope_refs(
                    flag.get("native_tool_call_envelope_refs") or flag.get("native_tool_call_envelopes")
                )
            )
            dropped_tool_calls = _dropped_tool_call_refs(flag.get("dropped_tool_calls"))
            native_count = len(native_tool_call_envelopes) or _int_value(flag.get("native_tool_calls_count")) or 1
            decoded_count = _int_value(flag.get("decoded_tool_calls_count"))
            provider_response_hash = _clean_string(flag.get("provider_response_hash"))
        break
    return build_tool_call_lifecycle_receipt(
        run_id=run_id,
        task_id=task_id,
        turn_id=turn_id,
        role=role,
        provider_response_hash=provider_response_hash,
        native_tool_calls_count=native_count,
        decoded_tool_calls_count=decoded_count,
        receipts=[],
        dropped_tool_calls=dropped_tool_calls,
        native_tool_call_envelopes=native_tool_call_envelopes,
        dispatch_status="dropped",
        failure_class=FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        reason=reason,
    ).to_dict()


def build_tool_dispatch_dropped_lifecycle_from_observed_calls(
    *,
    tool_names: Sequence[Any] = (),
    native_tool_call_envelopes: Sequence[Any] = (),
    run_id: str = "",
    task_id: str = "",
    turn_id: str = "",
    role: str = "",
    reason: str = "tool_dispatch_dropped",
) -> dict[str, Any]:
    """Return a dropped-dispatch lifecycle for observed calls without results.

    Boundary:
        Runtime callers provide observed tool names and/or native envelopes.
        Run Ledger owns the compatibility ``dropped_tool_calls`` shape and
        lifecycle projection.

    Complexity:
        O(t + e) over observed tool names and native envelope refs.
    """

    envelopes = _native_tool_call_envelope_refs(native_tool_call_envelopes)
    dropped_tool_calls: list[dict[str, Any]] = []
    if not envelopes:
        seen_tools: set[str] = set()
        for tool_name in tool_names:
            normalized = _clean_string(tool_name)
            if not normalized or normalized in seen_tools:
                continue
            seen_tools.add(normalized)
            dropped_tool_calls.append(
                {
                    "tool_name": normalized,
                    "reason": "tool_dispatch_dropped",
                }
            )
    return build_tool_call_lifecycle_receipt(
        run_id=run_id,
        task_id=task_id,
        turn_id=turn_id,
        role=role,
        native_tool_calls_count=len(envelopes) or len(dropped_tool_calls),
        receipts=[],
        dropped_tool_calls=dropped_tool_calls,
        native_tool_call_envelopes=envelopes,
        dispatch_status="dropped",
        failure_class=FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        reason=reason,
    ).to_dict()


def project_lifecycle_failure_evidence_to_metadata(
    metadata: dict[str, Any],
    lifecycle: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Append lifecycle-derived failure evidence to metadata.

    Boundary:
        This helper is the lifecycle-specific metadata projection entrypoint.
        It keeps lifecycle decoding in ``tool_lifecycle`` and metadata row /
        summary projection in ``failure_evidence``.

    Complexity:
        O(b + d + e + n*m) time from lifecycle evidence projection plus stable
        evidence row de-duplication; O(b + d + e + n) memory.
    """

    failure_evidence = failure_evidence_from_lifecycle_receipt(lifecycle)
    if not failure_evidence:
        return []
    return append_failure_evidence_to_metadata(metadata, failure_evidence)


def project_tool_lifecycle_metadata(metadata: dict[str, Any]) -> None:
    """Project canonical lifecycle, failure, and native tool facts into metadata.

    Boundary:
        This is the public projection owner for lifecycle-derived RoleTurnResult
        and runtime metadata. It canonicalizes existing lifecycle receipt
        evidence, appends lifecycle failure evidence when present, and derives
        native tool-call count/name facts from the same metadata. It does not
        create lifecycle receipts or authorize tool effects.

    Complexity:
        O(n) time and memory for lifecycle receipt and native envelope rows.
    """

    receipts = tool_call_lifecycle_receipts_from_metadata(metadata)
    if receipts:
        canonical_receipt = dict(receipts[0])
        metadata["tool_call_lifecycle_receipt"] = canonical_receipt
        project_lifecycle_failure_evidence_to_metadata(metadata, canonical_receipt)
    project_native_tool_call_facts_from_evidence_to_metadata(metadata, metadata)


def project_tool_lifecycle_receipt_to_metadata(
    metadata: dict[str, Any],
    lifecycle_receipt: Mapping[str, Any],
) -> None:
    """Project one lifecycle receipt into canonical/compat metadata keys.

    Boundary:
        Run Ledger owns the metadata key projection for lifecycle receipts.
        Completion owners may build or receive a receipt, but should not know
        which canonical and compatibility keys must be written.

    Complexity:
        O(n) time and memory through :func:`project_tool_lifecycle_metadata`.
    """

    metadata["tool_call_lifecycle_receipt"] = normalize_tool_call_lifecycle_receipt(lifecycle_receipt)
    metadata["tool_call_lifecycle"] = metadata["tool_call_lifecycle_receipt"]
    project_tool_lifecycle_metadata(metadata)
