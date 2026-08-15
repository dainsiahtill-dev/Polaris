from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from polaris.cells.control_plane.run_ledger.public import (
    looks_like_failure_evidence_payload,
    merge_failure_evidence_payload,
    summarize_failed_gate_evidence_context_slot,
)
from polaris.kernelone.events.final_request_evidence import (
    final_request_evidence_ref_for_requirement,
    looks_like_ce_blueprint_payload,
    looks_like_pm_contract_payload,
    looks_like_workspace_quality_evidence_payload,
    summarize_target_scope_evidence_payload,
    summarize_workspace_quality_evidence_context_slot,
    target_scope_evidence_entry,
)

from ..response_types import PreparedLLMRequest
from ._constants import (
    _ARCHITECTURE_OR_FILE_PLAN_KEYS,
    _CE_BLUEPRINT_CONTEXT_KEYS,
    _FAILED_GATE_EVIDENCE_CONTEXT_KEYS,
    _INTERFACE_DISCREPANCY_CONTEXT_KEYS,
    _MODULE_INTERFACE_CONTRACT_KEYS,
    _PM_CONTRACT_CONTEXT_KEYS,
    _WORKSPACE_QUALITY_EVIDENCE_CONTEXT_KEYS,
)
from ._primitives import (
    _bool_value,
    _canonical_actual_sibling_exports_hash,
    _int_value,
    _is_sha256,
    _mapping,
    _non_empty_attr,
    _stable_digest,
    _string_list,
)
from ._request_core import (
    _delivery_contract_payload,
    _execution_contract,
    _execution_contract_summary,
    _execution_envelope,
    _execution_envelope_hash,
    _execution_envelope_summary,
    _execution_profile,
    _execution_profile_summary,
    _execution_strategy,
    _request_context,
    _request_messages,
    _request_options,
    _resident_agi_audit_context_summary,
    _task_metadata,
    _task_type_value,
)


def _looks_like_module_interface_contract(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    schema_version = str(value.get("schema_version") or "").strip()
    if schema_version == "chief_engineer.module_interface_contract.v1":
        return True
    modules = value.get("modules")
    if not isinstance(modules, (list, tuple)):
        return False
    for module in modules:
        if not isinstance(module, dict):
            continue
        if module.get("path") and (
            module.get("actual_public_symbols")
            or module.get("planned_public_symbols")
            or module.get("consumes_symbols")
        ):
            return True
    return False


def _find_module_interface_contract(value: Any, *, depth: int = 0) -> dict[str, Any]:
    if depth > 5:
        return {}
    if isinstance(value, dict):
        for key in _MODULE_INTERFACE_CONTRACT_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, dict) and _looks_like_module_interface_contract(candidate):
                return dict(candidate)
        if _looks_like_module_interface_contract(value):
            return dict(value)
        for key in (
            "ce_blueprint",
            "chief_engineer_blueprint",
            "blueprint",
            "blueprint_payload",
            "task_blueprint",
            "task",
            "metadata",
            "context",
            "delivery_contract",
            "quality_contract",
        ):
            found = _find_module_interface_contract(value.get(key), depth=depth + 1)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for item in value:
            found = _find_module_interface_contract(item, depth=depth + 1)
            if found:
                return found
    return {}


def _looks_like_pm_contract_evidence(value: Any) -> bool:
    """Accept one PM contract or a validated project contract set."""

    if looks_like_pm_contract_payload(value):
        return True
    if not isinstance(value, dict):
        return False
    tasks = value.get("tasks")
    return isinstance(tasks, (list, tuple)) and any(looks_like_pm_contract_payload(task) for task in tasks)


def _pm_contract_payload(ai_request: Any | None) -> dict[str, Any]:
    if ai_request is None:
        return {}
    context_payload = _request_context(ai_request)
    task_payload = _mapping(context_payload.get("task"))
    for container in (
        context_payload,
        _mapping(context_payload.get("metadata")),
        task_payload,
        _mapping(task_payload.get("metadata")),
        _execution_contract(ai_request),
        _task_metadata(ai_request),
    ):
        for key in _PM_CONTRACT_CONTEXT_KEYS:
            candidate = container.get(key)
            if isinstance(candidate, dict) and _looks_like_pm_contract_evidence(candidate):
                return dict(candidate)
    return {}


def _ce_blueprint_payload(ai_request: Any | None) -> dict[str, Any]:
    if ai_request is None:
        return {}
    context_payload = _request_context(ai_request)
    task_payload = _mapping(context_payload.get("task"))
    for container in (
        context_payload,
        _mapping(context_payload.get("metadata")),
        task_payload,
        _mapping(task_payload.get("metadata")),
        _task_metadata(ai_request),
        _execution_contract(ai_request),
    ):
        for key in _CE_BLUEPRINT_CONTEXT_KEYS:
            candidate = container.get(key)
            if isinstance(candidate, dict) and looks_like_ce_blueprint_payload(candidate):
                return dict(candidate)
    return {}


