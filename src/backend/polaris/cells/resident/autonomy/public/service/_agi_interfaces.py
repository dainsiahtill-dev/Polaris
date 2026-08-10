"""Resident AGI evidence and platform interface projections."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polaris.cells.audit.diagnosis.public import QueryAuditDiagnosisTrailV1, query_audit_diagnosis_trail
from polaris.cells.audit.verdict.public import QueryAuditVerdictV1, create_artifact_service, query_audit_verdict
from polaris.cells.context.catalog.public import ContextCatalogService, SearchCellsQueryV1
from polaris.cells.context.engine.public import (
    ContextEngineError,
    QueryFinalProviderRequestAuditV1,
    query_final_provider_request_audit,
)
from polaris.cells.control_plane.run_ledger.public import (
    ReadRunLedgerProjectionQueryV1,
    ReadRunProvenanceBundleQueryV1,
    read_run_ledger_projection,
    read_run_provenance_bundle,
)
from polaris.cells.control_plane.verifier_policy.public import ReadVerifierPolicyQueryV1, read_verifier_policy
from polaris.cells.director.runtime.public import (
    QueryDirectorRepairAdvisoryPolicyV1,
    QueryDirectorRepairCoverageV1,
    QueryDirectorRepairStrategyCatalogV1,
    query_director_repair_advisory_policy,
    query_director_repair_coverage,
    query_director_repair_strategy_catalog,
)
from polaris.cells.director.tasking.public import resolve_task_execution_profile
from polaris.cells.resident.autonomy.internal.agi_audit_pack import resident_agi_context_snapshot_refs
from polaris.cells.resident.autonomy.internal.resident_runtime_service import get_resident_service
from polaris.cells.resident.autonomy.public import service as _service_pkg
from polaris.cells.resident.autonomy.public.contracts import QueryResidentAgiEvidenceInterfacesV1
from polaris.cells.runtime.artifact_store.public.service import resolve_artifact_path

from ._agi_gates import _resident_agi_capability_by_id, _resident_agi_select_decision_capability
from ._helpers import _merge_non_empty_strings


def _resident_agi_interface_base(*, interface_id: str, capability: dict[str, Any] | None) -> dict[str, Any]:
    capability_payload = dict(capability or {})
    return {
        "interface_id": interface_id,
        "capability": capability_payload,
        "name": str(capability_payload.get("name") or interface_id).strip() or interface_id,
        "category": str(capability_payload.get("category") or "unknown").strip() or "unknown",
        "access": str(capability_payload.get("access") or "unknown").strip() or "unknown",
        "contract_ref": str(capability_payload.get("contract_ref") or "").strip(),
        "risk_level": str(capability_payload.get("risk_level") or "unknown").strip() or "unknown",
        "endpoint": str(capability_payload.get("endpoint") or "").strip(),
        "available": False,
        "callable": False,
        "status": "unknown_interface" if not capability_payload else "metadata_only",
        "source": "resident.agi_capability_surface",
        "summary": {},
        "payload": {},
        "gaps": [],
        "recommended_next_action": "request_evidence",
    }


def _resident_agi_run_ledger_interface(
    *, workspace: str, run_id: str, max_runs: int, base: dict[str, Any]
) -> dict[str, Any]:
    try:
        projection = read_run_ledger_projection(
            ReadRunLedgerProjectionQueryV1(workspace=workspace, run_id=run_id, max_runs=max_runs)
        ).projection
    except (RuntimeError, ValueError, OSError) as exc:
        base.update(
            {
                "status": "unavailable",
                "source": "control_plane.run_ledger.public.read_run_ledger_projection",
                "gaps": [str(exc)],
                "recommended_next_action": "request_run_ledger_evidence",
            }
        )
        return base
    base.update(
        {
            "available": bool(projection.get("available")),
            "callable": True,
            "status": "available" if bool(projection.get("available")) else "unavailable",
            "source": "control_plane.run_ledger.public.read_run_ledger_projection",
            "summary": {
                "ok": bool(projection.get("ok")),
                "status": str(projection.get("status") or ""),
                "total": int(projection.get("total") or 0),
                "projected": int(projection.get("projected") or 0),
                "failed": int(projection.get("failed") or 0),
                "missing": int(projection.get("missing") or 0),
                "detail": str(projection.get("detail") or ""),
                "evidence_policy": projection.get("evidence_policy")
                if isinstance(projection.get("evidence_policy"), dict)
                else {},
            },
            "payload": {"projection": projection},
            "gaps": [] if bool(projection.get("available")) else ["run ledger projection is not available yet"],
            "recommended_next_action": "use_run_ledger_projection"
            if bool(projection.get("available"))
            else "request_run_ledger_evidence",
        }
    )
    return base


def _resident_agi_run_provenance_bundle_interface(
    *, workspace: str, run_id: str, base: dict[str, Any]
) -> dict[str, Any]:
    if not str(run_id or "").strip():
        base.update(
            {
                "status": "unavailable",
                "source": "control_plane.run_ledger.public.read_run_provenance_bundle",
                "gaps": ["run_id is required to read a run provenance bundle"],
                "recommended_next_action": "request_run_id_or_run_ledger_evidence",
            }
        )
        return base
    try:
        bundle = read_run_provenance_bundle(ReadRunProvenanceBundleQueryV1(workspace=workspace, run_id=run_id)).bundle
    except (RuntimeError, ValueError, OSError) as exc:
        base.update(
            {
                "status": "unavailable",
                "source": "control_plane.run_ledger.public.read_run_provenance_bundle",
                "gaps": [str(exc)],
                "recommended_next_action": "request_run_provenance_evidence",
            }
        )
        return base
    missing_authority_hashes = [
        key
        for key in ("pm_contract_hash", "ce_blueprint_hash", "handoff_decision_hash", "execution_envelope_hash")
        if str(bundle.get(key) or "").startswith("missing:")
    ]
    available = bool(bundle.get("bundle_id")) and (not missing_authority_hashes)
    base.update(
        {
            "available": available,
            "callable": True,
            "status": "available" if available else "empty",
            "source": "control_plane.run_ledger.public.read_run_provenance_bundle",
            "summary": {
                "bundle_id": str(bundle.get("bundle_id") or ""),
                "run_id": str(bundle.get("run_id") or ""),
                "task_id": str(bundle.get("task_id") or ""),
                "status": str(bundle.get("status") or ""),
                "final_status": str(bundle.get("final_status") or ""),
                "final_provider_request_count": len(bundle.get("final_provider_request_hashes") or []),
                "tool_receipt_count": len(bundle.get("tool_receipt_hashes") or []),
                "command_receipt_count": len(bundle.get("command_receipt_hashes") or []),
                "missing_authority_hashes": missing_authority_hashes,
            },
            "payload": {"bundle": bundle},
            "gaps": missing_authority_hashes,
            "recommended_next_action": "use_run_provenance_bundle"
            if available
            else "request_missing_provenance_evidence",
        }
    )
    return base


def _resident_agi_verifier_policy_interface(*, workspace: str, base: dict[str, Any]) -> dict[str, Any]:
    try:
        policy = read_verifier_policy(ReadVerifierPolicyQueryV1(workspace=workspace)).policy
    except (RuntimeError, ValueError, OSError) as exc:
        base.update(
            {
                "status": "unavailable",
                "source": "control_plane.verifier_policy.public.read_verifier_policy",
                "gaps": [str(exc)],
                "recommended_next_action": "request_verifier_policy_evidence",
            }
        )
        return base
    base.update(
        {
            "available": True,
            "callable": True,
            "status": "available",
            "source": "control_plane.verifier_policy.public.read_verifier_policy",
            "summary": {
                "enabled_modalities": list(policy.get("enabled_modalities") or []),
                "required_modalities": list(policy.get("required_modalities") or []),
                "policy_source": str(policy.get("source") or ""),
            },
            "payload": {"policy": policy},
            "gaps": [],
            "recommended_next_action": "use_verifier_policy_snapshot",
        }
    )
    return base


def _resident_agi_director_repair_strategy_catalog_interface(base: dict[str, Any]) -> dict[str, Any]:
    result = query_director_repair_strategy_catalog(QueryDirectorRepairStrategyCatalogV1())
    payload = result.to_dict()
    summary_raw = payload.get("summary")
    summary = summary_raw if isinstance(summary_raw, dict) else {}
    base.update(
        {
            "available": True,
            "callable": True,
            "status": "available",
            "source": "director.runtime.public.query_director_repair_strategy_catalog",
            "summary": {
                **summary,
                "schema_version": payload.get("schema_version"),
                "owner_cell": payload.get("owner_cell"),
                "access": payload.get("access"),
                "execution_boundary": payload.get("execution_boundary"),
                "chain": payload.get("chain"),
                "agi_execution_authority": bool(payload.get("agi_execution_authority")),
                "director_tool_execution_required": bool(payload.get("director_tool_execution_required")),
                "unknown_source_tool_policy": payload.get("unknown_source_tool_policy") or "fail_closed_high_risk",
            },
            "payload": payload,
            "gaps": [],
            "recommended_next_action": "use_director_repair_strategy_catalog_as_read_only_evidence",
        }
    )
    return base


def _resident_agi_audit_diagnosis_interface(
    *, workspace: str, run_id: str, task_id: str, base: dict[str, Any]
) -> dict[str, Any]:
    result = query_audit_diagnosis_trail(
        QueryAuditDiagnosisTrailV1(workspace=workspace, run_id=run_id or None, task_id=task_id or None, limit=100)
    )
    payload = dict(result.payload)
    base.update(
        {
            "available": bool(result.ok),
            "callable": True,
            "status": result.status if result.ok else "unavailable",
            "source": "audit.diagnosis.public.query_audit_diagnosis_trail",
            "summary": {
                "ok": result.ok,
                "status": result.status,
                "total": int(payload.get("total") or 0),
                "run_id": str(payload.get("run_id") or ""),
                "task_id": str(payload.get("task_id") or ""),
            },
            "payload": payload,
            "gaps": []
            if result.ok
            else [str(result.error_message or result.error_code or "audit diagnosis trail unavailable")],
            "recommended_next_action": "use_audit_diagnosis_trail" if result.ok else "request_audit_diagnosis_evidence",
        }
    )
    return base


def _resident_agi_audit_verdict_interface(
    *, workspace: str, run_id: str, task_id: str, base: dict[str, Any]
) -> dict[str, Any]:
    result = query_audit_verdict(
        QueryAuditVerdictV1(workspace=workspace, run_id=run_id or None, task_id=task_id or None, include_artifacts=True)
    )
    details = dict(result.details)
    base.update(
        {
            "available": bool(result.ok),
            "callable": True,
            "status": result.status if result.ok else "unavailable",
            "source": "audit.verdict.public.query_audit_verdict",
            "summary": {
                "ok": result.ok,
                "status": result.status,
                "verdict": result.verdict or "",
                "change_count": int(details.get("change_count") or 0),
                "review_count": int(details.get("review_count") or 0),
                "task_review_status": str(details.get("task_review_status") or ""),
            },
            "payload": {"details": details, "verdict": result.verdict},
            "gaps": []
            if result.ok
            else [str(result.error_message or result.error_code or "audit verdict unavailable")],
            "recommended_next_action": "use_audit_verdict_snapshot" if result.ok else "request_audit_verdict_evidence",
        }
    )
    return base


def _resident_agi_context_catalog_interface(
    *, workspace: str, decision_type: str, base: dict[str, Any]
) -> dict[str, Any]:
    query_text = " ".join(
        str(token or "").strip()
        for token in (decision_type, base.get("contract_ref"), base.get("category"), base.get("name"))
        if str(token or "").strip()
    )
    try:
        result = ContextCatalogService(workspace).search(SearchCellsQueryV1(query=query_text, limit=5))
    except (RuntimeError, ValueError, OSError) as exc:
        base.update(
            {
                "status": "unavailable",
                "source": "context.catalog.public.ContextCatalogService.search",
                "gaps": [str(exc)],
                "recommended_next_action": "sync_context_catalog_before_search",
            }
        )
        return base
    descriptors = [
        {
            "cell_id": item.cell_id,
            "title": item.title,
            "purpose": item.purpose,
            "domain": item.domain,
            "kind": item.kind,
            "visibility": item.visibility,
            "stateful": item.stateful,
            "owner": item.owner,
            "capability_summary": item.capability_summary,
        }
        for item in result.descriptors
    ]
    base.update(
        {
            "available": result.total > 0,
            "callable": True,
            "status": "available" if result.total > 0 else "empty",
            "source": "context.catalog.public.ContextCatalogService.search",
            "summary": {"query": query_text, "total": result.total},
            "payload": {"descriptors": descriptors},
            "gaps": [] if result.total > 0 else ["context catalog has no matching descriptors"],
            "recommended_next_action": "use_catalog_descriptors"
            if result.total > 0
            else "sync_context_catalog_before_search",
        }
    )
    return base


def _resident_agi_context_engine_interface(
    *,
    workspace: str,
    decision_type: str,
    run_id: str,
    task_id: str,
    context_refs: tuple[str, ...],
    evidence_refs: tuple[str, ...],
    base: dict[str, Any],
) -> dict[str, Any]:
    query_text = " ".join(
        _merge_non_empty_strings(
            [
                decision_type,
                task_id,
                str(base.get("contract_ref") or ""),
                str(base.get("category") or ""),
                str(base.get("name") or ""),
            ],
            list(context_refs),
            list(evidence_refs),
        )
    )
    try:
        context_payload = _service_pkg.get_anthropomorphic_context_v2(
            project_root=workspace,
            role="resident_agi",
            query=query_text or "Resident AGI evidence resolution",
            step=0,
            run_id=run_id or "resident-agi-evidence",
            phase=f"resident_agi_{decision_type or 'evidence'}",
        )
    except (ContextEngineError, RuntimeError, ValueError, OSError) as exc:
        base.update(
            {
                "status": "unavailable",
                "source": "context.engine.public.get_anthropomorphic_context_v2",
                "gaps": [str(exc)],
                "recommended_next_action": "request_context_engine_snapshot_or_catalog_search",
            }
        )
        return base
    context_text = str(context_payload.get("anthropomorphic_context") or "")
    context_os_summary_raw = context_payload.get("context_os_summary")
    context_os_summary = context_os_summary_raw if isinstance(context_os_summary_raw, dict) else {}
    context_pack = context_payload.get("context_pack")
    raw_items = getattr(context_pack, "items", ()) if context_pack is not None else ()
    raw_item_list = list(raw_items or ())
    context_items = [
        {
            "id": str(getattr(item, "id", "") or ""),
            "kind": str(getattr(item, "kind", "") or ""),
            "provider": str(getattr(item, "provider", "") or ""),
            "priority": getattr(item, "priority", None),
            "reason": str(getattr(item, "reason", "") or ""),
        }
        for item in raw_item_list[:8]
    ]
    prompt_context = context_payload.get("prompt_context_obj")
    prompt_context_payload = {
        "run_id": str(getattr(prompt_context, "run_id", "") or ""),
        "phase": str(getattr(prompt_context, "phase", "") or ""),
        "step": getattr(prompt_context, "step", None),
        "persona_id": str(getattr(prompt_context, "persona_id", "") or ""),
        "token_usage_estimate": getattr(prompt_context, "token_usage_estimate", None),
    }
    available = bool(context_text or context_items or context_os_summary)
    base.update(
        {
            "available": available,
            "callable": True,
            "status": "available" if available else "empty",
            "source": "context.engine.public.get_anthropomorphic_context_v2",
            "summary": {
                "role": "resident_agi",
                "query": query_text[:240],
                "context_item_count": len(raw_item_list),
                "context_preview_chars": min(len(context_text), 1200),
                "context_os_current_goal": str(context_os_summary.get("current_goal") or ""),
                "token_usage_estimate": prompt_context_payload["token_usage_estimate"],
            },
            "payload": {
                "context_os_summary": context_os_summary,
                "prompt_context": prompt_context_payload,
                "context_items": context_items,
                "anthropomorphic_context_preview": context_text[:1200],
            },
            "gaps": [] if available else ["context engine returned no role context items"],
            "recommended_next_action": "use_resolved_role_context" if available else "request_context_catalog_search",
        }
    )
    return base


def _resident_agi_read_json_artifact(*, workspace: str, relative_path: str) -> tuple[dict[str, Any], str, str]:
    try:
        resolved = resolve_artifact_path(workspace, "", relative_path)
    except (RuntimeError, ValueError, OSError) as exc:
        return ({}, "", str(exc))
    if not resolved:
        return ({}, "", "artifact path could not be resolved")
    path = Path(resolved)
    if not path.is_file():
        return ({}, str(path), "")
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw) if raw.strip() else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return ({}, str(path), str(exc))
    if not isinstance(payload, dict):
        return ({}, str(path), "artifact payload is not a JSON object")
    return (payload, str(path), "")


def _resident_agi_chief_engineer_blueprint_interface(*, workspace: str, base: dict[str, Any]) -> dict[str, Any]:
    payload, path, error = _resident_agi_read_json_artifact(
        workspace=workspace, relative_path="runtime/contracts/chief_engineer.blueprint.json"
    )
    if error:
        base.update(
            {
                "callable": True,
                "status": "unavailable",
                "source": "runtime.artifact_store.resolve_artifact_path",
                "summary": {"path": path},
                "payload": {"path": path},
                "gaps": [error],
                "recommended_next_action": "repair_or_regenerate_chief_engineer_blueprint",
            }
        )
        return base
    if not payload:
        base.update(
            {
                "callable": True,
                "status": "empty",
                "source": "runtime.artifact_store.resolve_artifact_path",
                "summary": {"path": path},
                "payload": {"path": path},
                "gaps": ["runtime/contracts/chief_engineer.blueprint.json is not present"],
                "recommended_next_action": "run_chief_engineer_preflight_before_director_execution",
            }
        )
        return base
    task_updates = payload.get("task_updates")
    architecture_decisions = payload.get("architecture_decisions")
    base.update(
        {
            "available": True,
            "callable": True,
            "status": "available",
            "source": "runtime/contracts/chief_engineer.blueprint.json",
            "summary": {
                "path": path,
                "schema_version": str(payload.get("schema_version") or ""),
                "role": str(payload.get("role") or payload.get("actor") or "ChiefEngineer"),
                "task_update_count": len(task_updates) if isinstance(task_updates, list) else 0,
                "architecture_decision_count": len(architecture_decisions)
                if isinstance(architecture_decisions, list)
                else 0,
                "reason": str(payload.get("reason") or ""),
                "summary": str(payload.get("summary") or "")[:240],
            },
            "payload": payload,
            "gaps": [],
            "recommended_next_action": "use_chief_engineer_blueprint_for_architecture_decision",
        }
    )
    return base


def _resident_agi_find_profile_payload(payload: Any) -> dict[str, Any]:
    stack: list[Any] = [payload]
    visited = 0
    while stack and visited < 500:
        visited += 1
        current = stack.pop()
        if isinstance(current, dict):
            for key in ("task_execution_profile", "director_execution_profile"):
                nested = current.get(key)
                if isinstance(nested, dict) and nested:
                    return dict(nested)
            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
    return {}


def _resident_agi_candidate_paths_from_refs(*refs: str) -> list[str]:
    paths: list[str] = []
    for ref in refs:
        value = str(ref or "").strip().replace("\\", "/")
        if not value or value.startswith(("http://", "https://")):
            continue
        if value.startswith(("runtime/", "workspace/", "config/")):
            continue
        if "/" not in value and "." not in value:
            continue
        paths.append(value)
    return paths[:12]


def _resident_agi_task_execution_profile_interface(
    *,
    workspace: str,
    decision_type: str,
    run_id: str,
    task_id: str,
    audit_pack: dict[str, Any],
    context_refs: tuple[str, ...],
    evidence_refs: tuple[str, ...],
    base: dict[str, Any],
) -> dict[str, Any]:
    existing_profile = _resident_agi_find_profile_payload(audit_pack)
    if existing_profile:
        profile_payload = existing_profile
        source = "resident.agi_audit_pack.task_execution_profile"
        computed_from_current_query = False
    else:
        refs = _merge_non_empty_strings(list(context_refs), list(evidence_refs))
        try:
            profile = resolve_task_execution_profile(
                subject=task_id or decision_type or "Resident AGI evidence query",
                description=" ".join(refs),
                metadata={
                    "source": "resident.agi_evidence_interface",
                    "decision_type": decision_type,
                    "run_id": run_id,
                    "task_id": task_id,
                },
                target_files=_resident_agi_candidate_paths_from_refs(*refs),
                workspace=workspace,
            )
        except (RuntimeError, ValueError, OSError) as exc:
            base.update(
                {
                    "callable": True,
                    "status": "unavailable",
                    "source": "director.tasking.resolve_task_execution_profile",
                    "gaps": [str(exc)],
                    "recommended_next_action": "request_director_task_execution_profile_evidence",
                }
            )
            return base
        profile_payload = profile.to_dict()
        source = "director.tasking.resolve_task_execution_profile"
        computed_from_current_query = True
    base.update(
        {
            "available": True,
            "callable": True,
            "status": "available",
            "source": source,
            "summary": {
                "schema_version": str(profile_payload.get("schema_version") or ""),
                "task_type": str(profile_payload.get("task_type") or ""),
                "phase": str(profile_payload.get("phase") or ""),
                "project_type": str(profile_payload.get("project_type") or ""),
                "language": str(profile_payload.get("language") or ""),
                "framework": str(profile_payload.get("framework") or ""),
                "temperature_phase": str(profile_payload.get("temperature_phase") or ""),
                "temperature": profile_payload.get("temperature"),
                "computed_from_current_query": computed_from_current_query,
            },
            "payload": {"profile": profile_payload},
            "gaps": [],
            "recommended_next_action": "use_task_execution_profile_for_prompt_temperature_and_output_contract",
        }
    )
    return base


def _resident_agi_runtime_events_interface(*, workspace: str, base: dict[str, Any]) -> dict[str, Any]:
    try:
        service = create_artifact_service(workspace)
        events = service.read_runtime_events(limit=50)
        events_path = service.get_runtime_events_path()
    except (RuntimeError, ValueError, OSError) as exc:
        base.update(
            {
                "callable": True,
                "status": "unavailable",
                "source": "audit.verdict.public.create_artifact_service.read_runtime_events",
                "gaps": [str(exc)],
                "recommended_next_action": "repair_runtime_events_artifact_or_use_run_ledger",
            }
        )
        return base
    available = bool(events)
    recent_event_types = [
        str(event.get("type") or event.get("event_type") or event.get("name") or "").strip()
        for event in events[-8:]
        if isinstance(event, dict)
    ]
    base.update(
        {
            "available": available,
            "callable": True,
            "status": "available" if available else "empty",
            "source": "audit.verdict.public.ArtifactService.read_runtime_events",
            "summary": {
                "path": events_path,
                "event_count": len(events),
                "recent_event_types": [item for item in recent_event_types if item],
            },
            "payload": {"path": events_path, "events": events},
            "gaps": [] if available else ["runtime/events/runtime.events.jsonl has no readable events"],
            "recommended_next_action": "use_runtime_event_stream_evidence"
            if available
            else "use_run_ledger_projection",
        }
    )
    return base


def _resident_agi_contextos_final_request_interface(
    *, workspace: str, audit_pack: dict[str, Any], base: dict[str, Any]
) -> dict[str, Any]:
    evidence_refs_raw = audit_pack.get("evidence_refs")
    evidence_refs = evidence_refs_raw if isinstance(evidence_refs_raw, list) else []
    context_refs = resident_agi_context_snapshot_refs(evidence_refs)
    if not context_refs:
        base.update(
            {
                "available": False,
                "callable": True,
                "status": "metadata_only",
                "source": "context.engine.public.query_final_provider_request_audit",
                "summary": {"context_snapshot_ref_count": 0, "context_snapshot_refs": []},
                "payload": {"context_snapshot_refs": []},
                "gaps": [
                    "no ContextOS snapshot hash or runtime/contexts/<hash> reference is present in the audit pack"
                ],
                "recommended_next_action": "request_final_request_snapshot",
            }
        )
        return base
    last_result_payload: dict[str, Any] = {}
    last_error = ""
    for context_ref in context_refs:
        result = query_final_provider_request_audit(
            QueryFinalProviderRequestAuditV1(workspace=workspace, context_snapshot_ref=context_ref)
        )
        if result.ok:
            payload = dict(result.payload)
            final_audit = payload.get("final_request_context_audit")
            base.update(
                {
                    "available": True,
                    "callable": True,
                    "status": "available",
                    "source": "context.engine.public.query_final_provider_request_audit",
                    "summary": {
                        "context_snapshot_ref_count": len(context_refs),
                        "selected_context_snapshot_ref": context_ref,
                        "final_request_token_estimate": final_audit.get("final_request_token_estimate")
                        if isinstance(final_audit, dict)
                        else None,
                        "tool_schema_count": payload.get("provider_request", {}).get("tool_schema_count")
                        if isinstance(payload.get("provider_request"), dict)
                        else None,
                    },
                    "payload": payload,
                    "gaps": [],
                    "recommended_next_action": "use_final_provider_request_audit",
                }
            )
            return base
        last_result_payload = dict(result.payload)
        last_error = result.error_code or result.error_message or result.status
    base.update(
        {
            "available": False,
            "callable": True,
            "status": "unavailable",
            "source": "context.engine.public.query_final_provider_request_audit",
            "summary": {
                "context_snapshot_ref_count": len(context_refs),
                "context_snapshot_refs": context_refs[:10],
                "last_error": last_error,
            },
            "payload": {"context_snapshot_refs": context_refs, "last_result": last_result_payload},
            "gaps": ["no referenced context snapshot contains readable final provider request audit evidence"],
            "recommended_next_action": "request_final_request_snapshot",
        }
    )
    return base


def _resident_agi_director_repair_advisory_policy_interface(base: dict[str, Any]) -> dict[str, Any]:
    result = query_director_repair_advisory_policy(QueryDirectorRepairAdvisoryPolicyV1())
    payload = result.to_dict()
    summary_raw = payload.get("summary")
    summary = summary_raw if isinstance(summary_raw, dict) else {}
    base.update(
        {
            "available": True,
            "callable": True,
            "status": "available",
            "source": "director.runtime.public.query_director_repair_advisory_policy",
            "summary": {
                **summary,
                "schema_version": payload.get("schema_version"),
                "owner_cell": payload.get("owner_cell"),
                "access": payload.get("access"),
                "execution_boundary": payload.get("execution_boundary"),
                "agi_execution_authority": bool(payload.get("agi_execution_authority")),
                "writes_allowed": bool(payload.get("writes_allowed")),
                "registration_allowed": bool(payload.get("registration_allowed")),
                "authoritative_receipts_allowed": bool(payload.get("authoritative_receipts_allowed")),
            },
            "payload": payload,
            "gaps": [],
            "recommended_next_action": "use_repair_advisory_policy_before_accepting_agi_suggested_rules",
        }
    )
    return base


def _resident_agi_repair_diagnostic_candidates(
    audit_pack: dict[str, Any], *, evidence_refs: tuple[str, ...] = (), context_refs: tuple[str, ...] = ()
) -> tuple[str, ...]:
    candidates: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            token = value.strip()
            if token and _looks_like_repair_diagnostic(token):
                candidates.append(token)
            return
        if isinstance(value, list):
            for item in value:
                collect(item)
            return
        if not isinstance(value, dict):
            return
        for key, nested in value.items():
            key_text = str(key or "").lower()
            if key_text in {
                "artifact_quality_errors",
                "quality_errors",
                "diagnostics",
                "errors",
                "compiler_errors",
            } or key_text in {"actual_outcome", "expected_outcome", "verifier", "quality", "repair", "metadata"}:
                collect(nested)

    for ref in (*context_refs, *evidence_refs):
        collect(ref)
    collect(audit_pack.get("run_ledger_summary"))
    collect(audit_pack.get("recent_decisions"))
    return tuple(dict.fromkeys(candidates))[:50]


def _looks_like_repair_diagnostic(value: str) -> bool:
    lowered = value.lower()
    return any(
        marker in lowered
        for marker in (
            "error ts",
            "ts1005",
            "error[",
            ".go:",
            "artifact_quality",
            "syntax check failed",
            "cannot find",
            "unresolved",
            "unlinked crate",
            "import path must be string",
        )
    )


def _resident_agi_director_repair_coverage_interface(
    *, audit_pack: dict[str, Any], context_refs: tuple[str, ...], evidence_refs: tuple[str, ...], base: dict[str, Any]
) -> dict[str, Any]:
    diagnostics = _resident_agi_repair_diagnostic_candidates(
        audit_pack, context_refs=context_refs, evidence_refs=evidence_refs
    )
    result = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=diagnostics))
    payload = result.to_dict()
    base.update(
        {
            "available": True,
            "callable": True,
            "status": "available",
            "source": "director.runtime.public.query_director_repair_coverage",
            "summary": {
                "schema_version": payload.get("schema_version"),
                "owner_cell": payload.get("owner_cell"),
                "access": payload.get("access"),
                "diagnostic_candidate_count": len(diagnostics),
                "total_diagnostics": payload.get("total_diagnostics"),
                "covered_diagnostic_count": payload.get("covered_diagnostic_count"),
                "uncovered_diagnostic_count": payload.get("uncovered_diagnostic_count"),
                "agi_execution_authority": bool(payload.get("agi_execution_authority")),
                "execution_boundary": payload.get("execution_boundary"),
            },
            "payload": payload,
            "gaps": []
            if diagnostics
            else ["no repair diagnostics were found in current AGI evidence refs or audit pack decisions"],
            "recommended_next_action": "use_repair_coverage_to_choose_retry_escalate_or_suggest_rule",
        }
    )
    return base


def _resident_agi_audit_pack_with_current_refs(
    audit_pack: dict[str, Any], *, context_refs: tuple[str, ...] = (), evidence_refs: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Return an audit pack view that prioritizes refs from the current decision."""
    audit_refs_raw = audit_pack.get("evidence_refs")
    audit_refs: list[Any] = audit_refs_raw if isinstance(audit_refs_raw, list) else []
    merged_refs = _merge_non_empty_strings(list(context_refs), list(evidence_refs), audit_refs)
    if merged_refs == audit_refs:
        return audit_pack
    payload = dict(audit_pack)
    payload["evidence_refs"] = merged_refs
    return payload


