"""Tool-call lifecycle receipt contracts for Run Ledger projections."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from polaris.cells.control_plane.run_ledger.public.failure_evidence import (
    FailureClassV1,
    FailureEvidenceV1,
    append_failure_evidence_to_metadata,
    normalize_failure_class,
)
from polaris.kernelone.tools.tool_kinds import is_write_tool_name


def _stable_json(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _clean_string(value: Any) -> str:
    return str(value or "").strip()


def _dispatch_status_key(value: Any) -> str:
    return "_".join(_clean_string(value).lower().replace("-", "_").split())


def _normalize_dispatch_status(value: Any) -> str:
    key = _dispatch_status_key(value)
    if not key:
        return ""
    aliases = {
        "ok": "dispatched",
        "success": "dispatched",
        "succeeded": "dispatched",
        "dispatched": "dispatched",
        "dropped": "dropped",
        "tool_dispatch_dropped": "dropped",
        "blocked": "blocked",
        "failed": "blocked",
        "failure": "blocked",
        "error": "blocked",
        "decode_failed": "decode_failed",
        "decode_error": "decode_failed",
        "unknown": "unknown",
    }
    return aliases.get(key, key)


def _int_value(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean_string(item)
        if text and text not in seen:
            output.append(text)
            seen.add(text)
    return output


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text_fallback_lifecycle_fields(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = _mapping(metadata)
    audit = _mapping(payload.get("final_request_context_audit"))
    surface = _mapping(audit.get("tool_execution_surface"))
    text_fallback_requested = bool(
        payload.get("text_fallback_requested")
        or payload.get("required_tool_text_fallback")
        or surface.get("text_fallback_requested")
    )
    compatibility_mode = _clean_string(payload.get("compatibility_mode") or surface.get("compatibility_mode")) or (
        "required_tool_text_fallback" if text_fallback_requested else "native_tools"
    )
    return {
        "compatibility_mode": compatibility_mode,
        "text_fallback_requested": text_fallback_requested,
        "parser_attempted": bool(payload.get("text_tool_parser_attempted") or surface.get("parser_attempted")),
        "native_tool_surface_absent_because_text_fallback": bool(
            payload.get("native_tool_surface_absent_because_text_fallback")
            or surface.get("native_tool_surface_absent_because_text_fallback")
        ),
    }


def _dropped_tool_call_refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    refs: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            ref = dict(item)
            if ref:
                refs.append(ref)
            continue
        tool_name = _clean_string(item)
        if tool_name:
            refs.append({"tool_name": tool_name, "reason": "tool_dispatch_dropped"})
    return refs


def _dropped_tool_call_count(refs: list[dict[str, Any]]) -> int:
    count = 0
    for ref in refs:
        explicit_count = _int_value(ref.get("count"))
        if explicit_count > 0:
            count += explicit_count
            continue
        count += 1
    return count


def _native_tool_call_envelope_refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            continue
        ref = dict(item)
        if not ref:
            continue
        key = _clean_string(ref.get("envelope_id"))
        if not key:
            key = _stable_hash(
                {
                    "call_id": _clean_string(ref.get("call_id")),
                    "tool_name": _clean_string(ref.get("tool_name")),
                    "raw_call_hash": _clean_string(ref.get("raw_call_hash")),
                    "arguments_hash": _clean_string(ref.get("arguments_hash")),
                }
            )
        if key in seen:
            continue
        seen.add(key)
        refs.append(ref)
    return refs


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


def _mapping_ref_key(value: Mapping[str, Any]) -> str:
    for key in ("receipt_hash", "batch_id", "effect_receipt_hash", "id"):
        token = _clean_string(value.get(key))
        if token:
            return f"{key}:{token}"
    return "stable:" + _stable_hash(dict(value))


def _mapping_refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            continue
        ref = dict(item)
        if not ref:
            continue
        key = _mapping_ref_key(ref)
        if key in seen:
            continue
        seen.add(key)
        refs.append(ref)
    return refs


def _dropped_tool_calls_from_native_envelopes(value: Any) -> list[dict[str, Any]]:
    dropped: list[dict[str, Any]] = []
    for envelope in _native_tool_call_envelope_refs(value):
        tool_name = _clean_string(envelope.get("tool_name"))
        envelope_id = _clean_string(envelope.get("envelope_id"))
        if not tool_name and not envelope_id:
            continue
        item: dict[str, Any] = {"reason": "tool_dispatch_dropped"}
        if tool_name:
            item["tool_name"] = tool_name
        if envelope_id:
            item["envelope_id"] = envelope_id
        dropped.append(item)
    return dropped


def _result_items(receipts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for receipt in receipts:
        for key in ("results", "raw_results"):
            values = receipt.get(key)
            if isinstance(values, list):
                rows.extend(dict(item) for item in values if isinstance(item, dict))
    return rows


def _effect_receipt_from_result(item: dict[str, Any]) -> dict[str, Any]:
    direct = item.get("effect_receipt")
    if isinstance(direct, dict):
        return dict(direct)
    result = item.get("result")
    if isinstance(result, dict):
        nested = result.get("effect_receipt")
        if isinstance(nested, dict):
            return dict(nested)
    return {}


def _effect_receipt_refs(receipts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _result_items(receipts):
        effect = _effect_receipt_from_result(item)
        if not effect:
            continue
        receipt_hash = _stable_hash(effect)
        if receipt_hash in seen:
            continue
        seen.add(receipt_hash)
        refs.append(
            {
                "receipt_hash": receipt_hash,
                "operation": _clean_string(effect.get("operation")),
                "file": _clean_string(effect.get("file") or effect.get("path")),
                "tool_name": _clean_string(item.get("tool_name")),
                "call_id": _clean_string(item.get("call_id")),
                "before_hash": _clean_string(effect.get("before_hash")),
                "after_hash": _clean_string(effect.get("after_hash")),
            }
        )
    return refs


def _successful_write_results_without_effect_receipts(receipts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    for item in _result_items(receipts):
        tool_name = _clean_string(item.get("tool_name"))
        status = _clean_string(item.get("status")).lower()
        if status != "success" or not is_write_tool_name(tool_name):
            continue
        if _effect_receipt_from_result(item):
            continue
        missing.append(
            {
                "tool_name": tool_name,
                "call_id": _clean_string(item.get("call_id")),
                "reason": "successful_write_tool_without_effect_receipt",
            }
        )
    return missing


def _batch_receipt_refs(receipts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for receipt in receipts:
        receipt_hash = _stable_hash(receipt)
        if receipt_hash in seen:
            continue
        seen.add(receipt_hash)
        refs.append(
            {
                "batch_id": _clean_string(receipt.get("batch_id")),
                "receipt_hash": receipt_hash,
            }
        )
    return refs


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
    elif result_count > 0:
        status = status or "dispatched"
    else:
        status = status or "blocked"
        failure = failure or FailureClassV1.MISSING_TOOL_RESULT.value

    failure_count = sum(_int_value(receipt.get("failure_count")) for receipt in receipt_rows)
    if failure_count > 0:
        failure = failure or FailureClassV1.TOOL_RESULT_FAILED.value

    ok = status == "dispatched" and failure_count == 0 and not failure
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
        reason=_clean_string(reason),
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


def _append_effect_receipt_copy(effect_receipts: list[dict[str, Any]], candidate: Any) -> None:
    if isinstance(candidate, dict):
        effect_receipts.append(dict(candidate))


def _append_top_level_effect_receipts(effect_receipts: list[dict[str, Any]], candidates: Any) -> None:
    if not isinstance(candidates, list):
        return
    for candidate in candidates:
        _append_effect_receipt_copy(effect_receipts, candidate)


def _append_result_effect_receipts(effect_receipts: list[dict[str, Any]], raw_results: Any) -> None:
    if not isinstance(raw_results, list):
        return
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        direct = item.get("effect_receipt")
        if isinstance(direct, dict):
            _append_effect_receipt_copy(effect_receipts, direct)
            continue
        result = item.get("result")
        if isinstance(result, dict):
            _append_effect_receipt_copy(effect_receipts, result.get("effect_receipt"))


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

    Complexity:
        O(t + m + e + r) over required tools, metadata candidates, native
        envelopes, and batch receipt rows.
    """

    if tool_results or batch_receipt_has_dispatch_evidence(batch_receipt):
        return None

    tools: list[str] = []
    seen_tools: set[str] = set()
    for tool_name in required_write_tools:
        normalized = _clean_string(tool_name)
        if not normalized or not is_write_tool_name(normalized) or normalized in seen_tools:
            continue
        seen_tools.add(normalized)
        tools.append(normalized)
    if not tools:
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
        reason=("required_tool_text_fallback_not_dispatched" if text_fallback_not_dispatched else reason),
        **fallback_fields,
    ).to_dict()


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


