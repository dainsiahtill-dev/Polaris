"""Run ledger primitives for factory pipeline evidence.

This module is the small control-plane foundation for moving factory runs from
role-local claims toward one append-only evidence stream. It deliberately keeps
effects explicit: callers create a ``JobToken``/event and then choose whether
to persist it through ``RunLedger``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from polaris.cells.control_plane.run_ledger.public.contracts import AppendRunLedgerEventCommandV1
from polaris.cells.control_plane.run_ledger.public.job_token import JobToken
from polaris.cells.control_plane.run_ledger.public.ledger import (
    RunLedger as PlatformRunLedger,
    stable_hash,
)
from polaris.cells.control_plane.run_ledger.public.projection import (
    build_run_ledger_projection as _build_platform_run_ledger_projection,
    summarize_run_ledger_projection as _summarize_platform_run_ledger_projection,
)
from polaris.cells.control_plane.run_ledger.public.service import append_run_ledger_event
from polaris.cells.control_plane.verifier_policy.public import (
    ReadVerifierPolicyQueryV1,
    read_verifier_policy,
    verifier_policy_to_gate_policy,
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


def _string_items(value: Any) -> list[str]:
    if isinstance(value, str):
        text = _clean_string(value)
        return [text] if text else []
    return _string_list(value)


def _modality_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.replace(";", ",").split(",")]
        return _string_list([item for item in raw_items if item])
    return _string_list(value)


def _lineage_entries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    output: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            output.append({str(key): item[key] for key in sorted(item)})
            continue
        text = _clean_string(item)
        if text:
            output.append({"ref": text})
    return output


def _receipt_payload(value: Any) -> dict[str, Any] | list[dict[str, Any]] | None:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (list, tuple)):
        items = [dict(item) for item in value if isinstance(item, dict)]
        return items if items else None
    return None


def _attach_tool_receipt_evidence(physical_evidence: dict[str, Any], gate: dict[str, Any]) -> None:
    for key in (
        "effect_receipt",
        "effect_receipts",
        "tool_receipts",
        "write_receipts",
        "command_receipts",
        "batch_receipt",
        "batch_receipts",
    ):
        payload = _receipt_payload(gate.get(key))
        if payload is not None:
            physical_evidence[key] = payload


def _event_content_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in event.items() if key not in {"append_id", "content_id", "event_id", "recorded_at"}
    }


class RunLedger(PlatformRunLedger):
    """Compatibility wrapper for factory callers using the platform ledger."""


def _safe_token(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value or "").strip())
    return cleaned.strip("-") or "unknown"


def _path_is_allowed(path: str, allowed_paths: list[str]) -> bool:
    normalized = _clean_string(path).replace("\\", "/").strip("/")
    if not normalized:
        return False
    for allowed in allowed_paths:
        scope = _clean_string(allowed).replace("\\", "/").strip("/")
        if not scope:
            continue
        if normalized == scope or normalized.startswith(scope.rstrip("/") + "/"):
            return True
    return False


def _repair_targets_from_lineage(repair_lineage: list[dict[str, Any]]) -> list[str]:
    targets: list[str] = []
    for entry in repair_lineage:
        targets.extend(_string_list(entry.get("target_files")))
        targets.extend(_string_list(entry.get("changed_files")))
    return _string_list(targets)


def _job_token_from_ledger_meta(value: dict[str, Any]) -> dict[str, Any]:
    direct = value.get("job_token")
    if isinstance(direct, dict):
        return direct
    ledger_event = value.get("ledger_event")
    if isinstance(ledger_event, dict):
        nested = ledger_event.get("job_token")
        if isinstance(nested, dict):
            return nested
    return {}


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


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _record_policy_dict(record: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = record.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _record_policy_dicts(record: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    return [value for key in keys if isinstance((value := record.get(key)), dict)]


def _record_bool(record: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = record.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def _required_evidence_modalities_from_record(
    record: dict[str, Any],
    *,
    stage: str,
) -> list[str]:
    required: list[str] = []

    def extend(value: Any) -> None:
        required.extend(_modality_list(value))

    if stage == "real_run_gate":
        required.extend(["code", "command"])
    if _record_bool(record, "requires_browser_evidence", "browser_required", "qa_browser_required"):
        required.append("browser")
    if _record_bool(record, "requires_visual_evidence", "visual_required", "qa_visual_required"):
        required.append("visual")

    gate_policy = _record_policy_dict(record, "gate_policy", "quality_gate_policy")
    verifier_policies = _record_policy_dicts(
        record,
        "control_plane_verifier_policy",
        "verifier_policy",
        "qa_verifier_policy",
        "domain_verifier_policy",
    )
    extend(record.get("required_evidence_modalities"))
    extend(gate_policy.get("required_evidence_modalities") or gate_policy.get("required_modalities"))
    for verifier_policy in verifier_policies:
        extend(verifier_policy.get("required_evidence_modalities") or verifier_policy.get("required_modalities"))

    verifier_specs = record.get("required_verifiers")
    if verifier_specs is None:
        verifier_specs = next(
            (policy.get("required_verifiers") for policy in verifier_policies if policy.get("required_verifiers")),
            None,
        )
    for verifier in _verifier_entries(verifier_specs):
        required.append("verifier")
        required.append(_clean_string(verifier.get("modality") or verifier.get("kind") or "domain"))

    return _string_list(required)


def _enabled_evidence_modalities_from_record(record: dict[str, Any]) -> list[str]:
    enabled: list[str] = []

    def extend(value: Any) -> None:
        enabled.extend(_modality_list(value))

    gate_policy = _record_policy_dict(record, "gate_policy", "quality_gate_policy")
    verifier_policies = _record_policy_dicts(
        record,
        "control_plane_verifier_policy",
        "verifier_policy",
        "qa_verifier_policy",
        "domain_verifier_policy",
    )
    extend(record.get("enabled_evidence_modalities"))
    extend(gate_policy.get("enabled_evidence_modalities") or gate_policy.get("enabled_modalities"))
    for verifier_policy in verifier_policies:
        extend(verifier_policy.get("enabled_evidence_modalities") or verifier_policy.get("enabled_modalities"))
    if _record_bool(record, "browser_enabled", "qa_browser_enabled") or any(
        bool(policy.get("browser_enabled")) for policy in verifier_policies
    ):
        enabled.append("browser")
    if _record_bool(record, "visual_enabled", "qa_visual_enabled") or any(
        bool(policy.get("visual_enabled")) for policy in verifier_policies
    ):
        enabled.append("visual")
    if any(
        bool(policy.get("llm_judge_enabled") or policy.get("multimodal_llm_enabled")) for policy in verifier_policies
    ):
        enabled.append("llm_judge")
    if any(
        bool(policy.get("custom_script_enabled") or policy.get("user_scripts_enabled")) for policy in verifier_policies
    ):
        enabled.append("custom_script")
    if any(bool(policy.get("domain_verifiers_enabled")) for policy in verifier_policies):
        enabled.append("domain")
    return _string_list(enabled)


def _record_with_control_plane_verifier_policy(workspace: Path, record: dict[str, Any]) -> dict[str, Any]:
    policy = read_verifier_policy(ReadVerifierPolicyQueryV1(workspace=str(workspace))).policy
    gate_policy = verifier_policy_to_gate_policy(policy)
    if not gate_policy.get("enabled_evidence_modalities") and not gate_policy.get("required_evidence_modalities"):
        return record
    return {
        **record,
        "control_plane_verifier_policy": gate_policy,
    }


def _required_modalities_from_job_token(job_token: dict[str, Any]) -> list[str]:
    gate_policy = _dict_value(job_token.get("gate_policy"))
    return _modality_list(gate_policy.get("required_evidence_modalities") or gate_policy.get("required_modalities"))


def _enabled_modalities_from_job_token(job_token: dict[str, Any]) -> list[str]:
    gate_policy = _dict_value(job_token.get("gate_policy"))
    return _modality_list(gate_policy.get("enabled_evidence_modalities") or gate_policy.get("enabled_modalities"))


def _missing_required_modalities(
    required_modalities: list[str],
    gate_modalities: dict[str, dict[str, Any]],
) -> list[str]:
    missing: list[str] = []
    for modality_name in required_modalities:
        modality = gate_modalities.get(modality_name)
        if not isinstance(modality, dict) or not modality.get("present") or not modality.get("ok"):
            missing.append(modality_name)
    return _string_list(missing)


def _evidence_modalities_from_physical_evidence(physical_evidence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Derive canonical evidence modalities from one physical evidence payload.

    The multimodal QA path must append evidence here instead of becoming a
    second source of truth. Future QA checks can attach domain verifier scripts,
    screenshots, browser traces, and model judgements under one ledger event.
    """

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


