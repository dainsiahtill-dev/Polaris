"""Aggregate task-market, governance, and distilled-knowledge pack builders."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from polaris.cells.control_plane.run_ledger.public import (
    suspected_files_from_failure_evidence_payload,
)
from polaris.cells.roles.runtime.public.aggregate_chat._helpers import (
    _aggregate_objective_from_messages,
)
from polaris.cells.roles.runtime.public.aggregate_chat._plan import (
    _aggregate_plan_failure_evidence_payload,
)
from polaris.cells.roles.runtime.public.aggregate_chat._specs import (
    _AGGREGATE_MODEL_ID,
    _BACKEND_ROOT,
)
from polaris.cells.roles.runtime.public.contracts import (
    AggregateChatCompletionsCommandV1,
    AggregateRoleLobeV1,
    AggregateRolePlanResultV1,
    RoleExecutionResultV1,
)


def _build_aggregate_task_market_projection_pack(
    *,
    command: AggregateChatCompletionsCommandV1,
) -> dict[str, Any]:
    try:
        from polaris.cells.runtime.task_market.public.projection_api import get_dashboard

        summary = get_dashboard(command.workspace)
        active_items = summary.get("active_items")
        if isinstance(active_items, list):
            summary = dict(summary)
            summary["active_items"] = active_items[:5]
            summary["active_items_truncated"] = len(active_items) > 5
        return {
            "enabled": True,
            "provider": "TaskMarketProjection",
            "status": "ok",
            "summary": summary,
        }
    except (ImportError, RuntimeError, ValueError, TypeError, OSError) as exc:
        return {
            "enabled": True,
            "provider": "TaskMarketProjection",
            "status": "degraded",
            "summary": {},
            "error_message": str(exc),
        }


def _read_aggregate_generated_pack_summary(pack_name: str, path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "pack": pack_name,
            "status": "missing",
            "path": path.relative_to(_BACKEND_ROOT).as_posix() if path.is_relative_to(_BACKEND_ROOT) else str(path),
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return {
            "pack": pack_name,
            "status": "degraded",
            "path": path.relative_to(_BACKEND_ROOT).as_posix() if path.is_relative_to(_BACKEND_ROOT) else str(path),
            "error_message": str(exc),
        }
    descriptors = payload.get("descriptors")
    capabilities = payload.get("capabilities")
    items = payload.get("items")
    return {
        "pack": pack_name,
        "status": "ok",
        "path": path.relative_to(_BACKEND_ROOT).as_posix() if path.is_relative_to(_BACKEND_ROOT) else str(path),
        "version": payload.get("version"),
        "cell_id": payload.get("cell_id"),
        "descriptor_count": len(descriptors) if isinstance(descriptors, list) else None,
        "capability_count": len(capabilities) if isinstance(capabilities, list) else None,
        "item_count": len(items) if isinstance(items, list) else None,
        "source_hash": payload.get("source_hash"),
        "snapshot_hash": payload.get("snapshot_hash"),
    }


def _serialize_context_budget(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    if hasattr(value, "dict"):
        dumped = value.dict()
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _build_aggregate_context_governance_pack(
    *,
    command: AggregateChatCompletionsCommandV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    chain_turn_index: int,
) -> dict[str, Any]:
    query = _aggregate_objective_from_messages(command.messages)
    descriptor_summaries = [
        _read_aggregate_generated_pack_summary(
            "context.catalog.descriptor",
            _BACKEND_ROOT / "polaris/cells/context/catalog/generated/descriptor.pack.json",
        ),
        _read_aggregate_generated_pack_summary(
            "context.engine.descriptor",
            _BACKEND_ROOT / "polaris/cells/context/engine/generated/descriptor.pack.json",
        ),
    ]
    generated_context_summaries = [
        _read_aggregate_generated_pack_summary(
            "context.catalog.context",
            _BACKEND_ROOT / "polaris/cells/context/catalog/generated/context.pack.json",
        ),
        _read_aggregate_generated_pack_summary(
            "context.engine.context",
            _BACKEND_ROOT / "polaris/cells/context/engine/generated/context.pack.json",
        ),
    ]
    try:
        from polaris.cells.context.engine.public.service import build_context_window, get_search_service

        retrieval_candidates = get_search_service().search(query, limit=5)
        pack, policy, budget, sources_enabled = build_context_window(
            project_root=command.workspace,
            role=selected_role,
            query=query,
            step=chain_turn_index + 1,
            run_id=command.run_id or command.session_id or _AGGREGATE_MODEL_ID,
            mode=selected_lobe.phase,
            sources_enabled=[],
            policy={"max_tokens": 4096, "max_chars": 16384, "cost_class": "LOCAL"},
            context_override={"state_first_context_os_enabled": True},
            session_id=command.session_id or "",
        )
        context_pack = {
            "provider": "context.engine.build_context_window",
            "request_hash": pack.request_hash,
            "item_count": len(pack.items),
            "total_tokens": pack.total_tokens,
            "total_chars": pack.total_chars,
            "snapshot_path": pack.snapshot_path,
            "snapshot_hash": pack.snapshot_hash,
            "sources_enabled": list(sources_enabled),
        }
        verify_pack = {
            "budget": _serialize_context_budget(budget),
            "policy_cost_class": policy.get("cost_class"),
            "descriptor_pack_count": len(descriptor_summaries),
            "generated_context_pack_count": len(generated_context_summaries),
            "retrieval_candidate_count": len(retrieval_candidates),
        }
        return {
            "enabled": True,
            "provider": "ContextCatalog+ContextEngine",
            "status": "ok",
            "query": query,
            "descriptor_pack": descriptor_summaries,
            "generated_context_pack": generated_context_summaries,
            "context_pack": context_pack,
            "verify_pack": verify_pack,
            "graph_constrained_retrieval": {
                "provider": "context.engine.search_gateway",
                "candidate_count": len(retrieval_candidates),
                "candidates": retrieval_candidates,
            },
        }
    except (ImportError, RuntimeError, ValueError, TypeError, OSError) as exc:
        return {
            "enabled": True,
            "provider": "ContextCatalog+ContextEngine",
            "status": "degraded",
            "query": query,
            "descriptor_pack": descriptor_summaries,
            "generated_context_pack": generated_context_summaries,
            "context_pack": {},
            "verify_pack": {},
            "graph_constrained_retrieval": {"candidate_count": 0, "candidates": []},
            "error_message": str(exc),
        }


def _serialize_distilled_knowledge_unit(unit: Any) -> dict[str, Any]:
    return {
        "knowledge_id": str(getattr(unit, "knowledge_id", "") or ""),
        "knowledge_type": str(getattr(unit, "knowledge_type", "") or ""),
        "pattern_summary": str(getattr(unit, "pattern_summary", "") or ""),
        "confidence": float(getattr(unit, "confidence", 0.0) or 0.0),
        "occurrence_count": int(getattr(unit, "occurrence_count", 0) or 0),
        "prevention_hint": getattr(unit, "prevention_hint", None),
        "metadata": dict(getattr(unit, "metadata", {}) or {}),
    }


def _build_aggregate_distilled_knowledge_pack(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
) -> dict[str, Any]:
    query = " ".join(
        token
        for token in (
            _aggregate_objective_from_messages(command.messages),
            selected_lobe.output_contract,
            " ".join(str(signal) for signal in plan.metadata.get("failure_signals") or ()),
        )
        if token
    )
    try:
        from polaris.cells.cognitive.knowledge_distiller.public.contracts import RetrieveKnowledgeQueryV1
        from polaris.cells.cognitive.knowledge_distiller.public.service import KnowledgeDistillerService

        result = KnowledgeDistillerService(workspace=command.workspace).retrieve_knowledge(
            RetrieveKnowledgeQueryV1(
                workspace=command.workspace,
                query=query,
                top_k=5,
                role_filter=selected_role,
                min_confidence=0.3,
            )
        )
        return {
            "enabled": True,
            "provider": "KnowledgeDistillerService.retrieve_knowledge",
            "status": "ok",
            "query": result.query,
            "total_available": result.total_available,
            "knowledge_units": [_serialize_distilled_knowledge_unit(unit) for unit in result.knowledge_units],
        }
    except (ImportError, RuntimeError, ValueError, TypeError, OSError) as exc:
        return {
            "enabled": True,
            "provider": "KnowledgeDistillerService.retrieve_knowledge",
            "status": "degraded",
            "query": query,
            "total_available": 0,
            "knowledge_units": [],
            "error_message": str(exc),
        }


def _distill_aggregate_lobe_result(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    chain_turn_index: int,
    result: RoleExecutionResultV1,
) -> dict[str, Any]:
    failure_evidence = _aggregate_plan_failure_evidence_payload(plan)
    error_summary = (
        result.error_message
        or failure_evidence.get("compiler_output")
        or failure_evidence.get("typecheck_output")
        or failure_evidence.get("apply_error")
        or ""
    )
    structured_findings: dict[str, Any] = {
        "task_progress": selected_lobe.phase,
        "action_taken": selected_lobe.output_contract,
        "verified_results": [result.status] if result.ok else [],
        "patched_files": [],
        "suspected_files": suspected_files_from_failure_evidence_payload(failure_evidence),
        "_findings_trajectory": [
            {
                "lobe_id": selected_lobe.lobe_id,
                "role_id": selected_role,
                "status": result.status,
                "chain_turn_index": chain_turn_index,
            }
        ],
    }
    if error_summary:
        structured_findings["error_summary"] = str(error_summary)
    try:
        from polaris.cells.cognitive.knowledge_distiller.public.contracts import DistillSessionCommandV1
        from polaris.cells.cognitive.knowledge_distiller.public.service import KnowledgeDistillerService

        distill_session_id = (
            result.session_id
            or command.session_id
            or result.task_id
            or result.run_id
            or command.run_id
            or f"aggregate:{selected_lobe.lobe_id}:{chain_turn_index}"
        )
        distillation = KnowledgeDistillerService(workspace=command.workspace).distill_session(
            DistillSessionCommandV1(
                workspace=command.workspace,
                session_id=distill_session_id,
                run_id=result.run_id or command.run_id,
                structured_findings=structured_findings,
                task_progress=selected_lobe.phase,
                outcome="completed" if result.ok else "failed",
                metadata={
                    "role": selected_role,
                    "lobe_id": selected_lobe.lobe_id,
                    "aggregate_model_id": plan.aggregate_model_id,
                    "chain_turn_index": chain_turn_index,
                },
            )
        )
        return {
            "enabled": True,
            "provider": "KnowledgeDistillerService.distill_session",
            "status": "ok",
            "knowledge_units_created": distillation.knowledge_units_created,
            "patterns_extracted": list(distillation.patterns_extracted),
            "knowledge_ids": list(distillation.knowledge_ids),
        }
    except (ImportError, RuntimeError, ValueError, TypeError, OSError) as exc:
        return {
            "enabled": True,
            "provider": "KnowledgeDistillerService.distill_session",
            "status": "degraded",
            "knowledge_units_created": 0,
            "patterns_extracted": [],
            "knowledge_ids": [],
            "error_message": str(exc),
        }