def _raw_native_tool_call_name(call: Mapping[str, Any]) -> str:
    function = call.get("function")
    if isinstance(function, Mapping):
        name = _clean_string(function.get("name"))
        if name:
            return name
    for key in ("name", "tool_name", "toolName", "function_name", "functionName"):
        name = _clean_string(call.get(key))
        if name:
            return name
    return ""


def _observed_tool_call_name(call: Mapping[str, Any]) -> str:
    function = call.get("function")
    if isinstance(function, Mapping):
        name = _clean_string(function.get("name"))
        if name:
            return name
    for key in ("name", "tool", "tool_name", "toolName", "function_name", "functionName"):
        name = _clean_string(call.get(key))
        if name:
            return name
    return ""


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


def _native_tool_call_arguments(call: Mapping[str, Any]) -> Any:
    function = call.get("function")
    if isinstance(function, Mapping) and "arguments" in function:
        return function.get("arguments")
    for key in ("arguments", "input", "args", "parameters"):
        if key in call:
            return call.get(key)
    return {}


def _native_tool_call_id(call: Mapping[str, Any], *, index: int, raw_call_hash: str) -> str:
    for key in ("id", "call_id", "tool_call_id", "toolUseId"):
        value = call.get(key)
        if value:
            return _clean_string(value)
    return f"native_tool_call_{index}_{raw_call_hash[:12]}"


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


