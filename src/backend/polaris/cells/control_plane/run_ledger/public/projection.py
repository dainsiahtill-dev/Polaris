"""Pure projection helpers for platform run-ledger read models."""

from __future__ import annotations

import json
from typing import Any

from polaris.cells.control_plane.run_ledger.public.directed_effect_receipt_validation import (
    directed_effect_receipt_payload_hash,
    directed_effect_receipt_v2_errors,
)
from polaris.cells.control_plane.run_ledger.public.task_boundary import (
    normalize_task_boundary_verdict,
)
from polaris.cells.control_plane.run_ledger.public.tool_lifecycle import (
    project_tool_lifecycle_event,
    project_tool_lifecycle_failure_status,
    project_tool_lifecycle_requirement,
    project_tool_lifecycle_summary,
    summarize_tool_lifecycle_events,
)


def _clean_string(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean_string(item).replace("\\", "/").lstrip("/")
        if text and text not in seen:
            output.append(text)
            seen.add(text)
    return output


def _modality_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.replace(";", ",").split(",")]
        return _string_list([item for item in raw_items if item])
    return _string_list(value)


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_dict_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, dict):
                return item
    return {}


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _task_boundary_task_key(verdict: dict[str, Any]) -> str:
    """Return the stable task identity for a task-boundary verdict row."""

    task_id = _clean_string(verdict.get("task_id"))
    if task_id:
        return task_id
    run_id = _clean_string(verdict.get("run_id")) or "unknown"
    turn_id = _clean_string(verdict.get("turn_id")) or "unknown"
    return f"run:{run_id}|turn:{turn_id}"


_TASK_BOUNDARY_BLOCKING_FIELDS = (
    "missing_target_files",
    "missing_entrypoint_targets",
    "unresolved_local_imports",
    "artifact_semantic_mismatches",
    "downstream_pending_artifacts",
    "blocked_dependencies",
    "missing_required_evidence_modalities",
    "failed_required_evidence_modalities",
    "missing_required_verifiers",
    "failed_required_verifiers",
)


def _preserves_completed_task_boundary(
    current: dict[str, Any] | None,
    candidate: dict[str, Any],
) -> bool:
    """Keep proven delivery authoritative across a zero-effect repair attempt.

    ``mutation_bypass_blocked`` means the follow-up turn was rejected before
    any requested mutation was dispatched.  It is useful stagnation evidence,
    but it cannot erase an earlier, ledger-bound ``completed_verified`` fact
    for the same task/run.  Real boundary defects and failed verifier evidence
    still replace the prior verdict and remain fail-closed.
    """

    if not isinstance(current, dict):
        return False
    if not (
        bool(current.get("ok"))
        and _clean_string(current.get("status")).lower() == "completed_verified"
        and _clean_string(current.get("failure_class")).upper() == "PASSED"
    ):
        return False
    if not (
        not bool(candidate.get("ok"))
        and _clean_string(candidate.get("status")).lower() == "deferred_followup_required"
        and _clean_string(candidate.get("failure_class")).upper() == "DEFERRED_FOLLOWUP_REQUIRED"
        and _clean_string(candidate.get("reason")).lower() == "mutation_bypass_blocked"
    ):
        return False
    if _clean_string(current.get("run_id")) != _clean_string(candidate.get("run_id")):
        return False
    if not (
        _clean_string(current.get("append_id"))
        and _clean_string(current.get("content_id"))
        and _string_list(current.get("evidence_refs"))
    ):
        return False
    if any(candidate.get(field_name) for field_name in _TASK_BOUNDARY_BLOCKING_FIELDS):
        return False
    return not (
        _string_list(candidate.get("target_files"))
        or _string_list(candidate.get("completed_artifacts"))
        or _dict_value(candidate.get("tool_dispatch"))
    )


