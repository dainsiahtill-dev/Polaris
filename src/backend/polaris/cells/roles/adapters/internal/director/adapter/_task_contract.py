"""Task-contract promotion and CE blueprint handoff helpers."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from polaris.kernelone.events.final_request_evidence import (
    looks_like_ce_blueprint_payload,
    looks_like_pm_contract_payload,
)

from ._payload import (
    _copy_mapping_payload,
    _string_list_payload,
)
from ._timeout_budget import (
    _join_limited_values,
    _path_looks_like_test_target,
)

logger = logging.getLogger("polaris.cells.roles.adapters.internal.director.adapter")

_TASK_CONTRACT_LIST_KEYS = (
    "target_files",
    "scope_paths",
    "context_files",
    "project_declared_target_files",
    "project_declared_source_targets",
    "project_declared_entrypoint_targets",
    "acceptance",
    "acceptance_criteria",
    "steps",
    "execution_checklist",
    "depends_on",
)
_AUTHORITATIVE_TASK_BOUNDARY_LIST_KEYS = frozenset({"target_files", "scope_paths"})
_TASK_CONTRACT_MAPPING_KEYS = (
    "pm_contract",
    "ce_blueprint",
    "ce_handoff_decision",
    "handoff_decision",
    "job_token",
    "control_plane_job_token",
    "capability_token",
    "delivery_plan_document",
    "delivery_depth_contract",
    "level_contract",
    "behavior_contract",
    "acceptance_contract",
    "manifest_entrypoint_contract",
    "module_interface_contract",
    "execution_profile",
    "execution_contract",
    "execution_envelope",
)
_TASK_CONTRACT_SCALAR_KEYS = (
    "title",
    "subject",
    "description",
    "goal",
    "objective",
    "phase",
    "project_type",
    "language",
    "domain",
    "factory_bench_project_id",
    "factory_bench_title",
    "factory_bench_level",
    "factory_bench_project_workspace",
    "backlog_ref",
    "task_id",
    "pm_task_id",
    "source_task_id",
    "external_task_id",
    "blueprint_id",
    "chief_engineer_blueprint_id",
    "chief_engineer_handoff_id",
    "blueprint_path",
    "runtime_blueprint_path",
    "pm_contract_hash",
    "contract_hash",
    "pm_contract_ref",
    "blueprint_hash",
    "ce_blueprint_hash",
    "ce_blueprint_ref",
    "handoff_decision_hash",
    "ce_handoff_decision_hash",
    "handoff_decision_ref",
    "ce_handoff_decision_ref",
    "handoff_source",
)
_TASK_RUNTIME_GOVERNANCE_SCALAR_KEYS = (
    "factory_run_deadline_epoch_seconds",
    "factory_director_execution_deadline_epoch_seconds",
    "factory_run_deadline_safety_seconds",
)
_STRUCTURED_TASK_CONTRACT_SLOT_KEYS = frozenset(
    {
        "pm_contract",
        "task_contract",
        "ce_blueprint",
        "chief_engineer_blueprint",
        "blueprint",
        "task_blueprint",
        "module_interface_contract",
    }
)
_ROLE_RUNTIME_METADATA_CONTEXT_EVIDENCE_KEYS = (
    "pm_contract",
    "task_contract",
    "ce_blueprint",
    "chief_engineer_blueprint",
    "blueprint",
    "task_blueprint",
    "module_interface_contract",
    "failed_gate_evidence",
    "failure_evidence",
    "workspace_quality_evidence",
    "quality_evidence",
    "target_files",
    "scope_paths",
    "context_files",
    "required_evidence",
)


def _task_contract_sources(task: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    if isinstance(task, dict):
        sources.append(task)
        metadata = task.get("metadata")
        if isinstance(metadata, dict):
            sources.append(metadata)
            nested = metadata.get("metadata")
            if isinstance(nested, dict):
                sources.append(nested)
    return sources


def _has_contract_value(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _looks_like_module_interface_contract_payload(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    schema_version = str(value.get("schema_version") or "").lower()
    if "module_interface" in schema_version or "interface_contract" in schema_version:
        return True
    return any(
        isinstance(value.get(key), (list, tuple, dict)) and bool(value.get(key))
        for key in (
            "modules",
            "public_symbols",
            "actual_public_symbols",
            "exports",
            "consumes_symbols",
            "interfaces",
        )
    )


def _structured_task_contract_slot_is_authoritative(key: str, value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    if key in {"pm_contract", "task_contract"}:
        return looks_like_pm_contract_payload(value)
    if key in {"ce_blueprint", "chief_engineer_blueprint", "blueprint", "task_blueprint"}:
        return looks_like_ce_blueprint_payload(value)
    if key == "module_interface_contract":
        return _looks_like_module_interface_contract_payload(value)
    return True


def _set_structured_task_contract_slot(payload: dict[str, Any], key: str, value: Any) -> None:
    """Install structured evidence unless a structured value already exists."""

    if key not in _STRUCTURED_TASK_CONTRACT_SLOT_KEYS:
        return
    copied = _copy_mapping_payload(value)
    if not copied:
        return
    existing = payload.get(key)
    if _structured_task_contract_slot_is_authoritative(key, existing):
        return
    payload[key] = copied


def _first_contract_value(sources: list[dict[str, Any]], key: str) -> Any:
    for source in sources:
        if not isinstance(source, dict):
            continue
        value = source.get(key)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
            continue
        if isinstance(value, (list, tuple, set)):
            normalized_list = [str(item).strip() for item in value if str(item or "").strip()]
            if normalized_list:
                return normalized_list
            continue
        if isinstance(value, dict):
            if value:
                return dict(value)
            continue
        if value is not None:
            return value
    return None


def _promoted_task_contract_payload(sources: list[dict[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in (*_TASK_CONTRACT_LIST_KEYS, *_TASK_CONTRACT_MAPPING_KEYS, *_TASK_CONTRACT_SCALAR_KEYS):
        value = _first_contract_value(sources, key)
        if value is not None:
            payload[key] = value

    subject = str(payload.get("subject") or "").strip()
    title = str(payload.get("title") or "").strip()
    if subject and not title:
        payload["title"] = subject
    elif title and not subject:
        payload["subject"] = title

    goal = str(payload.get("goal") or "").strip()
    objective = str(payload.get("objective") or "").strip()
    if goal and not objective:
        payload["objective"] = goal
    elif objective and not goal:
        payload["goal"] = objective
    return payload


def _normalize_contract_task_token(value: Any) -> str:
    token = str(value or "").strip().lower()
    token = re.sub(r"^(task[-_])+", "", token)
    return token


def _contract_list(value: Any) -> list[str]:
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item or "").strip()]
    return []


def _merge_contract_lists(*values: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in _contract_list(value):
            if item in seen:
                continue
            seen.add(item)
            merged.append(item)
    return merged


def _load_ce_blueprint_contract_payload(workspace: str, task: dict[str, Any]) -> dict[str, Any]:
    if not workspace or not isinstance(task, dict):
        return {}
    sources = _task_contract_sources(task)
    task_tokens = {
        token
        for source in sources
        for value in (
            source.get("id"),
            source.get("task_id"),
            source.get("pm_task_id"),
            source.get("external_task_id"),
        )
        if (token := _normalize_contract_task_token(value))
    }
    explicit_blueprint_ids = [
        item
        for source in sources
        for item in _contract_list(
            source.get("blueprint_id")
            or source.get("chief_engineer_blueprint_id")
            or source.get("ce_blueprint_id")
            or source.get("runtime_blueprint_id")
        )
    ]
    try:
        from polaris.cells.chief_engineer.blueprint.public import BlueprintPersistence
    except (ImportError, RuntimeError):
        return {}
    persistence = BlueprintPersistence(workspace, ensure_directory=False)
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for blueprint_id in dict.fromkeys([*explicit_blueprint_ids, *persistence.list_all()]):
        payload = persistence.load(blueprint_id)
        if not isinstance(payload, dict):
            continue
        payload_task = _normalize_contract_task_token(payload.get("task_id"))
        payload_tokens = {
            token
            for value in (payload_task, _normalize_contract_task_token(blueprint_id))
            if (token := _normalize_contract_task_token(value))
        }
        if task_tokens and task_tokens.isdisjoint(payload_tokens):
            continue
        if not task_tokens and not explicit_blueprint_ids:
            continue
        updated_at = str(payload.get("updated_at") or payload.get("created_at") or "").strip()
        candidates.append((updated_at, str(blueprint_id), payload))
    if not candidates:
        return {}
    _updated_at, _blueprint_id, payload = max(candidates, key=lambda item: (item[0], item[1]))
    return payload


def _merge_ce_blueprint_contract_payload(
    contract_payload: dict[str, Any],
    blueprint_payload: dict[str, Any],
) -> dict[str, Any]:
    if not blueprint_payload:
        return contract_payload
    merged = dict(contract_payload)
    for key in _TASK_CONTRACT_LIST_KEYS:
        if key in _AUTHORITATIVE_TASK_BOUNDARY_LIST_KEYS:
            if _has_contract_value(contract_payload, key):
                merged[key] = _contract_list(contract_payload.get(key))
            continue
        values = _merge_contract_lists(contract_payload.get(key), blueprint_payload.get(key))
        if values:
            merged[key] = values
    for key in _TASK_CONTRACT_MAPPING_KEYS:
        if not _has_contract_value(merged, key) and isinstance(blueprint_payload.get(key), dict):
            merged[key] = dict(blueprint_payload[key])
    for key in _TASK_CONTRACT_SCALAR_KEYS:
        if not _has_contract_value(merged, key) and blueprint_payload.get(key) is not None:
            merged[key] = blueprint_payload[key]
    merged.setdefault("ce_blueprint", dict(blueprint_payload))
    return merged


def _director_actual_interface_injection_enabled() -> bool:
    """Default ON so Director consumes actual sibling interfaces before writing."""

    raw = str(os.environ.get("KERNELONE_DIRECTOR_INJECT_WORKSPACE_INTERFACE", "")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _build_director_blueprint_handoff_lines(workspace: str, blueprint_id: str) -> list[str]:
    resolved_blueprint_id = str(blueprint_id or "").strip()
    if not resolved_blueprint_id:
        return ["- blueprint_id: not provided"]

    lines = [f"- blueprint_id: {resolved_blueprint_id}"]
    try:
        from polaris.cells.chief_engineer.blueprint.public import (
            BlueprintPersistence,
            validate_director_handoff_from_payload,
        )
    except (ImportError, RuntimeError) as exc:
        lines.append(f"- blueprint_payload: unavailable ({type(exc).__name__})")
        return lines

    payload = BlueprintPersistence(workspace, ensure_directory=False).load(resolved_blueprint_id)
    if not isinstance(payload, dict):
        lines.append("- blueprint_payload: missing or unreadable")
        return lines

    validation = validate_director_handoff_from_payload(
        workspace,
        {"blueprint_id": resolved_blueprint_id},
        require_strict=True,
    )
    lines.append(f"- handoff_ready: {'yes' if validation.get('allowed') else 'no'} ({validation.get('reason')})")
    decision_payload = validation.get("decision_payload")
    if isinstance(decision_payload, dict):
        blockers = _string_list_payload(decision_payload.get("blockers"), limit=4)
        if blockers:
            lines.append(_join_limited_values("handoff blockers", blockers))

    for label, key, limit in (
        ("blueprint target_files", "target_files", 16),
        ("blueprint scope_paths", "scope_paths", 16),
        ("blueprint acceptance", "acceptance_criteria", 10),
        ("blueprint execution_checklist", "execution_checklist", 10),
    ):
        item = _join_limited_values(label, _string_list_payload(payload.get(key), limit=limit))
        if item:
            lines.append(item)
    test_targets = [
        item
        for item in _string_list_payload(payload.get("target_files"), limit=40)
        if _path_looks_like_test_target(item)
    ]
    test_targets.extend(
        item
        for item in _string_list_payload(payload.get("scope_paths"), limit=40)
        if _path_looks_like_test_target(item) and item not in test_targets
    )
    if test_targets:
        lines.append(_join_limited_values("blueprint required test targets", test_targets[:12]))

    module_interface_contract = payload.get("module_interface_contract")
    if isinstance(module_interface_contract, dict) and module_interface_contract:
        contract_authority = str(
            module_interface_contract.get("authority") or "handoff_guidance_not_scope_authority"
        ).strip()
        lines.append(f"- module_interface_contract: authority={contract_authority}")
        modules = module_interface_contract.get("modules")
        if isinstance(modules, list):
            for module in modules[:10]:
                if not isinstance(module, dict):
                    continue
                path = str(module.get("path") or "").strip()
                actual_symbols = _string_list_payload(module.get("actual_public_symbols"), limit=8)
                planned_symbols = _string_list_payload(module.get("planned_public_symbols"), limit=8)
                consumes_symbols = _string_list_payload(module.get("consumes_symbols"), limit=8)
                symbol_source = str(module.get("symbol_source") or "").strip()
                confidence = module.get("symbol_confidence", module.get("selected_confidence"))
                role = str(module.get("role") or "").strip()
                if path and (actual_symbols or planned_symbols or consumes_symbols):
                    role_suffix = f" [{role}]" if role else ""
                    evidence_parts = [f"authority={contract_authority}"]
                    if symbol_source:
                        evidence_parts.append(f"symbol_source={symbol_source}")
                    if confidence is not None:
                        evidence_parts.append(f"confidence={confidence}")
                    evidence = " (" + ", ".join(evidence_parts) + ")"
                    if actual_symbols:
                        lines.append(f"  - {path}{role_suffix}: actual_exports {', '.join(actual_symbols)}{evidence}")
                    if planned_symbols:
                        planned_label = "planned_exports" if actual_symbols else "tentative_exports"
                        lines.append(f"  - {path}{role_suffix}: {planned_label} {', '.join(planned_symbols)}{evidence}")
                    if consumes_symbols:
                        lines.append(f"  - {path}{role_suffix}: consumes {', '.join(consumes_symbols)}{evidence}")
        rules = _string_list_payload(module_interface_contract.get("rules"), limit=4)
        for rule in rules:
            lines.append(f"  - interface rule: {rule}")

    shared_behavior_contract = payload.get("shared_behavior_contract")
    if isinstance(shared_behavior_contract, dict) and shared_behavior_contract:
        behavior_hash = str(shared_behavior_contract.get("shared_behavior_contract_hash") or "").strip()
        lines.append(f"- shared_behavior_contract: authority=chief_engineer hash={behavior_hash}")
        blueprint_task_id = str(payload.get("task_id") or payload.get("pm_task_id") or "").strip()
        task_bindings = shared_behavior_contract.get("task_bindings")
        bound_ids = set(
            _string_list_payload(
                task_bindings.get(blueprint_task_id) if isinstance(task_bindings, dict) else [],
                limit=12,
            )
        )
        invariants = shared_behavior_contract.get("invariants")
        if isinstance(invariants, list):
            for invariant in invariants:
                if not isinstance(invariant, dict):
                    continue
                invariant_id = str(invariant.get("invariant_id") or "").strip()
                if invariant_id not in bound_ids:
                    continue
                statement = str(invariant.get("statement") or "").strip()
                lines.append(f"  - behavior invariant {invariant_id}: {statement}")
                examples = invariant.get("verification_examples")
                if isinstance(examples, list) and examples and isinstance(examples[0], dict):
                    example = examples[0]
                    lines.append(
                        "    example: given="
                        + str(example.get("given") or "").strip()
                        + "; when="
                        + str(example.get("when") or "").strip()
                        + "; then="
                        + str(example.get("then") or "").strip()
                    )

    llm_blueprint = payload.get("llm_blueprint")
    if isinstance(llm_blueprint, dict) and llm_blueprint:
        authority = str(llm_blueprint.get("authority") or "advisory_only").strip()
        lines.append(f"- ce_llm_blueprint: consumed ({authority})")
        for label, key in (
            ("ce plan phases", "implementation_phases"),
            ("ce module boundaries", "module_boundaries"),
            ("ce verification", "verification_steps"),
            ("ce scope advisory", "scope_for_apply_advisory"),
            ("ce risks", "risk_flags"),
        ):
            item = _join_limited_values(label, _string_list_payload(llm_blueprint.get(key), limit=5))
            if item:
                lines.append(item)

    completeness = payload.get("contract_completeness")
    if isinstance(completeness, dict):
        missing = _string_list_payload(completeness.get("missing_fields"), limit=6)
        semantic_blockers = _string_list_payload(completeness.get("semantic_blockers"), limit=4)
        if missing:
            lines.append(_join_limited_values("blueprint missing_fields", missing))
        if semantic_blockers:
            lines.append(_join_limited_values("blueprint semantic_blockers", semantic_blockers))
        alignment = completeness.get("semantic_alignment")
        if isinstance(alignment, dict):
            expected_terms = _string_list_payload(alignment.get("expected_terms"), limit=8)
            planning_matches = _string_list_payload(alignment.get("planning_text_matches"), limit=8)
            advisory = _string_list_payload(alignment.get("advisory"), limit=4)
            if expected_terms:
                lines.append(_join_limited_values("blueprint expected_terms", expected_terms))
            if planning_matches:
                lines.append(_join_limited_values("blueprint planning_matches", planning_matches))
            if advisory:
                lines.append(_join_limited_values("blueprint advisory", advisory))

    return lines[:60]