def _build_capability_audit(
    *,
    contract_sources: list[str],
    blueprint_sources: list[str],
    target_files: list[str],
    allowed_paths: list[str],
    required_artifacts: list[str],
    repair_targets: list[str],
    qa_expectations: list[str],
) -> dict[str, Any]:
    target_outside_scope = [path for path in target_files if not _path_is_allowed(path, allowed_paths)]
    repair_outside_scope = [path for path in repair_targets if not _path_is_allowed(path, allowed_paths)]
    artifact_outside_scope = [path for path in required_artifacts if not _path_is_allowed(path, allowed_paths)]
    issues: list[str] = []
    if not contract_sources:
        issues.append("missing_pm_contract_source")
    if not blueprint_sources:
        issues.append("missing_ce_blueprint_source")
    if not target_files:
        issues.append("missing_target_files")
    if not allowed_paths:
        issues.append("missing_allowed_paths")
    if target_outside_scope:
        issues.append("target_files_outside_allowed_paths")
    if repair_outside_scope:
        issues.append("repair_targets_outside_allowed_paths")
    if artifact_outside_scope:
        issues.append("required_artifacts_outside_allowed_paths")
    return {
        "ok": not issues,
        "issues": issues,
        "contract_sources": contract_sources,
        "blueprint_sources": blueprint_sources,
        "target_files": target_files,
        "allowed_paths": allowed_paths,
        "required_artifacts": required_artifacts,
        "repair_targets": repair_targets,
        "qa_expectations": qa_expectations,
        "drift": {
            "target_files_outside_allowed_paths": target_outside_scope,
            "repair_targets_outside_allowed_paths": repair_outside_scope,
            "required_artifacts_outside_allowed_paths": artifact_outside_scope,
        },
    }


