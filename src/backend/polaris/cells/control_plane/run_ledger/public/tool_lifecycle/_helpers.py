"""Private helpers and constants for tool-call lifecycle receipts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from polaris.cells.control_plane.run_ledger.public.directed_effect_receipt_validation import (
    directed_effect_receipt_v2_errors,
)
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


def _effect_receipt_and_commit_from_result(item: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    direct = item.get("effect_receipt")
    if isinstance(direct, dict):
        commit = item.get("effect_receipt_commit")
        if isinstance(commit, dict):
            return dict(direct), dict(commit)
        # Historical/current ToolBatchRuntime rows copied the authoritative
        # receipt to the result envelope while leaving its TaskRuntime commit
        # inside the nested physical result.  Consume that preserved commit
        # only when the nested receipt is byte-for-byte the same mapping; a
        # mismatched nested pair must never authorize the direct receipt.
        result = item.get("result")
        if isinstance(result, dict):
            nested = result.get("effect_receipt")
            nested_commit = result.get("effect_receipt_commit")
            if isinstance(nested, dict) and nested == direct and isinstance(nested_commit, dict):
                return dict(direct), dict(nested_commit)
        return dict(direct), {}
    result = item.get("result")
    if isinstance(result, dict):
        nested = result.get("effect_receipt")
        if isinstance(nested, dict):
            commit = result.get("effect_receipt_commit")
            return dict(nested), dict(commit) if isinstance(commit, dict) else {}
    return {}, {}


def _canonical_sha256(value: Any) -> str:
    token = _clean_string(value)
    return token if len(token) == 64 and all(character in "0123456789abcdef" for character in token) else ""


def _effect_receipt_from_result(item: dict[str, Any]) -> dict[str, Any]:
    """Prefer TaskRuntime-committed DEO-3 receipts; keep legacy receipts readable."""

    effect, commit = _effect_receipt_and_commit_from_result(item)
    if not effect:
        return {}
    if _clean_string(effect.get("schema_version")) != "roles.adapters.director_physical_effect_receipt.v2":
        return effect

    if directed_effect_receipt_v2_errors(effect, commit, prefix="receipt"):
        return {}
    return {**effect, "task_runtime_receipt_commit": commit}


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
        commit = effect.get("task_runtime_receipt_commit")
        commit_map = commit if isinstance(commit, dict) else {}
        declared_receipt_hash = _canonical_sha256(effect.get("receipt_hash"))
        authoritative_v2 = (
            _clean_string(effect.get("schema_version")) == "roles.adapters.director_physical_effect_receipt.v2"
        )
        refs.append(
            {
                "receipt_hash": declared_receipt_hash or receipt_hash,
                "receipt_ref": _clean_string(commit_map.get("receipt_ref")),
                "projection_hash": receipt_hash,
                "operation": _clean_string(
                    effect.get("normalized_tool_name") if authoritative_v2 else effect.get("operation")
                ),
                "file": "" if authoritative_v2 else _clean_string(effect.get("file") or effect.get("path")),
                "tool_name": _clean_string(item.get("tool_name")),
                "call_id": _clean_string(item.get("call_id")),
                "before_hash": "" if authoritative_v2 else _clean_string(effect.get("before_hash")),
                "after_hash": "" if authoritative_v2 else _clean_string(effect.get("after_hash")),
                "receipt_outcome": _clean_string(effect.get("receipt_outcome")),
                "task_runtime_code": _clean_string(commit_map.get("code")),
                "task_runtime_state": _clean_string(commit_map.get("state")),
                "task_runtime_event_id": _clean_string(commit_map.get("event_id")),
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


def _first_tool_result_failure_reason(receipts: list[dict[str, Any]]) -> str:
    """Return the first concrete non-success tool result error/abort reason.

    Used when lifecycle failure_class is set but the caller left ``reason`` empty
    so failure_evidence does not collapse to the bare dispatch_status string
    (live L1-01 r156: reason=\"dispatched\").
    """

    for item in _result_items(receipts):
        status = _clean_string(item.get("status")).lower()
        if status in {"", "success", "pending", "ok"}:
            continue
        for key in ("error", "reason", "message"):
            candidate = _clean_string(item.get(key))
            if candidate:
                return candidate
        nested = item.get("result")
        if isinstance(nested, Mapping):
            for key in ("error", "error_type", "reason", "message", "code"):
                candidate = _clean_string(nested.get(key))
                if candidate:
                    return candidate
        if status in {"error", "timeout", "aborted", "cancelled", "failed"}:
            return status
    return ""


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


_TOOL_LIFECYCLE_OUTCOME_SCHEMA_VERSION = "polaris.tool_lifecycle_outcome_projection.v1"

_TOOL_LIFECYCLE_REQUIREMENT_SCHEMA_VERSION = "polaris.tool_lifecycle_requirement.v1"

_TOOL_LIFECYCLE_REQUIREMENT_SATISFIED = "satisfied"

_TOOL_LIFECYCLE_REQUIREMENT_NOT_REQUIRED = "not_required"

_TOOL_LIFECYCLE_REQUIREMENT_MISSING = "missing_required"


def _tool_lifecycle_is_required(value: Mapping[str, Any]) -> bool:
    """Return an explicitly declared lifecycle requirement."""

    return bool(value.get("requirement", False))


def _tool_lifecycle_requirement_status(*, requirement: bool, evidence_present: bool) -> str:
    """Classify lifecycle evidence against its explicit requirement."""

    if evidence_present:
        return _TOOL_LIFECYCLE_REQUIREMENT_SATISFIED
    if requirement:
        return _TOOL_LIFECYCLE_REQUIREMENT_MISSING
    return _TOOL_LIFECYCLE_REQUIREMENT_NOT_REQUIRED


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


def _is_terminal_incomplete_materialization_seal(event: Mapping[str, Any]) -> bool:
    """Return True for R137 blocked seals that close a claimed-without-tools gap.

    These receipts satisfy the lifecycle *requirement* (evidence is present) so
    they must not project as ``TOOL_LIFECYCLE_MISSING``. They remain attributable
    via :func:`project_tool_lifecycle_failure_status` / latest_by_task rows.
    """

    receipt = _mapping(event.get("receipt")) if isinstance(event.get("receipt"), Mapping) else {}
    dispatch_status = _normalize_dispatch_status(event.get("dispatch_status") or receipt.get("dispatch_status"))
    if dispatch_status != "blocked":
        return False
    failure_class = normalize_failure_class(event.get("failure_class") or receipt.get("failure_class"))
    if failure_class not in {
        FailureClassV1.INCOMPLETE_MATERIALIZATION.value,
        FailureClassV1.NO_MATERIALIZED_EFFECT.value,
    }:
        return False
    reason = _clean_string(event.get("reason") or receipt.get("reason")).lower()
    return any(
        token in reason
        for token in (
            "claimed_materialization_without_tool_lifecycle",
            "director_no_materialized_changes",
            "closed_without_tools",
            "incomplete_materialization",
            "multi_task_incomplete_without_tools",
            "director_stage_incomplete_without_tools",
        )
    )


def _lifecycle_event_is_unresolved(event: Mapping[str, Any]) -> bool:
    """Return whether a canonical lifecycle row remains unresolved.

    Terminal incomplete-materialization seals (R137/R177) are requirement
    evidence, not open gaps: they clear missing_required_task_keys for integrity
    while failure attribution stays on the seal row itself.
    """

    if _is_terminal_incomplete_materialization_seal(event):
        return False
    return bool(event.get("dropped")) or bool(event.get("failed")) or event.get("ok") is False


def _lifecycle_outcome_projection_from_events(
    events: Sequence[Mapping[str, Any]],
    *,
    source: str,
    degraded: bool,
    fallback: str = "",
    requirement: bool = False,
    required_task_keys: Sequence[str] = (),
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
        task_key: dict(event) for task_key, event in latest_by_task.items() if _lifecycle_event_is_unresolved(event)
    }
    normalized_required_task_keys = _string_list(required_task_keys)
    effective_requirement = bool(requirement or normalized_required_task_keys or latest_by_task)
    missing_required_task_keys = [
        task_key for task_key in normalized_required_task_keys if task_key not in latest_by_task
    ]
    requirement_status = _tool_lifecycle_requirement_status(
        requirement=effective_requirement,
        evidence_present=bool(latest_by_task) and not missing_required_task_keys,
    )
    return {
        "ok": (
            not unresolved_by_task
            and not missing_required_task_keys
            and requirement_status != _TOOL_LIFECYCLE_REQUIREMENT_MISSING
        ),
        "requirement": effective_requirement,
        "requirement_status": requirement_status,
        "required_task_keys": normalized_required_task_keys,
        "missing_required_task_keys": missing_required_task_keys,
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
            "requirement": effective_requirement,
            "requirement_status": requirement_status,
            "required_task_keys": normalized_required_task_keys,
            "missing_required_task_keys": missing_required_task_keys,
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
    for task_key, event in latest_by_task.items():
        event.setdefault("task_key", task_key)
    declared_requirement = (
        summary.get("requirement") if "requirement" in summary else metadata.get("requirement")
    ) is True
    required_task_keys = _string_list(
        summary.get("required_task_keys") if "required_task_keys" in summary else metadata.get("required_task_keys")
    )
    return _lifecycle_outcome_projection_from_events(
        tuple(latest_by_task.values()),
        source=_clean_string(metadata.get("source")) or "canonical",
        degraded=bool(metadata.get("degraded")),
        fallback=_clean_string(metadata.get("fallback")),
        requirement=declared_requirement,
        required_task_keys=required_task_keys,
    )


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
        requirement=_tool_lifecycle_is_required(summary),
    )
    if not events:
        return event_projection
    historical_failed = _int_value(summary.get("dropped_count")) > 0 or _int_value(summary.get("failed_count")) > 0
    if event_projection["unresolved_count"] or (not historical_failed and bool(summary.get("ok", True))):
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


_RECOVERABLE_TOOL_RESULT_FAILED_ADMISSION_REASONS = (
    "deo_claim_failed",
    "deo_parent_admission_failed",
    "deo_director_policy_denied",
    "deo_inventory_ready_failed",
    "deo_inventory_seal_failed",
    "parent_open_conflict",
)


def _tool_result_failed_is_recoverable_admission(event: Mapping[str, Any]) -> bool:
    """A dropped TOOL_RESULT_FAILED whose reason is a recoverable admission/policy
    denial (claim race, parent-open conflict, policy denial, inventory/seal) is a
    retryable per-tool denial, NOT a genuine dispatch-drop integrity break. The
    tool returned a failure (ok=False) the model can correct by re-issuing; the
    product gates catch any real defect. (L1-01 m03-r29 deepseek-Director:
    parent_open_conflict admission races were dropped+TOOL_RESULT_FAILED and broke
    canonical despite deepseek materializing 12 files cleanly.)
    """
    reason = str(event.get("reason") or "").lower()
    return any(token in reason for token in _RECOVERABLE_TOOL_RESULT_FAILED_ADMISSION_REASONS)


_COMPLETION_DISPATCH_EVIDENCE_KEYS: tuple[str, ...] = (
    "final_request_context_audit",
    "context_snapshot_ref",
    "context_snapshot_degraded",
    "context_snapshot_degraded_reason",
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
