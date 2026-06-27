"""Shared execution-contract derivation for Director task execution.

The execution contract is the common payload consumed by prompts, runtime
controls, quality gates, and final-request audit. It does not classify or decide
anything by itself; it composes the already-authoritative profile, strategy, and
PM/CE delivery contracts into one stable evidence object.
"""

from __future__ import annotations

from typing import Any, Mapping

from polaris.cells.control_plane.run_ledger.public import stable_hash
from polaris.cells.director.tasking.public.contracts import (
    TaskExecutionContractV1,
    TaskExecutionProfileV1,
    TaskExecutionStrategyV1,
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    rows: list[str] = []
    seen: set[str] = set()
    for item in value:
        token = str(item or "").strip()
        if token and token not in seen:
            seen.add(token)
            rows.append(token)
    return rows


def _contract_mapping(metadata: Mapping[str, Any], key: str) -> dict[str, Any]:
    direct = _mapping(metadata.get(key))
    if direct:
        return direct
    task_payload = _mapping(metadata.get("task"))
    nested = _mapping(task_payload.get(key))
    if nested:
        return nested
    task_metadata = _mapping(task_payload.get("metadata"))
    return _mapping(task_metadata.get(key))


def _contract_list(metadata: Mapping[str, Any], key: str) -> list[str]:
    direct = _string_list(metadata.get(key))
    if direct:
        return direct
    task_payload = _mapping(metadata.get("task"))
    nested = _string_list(task_payload.get(key))
    if nested:
        return nested
    task_metadata = _mapping(task_payload.get("metadata"))
    return _string_list(task_metadata.get(key))


def _delivery_contract(metadata: Mapping[str, Any]) -> dict[str, Any]:
    depth_contract = _contract_mapping(metadata, "delivery_depth_contract")
    plan_document = _contract_mapping(metadata, "delivery_plan_document")
    behavior_contract = _contract_mapping(metadata, "behavior_contract")
    if not behavior_contract:
        behavior_contract = _mapping(depth_contract.get("behavior_contract"))
    acceptance_contract = _mapping(depth_contract.get("acceptance_contract"))
    product_intent = _mapping(depth_contract.get("product_intent"))
    product_summary = _mapping(plan_document.get("product_summary"))
    level_contract = _contract_mapping(metadata, "level_contract") or _contract_mapping(
        metadata, "factory_bench_level_contract"
    )

    return {
        "delivery_depth_schema": str(depth_contract.get("schema_version") or ""),
        "delivery_plan_schema": str(plan_document.get("schema_version") or ""),
        "subject": str(product_intent.get("subject") or product_summary.get("intent") or "").strip(),
        "primary_entities": _string_list(product_intent.get("primary_entities"))
        or _string_list(product_summary.get("core_terms")),
        "rule_count": len(_string_list(behavior_contract.get("rule_matrix"))),
        "edge_case_count": len(_string_list(behavior_contract.get("edge_cases"))),
        "required_behavior_test_count": len(_string_list(acceptance_contract.get("required_behavior_tests"))),
        "deterministic_checks": _string_list(acceptance_contract.get("deterministic_checks")),
        "anti_hollow_rules": _string_list(depth_contract.get("anti_hollow_delivery"))
        or _string_list(acceptance_contract.get("anti_hollow_delivery")),
        "level_contract_schema": str(level_contract.get("schema_version") or ""),
        "level": level_contract.get("level", metadata.get("factory_bench_level")),
        "level_minimums": _mapping(level_contract.get("minimums")),
    }


def _architecture_contract(metadata: Mapping[str, Any]) -> dict[str, Any]:
    decisions = metadata.get("architecture_decisions")
    if not isinstance(decisions, list):
        decisions = _mapping(metadata.get("task")).get("architecture_decisions")
    if not isinstance(decisions, list):
        decisions = []
    selected_libraries = _contract_list(metadata, "selected_libraries")
    return {
        "decision_count": len(decisions),
        "selected_libraries": selected_libraries,
        "selected_library_count": len(selected_libraries),
        "decision_sources": sorted(
            {
                str(item.get("source") or "").strip()
                for item in decisions
                if isinstance(item, Mapping) and str(item.get("source") or "").strip()
            }
        ),
    }


def _quality_contract(
    profile: TaskExecutionProfileV1,
    strategy: TaskExecutionStrategyV1,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    quality_gates = _contract_list(metadata, "quality_gates")
    verification_commands = _contract_list(metadata, "verification_commands")
    depth_contract = _delivery_contract(metadata)
    deterministic_checks = list(depth_contract.get("deterministic_checks", []))
    return {
        "quality_gates": quality_gates,
        "verification_commands": verification_commands,
        "deterministic_checks": deterministic_checks,
        "evidence_requirements": list(strategy.evidence_requirements),
        "scope_policy": profile.scope_policy,
        "output_contract_id": profile.output_contract_id,
        "requires_language_best_practices": "language_best_practices" in strategy.evidence_requirements,
        "requires_architecture_or_file_plan": "architecture_or_file_plan" in strategy.evidence_requirements,
        "requires_failed_gate_evidence": "failed_gate_or_verification_evidence" in strategy.evidence_requirements,
    }


def build_task_execution_contract(
    profile: TaskExecutionProfileV1,
    strategy: TaskExecutionStrategyV1,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> TaskExecutionContractV1:
    """Build the shared execution contract from canonical profile + strategy."""

    normalized_metadata = _mapping(metadata)
    delivery_contract = _delivery_contract(normalized_metadata)
    architecture_contract = _architecture_contract(normalized_metadata)
    quality_contract = _quality_contract(profile, strategy, normalized_metadata)
    sampling = {
        "temperature": strategy.temperature,
        "temperature_phase": strategy.temperature_phase,
        "temperature_source": profile.temperature_source,
        "sampling_mode": strategy.sampling_mode,
    }
    context_budget = {
        "input_budget_tokens": strategy.input_budget_tokens,
        "output_budget_tokens": strategy.output_budget_tokens,
        "prompt_max_chars": strategy.prompt_max_chars,
        "min_context_utilization": strategy.min_context_utilization,
        "context_underutilized_policy": strategy.context_underutilized_policy,
        "policy": dict(strategy.context_budget_policy),
    }
    prompt_protocol = {
        "prompt_profile_mode": strategy.prompt_profile_mode,
        "prompt_profile_required": strategy.prompt_profile_required,
        "language_guidance_source": "director.tasking.language_guidance.select_guidance",
        "language": profile.language,
        "language_display_name": profile.language_display_name,
        "framework": profile.framework,
        "framework_display_name": profile.framework_display_name,
        "task_foci": list(profile.task_foci),
        "task_focus_labels": list(profile.task_focus_labels),
        "file_roles": list(profile.file_roles),
        "file_role_labels": list(profile.file_role_labels),
        "generation_mode": profile.generation_mode,
        "scope_policy": profile.scope_policy,
        "output_contract_id": profile.output_contract_id,
    }
    audit_seed = {
        "profile": profile.to_dict(),
        "strategy": strategy.to_dict(),
        "delivery_contract": delivery_contract,
        "architecture_contract": architecture_contract,
        "quality_contract": quality_contract,
    }
    audit_contract = {
        "profile_hash": stable_hash(profile.to_dict()),
        "strategy_hash": stable_hash(strategy.to_dict()),
        "contract_hash": stable_hash(audit_seed),
        "evidence_sources": [
            "task.execution_profile.v1",
            "task.execution_strategy.v1",
            "pm.delivery_plan_document",
            "pm.delivery_depth_contract",
            "chief_engineer.blueprint",
            "director.tasking",
        ],
    }

    return TaskExecutionContractV1(
        profile_schema_version=profile.schema_version,
        strategy_schema_version=strategy.schema_version,
        task_type=profile.task_type,
        phase=profile.phase,
        project_type=profile.project_type,
        language=profile.language,
        language_display_name=profile.language_display_name,
        framework=profile.framework,
        output_contract_id=profile.output_contract_id,
        generation_mode=profile.generation_mode,
        sampling=sampling,
        context_budget=context_budget,
        prompt_protocol=prompt_protocol,
        delivery_contract=delivery_contract,
        architecture_contract=architecture_contract,
        quality_contract=quality_contract,
        audit_contract=audit_contract,
        target_files=profile.target_files,
        scope_paths=profile.scope_paths,
        evidence_requirements=strategy.evidence_requirements,
    )


__all__ = ["build_task_execution_contract"]