def summarize_run_ledger_meta(value: Any) -> dict[str, Any]:
    """Return a fail-closed summary for persisted run ledger metadata."""

    if not isinstance(value, dict):
        return {
            "ok": False,
            "detail": "run ledger metadata missing",
            "missing": ["ledger_path", "content_id", "event_id", "append_id", "job_token_id"],
        }
    required = ("ledger_path", "content_id", "event_id", "append_id", "job_token_id")
    missing = [key for key in required if not _clean_string(value.get(key))]
    ledger_path = _clean_string(value.get("ledger_path"))
    if missing:
        return {
            "ok": False,
            "detail": f"run ledger metadata missing fields: {', '.join(missing)}",
            "ledger_path": ledger_path,
            "missing": missing,
        }
    ledger_file = Path(ledger_path)
    if not ledger_file.is_file():
        return {
            "ok": False,
            "detail": f"run ledger file missing: {ledger_path}",
            "ledger_path": ledger_path,
            "missing": ["ledger_file"],
        }
    job_token = _job_token_from_ledger_meta(value)
    capability_audit = job_token.get("capability_audit") if isinstance(job_token, dict) else {}
    if not isinstance(capability_audit, dict):
        return {
            "ok": False,
            "detail": "run ledger job token missing capability audit",
            "ledger_path": ledger_path,
            "missing": ["job_token.capability_audit"],
        }
    capability_issues = capability_audit.get("issues")
    if not bool(capability_audit.get("ok")):
        issue_list = [str(item) for item in capability_issues] if isinstance(capability_issues, list) else []
        return {
            "ok": False,
            "detail": "run ledger capability invalid: " + ", ".join(issue_list or ["unknown"]),
            "ledger_path": ledger_path,
            "missing": issue_list,
            "capability_audit": capability_audit,
        }
    return {
        "ok": True,
        "detail": f"run ledger event {value['content_id']} written",
        "ledger_path": ledger_path,
        "content_id": _clean_string(value.get("content_id")),
        "event_id": _clean_string(value.get("event_id")),
        "append_id": _clean_string(value.get("append_id")),
        "job_token_id": _clean_string(value.get("job_token_id")),
        "capability_audit": capability_audit,
        "missing": [],
    }


