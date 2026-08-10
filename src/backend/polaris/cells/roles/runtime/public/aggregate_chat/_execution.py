"""Aggregate lobe selection, execution context/metadata, and content renderers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from polaris.cells.roles.runtime.public.aggregate_chat._helpers import (
    _aggregate_objective_from_messages,
)
from polaris.cells.roles.runtime.public.aggregate_chat._memory import (
    _aggregate_max_lobe_turns,
    _lobe_has_current_role,
    _select_aggregate_execution_lobe,
)
from polaris.cells.roles.runtime.public.aggregate_chat._plan import (
    _aggregate_plan_failure_evidence_payload,
)
from polaris.cells.roles.runtime.public.contracts import (
    AggregateChatCompletionsCommandV1,
    AggregateChatMessageV1,
    AggregateRoleLobeV1,
    AggregateRolePlanResultV1,
    RoleExecutionResultV1,
)


def _select_aggregate_lobe_chain(
    *,
    plan: AggregateRolePlanResultV1,
    command: AggregateChatCompletionsCommandV1,
) -> tuple[AggregateRoleLobeV1, ...]:
    max_lobes = _aggregate_max_lobe_turns(command)
    start_lobe = _select_aggregate_execution_lobe(plan)
    ordered_lobes = list(plan.lobes)
    try:
        start_index = ordered_lobes.index(start_lobe)
    except ValueError:
        start_index = 0
    candidates = ordered_lobes[start_index:]
    selected = [lobe for lobe in candidates if _lobe_has_current_role(lobe, plan)]
    if not selected and _lobe_has_current_role(start_lobe, plan):
        selected = [start_lobe]
    return tuple(selected[:max_lobes])


def _select_aggregate_execution_role(
    *,
    plan: AggregateRolePlanResultV1,
    command: AggregateChatCompletionsCommandV1,
    selected_lobe: AggregateRoleLobeV1,
) -> str:
    current_roles = set(plan.current_role_ids)
    for source in (command.metadata, command.context):
        raw_role = source.get("aggregate_execution_role") if isinstance(source, Mapping) else None
        if raw_role is None and isinstance(source, Mapping):
            raw_role = source.get("execution_role")
        role = str(raw_role or "").strip()
        if role and role in current_roles:
            return role
    preferred_by_lobe: dict[str, tuple[str, ...]] = {
        "constraint_boundary_generator": ("architect", "qa"),
        "dialectic_self_heal_loop": ("chief_engineer", "qa"),
        "hippocampus_controller": ("director",),
        "tool_commit_guard": ("director", "qa"),
        "task_market_allocator": ("pm", "director", "qa"),
    }
    for role in preferred_by_lobe.get(selected_lobe.lobe_id, selected_lobe.role_ids):
        if role in current_roles and role in selected_lobe.role_ids:
            return role
    for role in selected_lobe.role_ids:
        if role in current_roles:
            return role
    for role in ("director", "chief_engineer", "architect", "pm", "qa"):
        if role in current_roles:
            return role
    if plan.current_role_ids:
        return plan.current_role_ids[0]
    raise ValueError("aggregate single_turn requires at least one concrete role")


def _selected_message_index(messages: tuple[AggregateChatMessageV1, ...]) -> int:
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role.lower() == "user":
            return index
    return len(messages) - 1


def _aggregate_history_from_messages(messages: tuple[AggregateChatMessageV1, ...]) -> tuple[tuple[str, str], ...]:
    selected_index = _selected_message_index(messages)
    return tuple((message.role, message.content) for index, message in enumerate(messages) if index != selected_index)


def _build_aggregate_lobe_directive(
    *,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    chain_turn_index: int,
) -> dict[str, Any]:
    return {
        "schema": "aggregate_lobe_directive.v1",
        "aggregate_model_id": plan.aggregate_model_id,
        "chain_turn_index": chain_turn_index,
        "lobe_id": selected_lobe.lobe_id,
        "phase": selected_lobe.phase,
        "role_id": selected_role,
        "role_ids": list(selected_lobe.role_ids),
        "virtual_role_ids": list(selected_lobe.virtual_role_ids),
        "compute_tier": selected_lobe.compute_tier,
        "capability_refs": list(selected_lobe.capability_refs),
        "attention_masks": list(selected_lobe.attention_masks),
        "memory_triggers": list(selected_lobe.memory_triggers),
        "handoff_keys": list(selected_lobe.handoff_keys),
        "takeover_triggers": list(selected_lobe.takeover_triggers),
        "output_contract": selected_lobe.output_contract,
        "truthful_migration": "Only role_id is executed; virtual_role_ids are planning constructs.",
    }


def _summarize_aggregate_memory_pack(memory_recall_pack: Mapping[str, Any]) -> dict[str, Any]:
    projections = memory_recall_pack.get("projections")
    projection_summaries: list[dict[str, Any]] = []
    if isinstance(projections, list):
        for item in projections[:5]:
            if not isinstance(item, Mapping):
                continue
            memory = item.get("memory")
            memory_payload = dict(memory) if isinstance(memory, Mapping) else {}
            projection_summaries.append(
                {
                    "memory_id": memory_payload.get("memory_id"),
                    "content": memory_payload.get("content"),
                    "injection_allowed": bool(item.get("injection_allowed")),
                    "injection_reason": item.get("injection_reason"),
                }
            )
    return {
        "enabled": bool(memory_recall_pack.get("enabled")),
        "provider": memory_recall_pack.get("provider"),
        "status": memory_recall_pack.get("status") or "skipped",
        "triggers": list(memory_recall_pack.get("triggers") or ()),
        "query": memory_recall_pack.get("query"),
        "projection_count": memory_recall_pack.get("projection_count", 0),
        "injection_allowed_count": memory_recall_pack.get("injection_allowed_count", 0),
        "projections": projection_summaries,
    }


def _build_aggregate_lobe_turn_envelope(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    chain_turn_index: int,
    prior_handoffs: tuple[Mapping[str, Any], ...],
    memory_recall_pack: Mapping[str, Any],
    contextos_attention_budget_pack: Mapping[str, Any],
    task_market_projection_pack: Mapping[str, Any],
    context_governance_pack: Mapping[str, Any],
    distilled_knowledge_pack: Mapping[str, Any],
) -> str:
    payload: dict[str, Any] = {
        "schema": "polaris.aggregate_lobe_turn.v1",
        "original_objective": _aggregate_objective_from_messages(command.messages),
        "execution_mode": command.execution_mode,
        "lobe_directive": _build_aggregate_lobe_directive(
            plan=plan,
            selected_lobe=selected_lobe,
            selected_role=selected_role,
            chain_turn_index=chain_turn_index,
        ),
        "failure": {
            "signals": list(plan.metadata.get("failure_signals") or ()),
            "evidence": _aggregate_plan_failure_evidence_payload(plan),
            "takeover_evidence_status": dict(plan.metadata.get("takeover_evidence_status") or {}),
            "takeover_directive": (
                {
                    "trigger": plan.takeover_directive.trigger,
                    "lobe_id": plan.takeover_directive.lobe_id,
                    "action_contract": plan.takeover_directive.action_contract,
                }
                if plan.takeover_directive is not None
                else None
            ),
        },
        "prior_handoffs": [dict(item) for item in prior_handoffs],
        "memory_recall": _summarize_aggregate_memory_pack(memory_recall_pack),
        "contextos_attention_budget": dict(contextos_attention_budget_pack),
        "context_governance": dict(context_governance_pack),
        "distilled_knowledge": dict(distilled_knowledge_pack),
        "runtime_projection": {
            "provider": task_market_projection_pack.get("provider"),
            "status": task_market_projection_pack.get("status"),
            "summary": task_market_projection_pack.get("summary") or {},
        },
        "response_contract": selected_lobe.output_contract,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _aggregate_execution_context(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    chain_turn_index: int = 0,
    prior_handoffs: tuple[Mapping[str, Any], ...] = (),
    memory_recall_pack: Mapping[str, Any] | None = None,
    contextos_attention_budget_pack: Mapping[str, Any] | None = None,
    task_market_projection_pack: Mapping[str, Any] | None = None,
    context_governance_pack: Mapping[str, Any] | None = None,
    distilled_knowledge_pack: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    context = dict(command.context)
    recall_pack = dict(memory_recall_pack or {})
    attention_budget_pack = dict(contextos_attention_budget_pack or {})
    task_projection_pack = dict(task_market_projection_pack or {})
    governance_pack = dict(context_governance_pack or {})
    knowledge_pack = dict(distilled_knowledge_pack or {})
    lobe_directive = _build_aggregate_lobe_directive(
        plan=plan,
        selected_lobe=selected_lobe,
        selected_role=selected_role,
        chain_turn_index=chain_turn_index,
    )
    context.setdefault("state_first_context_os_enabled", True)
    context["aggregate_runtime_context"] = {
        "aggregate_model_id": plan.aggregate_model_id,
        "execution_mode": command.execution_mode,
        "selected_lobe_id": selected_lobe.lobe_id,
        "selected_role_id": selected_role,
        "chain_turn_index": chain_turn_index,
        "execution_order": list(plan.execution_order),
        "prior_handoffs": [dict(item) for item in prior_handoffs],
        "lobe_directive": lobe_directive,
        "failure_signals": list(plan.metadata.get("failure_signals") or ()),
        "failure_evidence": _aggregate_plan_failure_evidence_payload(plan),
        "takeover_evidence_status": dict(plan.metadata.get("takeover_evidence_status") or {}),
        "akashic_recall_pack": recall_pack,
        "contextos_attention_budget_pack": attention_budget_pack,
        "task_market_projection_pack": task_projection_pack,
        "context_governance_pack": governance_pack,
        "distilled_knowledge_pack": knowledge_pack,
        "takeover_directive": (
            {
                "trigger": plan.takeover_directive.trigger,
                "lobe_id": plan.takeover_directive.lobe_id,
                "action_contract": plan.takeover_directive.action_contract,
            }
            if plan.takeover_directive is not None
            else None
        ),
    }
    return context


def _aggregate_execution_metadata(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    chain_turn_index: int = 0,
    prior_handoffs: tuple[Mapping[str, Any], ...] = (),
    memory_recall_pack: Mapping[str, Any] | None = None,
    contextos_attention_budget_pack: Mapping[str, Any] | None = None,
    task_market_projection_pack: Mapping[str, Any] | None = None,
    context_governance_pack: Mapping[str, Any] | None = None,
    distilled_knowledge_pack: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(command.metadata)
    recall_pack = dict(memory_recall_pack or {})
    attention_budget_pack = dict(contextos_attention_budget_pack or {})
    task_projection_pack = dict(task_market_projection_pack or {})
    governance_pack = dict(context_governance_pack or {})
    knowledge_pack = dict(distilled_knowledge_pack or {})
    lobe_directive = _build_aggregate_lobe_directive(
        plan=plan,
        selected_lobe=selected_lobe,
        selected_role=selected_role,
        chain_turn_index=chain_turn_index,
    )
    metadata.setdefault("context_os_expected", True)
    metadata.setdefault("cognitive_runtime_required", True)
    metadata.setdefault("cognitive_runtime_mode", "mainline")
    metadata["aggregate_execution"] = {
        "planner": "roles.runtime.aggregate_chat_completions.v1",
        "aggregate_model_id": plan.aggregate_model_id,
        "execution_mode": command.execution_mode,
        "selected_lobe_id": selected_lobe.lobe_id,
        "selected_role_id": selected_role,
        "chain_turn_index": chain_turn_index,
        "selected_lobe_phase": selected_lobe.phase,
        "selected_lobe_compute_tier": selected_lobe.compute_tier,
        "lobe_directive": lobe_directive,
        "prior_handoff_count": len(prior_handoffs),
        "takeover_evidence_status": dict(plan.metadata.get("takeover_evidence_status") or {}),
        "akashic_recall_status": {
            "enabled": bool(recall_pack.get("enabled")),
            "status": recall_pack.get("status") or "skipped",
            "projection_count": recall_pack.get("projection_count", 0),
            "injection_allowed_count": recall_pack.get("injection_allowed_count", 0),
        },
        "contextos_attention_budget_status": {
            "enabled": bool(attention_budget_pack.get("enabled")),
            "status": attention_budget_pack.get("status") or "skipped",
            "phase": attention_budget_pack.get("phase"),
            "attention_score_count": len(attention_budget_pack.get("attention_scores") or ()),
        },
        "task_market_projection_status": {
            "enabled": bool(task_projection_pack.get("enabled")),
            "status": task_projection_pack.get("status") or "skipped",
            "total_active": (task_projection_pack.get("summary") or {}).get("total_active", 0),
            "dead_letter_count": (task_projection_pack.get("summary") or {}).get("dead_letter_count", 0),
        },
        "context_governance_status": {
            "enabled": bool(governance_pack.get("enabled")),
            "status": governance_pack.get("status") or "skipped",
            "retrieval_candidate_count": (governance_pack.get("graph_constrained_retrieval") or {}).get(
                "candidate_count", 0
            ),
        },
        "distilled_knowledge_status": {
            "enabled": bool(knowledge_pack.get("enabled")),
            "status": knowledge_pack.get("status") or "skipped",
            "total_available": knowledge_pack.get("total_available", 0),
            "knowledge_unit_count": len(knowledge_pack.get("knowledge_units") or ()),
        },
        "p0_runtime_integrations": [
            item.tech_id for item in plan.runtime_integrations if item.priority == "p0" and item.status == "wired"
        ],
    }
    return metadata


def _aggregate_handoff_from_result(
    *,
    sequence: int,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    result: RoleExecutionResultV1,
) -> dict[str, Any]:
    runtime_evidence = result.metadata.get("cognitive_runtime_evidence")
    receipt_id = runtime_evidence.get("receipt_id") if isinstance(runtime_evidence, Mapping) else None
    handoff_id = runtime_evidence.get("handoff_id") if isinstance(runtime_evidence, Mapping) else None
    return {
        "sequence": sequence,
        "lobe_id": selected_lobe.lobe_id,
        "role_id": selected_role,
        "status": result.status,
        "ok": result.ok,
        "output_contract": selected_lobe.output_contract,
        "output": result.output,
        "tool_calls": list(result.tool_calls),
        "receipt_id": receipt_id,
        "handoff_id": handoff_id,
    }


def _render_aggregate_execution_content(
    *,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    execution_result: RoleExecutionResultV1,
) -> str:
    payload: dict[str, Any] = {
        "aggregate_model_id": plan.aggregate_model_id,
        "execution_mode": "single_turn",
        "selected_lobe_id": selected_lobe.lobe_id,
        "selected_role_id": selected_role,
        "status": execution_result.status,
        "ok": execution_result.ok,
        "output": execution_result.output,
        "tool_calls": list(execution_result.tool_calls),
        "runtime_evidence": {
            "strategy_fingerprint": execution_result.metadata.get("strategy_fingerprint"),
            "context_os_preflight": execution_result.metadata.get("context_os_preflight"),
            "cognitive_runtime_preflight": execution_result.metadata.get("cognitive_runtime_preflight"),
            "cognitive_runtime_evidence": execution_result.metadata.get("cognitive_runtime_evidence"),
        },
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _render_aggregate_chain_content(
    *,
    plan: AggregateRolePlanResultV1,
    chain_lobes: tuple[AggregateRoleLobeV1, ...],
    chain_roles: tuple[str, ...],
    execution_results: tuple[RoleExecutionResultV1, ...],
    handoffs: tuple[Mapping[str, Any], ...],
) -> str:
    payload: dict[str, Any] = {
        "aggregate_model_id": plan.aggregate_model_id,
        "execution_mode": "lobe_chain",
        "executed_lobes": [lobe.lobe_id for lobe in chain_lobes],
        "executed_roles": list(chain_roles),
        "status": "ok" if all(result.ok for result in execution_results) else "failed",
        "ok": all(result.ok for result in execution_results),
        "handoffs": [dict(item) for item in handoffs],
        "results": [
            {
                "sequence": index,
                "lobe_id": chain_lobes[index].lobe_id if index < len(chain_lobes) else "",
                "role_id": result.role,
                "status": result.status,
                "ok": result.ok,
                "output": result.output,
                "tool_calls": list(result.tool_calls),
                "runtime_evidence": {
                    "strategy_fingerprint": result.metadata.get("strategy_fingerprint"),
                    "context_os_preflight": result.metadata.get("context_os_preflight"),
                    "cognitive_runtime_preflight": result.metadata.get("cognitive_runtime_preflight"),
                    "cognitive_runtime_evidence": result.metadata.get("cognitive_runtime_evidence"),
                },
            }
            for index, result in enumerate(execution_results)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _stable_completion_id(command: AggregateChatCompletionsCommandV1, objective: str) -> str:
    seed = {
        "workspace": command.workspace,
        "model": command.model,
        "objective": objective,
        "messages": [
            {
                "role": message.role,
                "content": message.content,
                "name": message.name,
            }
            for message in command.messages
        ],
        "role_ids": list(command.role_ids),
        "failure_signals": list(command.failure_signals),
        "failure_evidence": dict(command.failure_evidence),
        "domain": command.domain,
        "execution_mode": command.execution_mode,
        "session_id": command.session_id,
        "run_id": command.run_id,
    }
    digest = hashlib.sha256(json.dumps(seed, ensure_ascii=True, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return f"aggcmpl-{digest}"


def _render_aggregate_plan_content(plan: AggregateRolePlanResultV1) -> str:
    payload: dict[str, Any] = {
        "aggregate_model_id": plan.aggregate_model_id,
        "execution_mode": "plan_only",
        "current_role_ids": list(plan.current_role_ids),
        "execution_order": list(plan.execution_order),
        "required_capability_refs": list(plan.required_capability_refs),
        "failure_signals": list(plan.metadata.get("failure_signals") or ()),
        "failure_evidence": _aggregate_plan_failure_evidence_payload(plan),
        "takeover_evidence_status": dict(plan.metadata.get("takeover_evidence_status") or {}),
        "runtime_integrations": [
            {
                "tech_id": integration.tech_id,
                "title": integration.title,
                "status": integration.status,
                "priority": integration.priority,
                "production_entrypoints": list(integration.production_entrypoints),
                "entrypoints_verified": integration.entrypoints_verified,
                "missing_entrypoints": list(integration.missing_entrypoints),
                "entrypoint_checks": [
                    {
                        "entrypoint": check.entrypoint,
                        "check_type": check.check_type,
                        "ok": check.ok,
                        "evidence": check.evidence,
                        "reason": check.reason,
                    }
                    for check in integration.entrypoint_checks
                ],
                "evidence_keys": list(integration.evidence_keys),
                "runtime_effects": list(integration.runtime_effects),
                "benefit": integration.benefit,
            }
            for integration in plan.runtime_integrations
        ],
        "compute_policy": dict(plan.compute_policy),
        "takeover_directive": (
            {
                "trigger": plan.takeover_directive.trigger,
                "lobe_id": plan.takeover_directive.lobe_id,
                "compute_tier": plan.takeover_directive.compute_tier,
                "reason": plan.takeover_directive.reason,
                "evidence_keys": list(plan.takeover_directive.evidence_keys),
                "action_contract": plan.takeover_directive.action_contract,
                "next_lobes": list(plan.takeover_directive.next_lobes),
                "status": plan.takeover_directive.status,
            }
            if plan.takeover_directive is not None
            else None
        ),
        "cognitive_ledger": [
            {
                "sequence": item.sequence,
                "lobe_id": item.lobe_id,
                "phase": item.phase,
                "compute_tier": item.compute_tier,
                "reads": list(item.reads),
                "writes": list(item.writes),
                "handoff_to": list(item.handoff_to),
                "takeover_triggers": list(item.takeover_triggers),
            }
            for item in plan.cognitive_ledger
        ],
        "warnings": list(plan.warnings),
        "truthful_migration": str(plan.metadata.get("truthful_migration") or ""),
        "lobes": [
            {
                "lobe_id": lobe.lobe_id,
                "phase": lobe.phase,
                "role_ids": list(lobe.role_ids),
                "virtual_role_ids": list(lobe.virtual_role_ids),
                "capability_refs": list(lobe.capability_refs),
                "attention_masks": list(lobe.attention_masks),
                "memory_triggers": list(lobe.memory_triggers),
                "compute_tier": lobe.compute_tier,
                "handoff_keys": list(lobe.handoff_keys),
                "takeover_triggers": list(lobe.takeover_triggers),
                "output_contract": lobe.output_contract,
                "status": lobe.status,
                "missing_role_ids": list(lobe.missing_role_ids),
            }
            for lobe in plan.lobes
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