def _pm_contract_summary(contract: dict[str, Any]) -> dict[str, Any]:
    if not contract:
        return {}
    task_id = str(contract.get("task_id") or contract.get("id") or "").strip()
    return {
        "schema_version": str(contract.get("schema_version") or ""),
        "task_id": task_id,
        "target_file_count": len(_string_list(contract.get("target_files") or contract.get("targets"))),
        "acceptance_count": len(_string_list(contract.get("acceptance") or contract.get("acceptance_criteria"))),
        "dependency_count": len(_string_list(contract.get("depends_on") or contract.get("dependencies"))),
    }


def _ce_blueprint_summary(blueprint: dict[str, Any]) -> dict[str, Any]:
    if not blueprint:
        return {}
    return {
        "schema_version": str(blueprint.get("schema_version") or ""),
        "target_file_count": len(_string_list(blueprint.get("target_files") or blueprint.get("scope_for_apply"))),
        "execution_checklist_count": len(_string_list(blueprint.get("execution_checklist"))),
        "has_module_interface_contract": bool(_find_module_interface_contract(blueprint)),
        "has_construction_plan": bool(blueprint.get("construction_plan")),
    }


def _target_scope_payload(ai_request: Any | None) -> dict[str, Any]:
    if ai_request is None:
        return {}

    context_payload = _request_context(ai_request)
    task_payload = _mapping(context_payload.get("task"))
    task_metadata = _task_metadata(ai_request)
    execution_envelope = _execution_envelope(ai_request)
    authorization = _mapping(execution_envelope.get("authorization"))
    candidates = (
        ("execution_envelope.authorization", authorization),
        ("execution_profile", _execution_profile(ai_request)),
        ("execution_contract", _execution_contract(ai_request)),
        ("task_metadata", task_metadata),
        ("context", context_payload),
        ("context.metadata", _mapping(context_payload.get("metadata"))),
        ("task", task_payload),
        ("task.metadata", _mapping(task_payload.get("metadata"))),
        ("pm_contract", _pm_contract_payload(ai_request)),
        ("ce_blueprint", _ce_blueprint_payload(ai_request)),
    )
    sources = [
        entry for source, payload in candidates if payload and (entry := target_scope_evidence_entry(source, payload))
    ]
    if not sources:
        return {}
    return {
        "schema_version": "polaris.target_scope.evidence.v1",
        "sources": sources,
    }


def _module_interface_contract_payload(ai_request: Any | None) -> dict[str, Any]:
    if ai_request is None:
        return {}
    context_payload = _request_context(ai_request)
    for payload in (
        context_payload,
        _execution_contract(ai_request),
        _task_metadata(ai_request),
    ):
        found = _find_module_interface_contract(payload)
        if found:
            return found
    return {}


def _module_interface_contract_summary(contract: dict[str, Any]) -> dict[str, Any]:
    if not contract:
        return {}
    modules = contract.get("modules")
    module_rows = [item for item in modules if isinstance(item, dict)] if isinstance(modules, (list, tuple)) else []
    actual_export_module_count = sum(1 for item in module_rows if _string_list(item.get("actual_public_symbols")))
    planned_export_module_count = sum(1 for item in module_rows if _string_list(item.get("planned_public_symbols")))
    return {
        "schema_version": str(contract.get("schema_version") or ""),
        "source": str(contract.get("source") or ""),
        "authority": str(contract.get("authority") or ""),
        "language": str(contract.get("language") or ""),
        "module_count": len(module_rows),
        "actual_export_module_count": actual_export_module_count,
        "planned_export_module_count": planned_export_module_count,
        "actual_interface_snapshot_sources": _string_list(contract.get("actual_interface_snapshot_sources")),
        "actual_interface_snapshot_file_count": _int_value(contract.get("actual_interface_snapshot_file_count")),
        "interface_conflict_count": len(contract.get("interface_conflicts") or [])
        if isinstance(contract.get("interface_conflicts"), list)
        else 0,
    }


def _actual_sibling_exports_message_bound(
    value: Mapping[str, Any],
    messages: list[dict[str, Any]],
) -> bool:
    rendered = "\n".join(str(message.get("content") or "") for message in messages)
    snapshot_hash = str(value.get("snapshot_sha256") or "").strip()
    marker = f"polaris.actual_sibling_exports.evidence.v2 snapshot_sha256={snapshot_hash}"
    if marker not in rendered:
        return False
    modules = value.get("modules")
    if not isinstance(modules, list):
        return False
    for module in modules:
        if not isinstance(module, dict):
            return False
        header = (
            f"--- parent_task_id={module.get('parent_task_id')} "
            f"receipt_id={module.get('effect_receipt_id')} "
            f"path={module.get('path')} sha256={module.get('sha256')} ---"
        )
        body = str(module.get("body") or "")
        if f"{header}\n{body}" not in rendered:
            return False
    return True