def _resident_agi_metadata_only_interface(base: dict[str, Any]) -> dict[str, Any]:
    access = str(base.get("access") or "").strip()
    contract_ref = str(base.get("contract_ref") or "").strip()
    if "execute" in access:
        base.update(
            {
                "status": "governed_execute_only",
                "source": "resident.agi_capability_surface",
                "gaps": ["this endpoint requires a governed command and is not executed by read-only evidence query"],
                "recommended_next_action": "request_governed_execution_if_read_evidence_is_insufficient",
            }
        )
        return base
    if contract_ref in {"audit.diagnosis", "audit.verdict", "audit.evidence.bundle", "context.engine"}:
        base.update(
            {
                "status": "needs_public_facade",
                "source": "resident.agi_capability_surface",
                "gaps": [f"{contract_ref} has no safe Resident AGI read facade wired here yet"],
                "recommended_next_action": "request_platform_facade_or_use_existing_audit_pack_summary",
            }
        )
    return base


def _resident_agi_evidence_interface_group_id(interface: dict[str, Any]) -> str:
    interface_id = str(interface.get("interface_id") or "").strip()
    category = str(interface.get("category") or "").strip()
    contract_ref = str(interface.get("contract_ref") or "").strip()
    if interface_id.startswith("director.") or category.startswith("director_repair"):
        return "director_repair"
    if interface_id.startswith("verifier.") or contract_ref.startswith("control_plane.verifier"):
        return "verifier"
    if interface_id.startswith("audit.") or contract_ref.startswith("audit."):
        return "audit"
    if interface_id.startswith("context.") or contract_ref.startswith("context."):
        return "context"
    if interface_id.startswith("contextos.") or contract_ref == "roles.final_request_context_audit":
        return "llm_context"
    if (
        interface_id.startswith("run_ledger.")
        or interface_id.startswith("run_provenance_bundle.")
        or contract_ref in {"control_plane.run_ledger", "control_plane.run_provenance_bundle"}
    ):
        return "run_ledger"
    if "execute" in str(interface.get("access") or "").strip().lower():
        return "governed_execution"
    return "other"


