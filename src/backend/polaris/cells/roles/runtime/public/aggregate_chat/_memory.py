"""Aggregate memory recall, attention candidates, and ContextOS budget packs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from polaris.cells.roles.runtime.public.aggregate_chat._entrypoint_checks import (
    _dedupe_tokens,
)
from polaris.cells.roles.runtime.public.aggregate_chat._helpers import (
    _aggregate_objective_from_messages,
)
from polaris.cells.roles.runtime.public.aggregate_chat._plan import (
    _aggregate_plan_failure_evidence_payload,
)
from polaris.cells.roles.runtime.public.contracts import (
    AggregateChatCompletionsCommandV1,
    AggregateRoleLobeV1,
    AggregateRolePlanResultV1,
)


def _lobe_by_id(plan: AggregateRolePlanResultV1, lobe_id: str | None) -> AggregateRoleLobeV1 | None:
    token = str(lobe_id or "").strip()
    if not token:
        return None
    for lobe in plan.lobes:
        if lobe.lobe_id == token:
            return lobe
    return None


def _select_aggregate_execution_lobe(plan: AggregateRolePlanResultV1) -> AggregateRoleLobeV1:
    takeover_lobe = _lobe_by_id(
        plan,
        plan.takeover_directive.lobe_id if plan.takeover_directive is not None else None,
    )
    if takeover_lobe is not None:
        return takeover_lobe
    for lobe in plan.lobes:
        if lobe.status == "active":
            return lobe
    return plan.lobes[0]


def _lobe_has_current_role(lobe: AggregateRoleLobeV1, plan: AggregateRolePlanResultV1) -> bool:
    current_roles = set(plan.current_role_ids)
    return any(role_id in current_roles for role_id in lobe.role_ids)


def _aggregate_max_lobe_turns(command: AggregateChatCompletionsCommandV1) -> int:
    for source in (command.metadata, command.context):
        raw_value = source.get("max_lobe_turns") if isinstance(source, Mapping) else None
        if raw_value is None and isinstance(source, Mapping):
            raw_value = source.get("aggregate_max_lobe_turns")
        if raw_value is None:
            continue
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            continue
        return min(max(parsed, 1), 5)
    return 3


def _aggregate_memory_recall_limit(command: AggregateChatCompletionsCommandV1) -> int:
    for source in (command.metadata, command.context):
        raw_value = source.get("memory_recall_limit") if isinstance(source, Mapping) else None
        if raw_value is None and isinstance(source, Mapping):
            raw_value = source.get("akashic_recall_limit")
        if raw_value is None:
            continue
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            continue
        return min(max(parsed, 0), 10)
    return 5


def _aggregate_memory_recall_triggers(
    *,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
) -> tuple[str, ...]:
    failure_signals = tuple(str(item) for item in plan.metadata.get("failure_signals") or ())
    triggers = [
        signal
        for signal in failure_signals
        if signal in selected_lobe.memory_triggers
        or signal in {"localization_uncertain", "degraded_signal", "empty_repo_map", "long_session"}
    ]
    if selected_lobe.lobe_id == "hippocampus_controller":
        triggers.append("hippocampus_controller")
    return _dedupe_tokens(triggers)


def _aggregate_memory_recall_query(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
) -> str:
    evidence = _aggregate_plan_failure_evidence_payload(plan)
    evidence_text = " ".join(str(value) for value in evidence.values())
    return " ".join(
        item
        for item in (
            _aggregate_objective_from_messages(command.messages),
            selected_lobe.lobe_id,
            " ".join(str(item) for item in plan.metadata.get("failure_signals") or ()),
            evidence_text,
        )
        if item
    ).strip()


def _aggregate_memory_current_facts(
    *,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    prior_handoffs: tuple[Mapping[str, Any], ...],
) -> list[str]:
    facts = [
        f"aggregate_model_id={plan.aggregate_model_id}",
        f"selected_lobe_id={selected_lobe.lobe_id}",
        f"selected_lobe_phase={selected_lobe.phase}",
    ]
    for signal in plan.metadata.get("failure_signals") or ():
        facts.append(f"failure_signal={signal}")
    evidence = _aggregate_plan_failure_evidence_payload(plan)
    if evidence:
        facts.extend(f"failure_evidence.{key}={value}" for key, value in evidence.items())
    for handoff in prior_handoffs:
        facts.append(
            "prior_handoff="
            + json.dumps(
                {
                    "lobe_id": handoff.get("lobe_id"),
                    "role_id": handoff.get("role_id"),
                    "status": handoff.get("status"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return facts


def _build_aggregate_memory_recall_pack(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    prior_handoffs: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    triggers = _aggregate_memory_recall_triggers(plan=plan, selected_lobe=selected_lobe)
    limit = _aggregate_memory_recall_limit(command)
    if not triggers or limit <= 0:
        return {
            "enabled": False,
            "provider": "ContextOS.MemoryManager",
            "triggers": triggers,
            "reason": "no_memory_trigger" if not triggers else "limit_zero",
        }
    query = _aggregate_memory_recall_query(command=command, plan=plan, selected_lobe=selected_lobe)
    current_facts = _aggregate_memory_current_facts(
        plan=plan,
        selected_lobe=selected_lobe,
        prior_handoffs=prior_handoffs,
    )
    try:
        from polaris.kernelone.context.context_os.memory import MemoryManager

        manager = MemoryManager(workspace=command.workspace)
        projections = manager.process(query=query, current_facts=current_facts, limit=limit)
        projection_payloads = [projection.to_dict() for projection in projections[:limit]]
        return {
            "enabled": True,
            "provider": "ContextOS.MemoryManager",
            "status": "ok",
            "triggers": triggers,
            "query": query,
            "current_facts": current_facts,
            "projection_count": len(projection_payloads),
            "injection_allowed_count": sum(1 for item in projection_payloads if bool(item.get("injection_allowed"))),
            "projections": projection_payloads,
            "truthful_migration": "Memory is supplementary and never overrides current failure evidence.",
        }
    except (RuntimeError, ValueError, TypeError, OSError) as exc:
        return {
            "enabled": True,
            "provider": "ContextOS.MemoryManager",
            "status": "degraded",
            "triggers": triggers,
            "query": query,
            "current_facts": current_facts,
            "projection_count": 0,
            "injection_allowed_count": 0,
            "projections": [],
            "error_message": str(exc),
        }


def _aggregate_phase_for_contextos(
    *,
    selected_lobe: AggregateRoleLobeV1,
    plan: AggregateRolePlanResultV1,
) -> str:
    if plan.takeover_directive is not None and plan.takeover_directive.trigger in {
        "compile_failure",
        "typecheck_failure",
        "failed_apply",
    }:
        return "debugging"
    return {
        "preflight": "planning",
        "blueprint_refinement": "planning",
        "execution_context_projection": "exploration",
        "apply_and_verify": "verification",
        "stage_handoff": "review",
    }.get(selected_lobe.phase, "planning")


def _estimate_aggregate_text_tokens(value: Any) -> int:
    try:
        payload = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        payload = str(value)
    return max(1, len(payload) // 4) if payload else 0


def _build_aggregate_attention_candidates(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    prior_handoffs: tuple[Mapping[str, Any], ...],
) -> tuple[Any, ...]:
    objective = _aggregate_objective_from_messages(command.messages)
    candidates: list[Any] = [
        SimpleNamespace(
            event_id="aggregate.objective",
            content=objective,
            role="user",
            kind="user_turn",
            sequence=0,
            metadata={"is_pinned": True, "candidate_kind": "objective"},
            created_at="",
        )
    ]
    failure_evidence = _aggregate_plan_failure_evidence_payload(plan)
    if failure_evidence:
        candidates.append(
            SimpleNamespace(
                event_id="aggregate.failure_evidence",
                content=json.dumps(failure_evidence, ensure_ascii=False, sort_keys=True),
                role="system",
                kind="error",
                sequence=1,
                metadata={"contains_error": True, "candidate_kind": "failure_evidence"},
                created_at="",
            )
        )
    if prior_handoffs:
        candidates.append(
            SimpleNamespace(
                event_id="aggregate.prior_handoffs",
                content=json.dumps([dict(item) for item in prior_handoffs], ensure_ascii=False, sort_keys=True),
                role="assistant",
                kind="tool_result",
                sequence=2,
                metadata={"contains_tool_result": True, "candidate_kind": "prior_handoffs"},
                created_at="",
            )
        )
    candidates.append(
        SimpleNamespace(
            event_id=f"aggregate.lobe.{selected_lobe.lobe_id}",
            content=" ".join((selected_lobe.title, selected_lobe.phase, " ".join(selected_lobe.attention_masks))),
            role="system",
            kind="system",
            sequence=3,
            metadata={"candidate_kind": "lobe_directive"},
            created_at="",
        )
    )
    return tuple(candidates)


def _build_aggregate_contextos_attention_budget_pack(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    prior_handoffs: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    phase_name = _aggregate_phase_for_contextos(selected_lobe=selected_lobe, plan=plan)
    transcript_tokens = sum(_estimate_aggregate_text_tokens(message.content) for message in command.messages)
    artifact_tokens = _estimate_aggregate_text_tokens(_aggregate_plan_failure_evidence_payload(plan))
    artifact_tokens += _estimate_aggregate_text_tokens([dict(item) for item in prior_handoffs]) if prior_handoffs else 0
    try:
        from polaris.kernelone.context.context_os.attention import AttentionScorer
        from polaris.kernelone.context.context_os.attention.scorer import ScoringContext
        from polaris.kernelone.context.context_os.phase_budget_planner import PhaseAwareBudgetPlanner
        from polaris.kernelone.context.context_os.phase_detection import TaskPhase
        from polaris.kernelone.context.context_os.predictive import PredictiveCompressor

        task_phase = TaskPhase(phase_name)
        budget_plan = PhaseAwareBudgetPlanner().plan_budget(
            phase=task_phase,
            transcript_tokens=transcript_tokens,
            artifact_tokens=artifact_tokens,
        )
        prediction = PredictiveCompressor().predict(current_phase=task_phase.value, recent_events=())
        scorer = AttentionScorer(use_embeddings=False)
        scoring_context = ScoringContext(
            current_intent=_aggregate_objective_from_messages(command.messages),
            current_goal=selected_lobe.output_contract,
            hard_constraints=tuple(selected_lobe.attention_masks),
            current_task_id=command.run_id or command.session_id or "",
            current_phase=task_phase,
        )
        attention_scores = [
            {
                "event_id": str(getattr(candidate, "event_id", "")),
                "kind": str(getattr(candidate, "kind", "")),
                "score": scorer.score_candidate(candidate, scoring_context).to_dict(),
            }
            for candidate in _build_aggregate_attention_candidates(
                command=command,
                plan=plan,
                selected_lobe=selected_lobe,
                prior_handoffs=prior_handoffs,
            )
        ]
        return {
            "enabled": True,
            "provider": "ContextOS.Attention+PhaseBudget+PredictiveCompression",
            "status": "ok",
            "phase": task_phase.value,
            "attention_masks": list(selected_lobe.attention_masks),
            "phase_budget": budget_plan.to_dict(),
            "attention_scores": attention_scores,
            "predictive_compression": prediction.to_dict(),
        }
    except (ImportError, RuntimeError, ValueError, TypeError, OSError) as exc:
        return {
            "enabled": True,
            "provider": "ContextOS.Attention+PhaseBudget+PredictiveCompression",
            "status": "degraded",
            "phase": phase_name,
            "attention_masks": list(selected_lobe.attention_masks),
            "phase_budget": {},
            "attention_scores": [],
            "predictive_compression": {},
            "error_message": str(exc),
        }