def build_job_token_from_record(
    record: dict[str, Any],
    *,
    run_id: str = "",
    project_id: str = "",
    stage: str = "real_run_gate",
) -> JobToken:
    """Build a canonical job token from the current bench/factory record."""

    chain = _dict_value(record.get("chain"))
    chain_results = _dict_value(record.get("chain_results"))
    audit_bundle = _dict_value(chain.get("audit_bundle"))
    record_artifacts = _dict_value(record.get("artifacts"))
    audit_artifacts = _dict_value(audit_bundle.get("artifacts"))
    target_files = _string_list(
        record.get("target_files") or record.get("declared_source_targets") or record.get("code_files")
    )
    if not target_files:
        target_files = _string_list(record.get("code_files"))
    allowed_paths = _string_list(
        record.get("allowed_paths")
        or record.get("scope_paths")
        or record.get("target_files")
        or record.get("declared_source_targets")
        or record.get("code_files")
    )
    required_artifacts = _string_list(record.get("required_artifacts") or record.get("code_files"))
    parent_token_id = _clean_string(
        record.get("parent_token_id") or record.get("previous_job_token_id") or record.get("repair_parent_token_id")
    )
    repair_lineage = _lineage_entries(record.get("repair_lineage"))
    quality_repair = record.get("factory_workspace_quality_repair")
    if isinstance(quality_repair, dict):
        repair_lineage.append(
            {
                "source": "factory_workspace_quality_repair",
                "run_id": _clean_string(quality_repair.get("run_id")),
                "changed_files": _string_list(quality_repair.get("changed_files")),
                "target_files": _string_list(quality_repair.get("target_files")),
            }
        )
    repair_targets = _repair_targets_from_lineage(repair_lineage)
    qa_expectations = (
        _string_items(record.get("qa_expectations"))
        or _string_items(record.get("acceptance_criteria"))
        or _string_items(record.get("acceptance"))
        or _string_items(record.get("qa_contract"))
    )
    required_evidence_modalities = _required_evidence_modalities_from_record(
        record,
        stage=stage,
    )
    enabled_evidence_modalities = _enabled_evidence_modalities_from_record(record)
    gate_policy = {
        "stage": stage,
        "requires_physical_artifacts": True,
        "requires_real_entrypoint": stage == "real_run_gate",
        "requires_command_evidence": stage == "real_run_gate",
        "enabled_evidence_modalities": enabled_evidence_modalities,
        "required_evidence_modalities": required_evidence_modalities,
    }
    contract_sources: list[str] = []
    if chain_results.get("contract_goal") or record.get("contract_goal"):
        contract_sources.append("contract_goal")
    if record.get("brief") or record.get("project_brief"):
        contract_sources.append("project_brief")
    if target_files:
        contract_sources.append("target_files")
    blueprint_sources: list[str] = []
    blueprint_artifacts = _string_list(record_artifacts.get("blueprint") or audit_artifacts.get("blueprint"))
    if record.get("blueprint_id") or audit_bundle.get("blueprint_id"):
        blueprint_sources.append("blueprint_id")
    if record.get("blueprints") or audit_bundle.get("blueprints"):
        blueprint_sources.append("blueprints")
    if record.get("chief_engineer") or audit_bundle.get("chief_engineer"):
        blueprint_sources.append("chief_engineer")
    if blueprint_artifacts:
        blueprint_sources.append("artifacts.blueprint")
    capability_audit = _build_capability_audit(
        contract_sources=contract_sources,
        blueprint_sources=blueprint_sources,
        target_files=target_files,
        allowed_paths=allowed_paths,
        required_artifacts=required_artifacts,
        repair_targets=repair_targets,
        qa_expectations=qa_expectations,
    )
    contract_facts = {
        "contract_goal": chain_results.get("contract_goal") or record.get("contract_goal") or "",
        "project_brief": record.get("brief") or record.get("project_brief") or "",
        "target_files": target_files,
        "allowed_paths": allowed_paths,
        "qa_expectations": qa_expectations,
    }
    blueprint_facts = {
        "blueprint_id": record.get("blueprint_id") or audit_bundle.get("blueprint_id") or "",
        "blueprints": record.get("blueprints") or audit_bundle.get("blueprints") or [],
        "blueprint_artifacts": blueprint_artifacts,
        "chief_engineer": record.get("chief_engineer") or audit_bundle.get("chief_engineer") or {},
    }
    token_run_id = _clean_string(run_id or record.get("run_id"))
    token_factory_run_id = _clean_string(chain.get("run_id") or record.get("factory_run_id"))
    token_project_id = _clean_string(project_id or record.get("id") or record.get("project_id"))
    contract_hash = stable_hash(contract_facts)
    blueprint_hash = stable_hash(blueprint_facts)
    token_payload: dict[str, Any] = {
        "schema_version": 1,
        "run_id": token_run_id,
        "factory_run_id": token_factory_run_id,
        "project_id": token_project_id,
        "stage": stage,
        "target_files": target_files,
        "allowed_paths": allowed_paths,
        "required_artifacts": required_artifacts,
        "gate_policy": gate_policy,
        "capability_audit": capability_audit,
        "parent_token_id": parent_token_id,
        "repair_lineage": repair_lineage,
        "contract_hash": contract_hash,
        "blueprint_hash": blueprint_hash,
        "source": "control_plane.job_token",
    }
    return JobToken(
        schema_version=1,
        token_id=stable_hash(token_payload),
        run_id=token_run_id,
        factory_run_id=token_factory_run_id,
        project_id=token_project_id,
        stage=stage,
        target_files=target_files,
        allowed_paths=allowed_paths,
        required_artifacts=required_artifacts,
        gate_policy=gate_policy,
        capability_audit=capability_audit,
        parent_token_id=parent_token_id,
        repair_lineage=repair_lineage,
        contract_hash=contract_hash,
        blueprint_hash=blueprint_hash,
        source="control_plane.job_token",
    )


