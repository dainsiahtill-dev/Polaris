"""Pure projection helpers for platform run-ledger read models."""

from __future__ import annotations

from typing import Any

from polaris.cells.control_plane.run_ledger.public.failure_evidence import (
    FailureClassV1,
    normalize_failure_class,
)
from polaris.cells.control_plane.run_ledger.public.task_boundary import (
    normalize_task_boundary_verdict,
)
from polaris.cells.control_plane.run_ledger.public.tool_lifecycle import (
    project_tool_lifecycle_event,
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
        return [direct]

    nested_result = value.get("result")
    if isinstance(nested_result, dict):
        nested = nested_result.get("effect_receipt")
        if isinstance(nested, dict):
            return [nested]

    for key in ("results", "raw_results"):
        nested_results = value.get(key)
        if isinstance(nested_results, list):
            return _receipt_entries(nested_results)

    if "operation" in value and (
        "capability_token" in value or "director_policy" in value or "command" in value or "file" in value
    ):
        return [value]

    return []


def _mapping_entries(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list | tuple):
        return [dict(item) for item in value if isinstance(item, dict)]
    return []


def _tool_receipts_from_physical_evidence(physical_evidence: dict[str, Any]) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
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
        receipts.extend(_receipt_entries(physical_evidence.get(key)))
    return receipts


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

    authoritative_success = bool(policy.get("authoritative_success")) if policy else bool(receipts) and all(
        bool(receipt.get("authoritative"))
        and _clean_string(receipt.get("status")) == "applied"
        and _clean_string(receipt.get("evidence_status")) == "resolved_evidence"
        for receipt in receipts
    )
    missing_evidence_count = _int_value(policy.get("missing_evidence_receipt_count")) if policy else sum(
        1 for receipt in receipts if _clean_string(receipt.get("evidence_status")) == "missing_evidence"
    )
    failed_evidence_count = _int_value(policy.get("failed_evidence_receipt_count")) if policy else sum(
        1 for receipt in receipts if _clean_string(receipt.get("evidence_status")) == "failed_evidence"
    )
    non_authoritative_count = _int_value(policy.get("non_authoritative_receipt_count")) if policy else sum(
        1
        for receipt in receipts
        if not bool(receipt.get("authoritative"))
        or _clean_string(receipt.get("status")) != "applied"
        or _clean_string(receipt.get("evidence_status")) != "resolved_evidence"
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
        receipt
        for receipt in receipts
        if _clean_string(receipt.get("status")) not in {"succeeded", "skipped_fresh"}
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


def _tool_receipt_modality(
    physical_evidence: dict[str, Any],
    job_token: dict[str, Any],
) -> dict[str, Any] | None:
    receipts = _tool_receipts_from_physical_evidence(physical_evidence)
    if not receipts:
        return None

    expected_token_id = _clean_string(job_token.get("token_id"))
    expected_contract_hash = _clean_string(job_token.get("contract_hash"))
    expected_blueprint_hash = _clean_string(job_token.get("blueprint_hash"))
    invalid: list[str] = []
    operations: list[str] = []
    for index, receipt in enumerate(receipts):
        operations.append(_clean_string(receipt.get("operation")) or "unknown")
        token = _receipt_capability_token(receipt)
        receipt_token_id = _clean_string(token.get("token_id"))
        if not receipt_token_id:
            invalid.append(f"receipt[{index}]:missing_token")
            continue
        if expected_token_id and receipt_token_id != expected_token_id:
            invalid.append(f"receipt[{index}]:token_mismatch")
        receipt_contract_hash = _clean_string(token.get("contract_hash"))
        if expected_contract_hash and receipt_contract_hash and receipt_contract_hash != expected_contract_hash:
            invalid.append(f"receipt[{index}]:contract_hash_mismatch")
        receipt_blueprint_hash = _clean_string(token.get("blueprint_hash"))
        if expected_blueprint_hash and receipt_blueprint_hash and receipt_blueprint_hash != expected_blueprint_hash:
            invalid.append(f"receipt[{index}]:blueprint_hash_mismatch")

    if not expected_token_id:
        invalid.append("job_token:missing_token_id")

    ok = not invalid
    return {
        "present": True,
        "ok": ok,
        "detail": f"{len(receipts)} token-scoped tool receipt(s)" if ok else ", ".join(invalid),
        "metadata": {
            "receipt_count": len(receipts),
            "operations": _string_list(operations),
            "invalid": _string_list(invalid),
            "expected_token_id": expected_token_id,
        },
    }


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


def build_run_ledger_projection(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the canonical read model for ledger-backed UI/QA projections."""

    gates: list[dict[str, Any]] = []
    capability_issues: list[str] = []
    job_token_ids: list[str] = []
    latest_token: dict[str, Any] = {}
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
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("event_type")
        if event_type == "tool_call_lifecycle":
            lifecycle_raw = event.get("tool_call_lifecycle_receipt") or event.get("tool_call_lifecycle")
            lifecycle_event = project_tool_lifecycle_event(
                lifecycle_raw if isinstance(lifecycle_raw, dict) else event,
                append_id=event.get("append_id"),
                content_id=event.get("content_id") or event.get("event_id"),
            )
            tool_lifecycle_events.append(lifecycle_event)
            continue
        task_boundary_raw = event.get("task_boundary_verdict")
        if event_type == "task_boundary_verdict" or isinstance(task_boundary_raw, dict):
            verdict = normalize_task_boundary_verdict(
                task_boundary_raw if isinstance(task_boundary_raw, dict) else event
            )
            verdict.setdefault("append_id", _clean_string(event.get("append_id")))
            verdict.setdefault("content_id", _clean_string(event.get("content_id") or event.get("event_id")))
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
        raw_physical_evidence = event.get("physical_evidence")
        physical_evidence: dict[str, Any] = raw_physical_evidence if isinstance(raw_physical_evidence, dict) else {}
        raw_job_token = event.get("job_token")
        job_token: dict[str, Any] = raw_job_token if isinstance(raw_job_token, dict) else {}
        raw_capability_audit = job_token.get("capability_audit")
        capability_audit: dict[str, Any] = raw_capability_audit if isinstance(raw_capability_audit, dict) else {}
        issues = capability_audit.get("issues")
        if isinstance(issues, list):
            capability_issues.extend(str(item) for item in issues if str(item))
        token_id = _clean_string(job_token.get("token_id"))
        if token_id:
            job_token_ids.append(token_id)
            latest_token = job_token
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
        gate_required_modalities = _required_modalities_from_job_token(job_token)
        enabled_modalities.extend(gate_enabled_modalities)
        required_modalities.extend(gate_required_modalities)
        gate_missing_required_modalities, gate_failed_required_modalities = _required_modalities_status(
            gate_required_modalities,
            gate_modalities,
        )
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
            }
        )
    failed_gates = [gate for gate in gates if not gate["ok"]]
    capability_ok = bool(gates) and not capability_issues and all(gate["capability_ok"] for gate in gates)
    enabled_modalities = _string_list(enabled_modalities)
    required_modalities = _string_list(required_modalities)
    missing_required_modalities = _string_list(missing_required_modalities)
    failed_required_modalities = _string_list(failed_required_modalities)
    evidence_policy_integrity_ok = bool(gates) and not missing_required_modalities
    evidence_policy_outcome_ok = bool(gates) and not failed_required_modalities
    evidence_policy_ok = evidence_policy_integrity_ok and evidence_policy_outcome_ok
    latest_task_boundary = task_boundary_verdicts[-1] if task_boundary_verdicts else {}
    failed_task_boundaries = [verdict for verdict in task_boundary_verdicts if not bool(verdict.get("ok"))]
    task_boundary_ok = not failed_task_boundaries
    tool_lifecycle_summary = summarize_tool_lifecycle_events(tool_lifecycle_events)
    tool_lifecycle_ok = bool(tool_lifecycle_summary.get("ok"))
    integrity_ok = bool(gates) and capability_ok and evidence_policy_integrity_ok and tool_lifecycle_ok
    outcome_ok = bool(gates) and not failed_gates and not failed_required_modalities and task_boundary_ok
    projection_ok = integrity_ok and outcome_ok
    return {
        "schema_version": 1,
        "source": "run_ledger",
        "ok": projection_ok,
        "integrity_ok": integrity_ok,
        "outcome_ok": outcome_ok,
        "event_count": len(events),
        "gate_count": len(gates),
        "missing": ([] if gates else ["gate_events"]) + missing_required_modalities,
        "gates": gates,
        "failed_gates": failed_gates,
        "capability": {
            "ok": capability_ok,
            "issues": sorted(set(capability_issues)),
            "latest_token_id": _clean_string(latest_token.get("token_id")) if latest_token else "",
            "latest_contract_hash": _clean_string(latest_token.get("contract_hash")) if latest_token else "",
            "latest_blueprint_hash": _clean_string(latest_token.get("blueprint_hash")) if latest_token else "",
            "job_token_ids": list(dict.fromkeys(job_token_ids)),
        },
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
        "tool_lifecycle": {
            "ok": tool_lifecycle_ok,
            "event_count": _int_value(tool_lifecycle_summary.get("event_count")),
            "native_tool_calls_count": _int_value(tool_lifecycle_summary.get("native_tool_calls_count")),
            "decoded_tool_calls_count": _int_value(tool_lifecycle_summary.get("decoded_tool_calls_count")),
            "dispatched_tool_calls_count": _int_value(tool_lifecycle_summary.get("dispatched_tool_calls_count")),
            "tool_result_count": _int_value(tool_lifecycle_summary.get("tool_result_count")),
            "effect_receipt_count": _int_value(tool_lifecycle_summary.get("effect_receipt_count")),
            "native_tool_call_names": _string_list(tool_lifecycle_summary.get("native_tool_call_names")),
            "dropped_count": _int_value(tool_lifecycle_summary.get("dropped_count")),
            "failed_count": _int_value(tool_lifecycle_summary.get("failed_count")),
            "failure_evidence": list(tool_lifecycle_summary.get("failure_evidence") or []),
            "events": list(tool_lifecycle_summary.get("events") or []),
        },
        "task_boundary": {
            "ok": task_boundary_ok,
            "verdict_count": len(task_boundary_verdicts),
            "latest": latest_task_boundary,
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
        events = tool_lifecycle_map.get("events")
        event_rows = events if isinstance(events, list) else []
        failed_events = [item for item in event_rows if isinstance(item, dict) and bool(item.get("failed"))]
        failure_evidence_raw = tool_lifecycle_map.get("failure_evidence")
        failure_evidence = [
            dict(item)
            for item in failure_evidence_raw
            if isinstance(item, dict)
        ] if isinstance(failure_evidence_raw, list) else []
        failure = normalize_failure_class(
            failed_events[-1].get("failure_class") if failed_events else "",
            default=FailureClassV1.TOOL_LIFECYCLE_FAILED,
        )
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
        latest = task_boundary_map.get("latest")
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
