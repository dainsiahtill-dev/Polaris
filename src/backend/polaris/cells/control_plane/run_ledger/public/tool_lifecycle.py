"""Tool-call lifecycle receipt contracts for Run Ledger projections."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from polaris.cells.control_plane.run_ledger.public.failure_evidence import (
    FailureClassV1,
    normalize_failure_class,
)
from polaris.kernelone.tools.tool_kinds import is_write_tool_name


def _stable_json(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _clean_string(value: Any) -> str:
    return str(value or "").strip()


def _int_value(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


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


def _native_tool_call_envelope_refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


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
    for receipt in receipts:
        refs.append(
            {
                "batch_id": _clean_string(receipt.get("batch_id")),
                "receipt_hash": _stable_hash(receipt),
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
) -> ToolCallLifecycleReceiptV1:
    receipt_rows = [dict(item) for item in receipts or [] if isinstance(item, dict)]
    native_envelope_refs = _native_tool_call_envelope_refs(native_tool_call_envelopes)
    batch_refs = _batch_receipt_refs(receipt_rows)
    effect_refs = _effect_receipt_refs(receipt_rows)
    missing_write_effects = _successful_write_results_without_effect_receipts(receipt_rows)
    result_count = len(_result_items(receipt_rows))
    native_count = len(native_envelope_refs) if native_envelope_refs else _int_value(native_tool_calls_count)
    decoded_count = _int_value(decoded_tool_calls_count)
    dispatched_count = _int_value(dispatched_tool_calls_count)
    status = _clean_string(dispatch_status)
    failure = normalize_failure_class(failure_class)
    dropped: list[dict[str, Any]] = _dropped_tool_call_refs(dropped_tool_calls)

    if native_count > 0 and dispatched_count <= 0:
        status = status or "dropped"
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
    )


def normalize_tool_call_lifecycle_receipt(value: Any) -> dict[str, Any]:
    """Return a safe tool lifecycle receipt mapping."""

    if isinstance(value, ToolCallLifecycleReceiptV1):
        return value.to_dict()
    payload = _mapping(value)
    if payload:
        payload.setdefault("schema_version", "tool_call_lifecycle_receipt.v1")
        native_refs = _native_tool_call_envelope_refs(
            payload.get("native_tool_call_envelope_refs") or payload.get("native_tool_call_envelopes")
        )
        native_count = len(native_refs) if native_refs else _int_value(payload.get("native_tool_calls_count"))
        dispatched_count = _int_value(payload.get("dispatched_tool_calls_count"))
        payload["native_tool_call_envelope_refs"] = native_refs
        payload["native_tool_calls_count"] = native_count
        payload["decoded_tool_calls_count"] = _int_value(payload.get("decoded_tool_calls_count"))
        payload["dispatched_tool_calls_count"] = dispatched_count
        payload["tool_result_count"] = _int_value(payload.get("tool_result_count"))
        payload["effect_receipt_count"] = _int_value(payload.get("effect_receipt_count"))
        dropped_tool_calls = _dropped_tool_call_refs(payload.get("dropped_tool_calls"))
        if native_count > 0 and dispatched_count <= 0 and not dropped_tool_calls:
            dropped_tool_calls = _dropped_tool_calls_from_native_envelopes(native_refs)
        payload["dropped_tool_calls"] = dropped_tool_calls
        if native_count > 0 and dispatched_count <= 0:
            if not _clean_string(payload.get("dispatch_status")):
                payload["dispatch_status"] = "dropped"
            if not normalize_failure_class(payload.get("failure_class")):
                payload["failure_class"] = FailureClassV1.TOOL_DISPATCH_DROPPED.value
        payload.setdefault("ok", False)
        payload.setdefault("dispatch_status", "unknown")
        payload.setdefault("failure_class", FailureClassV1.TOOL_LIFECYCLE_UNKNOWN.value)
        return payload
    return {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "ok": False,
        "dispatch_status": "unknown",
        "failure_class": FailureClassV1.TOOL_LIFECYCLE_MISSING.value,
    }


__all__ = [
    "ToolCallLifecycleReceiptV1",
    "build_tool_call_lifecycle_receipt",
    "normalize_tool_call_lifecycle_receipt",
]