def _resident_agi_evidence_group_name(group_id: str) -> str:
    return {
        "audit": "Audit",
        "context": "Context",
        "director_repair": "Director repair",
        "governed_execution": "Governed execution",
        "llm_context": "LLM context",
        "run_ledger": "Run ledger",
        "verifier": "Verifier",
        "other": "Other",
    }.get(group_id, group_id)


def _resident_agi_evidence_capability_matrix(
    *,
    decision_type: str,
    selected_decision_capability: dict[str, Any],
    interfaces: list[dict[str, Any]],
    required_interface_ids: list[str],
    optional_interface_ids: list[str],
    audit_pack: dict[str, Any],
) -> dict[str, Any]:
    decision_profile_raw = audit_pack.get("decision_profile")
    decision_profile: dict[str, Any] = decision_profile_raw if isinstance(decision_profile_raw, dict) else {}
    recommendations_raw = decision_profile.get("evidence_interface_recommendations")
    recommendations = recommendations_raw if isinstance(recommendations_raw, list) else []
    recommendation_by_id = {
        str(item.get("capability_id") or "").strip(): item
        for item in recommendations
        if isinstance(item, dict) and str(item.get("capability_id") or "").strip()
    }
    required_set = set(required_interface_ids)
    optional_set = set(optional_interface_ids)
    rows: list[dict[str, Any]] = []
    groups: dict[str, dict[str, Any]] = {}
    missing_required_interface_ids: list[str] = []
    status_counts: dict[str, int] = {}
    for interface in interfaces:
        interface_id = str(interface.get("interface_id") or "").strip()
        status = str(interface.get("status") or "unknown").strip() or "unknown"
        available = bool(interface.get("available")) or status == "available"
        callable_now = bool(interface.get("callable"))
        access = str(interface.get("access") or "").strip()
        risk_level = str(interface.get("risk_level") or "").strip()
        group_id = _resident_agi_evidence_interface_group_id(interface)
        recommendation = recommendation_by_id.get(interface_id, {})
        required = interface_id in required_set
        optional = interface_id in optional_set
        recommended_now = bool(recommendation.get("recommended_now")) or required
        gaps_raw = interface.get("gaps")
        gaps = (
            [str(item or "").strip() for item in gaps_raw if str(item or "").strip()]
            if isinstance(gaps_raw, list)
            else []
        )
        if required and (not available):
            missing_required_interface_ids.append(interface_id)
        status_counts[status] = status_counts.get(status, 0) + 1
        row = {
            "interface_id": interface_id,
            "name": str(interface.get("name") or interface_id).strip() or interface_id,
            "group_id": group_id,
            "group_name": _resident_agi_evidence_group_name(group_id),
            "required": required,
            "optional": optional,
            "recommended_now": recommended_now,
            "available": available,
            "callable": callable_now,
            "status": status,
            "source": str(interface.get("source") or "").strip(),
            "access": access,
            "risk_level": risk_level,
            "contract_ref": str(interface.get("contract_ref") or "").strip(),
            "recommended_next_action": str(interface.get("recommended_next_action") or "").strip(),
            "priority": int(recommendation.get("priority") or 100),
            "reason": str(recommendation.get("reason") or "").strip(),
            "gap_count": len(gaps),
            "gaps": gaps[:5],
        }
        rows.append(row)
        group = groups.setdefault(
            group_id,
            {
                "group_id": group_id,
                "name": _resident_agi_evidence_group_name(group_id),
                "interface_ids": [],
                "total": 0,
                "available": 0,
                "required": 0,
                "missing_required": 0,
                "recommended_now": 0,
                "high_risk": 0,
                "governed_execute": 0,
            },
        )
        group["interface_ids"].append(interface_id)
        group["total"] += 1
        group["available"] += 1 if available else 0
        group["required"] += 1 if required else 0
        group["missing_required"] += 1 if required and (not available) else 0
        group["recommended_now"] += 1 if recommended_now else 0
        group["high_risk"] += 1 if risk_level.lower() == "high" else 0
        group["governed_execute"] += 1 if "execute" in access.lower() else 0
    return {
        "schema_version": "resident.agi_evidence_capability_matrix.v1",
        "workspace_evidence_source": "resident.autonomy.public.query_resident_agi_evidence_interfaces",
        "decision_type": decision_type,
        "selected_decision_id": str(selected_decision_capability.get("decision_id") or decision_type).strip(),
        "rows": sorted(
            rows, key=lambda item: (int(item["priority"]), str(item["group_id"]), str(item["interface_id"]))
        ),
        "groups": sorted(groups.values(), key=lambda item: str(item["group_id"])),
        "summary": {
            "total": len(rows),
            "available": sum(1 for item in rows if bool(item["available"])),
            "required": len(required_set),
            "required_available": sum(1 for item in rows if bool(item["required"]) and bool(item["available"])),
            "missing_required": len(missing_required_interface_ids),
            "missing_required_interface_ids": missing_required_interface_ids,
            "recommended_now": sum(1 for item in rows if bool(item["recommended_now"])),
            "callable": sum(1 for item in rows if bool(item["callable"])),
            "high_risk": sum(1 for item in rows if str(item["risk_level"]).lower() == "high"),
            "governed_execute": sum(1 for item in rows if "execute" in str(item["access"]).lower()),
            "status_counts": status_counts,
            "advisory_only": True,
            "authoritative": False,
            "agi_execution_authority": False,
        },
    }