_NATIVE_TOOL_FACT_EVIDENCE_KEYS: tuple[str, ...] = (
    "tool_call_lifecycle",
    "tool_call_lifecycle_receipt",
    "tool_call_lifecycle_receipts",
    "native_tool_call_envelopes",
    "native_tool_call_envelope_refs",
    "native_tool_calls_count",
    "native_tool_call_names",
)

_NATIVE_TOOL_NAME_EVIDENCE_KEYS: tuple[str, ...] = (
    "tool_call_lifecycle",
    "tool_call_lifecycle_receipt",
    "tool_call_lifecycle_receipts",
    "native_tool_call_envelopes",
    "native_tool_call_envelope_refs",
    "native_tool_call_names",
)


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
    if dispatch_status not in {"dropped", "blocked", "decode_failed", "failed"} and failure_class not in task_boundary_failures:
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


_TOOL_LIFECYCLE_OUTCOME_SCHEMA_VERSION = "polaris.tool_lifecycle_outcome_projection.v1"


def _lifecycle_task_identity(value: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    """Return the stable task identity for one lifecycle projection row."""

    event = dict(value)
    receipt = _mapping(event.get("receipt"))
    task_id = _clean_string(event.get("task_id")) or _clean_string(receipt.get("task_id"))
    run_id = _clean_string(event.get("run_id")) or _clean_string(receipt.get("run_id"))
    turn_id = _clean_string(event.get("turn_id")) or _clean_string(receipt.get("turn_id"))
    if task_id:
        return task_id, task_id, run_id, turn_id, "task_id"
    explicit_task_key = _clean_string(event.get("task_key"))
    if explicit_task_key:
        return (
            explicit_task_key,
            "",
            run_id,
            turn_id,
            _clean_string(event.get("task_identity_source")) or "explicit_task_key",
        )
    return (
        f"run:{run_id or 'unknown'}|turn:{turn_id or 'unknown'}",
        "",
        run_id,
        turn_id,
        "run_turn_fallback",
    )


def _lifecycle_event_is_unresolved(event: Mapping[str, Any]) -> bool:
    """Return whether a canonical lifecycle row remains unresolved."""

    return bool(event.get("dropped")) or bool(event.get("failed")) or event.get("ok") is False


def _lifecycle_outcome_projection_from_events(
    events: Sequence[Mapping[str, Any]],
    *,
    source: str,
    degraded: bool,
    fallback: str = "",
) -> dict[str, Any]:
    """Project latest and unresolved lifecycle state once per task identity."""

    latest_by_task: dict[str, dict[str, Any]] = {}
    for raw_event in events:
        if not isinstance(raw_event, Mapping):
            continue
        event = dict(raw_event)
        task_key, task_id, run_id, turn_id, identity_source = _lifecycle_task_identity(event)
        event["task_key"] = task_key
        event["task_id"] = task_id
        event["run_id"] = run_id
        event["turn_id"] = turn_id
        event["task_identity_source"] = identity_source
        latest_by_task.pop(task_key, None)
        latest_by_task[task_key] = event
    unresolved_by_task = {
        task_key: dict(event)
        for task_key, event in latest_by_task.items()
        if _lifecycle_event_is_unresolved(event)
    }
    return {
        "ok": not unresolved_by_task,
        "latest_by_task": latest_by_task,
        "unresolved_by_task": unresolved_by_task,
        "unresolved_count": len(unresolved_by_task),
        "unresolved_dropped_count": sum(bool(event.get("dropped")) for event in unresolved_by_task.values()),
        "unresolved_failed_count": sum(bool(event.get("failed")) for event in unresolved_by_task.values()),
        "outcome_projection": {
            "schema_version": _TOOL_LIFECYCLE_OUTCOME_SCHEMA_VERSION,
            "source": source,
            "degraded": degraded,
            "fallback": fallback,
        },
    }


def _canonical_lifecycle_outcome_projection(summary: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return a normalized canonical outcome projection when one is present."""

    latest_raw = summary.get("latest_by_task")
    unresolved_raw = summary.get("unresolved_by_task")
    metadata_raw = summary.get("outcome_projection")
    if not isinstance(latest_raw, Mapping) or not isinstance(unresolved_raw, Mapping):
        return None
    metadata = _mapping(metadata_raw)
    if metadata.get("schema_version") != _TOOL_LIFECYCLE_OUTCOME_SCHEMA_VERSION:
        return None
    latest_by_task = {
        _clean_string(task_key): dict(event)
        for task_key, event in latest_raw.items()
        if _clean_string(task_key) and isinstance(event, Mapping)
    }
    unresolved_by_task = {
        _clean_string(task_key): dict(event)
        for task_key, event in unresolved_raw.items()
        if _clean_string(task_key) and isinstance(event, Mapping)
    }
    for task_key, event in latest_by_task.items():
        event.setdefault("task_key", task_key)
    for task_key, event in unresolved_by_task.items():
        event.setdefault("task_key", task_key)
    return {
        "ok": not unresolved_by_task,
        "latest_by_task": latest_by_task,
        "unresolved_by_task": unresolved_by_task,
        "unresolved_count": len(unresolved_by_task),
        "unresolved_dropped_count": sum(bool(event.get("dropped")) for event in unresolved_by_task.values()),
        "unresolved_failed_count": sum(bool(event.get("failed")) for event in unresolved_by_task.values()),
        "outcome_projection": {
            "schema_version": _TOOL_LIFECYCLE_OUTCOME_SCHEMA_VERSION,
            "source": _clean_string(metadata.get("source")) or "canonical",
            "degraded": bool(metadata.get("degraded")),
            "fallback": _clean_string(metadata.get("fallback")),
        },
    }


def _legacy_lifecycle_outcome_projection(
    summary: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project old lifecycle summaries with an explicit structured fallback."""

    event_projection = _lifecycle_outcome_projection_from_events(
        events,
        source="legacy_event_rows",
        degraded=True,
        fallback="legacy_event_rows",
    )
    historical_failed = _int_value(summary.get("dropped_count")) > 0 or _int_value(summary.get("failed_count")) > 0
    if event_projection["unresolved_count"] or (
        not historical_failed and bool(summary.get("ok", True))
    ):
        return event_projection
    dropped = _int_value(summary.get("dropped_count")) > 0
    failure_class = (
        FailureClassV1.TOOL_DISPATCH_DROPPED.value if dropped else FailureClassV1.TOOL_LIFECYCLE_FAILED.value
    )
    return _lifecycle_outcome_projection_from_events(
        [
            {
                "task_key": "legacy:aggregate",
                "task_identity_source": "legacy_aggregate_fallback",
                "status": "dropped" if dropped else "failed",
                "failure_class": failure_class,
                "reason": failure_class,
                "dropped": dropped,
                "failed": True,
                "ok": False,
            }
        ],
        source="legacy_aggregate",
        degraded=True,
        fallback="historical_counts",
    )


def project_tool_lifecycle_event(
    value: Any,
    *,
    append_id: Any = "",
    content_id: Any = "",
) -> dict[str, Any]:
    """Project one lifecycle receipt into the canonical Run Ledger read-model row.

    Boundary:
        The projection is derived only from ``tool_call_lifecycle_receipt.v1``.
        It centralizes lifecycle counters, dropped/failed flags, receipt refs and
        lifecycle-derived failure evidence for Run Ledger projections.

    Complexity:
        O(e + d) time and memory through lifecycle normalization and native-tool
        fact projection, where ``e`` is envelope refs and ``d`` is dropped-call
        refs.
    """

    lifecycle = normalize_tool_call_lifecycle_receipt(value)
    native_facts = native_tool_call_facts_from_lifecycle_receipt(lifecycle)
    native_count = _int_value(native_facts.get("native_tool_calls_count"))
    native_names = list(native_facts.get("native_tool_call_names") or [])
    decoded_count = _int_value(lifecycle.get("decoded_tool_calls_count"))
    dispatched_count = _int_value(lifecycle.get("dispatched_tool_calls_count"))
    result_count = _int_value(lifecycle.get("tool_result_count"))
    effect_count = _int_value(lifecycle.get("effect_receipt_count"))
    dispatch_status = _clean_string(lifecycle.get("dispatch_status"))
    dropped = bool(lifecycle.get("dropped")) or dispatch_status == "dropped"
    if native_count > 0 and dispatched_count <= 0:
        dropped = True
    failed = not bool(lifecycle.get("ok", False))
    failure_evidence = failure_evidence_from_lifecycle_receipt(lifecycle)
    task_key, task_id, run_id, turn_id, identity_source = _lifecycle_task_identity(lifecycle)
    row = {
        "status": dispatch_status or ("dropped" if dropped else "ok"),
        "failure_class": _clean_string(lifecycle.get("failure_class")),
        "reason": _clean_string(lifecycle.get("reason")),
        "ok": not failed,
        "failed": failed,
        "native_tool_calls_count": native_count,
        "native_tool_call_names": native_names,
        "decoded_tool_calls_count": decoded_count,
        "dispatched_tool_calls_count": dispatched_count,
        "tool_result_count": result_count,
        "effect_receipt_count": effect_count,
        "dropped": dropped,
        "provider_response_hash": _clean_string(lifecycle.get("provider_response_hash")),
        "batch_receipt_hash": _clean_string(lifecycle.get("batch_receipt_hash")),
        "batch_receipt_refs": _mapping_refs(lifecycle.get("batch_receipt_refs")),
        "effect_receipt_refs": _mapping_refs(lifecycle.get("effect_receipt_refs")),
        "receipt": lifecycle,
        "task_key": task_key,
        "task_id": task_id,
        "run_id": run_id,
        "turn_id": turn_id,
        "task_identity_source": identity_source,
        "append_id": _clean_string(append_id),
        "content_id": _clean_string(content_id),
    }
    if failure_evidence:
        row["failure_evidence"] = failure_evidence
    return row


def summarize_tool_lifecycle_events(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize canonical lifecycle event rows for Run Ledger projections.

    Boundary:
        Input rows must already come from :func:`project_tool_lifecycle_event`.
        This helper owns aggregate lifecycle counters so Run Ledger projections
        do not maintain a second hand-written interpretation of event fields.

    Complexity:
        O(n + m) time and memory where ``n`` is lifecycle event count and ``m``
        is the number of native tool names / failure evidence rows.
    """

    native_count = 0
    decoded_count = 0
    dispatched_count = 0
    result_count = 0
    effect_count = 0
    dropped_count = 0
    failed_count = 0
    native_names: list[str] = []
    failure_evidence: list[dict[str, Any]] = []
    projected_events: list[dict[str, Any]] = []
    for raw_event in events:
        if not isinstance(raw_event, Mapping):
            continue
        event = dict(raw_event)
        projected_events.append(event)
        native_count += _int_value(event.get("native_tool_calls_count"))
        decoded_count += _int_value(event.get("decoded_tool_calls_count"))
        dispatched_count += _int_value(event.get("dispatched_tool_calls_count"))
        result_count += _int_value(event.get("tool_result_count"))
        effect_count += _int_value(event.get("effect_receipt_count"))
        native_names.extend(name for item in event.get("native_tool_call_names") or [] if (name := _clean_string(item)))
        if bool(event.get("dropped")):
            dropped_count += 1
        if bool(event.get("failed")):
            failed_count += 1
        evidence = event.get("failure_evidence")
        if isinstance(evidence, Mapping):
            failure_evidence.append(dict(evidence))
    outcome_projection = _lifecycle_outcome_projection_from_events(
        projected_events,
        source="event_rows",
        degraded=False,
    )
    return {
        "ok": bool(outcome_projection["ok"]),
        "event_count": len(projected_events),
        "native_tool_calls_count": native_count,
        "decoded_tool_calls_count": decoded_count,
        "dispatched_tool_calls_count": dispatched_count,
        "tool_result_count": result_count,
        "effect_receipt_count": effect_count,
        "native_tool_call_names": list(dict.fromkeys(native_names)),
        "dropped_count": dropped_count,
        "failed_count": failed_count,
        "failure_evidence": failure_evidence,
        "events": projected_events,
        **outcome_projection,
    }


def project_tool_lifecycle_summary(summary: Mapping[str, Any] | None) -> dict[str, Any]:
    """Project the canonical lifecycle summary into Run Ledger read-model shape.

    Boundary:
        ``summarize_tool_lifecycle_events`` owns lifecycle aggregation and this
        helper owns the read-model field projection for that aggregate. Generic
        Run Ledger projections should not hand-copy lifecycle count/name/event
        fields because that recreates a second summary contract.

    Complexity:
        O(n + m) over native tool names, failure evidence rows, and events; O(n)
        memory for the copied projection lists.
    """

    lifecycle = summary if isinstance(summary, Mapping) else {}
    if not lifecycle:
        lifecycle = empty_tool_lifecycle_summary()
    failure_evidence_raw = lifecycle.get("failure_evidence")
    events_raw = lifecycle.get("events")
    failure_evidence = (
        [dict(item) for item in failure_evidence_raw if isinstance(item, Mapping)]
        if isinstance(failure_evidence_raw, list)
        else []
    )
    events = [dict(item) for item in events_raw if isinstance(item, Mapping)] if isinstance(events_raw, list) else []
    outcome_projection = _canonical_lifecycle_outcome_projection(lifecycle)
    if outcome_projection is None:
        outcome_projection = _legacy_lifecycle_outcome_projection(lifecycle, events)
    return {
        "ok": bool(outcome_projection["ok"]),
        "event_count": _int_value(lifecycle.get("event_count")),
        "native_tool_calls_count": _int_value(lifecycle.get("native_tool_calls_count")),
        "decoded_tool_calls_count": _int_value(lifecycle.get("decoded_tool_calls_count")),
        "dispatched_tool_calls_count": _int_value(lifecycle.get("dispatched_tool_calls_count")),
        "tool_result_count": _int_value(lifecycle.get("tool_result_count")),
        "effect_receipt_count": _int_value(lifecycle.get("effect_receipt_count")),
        "native_tool_call_names": _string_list(lifecycle.get("native_tool_call_names")),
        "dropped_count": _int_value(lifecycle.get("dropped_count")),
        "failed_count": _int_value(lifecycle.get("failed_count")),
        "failure_evidence": failure_evidence,
        "events": events,
        **outcome_projection,
    }


def project_tool_lifecycle_failure_status(summary: Mapping[str, Any] | None) -> dict[str, Any]:
    """Project aggregate lifecycle failure status from a canonical summary.

    Boundary:
        Run Ledger owns the precedence between dropped dispatch and other
        lifecycle failures. Runtime/UI projections should consume this helper
        instead of reinterpreting ``dropped_count`` / ``failed_count`` locally.

    Complexity:
        O(n) time and O(1) additional memory over projected lifecycle events.
    """

    lifecycle = summary if isinstance(summary, Mapping) else {}
    projected = project_tool_lifecycle_summary(lifecycle)
    unresolved_raw = projected.get("unresolved_by_task")
    unresolved_by_task = unresolved_raw if isinstance(unresolved_raw, Mapping) else {}
    metadata = _mapping(projected.get("outcome_projection"))
    degraded = bool(metadata.get("degraded"))
    fallback = _clean_string(metadata.get("fallback"))
    if not unresolved_by_task:
        return {
            "failed": False,
            "status": "",
            "failure_class": "",
            "reason": "",
            "degraded": degraded,
            "fallback": fallback,
        }

    latest = next(reversed(unresolved_by_task.values()))
    latest_event = dict(latest) if isinstance(latest, Mapping) else {}
    dropped = bool(latest_event.get("dropped"))
    failure_class = normalize_failure_class(
        _clean_string(latest_event.get("failure_class"))
        or (FailureClassV1.TOOL_DISPATCH_DROPPED.value if dropped else FailureClassV1.TOOL_LIFECYCLE_FAILED.value)
    )
    return {
        "failed": True,
        "status": _clean_string(latest_event.get("status")) or ("dropped" if dropped else "failed"),
        "failure_class": failure_class,
        "reason": _clean_string(latest_event.get("reason")) or failure_class,
        "degraded": degraded,
        "fallback": fallback,
    }


def empty_tool_lifecycle_summary() -> dict[str, Any]:
    """Return the canonical empty tool-lifecycle summary shape."""

    return {
        "ok": True,
        "event_count": 0,
        "native_tool_calls_count": 0,
        "decoded_tool_calls_count": 0,
        "dispatched_tool_calls_count": 0,
        "tool_result_count": 0,
        "effect_receipt_count": 0,
        "native_tool_call_names": [],
        "dropped_count": 0,
        "failed_count": 0,
        "failure_evidence": [],
        "events": [],
        "latest_by_task": {},
        "unresolved_by_task": {},
        "unresolved_count": 0,
        "unresolved_dropped_count": 0,
        "unresolved_failed_count": 0,
        "outcome_projection": {
            "schema_version": _TOOL_LIFECYCLE_OUTCOME_SCHEMA_VERSION,
            "source": "event_rows",
            "degraded": False,
            "fallback": "",
        },
    }


def merge_tool_lifecycle_summaries(projects: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Merge project-level lifecycle summaries into a single read model.

    Boundary:
        This is a pure projection helper. It does not inspect ledger event rows
        and does not create lifecycle receipts.

    Complexity:
        O(n + m) time and memory, where ``n`` is project count and ``m`` is the
        total number of projected lifecycle events and native tool names.
    """

    totals = empty_tool_lifecycle_summary()
    native_names: list[str] = []
    failure_evidence: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for project in projects:
        lifecycle_raw = project.get("tool_lifecycle")
        lifecycle: Mapping[str, Any] = lifecycle_raw if isinstance(lifecycle_raw, Mapping) else {}
        if not lifecycle:
            continue
        totals["ok"] = bool(totals["ok"]) and bool(lifecycle.get("ok", True))
        for key in (
            "event_count",
            "native_tool_calls_count",
            "decoded_tool_calls_count",
            "dispatched_tool_calls_count",
            "tool_result_count",
            "effect_receipt_count",
            "dropped_count",
            "failed_count",
        ):
            totals[key] = _int_value(totals.get(key)) + _int_value(lifecycle.get(key))
        native_names.extend(
            name for item in lifecycle.get("native_tool_call_names") or [] if (name := _clean_string(item))
        )
        raw_failure_evidence = lifecycle.get("failure_evidence")
        if isinstance(raw_failure_evidence, list):
            failure_evidence.extend(dict(item) for item in raw_failure_evidence if isinstance(item, Mapping))
        raw_events = lifecycle.get("events")
        if isinstance(raw_events, list):
            events.extend(dict(item) for item in raw_events if isinstance(item, Mapping))
    totals["native_tool_call_names"] = list(dict.fromkeys(native_names))
    totals["failure_evidence"] = failure_evidence
    totals["events"] = events
    canonical_event_rows = bool(events) and all(
        isinstance(event, Mapping) and bool(_clean_string(event.get("task_key"))) for event in events
    )
    outcome_projection = (
        _lifecycle_outcome_projection_from_events(
            events,
            source="merged_event_rows",
            degraded=False,
        )
        if canonical_event_rows
        else _legacy_lifecycle_outcome_projection(totals, events)
    )
    totals.update(outcome_projection)
    return totals


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


_COMPLETION_DISPATCH_EVIDENCE_KEYS: tuple[str, ...] = (
    "final_request_context_audit",
    "required_tools",
    "tool_call_lifecycle",
    "tool_call_lifecycle_receipt",
    "tool_call_lifecycle_receipts",
    "native_tool_call_envelopes",
    "native_tool_call_envelope_refs",
)

_COMPLETION_AUDIT_EVIDENCE_KEYS: tuple[str, ...] = (
    "native_tool_calls_count",
    "native_tool_call_names",
    "failure_evidence",
    "failure_evidence_summary",
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


__all__ = [
    "NativeToolCallEnvelopeV1",
    "ToolCallLifecycleReceiptV1",
    "batch_receipt_has_dispatch_evidence",
    "build_missing_dispatch_lifecycle_receipt",
    "build_native_tool_call_envelope_payloads",
    "build_native_tool_call_envelopes",
    "build_tool_batch_lifecycle_receipt",
    "build_tool_batch_lifecycle_receipt_from_sources",
    "build_tool_call_lifecycle_receipt",
    "build_tool_call_lifecycle_run_ledger_event",
    "build_tool_dispatch_dropped_anomaly_from_lifecycle_receipt",
    "build_tool_dispatch_dropped_anomaly_from_sources",
    "build_tool_dispatch_dropped_anomaly_projection",
    "build_tool_dispatch_dropped_lifecycle_from_anomaly_flags",
    "build_tool_dispatch_dropped_lifecycle_from_observed_calls",
    "effect_receipts_from_batch_receipts",
    "empty_tool_lifecycle_summary",
    "failure_evidence_from_lifecycle_receipt",
    "merge_tool_lifecycle_summaries",
    "native_tool_call_count_from_facts",
    "native_tool_call_count_from_metadata",
    "native_tool_call_envelope_refs_from_metadata",
    "native_tool_call_facts_from_lifecycle_receipt",
    "native_tool_call_facts_from_metadata",
    "native_tool_call_facts_from_raw_calls",
    "native_tool_call_facts_from_sources",
    "native_tool_call_names_from_facts",
    "normalize_native_tool_call_envelope_refs",
    "normalize_tool_call_lifecycle_receipt",
    "observed_tool_call_names_from_sources",
    "project_completion_audit_evidence_to_metadata",
    "project_completion_dispatch_evidence_to_metadata",
    "project_lifecycle_failure_evidence_to_metadata",
    "project_native_tool_call_envelopes_to_metadata",
    "project_native_tool_call_facts_from_evidence_to_metadata",
    "project_native_tool_call_facts_to_metadata",
    "project_tool_lifecycle_event",
    "project_tool_lifecycle_failure_status",
    "project_tool_lifecycle_metadata",
    "project_tool_lifecycle_receipt_to_metadata",
    "project_tool_lifecycle_summary",
    "summarize_tool_lifecycle_events",
    "task_boundary_tool_dispatch_from_lifecycle_metadata",
    "task_boundary_tool_dispatch_from_lifecycle_receipt",
    "tool_call_lifecycle_receipts_from_metadata",
    "tool_dispatch_dropped_error_message",
    "tool_dispatch_dropped_guard_applies",
]
