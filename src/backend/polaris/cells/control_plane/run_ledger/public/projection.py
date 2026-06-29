"""Pure projection helpers for platform run-ledger read models."""

from __future__ import annotations

from typing import Any


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
                bool(item.get("ok")) for item in sampled_commands if isinstance(item, dict)
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
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("event_type")
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
    integrity_ok = bool(gates) and capability_ok and evidence_policy_integrity_ok
    outcome_ok = bool(gates) and not failed_gates and not failed_required_modalities
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
    evidence_policy = value.get("evidence_policy")
    evidence_policy_map = evidence_policy if isinstance(evidence_policy, dict) else {}
    missing = evidence_policy_map.get("missing_required_modalities") if evidence_policy_map else []
    missing_list = [str(item) for item in missing] if isinstance(missing, list) else []
    if evidence_policy_map and missing_list:
        return {
            "ok": False,
            "detail": "run ledger projection missing required evidence: " + ", ".join(missing_list),
            "missing": missing_list,
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
            "outcome_ok": bool(value.get("outcome_ok")),
            "failed_gate_count": failed_gate_count,
            "capability": capability_map,
            "evidence_policy": evidence_policy_map,
        }
    return {
        "ok": True,
        "detail": f"run ledger projection ready ({int(value.get('gate_count') or 0)} gate event(s))",
        "missing": [],
        "outcome_ok": bool(value.get("outcome_ok")),
        "failed_gate_count": failed_gate_count,
        "capability": capability_map,
        "evidence_policy": evidence_policy_map,
    }


__all__ = [
    "build_run_ledger_projection",
    "summarize_run_ledger_projection",
]