def query_resident_agi_evidence_interfaces(query: QueryResidentAgiEvidenceInterfacesV1) -> dict[str, Any]:
    """Return the evidence interfaces a Resident AGI turn can safely inspect."""
    status_payload = get_resident_service(query.workspace).get_status(include_details=True)
    audit_pack = _service_pkg.build_resident_agi_audit_pack(
        workspace=query.workspace, status_payload=status_payload, decision_limit=query.decision_limit
    )
    audit_pack = _resident_agi_audit_pack_with_current_refs(
        audit_pack, context_refs=query.context_refs, evidence_refs=query.evidence_refs
    )
    selected_decision_capability = _resident_agi_select_decision_capability(
        decision_type=query.decision_type, audit_pack=audit_pack
    )
    selected_required_interfaces = [
        str(item or "").strip()
        for item in selected_decision_capability.get("required_evidence_interfaces", [])
        if str(item or "").strip()
    ]
    selected_optional_interfaces = [
        str(item or "").strip()
        for item in selected_decision_capability.get("optional_evidence_interfaces", [])
        if str(item or "").strip()
    ]
    requested_interface_ids = _merge_non_empty_strings(
        list(query.interface_ids),
        selected_required_interfaces if not query.interface_ids else [],
        selected_optional_interfaces if not query.interface_ids else [],
    )
    capability_by_id = _resident_agi_capability_by_id(audit_pack)
    interfaces: list[dict[str, Any]] = []
    for interface_id in requested_interface_ids:
        base = _resident_agi_interface_base(interface_id=interface_id, capability=capability_by_id.get(interface_id))
        if interface_id == "run_ledger.read":
            item = _resident_agi_run_ledger_interface(
                workspace=query.workspace, run_id=query.run_id, max_runs=query.max_runs, base=base
            )
        elif interface_id == "run_provenance_bundle.read":
            item = _resident_agi_run_provenance_bundle_interface(
                workspace=query.workspace, run_id=query.run_id, base=base
            )
        elif interface_id == "audit.diagnosis.read":
            item = _resident_agi_audit_diagnosis_interface(
                workspace=query.workspace, run_id=query.run_id, task_id=query.task_id, base=base
            )
        elif interface_id == "audit.verdict.read":
            item = _resident_agi_audit_verdict_interface(
                workspace=query.workspace, run_id=query.run_id, task_id=query.task_id, base=base
            )
        elif interface_id == "verifier.policy.read":
            item = _resident_agi_verifier_policy_interface(workspace=query.workspace, base=base)
        elif interface_id == "context.catalog.search":
            item = _resident_agi_context_catalog_interface(
                workspace=query.workspace, decision_type=query.decision_type, base=base
            )
        elif interface_id == "context.engine.resolve":
            item = _resident_agi_context_engine_interface(
                workspace=query.workspace,
                decision_type=query.decision_type,
                run_id=query.run_id,
                task_id=query.task_id,
                context_refs=query.context_refs,
                evidence_refs=query.evidence_refs,
                base=base,
            )
        elif interface_id == "contextos.final_request_audit.read":
            item = _resident_agi_contextos_final_request_interface(
                workspace=query.workspace, audit_pack=audit_pack, base=base
            )
        elif interface_id == "task.execution_profile.read":
            item = _resident_agi_task_execution_profile_interface(
                workspace=query.workspace,
                decision_type=query.decision_type,
                run_id=query.run_id,
                task_id=query.task_id,
                audit_pack=audit_pack,
                context_refs=query.context_refs,
                evidence_refs=query.evidence_refs,
                base=base,
            )
        elif interface_id == "chief_engineer.blueprint.read":
            item = _resident_agi_chief_engineer_blueprint_interface(workspace=query.workspace, base=base)
        elif interface_id == "director.deterministic_repair_strategy_catalog.read":
            item = _resident_agi_director_repair_strategy_catalog_interface(base)
        elif interface_id == "director.repair_coverage.read":
            item = _resident_agi_director_repair_coverage_interface(
                audit_pack=audit_pack, context_refs=query.context_refs, evidence_refs=query.evidence_refs, base=base
            )
        elif interface_id == "director.repair_advisory_policy.read":
            item = _resident_agi_director_repair_advisory_policy_interface(base)
        elif interface_id == "runtime.events.read":
            item = _resident_agi_runtime_events_interface(workspace=query.workspace, base=base)
        else:
            item = _resident_agi_metadata_only_interface(base)
        interfaces.append(item)
    required_set = set(selected_required_interfaces)
    missing_required = [
        str(item.get("interface_id") or "")
        for item in interfaces
        if str(item.get("interface_id") or "") in required_set and str(item.get("status") or "") != "available"
    ]
    status_counts: dict[str, int] = {}
    for item in interfaces:
        status = str(item.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    capability_matrix = _resident_agi_evidence_capability_matrix(
        decision_type=query.decision_type,
        selected_decision_capability=selected_decision_capability,
        interfaces=interfaces,
        required_interface_ids=selected_required_interfaces,
        optional_interface_ids=selected_optional_interfaces,
        audit_pack=audit_pack,
    )
    return {
        "schema_version": "resident.agi_evidence_interfaces.v1",
        "workspace": query.workspace,
        "decision_type": query.decision_type,
        "run_id": query.run_id,
        "task_id": query.task_id,
        "context_refs": list(query.context_refs),
        "evidence_refs": list(query.evidence_refs),
        "selected_decision_capability": selected_decision_capability,
        "required_evidence_interfaces": selected_required_interfaces,
        "optional_evidence_interfaces": selected_optional_interfaces,
        "requested_interface_ids": requested_interface_ids,
        "interfaces": interfaces,
        "capability_matrix": capability_matrix,
        "summary": {
            "total": len(interfaces),
            "available": status_counts.get("available", 0),
            "metadata_only": status_counts.get("metadata_only", 0),
            "needs_public_facade": status_counts.get("needs_public_facade", 0),
            "governed_execute_only": status_counts.get("governed_execute_only", 0),
            "unavailable": status_counts.get("unavailable", 0),
            "empty": status_counts.get("empty", 0),
            "unknown_interface": status_counts.get("unknown_interface", 0),
            "missing_required_interface_ids": missing_required,
        },
        "audit_pack_ref": {
            "schema_version": audit_pack.get("schema_version"),
            "evidence_gate_status": (audit_pack.get("evidence_gate") or {}).get("status")
            if isinstance(audit_pack.get("evidence_gate"), dict)
            else "",
            "hard_rule_gate_status": (audit_pack.get("hard_rule_gate") or {}).get("status")
            if isinstance(audit_pack.get("hard_rule_gate"), dict)
            else "",
        },
    }