def build_gate_ledger_event(
    job_token: JobToken,
    gate: dict[str, Any],
    *,
    gate_name: str = "real_run_gate",
) -> dict[str, Any]:
    """Convert a gate result into a standard append-only ledger event."""

    raw_requirements = gate.get("requirements")
    requirements: dict[str, Any] = raw_requirements if isinstance(raw_requirements, dict) else {}
    raw_entrypoint = gate.get("entrypoint")
    entrypoint: dict[str, Any] = raw_entrypoint if isinstance(raw_entrypoint, dict) else {}
    raw_commands = gate.get("commands")
    commands: list[Any] = raw_commands if isinstance(raw_commands, list) else []
    total_command_count = int(gate.get("command_count_total") or len(commands))
    physical_evidence = {
        "requirements": requirements,
        "entrypoint": entrypoint,
        "command_count": total_command_count,
        "sampled_command_count": len(commands),
        "commands_truncated": bool(gate.get("commands_truncated")),
        "commands": commands,
        "modalities": _evidence_modalities_from_physical_evidence(
            {
                "modalities": gate.get("modalities"),
                "requirements": requirements,
                "entrypoint": entrypoint,
                "command_count": total_command_count,
                "commands": commands,
                "verifier_results": gate.get("verifier_results"),
                "custom_verifiers": gate.get("custom_verifiers"),
                "domain_verifiers": gate.get("domain_verifiers"),
                "script_verifiers": gate.get("script_verifiers"),
                "user_verifiers": gate.get("user_verifiers"),
                "qa_verifiers": gate.get("qa_verifiers"),
                "llm_judge": gate.get("llm_judge"),
                "visual_judgement": gate.get("visual_judgement"),
                "qa_visual_judgement": gate.get("qa_visual_judgement"),
            }
        ),
    }
    _attach_tool_receipt_evidence(physical_evidence, gate)
    event = {
        "schema_version": 1,
        "event_type": "gate_evaluated",
        "stage": job_token.stage,
        "job_token": job_token.to_dict(),
        "gate": {
            "name": gate_name,
            "ok": bool(gate.get("ok")),
            "summary": _clean_string(gate.get("summary")),
            "failing_requirements": [
                name for name, item in requirements.items() if isinstance(item, dict) and not bool(item.get("ok"))
            ],
        },
        "physical_evidence": physical_evidence,
    }
    event["content_id"] = stable_hash(_event_content_payload(event))
    event["event_id"] = event["content_id"]
    return event


def persist_real_run_gate_ledger(
    workspace: Path,
    record: dict[str, Any],
    gate: dict[str, Any],
    *,
    run_id: str = "",
    project_id: str = "",
    stage: str = "real_run_gate",
    gate_name: str = "real_run_gate",
) -> dict[str, Any]:
    """Persist a real-run gate event and return lightweight ledger metadata."""

    token_record = _record_with_control_plane_verifier_policy(workspace, record)
    token = build_job_token_from_record(token_record, run_id=run_id, project_id=project_id, stage=stage)
    event = build_gate_ledger_event(token, gate, gate_name=gate_name)
    persisted = append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(workspace),
            run_id=token.run_id or run_id or "unknown",
            event=event,
        )
    ).receipt
    persisted_event = persisted["event"]
    return {
        "ledger_path": persisted["ledger_path"],
        "event_id": persisted_event["event_id"],
        "content_id": persisted_event["content_id"],
        "append_id": persisted_event["append_id"],
        "job_token_id": token.token_id,
        "job_token": token.to_dict(),
        "ledger_event": persisted_event,
        "stage": token.stage,
        "gate": gate_name,
    }


def build_run_ledger_projection(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the canonical read model for ledger-backed UI/QA projections."""

    return _build_platform_run_ledger_projection(events)


def summarize_run_ledger_projection(value: Any) -> dict[str, Any]:
    """Return the control-plane integrity status for a ledger projection."""

    return _summarize_platform_run_ledger_projection(value)


def load_run_ledger_projection(workspace: Path, *, run_id: str) -> dict[str, Any]:
    """Read a run ledger file and return the canonical projection."""

    return build_run_ledger_projection(RunLedger(workspace, run_id=run_id).read_events())