def _looks_like_actual_sibling_exports(
    value: Any,
    *,
    messages: list[dict[str, Any]] | None = None,
) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("schema_version") != "polaris.actual_sibling_exports.evidence.v2":
        return False
    if value.get("source") != "roles.adapters.director.task_runtime_dependency_artifact_snapshot":
        return False
    dependency_ids = _string_list(value.get("dependency_task_ids"))
    covered_parent_ids = _string_list(value.get("covered_parent_task_ids"))
    if not dependency_ids or dependency_ids != covered_parent_ids or len(set(dependency_ids)) != len(dependency_ids):
        return False
    modules = value.get("modules")
    if not isinstance(modules, list) or not modules:
        return False
    if type(value.get("module_count")) is not int or value.get("module_count") != len(modules):
        return False
    total_bytes = 0
    module_parent_ids: set[str] = set()
    required_hash_fields = (
        "source_fact_hash",
        "effect_receipt_hash",
        "effect_receipt_binding_hash",
        "physical_result_hash",
        "target_state_hash",
        "sha256",
    )
    for module in modules:
        if not isinstance(module, dict):
            return False
        parent_task_id = str(module.get("parent_task_id") or "").strip()
        if parent_task_id not in dependency_ids:
            return False
        module_parent_ids.add(parent_task_id)
        if not str(module.get("parent_runtime_task_id") or "").strip():
            return False
        if not str(module.get("parent_external_task_id") or "").strip():
            return False
        if not str(module.get("source_fact_ref") or "").startswith("task_runtime.observable_task:"):
            return False
        if not str(module.get("effect_receipt_id") or "").strip():
            return False
        if any(not _is_sha256(module.get(field)) for field in required_hash_fields):
            return False
        path = str(module.get("path") or "").strip()
        if (
            not path
            or path.startswith("/")
            or "\\" in path
            or "\x00" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            return False
        body = module.get("body")
        if not isinstance(body, str) or not body:
            return False
        body_bytes = body.encode("utf-8")
        if type(module.get("byte_count")) is not int or module.get("byte_count") != len(body_bytes):
            return False
        if hashlib.sha256(body_bytes).hexdigest() != module.get("sha256"):
            return False
        guarded_snapshot = module.get("guarded_snapshot")
        if not isinstance(guarded_snapshot, dict):
            return False
        for field in ("device", "inode", "mtime_ns", "ctime_ns", "root_device", "root_inode"):
            if type(guarded_snapshot.get(field)) is not int or guarded_snapshot[field] < 0:
                return False
        total_bytes += len(body_bytes)
    # L2-12: CE split parents can be covered with zero owned artifacts
    # (TASK-3-foundation).  Journal used to require every declared parent to
    # contribute a module, so TASK-2 sealed bodies were present in messages
    # but has_actual_sibling_exports stayed false.
    zero_artifact_parent_ids = _string_list(value.get("zero_artifact_parent_task_ids"))
    if len(set(zero_artifact_parent_ids)) != len(zero_artifact_parent_ids):
        return False
    if any(parent_id not in dependency_ids for parent_id in zero_artifact_parent_ids):
        return False
    if module_parent_ids & set(zero_artifact_parent_ids):
        return False
    if module_parent_ids | set(zero_artifact_parent_ids) != set(dependency_ids):
        return False
    if type(value.get("total_byte_count")) is not int or value.get("total_byte_count") != total_bytes:
        return False
    snapshot_hash = str(value.get("snapshot_sha256") or "").strip()
    if not _is_sha256(snapshot_hash) or snapshot_hash != _canonical_actual_sibling_exports_hash(value):
        return False
    return messages is None or _actual_sibling_exports_message_bound(value, messages)


def _direct_actual_sibling_exports_payload(
    ai_request: Any | None,
    *,
    messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if ai_request is None:
        return {}
    context_payload = _request_context(ai_request)
    for container in (
        context_payload,
        _mapping(context_payload.get("metadata")),
        _task_metadata(ai_request),
        _mapping(context_payload.get("ce_blueprint")),
        _mapping(context_payload.get("chief_engineer_blueprint")),
        _mapping(context_payload.get("blueprint")),
    ):
        candidate = container.get("actual_sibling_exports")
        if isinstance(candidate, dict) and _looks_like_actual_sibling_exports(candidate, messages=messages):
            return dict(candidate)
    return {}


def _actual_sibling_exports_payload(
    ai_request: Any | None,
    module_interface_contract: dict[str, Any] | None = None,
    *,
    messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    del module_interface_contract
    if ai_request is None:
        return {}
    direct_payload = _direct_actual_sibling_exports_payload(ai_request, messages=messages)
    if direct_payload:
        return direct_payload
    return {}


def _looks_like_interface_discrepancy_payload(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    schema_version = str(value.get("schema_version") or "").strip()
    if "interface_discrepancy" in schema_version or schema_version == "director.interface_delta.v1":
        return True
    if isinstance(value.get("interface_delta"), dict) or isinstance(value.get("triage_summary"), dict):
        return True
    if str(value.get("recommended_route") or "").strip() in {
        "pending_design_interface_contract",
        "director_retry_with_interface_discrepancy_context",
        "task_boundary_interface_discrepancy",
    }:
        return True
    if str(value.get("plan_probe_status") or value.get("reason") or "").strip() == "coverage_matched_but_unplannable":
        return True
    return bool(value.get("interface_delta_available") or value.get("triage_summary_available"))


def _first_interface_discrepancy_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, dict):
                return dict(item)
    return {}


def _find_interface_discrepancy_context(value: Any, *, depth: int = 0) -> dict[str, Any]:
    if depth > 5:
        return {}
    if isinstance(value, dict):
        for key in _INTERFACE_DISCREPANCY_CONTEXT_KEYS:
            found = _first_interface_discrepancy_mapping(value.get(key))
            if _looks_like_interface_discrepancy_payload(found):
                return found
        if _looks_like_interface_discrepancy_payload(value):
            return dict(value)
        for key in (
            "metadata",
            "context",
            "repair",
            "run_ledger",
            "run_ledger_projection",
            "evidence",
            "physical_evidence",
            "task_boundary",
            "task_boundary_quality",
            "plan_probe_preaudit",
            "task_metadata",
        ):
            found = _find_interface_discrepancy_context(value.get(key), depth=depth + 1)
            if found:
                return found
        modalities = value.get("modalities")
        if isinstance(modalities, dict):
            for modality in modalities.values():
                found = _find_interface_discrepancy_context(modality, depth=depth + 1)
                if found:
                    return found
        elif isinstance(modalities, (list, tuple)):
            for modality in modalities:
                found = _find_interface_discrepancy_context(modality, depth=depth + 1)
                if found:
                    return found
    elif isinstance(value, (list, tuple)):
        for item in value:
            found = _find_interface_discrepancy_context(item, depth=depth + 1)
            if found:
                return found
    return {}


def _interface_discrepancy_context_payload(ai_request: Any | None) -> dict[str, Any]:
    if ai_request is None:
        return {}
    context_payload = _request_context(ai_request)
    payload = _find_interface_discrepancy_context(context_payload)
    if not payload:
        payload = _find_interface_discrepancy_context(_task_metadata(ai_request))
    if not payload:
        payload = _find_interface_discrepancy_context(_execution_contract(ai_request))
    if not payload:
        payload = _find_interface_discrepancy_context(_execution_envelope(ai_request))
    if not payload:
        return {}
    nested_evidence = _find_interface_discrepancy_context(payload.get("interface_discrepancy_evidence"))
    if nested_evidence:
        payload = {**nested_evidence, **payload}

    metadata = _mapping(payload.get("metadata"))
    interface_delta = payload.get("interface_delta")
    if not isinstance(interface_delta, dict):
        interface_delta = metadata.get("interface_delta")
    triage_summary = payload.get("triage_summary")
    if not isinstance(triage_summary, dict):
        triage_summary = metadata.get("triage_summary")
    interface_delta_map = dict(interface_delta) if isinstance(interface_delta, dict) else {}
    triage_summary_map = dict(triage_summary) if isinstance(triage_summary, dict) else {}
    diagnostic_count = _int_value(
        payload.get("covered_unplannable_diagnostic_count")
        or payload.get("diagnostic_count")
        or metadata.get("covered_unplannable_diagnostic_count")
        or interface_delta_map.get("diagnostic_count")
    )
    diagnostics = payload.get("diagnostics")
    if diagnostic_count <= 0 and isinstance(diagnostics, (list, tuple)):
        diagnostic_count = len(diagnostics)
    return {
        "schema_version": "polaris.interface_discrepancy_context.evidence.v1",
        "source_schema_version": str(payload.get("schema_version") or ""),
        "source": str(payload.get("source") or payload.get("modality") or "interface_discrepancy_context"),
        "plan_probe_status": str(payload.get("plan_probe_status") or metadata.get("plan_probe_status") or ""),
        "reason": str(payload.get("reason") or metadata.get("reason") or ""),
        "recommended_owner": str(
            payload.get("recommended_owner")
            or metadata.get("recommended_owner")
            or triage_summary_map.get("recommended_owner")
            or ""
        ),
        "recommended_route": str(
            payload.get("recommended_route")
            or metadata.get("recommended_route")
            or triage_summary_map.get("recommended_route")
            or ""
        ),
        "director_retry_allowed": _bool_value(
            payload.get("director_retry_allowed")
            if payload.get("director_retry_allowed") is not None
            else metadata.get("director_retry_allowed")
            if metadata.get("director_retry_allowed") is not None
            else triage_summary_map.get("director_retry_allowed")
        ),
        "llm_fallback_blocked": _bool_value(
            payload.get("llm_fallback_blocked")
            if payload.get("llm_fallback_blocked") is not None
            else metadata.get("llm_fallback_blocked")
            if metadata.get("llm_fallback_blocked") is not None
            else triage_summary_map.get("llm_fallback_blocked")
        ),
        "interface_delta_available": bool(interface_delta_map),
        "interface_delta": interface_delta_map,
        "interface_delta_hash": _stable_digest(interface_delta_map) if interface_delta_map else "",
        "triage_summary_available": bool(triage_summary_map),
        "triage_summary": triage_summary_map,
        "triage_summary_hash": _stable_digest(triage_summary_map) if triage_summary_map else "",
        "diagnostic_count": diagnostic_count,
        "source_tools": _string_list(payload.get("source_tools") or metadata.get("source_tools")),
    }


def _first_evidence_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, dict):
                return dict(item)
    return {}


def _evidence_mapping_for_keys(value: Any, *, keys: tuple[str, ...]) -> dict[str, Any]:
    accepted_refs = {_evidence_ref(key) for key in keys}
    if "failed_gate_evidence" in accepted_refs:
        merged = merge_failure_evidence_payload({}, value)
        if merged.get("items"):
            return dict(merged)
    return _first_evidence_mapping(value)


def _evidence_ref(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return str(final_request_evidence_ref_for_requirement(value) or "")


def _context_slot_payload(value: Any, *, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    accepted_refs = {_evidence_ref(key) for key in keys}
    slot_ref = _evidence_ref(
        str(
            value.get("ref_type")
            or value.get("evidence_ref")
            or value.get("slot_type")
            or value.get("evidence_type")
            or value.get("name")
            or ""
        )
    )
    if slot_ref not in accepted_refs:
        return {}
    for payload_key in ("payload", "evidence", "value", "source_payload", "details"):
        payload = _evidence_mapping_for_keys(value.get(payload_key), keys=keys)
        if payload:
            return payload
    return dict(value)


def _find_structured_evidence_context(
    value: Any,
    *,
    keys: tuple[str, ...],
    predicate: Any,
    depth: int = 0,
) -> dict[str, Any]:
    if depth > 5:
        return {}
    if isinstance(value, dict):
        slot_payload = _context_slot_payload(value, keys=keys)
        if predicate(slot_payload):
            return slot_payload
        for key in keys:
            found = _evidence_mapping_for_keys(value.get(key), keys=keys)
            if predicate(found):
                return found
        if predicate(value):
            return dict(value)
        for key in (
            "metadata",
            "context",
            "evidence",
            "run_ledger",
            "run_ledger_projection",
            "evidence_policy",
            "quality",
            "quality_gate",
            "workspace_quality",
            "context_evidence_slots",
            "evidence_slots",
            "typed_evidence_slots",
            "required_evidence_slots",
            "task_boundary",
            "task_boundary_verdict",
            "task_metadata",
        ):
            found = _find_structured_evidence_context(
                value.get(key),
                keys=keys,
                predicate=predicate,
                depth=depth + 1,
            )
            if found:
                return found
        modalities = value.get("modalities")
        if isinstance(modalities, dict):
            for modality in modalities.values():
                found = _find_structured_evidence_context(
                    modality,
                    keys=keys,
                    predicate=predicate,
                    depth=depth + 1,
                )
                if found:
                    return found
        elif isinstance(modalities, (list, tuple)):
            for modality in modalities:
                found = _find_structured_evidence_context(
                    modality,
                    keys=keys,
                    predicate=predicate,
                    depth=depth + 1,
                )
                if found:
                    return found
    elif isinstance(value, (list, tuple)):
        for item in value:
            found = _find_structured_evidence_context(item, keys=keys, predicate=predicate, depth=depth + 1)
            if found:
                return found
    return {}


def _failed_gate_evidence_payload(ai_request: Any | None) -> dict[str, Any]:
    if ai_request is None:
        return {}
    for payload in (
        _request_context(ai_request),
        _task_metadata(ai_request),
        _execution_contract(ai_request),
        _execution_envelope(ai_request),
    ):
        found = _find_structured_evidence_context(
            payload,
            keys=_FAILED_GATE_EVIDENCE_CONTEXT_KEYS,
            predicate=looks_like_failure_evidence_payload,
        )
        if found:
            return dict(summarize_failed_gate_evidence_context_slot(found))
    return {}


def _workspace_quality_evidence_payload(ai_request: Any | None) -> dict[str, Any]:
    if ai_request is None:
        return {}
    for payload in (
        _request_context(ai_request),
        _task_metadata(ai_request),
        _execution_contract(ai_request),
        _execution_envelope(ai_request),
    ):
        found = _find_structured_evidence_context(
            payload,
            keys=_WORKSPACE_QUALITY_EVIDENCE_CONTEXT_KEYS,
            predicate=looks_like_workspace_quality_evidence_payload,
        )
        if found:
            return dict(summarize_workspace_quality_evidence_context_slot(found))
    return {}


def _architecture_payload_from_blueprint(value: Any) -> dict[str, Any]:
    blueprint = _mapping(value)
    if not blueprint:
        return {}
    payload: dict[str, Any] = {
        key: blueprint.get(key)
        for key in (
            "construction_plan",
            "scope_for_apply",
            "architecture_decisions",
            "execution_checklist",
            "target_files",
            "scope_paths",
        )
        if blueprint.get(key) not in (None, "", [])
    }
    llm_blueprint = _mapping(blueprint.get("llm_blueprint"))
    for key in (
        "implementation_phases",
        "module_boundaries",
        "verification_steps",
        "scope_for_apply_advisory",
        "risk_flags",
    ):
        if llm_blueprint.get(key) not in (None, "", []):
            payload[f"llm_blueprint.{key}"] = llm_blueprint.get(key)
    return payload


def _looks_like_architecture_or_file_plan_payload(value: Any) -> bool:
    """Return whether a direct context value is structured plan evidence."""

    if isinstance(value, dict):
        payload = value.get("payload")
        if payload is not None and _looks_like_architecture_or_file_plan_payload(payload):
            return True
        if _architecture_payload_from_blueprint(value):
            return True
        return any(
            value.get(key) not in (None, "", [])
            for key in (
                "implementation_phases",
                "module_boundaries",
                "verification_steps",
                "file_plan",
                "architecture_plan",
                "architecture_or_file_plan",
            )
        )
    if isinstance(value, (list, tuple)):
        return any(_looks_like_architecture_or_file_plan_payload(item) for item in value)
    return False


def _architecture_payload_from_delivery_contracts(ai_request: Any) -> dict[str, Any]:
    delivery_plan = _delivery_contract_payload(ai_request, "delivery_plan_document")
    delivery_depth = _delivery_contract_payload(ai_request, "delivery_depth_contract")
    payload: dict[str, Any] = {}
    if delivery_plan:
        payload["delivery_plan_document"] = {
            key: delivery_plan.get(key)
            for key in (
                "title",
                "language",
                "project_type",
                "product_summary",
                "user_journey",
                "capability_plan",
                "behavior_plan",
                "verification_plan",
                "evolution_notes",
            )
            if delivery_plan.get(key) not in (None, "", [])
        }
    if delivery_depth:
        payload["delivery_depth_contract"] = {
            key: delivery_depth.get(key)
            for key in (
                "product_intent",
                "behavior_contract",
                "acceptance_contract",
                "level_contract",
                "required_evidence",
            )
            if delivery_depth.get(key) not in (None, "", [])
        }
    return payload


def _architecture_or_file_plan_payload(ai_request: Any | None) -> dict[str, Any]:
    if ai_request is None:
        return {}
    context_payload = _request_context(ai_request)
    for key in _ARCHITECTURE_OR_FILE_PLAN_KEYS:
        raw = context_payload.get(key)
        if _looks_like_architecture_or_file_plan_payload(raw):
            return {"source": f"context.{key}", "payload": raw}
    for key in ("ce_blueprint", "chief_engineer_blueprint", "blueprint", "blueprint_payload", "task_blueprint"):
        payload = _architecture_payload_from_blueprint(context_payload.get(key))
        if payload:
            return {"source": f"context.{key}", "payload": payload}
    delivery_contract_payload = _architecture_payload_from_delivery_contracts(ai_request)
    if delivery_contract_payload:
        return {"source": "delivery_contracts", "payload": delivery_contract_payload}
    task_metadata = _task_metadata(ai_request)
    for key in ("architecture_decisions", "execution_checklist", "implementation_plan", "file_plan"):
        raw = task_metadata.get(key)
        if raw not in (None, "", []):
            return {"source": f"task_metadata.{key}", "payload": raw}
    execution_profile = _execution_profile(ai_request)
    raw_decisions = execution_profile.get("architecture_decisions")
    if raw_decisions not in (None, "", []):
        return {"source": "execution_profile.architecture_decisions", "payload": raw_decisions}
    return {}


def _architecture_or_file_plan_summary(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}
    plan_payload = payload.get("payload")
    plan_mapping = _mapping(plan_payload)
    return {
        "source": str(payload.get("source") or ""),
        "construction_plan_present": bool(plan_mapping.get("construction_plan")),
        "scope_for_apply_count": len(_string_list(plan_mapping.get("scope_for_apply"))),
        "architecture_decision_count": len(_string_list(plan_mapping.get("architecture_decisions"))),
        "execution_checklist_count": len(_string_list(plan_mapping.get("execution_checklist"))),
        "target_files_count": len(_string_list(plan_mapping.get("target_files"))),
        "payload_hash": _stable_digest(plan_payload),
    }


def _request_metadata_summary(ai_request: Any, prepared: PreparedLLMRequest) -> dict[str, Any]:
    context_payload = _request_context(ai_request)
    options = _request_options(ai_request, prepared)
    execution_profile = _execution_profile(ai_request)
    execution_strategy = _execution_strategy(ai_request)
    execution_contract = _execution_contract(ai_request)
    execution_envelope = _execution_envelope(ai_request)
    execution_profile_summary = _execution_profile_summary(ai_request)
    execution_contract_summary = _execution_contract_summary(ai_request)
    execution_envelope_summary = _execution_envelope_summary(ai_request)
    delivery_plan_document = _delivery_contract_payload(ai_request, "delivery_plan_document")
    delivery_depth_contract = _delivery_contract_payload(ai_request, "delivery_depth_contract")
    task_metadata = _task_metadata(ai_request)
    pm_contract = _pm_contract_payload(ai_request)
    ce_blueprint = _ce_blueprint_payload(ai_request)
    target_scope = _target_scope_payload(ai_request)
    module_interface_contract = _module_interface_contract_payload(ai_request)
    actual_sibling_exports = _actual_sibling_exports_payload(
        ai_request,
        module_interface_contract,
        messages=_request_messages(
            ai_request,
            [dict(item) for item in prepared.messages if isinstance(item, dict)],
        ),
    )
    interface_discrepancy_context = _interface_discrepancy_context_payload(ai_request)
    architecture_or_file_plan = _architecture_or_file_plan_payload(ai_request)
    failed_gate_evidence = _failed_gate_evidence_payload(ai_request)
    workspace_quality_evidence = _workspace_quality_evidence_payload(ai_request)
    resident_agi_audit_context = _resident_agi_audit_context_summary(ai_request)
    summary: dict[str, Any] = {
        "schema_version": "llm.request_metadata_summary.v1",
        "task_type": _task_type_value(ai_request),
        "role": _non_empty_attr(ai_request, name="role"),
        "mode": str(context_payload.get("mode") or "").strip(),
        "native_tool_mode": str(context_payload.get("native_tool_mode") or "").strip(),
        "response_format_mode": str(context_payload.get("response_format_mode") or "").strip(),
        "context_keys": sorted(str(key) for key in context_payload),
        "option_keys": sorted(str(key) for key in options),
        "temperature": options.get("temperature") if isinstance(options.get("temperature"), (int, float)) else None,
        "max_tokens": options.get("max_tokens") if isinstance(options.get("max_tokens"), int) else None,
        "reasoning_budget_tokens": (
            options.get("reasoning_budget_tokens") if isinstance(options.get("reasoning_budget_tokens"), int) else None
        ),
        "has_execution_profile": bool(execution_profile),
        "execution_profile_summary": execution_profile_summary,
        "execution_profile_hash": _stable_digest(execution_profile) if execution_profile else "",
        "has_execution_strategy": bool(execution_strategy),
        "execution_strategy_summary": {
            key: execution_strategy.get(key)
            for key in (
                "schema_version",
                "source",
                "temperature",
                "temperature_phase",
                "output_budget_tokens",
                "input_budget_tokens",
                "prompt_max_chars",
                "min_context_utilization",
                "context_underutilized_policy",
            )
            if execution_strategy.get(key) not in (None, "")
        },
        "execution_strategy_hash": _stable_digest(execution_strategy) if execution_strategy else "",
        "has_execution_contract": bool(execution_contract),
        "execution_contract_summary": execution_contract_summary,
        "execution_contract_hash": _stable_digest(execution_contract) if execution_contract else "",
        "has_execution_envelope": bool(execution_envelope),
        "execution_envelope_summary": execution_envelope_summary,
        "execution_envelope_hash": _execution_envelope_hash(ai_request, execution_envelope),
        "has_delivery_plan_document": bool(delivery_plan_document),
        "delivery_plan_document_hash": _stable_digest(delivery_plan_document) if delivery_plan_document else "",
        "has_delivery_depth_contract": bool(delivery_depth_contract),
        "delivery_depth_contract_hash": _stable_digest(delivery_depth_contract) if delivery_depth_contract else "",
        "has_pm_contract": bool(pm_contract),
        "pm_contract_summary": _pm_contract_summary(pm_contract),
        "pm_contract_hash": _stable_digest(pm_contract) if pm_contract else "",
        "has_chief_engineer_blueprint": bool(ce_blueprint),
        "chief_engineer_blueprint_summary": _ce_blueprint_summary(ce_blueprint),
        "chief_engineer_blueprint_hash": _stable_digest(ce_blueprint) if ce_blueprint else "",
        "has_target_scope": bool(target_scope),
        "target_scope_summary": summarize_target_scope_evidence_payload(target_scope),
        "target_scope_hash": _stable_digest(target_scope) if target_scope else "",
        "has_task_metadata": bool(task_metadata),
        "task_metadata_keys": sorted(str(key) for key in task_metadata),
        "task_metadata_hash": _stable_digest(task_metadata) if task_metadata else "",
        "has_module_interface_contract": bool(module_interface_contract),
        "module_interface_contract_summary": _module_interface_contract_summary(module_interface_contract),
        "module_interface_contract_hash": _stable_digest(module_interface_contract)
        if module_interface_contract
        else "",
        "has_actual_sibling_exports": bool(actual_sibling_exports),
        "actual_sibling_exports_summary": actual_sibling_exports,
        "actual_sibling_exports_hash": _stable_digest(actual_sibling_exports) if actual_sibling_exports else "",
        "has_interface_discrepancy_context": bool(interface_discrepancy_context),
        "interface_discrepancy_context_summary": interface_discrepancy_context,
        "interface_discrepancy_context_hash": _stable_digest(interface_discrepancy_context)
        if interface_discrepancy_context
        else "",
        "has_architecture_or_file_plan": bool(architecture_or_file_plan),
        "architecture_or_file_plan_summary": _architecture_or_file_plan_summary(architecture_or_file_plan),
        "architecture_or_file_plan_hash": _stable_digest(architecture_or_file_plan)
        if architecture_or_file_plan
        else "",
        "has_failed_gate_evidence": bool(failed_gate_evidence),
        "failed_gate_evidence_summary": failed_gate_evidence,
        "failed_gate_evidence_hash": _stable_digest(failed_gate_evidence) if failed_gate_evidence else "",
        "has_workspace_quality_evidence": bool(workspace_quality_evidence),
        "workspace_quality_evidence_summary": workspace_quality_evidence,
        "workspace_quality_evidence_hash": _stable_digest(workspace_quality_evidence)
        if workspace_quality_evidence
        else "",
        "has_resident_agi_audit_context": bool(resident_agi_audit_context),
        "resident_agi_audit_context": resident_agi_audit_context,
        "resident_agi_audit_context_hash": _stable_digest(resident_agi_audit_context)
        if resident_agi_audit_context
        else "",
    }
    summary["has_language_guidance"] = bool(
        execution_profile_summary.get("language")
        or execution_profile_summary.get("framework")
        or execution_profile_summary.get("runtime")
    )
    summary["has_output_contract"] = bool(
        execution_profile_summary.get("output_contract_id")
        or context_payload.get("response_format_mode")
        or options.get("response_format")
    )
    return summary


def _request_sampling_audit(ai_request: Any, prepared: PreparedLLMRequest) -> dict[str, Any]:
    options = _request_options(ai_request, prepared)
    request_sampling = _mapping(_request_context(ai_request).get("request_sampling"))
    profile = _execution_profile(ai_request)
    contract = _execution_contract(ai_request)
    raw_contract_sampling = contract.get("sampling")
    contract_sampling: dict[str, Any] = dict(raw_contract_sampling) if isinstance(raw_contract_sampling, dict) else {}
    temperature = options.get("temperature")
    max_tokens = options.get("max_tokens")
    return {
        "temperature": temperature if isinstance(temperature, (int, float)) else None,
        "max_tokens": max_tokens if isinstance(max_tokens, int) else None,
        "temperature_source": str(
            request_sampling.get("temperature_source")
            or profile.get("temperature_source")
            or contract_sampling.get("temperature_source")
            or "request_options"
        ),
        "temperature_phase": str(profile.get("temperature_phase") or contract_sampling.get("temperature_phase") or ""),
        "sampling_mode": str(profile.get("sampling_mode") or contract_sampling.get("sampling_mode") or ""),
        "task_type": str(profile.get("task_type") or contract.get("task_type") or _task_type_value(ai_request)),
        "phase": str(profile.get("phase") or contract.get("phase") or ""),
        "execution_profile_schema": str(profile.get("schema_version") or ""),
        "execution_profile_source": str(profile.get("source") or ""),
        "execution_contract_schema": str(contract.get("schema_version") or ""),
        "execution_contract_source": str(contract.get("source") or ""),
    }