def _merge_evidence_modality(
    modalities: dict[str, dict[str, Any]],
    name: str,
    *,
    present: bool,
    ok: bool,
    detail: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    clean_name = _clean_string(name)
    if not clean_name:
        return
    current = modalities.get(clean_name)
    if current is None:
        modalities[clean_name] = {
            "present": bool(present),
            "ok": bool(ok),
            "detail": detail,
            "metadata": metadata or {},
        }
        return
    current["present"] = bool(current.get("present")) or bool(present)
    current["ok"] = bool(current.get("ok")) and bool(ok)
    if detail:
        current["detail"] = detail
    if metadata:
        existing = current.get("metadata")
        current["metadata"] = {**(existing if isinstance(existing, dict) else {}), **metadata}


def _normalize_declared_modalities(value: Any) -> dict[str, dict[str, Any]]:
    modalities: dict[str, dict[str, Any]] = {}
    entries: list[tuple[str, dict[str, Any]]] = []
    if isinstance(value, dict):
        entries = [(str(name), raw) for name, raw in value.items() if isinstance(raw, dict)]
    elif isinstance(value, list):
        entries = [
            (str(item.get("name") or item.get("modality") or ""), item) for item in value if isinstance(item, dict)
        ]
    else:
        return modalities
    for name, raw in entries:
        nested_metadata = raw.get("metadata")
        metadata = nested_metadata if isinstance(nested_metadata, dict) else {}
        metadata = {
            **metadata,
            **{
                key: raw[key]
                for key in sorted(raw)
                if key not in {"name", "modality", "present", "ok", "detail", "metadata"}
            },
        }
        _merge_evidence_modality(
            modalities,
            str(name),
            present=bool(raw.get("present", True)),
            ok=bool(raw.get("ok")),
            detail=_clean_string(raw.get("detail")),
            metadata=metadata,
        )
    return modalities


def _verifier_entries(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        if any(key in value for key in ("ok", "passed", "name", "modality", "kind", "script")):
            return [value]
        return [item for item in value.values() if isinstance(item, dict)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _required_modalities_from_job_token(job_token: dict[str, Any]) -> list[str]:
    gate_policy = _dict_value(job_token.get("gate_policy"))
    return _modality_list(gate_policy.get("required_evidence_modalities") or gate_policy.get("required_modalities"))


def _enabled_modalities_from_job_token(job_token: dict[str, Any]) -> list[str]:
    gate_policy = _dict_value(job_token.get("gate_policy"))
    return _modality_list(gate_policy.get("enabled_evidence_modalities") or gate_policy.get("enabled_modalities"))


def _required_modalities_status(
    required_modalities: list[str],
    gate_modalities: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str]]:
    missing: list[str] = []
    failed: list[str] = []
    for modality_name in required_modalities:
        modality = gate_modalities.get(modality_name)
        if not isinstance(modality, dict) or not modality.get("present"):
            missing.append(modality_name)
        elif not modality.get("ok"):
            failed.append(modality_name)
    return _string_list(missing), _string_list(failed)


def _missing_required_modalities(
    required_modalities: list[str],
    gate_modalities: dict[str, dict[str, Any]],
) -> list[str]:
    missing, _failed = _required_modalities_status(required_modalities, gate_modalities)
    return missing


def _receipt_entries(value: Any) -> list[dict[str, Any]]:
    """Extract canonical tool effect receipts from nested evidence payloads."""

    if isinstance(value, list):
        receipts: list[dict[str, Any]] = []
        for item in value:
            receipts.extend(_receipt_entries(item))
        return receipts
    if not isinstance(value, dict):
        return []

    direct = value.get("effect_receipt")
    if isinstance(direct, dict):
        receipt = dict(direct)
        commit = value.get("effect_receipt_commit")
        if isinstance(commit, dict):
            receipt["_task_runtime_receipt_commit"] = dict(commit)
        return [receipt]

    nested_result = value.get("result")
    if isinstance(nested_result, dict):
        nested = nested_result.get("effect_receipt")
        if isinstance(nested, dict):
            receipt = dict(nested)
            commit = nested_result.get("effect_receipt_commit")
            if isinstance(commit, dict):
                receipt["_task_runtime_receipt_commit"] = dict(commit)
            return [receipt]

    for key in ("results", "raw_results"):
        nested_results = value.get(key)
        if isinstance(nested_results, list):
            return _receipt_entries(nested_results)

    if "operation" in value and (
        "capability_token" in value or "director_policy" in value or "command" in value or "file" in value
    ):
        return [value]

    return []


def _recovery_entries(value: Any) -> list[dict[str, Any]]:
    """Extract TaskRuntime recovery/dead-letter facts from nested tool evidence."""

    if isinstance(value, list):
        entries: list[dict[str, Any]] = []
        for item in value:
            entries.extend(_recovery_entries(item))
        return entries
    if not isinstance(value, dict):
        return []
    direct = value.get("effect_recovery")
    if isinstance(direct, dict):
        return [dict(direct)]
    nested_result = value.get("result")
    if isinstance(nested_result, dict):
        nested = nested_result.get("effect_recovery")
        if isinstance(nested, dict):
            return [dict(nested)]
    for key in ("results", "raw_results"):
        nested_results = value.get(key)
        if isinstance(nested_results, list):
            return _recovery_entries(nested_results)
    return []


def _mapping_entries(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list | tuple):
        return [dict(item) for item in value if isinstance(item, dict)]
    return []


def _tool_receipts_from_physical_evidence(physical_evidence: dict[str, Any]) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in (
        "effect_receipt",
        "effect_receipts",
        "tool_receipts",
        "write_receipts",
        "command_receipts",
        "batch_receipt",
        "batch_receipts",
        "commands",
    ):
        for receipt in _receipt_entries(physical_evidence.get(key)):
            try:
                identity = json.dumps(
                    receipt,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            except (TypeError, ValueError):
                receipts.append(receipt)
                continue
            if identity in seen:
                continue
            seen.add(identity)
            receipts.append(receipt)
    return receipts


def _directed_effect_recoveries_from_physical_evidence(
    physical_evidence: dict[str, Any],
) -> list[dict[str, Any]]:
    recoveries: list[dict[str, Any]] = []
    for key in (
        "effect_recovery",
        "effect_recoveries",
        "batch_receipt",
        "batch_receipts",
        "commands",
    ):
        recoveries.extend(_recovery_entries(physical_evidence.get(key)))
    return recoveries


def _repair_receipts_from_physical_evidence(physical_evidence: dict[str, Any]) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for key in (
        "repair_receipts",
        "director_repair_receipts",
        "repair_kernel_receipts",
        "deterministic_repair_receipts",
    ):
        receipts.extend(_mapping_entries(physical_evidence.get(key)))
    repair_result = physical_evidence.get("repair_result")
    if isinstance(repair_result, dict):
        receipts.extend(_mapping_entries(repair_result.get("receipts")))
    repair_kernel = physical_evidence.get("repair_kernel")
    if isinstance(repair_kernel, dict):
        receipts.extend(_mapping_entries(repair_kernel.get("receipts")))
    return receipts


def _receipt_authority_policy_from_physical_evidence(physical_evidence: dict[str, Any]) -> dict[str, Any]:
    direct = physical_evidence.get("receipt_authority_policy")
    if isinstance(direct, dict):
        return direct
    repair_result = physical_evidence.get("repair_result")
    if isinstance(repair_result, dict) and isinstance(repair_result.get("receipt_authority_policy"), dict):
        return dict(repair_result["receipt_authority_policy"])
    metadata = repair_result.get("metadata") if isinstance(repair_result, dict) else None
    if isinstance(metadata, dict) and isinstance(metadata.get("receipt_authority_policy"), dict):
        return dict(metadata["receipt_authority_policy"])
    repair_kernel = physical_evidence.get("repair_kernel")
    if isinstance(repair_kernel, dict) and isinstance(repair_kernel.get("receipt_authority_policy"), dict):
        return dict(repair_kernel["receipt_authority_policy"])
    return {}


def _repair_modality(physical_evidence: dict[str, Any]) -> dict[str, Any] | None:
    receipts = _repair_receipts_from_physical_evidence(physical_evidence)
    policy = _receipt_authority_policy_from_physical_evidence(physical_evidence)
    if not receipts and not policy:
        return None

    authoritative_success = (
        bool(policy.get("authoritative_success"))
        if policy
        else bool(receipts)
        and all(
            bool(receipt.get("authoritative"))
            and _clean_string(receipt.get("status")) == "applied"
            and _clean_string(receipt.get("evidence_status")) == "resolved_evidence"
            for receipt in receipts
        )
    )
    missing_evidence_count = (
        _int_value(policy.get("missing_evidence_receipt_count"))
        if policy
        else sum(1 for receipt in receipts if _clean_string(receipt.get("evidence_status")) == "missing_evidence")
    )
    failed_evidence_count = (
        _int_value(policy.get("failed_evidence_receipt_count"))
        if policy
        else sum(1 for receipt in receipts if _clean_string(receipt.get("evidence_status")) == "failed_evidence")
    )
    non_authoritative_count = (
        _int_value(policy.get("non_authoritative_receipt_count"))
        if policy
        else sum(
            1
            for receipt in receipts
            if not bool(receipt.get("authoritative"))
            or _clean_string(receipt.get("status")) != "applied"
            or _clean_string(receipt.get("evidence_status")) != "resolved_evidence"
        )
    )
    blocker = ""
    if missing_evidence_count:
        blocker = "repair_missing_revalidation_evidence"
    elif failed_evidence_count:
        blocker = "repair_failed_revalidation_evidence"
    elif non_authoritative_count:
        blocker = "repair_non_authoritative_receipt"
    detail = (
        f"{len(receipts)} authoritative repair receipt(s)"
        if authoritative_success
        else blocker or "repair receipt authority policy failed"
    )
    return {
        "present": True,
        "ok": authoritative_success,
        "detail": detail,
        "metadata": {
            "receipt_count": len(receipts) or _int_value(policy.get("receipt_count")),
            "authoritative_success": authoritative_success,
            "missing_evidence_receipt_count": missing_evidence_count,
            "failed_evidence_receipt_count": failed_evidence_count,
            "non_authoritative_receipt_count": non_authoritative_count,
            "blocker": blocker,
            "source_tools": _string_list([receipt.get("source_tool") for receipt in receipts]),
            "receipt_ids": _string_list([receipt.get("receipt_id") for receipt in receipts]),
            "policy": policy,
        },
    }


def _task_boundary_modality(physical_evidence: dict[str, Any]) -> dict[str, Any] | None:
    repair = physical_evidence.get("repair")
    repair_map: dict[str, Any] = repair if isinstance(repair, dict) else {}
    plan_probe = repair_map.get("plan_probe_preaudit") or physical_evidence.get("plan_probe_preaudit")
    plan_probe_map: dict[str, Any] = plan_probe if isinstance(plan_probe, dict) else {}
    evidence = repair_map.get("interface_discrepancy_evidence") or physical_evidence.get(
        "interface_discrepancy_evidence"
    )
    evidence_map: dict[str, Any] = evidence if isinstance(evidence, dict) else {}
    receipt_map = _first_dict_value(
        repair_map.get("interface_discrepancy_receipts")
        or physical_evidence.get("interface_discrepancy_receipts")
        or repair_map.get("interface_discrepancy_receipt")
        or physical_evidence.get("interface_discrepancy_receipt")
    )
    if receipt_map:
        evidence_map = {**evidence_map, **receipt_map}
    status = _clean_string(plan_probe_map.get("status") or evidence_map.get("plan_probe_status"))
    reason = _clean_string(evidence_map.get("reason"))
    has_task_boundary_evidence = bool(status or reason or evidence_map)
    if not has_task_boundary_evidence:
        return None

    blocked = (
        status == "coverage_matched_but_unplannable"
        or reason == "coverage_matched_but_unplannable"
        or bool(evidence_map.get("llm_fallback_blocked"))
    )
    director_retry_allowed = bool(evidence_map.get("director_retry_allowed"))
    ok = bool(status in {"already_clean", "covered_plannable"} and not blocked)
    detail = status or reason or "task_boundary_evidence"
    if blocked and not director_retry_allowed:
        detail = "task_boundary_interface_discrepancy_required"
    diagnostic_count = _int_value(
        plan_probe_map.get("covered_unplannable_diagnostic_count")
        or evidence_map.get("covered_unplannable_diagnostic_count")
    )
    if not diagnostic_count and isinstance(evidence_map.get("diagnostics"), (list, tuple)):
        diagnostic_count = len(evidence_map.get("diagnostics") or [])
    interface_delta = evidence_map.get("interface_delta")
    interface_delta_map = interface_delta if isinstance(interface_delta, dict) else {}
    triage_summary = evidence_map.get("triage_summary")
    triage_summary_map = triage_summary if isinstance(triage_summary, dict) else {}
    return {
        "present": True,
        "ok": ok,
        "detail": detail,
        "metadata": {
            "plan_probe_status": status,
            "reason": reason,
            "covered_unplannable_source_tools": _string_list(
                plan_probe_map.get("covered_unplannable_source_tools")
                or evidence_map.get("covered_unplannable_source_tools")
                or evidence_map.get("source_tools")
            ),
            "covered_unplannable_diagnostic_count": diagnostic_count,
            "recommended_owner": _clean_string(evidence_map.get("recommended_owner")),
            "recommended_route": _clean_string(evidence_map.get("recommended_route")),
            "director_retry_allowed": director_retry_allowed,
            "llm_fallback_blocked": bool(evidence_map.get("llm_fallback_blocked")),
            "interface_discrepancy_schema_version": _clean_string(evidence_map.get("schema_version")),
            "interface_delta_available": bool(interface_delta_map),
            "interface_delta": dict(interface_delta_map),
            "triage_summary_available": bool(triage_summary_map),
            "triage_summary": dict(triage_summary_map),
        },
    }


def _environment_prep_receipts_from_physical_evidence(physical_evidence: dict[str, Any]) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for key in (
        "environment_prep_receipts",
        "environment_prep",
        "director_environment_prep_receipts",
    ):
        receipts.extend(_mapping_entries(physical_evidence.get(key)))
    repair_result = physical_evidence.get("repair_result")
    if isinstance(repair_result, dict):
        metadata = repair_result.get("metadata")
        if isinstance(metadata, dict):
            receipts.extend(_mapping_entries(metadata.get("environment_prep_receipts")))
    repair_kernel = physical_evidence.get("repair_kernel")
    if isinstance(repair_kernel, dict):
        receipts.extend(_mapping_entries(repair_kernel.get("environment_prep_receipts")))
    return receipts


def _environment_prep_modality(physical_evidence: dict[str, Any]) -> dict[str, Any] | None:
    receipts = _environment_prep_receipts_from_physical_evidence(physical_evidence)
    if not receipts:
        return None
    failed = [
        receipt for receipt in receipts if _clean_string(receipt.get("status")) not in {"succeeded", "skipped_fresh"}
    ]
    ok = not failed
    return {
        "present": True,
        "ok": ok,
        "detail": f"{len(receipts)} environment prep receipt(s)" if ok else "environment prep failed",
        "metadata": {
            "receipt_count": len(receipts),
            "failed_receipt_count": len(failed),
            "plan_ids": _string_list([receipt.get("plan_id") for receipt in receipts]),
            "ecosystems": _string_list([receipt.get("ecosystem") for receipt in receipts]),
            "manifests": _string_list([receipt.get("manifest") for receipt in receipts]),
            "statuses": _string_list([receipt.get("status") for receipt in receipts]),
            "error_codes": _string_list([receipt.get("error_code") for receipt in failed]),
        },
    }


def _receipt_capability_token(receipt: dict[str, Any]) -> dict[str, Any]:
    direct = receipt.get("capability_token")
    if isinstance(direct, dict):
        return direct
    director_policy = receipt.get("director_policy")
    if isinstance(director_policy, dict):
        nested = director_policy.get("capability_token")
        if isinstance(nested, dict):
            return nested
    return {}


def _is_lower_sha256(value: Any) -> bool:
    text = _clean_string(value)
    return len(text) == 64 and text == text.lower() and all(character in "0123456789abcdef" for character in text)


def _directed_effect_receipt_payload_hash(receipt: dict[str, Any]) -> str | None:
    """Compatibility alias for existing tests and local callers."""

    return directed_effect_receipt_payload_hash(receipt)


def _directed_effect_receipt_errors(receipt: dict[str, Any], *, index: int) -> list[str] | None:
    """Validate the TaskRuntime authority binding for a DEO-3 receipt projection."""

    commit = receipt.get("_task_runtime_receipt_commit")
    typed_commit = commit if isinstance(commit, dict) else None
    errors = directed_effect_receipt_v2_errors(receipt, typed_commit, prefix=f"receipt[{index}]")
    return list(errors) if errors is not None else None


def _directed_effect_recovery_errors(recovery: dict[str, Any], *, index: int) -> list[str]:
    """Validate one durable TaskRuntime recovery/dead-letter projection."""

    prefix = f"recovery[{index}]"
    errors: list[str] = []
    state = _clean_string(recovery.get("state"))
    expected_code = {"RECOVERY_PENDING": "recovery_pending", "DEAD_LETTER": "dead_lettered"}.get(state)
    if _clean_string(recovery.get("schema_version")) != "roles.adapters.directed_effect_recovery_fact.v1":
        errors.append(f"{prefix}:invalid_schema")
    if expected_code is None:
        errors.append(f"{prefix}:invalid_state")
    elif _clean_string(recovery.get("code")) != expected_code:
        errors.append(f"{prefix}:state_code_mismatch")
    for flag in ("authoritative", "durable"):
        if recovery.get(flag) is not True:
            errors.append(f"{prefix}:{flag}_not_true")
    for field in ("event_id", "operation_id"):
        if not _clean_string(recovery.get(field)):
            errors.append(f"{prefix}:missing_{field}")
    if _int_value(recovery.get("version")) <= 0:
        errors.append(f"{prefix}:invalid_version")
    evidence_prefix = "recovery" if state == "RECOVERY_PENDING" else "resolution"
    if not _clean_string(recovery.get(f"{evidence_prefix}_evidence_ref")):
        errors.append(f"{prefix}:missing_{evidence_prefix}_evidence_ref")
    if not _is_lower_sha256(recovery.get(f"{evidence_prefix}_evidence_hash")):
        errors.append(f"{prefix}:invalid_{evidence_prefix}_evidence_hash")
    return errors


def _tool_receipt_modality(
    physical_evidence: dict[str, Any],
    job_token: dict[str, Any],
) -> dict[str, Any] | None:
    receipts = _tool_receipts_from_physical_evidence(physical_evidence)
    recoveries = _directed_effect_recoveries_from_physical_evidence(physical_evidence)
    if not receipts and not recoveries:
        return None
    authoritative_receipts, legacy_receipts, invalid, operations = _classify_tool_receipts(
        receipts,
        job_token=job_token,
    )
    valid_recoveries, recovery_errors = _classify_directed_effect_recoveries(recoveries)
    invalid.extend(recovery_errors)
    failed_directed_effect_receipts = [
        receipt for receipt in authoritative_receipts if _clean_string(receipt.get("receipt_outcome")) == "failed"
    ]
    present = bool(authoritative_receipts or valid_recoveries)
    ok = present and not invalid and not failed_directed_effect_receipts and not valid_recoveries
    task_runtime_event_ids = _string_list(
        [_dict_value(receipt.get("_task_runtime_receipt_commit")).get("event_id") for receipt in authoritative_receipts]
    )
    detail = _tool_receipt_modality_detail(
        present=present,
        invalid=invalid,
        failed_receipt_count=len(failed_directed_effect_receipts),
        recovery_count=len(valid_recoveries),
        receipt_count=len(authoritative_receipts),
    )
    return {
        "present": present,
        "ok": ok,
        "detail": detail,
        "metadata": {
            "receipt_count": len(authoritative_receipts),
            "observed_receipt_count": len(receipts),
            "task_runtime_receipt_count": len(authoritative_receipts),
            "legacy_receipt_count": len(legacy_receipts),
            "failed_receipt_count": len(failed_directed_effect_receipts),
            "recovery_count": len(valid_recoveries),
            "recovery_states": _string_list([item.get("state") for item in valid_recoveries]),
            "recovery_event_ids": _string_list([item.get("event_id") for item in valid_recoveries]),
            "operations": _string_list(operations),
            "invalid": _string_list(invalid),
            "expected_token_id": _clean_string(job_token.get("token_id")),
            "task_runtime_event_ids": task_runtime_event_ids,
        },
    }


def _classify_tool_receipts(
    receipts: list[dict[str, Any]],
    *,
    job_token: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[str]]:
    authoritative: list[dict[str, Any]] = []
    legacy: list[dict[str, Any]] = []
    invalid: list[str] = []
    operations: list[str] = []
    for index, receipt in enumerate(receipts):
        errors = _directed_effect_receipt_errors(receipt, index=index)
        if errors is None:
            operations.append(_clean_string(receipt.get("operation")) or "unknown")
            legacy.append(receipt)
            invalid.extend(_legacy_tool_receipt_errors(receipt, index=index, job_token=job_token))
        else:
            operations.append(_clean_string(receipt.get("normalized_tool_name")) or "unknown")
            if errors:
                invalid.extend(errors)
            else:
                authoritative.append(receipt)
    if legacy and not _clean_string(job_token.get("token_id")):
        invalid.append("job_token:missing_token_id")
    return authoritative, legacy, invalid, operations


def _legacy_tool_receipt_errors(
    receipt: dict[str, Any],
    *,
    index: int,
    job_token: dict[str, Any],
) -> list[str]:
    errors = [f"receipt[{index}]:legacy_receipt_non_authoritative"]
    token = _receipt_capability_token(receipt)
    receipt_token_id = _clean_string(token.get("token_id"))
    if not receipt_token_id:
        errors.append(f"receipt[{index}]:missing_token")
        return errors
    expected_token_id = _clean_string(job_token.get("token_id"))
    if expected_token_id and receipt_token_id != expected_token_id:
        errors.append(f"receipt[{index}]:token_mismatch")
    for field in ("contract_hash", "blueprint_hash"):
        expected = _clean_string(job_token.get(field))
        observed = _clean_string(token.get(field))
        if expected and observed and observed != expected:
            errors.append(f"receipt[{index}]:{field}_mismatch")
    return errors


def _classify_directed_effect_recoveries(
    recoveries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    valid: list[dict[str, Any]] = []
    invalid: list[str] = []
    for index, recovery in enumerate(recoveries):
        errors = _directed_effect_recovery_errors(recovery, index=index)
        if errors:
            invalid.extend(errors)
        else:
            valid.append(recovery)
    return valid, invalid


def _tool_receipt_modality_detail(
    *,
    present: bool,
    invalid: list[str],
    failed_receipt_count: int,
    recovery_count: int,
    receipt_count: int,
) -> str:
    if not present:
        return ", ".join(invalid) or "no authoritative tool receipt"
    if invalid:
        return ", ".join(invalid)
    if failed_receipt_count:
        return f"{failed_receipt_count} committed physical effect receipt(s) failed"
    if recovery_count:
        return f"{recovery_count} unresolved physical effect recovery fact(s)"
    return f"{receipt_count} authoritative tool receipt(s)"


def _evidence_modalities_from_physical_evidence(physical_evidence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Derive canonical evidence modalities from one physical evidence payload."""

    modalities = _normalize_declared_modalities(physical_evidence.get("modalities"))
    requirements = physical_evidence.get("requirements")
    requirement_map: dict[str, Any] = requirements if isinstance(requirements, dict) else {}
    code_requirement_names = (
        "artifact_landed",
        "source_files_present",
        "declared_source_targets_present",
        "scaffolding_present",
    )
    code_requirements = [
        item for name in code_requirement_names if isinstance((item := requirement_map.get(name)), dict)
    ]
    if code_requirements:
        _merge_evidence_modality(
            modalities,
            "code",
            present=True,
            ok=all(bool(item.get("ok")) for item in code_requirements),
            detail=", ".join(name for name in code_requirement_names if name in requirement_map),
        )
    repair_modality = _repair_modality(physical_evidence)
    if repair_modality is not None:
        _merge_evidence_modality(
            modalities,
            "repair",
            present=bool(repair_modality.get("present")),
            ok=bool(repair_modality.get("ok")),
            detail=_clean_string(repair_modality.get("detail")),
            metadata=_dict_value(repair_modality.get("metadata")),
        )
    task_boundary_modality = _task_boundary_modality(physical_evidence)
    if task_boundary_modality is not None:
        _merge_evidence_modality(
            modalities,
            "task_boundary",
            present=bool(task_boundary_modality.get("present")),
            ok=bool(task_boundary_modality.get("ok")),
            detail=_clean_string(task_boundary_modality.get("detail")),
            metadata=_dict_value(task_boundary_modality.get("metadata")),
        )
    environment_prep_modality = _environment_prep_modality(physical_evidence)
    if environment_prep_modality is not None:
        _merge_evidence_modality(
            modalities,
            "environment_prep",
            present=bool(environment_prep_modality.get("present")),
            ok=bool(environment_prep_modality.get("ok")),
            detail=_clean_string(environment_prep_modality.get("detail")),
            metadata=_dict_value(environment_prep_modality.get("metadata")),
        )
    entrypoint = physical_evidence.get("entrypoint")
    entrypoint_map: dict[str, Any] = entrypoint if isinstance(entrypoint, dict) else {}
    entrypoint_kind = _clean_string(entrypoint_map.get("kind"))
    entrypoint_is_command = bool(entrypoint_kind) and entrypoint_kind not in {"web_static", "web_playwright"}

    build_requirement = requirement_map.get("build_test_lint_ran")
    commands = physical_evidence.get("commands")
    command_count = int(physical_evidence.get("command_count") or 0)
    if isinstance(build_requirement, dict) or command_count > 0 or isinstance(commands, list) or entrypoint_is_command:
        if isinstance(build_requirement, dict):
            command_ok = bool(build_requirement.get("ok"))
            command_detail = _clean_string(build_requirement.get("detail"))
        else:
            sampled_commands = commands if isinstance(commands, list) else []
            command_ok = bool(sampled_commands) and all(
                bool(item.get("ok") if "ok" in item else item.get("passed"))
                for item in sampled_commands
                if isinstance(item, dict)
            )
            command_detail = f"{command_count or len(sampled_commands)} command evidence item(s)"
        command_count_for_metadata = command_count
        if entrypoint_is_command:
            entrypoint_ok = bool(entrypoint_map.get("ok"))
            entrypoint_detail = _clean_string(
                entrypoint_map.get("detail") or entrypoint_map.get("stderr_tail") or entrypoint_kind
            )
            command_ok = bool(command_ok) and entrypoint_ok if command_detail else entrypoint_ok
            command_count_for_metadata += 1
            if not entrypoint_ok:
                command_detail = "; ".join(
                    item
                    for item in (
                        command_detail,
                        f"entrypoint_smoke failed: {entrypoint_detail}"
                        if entrypoint_detail
                        else "entrypoint_smoke failed",
                    )
                    if item
                )
        _merge_evidence_modality(
            modalities,
            "command",
            present=True,
            ok=command_ok,
            detail=command_detail,
            metadata={
                "command_count": command_count_for_metadata,
                "entrypoint_kind": entrypoint_kind,
            },
        )
    verifier_payloads = (
        physical_evidence.get("verifier_results"),
        physical_evidence.get("custom_verifiers"),
        physical_evidence.get("domain_verifiers"),
        physical_evidence.get("script_verifiers"),
        physical_evidence.get("user_verifiers"),
        physical_evidence.get("qa_verifiers"),
    )
    for verifier in [entry for payload in verifier_payloads for entry in _verifier_entries(payload)]:
        verifier_ok = bool(verifier.get("ok") or verifier.get("passed"))
        verifier_name = _clean_string(verifier.get("name") or verifier.get("script") or verifier.get("id"))
        verifier_modality = _clean_string(verifier.get("modality") or verifier.get("kind") or "domain")
        verifier_detail = _clean_string(verifier.get("detail") or verifier.get("reason") or verifier_name)
        verifier_metadata = {
            "id": _clean_string(verifier.get("id")),
            "name": verifier_name,
            "script": _clean_string(verifier.get("script")),
            "hash": _clean_string(verifier.get("hash") or verifier.get("evidence_hash")),
            "exit_code": verifier.get("exit_code"),
            "metric": verifier.get("metric"),
            "threshold": verifier.get("threshold"),
        }
        _merge_evidence_modality(
            modalities,
            "verifier",
            present=True,
            ok=verifier_ok,
            detail=verifier_detail,
            metadata=verifier_metadata,
        )
        _merge_evidence_modality(
            modalities,
            verifier_modality,
            present=True,
            ok=verifier_ok,
            detail=verifier_detail,
            metadata=verifier_metadata,
        )
    if entrypoint_kind in {"web_static", "web_playwright"}:
        _merge_evidence_modality(
            modalities,
            "browser",
            present=True,
            ok=bool(entrypoint_map.get("ok")),
            detail=_clean_string(entrypoint_map.get("detail")),
            metadata={
                "kind": entrypoint_kind,
                "url": _clean_string(entrypoint_map.get("url")),
                "http_status": entrypoint_map.get("http_status"),
            },
        )
    visual_signal_present = bool(
        entrypoint_map.get("has_canvas")
        or "canvas_non_blank" in entrypoint_map
        or "canvas_screenshot_non_blank" in entrypoint_map
        or entrypoint_map.get("screenshot_hash")
        or entrypoint_map.get("screenshot_path")
    )
    if entrypoint_kind == "web_playwright" or visual_signal_present:
        visual_ok = bool(
            entrypoint_map.get("canvas_non_blank")
            or entrypoint_map.get("canvas_screenshot_non_blank")
            or entrypoint_map.get("screenshot_hash")
            or entrypoint_map.get("screenshot_path")
        )
        _merge_evidence_modality(
            modalities,
            "visual",
            present=visual_signal_present,
            ok=visual_ok,
            detail=_clean_string(entrypoint_map.get("detail")),
            metadata={
                "has_canvas": bool(entrypoint_map.get("has_canvas")),
                "canvas_non_blank": bool(entrypoint_map.get("canvas_non_blank")),
                "screenshot_hash": _clean_string(entrypoint_map.get("screenshot_hash")),
                "screenshot_path": _clean_string(entrypoint_map.get("screenshot_path")),
            },
        )
    judgement = (
        physical_evidence.get("llm_judge")
        or physical_evidence.get("visual_judgement")
        or physical_evidence.get("qa_visual_judgement")
    )
    if isinstance(judgement, dict):
        _merge_evidence_modality(
            modalities,
            "llm_judge",
            present=True,
            ok=bool(judgement.get("ok") or judgement.get("passed")),
            detail=_clean_string(judgement.get("detail") or judgement.get("reason")),
            metadata={
                "provider": _clean_string(judgement.get("provider")),
                "model": _clean_string(judgement.get("model")),
                "confidence": judgement.get("confidence"),
            },
        )
    return modalities


GateRevisionKey = tuple[str, str, str, str, str, str, str]


def _gate_revision_key(event: dict[str, Any]) -> GateRevisionKey | None:
    """Return an explicit stable obligation identity, never a scope guess.

    Legacy events without a first-class obligation and subject remain independent
    immutable gates. Target paths are authorization scope, not identity: sibling
    tasks may legitimately share them.
    """

    raw_gate = event.get("gate")
    gate = raw_gate if isinstance(raw_gate, dict) else {}
    raw_token = event.get("job_token")
    token = raw_token if isinstance(raw_token, dict) else {}
    factory_run_id = _clean_string(token.get("factory_run_id"))
    project_id = _clean_string(token.get("project_id"))
    authority_id = factory_run_id or _clean_string(token.get("run_id")) or _clean_string(token.get("token_id"))
    obligation_id = _clean_string(event.get("gate_obligation_id") or gate.get("obligation_id"))
    subject_kind = _clean_string(event.get("gate_subject_kind") or gate.get("subject_kind"))
    subject_id = _clean_string(event.get("gate_subject_id") or gate.get("subject_id"))
    if not obligation_id or not subject_kind or not subject_id:
        return None
    return (
        authority_id,
        project_id,
        _clean_string(event.get("stage") or token.get("stage")),
        _clean_string(gate.get("name")) or "unknown",
        obligation_id,
        subject_kind,
        subject_id,
    )


def _latest_task_boundary_epochs(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Project the latest canonical delivery epoch for each task.

    QA verdicts judge one concrete Director delivery.  They remain immutable
    history after a same-task Director retry, but they must not authorize or
    block a newer TaskBoundary for that task.
    """

    latest_by_task: dict[str, dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        boundary_raw = event.get("task_boundary_verdict")
        if event.get("event_type") != "task_boundary_verdict" and not isinstance(boundary_raw, dict):
            continue
        verdict = normalize_task_boundary_verdict(boundary_raw if isinstance(boundary_raw, dict) else event)
        for key in ("task_id", "run_id", "turn_id"):
            if not _clean_string(verdict.get(key)):
                verdict[key] = _clean_string(event.get(key))
        task_key = _task_boundary_task_key(verdict)
        if _preserves_completed_task_boundary(latest_by_task.get(task_key), verdict):
            continue
        latest_by_task[task_key] = verdict
    return latest_by_task


def _effective_gate_event_indexes(events: list[dict[str, Any]]) -> tuple[frozenset[int], tuple[str, ...]]:
    """Select only explicitly chained revisions; keep legacy events independent."""

    effective_indexes = {
        index
        for index, event in enumerate(events)
        if isinstance(event, dict) and event.get("event_type") == "gate_evaluated"
    }
    latest_by_obligation: dict[GateRevisionKey, tuple[int, int, str]] = {}
    issues: list[str] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("event_type") != "gate_evaluated":
            continue
        key = _gate_revision_key(event)
        if key is None:
            continue
        raw_gate = event.get("gate")
        gate = raw_gate if isinstance(raw_gate, dict) else {}
        try:
            revision = int(event.get("gate_revision") or gate.get("revision") or 0)
        except (TypeError, ValueError):
            revision = 0
        supersedes = _clean_string(event.get("supersedes_content_id") or gate.get("supersedes_content_id"))
        content_id = _clean_string(event.get("content_id") or event.get("event_id"))
        previous = latest_by_obligation.get(key)
        if not content_id or revision < 1:
            issues.append(f"invalid_gate_revision_metadata:{index}")
            continue
        if previous is None:
            if revision != 1 or supersedes:
                issues.append(f"gate_revision_chain_missing_parent:{index}")
                continue
        else:
            previous_index, previous_revision, previous_content_id = previous
            if revision != previous_revision + 1 or supersedes != previous_content_id:
                issues.append(f"gate_revision_chain_fork_or_stale:{index}")
                continue
            effective_indexes.discard(previous_index)
        latest_by_obligation[key] = (index, revision, content_id)

    # QA authority is delivery-epoch scoped.  Keep historical verdict facts in
    # ``gates`` but exclude a verdict when the same task now has a newer
    # canonical TaskBoundary run.  Exact IDs only: missing legacy identity is
    # preserved rather than guessed.
    latest_boundaries = _latest_task_boundary_epochs(events)
    for index in tuple(effective_indexes):
        event = events[index]
        raw_gate = event.get("gate")
        gate = raw_gate if isinstance(raw_gate, dict) else {}
        gate_name = _clean_string(gate.get("name")).lower()
        if gate_name not in {"qa_verdict", "tool_receipt"}:
            continue
        raw_physical = event.get("physical_evidence")
        physical = raw_physical if isinstance(raw_physical, dict) else {}
        physical_metadata = _dict_value(physical.get("metadata"))
        raw_token = event.get("job_token")
        token = raw_token if isinstance(raw_token, dict) else {}
        task_id = _clean_string(
            event.get("task_id") or physical.get("task_id") or token.get("task_id") or physical_metadata.get("task_id")
        )
        verdict_run_id = _clean_string(event.get("run_id") or physical.get("run_id") or token.get("run_id"))
        latest_boundary = latest_boundaries.get(task_id)
        latest_run_id = _clean_string((latest_boundary or {}).get("run_id"))
        if (
            gate_name == "qa_verdict"
            and task_id
            and verdict_run_id
            and latest_run_id
            and verdict_run_id != latest_run_id
        ):
            effective_indexes.discard(index)
            continue
        # Tool-batch failures are attempt facts, not durable delivery verdicts.
        # Once the same immutable task owns a canonical completed_verified
        # boundary, retain failed receipts in append-only history but remove
        # them from current outcome authority. Successful receipts stay
        # effective as physical-effect evidence. This prevents a repaired task
        # from remaining permanently failed without hiding an unresolved task.
        if (
            gate_name == "tool_receipt"
            and not bool(gate.get("ok"))
            and task_id
            and bool((latest_boundary or {}).get("ok"))
            and _clean_string((latest_boundary or {}).get("status")).lower() == "completed_verified"
        ):
            effective_indexes.discard(index)
    return frozenset(effective_indexes), tuple(issues)


def _required_modalities_by_gate_obligation(
    events: list[dict[str, Any]],
) -> dict[GateRevisionKey, list[str]]:
    """Preserve the strongest required-evidence contract across revisions.

    A repaired gate may supersede an earlier outcome, but it must not erase an
    evidence obligation by publishing a narrower retry token.  Required
    modalities therefore accumulate for the stable obligation while observed
    evidence and the pass/fail verdict still come from the latest revision.
    """

    required_by_obligation: dict[GateRevisionKey, list[str]] = {}
    for event in events:
        if not isinstance(event, dict) or event.get("event_type") != "gate_evaluated":
            continue
        raw_token = event.get("job_token")
        token = raw_token if isinstance(raw_token, dict) else {}
        key = _gate_revision_key(event)
        if key is None:
            continue
        required_by_obligation[key] = _string_list(
            [
                *required_by_obligation.get(key, []),
                *_required_modalities_from_job_token(token),
            ]
        )
    return required_by_obligation


def build_run_ledger_projection(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the canonical read model for ledger-backed UI/QA projections."""

    gates: list[dict[str, Any]] = []
    capability_issues: list[str] = []
    job_token_ids: list[str] = []
    latest_token: dict[str, Any] = {}
    execution_capability_by_task: dict[str, dict[str, Any]] = {}
    command_count_total = 0
    sampled_command_count = 0
    truncated_command_events = 0
    evidence_modalities: dict[str, dict[str, Any]] = {}
    enabled_modalities: list[str] = []
    required_modalities: list[str] = []
    missing_required_modalities: list[str] = []
    failed_required_modalities: list[str] = []
    tool_receipt_count = 0
    tool_receipt_tools: list[str] = []
    tool_receipt_hash_deltas: list[dict[str, Any]] = []
    task_boundary_verdicts: list[dict[str, Any]] = []
    tool_lifecycle_events: list[dict[str, Any]] = []
    tool_lifecycle_requirement_events: list[dict[str, Any]] = []
    effective_gate_event_indexes, gate_revision_issues = _effective_gate_event_indexes(events)
    latest_task_boundaries = _latest_task_boundary_epochs(events)
    required_modalities_by_obligation = _required_modalities_by_gate_obligation(events)
    for event_index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        event_type = event.get("event_type")
        if event_type == "tool_call_lifecycle":
            lifecycle_raw = event.get("tool_call_lifecycle_receipt") or event.get("tool_call_lifecycle")
            lifecycle_input = dict(lifecycle_raw) if isinstance(lifecycle_raw, dict) else dict(event)
            for key in ("task_id", "run_id", "turn_id"):
                if not _clean_string(lifecycle_input.get(key)):
                    lifecycle_input[key] = _clean_string(event.get(key))
            lifecycle_event = project_tool_lifecycle_event(
                lifecycle_input,
                append_id=event.get("append_id"),
                content_id=event.get("content_id") or event.get("event_id"),
            )
            tool_lifecycle_events.append(lifecycle_event)
            continue
        if event_type == "tool_lifecycle_requirement":
            requirement_raw = event.get("tool_lifecycle_requirement")
            if isinstance(requirement_raw, dict):
                tool_lifecycle_requirement_events.append(dict(requirement_raw))
            continue
        task_boundary_raw = event.get("task_boundary_verdict")
        if event_type == "task_boundary_verdict" or isinstance(task_boundary_raw, dict):
            verdict = normalize_task_boundary_verdict(
                task_boundary_raw if isinstance(task_boundary_raw, dict) else event
            )
            for key in ("task_id", "run_id", "turn_id"):
                if not _clean_string(verdict.get(key)):
                    verdict[key] = _clean_string(event.get(key))
            verdict.setdefault("append_id", _clean_string(event.get("append_id")))
            verdict.setdefault("content_id", _clean_string(event.get("content_id") or event.get("event_id")))
            verdict["task_key"] = _task_boundary_task_key(verdict)
            task_boundary_verdicts.append(verdict)
            if event_type == "task_boundary_verdict":
                continue
        if event_type == "tool_receipt":
            tool_receipt_count += 1
            receipt_tool = _clean_string(event.get("tool"))
            if receipt_tool:
                tool_receipt_tools.append(receipt_tool)
            delta = event.get("file_hash_delta")
            if isinstance(delta, dict):
                tool_receipt_hash_deltas.append(delta)
            continue
        if event_type != "gate_evaluated":
            continue
        raw_gate = event.get("gate")
        gate: dict[str, Any] = raw_gate if isinstance(raw_gate, dict) else {}
        gate_is_effective = event_index in effective_gate_event_indexes
        raw_physical_evidence = event.get("physical_evidence")
        physical_evidence: dict[str, Any] = raw_physical_evidence if isinstance(raw_physical_evidence, dict) else {}
        raw_job_token = event.get("job_token")
        job_token: dict[str, Any] = raw_job_token if isinstance(raw_job_token, dict) else {}
        raw_capability_audit = job_token.get("capability_audit")
        capability_audit: dict[str, Any] = raw_capability_audit if isinstance(raw_capability_audit, dict) else {}
        issues = capability_audit.get("issues")
        if gate_is_effective and isinstance(issues, list):
            capability_issues.extend(str(item) for item in issues if str(item))
        token_id = _clean_string(job_token.get("token_id"))
        if token_id:
            job_token_ids.append(token_id)
        if gate_is_effective:
            latest_token = job_token
        physical_metadata = _dict_value(physical_evidence.get("metadata"))
        task_id = _clean_string(job_token.get("task_id") or physical_metadata.get("task_id"))
        token_stage = _clean_string(job_token.get("stage") or event.get("stage"))
        latest_task_boundary = latest_task_boundaries.get(task_id)
        task_delivery_verified = bool(
            latest_task_boundary
            and latest_task_boundary.get("ok") is True
            and _clean_string(latest_task_boundary.get("status")).lower() == "completed_verified"
        )
        # Outcome authority and execution-capability authority are distinct.
        # A failed tool batch becomes historical after a canonical
        # completed_verified boundary, but its immutable task-local JobToken
        # is still the authority required to seal receipts for the files that
        # did land.  Dropping both facts made execution_broker unable to record
        # ProjectArtifactReceiptV1 and broke downstream sibling-export repair.
        if (gate_is_effective or task_delivery_verified) and task_id and token_stage == "pending_exec":
            execution_capability_by_task[task_id] = {
                "ok": bool(capability_audit.get("ok")) and not bool(issues),
                "issues": list(issues) if isinstance(issues, list) else [],
                "latest_token_id": token_id,
                "latest_contract_hash": _clean_string(job_token.get("contract_hash")),
                "latest_blueprint_hash": _clean_string(job_token.get("blueprint_hash")),
                # Freeze the capability epoch as it existed when this task's
                # Director gate settled.  Earlier prerequisite tokens remain
                # part of the original artifact authority; later QA/workspace
                # tokens must not retroactively mutate it.
                "job_token_ids": list(dict.fromkeys(job_token_ids)),
                "stage": token_stage,
                "task_id": task_id,
            }
        command_count_total += int(physical_evidence.get("command_count") or 0)
        sampled_command_count += int(physical_evidence.get("sampled_command_count") or 0)
        if physical_evidence.get("commands_truncated"):
            truncated_command_events += 1
        gate_modalities = _evidence_modalities_from_physical_evidence(physical_evidence)
        tool_receipt_modality = _tool_receipt_modality(physical_evidence, job_token)
        if tool_receipt_modality is not None:
            _merge_evidence_modality(
                gate_modalities,
                "tool_receipt",
                present=bool(tool_receipt_modality.get("present")),
                ok=bool(tool_receipt_modality.get("ok")),
                detail=_clean_string(tool_receipt_modality.get("detail")),
                metadata=_dict_value(tool_receipt_modality.get("metadata")),
            )
        gate_enabled_modalities = _enabled_modalities_from_job_token(job_token)
        declared_gate_required_modalities = _required_modalities_from_job_token(job_token)
        gate_revision_key = _gate_revision_key(event)
        gate_required_modalities = (
            required_modalities_by_obligation.get(gate_revision_key, declared_gate_required_modalities)
            if gate_is_effective and gate_revision_key is not None
            else declared_gate_required_modalities
        )
        if gate_is_effective:
            enabled_modalities.extend(gate_enabled_modalities)
            required_modalities.extend(gate_required_modalities)
        gate_missing_required_modalities, gate_failed_required_modalities = _required_modalities_status(
            gate_required_modalities,
            gate_modalities,
        )
        gate_task_id = _clean_string(
            event.get("task_id")
            or physical_evidence.get("task_id")
            or job_token.get("task_id")
            or physical_metadata.get("task_id")
        )
        gate_run_id = _clean_string(event.get("run_id") or physical_evidence.get("run_id") or job_token.get("run_id"))
        if gate_is_effective:
            missing_required_modalities.extend(gate_missing_required_modalities)
            failed_required_modalities.extend(gate_failed_required_modalities)
            for modality_name, modality in gate_modalities.items():
                summary = evidence_modalities.setdefault(
                    modality_name,
                    {"total": 0, "present": 0, "ok": 0, "failed": 0, "latest_detail": ""},
                )
                summary["total"] = int(summary["total"]) + 1
                if modality.get("present"):
                    summary["present"] = int(summary["present"]) + 1
                if modality.get("ok"):
                    summary["ok"] = int(summary["ok"]) + 1
                else:
                    summary["failed"] = int(summary["failed"]) + 1
                detail = _clean_string(modality.get("detail"))
                if detail:
                    summary["latest_detail"] = detail
        gates.append(
            {
                "name": _clean_string(gate.get("name")) or "unknown",
                "stage": _clean_string(event.get("stage")),
                "task_id": gate_task_id,
                "run_id": gate_run_id,
                "ok": bool(gate.get("ok")),
                "summary": _clean_string(gate.get("summary")),
                "content_id": _clean_string(event.get("content_id") or event.get("event_id")),
                "append_id": _clean_string(event.get("append_id")),
                "job_token_id": token_id,
                "capability_ok": bool(capability_audit.get("ok")),
                "capability_issues": list(issues) if isinstance(issues, list) else [],
                "evidence_modalities": gate_modalities,
                "enabled_evidence_modalities": gate_enabled_modalities,
                "required_evidence_modalities": gate_required_modalities,
                "missing_required_evidence_modalities": gate_missing_required_modalities,
                "failed_required_evidence_modalities": gate_failed_required_modalities,
                "effective": gate_is_effective,
                "gate_obligation_id": _clean_string(event.get("gate_obligation_id") or gate.get("obligation_id")),
                "gate_subject_kind": _clean_string(event.get("gate_subject_kind") or gate.get("subject_kind")),
                "gate_subject_id": _clean_string(event.get("gate_subject_id") or gate.get("subject_id")),
                "gate_revision": _int_value(event.get("gate_revision") or gate.get("revision")),
                "supersedes_content_id": _clean_string(
                    event.get("supersedes_content_id") or gate.get("supersedes_content_id")
                ),
            }
        )
    effective_gates = [gate for gate in gates if gate["effective"]]
    failed_gates = [gate for gate in effective_gates if not gate["ok"]]
    historical_failed_gate_count = sum(not gate["ok"] for gate in gates)
    capability_ok = (
        bool(effective_gates) and not capability_issues and all(gate["capability_ok"] for gate in effective_gates)
    )
    enabled_modalities = _string_list(enabled_modalities)
    required_modalities = _string_list(required_modalities)
    missing_required_modalities = _string_list(missing_required_modalities)
    failed_required_modalities = _string_list(failed_required_modalities)
    evidence_policy_integrity_ok = bool(effective_gates) and not missing_required_modalities
    evidence_policy_outcome_ok = bool(effective_gates) and not failed_required_modalities
    evidence_policy_ok = evidence_policy_integrity_ok and evidence_policy_outcome_ok
    latest_task_boundary_by_task: dict[str, dict[str, Any]] = {}
    suppressed_non_mutating_deferred_count = 0
    for verdict in task_boundary_verdicts:
        task_key = _task_boundary_task_key(verdict)
        if _preserves_completed_task_boundary(latest_task_boundary_by_task.get(task_key), verdict):
            suppressed_non_mutating_deferred_count += 1
            continue
        latest_task_boundary_by_task.pop(task_key, None)
        latest_task_boundary_by_task[task_key] = dict(verdict)
    latest_task_boundary = {}
    if task_boundary_verdicts:
        latest_task_boundary = dict(
            latest_task_boundary_by_task.get(_task_boundary_task_key(task_boundary_verdicts[-1]), {})
        )
    historical_failed_task_boundary_count = sum(not bool(verdict.get("ok")) for verdict in task_boundary_verdicts)
    failed_task_boundaries = [
        verdict for verdict in latest_task_boundary_by_task.values() if not bool(verdict.get("ok"))
    ]
    task_boundary_ok = not failed_task_boundaries
    tool_lifecycle_requirement = project_tool_lifecycle_requirement(
        tool_lifecycle_requirement_events,
        tool_lifecycle_events,
    )
    tool_lifecycle_summary = summarize_tool_lifecycle_events(
        tool_lifecycle_events,
        requirement_projection=tool_lifecycle_requirement,
    )
    tool_lifecycle_projection = project_tool_lifecycle_summary(tool_lifecycle_summary)
    tool_lifecycle_ok = bool(tool_lifecycle_projection.get("ok"))
    if not tool_lifecycle_ok:
        # M08: respect failure_status.failed. A recoverable per-tool execution
        # failure (TOOL_RESULT_FAILED — tool ran, returned ok=False) is a product-
        # quality defect, not a control-plane integrity break; tool_lifecycle_ok
        # (and thus integrity_ok / canonical_execution) stays green. Only
        # MISSING/DROPPED/missing-effect/INCOMPLETE evidence breaks integrity.
        _tl_failure_status = project_tool_lifecycle_failure_status(tool_lifecycle_summary)
        if not _tl_failure_status.get("failed"):
            tool_lifecycle_ok = True
    gate_revision_integrity_ok = not gate_revision_issues
    integrity_ok = (
        bool(effective_gates)
        and capability_ok
        and evidence_policy_integrity_ok
        and tool_lifecycle_ok
        and gate_revision_integrity_ok
    )
    outcome_ok = bool(effective_gates) and not failed_gates and not failed_required_modalities and task_boundary_ok
    projection_ok = integrity_ok and outcome_ok
    return {
        "schema_version": 1,
        "source": "run_ledger",
        "ok": projection_ok,
        "integrity_ok": integrity_ok,
        "outcome_ok": outcome_ok,
        "event_count": len(events),
        "gate_count": len(gates),
        "effective_gate_count": len(effective_gates),
        "historical_failed_gate_count": historical_failed_gate_count,
        "missing": ([] if gates else ["gate_events"]) + missing_required_modalities,
        "gates": gates,
        "effective_gates": effective_gates,
        "failed_gates": failed_gates,
        "gate_revisions": {
            "integrity_ok": gate_revision_integrity_ok,
            "issues": list(gate_revision_issues),
        },
        "capability": {
            "ok": capability_ok,
            "issues": sorted(set(capability_issues)),
            "latest_token_id": _clean_string(latest_token.get("token_id")) if latest_token else "",
            "latest_contract_hash": _clean_string(latest_token.get("contract_hash")) if latest_token else "",
            "latest_blueprint_hash": _clean_string(latest_token.get("blueprint_hash")) if latest_token else "",
            "job_token_ids": list(dict.fromkeys(job_token_ids)),
        },
        # Artifact execution authority is task-local.  Global capability also
        # contains later workspace/QA tokens; using that aggregate for an
        # artifact receipt changes its authority revision after QA and makes a
        # byte-current Director delivery invisible on the next local retry.
        "execution_capability_by_task": dict(sorted(execution_capability_by_task.items())),
        "physical_evidence": {
            "command_count": command_count_total,
            "sampled_command_count": sampled_command_count,
            "truncated_command_events": truncated_command_events,
        },
        "evidence_modalities": dict(sorted(evidence_modalities.items())),
        "evidence_policy": {
            "ok": evidence_policy_ok,
            "integrity_ok": evidence_policy_integrity_ok,
            "outcome_ok": evidence_policy_outcome_ok,
            "enabled_modalities": enabled_modalities,
            "required_modalities": required_modalities,
            "missing_required_modalities": missing_required_modalities,
            "failed_required_modalities": failed_required_modalities,
        },
        "tool_receipts": {
            "count": tool_receipt_count,
            "tools": list(dict.fromkeys(tool_receipt_tools)),
            "hash_deltas": tool_receipt_hash_deltas,
        },
        "tool_lifecycle": tool_lifecycle_projection,
        "task_boundary": {
            "ok": task_boundary_ok,
            "verdict_count": len(task_boundary_verdicts),
            "historical_failed_count": historical_failed_task_boundary_count,
            "suppressed_non_mutating_deferred_count": suppressed_non_mutating_deferred_count,
            "latest": latest_task_boundary,
            "latest_by_task": latest_task_boundary_by_task,
            "failed": failed_task_boundaries,
        },
    }


def summarize_run_ledger_projection(value: Any) -> dict[str, Any]:
    """Return the control-plane integrity status for a ledger projection."""

    if not isinstance(value, dict):
        return {
            "ok": False,
            "detail": "run ledger projection missing",
            "missing": ["run_ledger_projection"],
        }
    if value.get("source") != "run_ledger":
        return {
            "ok": False,
            "detail": "run ledger projection source mismatch",
            "missing": ["source"],
        }
    if int(value.get("gate_count") or 0) <= 0:
        return {
            "ok": False,
            "detail": "run ledger projection has no gate events",
            "missing": ["gate_events"],
        }
    capability = value.get("capability")
    capability_map = capability if isinstance(capability, dict) else {}
    if not bool(capability_map.get("ok")):
        issues = capability_map.get("issues")
        issue_list = [str(item) for item in issues] if isinstance(issues, list) else ["capability"]
        return {
            "ok": False,
            "detail": "run ledger projection capability invalid: " + ", ".join(issue_list),
            "missing": issue_list,
            "capability": capability_map,
        }
    tool_lifecycle = value.get("tool_lifecycle")
    tool_lifecycle_map = tool_lifecycle if isinstance(tool_lifecycle, dict) else {}
    if tool_lifecycle_map and not bool(tool_lifecycle_map.get("ok", True)):
        failure_status = project_tool_lifecycle_failure_status(tool_lifecycle_map)
        failure_evidence_raw = tool_lifecycle_map.get("failure_evidence")
        failure_evidence = (
            [dict(item) for item in failure_evidence_raw if isinstance(item, dict)]
            if isinstance(failure_evidence_raw, list)
            else []
        )
        failure = _clean_string(failure_status.get("failure_class")) or "TOOL_LIFECYCLE_FAILED"
        # M08 (caller side): respect the failure_status.failed verdict. A recoverable
        # per-tool execution failure (TOOL_RESULT_FAILED — tool ran, returned ok=False)
        # is a product-quality defect caught by real_run_gate/delivery_depth, NOT a
        # control-plane integrity break; project ok:True so canonical_execution stays
        # green. Only MISSING/DROPPED/missing-effect/INCOMPLETE evidence (failed:True)
        # breaks integrity. (L1-01 r27 still died on TOOL_RESULT_FAILED because this
        # caller checked the summary's raw ``ok`` flag instead of the failed verdict.)
        if not failure_status.get("failed"):
            return {
                "ok": True,
                "detail": "run ledger projection tool lifecycle recoverable: " + failure,
                "missing": [],
                "failed_control_plane_events": [],
                "failure_evidence": failure_evidence,
                "capability": capability_map,
                "tool_lifecycle": tool_lifecycle_map,
            }
        return {
            "ok": False,
            "detail": "run ledger projection tool lifecycle failed: " + failure,
            "missing": [],
            "failed_control_plane_events": [failure],
            "failure_evidence": failure_evidence,
            "capability": capability_map,
            "tool_lifecycle": tool_lifecycle_map,
        }
    task_boundary = value.get("task_boundary")
    task_boundary_map = task_boundary if isinstance(task_boundary, dict) else {}
    if task_boundary_map and not bool(task_boundary_map.get("ok", True)):
        failed = task_boundary_map.get("failed")
        failed_rows = [dict(item) for item in failed if isinstance(item, dict)] if isinstance(failed, list) else []
        latest = failed_rows[-1] if failed_rows else task_boundary_map.get("latest")
        latest_map = normalize_task_boundary_verdict(latest if isinstance(latest, dict) else {})
        failure = str(latest_map.get("failure_class") or "TASK_BOUNDARY_FAILED")
        return {
            "ok": False,
            "detail": "run ledger projection task boundary failed: " + failure,
            "missing": [],
            "failed_control_plane_events": [failure],
            "capability": capability_map,
            "task_boundary": task_boundary_map,
        }
    evidence_policy = value.get("evidence_policy")
    evidence_policy_map = evidence_policy if isinstance(evidence_policy, dict) else {}
    missing = evidence_policy_map.get("missing_required_modalities") if evidence_policy_map else []
    missing_list = [str(item) for item in missing] if isinstance(missing, list) else []
    if evidence_policy_map and missing_list:
        return {
            "ok": False,
            "detail": "run ledger projection missing required evidence: " + ", ".join(missing_list),
            "missing": missing_list,
            "failed_control_plane_events": [],
            "capability": capability_map,
            "evidence_policy": evidence_policy_map,
        }
    failed_gates = value.get("failed_gates")
    failed_gate_count = len(failed_gates) if isinstance(failed_gates, list) else 0
    failed_required = evidence_policy_map.get("failed_required_modalities") if evidence_policy_map else []
    failed_required_list = [str(item) for item in failed_required] if isinstance(failed_required, list) else []
    if failed_required_list:
        return {
            "ok": False,
            "detail": "run ledger projection required evidence failed: " + ", ".join(failed_required_list),
            "missing": [],
            "failed_required_modalities": failed_required_list,
            "failed_control_plane_events": [],
            "outcome_ok": bool(value.get("outcome_ok")),
            "failed_gate_count": failed_gate_count,
            "capability": capability_map,
            "evidence_policy": evidence_policy_map,
        }
    if not bool(value.get("ok")):
        return {
            "ok": False,
            "detail": f"run ledger projection has {failed_gate_count} failed gate(s)",
            "missing": ["failed_gates"] if failed_gate_count else ["projection_ok"],
            "failed_control_plane_events": ["failed_gates"] if failed_gate_count else ["projection_ok"],
            "outcome_ok": bool(value.get("outcome_ok")),
            "failed_gate_count": failed_gate_count,
            "capability": capability_map,
            "evidence_policy": evidence_policy_map,
        }
    return {
        "ok": True,
        "detail": f"run ledger projection ready ({int(value.get('gate_count') or 0)} gate event(s))",
        "missing": [],
        "failed_control_plane_events": [],
        "outcome_ok": bool(value.get("outcome_ok")),
        "failed_gate_count": failed_gate_count,
        "capability": capability_map,
        "evidence_policy": evidence_policy_map,
    }


__all__ = [
    "build_run_ledger_projection",
    "summarize_run_ledger_projection",
]
